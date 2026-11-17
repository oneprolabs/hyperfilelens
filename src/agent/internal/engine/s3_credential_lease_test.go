package engine

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
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
		_ = json.NewEncoder(w).Encode(temporaryS3Identity{
			AccessKeyID: "test-access", SecretAccessKey: "test-secret",
			SessionToken: "test-token", ExpiresAt: time.Now().Add(15 * time.Minute),
		})
	}))
	defer server.Close()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: t.TempDir(), APIBaseURL: server.URL,
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
		DataDir: t.TempDir(), APIBaseURL: server.URL,
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
		})
	}))
	defer server.Close()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: t.TempDir(), APIBaseURL: server.URL,
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
}
