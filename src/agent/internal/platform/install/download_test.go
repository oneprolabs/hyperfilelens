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
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
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

func TestDownloadURLPreservesExistingSuccessful2xxBehavior(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	payload := []byte("created download")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write(payload)
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	if err := DownloadURL(context.Background(), server.URL, destination); err != nil {
		t.Fatalf("ordinary 2xx download failed: %v", err)
	}
	got, err := os.ReadFile(destination)
	if err != nil || !bytes.Equal(got, payload) {
		t.Fatalf("downloaded content = %q, err = %v", got, err)
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
		{name: "range rejected stays local to resumable downloads", err: ErrDownloadRangeRejected, want: false},
		{name: "range unsatisfied stays local to resumable downloads", err: &DownloadHTTPError{StatusCode: 416, Status: "416 Range Not Satisfiable"}, want: false},
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

func TestResumableDownloadErrorClassificationStaysLocal(t *testing.T) {
	for _, err := range []error{
		ErrDownloadRangeRejected,
		&DownloadHTTPError{StatusCode: http.StatusRequestedRangeNotSatisfiable, Status: "416 Range Not Satisfiable"},
		&DownloadHTTPError{StatusCode: http.StatusNotImplemented, Status: "501 Not Implemented"},
	} {
		if !isResumableDownloadError(err) {
			t.Fatalf("resumable download error %v was not retryable", err)
		}
		if IsRetryableDownloadError(err) {
			t.Fatalf("resumable-only error %v changed ordinary download classification", err)
		}
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

func TestResumableDownloadContinuesAfterUnexpectedEOF(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	payload := bytes.Repeat([]byte("resumable-download-"), 32)
	cut := len(payload) / 2
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		switch requests.Add(1) {
		case 1:
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
			_, _ = w.Write(payload[:cut])
		case 2:
			if got := request.Header.Get("Range"); got != "bytes="+strconv.Itoa(cut)+"-" {
				t.Errorf("resume Range = %q, want bytes=%d-", got, cut)
			}
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)-cut))
			w.Header().Set("Content-Range", "bytes "+strconv.Itoa(cut)+"-"+strconv.Itoa(len(payload)-1)+"/"+strconv.Itoa(len(payload)))
			w.WriteHeader(http.StatusPartialContent)
			_, _ = w.Write(payload[cut:])
		default:
			t.Errorf("unexpected request %d", requests.Load())
		}
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	err := downloadURLResumableWithPolicy(
		context.Background(), server.URL, destination, nil, nil,
		time.Millisecond, time.Second, []time.Duration{0, 0, 0},
	)
	if err != nil {
		t.Fatalf("resumable download failed: %v", err)
	}
	got, err := os.ReadFile(destination)
	if err != nil {
		t.Fatalf("read destination: %v", err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatal("resumed content does not match payload")
	}
	if _, err := os.Stat(destination + ".part"); !os.IsNotExist(err) {
		t.Fatalf("partial file was not removed: %v", err)
	}
}

func TestDownloadURLResumableWithProgressPublicEntryPoint(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	payload := []byte("public-resumable-entry-point")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
		_, _ = w.Write(payload)
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	var final DownloadProgress
	retryNotices := 0
	err := DownloadURLResumableWithProgress(
		context.Background(), server.URL, destination,
		func(progress DownloadProgress) {
			if progress.Completed {
				final = progress
			}
		},
		func(_, _ int, _ time.Duration, _ error, _ int64) { retryNotices++ },
	)
	if err != nil {
		t.Fatalf("public resumable download failed: %v", err)
	}
	got, readErr := os.ReadFile(destination)
	if readErr != nil || !bytes.Equal(got, payload) {
		t.Fatalf("downloaded content = %q, err = %v", got, readErr)
	}
	if !final.Completed || final.DownloadedBytes != int64(len(payload)) || retryNotices != 0 {
		t.Fatalf("final progress = %#v, retry notices = %d", final, retryNotices)
	}
}

func TestResumableDownloadDiscardsPartialFromPreviousProcess(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	payload := []byte("complete-current-download")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if got := request.Header.Get("Range"); got != "" {
			t.Errorf("initial request reused stale partial file with Range %q", got)
		}
		w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
		_, _ = w.Write(payload)
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	if err := os.WriteFile(destination+".part", []byte("stale-partial"), 0o600); err != nil {
		t.Fatal(err)
	}
	err := downloadURLResumableWithPolicy(
		context.Background(), server.URL, destination, nil, nil,
		time.Millisecond, time.Second, []time.Duration{0, 0, 0},
	)
	if err != nil {
		t.Fatalf("download failed: %v", err)
	}
	got, readErr := os.ReadFile(destination)
	if readErr != nil || !bytes.Equal(got, payload) {
		t.Fatalf("downloaded content = %q, err = %v", got, readErr)
	}
}

func TestResumableDownloadRestartsWhenServerIgnoresRange(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	payload := []byte("complete response after restart")
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if requests.Add(1) == 1 {
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
			_, _ = w.Write(payload[:8])
			return
		}
		if request.Header.Get("Range") == "" {
			t.Error("retry did not attempt to resume the partial file")
		}
		w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
		_, _ = w.Write(payload)
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	var final DownloadProgress
	err := downloadURLResumableWithPolicy(
		context.Background(), server.URL, destination, func(progress DownloadProgress) {
			if progress.Completed {
				final = progress
			}
		}, nil,
		time.Millisecond, time.Second, []time.Duration{0, 0, 0},
	)
	if err != nil {
		t.Fatalf("restart download failed: %v", err)
	}
	got, err := os.ReadFile(destination)
	if err != nil || !bytes.Equal(got, payload) {
		t.Fatalf("downloaded content = %q, err = %v", got, err)
	}
	if final.DownloadedBytes != int64(len(payload)) || final.TotalBytes != int64(len(payload)) {
		t.Fatalf("restart progress retained partial bytes: %#v", final)
	}
}

func TestResumableDownloadRejectsUnexpectedSuccessfulStatus(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	payload := []byte("restart-after-unexpected-status")
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		switch requests.Add(1) {
		case 1:
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
			_, _ = w.Write(payload[:8])
		case 2:
			if request.Header.Get("Range") == "" {
				t.Error("second request did not attempt to resume")
			}
			w.WriteHeader(http.StatusNoContent)
		case 3:
			if request.Header.Get("Range") != "" {
				t.Error("unexpected 2xx response did not reset the partial download")
			}
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
			_, _ = w.Write(payload)
		default:
			t.Errorf("unexpected request %d", requests.Load())
		}
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	err := downloadURLResumableWithPolicy(
		context.Background(), server.URL, destination, nil, nil,
		time.Millisecond, time.Second, []time.Duration{0, 0, 0},
	)
	if err != nil {
		t.Fatalf("download after unexpected 2xx failed: %v", err)
	}
	got, _ := os.ReadFile(destination)
	if !bytes.Equal(got, payload) {
		t.Fatalf("downloaded content = %q", got)
	}
}

func TestResumableDownloadRejectsMismatchedContentRange(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	payload := []byte("content-range-must-match")
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		switch requests.Add(1) {
		case 1:
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
			_, _ = w.Write(payload[:8])
		case 2:
			w.Header().Set("Content-Range", "bytes 9-"+strconv.Itoa(len(payload)-1)+"/"+strconv.Itoa(len(payload)))
			w.WriteHeader(http.StatusPartialContent)
			_, _ = w.Write(payload[9:])
		case 3:
			if request.Header.Get("Range") != "" {
				t.Error("mismatched range did not clear the partial download")
			}
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
			_, _ = w.Write(payload)
		default:
			t.Errorf("unexpected request %d", requests.Load())
		}
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	err := downloadURLResumableWithPolicy(
		context.Background(), server.URL, destination, nil, nil,
		time.Millisecond, time.Second, []time.Duration{0, 0, 0},
	)
	if err != nil {
		t.Fatalf("download after mismatched range failed: %v", err)
	}
	got, _ := os.ReadFile(destination)
	if !bytes.Equal(got, payload) {
		t.Fatalf("downloaded content = %q", got)
	}
}

func TestResumableDownloadRejectsContentRangeLengthMismatch(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	payload := []byte("range-length-must-match")
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		switch requests.Add(1) {
		case 1:
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
			_, _ = w.Write(payload[:6])
		case 2:
			// The declared range has one fewer byte than Content-Length. It
			// must not be appended to the existing partial file.
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)-6))
			w.Header().Set("Content-Range", "bytes 6-"+strconv.Itoa(len(payload)-2)+"/"+strconv.Itoa(len(payload)))
			w.WriteHeader(http.StatusPartialContent)
			_, _ = w.Write(payload[6:])
		case 3:
			if request.Header.Get("Range") != "" {
				t.Error("invalid range response did not clear the partial download")
			}
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
			_, _ = w.Write(payload)
		default:
			t.Errorf("unexpected request %d", requests.Load())
		}
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	err := downloadURLResumableWithPolicy(
		context.Background(), server.URL, destination, nil, nil,
		time.Millisecond, time.Second, []time.Duration{0, 0, 0},
	)
	if err != nil {
		t.Fatalf("download after invalid range response failed: %v", err)
	}
	got, _ := os.ReadFile(destination)
	if !bytes.Equal(got, payload) {
		t.Fatalf("downloaded content = %q", got)
	}
}

