package engine

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"hyperfilelens/agent/internal/infra/kopialayout"
	"hyperfilelens/agent/internal/model"
)

func TestTemporaryS3IdentityAtomicPublicationAndRefresh(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(kopialayout.RuntimeDir(root), "test-lease", "identity.json")
	identity := temporaryS3Identity{
		AccessKeyID: "first-access", SecretAccessKey: "test-secret",
		SessionToken: "test-token", ExpiresAt: time.Now().Add(time.Minute),
	}
	if err := publishTemporaryS3Identity(root, path, identity); err != nil {
		t.Fatal(err)
	}
	provider := fileS3CredentialsProvider{path: path}
	value, err := provider.Retrieve(context.Background())
	if err != nil || value.AccessKeyID != "first-access" || value.SessionToken != "test-token" {
		t.Fatalf("initial identity failed: %v", err)
	}
	identity.AccessKeyID = "second-access"
	if err := publishTemporaryS3Identity(root, path, identity); err != nil {
		t.Fatal(err)
	}
	value, err = provider.Retrieve(context.Background())
	if err != nil || value.AccessKeyID != "second-access" {
		t.Fatalf("renewed identity not read: %v", err)
	}
	identity.ExpiresAt = time.Now().Add(-time.Minute)
	if err := publishTemporaryS3Identity(root, path, identity); err != nil {
		t.Fatal(err)
	}
	if _, err := provider.Retrieve(context.Background()); err == nil {
		t.Fatal("expired identity accepted")
	}
}

func TestRepositoryCredentialLeaseOnlyCallsConfiguredControlPlane(t *testing.T) {
	const leasePath = "/api/v1/subscription/managed-storage/leases/550e8400-e29b-41d4-a716-446655440000/"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != leasePath || r.Method != http.MethodPost || r.Header.Get("Authorization") != "Bearer test-lease" {
			t.Error("unexpected lease request")
			w.WriteHeader(403)
			return
		}
		if r.Header.Get("X-Node-ID") != "42" || r.Header.Get("X-Node-Token") != "node-secret" ||
			r.Header.Get("X-Org-Key") != "tenant-a" {
			t.Error("missing execution-node identity")
			w.WriteHeader(403)
			return
		}
		_ = json.NewEncoder(w).Encode(temporaryS3Identity{
			AccessKeyID: "test-access", SecretAccessKey: "test-secret",
			SessionToken: "test-token", ExpiresAt: time.Now().Add(15 * time.Minute),
			LeaseExpiresAt: time.Now().Add(30 * time.Minute),
		})
	}))
	defer server.Close()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: t.TempDir(), APIBaseURL: server.URL, NodeID: "42", NodeToken: "node-secret", OrgKey: "tenant-a",
	}})
	ctx, cleanup, err := engine.startRepositoryCredentialLease(context.Background(), &repositoryCredentialLease{
		Path: leasePath, Token: "test-lease",
	})
	if err != nil {
		t.Fatal(err)
	}
	path := repositoryCredentialFile(ctx)
	if _, err := os.Stat(path); err != nil {
		t.Fatal(err)
	}
	cleanup()
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("temporary credentials survived task completion")
	}
	if ctx.Err() == nil {
		t.Fatal("credential lease context survived cleanup")
	}
}

func TestRepositoryCredentialLeaseRejectsRedirectAndResponseLeaks(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Location", "https://must-not-call.invalid/")
		w.WriteHeader(http.StatusTemporaryRedirect)
		_, _ = w.Write([]byte("must-not-log-test-secret"))
	}))
	defer server.Close()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: t.TempDir(), APIBaseURL: server.URL, NodeID: "42", NodeToken: "node-secret", OrgKey: "tenant-a",
	}})
	_, _, err := engine.startRepositoryCredentialLease(context.Background(), &repositoryCredentialLease{
		Path:  "/api/v1/subscription/managed-storage/leases/550e8400-e29b-41d4-a716-446655440000/",
		Token: "must-not-log-test-secret",
	})
	if err == nil || strings.Contains(err.Error(), "must-not-log") {
		t.Fatal("redirect accepted or secret leaked")
	}
}

func TestRepositoryCredentialLeasePathCannotSelectAnArbitraryEndpoint(t *testing.T) {
	for _, path := range []string{"https://evil.invalid/", "//evil.invalid/", "/other", "/../api/"} {
		if _, err := parseRepositoryCredentialLease(map[string]any{"path": path, "token": "test"}); err == nil {
			t.Fatal("arbitrary endpoint accepted")
		}
	}
}

