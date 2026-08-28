package release

import (
	"bytes"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

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

func TestReleaseResponseBodyRedactsEnrollmentSecret(t *testing.T) {
	t.Parallel()
	message := redactReleaseSecret(
		"request /release?token=secret-value was denied",
		"secret-value",
	)
	if strings.Contains(message, "secret-value") {
		t.Fatalf("response error exposed enrollment secret: %s", message)
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

func TestRetryableReleaseErrors(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		err  error
		want bool
	}{
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
