package release

import (
	"bytes"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"hyperfilelens/agent/internal/model"
)

func TestReleaseQueryValuesReportsLinuxOSVersion(t *testing.T) {
	t.Parallel()
	cfg := &model.AgentConfig{
		OrgKey:    "org_test",
		NodeToken: "token",
		Role:      model.RoleGateway,
	}

	linux := releaseQueryValues(cfg, "linux", "amd64", "https://console.example", "20.04")
	if got := linux.Get("os_version"); got != "20.04" {
		t.Fatalf("linux os_version = %q, want 20.04", got)
	}

	darwin := releaseQueryValues(cfg, "darwin", "amd64", "https://console.example", "14.5")
	if got := darwin.Get("os_version"); got != "" {
		t.Fatalf("darwin os_version = %q, want empty", got)
	}
}

func TestReleaseRequestErrorDoesNotExposeSignedQuery(t *testing.T) {
	t.Parallel()
	err := &url.Error{
		Op:  "Get",
		URL: "https://console.example/release?token=secret-value",
		Err: errors.New("connection refused"),
	}

	message := sanitizeReleaseRequestError(err).Error()
	if strings.Contains(message, "secret-value") || strings.Contains(message, "token=") {
		t.Fatalf("request error exposed enrollment secret: %s", message)
	}
}

func TestFetchArtifactRejectsOversizedResponse(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write(bytes.Repeat([]byte("x"), releaseResponseLimit+1))
	}))
	defer server.Close()

	_, err := FetchArtifact(t.Context(), &model.AgentConfig{
		APIBaseURL: server.URL,
		OrgKey:     "org-a",
		NodeToken:  "token-a",
		Role:       model.RoleAgent,
	})
	if err == nil || !strings.Contains(err.Error(), "response exceeds") {
		t.Fatalf("FetchArtifact error = %v", err)
	}
}