func TestResumableDownloadRejectsUnknownContentRangeTotal(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	payload := []byte("range-total-must-be-known")
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		switch requests.Add(1) {
		case 1:
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
			_, _ = w.Write(payload[:6])
		case 2:
			w.Header().Set("Content-Length", "6")
			w.Header().Set("Content-Range", "bytes 6-11/*")
			w.WriteHeader(http.StatusPartialContent)
			_, _ = w.Write(payload[6:12])
		case 3:
			if request.Header.Get("Range") != "" {
				t.Error("unknown range total did not clear the partial download")
			}
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
			_, _ = w.Write(payload)
		default:
			t.Errorf("unexpected request %d", requests.Load())
		}
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	err := downloadURLResumableWithPolicy(
		context.Background(), server.URL, destination, nil, nil,
		time.Millisecond, time.Second, []time.Duration{0, 0, 0},
	)
	if err != nil {
		t.Fatalf("download after unknown range total failed: %v", err)
	}
	got, _ := os.ReadFile(destination)
	if !bytes.Equal(got, payload) {
		t.Fatalf("downloaded content = %q", got)
	}
}

func TestResumableDownloadRejectsChangedTotalLength(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	firstPayload := []byte("original-resource")
	replacement := []byte("replacement-resource-is-longer")
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		switch requests.Add(1) {
		case 1:
			w.Header().Set("Content-Length", strconv.Itoa(len(firstPayload)))
			_, _ = w.Write(firstPayload[:6])
		case 2:
			w.Header().Set("Content-Length", strconv.Itoa(len(replacement)-6))
			w.Header().Set("Content-Range", "bytes 6-"+strconv.Itoa(len(replacement)-1)+"/"+strconv.Itoa(len(replacement)))
			w.WriteHeader(http.StatusPartialContent)
			_, _ = w.Write(replacement[6:])
		case 3:
			if request.Header.Get("Range") != "" {
				t.Error("changed total length did not clear the partial download")
			}
			w.Header().Set("Content-Length", strconv.Itoa(len(replacement)))
			_, _ = w.Write(replacement)
		default:
			t.Errorf("unexpected request %d", requests.Load())
		}
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	err := downloadURLResumableWithPolicy(
		context.Background(), server.URL, destination, nil, nil,
		time.Millisecond, time.Second, []time.Duration{0, 0, 0},
	)
	if err != nil {
		t.Fatalf("download after total-length change failed: %v", err)
	}
	got, _ := os.ReadFile(destination)
	if !bytes.Equal(got, replacement) {
		t.Fatalf("downloaded content = %q", got)
	}
}

