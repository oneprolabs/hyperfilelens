package release

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"runtime"
	"strings"
	"time"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/hostinfo"
	"hyperfilelens/agent/internal/platform/tlsclient"
)

const (
	releaseRequestTimeout = 45 * time.Second
	releaseResponseLimit  = 64 * 1024
	releaseMaxAttempts    = 5
	releaseRetryDelay     = 5 * time.Second
)

// RetryHook is called before sleeping between release API retries.
type RetryHook func(attempt, maxAttempts int, err error)

// Artifact describes the package selected for this host and role.
type Artifact struct {
	DownloadURL   string
	Version       string
	DownloadSize  uint64
	RequiredSpace uint64
}

// FetchDownloadURL resolves a signed agent package URL from the enrollment release API.
func FetchDownloadURL(ctx context.Context, cfg *model.AgentConfig) (downloadURL, version string, err error) {
	artifact, err := FetchArtifact(ctx, cfg)
	return artifact.DownloadURL, artifact.Version, err
}

// FetchArtifact returns signed download and capacity metadata for preflight.
func FetchArtifact(ctx context.Context, cfg *model.AgentConfig) (Artifact, error) {
	return fetchArtifactOnce(ctx, cfg)
}

// FetchDownloadURLWithRetry resolves a release URL, retrying transient console errors.
func FetchDownloadURLWithRetry(ctx context.Context, cfg *model.AgentConfig, onRetry RetryHook) (downloadURL, version string, err error) {
	var lastErr error
	for attempt := 1; attempt <= releaseMaxAttempts; attempt++ {
		var artifact Artifact
		artifact, err = fetchArtifactOnce(ctx, cfg)
		if err == nil {
			return artifact.DownloadURL, artifact.Version, nil
		}
		lastErr = err
		if attempt >= releaseMaxAttempts || !IsRetryableReleaseError(err) {
			return "", "", err
		}
		if onRetry != nil {
			onRetry(attempt, releaseMaxAttempts, err)
		}
		select {
		case <-ctx.Done():
			return "", "", ctx.Err()
		case <-time.After(releaseRetryDelay):
		}
	}
	return "", "", lastErr
}

func fetchArtifactOnce(ctx context.Context, cfg *model.AgentConfig) (Artifact, error) {
	base := strings.TrimRight(strings.TrimSpace(cfg.APIBaseURL), "/")
	if base == "" {
		return Artifact{}, fmt.Errorf("HFL_API_BASE not configured")
	}
	if strings.TrimSpace(cfg.OrgKey) == "" || strings.TrimSpace(cfg.NodeToken) == "" {
		return Artifact{}, fmt.Errorf("HFL_ORG_KEY and HFL_NODE_TOKEN required for artifact download")
	}
	platform := runtime.GOOS
	arch := "amd64"
	if runtime.GOARCH == "arm64" {
		arch = "arm64"
	}
	osVersion := ""
	if platform == "linux" {
		osVersion = strings.TrimSpace(hostinfo.Collect(ctx).OSVersion)
	}
	q := releaseQueryValues(cfg, platform, arch, base, osVersion)
	reqURL := base + "/api/v1/node/enrollment/agent/release?" + q.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, reqURL, nil)
	if err != nil {
		return Artifact{}, errors.New("release API request is invalid")
	}
	client := &http.Client{Timeout: releaseRequestTimeout}
	if tlsclient.InsecureTLSEnabled() {
		client.Transport = tlsclient.Transport()
	}
	resp, err := client.Do(req)
	if err != nil {
		return Artifact{}, fmt.Errorf(
			"release API request failed: %w",
			sanitizeReleaseRequestError(err),
		)
	}
	defer resp.Body.Close()
	body, readErr := io.ReadAll(io.LimitReader(resp.Body, releaseResponseLimit+1))
	if readErr != nil {
		return Artifact{}, fmt.Errorf("release API response read failed: %w", readErr)
	}
	if len(body) > releaseResponseLimit {
		return Artifact{}, fmt.Errorf(
			"release API response exceeds %d bytes",
			releaseResponseLimit,
		)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return Artifact{}, fmt.Errorf(
			"release API HTTP %s: %s",
			resp.Status,
			redactReleaseSecret(strings.TrimSpace(string(body)), cfg.NodeToken),
		)
	}
	var parsed map[string]any
	if err := json.Unmarshal(body, &parsed); err != nil {
		return Artifact{}, err
	}
	data := parsed
	if nested, ok := parsed["data"].(map[string]any); ok {
		data = nested
	}
	dl, _ := data["download_url"].(string)
	ver, _ := data["version"].(string)
	if dl == "" {
		return Artifact{}, fmt.Errorf("release API missing download_url")
	}
	return Artifact{
		DownloadURL:   dl,
		Version:       ver,
		DownloadSize:  uint64Number(data["download_size"]),
		RequiredSpace: uint64Number(data["required_space"]),
	}, nil
}

func sanitizeReleaseRequestError(err error) error {
	var urlErr *url.Error
	if errors.As(err, &urlErr) && urlErr.Err != nil {
		return urlErr.Err
	}
	return err
}

func redactReleaseSecret(message, secret string) string {
	secret = strings.TrimSpace(secret)
	if secret == "" {
		return message
	}
	return strings.ReplaceAll(message, secret, "<redacted>")
}

func uint64Number(value any) uint64 {
	switch number := value.(type) {
	case float64:
		if number > 0 {
			return uint64(number)
		}
	case json.Number:
		parsed, _ := number.Int64()
		if parsed > 0 {
			return uint64(parsed)
		}
	}
	return 0
}

func releaseQueryValues(
	cfg *model.AgentConfig,
	platform string,
	arch string,
	apiBase string,
	osVersion string,
) url.Values {
	q := url.Values{
		"org":      {cfg.OrgKey},
		"role":     {string(cfg.Role)},
		"token":    {cfg.NodeToken},
		"platform": {platform},
		"arch":     {arch},
		"api_base": {apiBase},
	}
	if platform == "linux" && strings.TrimSpace(osVersion) != "" {
		q.Set("os_version", strings.TrimSpace(osVersion))
	}
	return q
}

// IsRetryableReleaseError reports whether FetchDownloadURLWithRetry should try again.
func IsRetryableReleaseError(err error) bool {
	if err == nil {
		return false
	}
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
		return true
	}
	var netErr net.Error
	if errors.As(err, &netErr) && netErr.Timeout() {
		return true
	}
	msg := strings.ToLower(err.Error())
	switch {
	case strings.Contains(msg, "500 internal server error"),
		strings.Contains(msg, "502 bad gateway"),
		strings.Contains(msg, "503 service unavailable"),
		strings.Contains(msg, "504 gateway timeout"),
		strings.Contains(msg, "504 gateway time-out"),
		strings.Contains(msg, "429 too many requests"),
		strings.Contains(msg, "connection reset"),
		strings.Contains(msg, "connection refused"),
		strings.Contains(msg, "i/o timeout"),
		strings.Contains(msg, "context deadline exceeded"):
		return true
	default:
		return false
	}
}