func TestFetchArtifactWithRetryRecoversFromTemporaryServerError(t *testing.T) {
	attempts := 0
	retries := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		attempts++
		if attempts < 3 {
			w.Header().Set("Content-Type", "text/html")
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = w.Write([]byte("<html>temporary outage</html>"))
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"download_url":"https://console.example/agent","version":"1.0.0"}`))
	}))
	defer server.Close()

	artifact, err := fetchArtifactWithRetry(
		t.Context(),
		&model.AgentConfig{
			APIBaseURL: server.URL,
			OrgKey:     "org-a",
			NodeToken:  "token-a",
			Role:       model.RoleAgent,
		},
		func(_, _ int, _ error) { retries++ },
		3,
		0,
		0,
	)
	if err != nil {
		t.Fatalf("fetchArtifactWithRetry() error = %v", err)
	}
	if attempts != 3 || retries != 2 {
		t.Fatalf("attempts = %d, retries = %d, want 3 and 2", attempts, retries)
	}
	if artifact.DownloadURL != "https://console.example/agent" {
		t.Fatalf("download URL = %q", artifact.DownloadURL)
	}
}

func TestFetchArtifactDoesNotExposeHTMLServerError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte("<html><body>database is shutting down</body></html>"))
	}))
	defer server.Close()

	_, err := FetchArtifact(t.Context(), &model.AgentConfig{
		APIBaseURL: server.URL,
		OrgKey:     "org-a",
		NodeToken:  "token-a",
		Role:       model.RoleAgent,
	})
	if err == nil || !strings.Contains(err.Error(), "release API HTTP 500") {
		t.Fatalf("FetchArtifact error = %v", err)
	}
	if strings.Contains(err.Error(), "<html>") || strings.Contains(err.Error(), "database is shutting down") {
		t.Fatalf("FetchArtifact exposed HTML server details: %v", err)
	}
}

func TestFetchArtifactRetainsOnlyKnownReleaseErrorDetails(t *testing.T) {
	for _, test := range []struct {
		name        string
		status      int
		body        string
		wantDetail  string
		neverExpose string
	}{
		{
			name:       "known platform mismatch",
			status:     http.StatusConflict,
			body:       `{"error":"platform does not match enrollment token"}`,
			wantDetail: "platform does not match enrollment token",
		},
		{
			name:        "untrusted JSON",
			status:      http.StatusConflict,
			body:        `{"error":"token-a"}`,
			neverExpose: "token-a",
		},
		{
			name:        "500 JSON internal detail",
			status:      http.StatusInternalServerError,
			body:        `{"error":"database password in server error"}`,
			neverExpose: "database password in server error",
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(test.status)
				_, _ = w.Write([]byte(test.body))
			}))
			defer server.Close()

			_, err := FetchArtifact(t.Context(), &model.AgentConfig{
				APIBaseURL: server.URL,
				OrgKey:     "org-a",
				NodeToken:  "token-a",
				Role:       model.RoleAgent,
			})
			if err == nil || !strings.Contains(err.Error(), fmt.Sprintf("%d", test.status)) {
				t.Fatalf("FetchArtifact error = %v", err)
			}
			if test.wantDetail != "" && !strings.Contains(err.Error(), test.wantDetail) {
				t.Fatalf("FetchArtifact lost safe API detail: %v", err)
			}
			if test.neverExpose != "" && strings.Contains(err.Error(), test.neverExpose) {
				t.Fatalf("FetchArtifact exposed untrusted API detail: %v", err)
			}
		})
	}
}

func TestFetchArtifactWithRetryRejectsAuthorizationWithoutRetry(t *testing.T) {
	for _, code := range []int{http.StatusUnauthorized, http.StatusForbidden} {
		t.Run(http.StatusText(code), func(t *testing.T) {
			attempts := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				attempts++
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(code)
				_, _ = w.Write([]byte(`{"error":"private-server-detail"}`))
			}))
			defer server.Close()
			_, err := fetchArtifactWithRetry(
				t.Context(),
				&model.AgentConfig{
					APIBaseURL: server.URL,
					OrgKey:     "org-a",
					NodeToken:  "secret",
					Role:       model.RoleAgent,
				},
				nil, 3, 0, 0,
			)
			if err == nil || attempts != 1 || strings.Contains(err.Error(), "private-server-detail") {
				t.Fatalf("attempts = %d, error = %v; want one safe denial", attempts, err)
			}
		})
	}
}

func TestFetchArtifactWithRetryStopsAfterThreeServerErrors(t *testing.T) {
	attempts := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		attempts++
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer server.Close()
	_, err := fetchArtifactWithRetry(
		t.Context(),
		&model.AgentConfig{
			APIBaseURL: server.URL,
			OrgKey:     "org-a",
			NodeToken:  "secret",
			Role:       model.RoleAgent,
		},
		nil, 3, 0, 0,
	)
	if err == nil || attempts != 3 || !strings.Contains(err.Error(), "503 Service Unavailable") {
		t.Fatalf("attempts = %d, error = %v; want three bounded retries", attempts, err)
	}
}

func TestPreflightRetryAfterIsBounded(t *testing.T) {
	if got := releaseRetryAfter("15"); got != preflightMaxWait {
		t.Fatalf("Retry-After = %s, want %s", got, preflightMaxWait)
	}
	if got := preflightRetryWait(&ReleaseHTTPError{RetryAfter: 2 * time.Second}, 3*time.Second, preflightMaxWait); got != 2*time.Second {
		t.Fatalf("short Retry-After = %s, want 2s", got)
	}
	if got := preflightRetryWait(&ReleaseHTTPError{}, 3*time.Second, preflightMaxWait); got != 3*time.Second {
		t.Fatalf("fallback delay = %s, want 3s", got)
	}
}

func TestRetryableReleaseErrors(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		err  error
		want bool
	}{
		{name: "typed service unavailable", err: &ReleaseHTTPError{StatusCode: 503, Status: "503 Service Unavailable"}, want: true},
		{name: "typed unauthorized", err: &ReleaseHTTPError{StatusCode: 401, Status: "401 Unauthorized"}, want: false},
		{name: "typed forbidden", err: &ReleaseHTTPError{StatusCode: 403, Status: "403 Forbidden"}, want: false},
		{name: "typed conflict", err: &ReleaseHTTPError{StatusCode: 409, Status: "409 Conflict"}, want: false},
		{name: "internal server error", err: errors.New("release API HTTP 500 Internal Server Error"), want: true},
		{name: "bad gateway", err: errors.New("release API HTTP 502 Bad Gateway"), want: true},
		{name: "service unavailable", err: errors.New("release API HTTP 503 Service Unavailable"), want: true},
		{name: "gateway timeout", err: errors.New("release API HTTP 504 Gateway Timeout"), want: true},
		{name: "too many requests", err: errors.New("release API HTTP 429 Too Many Requests"), want: true},
		{name: "not found", err: errors.New("release API HTTP 404 Not Found"), want: false},
		{name: "invalid response", err: errors.New("release API response is invalid"), want: false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			if got := IsRetryableReleaseError(test.err); got != test.want {
				t.Fatalf("IsRetryableReleaseError(%v) = %t, want %t", test.err, got, test.want)
			}
		})
	}
}
