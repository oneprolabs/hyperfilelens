package install

import (
	"context"
	"errors"
	"fmt"
	"io"
	"math/rand/v2"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
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
	// ErrDownloadRangeRejected identifies a resume response that cannot safely
	// be appended to the existing partial file.
	ErrDownloadRangeRejected = errors.New("download range rejected")
)

// DownloadHTTPError retains the response status without retaining a signed URL.
type DownloadHTTPError struct {
	StatusCode    int
	Status        string
	retryAfter    time.Duration
	hasRetryAfter bool
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
	return downloadURLAttempt(ctx, rawURL, destPath, reporter, interval, idleTimeout, false, nil)
}

// DownloadRetryNotice describes one retry of a resumable download.
type DownloadRetryNotice func(attempt, maxAttempts int, delay time.Duration, err error, resumeBytes int64)

type downloadResumeValidator struct {
	etag         string
	lastModified string
	totalBytes   int64
	started      time.Time
}

// DownloadURLResumableWithProgress retries transient failures while preserving
// a partial file in the current process and resuming it when the server agrees.
// The destination is still promoted atomically only after a complete response.
func DownloadURLResumableWithProgress(
	ctx context.Context,
	rawURL string,
	destPath string,
	reporter ProgressReporter,
	retryNotice DownloadRetryNotice,
) error {
	return downloadURLResumableWithSettings(
		ctx,
		rawURL,
		destPath,
		reporter,
		retryNotice,
		defaultDownloadProgressInterval,
		defaultDownloadIdleTimeout,
	)
}

func downloadURLResumableWithSettings(
	ctx context.Context,
	rawURL string,
	destPath string,
	reporter ProgressReporter,
	retryNotice DownloadRetryNotice,
	interval time.Duration,
	idleTimeout time.Duration,
) error {
	return downloadURLResumableWithPolicy(
		ctx,
		rawURL,
		destPath,
		reporter,
		retryNotice,
		interval,
		idleTimeout,
		[]time.Duration{5 * time.Second, 15 * time.Second, 30 * time.Second},
	)
}

func downloadURLResumableWithPolicy(
	ctx context.Context,
	rawURL string,
	destPath string,
	reporter ProgressReporter,
	retryNotice DownloadRetryNotice,
	interval time.Duration,
	idleTimeout time.Duration,
	delays []time.Duration,
) error {
	const maxAttempts = 4
	partPath := destPath + ".part"
	if err := removePartialDownload(partPath); err != nil {
		return fmt.Errorf("remove stale partial download: %w", err)
	}
	validator := &downloadResumeValidator{totalBytes: -1, started: time.Now()}
	var lastErr error
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		if err := ctx.Err(); err != nil {
			return cleanupPartialDownload(partPath, err)
		}
		err := downloadURLAttempt(ctx, rawURL, destPath, reporter, interval, idleTimeout, true, validator)
		if err == nil {
			return nil
		}
		if ctxErr := ctx.Err(); ctxErr != nil {
			return cleanupPartialDownload(partPath, ctxErr)
		}
		lastErr = err
		if !isResumableDownloadError(err) || attempt == maxAttempts {
			result := err
			if attempt == maxAttempts && isResumableDownloadError(err) {
				result = fmt.Errorf("download failed after %d attempts: %w", maxAttempts, err)
			}
			return cleanupPartialDownload(partPath, result)
		}
		var httpErr *DownloadHTTPError
		if errors.Is(err, ErrDownloadRangeRejected) ||
			(errors.As(err, &httpErr) && httpErr.StatusCode == http.StatusRequestedRangeNotSatisfiable) {
			if cleanupErr := removePartialDownload(partPath); cleanupErr != nil {
				return errors.Join(err, fmt.Errorf("reset partial download: %w", cleanupErr))
			}
		}
		resumeBytes := partialDownloadSize(destPath)
		delay := 0 * time.Second
		if attempt-1 < len(delays) {
			delay = jitteredRetryDelay(delays[attempt-1])
		}
		if httpRetryDelay := downloadRetryAfter(err); httpRetryDelay > 0 {
			delay = httpRetryDelay
		}
		if retryNotice != nil {
			retryNotice(attempt, maxAttempts, delay, err, resumeBytes)
		}
		if err := waitWithContext(ctx, delay); err != nil {
			return cleanupPartialDownload(partPath, err)
		}
	}
	return lastErr
}

func removePartialDownload(partPath string) error {
	err := os.Remove(partPath)
	if err == nil || errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return err
}

func cleanupPartialDownload(partPath string, result error) error {
	if cleanupErr := removePartialDownload(partPath); cleanupErr != nil {
		return errors.Join(result, fmt.Errorf("remove partial download: %w", cleanupErr))
	}
	return result
}

