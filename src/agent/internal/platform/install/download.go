package install

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sync/atomic"
	"time"
)

const (
	defaultDownloadProgressInterval = time.Second
	defaultDownloadIdleTimeout      = 90 * time.Second
)

var (
	// ErrDownloadNoProgress identifies a body that stopped yielding bytes while
	// the HTTP connection itself remained open.
	ErrDownloadNoProgress = errors.New("download made no progress")
	// ErrDownloadSizeMismatch identifies a truncated response with a declared
	// Content-Length. A fresh URL may make a later transfer succeed.
	ErrDownloadSizeMismatch = errors.New("download size mismatch")
)

// DownloadHTTPError retains the response status without retaining a signed URL.
type DownloadHTTPError struct {
	StatusCode int
	Status     string
}

func (err *DownloadHTTPError) Error() string {
	return "download HTTP " + err.Status
}

// DownloadProgress describes one safe, URL-free transfer progress snapshot.
type DownloadProgress struct {
	DownloadedBytes int64
	TotalBytes      int64
	Elapsed         time.Duration
	Idle            time.Duration
	BytesPerSecond  float64
	Completed       bool
}

// ProgressReporter receives rate-limited download progress snapshots.
type ProgressReporter func(DownloadProgress)

// DownloadURL streams url into destPath without interactive progress output.
func DownloadURL(ctx context.Context, rawURL, destPath string) error {
	return DownloadURLWithProgress(ctx, rawURL, destPath, nil)
}

// DownloadURLWithProgress safely downloads one file and reports transfer progress.
func DownloadURLWithProgress(
	ctx context.Context,
	rawURL string,
	destPath string,
	reporter ProgressReporter,
) error {
	return downloadURLWithInterval(
		ctx,
		rawURL,
		destPath,
		reporter,
		defaultDownloadProgressInterval,
	)
}

func downloadURLWithInterval(
	ctx context.Context,
	rawURL string,
	destPath string,
	reporter ProgressReporter,
	interval time.Duration,
) error {
	return downloadURLWithSettings(
		ctx,
		rawURL,
		destPath,
		reporter,
		interval,
		defaultDownloadIdleTimeout,
	)
}

func downloadURLWithSettings(
	ctx context.Context,
	rawURL string,
	destPath string,
	reporter ProgressReporter,
	interval time.Duration,
	idleTimeout time.Duration,
) error {
	downloadCtx, cancel := context.WithCancelCause(ctx)
	defer cancel(nil)
	req, err := http.NewRequestWithContext(downloadCtx, http.MethodGet, rawURL, nil)
	if err != nil {
		return fmt.Errorf("create download request: %w", sanitizeDownloadError(err))
	}
	client := downloadHTTPClient()
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("download request failed: %w", sanitizeDownloadError(err))
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return &DownloadHTTPError{StatusCode: resp.StatusCode, Status: resp.Status}
	}
	if err := os.MkdirAll(filepath.Dir(destPath), 0o755); err != nil {
		return err
	}

	partPath := destPath + ".part"
	if err := os.Remove(partPath); err != nil && !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("remove stale partial download: %w", err)
	}
	file, err := os.OpenFile(partPath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	completed := false
	defer func() {
		_ = file.Close()
		if !completed {
			_ = os.Remove(partPath)
		}
	}()

	started := time.Now()
	var downloaded atomic.Int64
	var lastByteAt atomic.Int64
	lastByteAt.Store(started.UnixNano())
	stopProgress := startDownloadProgress(
		reporter,
		&downloaded,
		&lastByteAt,
		resp.ContentLength,
		started,
		interval,
	)
	stopIdleWatchdog := startDownloadIdleWatchdog(
		downloadCtx,
		cancel,
		&lastByteAt,
		idleTimeout,
	)
	written, copyErr := io.Copy(file, io.TeeReader(resp.Body, byteCounter{
		value:      &downloaded,
		lastByteAt: &lastByteAt,
	}))
	stopIdleWatchdog()
	stopProgress(false)
	if copyErr != nil {
		if errors.Is(context.Cause(downloadCtx), ErrDownloadNoProgress) {
			return fmt.Errorf("download stream failed: %w after %s", ErrDownloadNoProgress, idleTimeout)
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return fmt.Errorf("download stream failed: %w", sanitizeDownloadError(copyErr))
	}
	if resp.ContentLength >= 0 && written != resp.ContentLength {
		return fmt.Errorf(
			"%w: received %d bytes, expected %d",
			ErrDownloadSizeMismatch,
			written,
			resp.ContentLength,
		)
	}
	if err := file.Close(); err != nil {
		return err
	}
	if err := os.Rename(partPath, destPath); err != nil {
		return err
	}
	completed = true
	stopProgress(true)
	return nil
}

type byteCounter struct {
	value      *atomic.Int64
	lastByteAt *atomic.Int64
}

func (counter byteCounter) Write(p []byte) (int, error) {
	if len(p) > 0 {
		counter.value.Add(int64(len(p)))
		counter.lastByteAt.Store(time.Now().UnixNano())
	}
	return len(p), nil
}

func startDownloadIdleWatchdog(
	ctx context.Context,
	cancel context.CancelCauseFunc,
	lastByteAt *atomic.Int64,
	idleTimeout time.Duration,
) func() {
	if idleTimeout <= 0 {
		return func() {}
	}
	interval := min(idleTimeout/10, time.Second)
	if interval < time.Millisecond {
		interval = time.Millisecond
	}
	done := make(chan struct{})
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case now := <-ticker.C:
				last := time.Unix(0, lastByteAt.Load())
				if now.Sub(last) >= idleTimeout {
					cancel(ErrDownloadNoProgress)
					return
				}
			case <-ctx.Done():
				return
			case <-done:
				return
			}
		}
	}()
	var stoppedOnce atomic.Bool
	return func() {
		if stoppedOnce.CompareAndSwap(false, true) {
			close(done)
			<-stopped
		}
	}
}

