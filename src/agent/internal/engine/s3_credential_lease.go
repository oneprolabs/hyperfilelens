package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
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
}

type repositoryCredentialLease struct {
	Path  string
	Token string
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
	if !repositoryCredentialLeasePath.MatchString(lease.Path) || lease.Token == "" {
		return nil, fmt.Errorf("invalid repository credential lease")
	}
	return lease, nil
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
	taskCtx, cancel := context.WithCancel(ctx)
	installationRoot := agentDataDir(e.current())
	root := filepath.Join(kopialayout.RuntimeDir(installationRoot), "storage-identities")
	if err := kopialayout.PreparePrivateRuntimeDir(installationRoot, root); err != nil {
		cancel()
		return ctx, nil, fmt.Errorf("cannot prepare temporary storage credentials")
	}
	dir, err := os.MkdirTemp(root, "lease-")
	if err != nil {
		cancel()
		return ctx, nil, fmt.Errorf("cannot prepare temporary storage credentials")
	}
	path := filepath.Join(dir, "identity.json")
	fetch := func() error {
		req, err := http.NewRequestWithContext(taskCtx, http.MethodPost, base.String(), nil)
		if err != nil {
			return fmt.Errorf("cannot request temporary storage credentials")
		}
		req.Header.Set("Authorization", "Bearer "+lease.Token)
		req.Header.Set("Accept", "application/json")
		response, err := client.Do(req)
		if err != nil {
			return fmt.Errorf("temporary storage credential request failed")
		}
		defer response.Body.Close()
		if response.StatusCode != http.StatusOK {
			// Do not include the response body or authorization in an error.
			return fmt.Errorf("temporary storage credential authorization failed")
		}
		var identity temporaryS3Identity
		decoder := json.NewDecoder(io.LimitReader(response.Body, 65537))
		if err := decoder.Decode(&identity); err != nil || decoder.Decode(new(any)) != io.EOF {
			return fmt.Errorf("invalid temporary storage credential response")
		}
		now := time.Now()
		if identity.AccessKeyID == "" || identity.SecretAccessKey == "" || identity.SessionToken == "" ||
			!now.Add(60*time.Second).Before(identity.ExpiresAt) || identity.ExpiresAt.After(now.Add(3660*time.Second)) {
			return fmt.Errorf("temporary storage credentials expired or incomplete")
		}
		return publishTemporaryS3Identity(installationRoot, path, identity)
	}
	if err := fetch(); err != nil {
		cancel()
		_ = os.RemoveAll(dir)
		return ctx, nil, err
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-taskCtx.Done():
				return
			case <-ticker.C:
				if err := fetch(); err != nil {
					// Removing the file also prevents any in-flight Kopia client
					// from reusing a previously loaded identity on a new request.
					_ = os.Remove(path)
					cancel()
					return
				}
			}
		}
	}()
	cleanup := func() {
		cancel()
		<-done
		_ = os.RemoveAll(dir)
	}
	return context.WithValue(taskCtx, s3CredentialFileContextKey{}, path), cleanup, nil
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
