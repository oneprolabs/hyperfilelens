package engine

import (
	"context"
	"encoding/json"
	"fmt"
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

const testRepositoryLeasePath = "/api/v1/subscription/managed-storage/leases/550e8400-e29b-41d4-a716-446655440000/"

func TestRepositoryLeaseSurvivesThreeDaysOfShortIdentityRotations(t *testing.T) {
	var clock atomic.Int64
	start := time.Now()
	clock.Store(start.UnixNano())
	now := func() time.Time { return time.Unix(0, clock.Load()) }
	var calls atomic.Int32
	reached := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		call := calls.Add(1)
		if r.Header.Get("Authorization") != "Bearer original-task-grant" {
			t.Error("task grant was replaced by a cloud identity")
		}
		if call > 1 {
			clock.Add(int64(10 * time.Minute))
		}
		_ = json.NewEncoder(w).Encode(temporaryS3Identity{
			AccessKeyID: fmt.Sprintf("access-%d", call), SecretAccessKey: "temporary-secret",
			SessionToken: fmt.Sprintf("token-%d", call),
			ExpiresAt:    now().Add(15 * time.Minute), LeaseExpiresAt: now().Add(30 * time.Minute),
		})
		if call == 433 {
			close(reached)
		}
	}))
	defer server.Close()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: t.TempDir(), APIBaseURL: server.URL, NodeID: "42", NodeToken: "node-secret", OrgKey: "tenant-a",
	}})
	timing := defaultRepositoryLeaseTiming(time.Millisecond)
	timing.now = now
	ctx, cleanup, err := engine.startRepositoryCredentialLeaseWithTiming(context.Background(),
		&repositoryCredentialLease{Path: testRepositoryLeasePath, Token: "original-task-grant"}, timing)
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	select {
	case <-reached:
	case <-ctx.Done():
		t.Fatalf("multi-day execution stopped after %d rotations: %v", calls.Load(), context.Cause(ctx))
	case <-time.After(60 * time.Second):
		t.Fatalf("did not complete three simulated days: %d rotations", calls.Load())
	}
	if ctx.Err() != nil || now().Sub(start) < 72*time.Hour {
		t.Fatalf("multi-day execution stopped: %v", context.Cause(ctx))
	}
	identity, err := (fileS3CredentialsProvider{path: repositoryCredentialFile(ctx)}).Retrieve(ctx)
	if err != nil || identity.AccessKeyID == "access-1" {
		t.Fatalf("open credential provider did not read rotated identity: %v", err)
	}
}

func TestRepositoryLeaseRejectsIncompleteOrExcessiveIdentityWithoutFallback(t *testing.T) {
	for _, invalid := range []string{"missing-token", "long-cloud-ttl", "missing-lease", "long-lease-ttl", "malformed"} {
		t.Run(invalid, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				identity := temporaryS3Identity{
					AccessKeyID: "access", SecretAccessKey: "must-not-leak-secret", SessionToken: "token",
					ExpiresAt: time.Now().Add(15 * time.Minute), LeaseExpiresAt: time.Now().Add(30 * time.Minute),
				}
				switch invalid {
				case "missing-token":
					identity.SessionToken = ""
				case "long-cloud-ttl":
					identity.ExpiresAt = time.Now().Add(time.Hour)
				case "missing-lease":
					identity.LeaseExpiresAt = time.Time{}
				case "long-lease-ttl":
					identity.LeaseExpiresAt = time.Now().Add(24 * time.Hour)
				case "malformed":
					_, _ = w.Write([]byte("must-not-leak-secret"))
					return
				}
				_ = json.NewEncoder(w).Encode(identity)
			}))
			defer server.Close()
			t.Setenv("AWS_ACCESS_KEY_ID", "forbidden-parent-access")
			t.Setenv("AWS_SECRET_ACCESS_KEY", "forbidden-parent-secret")
			engine := New(staticConfigProvider{cfg: &model.AgentConfig{
				DataDir: t.TempDir(), APIBaseURL: server.URL, NodeID: "42", NodeToken: "node-secret", OrgKey: "tenant-a",
			}})
			_, _, err := engine.startRepositoryCredentialLease(context.Background(),
				&repositoryCredentialLease{Path: testRepositoryLeasePath, Token: "task-grant"})
			if err == nil || strings.Contains(err.Error(), "must-not-leak") || strings.Contains(err.Error(), "forbidden-parent") {
				t.Fatal("invalid identity accepted, parent fallback used, or secret leaked")
			}
		})
	}
}

func TestRepositoryLeaseStartupOutageIsBoundedAndCleansUp(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer server.Close()
	dataDir := t.TempDir()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: dataDir, APIBaseURL: server.URL, NodeID: "42", NodeToken: "node-secret", OrgKey: "tenant-a",
	}})
	timing := defaultRepositoryLeaseTiming(time.Millisecond)
	timing.initialWindow, timing.retryMin, timing.retryMax = 40*time.Millisecond, time.Millisecond, 5*time.Millisecond
	_, _, err := engine.startRepositoryCredentialLeaseWithTiming(context.Background(),
		&repositoryCredentialLease{Path: testRepositoryLeasePath, Token: "task-grant"}, timing)
	if err == nil || calls.Load() < 2 {
		t.Fatal("startup outage did not retry within a finite window")
	}
	matches, err := os.ReadDir(filepath.Join(kopialayout.RuntimeDir(dataDir), "storage-identities"))
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) != 0 {
		t.Fatal("failed startup left task-local credentials behind")
	}
}