func TestResumableDownloadRestartsAfterRangeNotSatisfiable(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	payload := []byte("restart-after-416")
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		switch requests.Add(1) {
		case 1:
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
			_, _ = w.Write(payload[:7])
		case 2:
			if request.Header.Get("Range") == "" {
				t.Error("second request did not attempt a resume")
			}
			http.Error(w, "range unavailable", http.StatusRequestedRangeNotSatisfiable)
		case 3:
			if request.Header.Get("Range") != "" {
				t.Error("416 response did not reset the partial download")
			}
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
			_, _ = w.Write(payload)
		}
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	err := downloadURLResumableWithPolicy(
		context.Background(), server.URL, destination, nil, nil,
		time.Millisecond, time.Second, []time.Duration{0, 0, 0},
	)
	if err != nil {
		t.Fatalf("download after 416 failed: %v", err)
	}
	got, _ := os.ReadFile(destination)
	if !bytes.Equal(got, payload) {
		t.Fatalf("downloaded content = %q", got)
	}
}

func TestResumableDownloadRejectsChangedETag(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	payload := []byte("new-resource-version")
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		switch requests.Add(1) {
		case 1:
			w.Header().Set("ETag", `"v1"`)
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
			_, _ = w.Write(payload[:6])
		case 2:
			if got := request.Header.Get("If-Range"); got != `"v1"` {
				t.Errorf("If-Range = %q", got)
			}
			w.Header().Set("ETag", `"v2"`)
			w.Header().Set("Content-Range", "bytes 6-"+strconv.Itoa(len(payload)-1)+"/"+strconv.Itoa(len(payload)))
			w.WriteHeader(http.StatusPartialContent)
			_, _ = w.Write(payload[6:])
		case 3:
			if request.Header.Get("Range") != "" {
				t.Error("changed ETag did not reset the partial download")
			}
			w.Header().Set("ETag", `"v2"`)
			w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
			_, _ = w.Write(payload)
		}
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	err := downloadURLResumableWithPolicy(
		context.Background(), server.URL, destination, nil, nil,
		time.Millisecond, time.Second, []time.Duration{0, 0, 0},
	)
	if err != nil {
		t.Fatalf("download after ETag change failed: %v", err)
	}
	got, _ := os.ReadFile(destination)
	if !bytes.Equal(got, payload) {
		t.Fatalf("downloaded content = %q", got)
	}
}