func jitteredRetryDelay(delay time.Duration) time.Duration {
	if delay <= 0 {
		return 0
	}
	limit := delay / 5
	if limit <= 0 {
		return delay
	}
	return delay - limit + time.Duration(rand.Int64N(int64(2*limit)+1))
}

func downloadRetryAfter(err error) time.Duration {
	var httpErr *DownloadHTTPError
	if !errors.As(err, &httpErr) ||
		(httpErr.StatusCode != http.StatusTooManyRequests && httpErr.StatusCode != http.StatusServiceUnavailable) ||
		!httpErr.hasRetryAfter {
		return 0
	}
	return min(max(httpErr.retryAfter, 5*time.Second), 120*time.Second)
}

func isResumableDownloadError(err error) bool {
	if IsRetryableDownloadError(err) || errors.Is(err, ErrDownloadRangeRejected) {
		return true
	}
	var httpErr *DownloadHTTPError
	if !errors.As(err, &httpErr) {
		return false
	}
	return httpErr.StatusCode == http.StatusRequestedRangeNotSatisfiable ||
		(httpErr.StatusCode >= 500 && httpErr.StatusCode < 600)
}

func waitWithContext(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func partialDownloadSize(destPath string) int64 {
	info, err := os.Stat(destPath + ".part")
	if err != nil || !info.Mode().IsRegular() {
		return 0
	}
	return info.Size()
}

func downloadURLAttempt(
	ctx context.Context,
	rawURL string,
	destPath string,
	reporter ProgressReporter,
	interval time.Duration,
	idleTimeout time.Duration,
	resume bool,
	validator *downloadResumeValidator,
) error {
	downloadCtx, cancel := context.WithCancelCause(ctx)
	defer cancel(nil)
	req, err := http.NewRequestWithContext(downloadCtx, http.MethodGet, rawURL, nil)
	if err != nil {
		return fmt.Errorf("create download request: %w", sanitizeDownloadError(err))
	}
	partPath := destPath + ".part"
	resumeBytes := int64(0)
	if resume {
		resumeBytes = partialDownloadSize(destPath)
		if resumeBytes > 0 {
			req.Header.Set("Range", "bytes="+strconv.FormatInt(resumeBytes, 10)+"-")
			if validator != nil {
				if validator.etag != "" {
					req.Header.Set("If-Range", validator.etag)
				} else if validator.lastModified != "" {
					req.Header.Set("If-Range", validator.lastModified)
				}
			}
		}
	}
	if resume {
		req.Header.Set("Accept-Encoding", "identity")
	}
	client := downloadHTTPClient()
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("download request failed: %w", sanitizeDownloadError(err))
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		retryAfter, hasRetryAfter := parseRetryAfter(resp.Header.Get("Retry-After"), time.Now())
		return &DownloadHTTPError{
			StatusCode:    resp.StatusCode,
			Status:        resp.Status,
			retryAfter:    retryAfter,
			hasRetryAfter: hasRetryAfter,
		}
	}
	if resume {
		if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusPartialContent {
			return ErrDownloadRangeRejected
		}
		if resumeBytes == 0 && resp.StatusCode != http.StatusOK {
			return ErrDownloadRangeRejected
		}
	}
	if validator != nil {
		if resumeBytes == 0 || resp.StatusCode == http.StatusOK {
			validator.etag = resp.Header.Get("ETag")
			validator.lastModified = resp.Header.Get("Last-Modified")
			validator.totalBytes = resp.ContentLength
		} else if resp.StatusCode == http.StatusPartialContent &&
			validator.etag != "" && resp.Header.Get("ETag") != "" &&
			resp.Header.Get("ETag") != validator.etag {
			return ErrDownloadRangeRejected
		} else if resp.StatusCode == http.StatusPartialContent &&
			validator.etag == "" && validator.lastModified != "" &&
			resp.Header.Get("Last-Modified") != "" &&
			resp.Header.Get("Last-Modified") != validator.lastModified {
			return ErrDownloadRangeRejected
		}
	}
	if err := os.MkdirAll(filepath.Dir(destPath), 0o755); err != nil {
		return err
	}

	appendMode := resumeBytes > 0 && resp.StatusCode == http.StatusPartialContent
	var rangeEnd, rangeTotal int64
	if appendMode {
		start, end, total, ok := parseContentRange(resp.Header.Get("Content-Range"))
		if !ok || start != resumeBytes || total < 0 {
			return ErrDownloadRangeRejected
		}
		if validator != nil {
			if validator.totalBytes >= 0 && validator.totalBytes != total {
				return ErrDownloadRangeRejected
			}
			validator.totalBytes = total
		}
		rangeEnd = end
		rangeTotal = total
		if resp.ContentLength >= 0 && resp.ContentLength != end-start+1 {
			return ErrDownloadRangeRejected
		}
	} else if err := os.Remove(partPath); err != nil && !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("remove stale partial download: %w", err)
	}
	progressBase := int64(0)
	if appendMode {
		progressBase = resumeBytes
	}
	flags := os.O_WRONLY
	if appendMode {
		flags |= os.O_APPEND
	} else {
		flags |= os.O_CREATE | os.O_EXCL
	}
	file, err := os.OpenFile(partPath, flags, 0o600)
	if err != nil {
		return err
	}
	if appendMode {
		info, statErr := file.Stat()
		if statErr != nil {
			_ = file.Close()
			return statErr
		}
		if !info.Mode().IsRegular() || info.Size() != resumeBytes {
			_ = file.Close()
			return ErrDownloadRangeRejected
		}
	}
	completed := false
	defer func() {
		_ = file.Close()
		if !completed && !resume {
			_ = os.Remove(partPath)
		}
	}()

	started := time.Now()
	progressStarted := started
	if validator != nil && !validator.started.IsZero() {
		progressStarted = validator.started
	}
	var downloaded atomic.Int64
	downloaded.Store(progressBase)
	var lastByteAt atomic.Int64
	lastByteAt.Store(started.UnixNano())
	stopProgress := startDownloadProgress(
		reporter,
		&downloaded,
		&lastByteAt,
		totalDownloadLength(resp, progressBase, appendMode),
		progressStarted,
		started,
		progressBase,
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
	if appendMode {
		expected := rangeEnd - resumeBytes + 1
		if written != expected {
			if written > expected {
				return ErrDownloadRangeRejected
			}
			return fmt.Errorf(
				"%w: received %d bytes, expected %d",
				ErrDownloadSizeMismatch,
				written,
				expected,
			)
		}
		if actual := resumeBytes + written; actual != rangeTotal {
			return fmt.Errorf(
				"%w: received %d bytes, expected %d",
				ErrDownloadSizeMismatch,
				actual,
				rangeTotal,
			)
		}
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

func parseRetryAfter(value string, now time.Time) (time.Duration, bool) {
	value = strings.TrimSpace(value)
	if value == "" {
		return 0, false
	}
	if seconds, err := strconv.ParseInt(value, 10, 64); err == nil {
		if seconds < 0 {
			return 0, false
		}
		const maxDuration = time.Duration(1<<63 - 1)
		if seconds > int64(maxDuration/time.Second) {
			return maxDuration, true
		}
		return time.Duration(seconds) * time.Second, true
	}
	when, err := http.ParseTime(value)
	if err != nil {
		return 0, false
	}
	if !when.After(now) {
		return 0, true
	}
	return when.Sub(now), true
}

func totalDownloadLength(resp *http.Response, resumeBytes int64, appendMode bool) int64 {
	if !appendMode {
		return resp.ContentLength
	}
	_, _, total, ok := parseContentRange(resp.Header.Get("Content-Range"))
	if ok && total >= 0 {
		return total
	}
	if resp.ContentLength >= 0 {
		return resumeBytes + resp.ContentLength
	}
	return -1
}

func parseContentRange(value string) (start, end, total int64, ok bool) {
	parts := strings.Fields(strings.TrimSpace(value))
	if len(parts) != 2 || parts[0] != "bytes" {
		return 0, 0, 0, false
	}
	rangeAndTotal := strings.SplitN(parts[1], "/", 2)
	if len(rangeAndTotal) != 2 {
		return 0, 0, 0, false
	}
	bounds := strings.SplitN(rangeAndTotal[0], "-", 2)
	if len(bounds) != 2 {
		return 0, 0, 0, false
	}
	start, err1 := strconv.ParseInt(bounds[0], 10, 64)
	end, err2 := strconv.ParseInt(bounds[1], 10, 64)
	if err1 != nil || err2 != nil || start < 0 || end < start {
		return 0, 0, 0, false
	}
	if rangeAndTotal[1] == "*" {
		return start, end, -1, true
	}
	total, err := strconv.ParseInt(rangeAndTotal[1], 10, 64)
	if err != nil || total <= end {
		return 0, 0, 0, false
	}
	return start, end, total, true
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
	progressStarted time.Time,
	attemptStarted time.Time,
	initialBytes int64,
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
		lastBytes := initialBytes
		lastAt := attemptStarted
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
					Elapsed:         now.Sub(progressStarted),
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
		elapsed := time.Since(progressStarted)
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
	if errors.Is(err, ErrDownloadNoProgress) ||
		errors.Is(err, ErrDownloadSizeMismatch) ||
		errors.Is(err, io.ErrUnexpectedEOF) {
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
