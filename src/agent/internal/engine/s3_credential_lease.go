package engine

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand/v2"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"

	"hyperfilelens/agent/internal/infra/kopialayout"
	"hyperfilelens/agent/internal/platform/tlsclient"
)

type s3CredentialFileContextKey struct{}

type temporaryS3Identity struct {
	AccessKeyID     string    `json:"access_key_id"`
	SecretAccessKey string    `json:"secret_access_key"`
	SessionToken    string    `json:"session_token"`
	ExpiresAt       time.Time `json:"expires_at"`
	LeaseExpiresAt  time.Time `json:"lease_expires_at,omitempty"`
}

type repositoryCredentialLease struct {
	Path      string
	Token     string
	ExpiresAt time.Time
}

var repositoryCredentialLeasePath = regexp.MustCompile(`^/api/v1/subscription/managed-storage/leases/[a-f0-9-]{36}/$`)

func parseRepositoryCredentialLease(raw any) (*repositoryCredentialLease, error) {
	if raw == nil {
		return nil, nil
	}
	values, ok := raw.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("invalid repository credential lease")
	}
	lease := &repositoryCredentialLease{
		Path: stringValue(values["path"]), Token: stringValue(values["token"]),
	}
	expiry, err := time.Parse(time.RFC3339Nano, stringValue(values["expires_at"]))
	lease.ExpiresAt = expiry
	if !repositoryCredentialLeasePath.MatchString(lease.Path) || lease.Token == "" || err != nil {
		return nil, fmt.Errorf("invalid repository credential lease")
	}
	return lease, nil
}

var (
	errRepositoryCredentialAuthorization = errors.New("temporary storage credential authorization failed")
	errRepositoryCredentialRefresh       = errors.New("temporary storage credential refresh failed before the safety deadline")
	errRepositoryUserStorageLimit        = errors.New("the repository owner's backup storage limit has been reached; review Resource Usage or contact your administrator")
)

const repositoryUserStorageLimitCode = "USER_BACKUP_STORAGE_LIMIT_REACHED"

// Only recognize the exact control-plane quota contract. Never return remote
// titles, diagnostics, metadata or response bodies in task results or logs.
func repositoryCredentialDenial(body io.Reader) error {
	type problem struct {
		Code string `json:"code"`
		Meta struct {
			Scope     string `json:"scope"`
			QuotaType string `json:"quota_type"`
		} `json:"meta"`
	}
	var envelope struct {
		Data problem `json:"data"`
	}
	decoder := json.NewDecoder(io.LimitReader(body, 65537))
	if decoder.Decode(&envelope) == nil && decoder.Decode(new(any)) == io.EOF &&
		envelope.Data.Code == "SUBSCRIPTION.QUOTA_EXCEEDED" &&
		envelope.Data.Meta.Scope == "organization_user" &&
		envelope.Data.Meta.QuotaType == "max_storage_bytes" {
		return errRepositoryUserStorageLimit
	}
	return credentialRefreshError{}
}

func repositoryCredentialFailure(err error, result map[string]any) Result {
	if errors.Is(err, errRepositoryUserStorageLimit) {
		if result == nil {
			result = make(map[string]any)
		}
		result["error_code"] = repositoryUserStorageLimitCode
	}
	return Result{Status: "failed", Result: result, Error: err.Error()}
}

type credentialRefreshError struct {
	retryable bool
}

func (e credentialRefreshError) Error() string {
	if e.retryable {
		return "temporary storage credential service is unavailable"
	}
	return errRepositoryCredentialAuthorization.Error()
}

// Internal timings, not user-tunable security switches. Tests can advance the
// clock without waiting days or shortening real cloud credential lifetimes.
type repositoryLeaseTiming struct {
	poll, retryMin, retryMax, safetyMargin, initialWindow time.Duration
	now                                                   func() time.Time
}

func defaultRepositoryLeaseTiming(interval time.Duration) repositoryLeaseTiming {
	return repositoryLeaseTiming{
		poll: interval, retryMin: time.Second, retryMax: 15 * time.Second,
		safetyMargin: time.Minute, initialWindow: 2 * time.Minute, now: time.Now,
	}
}

func (t repositoryLeaseTiming) delay(failures int) time.Duration {
	delay := t.poll
	if failures > 0 {
		delay = t.retryMin
		for i := 1; i < failures && delay < t.retryMax; i++ {
			delay = min(delay*2, t.retryMax)
		}
	}
	return time.Duration(float64(delay) * (0.9 + rand.Float64()*0.2))
}

func waitRepositoryCredentialRetry(ctx context.Context, delay time.Duration) bool {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}

func repositoryCredentialFile(ctx context.Context) string {
	path, _ := ctx.Value(s3CredentialFileContextKey{}).(string)
	return path
}