func TestResumableDownloadRejectsUnexpectedInitialPartialResponse(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		w.Header().Set("Content-Range", "bytes 0-3/8")
		w.WriteHeader(http.StatusPartialContent)
		_, _ = w.Write([]byte("half"))
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	err := downloadURLResumableWithPolicy(
		context.Background(), server.URL, destination, nil, nil,
		time.Millisecond, time.Second, []time.Duration{0, 0, 0},
	)
	if !errors.Is(err, ErrDownloadRangeRejected) || requests.Load() != 4 {
		t.Fatalf("error = %v, requests = %d", err, requests.Load())
	}
	for _, path := range []string{destination, destination + ".part"} {
		if _, statErr := os.Stat(path); !os.IsNotExist(statErr) {
			t.Fatalf("unexpected download artifact %s: %v", path, statErr)
		}
	}
}

func TestResumableDownloadDoesNotRetryPermanentHTTPError(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		http.Error(w, "forbidden", http.StatusForbidden)
	}))
	defer server.Close()

	err := downloadURLResumableWithPolicy(
		context.Background(), server.URL, filepath.Join(t.TempDir(), "artifact"), nil, nil,
		time.Millisecond, time.Second, []time.Duration{0, 0, 0},
	)
	if err == nil || requests.Load() != 1 {
		t.Fatalf("error = %v, requests = %d", err, requests.Load())
	}
}

func TestResumableDownloadCancellationStopsRetryWait(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	destination := filepath.Join(t.TempDir(), "artifact")
	err := downloadURLResumableWithPolicy(
		ctx, server.URL, destination, nil,
		func(_, _ int, _ time.Duration, _ error, _ int64) { cancel() },
		time.Millisecond, time.Second, []time.Duration{time.Hour, time.Hour, time.Hour},
	)
	if !errors.Is(err, context.Canceled) || requests.Load() != 1 {
		t.Fatalf("error = %v, requests = %d", err, requests.Load())
	}
	if _, statErr := os.Stat(destination + ".part"); !os.IsNotExist(statErr) {
		t.Fatalf("partial file was not removed: %v", statErr)
	}
}

func TestResumableDownloadDoesNotRetryExpiredContext(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		requests.Add(1)
		w.Header().Set("Content-Length", "1024")
		w.WriteHeader(http.StatusOK)
		w.(http.Flusher).Flush()
		<-request.Context().Done()
	}))
	defer server.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Millisecond)
	defer cancel()
	destination := filepath.Join(t.TempDir(), "artifact")
	retryNotices := 0
	err := downloadURLResumableWithPolicy(
		ctx, server.URL, destination, nil,
		func(_, _ int, _ time.Duration, _ error, _ int64) { retryNotices++ },
		time.Millisecond, time.Second, []time.Duration{time.Hour, time.Hour, time.Hour},
	)
	if !errors.Is(err, context.DeadlineExceeded) || requests.Load() != 1 || retryNotices != 0 {
		t.Fatalf("error = %v, requests = %d, retry notices = %d", err, requests.Load(), retryNotices)
	}
	if _, statErr := os.Stat(destination + ".part"); !os.IsNotExist(statErr) {
		t.Fatalf("partial file was not removed: %v", statErr)
	}
}