func startDownloadProgress(
	reporter ProgressReporter,
	downloaded *atomic.Int64,
	lastByteAt *atomic.Int64,
	total int64,
	started time.Time,
	interval time.Duration,
) func(completed bool) {
	if reporter == nil {
		return func(bool) {}
	}
	if interval <= 0 {
		interval = defaultDownloadProgressInterval
	}
	done := make(chan struct{})
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		lastBytes := int64(0)
		lastAt := started
		for {
			select {
			case now := <-ticker.C:
				current := downloaded.Load()
				idle := now.Sub(time.Unix(0, lastByteAt.Load()))
				if idle < 0 {
					idle = 0
				}
				seconds := now.Sub(lastAt).Seconds()
				rate := float64(0)
				if seconds > 0 {
					rate = float64(current-lastBytes) / seconds
				}
				reporter(DownloadProgress{
					DownloadedBytes: current,
					TotalBytes:      total,
					Elapsed:         now.Sub(started),
					Idle:            idle,
					BytesPerSecond:  rate,
				})
				lastBytes = current
				lastAt = now
			case <-done:
				return
			}
		}
	}()
	var stoppedOnce atomic.Bool
	return func(completed bool) {
		if stoppedOnce.CompareAndSwap(false, true) {
			close(done)
			<-stopped
		}
		if !completed {
			return
		}
		elapsed := time.Since(started)
		current := downloaded.Load()
		rate := float64(0)
		if elapsed > 0 {
			rate = float64(current) / elapsed.Seconds()
		}
		reporter(DownloadProgress{
			DownloadedBytes: current,
			TotalBytes:      total,
			Elapsed:         elapsed,
			BytesPerSecond:  rate,
			Completed:       true,
		})
	}
}

// IsRetryableDownloadError reports whether a fresh signed URL and connection
// may recover a failed package-body transfer.
func IsRetryableDownloadError(err error) bool {
	if err == nil || errors.Is(err, context.Canceled) {
		return false
	}
	if errors.Is(err, ErrDownloadNoProgress) || errors.Is(err, ErrDownloadSizeMismatch) || errors.Is(err, io.ErrUnexpectedEOF) {
		return true
	}
	var httpErr *DownloadHTTPError
	if errors.As(err, &httpErr) {
		switch httpErr.StatusCode {
		case http.StatusRequestTimeout,
			http.StatusTooManyRequests,
			http.StatusInternalServerError,
			http.StatusBadGateway,
			http.StatusServiceUnavailable,
			http.StatusGatewayTimeout:
			return true
		default:
			return false
		}
	}
	var netErr net.Error
	if !errors.As(err, &netErr) {
		return false
	}
	if netErr.Timeout() || netErr.Temporary() {
		return true
	}
	var opErr *net.OpError
	return errors.As(err, &opErr)
}

func sanitizeDownloadError(err error) error {
	var urlErr *url.Error
	if errors.As(err, &urlErr) && urlErr.Err != nil {
		return urlErr.Err
	}
	return err
}