func TestRepositoryCredentialLeaseRevocationCancelsRunningExecution(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if requests.Add(1) > 1 {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		_ = json.NewEncoder(w).Encode(temporaryS3Identity{
			AccessKeyID: "test-access", SecretAccessKey: "test-secret",
			SessionToken: "test-token", ExpiresAt: time.Now().Add(15 * time.Minute),
			LeaseExpiresAt: time.Now().Add(30 * time.Minute),
		})
	}))
	defer server.Close()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: t.TempDir(), APIBaseURL: server.URL, NodeID: "42", NodeToken: "node-secret", OrgKey: "tenant-a",
	}})
	ctx, cleanup, err := engine.startRepositoryCredentialLeaseWithInterval(
		context.Background(),
		&repositoryCredentialLease{
			Path:  "/api/v1/subscription/managed-storage/leases/550e8400-e29b-41d4-a716-446655440000/",
			Token: "test-lease",
		},
		10*time.Millisecond,
	)
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	select {
	case <-ctx.Done():
	case <-time.After(2 * time.Second):
		t.Fatal("revoked credential lease did not cancel execution")
	}
	if _, err := os.Stat(repositoryCredentialFile(ctx)); !os.IsNotExist(err) {
		t.Fatal("revoked credential file survived")
	}
	if !errors.Is(context.Cause(ctx), errRepositoryCredentialAuthorization) {
		t.Fatal("execution lost the explicit authorization failure reason")
	}
}

const userStorageLimitResponse = `{"code":403,"message":"failed","data":{"code":"SUBSCRIPTION.QUOTA_EXCEEDED","meta":{"scope":"organization_user","quota_type":"max_storage_bytes","diagnostic":"must-not-log-secret"},"title":"must-not-log-secret"}}`

func TestRepositoryCredentialDenialRecognizesOnlyUserStorageLimit(t *testing.T) {
	for _, test := range []struct {
		name, body string
		wantLimit  bool
	}{
		{"user storage", userStorageLimitResponse, true},
		{"organization", strings.ReplaceAll(userStorageLimitResponse, "organization_user", "organization"), false},
		{"other meter", strings.ReplaceAll(userStorageLimitResponse, "max_storage_bytes", "ai_tokens"), false},
		{"unknown usage", strings.ReplaceAll(userStorageLimitResponse, "QUOTA_EXCEEDED", "QUOTA_USAGE_UNAVAILABLE"), false},
		{"missing scope", `{"data":{"code":"SUBSCRIPTION.QUOTA_EXCEEDED"}}`, false},
		{"malformed", `{"data":`, false},
		{"trailing payload", userStorageLimitResponse + `{}`, false},
		{"oversized", `{"padding":"` + strings.Repeat("a", 65537) + `","data":{}}`, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			err := repositoryCredentialDenial(strings.NewReader(test.body))
			if errors.Is(err, errRepositoryUserStorageLimit) != test.wantLimit {
				t.Fatalf("unexpected quota classification: %v", err)
			}
			if strings.Contains(err.Error(), "must-not-log") {
				t.Fatal("remote response leaked into execution error")
			}
		})
	}
}

func TestRepositoryCredentialQuotaDenialStopsExecutionAndRemovesIdentity(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if requests.Add(1) > 1 {
			w.WriteHeader(http.StatusForbidden)
			_, _ = w.Write([]byte(userStorageLimitResponse))
			return
		}
		_ = json.NewEncoder(w).Encode(temporaryS3Identity{
			AccessKeyID: "access", SecretAccessKey: "secret", SessionToken: "token",
			ExpiresAt: time.Now().Add(15 * time.Minute), LeaseExpiresAt: time.Now().Add(30 * time.Minute),
		})
	}))
	defer server.Close()
	root := t.TempDir()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: root, APIBaseURL: server.URL, NodeID: "42", NodeToken: "node-secret", OrgKey: "tenant-a",
	}})
	ctx, cleanup, err := engine.startRepositoryCredentialLeaseWithInterval(
		context.Background(),
		&repositoryCredentialLease{
			Path:  "/api/v1/subscription/managed-storage/leases/550e8400-e29b-41d4-a716-446655440000/",
			Token: "test-lease",
		}, 10*time.Millisecond,
	)
	if err != nil {
		t.Fatal(err)
	}
	select {
	case <-ctx.Done():
	case <-time.After(2 * time.Second):
		t.Fatal("quota denial did not stop execution")
	}
	cleanup()
	if !errors.Is(context.Cause(ctx), errRepositoryUserStorageLimit) {
		t.Fatal("quota denial became a generic cancellation")
	}
	if _, err := os.Stat(repositoryCredentialFile(ctx)); !os.IsNotExist(err) {
		t.Fatal("quota-denied identity survived")
	}
	result := repositoryCredentialFailure(context.Cause(ctx), map[string]any{"exit_code": 0})
	if result.Status != "failed" || result.Result["error_code"] != repositoryUserStorageLimitCode ||
		result.Result["exit_code"] != 0 || strings.Contains(result.Error, "must-not-log") {
		t.Fatalf("incorrect terminal result: %#v", result)
	}
}

