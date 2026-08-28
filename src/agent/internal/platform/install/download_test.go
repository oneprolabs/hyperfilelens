package install

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestDownloadURLWithProgressKnownLength(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	payload := bytes.Repeat([]byte("download-progress-"), 4096)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Length", fmt.Sprint(len(payload)))
		flusher, _ := w.(http.Flusher)
		for offset := 0; offset < len(payload); offset += 4096 {
			end := min(offset+4096, len(payload))
			_, _ = w.Write(payload[offset:end])
			if flusher != nil {
				flusher.Flush()
			}
			time.Sleep(time.Millisecond)
		}
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact.tar.gz")
	var mu sync.Mutex
	events := make([]DownloadProgress, 0)
	err := downloadURLWithInterval(
		context.Background(),
		server.URL+"/artifact?token=must-not-be-logged",
		destination,
		func(progress DownloadProgress) {
			mu.Lock()
			defer mu.Unlock()
			events = append(events, progress)
		},
		2*time.Millisecond,
	)
	if err != nil {
		t.Fatalf("download failed: %v", err)
	}
	got, err := os.ReadFile(destination)
	if err != nil {
		t.Fatalf("read destination: %v", err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatal("downloaded content does not match payload")
	}
	if _, err := os.Stat(destination + ".part"); !os.IsNotExist(err) {
		t.Fatalf("partial file was not removed: %v", err)
	}

	mu.Lock()
	defer mu.Unlock()
	if len(events) < 2 {
		t.Fatalf("expected intermediate and final progress, got %d event(s)", len(events))
	}
	final := events[len(events)-1]
	if !final.Completed {
		t.Fatal("final progress event is not complete")
	}
	if final.DownloadedBytes != int64(len(payload)) || final.TotalBytes != int64(len(payload)) {
		t.Fatalf("unexpected final progress: %#v", final)
	}
}

func TestDownloadClientDoesNotCapWholeTransferDuration(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	client := downloadHTTPClient()
	if client.Timeout != 0 {
		t.Fatalf("download client timeout = %s, want no whole-transfer timeout", client.Timeout)
	}
	transport, ok := client.Transport.(*http.Transport)
	if !ok {
		t.Fatalf("download transport type = %T, want *http.Transport", client.Transport)
	}
	if transport.ResponseHeaderTimeout != downloadResponseHeaderTimeout {
		t.Fatalf(
			"response header timeout = %s, want %s",
			transport.ResponseHeaderTimeout,
			downloadResponseHeaderTimeout,
		)
	}
}

func TestDownloadURLWithProgressUnknownLength(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		flusher := w.(http.Flusher)
		for range 4 {
			_, _ = w.Write([]byte("chunked-transfer"))
			flusher.Flush()
			time.Sleep(3 * time.Millisecond)
		}
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact.tar.gz")
	var final DownloadProgress
	err := downloadURLWithInterval(
		context.Background(),
		server.URL,
		destination,
		func(progress DownloadProgress) {
			if progress.Completed {
				final = progress
			}
		},
		time.Millisecond,
	)
	if err != nil {
		t.Fatalf("download failed: %v", err)
	}
	if !final.Completed || final.TotalBytes != -1 || final.DownloadedBytes == 0 {
		t.Fatalf("unexpected final progress: %#v", final)
	}
}

func TestDownloadURLReportsHeartbeatWhileWaitingForData(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	payload := []byte("delayed payload")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Length", fmt.Sprint(len(payload)))
		w.WriteHeader(http.StatusOK)
		w.(http.Flusher).Flush()
		time.Sleep(25 * time.Millisecond)
		_, _ = w.Write(payload)
	}))
	defer server.Close()

	var mu sync.Mutex
	events := make([]DownloadProgress, 0)
	err := downloadURLWithInterval(
		context.Background(),
		server.URL,
		filepath.Join(t.TempDir(), "artifact"),
		func(progress DownloadProgress) {
			mu.Lock()
			defer mu.Unlock()
			events = append(events, progress)
		},
		5*time.Millisecond,
	)
	if err != nil {
		t.Fatalf("download failed: %v", err)
	}
	mu.Lock()
	defer mu.Unlock()
	waitingEvents := 0
	for _, event := range events {
		if !event.Completed && event.DownloadedBytes == 0 {
			waitingEvents++
		}
	}
	if waitingEvents == 0 {
		t.Fatalf("expected a waiting heartbeat, got %#v", events)
	}
}

func TestDownloadURLStopsAfterNoProgress(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		w.Header().Set("Content-Length", "1024")
		w.WriteHeader(http.StatusOK)
		w.(http.Flusher).Flush()
		<-request.Context().Done()
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	started := time.Now()
	err := downloadURLWithSettings(
		context.Background(),
		server.URL,
		destination,
		nil,
		time.Millisecond,
		25*time.Millisecond,
	)
	if !errors.Is(err, ErrDownloadNoProgress) {
		t.Fatalf("download error = %v, want ErrDownloadNoProgress", err)
	}
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("no-progress cancellation took %s", elapsed)
	}
	for _, path := range []string{destination, destination + ".part"} {
		if _, statErr := os.Stat(path); !os.IsNotExist(statErr) {
			t.Fatalf("stalled download left %s: %v", path, statErr)
		}
	}
}