// startRepositoryCredentialLease owns one task-local file and cancels execution
// if reauthorization/renewal fails. Credentials are never put into a command
// line, progress report, or persisted Kopia configuration.
func (e *Engine) startRepositoryCredentialLease(
	ctx context.Context, lease *repositoryCredentialLease,
) (context.Context, func(), error) {
	return e.startRepositoryCredentialLeaseWithInterval(ctx, lease, 30*time.Second)
}

func (e *Engine) startRepositoryCredentialLeaseWithInterval(
	ctx context.Context, lease *repositoryCredentialLease, interval time.Duration,
) (context.Context, func(), error) {
	return e.startRepositoryCredentialLeaseWithTiming(ctx, lease, defaultRepositoryLeaseTiming(interval))
}

func (e *Engine) startRepositoryCredentialLeaseWithTiming(
	ctx context.Context, lease *repositoryCredentialLease, timing repositoryLeaseTiming,
) (context.Context, func(), error) {
	if lease == nil {
		return ctx, func() {}, nil
	}
	base, err := url.Parse(e.current().APIBaseURL)
	if err != nil || base.Host == "" || base.User != nil || base.RawQuery != "" || base.Fragment != "" {
		return ctx, nil, fmt.Errorf("invalid control-plane URL for credential renewal")
	}
	if base.Scheme != "https" {
		ip := net.ParseIP(base.Hostname())
		if base.Scheme != "http" || (base.Hostname() != "localhost" && (ip == nil || !ip.IsLoopback())) {
			return ctx, nil, fmt.Errorf("credential renewal requires HTTPS")
		}
	}
	// The signed task contains a relative path only; the destination is always
	// this Agent's configured control plane. Never follow a redirect with secrets.
	base.Path, base.RawPath = lease.Path, ""
	client := &http.Client{
		Timeout: 20 * time.Second,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	if tlsclient.InsecureTLSEnabled() {
		client.Transport = tlsclient.Transport()
	}
	taskCtx, cancel := context.WithCancelCause(ctx)
	installationRoot := agentDataDir(e.current())
	root := filepath.Join(kopialayout.RuntimeDir(installationRoot), "storage-identities")
	if err := kopialayout.PreparePrivateRuntimeDir(installationRoot, root); err != nil {
		cancel(nil)
		return ctx, nil, fmt.Errorf("cannot prepare temporary storage credentials")
	}
	dir, err := os.MkdirTemp(root, "lease-")
	if err != nil {
		cancel(nil)
		return ctx, nil, fmt.Errorf("cannot prepare temporary storage credentials")
	}
	path := filepath.Join(dir, "identity.json")
	deadline := timing.now().Add(timing.initialWindow)
	if !lease.ExpiresAt.IsZero() {
		deadline = minTime(deadline, lease.ExpiresAt.Add(-timing.safetyMargin))
	}
	fetch := func() error {
		remaining := deadline.Sub(timing.now())
		if remaining <= 0 {
			return errRepositoryCredentialRefresh
		}
		requestCtx, requestCancel := context.WithTimeout(taskCtx, min(remaining, 20*time.Second))
		defer requestCancel()
		req, err := http.NewRequestWithContext(requestCtx, http.MethodPost, base.String(), nil)
		if err != nil {
			return fmt.Errorf("cannot request temporary storage credentials")
		}
		req.Header.Set("Authorization", "Bearer "+lease.Token)
		req.Header.Set("Accept", "application/json")
		// Read current enrollment material on each attempt so credential rotation
		// does not require restarting a multi-day Kopia process.
		cfg := e.current()
		if cfg.NodeID == "" || cfg.NodeToken == "" || cfg.OrgKey == "" {
			return errRepositoryCredentialAuthorization
		}
		req.Header.Set("X-Node-ID", cfg.NodeID)
		req.Header.Set("X-Node-Token", cfg.NodeToken)
		req.Header.Set("X-Org-Key", cfg.OrgKey)
		response, err := client.Do(req)
		if err != nil {
			return credentialRefreshError{retryable: true}
		}
		defer response.Body.Close()
		if response.StatusCode != http.StatusOK {
			if response.StatusCode == http.StatusForbidden {
				return repositoryCredentialDenial(response.Body)
			}
			// Do not include the response body or authorization in an error.
			return credentialRefreshError{retryable: response.StatusCode == http.StatusRequestTimeout ||
				response.StatusCode == http.StatusTooManyRequests || response.StatusCode >= 500 && response.StatusCode <= 599}
		}
		var identity temporaryS3Identity
		decoder := json.NewDecoder(io.LimitReader(response.Body, 65537))
		if err := decoder.Decode(&identity); err != nil || decoder.Decode(new(any)) != io.EOF {
			return fmt.Errorf("invalid temporary storage credential response")
		}
		now := timing.now()
		if identity.AccessKeyID == "" || identity.SecretAccessKey == "" || identity.SessionToken == "" ||
			!now.Add(60*time.Second).Before(identity.ExpiresAt) || identity.ExpiresAt.After(now.Add(960*time.Second)) ||
			!now.Add(timing.safetyMargin).Before(identity.LeaseExpiresAt) ||
			identity.LeaseExpiresAt.After(now.Add(31*time.Minute)) {
			return fmt.Errorf("temporary storage credentials expired or incomplete")
		}
		// A response that arrived after the previous safety deadline cannot
		// revive an execution which should already have stopped.
		if !now.Before(deadline) {
			return errRepositoryCredentialRefresh
		}
		if err := publishTemporaryS3Identity(installationRoot, path, identity); err != nil {
			return err
		}
		deadline = minTime(identity.ExpiresAt, identity.LeaseExpiresAt).Add(-timing.safetyMargin)
		return nil
	}
	// Start-up outages get the same bounded retry treatment, but no workload
	// starts before the first authenticated, complete identity is available.
	for failures := 0; ; {
		err := fetch()
		if err == nil {
			break
		}
		var refreshErr credentialRefreshError
		if !errors.As(err, &refreshErr) || !refreshErr.retryable || !timing.now().Before(deadline) {
			cancel(nil)
			_ = os.RemoveAll(dir)
			return ctx, nil, err
		}
		failures++
		if !waitRepositoryCredentialRetry(taskCtx, min(timing.delay(failures), deadline.Sub(timing.now()))) {
			cancel(nil)
			_ = os.RemoveAll(dir)
			return ctx, nil, taskCtx.Err()
		}
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		failures := 0
		for {
			if !waitRepositoryCredentialRetry(taskCtx, min(timing.delay(failures), deadline.Sub(timing.now()))) {
				return
			}
			err := fetch()
			if err == nil {
				failures = 0
				continue
			}
			var refreshErr credentialRefreshError
			if errors.As(err, &refreshErr) && refreshErr.retryable && timing.now().Before(deadline) {
				failures++
				continue
			}
			// Explicit denials and malformed identities fail closed; transient
			// failures are tolerated only while both known grants remain safe.
			_ = os.Remove(path)
			cause := errRepositoryCredentialAuthorization
			if errors.Is(err, errRepositoryCredentialRefresh) || errors.As(err, &refreshErr) && refreshErr.retryable {
				cause = errRepositoryCredentialRefresh
			} else if errors.Is(err, errRepositoryUserStorageLimit) {
				cause = errRepositoryUserStorageLimit
			}
			cancel(cause)
			return
		}
	}()
	cleanup := func() {
		cancel(nil)
		<-done
		_ = os.RemoveAll(dir)
	}
	return context.WithValue(taskCtx, s3CredentialFileContextKey{}, path), cleanup, nil
}

func minTime(a, b time.Time) time.Time {
	if a.Before(b) {
		return a
	}
	return b
}

func publishTemporaryS3Identity(root, path string, identity temporaryS3Identity) error {
	data, err := json.Marshal(identity)
	if err != nil {
		return fmt.Errorf("cannot encode temporary storage credentials")
	}
	if err := kopialayout.WritePrivateRuntimeFile(root, path, data); err != nil {
		return fmt.Errorf("cannot publish temporary storage credentials")
	}
	return nil
}

// The cleanup/ownership client also uses the live identity, not a static copy
// taken before a potentially long operation.
type fileS3CredentialsProvider struct{ path string }

func (p fileS3CredentialsProvider) Retrieve(context.Context) (aws.Credentials, error) {
	file, err := os.Open(p.path)
	if err != nil {
		return aws.Credentials{}, fmt.Errorf("temporary storage credentials unavailable")
	}
	defer file.Close()
	var identity temporaryS3Identity
	decoder := json.NewDecoder(io.LimitReader(file, 65537))
	if err := decoder.Decode(&identity); err != nil || decoder.Decode(new(any)) != io.EOF ||
		identity.AccessKeyID == "" || identity.SecretAccessKey == "" || identity.SessionToken == "" ||
		!time.Now().Add(5*time.Second).Before(identity.ExpiresAt) {
		return aws.Credentials{}, fmt.Errorf("temporary storage credentials expired or incomplete")
	}
	return aws.Credentials{
		AccessKeyID: identity.AccessKeyID, SecretAccessKey: identity.SecretAccessKey,
		SessionToken: identity.SessionToken, CanExpire: true, Expires: identity.ExpiresAt,
		Source: "repository-credential-lease",
	}, nil
}