func TestResumableDownloadCleansPartialAfterRetryExhaustion(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Length", "100")
		_, _ = w.Write([]byte("partial"))
	}))
	defer server.Close()

	destination := filepath.Join(t.TempDir(), "artifact")
	if err := os.WriteFile(destination, []byte("existing-good-copy"), 0o600); err != nil {
		t.Fatal(err)
	}
	err := downloadURLResumableWithPolicy(
		context.Background(), server.URL, destination, nil, nil,
		time.Millisecond, time.Second, []time.Duration{0, 0, 0},
	)
	if err == nil || !strings.Contains(err.Error(), "after 4 attempts") {
		t.Fatalf("download error = %v", err)
	}
	got, _ := os.ReadFile(destination)
	if string(got) != "existing-good-copy" {
		t.Fatalf("existing destination was replaced: %q", got)
	}
	if _, statErr := os.Stat(destination + ".part"); !os.IsNotExist(statErr) {
		t.Fatalf("partial file was not removed: %v", statErr)
	}
}

func TestPartialCleanupFailurePreservesOriginalError(t *testing.T) {
	partPath := filepath.Join(t.TempDir(), "artifact.part")
	if err := os.Mkdir(partPath, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(partPath, "child"), []byte("occupied"), 0o600); err != nil {
		t.Fatal(err)
	}

	original := errors.New("download failed")
	err := cleanupPartialDownload(partPath, original)
	if !errors.Is(err, original) {
		t.Fatalf("cleanup error lost original failure: %v", err)
	}
	if !strings.Contains(err.Error(), "remove partial download") {
		t.Fatalf("cleanup failure was not reported: %v", err)
	}
}

func TestRetryDelayPolicy(t *testing.T) {
	for range 100 {
		delay := jitteredRetryDelay(15 * time.Second)
		if delay < 12*time.Second || delay > 18*time.Second {
			t.Fatalf("jittered delay = %s", delay)
		}
	}
	for _, test := range []struct {
		value string
		want  time.Duration
	}{
		{value: "0", want: 5 * time.Second},
		{value: "2", want: 5 * time.Second},
		{value: "60", want: 60 * time.Second},
		{value: "300", want: 120 * time.Second},
	} {
		retryAfter, hasRetryAfter := parseRetryAfter(test.value, time.Now())
		err := &DownloadHTTPError{
			StatusCode:    http.StatusTooManyRequests,
			Status:        "429 Too Many Requests",
			retryAfter:    retryAfter,
			hasRetryAfter: hasRetryAfter,
		}
		if got := downloadRetryAfter(err); got != test.want {
			t.Fatalf("Retry-After %q = %s, want %s", test.value, got, test.want)
		}
	}
	now := time.Date(2026, time.August, 31, 12, 0, 0, 0, time.UTC)
	retryAt := now.Add(90 * time.Second).Format(http.TimeFormat)
	if got, ok := parseRetryAfter(retryAt, now); !ok || got != 90*time.Second {
		t.Fatalf("Retry-After date = %s, want 1m30s", got)
	}
	pastRetryAt := now.Add(-time.Minute).Format(http.TimeFormat)
	pastDelay, pastValid := parseRetryAfter(pastRetryAt, now)
	if got := downloadRetryAfter(&DownloadHTTPError{
		StatusCode:    http.StatusTooManyRequests,
		Status:        "429 Too Many Requests",
		retryAfter:    pastDelay,
		hasRetryAfter: pastValid,
	}); got != 5*time.Second {
		t.Fatalf("past Retry-After date = %s, want 5s", got)
	}
	oversized, valid := parseRetryAfter("999999999999999999", now)
	if got := downloadRetryAfter(&DownloadHTTPError{
		StatusCode:    http.StatusServiceUnavailable,
		Status:        "503 Service Unavailable",
		retryAfter:    oversized,
		hasRetryAfter: valid,
	}); got != 120*time.Second {
		t.Fatalf("oversized Retry-After = %s, want 2m0s", got)
	}
}

func TestResumedProgressRateExcludesExistingBytes(t *testing.T) {
	var downloaded atomic.Int64
	var lastByteAt atomic.Int64
	initial := int64(100 * 1024 * 1024)
	downloaded.Store(initial)
	lastByteAt.Store(time.Now().UnixNano())
	events := make(chan DownloadProgress, 2)
	stop := startDownloadProgress(
		func(progress DownloadProgress) { events <- progress },
		&downloaded, &lastByteAt, initial*2,
		time.Now().Add(-time.Second), time.Now(), initial, 5*time.Millisecond,
	)
	event := <-events
	stop(false)
	if event.BytesPerSecond != 0 {
		t.Fatalf("resumed progress rate included existing bytes: %.0f", event.BytesPerSecond)
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
