package enroll

import (
	"context"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestWebsocketDialAddressUsesDefaultPorts(t *testing.T) {
	t.Parallel()
	tests := map[string]string{
		"wss://hyperfilelens.com/ws/node/agent/":     "hyperfilelens.com:443",
		"ws://console.example/ws/node/agent/":        "console.example:80",
		"wss://console.example:11443/ws/node/agent/": "console.example:11443",
		"wss://[2001:db8::1]/ws/node/agent/":         "[2001:db8::1]:443",
	}
	for raw, want := range tests {
		raw, want := raw, want
		t.Run(raw, func(t *testing.T) {
			t.Parallel()
			parsed, err := url.Parse(raw)
			if err != nil {
				t.Fatal(err)
			}
			if got := websocketDialAddress(parsed); got != want {
				t.Fatalf("websocketDialAddress(%q) = %q, want %q", raw, got, want)
			}
		})
	}
}

func TestCheckWSSReachableAcceptsAuthenticationRejection(t *testing.T) {
	t.Parallel()
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		http.Error(writer, "authentication required", http.StatusForbidden)
	}))
	defer server.Close()

	result := checkWSSReachable(
		context.Background(),
		"ws"+strings.TrimPrefix(server.URL, "http")+"/ws/node/agent/",
	)

	if !result.OK {
		t.Fatalf("authentication rejection should prove route reachability: %+v", result)
	}
}

func TestCheckWSSReachableRejectsMissingRoute(t *testing.T) {
	t.Parallel()
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		requests.Add(1)
		http.NotFoundHandler().ServeHTTP(writer, request)
	}))
	defer server.Close()

	result := checkWSSReachable(
		context.Background(),
		"ws"+strings.TrimPrefix(server.URL, "http")+"/missing/",
	)

	if result.OK || !strings.Contains(result.Detail, "returned 404") {
		t.Fatalf("missing WebSocket route was not rejected: %+v", result)
	}
	if got := requests.Load(); got != 1 {
		t.Fatalf("missing route requests = %d, want 1", got)
	}
}

func TestCheckWSSReachableRetriesTransientGatewayFailure(t *testing.T) {
	t.Parallel()
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		if requests.Add(1) == 1 {
			http.Error(writer, "upstream unavailable", http.StatusBadGateway)
			return
		}
		http.Error(writer, "authentication required", http.StatusForbidden)
	}))
	defer server.Close()

	result := checkWSSReachableWithRetry(
		context.Background(),
		"ws"+strings.TrimPrefix(server.URL, "http")+"/ws/node/agent/",
		time.Second,
		[]time.Duration{0},
	)

	if !result.OK {
		t.Fatalf("transient gateway failure should recover: %+v", result)
	}
	if got := requests.Load(); got != 2 {
		t.Fatalf("transient gateway requests = %d, want 2", got)
	}
}

func TestCheckWSSReachableRejectsPersistentGatewayFailure(t *testing.T) {
	t.Parallel()
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		http.Error(writer, "upstream unavailable", http.StatusBadGateway)
	}))
	defer server.Close()

	result := checkWSSReachableWithRetry(
		context.Background(),
		"ws"+strings.TrimPrefix(server.URL, "http")+"/ws/node/agent/",
		time.Second,
		[]time.Duration{0, 0},
	)

	if result.OK || !strings.Contains(result.Detail, "returned 502") {
		t.Fatalf("persistent gateway failure was not rejected: %+v", result)
	}
	if got := requests.Load(); got != 3 {
		t.Fatalf("persistent gateway requests = %d, want 3", got)
	}
}

func TestTransientWSSFailureClassification(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name     string
		response *http.Response
		err      error
		want     bool
	}{
		{name: "bad gateway", response: &http.Response{StatusCode: http.StatusBadGateway}, want: true},
		{name: "service unavailable", response: &http.Response{StatusCode: http.StatusServiceUnavailable}, want: true},
		{name: "gateway timeout", response: &http.Response{StatusCode: http.StatusGatewayTimeout}, want: true},
		{name: "internal error", response: &http.Response{StatusCode: http.StatusInternalServerError}, want: false},
		{name: "missing route", response: &http.Response{StatusCode: http.StatusNotFound}, want: false},
		{name: "missing DNS name", err: &net.DNSError{Err: "no such host", IsNotFound: true}, want: false},
		{name: "permanent DNS error", err: &net.DNSError{Err: "invalid DNS response"}, want: false},
		{name: "temporary network error", err: &net.DNSError{Err: "temporary failure", IsTemporary: true}, want: true},
		{name: "unexpected EOF", err: io.ErrUnexpectedEOF, want: true},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			if got := transientWSSFailure(test.response, test.err); got != test.want {
				t.Fatalf("transientWSSFailure() = %t, want %t", got, test.want)
			}
		})
	}
}