func TestEngineReportsQuotaDeniedBeforeStartingWork(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(userStorageLimitResponse))
	}))
	defer server.Close()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: t.TempDir(), APIBaseURL: server.URL, NodeID: "42", NodeToken: "node-secret", OrgKey: "tenant-a",
	}})
	result := engine.Run(context.Background(), Command{
		Kind: "agent.ping", Payload: map[string]any{
			"repository": map[string]any{
				"type": "s3", "bucket": "test-bucket",
				"credential_mode": "repository_temporary_identity_v1",
				"credential_lease": map[string]any{
					"path":  "/api/v1/subscription/managed-storage/leases/550e8400-e29b-41d4-a716-446655440000/",
					"token": "test-lease", "expires_at": time.Now().Add(30 * time.Minute).Format(time.RFC3339Nano),
				},
			},
		},
	}, nil)
	if result.Status != "failed" || result.Result["error_code"] != repositoryUserStorageLimitCode ||
		!strings.Contains(result.Error, "storage limit") || strings.Contains(result.Error, "must-not-log") {
		t.Fatalf("quota denial was lost: %#v", result)
	}
}

func TestEngineStopsKopiaProcessAfterQuotaDenial(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake Kopia shell script is Unix-only")
	}
	root := t.TempDir()
	started := filepath.Join(root, "started")
	bin := filepath.Join(root, "kopia")
	if err := os.WriteFile(bin, []byte(fmt.Sprintf("#!/bin/sh\ntouch %q\nexec sleep 60\n", started)), 0o700); err != nil {
		t.Fatal(err)
	}
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if requests.Add(1) > 1 {
			for {
				if _, err := os.Stat(started); err == nil {
					break
				}
				if !waitRepositoryCredentialRetry(r.Context(), 10*time.Millisecond) {
					return
				}
			}
			w.WriteHeader(http.StatusForbidden)
			_, _ = w.Write([]byte(userStorageLimitResponse))
			return
		}
		_ = json.NewEncoder(w).Encode(temporaryS3Identity{
			AccessKeyID: "access", SecretAccessKey: "secret", SessionToken: "token",
			ExpiresAt: time.Now().Add(15 * time.Minute), LeaseExpiresAt: time.Now().Add(30 * time.Minute),
		})
	}))
	defer server.Close()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: filepath.Join(root, "agent"), KopiaPath: bin,
		APIBaseURL: server.URL, NodeID: "42", NodeToken: "node-secret", OrgKey: "tenant-a",
	}})
	parent, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	ctx, cleanup, err := engine.startRepositoryCredentialLeaseWithInterval(parent, &repositoryCredentialLease{
		Path:  "/api/v1/subscription/managed-storage/leases/550e8400-e29b-41d4-a716-446655440000/",
		Token: "test-lease",
	}, 10*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	result := engine.Run(ctx, Command{
		Kind: "kopia-test", Payload: map[string]any{
			"args": []any{"snapshot", "list"}, "config_file": filepath.Join(root, "agent", "repo.config"),
		},
	}, nil)
	if _, err := os.Stat(started); err != nil {
		t.Fatal("fake Kopia process never started")
	}
	if parent.Err() != nil {
		t.Fatal("process survived quota denial until the parent deadline")
	}
	if result.Status != "failed" || result.Result["error_code"] != repositoryUserStorageLimitCode {
		t.Fatalf("process failure lost the quota reason: %#v", result)
	}
}

func TestRepositoryLeaseParseRequiresFiniteGrant(t *testing.T) {
	values := map[string]any{
		"path":  "/api/v1/subscription/managed-storage/leases/550e8400-e29b-41d4-a716-446655440000/",
		"token": "task-token",
	}
	if _, err := parseRepositoryCredentialLease(values); err == nil {
		t.Fatal("grant without expiration accepted")
	}
	values["expires_at"] = time.Now().Add(30 * time.Minute).Format(time.RFC3339Nano)
	if _, err := parseRepositoryCredentialLease(values); err != nil {
		t.Fatal(err)
	}
}