func TestDownloadURLAllowsSlowContinuousProgress(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	payload := []byte("slow-but-moving")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Length", fmt.Sprint(len(payload)))
		w.WriteHeader(http.StatusOK)
		flusher := w.(http.Flusher)
		for _, value := range payload {
			_, _ = w.Write([]byte{value})
			flusher.Flush()
			time.Sleep(8 * time.Millisecond)
		}
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	err := downloadURLWithSettings(
		context.Background(),
		server.URL,
		destination,
		nil,
		time.Millisecond,
		25*time.Millisecond,
	)
	if err != nil {
		t.Fatalf("slow progressing download failed: %v", err)
	}
	got, readErr := os.ReadFile(destination)
	if readErr != nil || !bytes.Equal(got, payload) {
		t.Fatalf("downloaded content = %q, err = %v", got, readErr)
	}
}

func TestRetryableDownloadErrors(t *testing.T) {
	tests := []struct {
		name string
		err  error
		want bool
	}{
		{name: "no progress", err: ErrDownloadNoProgress, want: true},
		{name: "truncated", err: ErrDownloadSizeMismatch, want: true},
		{name: "rate limited", err: &DownloadHTTPError{StatusCode: 429, Status: "429 Too Many Requests"}, want: true},
		{name: "server unavailable", err: &DownloadHTTPError{StatusCode: 503, Status: "503 Service Unavailable"}, want: true},
		{name: "not implemented", err: &DownloadHTTPError{StatusCode: 501, Status: "501 Not Implemented"}, want: false},
		{name: "permanent network error", err: permanentNetworkError{}, want: false},
		{name: "temporary network error", err: temporaryNetworkError{}, want: true},
		{name: "transport operation error", err: &net.OpError{Op: "read", Net: "tcp", Err: permanentNetworkError{}}, want: true},
		{name: "forbidden", err: &DownloadHTTPError{StatusCode: 403, Status: "403 Forbidden"}, want: false},
		{name: "not found", err: &DownloadHTTPError{StatusCode: 404, Status: "404 Not Found"}, want: false},
		{name: "canceled", err: context.Canceled, want: false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := IsRetryableDownloadError(test.err); got != test.want {
				t.Fatalf("IsRetryableDownloadError(%v) = %t, want %t", test.err, got, test.want)
			}
		})
	}
}

type permanentNetworkError struct{}

func (permanentNetworkError) Error() string   { return "permanent network error" }
func (permanentNetworkError) Timeout() bool   { return false }
func (permanentNetworkError) Temporary() bool { return false }

type temporaryNetworkError struct{}

func (temporaryNetworkError) Error() string   { return "temporary network error" }
func (temporaryNetworkError) Timeout() bool   { return false }
func (temporaryNetworkError) Temporary() bool { return true }

func TestDownloadURLRemovesPartialOnContextCancellation(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		w.Header().Set("Content-Length", "1024")
		w.WriteHeader(http.StatusOK)
		w.(http.Flusher).Flush()
		<-request.Context().Done()
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Millisecond)
	defer cancel()
	err := DownloadURL(ctx, server.URL, destination)
	if err == nil {
		t.Fatal("expected canceled download to fail")
	}
	for _, path := range []string{destination, destination + ".part"} {
		if _, statErr := os.Stat(path); !os.IsNotExist(statErr) {
			t.Fatalf("canceled download left %s: %v", path, statErr)
		}
	}
}

func TestDownloadURLPreservesDestinationAndRemovesPartialOnFailure(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Length", "64")
		_, _ = w.Write([]byte("truncated"))
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact.tar.gz")
	if err := os.WriteFile(destination, []byte("installed-good-copy"), 0o600); err != nil {
		t.Fatal(err)
	}
	err := DownloadURL(context.Background(), server.URL, destination)
	if err == nil {
		t.Fatal("expected truncated download to fail")
	}
	got, readErr := os.ReadFile(destination)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if string(got) != "installed-good-copy" {
		t.Fatalf("existing destination was replaced: %q", got)
	}
	if _, statErr := os.Stat(destination + ".part"); !os.IsNotExist(statErr) {
		t.Fatalf("partial file was not removed: %v", statErr)
	}
}

func TestDownloadURLErrorDoesNotExposeSignedURL(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	rawURL := "http://127.0.0.1:1/artifact?token=must-not-appear"
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	err := DownloadURL(ctx, rawURL, filepath.Join(t.TempDir(), "artifact"))
	if err == nil {
		t.Fatal("expected connection failure")
	}
	message := err.Error()
	for _, secret := range []string{"must-not-appear", rawURL} {
		if strings.Contains(message, secret) {
			t.Fatalf("download error exposed signed URL data: %s", message)
		}
	}
}