func TestRepositoryLeaseTransientFailureRecoversWithoutRemovingCredentials(t *testing.T) {
	var requests atomic.Int32
	failed := make(chan struct{})
	recovered := make(chan struct{})
	resume := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		call := requests.Add(1)
		if call == 2 {
			close(failed)
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		if call == 3 {
			select {
			case <-resume:
			case <-r.Context().Done():
				return
			}
			close(recovered)
		}
		_ = json.NewEncoder(w).Encode(temporaryS3Identity{
			AccessKeyID: "access", SecretAccessKey: "secret", SessionToken: "token",
			ExpiresAt: time.Now().Add(15 * time.Minute), LeaseExpiresAt: time.Now().Add(30 * time.Minute),
		})
	}))
	defer server.Close()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: t.TempDir(), APIBaseURL: server.URL, NodeID: "42", NodeToken: "node-secret", OrgKey: "tenant-a",
	}})
	timing := defaultRepositoryLeaseTiming(5 * time.Millisecond)
	timing.retryMin, timing.retryMax = time.Millisecond, 5*time.Millisecond
	ctx, cleanup, err := engine.startRepositoryCredentialLeaseWithTiming(context.Background(),
		&repositoryCredentialLease{Path: "/api/v1/subscription/managed-storage/leases/550e8400-e29b-41d4-a716-446655440000/", Token: "grant"}, timing)
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	select {
	case <-failed:
	case <-time.After(2 * time.Second):
		t.Fatal("refresh not attempted")
	}
	if ctx.Err() != nil {
		t.Fatal("transient failure canceled execution")
	}
	if _, err := (fileS3CredentialsProvider{path: repositoryCredentialFile(ctx)}).Retrieve(ctx); err != nil {
		t.Fatalf("still-valid credentials were removed: %v", err)
	}
	close(resume)
	select {
	case <-recovered:
	case <-time.After(2 * time.Second):
		t.Fatal("refresh did not recover")
	}
}

func TestRepositoryLeaseOutageStopsAtKnownCloudOrLeaseDeadline(t *testing.T) {
	for _, boundary := range []string{"cloud", "lease"} {
		t.Run(boundary, func(t *testing.T) {
			var clock atomic.Int64
			clock.Store(time.Now().UnixNano())
			now := func() time.Time { return time.Unix(0, clock.Load()) }
			var calls atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if calls.Add(1) > 1 {
					w.WriteHeader(http.StatusTooManyRequests)
					return
				}
				leaseExpiry := now().Add(30 * time.Minute)
				if boundary == "lease" {
					leaseExpiry = now().Add(3 * time.Minute)
				}
				_ = json.NewEncoder(w).Encode(temporaryS3Identity{
					AccessKeyID: "access", SecretAccessKey: "secret", SessionToken: "token",
					ExpiresAt: now().Add(15 * time.Minute), LeaseExpiresAt: leaseExpiry,
				})
			}))
			defer server.Close()
			engine := New(staticConfigProvider{cfg: &model.AgentConfig{
				DataDir: t.TempDir(), APIBaseURL: server.URL, NodeID: "42", NodeToken: "node-secret", OrgKey: "tenant-a",
			}})
			timing := defaultRepositoryLeaseTiming(5 * time.Millisecond)
			timing.now, timing.retryMin, timing.retryMax = now, time.Millisecond, 5*time.Millisecond
			ctx, cleanup, err := engine.startRepositoryCredentialLeaseWithTiming(context.Background(),
				&repositoryCredentialLease{Path: "/api/v1/subscription/managed-storage/leases/550e8400-e29b-41d4-a716-446655440000/", Token: "grant"}, timing)
			if err != nil {
				t.Fatal(err)
			}
			defer cleanup()
			advance := 14 * time.Minute
			if boundary == "lease" {
				advance = 2 * time.Minute
			}
			clock.Add(int64(advance))
			select {
			case <-ctx.Done():
			case <-time.After(2 * time.Second):
				t.Fatal("outage outlived the known safety deadline")
			}
			if !errors.Is(context.Cause(ctx), errRepositoryCredentialRefresh) {
				t.Fatal("missing safe refresh failure reason")
			}
			if _, err := os.Stat(repositoryCredentialFile(ctx)); !os.IsNotExist(err) {
				t.Fatal("expired execution identity survived")
			}
		})
	}
}

func TestRepositoryLeaseInitialFailureRetriesButNeverRunsWithoutIdentity(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		_ = json.NewEncoder(w).Encode(temporaryS3Identity{
			AccessKeyID: "access", SecretAccessKey: "secret", SessionToken: "token",
			ExpiresAt: time.Now().Add(15 * time.Minute), LeaseExpiresAt: time.Now().Add(30 * time.Minute),
		})
	}))
	defer server.Close()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: t.TempDir(), APIBaseURL: server.URL, NodeID: "42", NodeToken: "node-secret", OrgKey: "tenant-a",
	}})
	timing := defaultRepositoryLeaseTiming(time.Minute)
	timing.retryMin = time.Millisecond
	ctx, cleanup, err := engine.startRepositoryCredentialLeaseWithTiming(context.Background(),
		&repositoryCredentialLease{Path: "/api/v1/subscription/managed-storage/leases/550e8400-e29b-41d4-a716-446655440000/", Token: "grant"}, timing)
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	if calls.Load() != 2 || repositoryCredentialFile(ctx) == "" {
		t.Fatal("startup did not retry and publish a complete identity")
	}
}
