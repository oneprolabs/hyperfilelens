package performance

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"hyperfilelens/agent/internal/model"
)

func validPolicy() Policy {
	cache := 1024
	return Policy{Revision: 1, Settings: Settings{CPU: 2, Memory: 2048, Bandwidth: 20, Cache: &cache, Level: "info", Retention: 30, LogSpace: 512}}
}

func TestPolicyBoundsAndStaleRevision(t *testing.T) {
	p := validPolicy()
	if err := Validate(p); err != nil {
		t.Fatal(err)
	}
	invalid := p
	invalid.Settings.CPU = 0
	if Validate(invalid) == nil {
		t.Fatal("invalid quota accepted")
	}
	m := New(t.TempDir())
	m.policy = p
	m.apply(context.Background(), &model.AgentConfig{}, Policy{Revision: 0}, true)
	if m.Revision() != 1 {
		t.Fatal("stale revision replaced active policy")
	}
	invalid = p
	invalid.Settings.Level = "trace"
	m.apply(context.Background(), &model.AgentConfig{}, invalid, true)
	if m.Revision() != 1 {
		t.Fatal("invalid revision replaced active policy")
	}
}

func TestSharedBandwidthBudgetAndTaskWaitTracking(t *testing.T) {
	now := time.Now()
	b := bandwidthLimiter{last: now, waiting: make(map[string]time.Time)}
	if b.grant(quotaChunk, "a", 1_000_000, now) != 0 {
		t.Fatal("granted without budget")
	}
	now = now.Add(time.Second)
	if b.grant(quotaChunk, "a", 1_000_000, now) != quotaChunk {
		t.Fatal("first task did not receive credit")
	}
	if b.grant(quotaChunk, "b", 1_000_000, now) != 0 {
		t.Fatal("each task received its own budget")
	}
	if _, ok := b.waiting["b"]; !ok {
		t.Fatal("live quota wait missing")
	}
}

func TestSharedBandwidthServesWaitingTasksInOrder(t *testing.T) {
	now := time.Now()
	b := bandwidthLimiter{last: now, waiting: make(map[string]time.Time)}
	b.grant(quotaChunk, "first", 1_000_000, now)
	b.grant(quotaChunk, "second", 1_000_000, now)
	now = now.Add(time.Second)
	if b.grant(quotaChunk, "second", 1_000_000, now) != 0 {
		t.Fatal("waiting task was bypassed")
	}
	if b.grant(quotaChunk, "first", 1_000_000, now) != quotaChunk {
		t.Fatal("head was not served")
	}
	now = now.Add(time.Second)
	if b.grant(quotaChunk, "third", 1_000_000, now) != 0 {
		t.Fatal("new task starved a waiting task")
	}
	if b.grant(quotaChunk, "second", 1_000_000, now) != quotaChunk {
		t.Fatal("second task starved")
	}
}

func TestBackupOnlyEnvironmentAndNoPolicyActivationByDefault(t *testing.T) {
	previous := current.Swap(nil)
	defer current.Store(previous)
	env := map[string]string{
		"KOPIA_PASSWORD":         "test-secret",
		"HFL_BACKUP_QUOTA_URL":   "http://127.0.0.1:1",
		"HFL_BACKUP_QUOTA_TOKEN": "stale-token",
		"HFL_BACKUP_QUOTA_TASK":  "stale-task",
		"HFL_BACKUP_QUOTA_MODE":  "server",
		"HFL_RUNTIME_LOG_POLICY": "stale-policy",
	}
	result := BackupEnv("task", env)
	if result["HFL_BACKUP_DATA"] != "1" || result["HFL_BACKUP_QUOTA_URL"] != "" {
		t.Fatal(result)
	}
	for _, key := range []string{
		"HFL_BACKUP_QUOTA_TOKEN",
		"HFL_BACKUP_QUOTA_TASK",
		"HFL_BACKUP_QUOTA_MODE",
		"HFL_RUNTIME_LOG_POLICY",
	} {
		if _, exists := result[key]; exists {
			t.Fatalf("stale optional environment variable retained: %s", key)
		}
	}
	if _, exists := env["HFL_BACKUP_DATA"]; exists {
		t.Fatal("mutated caller environment")
	}
	m := New(t.TempDir())
	m.policy = validPolicy()
	m.kopiaSupported = true
	m.limiter = &bandwidthLimiter{address: "http://127.0.0.1:9999", token: "private-token", waiting: make(map[string]time.Time)}
	current.Store(m)
	result = BackupEnv("snapshot", env)
	if result["HFL_BACKUP_QUOTA_TASK"] != "snapshot" {
		t.Fatal(result)
	}
	server := ServerEnv("repository", env)
	if server["HFL_BACKUP_QUOTA_MODE"] != "server" || server["HFL_BACKUP_DATA"] != "" {
		t.Fatal(server)
	}
	if strings.Contains(string(mustJSON(t, m.Report())), "private-token") {
		t.Fatal("token leaked in report")
	}
}

func mustJSON(t *testing.T, value any) []byte {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestSynchronizationPersistsIdentityAndReportsWithoutBusinessTasks(t *testing.T) {
	var posts int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Node-Token") != "node-secret" || r.Header.Get("X-Org-Key") != "org" {
			t.Error("missing auth")
		}
		if r.Method == "GET" {
			_ = json.NewEncoder(w).Encode(map[string]any{"policy": validPolicy()})
			return
		}
		posts++
		var report Report
		if json.NewDecoder(r.Body).Decode(&report) != nil || report.Revision != 1 {
			t.Error("invalid report")
		}
		_ = json.NewEncoder(w).Encode(map[string]bool{"accepted": true})
	}))
	defer server.Close()
	root := t.TempDir()
	m := New(root)
	cfg := &model.AgentConfig{APIBaseURL: server.URL, NodeID: "25", NodeToken: "node-secret", OrgKey: "org", InstallationID: "install"}
	m.sync(context.Background(), cfg)
	raw, err := os.ReadFile(filepath.Join(root, "runtime", "performance-policy.json"))
	if err != nil {
		t.Fatal(err)
	}
	var cached cachedPolicy
	if json.Unmarshal(raw, &cached) != nil || cached.NodeID != "25" || cached.Policy.Revision != 1 {
		t.Fatal(string(raw))
	}
	if strings.Contains(string(raw), "node-secret") || strings.Contains(string(raw), `"org_key"`) ||
		strings.Contains(string(raw), `"org"`) {
		t.Fatal("credential persisted")
	}
	if posts != 1 {
		t.Fatal("missing acknowledgement")
	}
	m.sync(context.Background(), cfg)
	if posts != 2 || m.Revision() != 1 {
		t.Fatal("duplicate policy was not idempotent")
	}
}

func TestUnreachablePolicyAndUnwritablePersistenceAreFailOpen(t *testing.T) {
	m := New(t.TempDir())
	m.policy = validPolicy()
	m.sync(context.Background(), &model.AgentConfig{APIBaseURL: "http://127.0.0.1:1", NodeID: "1", NodeToken: "test", OrgKey: "org"})
	if m.Revision() != 1 {
		t.Fatal("lost last valid policy")
	}
	m = New(t.TempDir())
	if err := os.WriteFile(filepath.Dir(m.path), []byte("block runtime directory"), 0600); err != nil {
		t.Fatal(err)
	}
	m.apply(context.Background(), &model.AgentConfig{}, validPolicy(), true)
	if m.Revision() != 0 {
		t.Fatal("unpersisted policy acknowledged")
	}
}

func TestMemoryPressureNeverReturnsAdmissionFailure(t *testing.T) {
	previous := current.Load()
	defer current.Store(previous)
	m := New(t.TempDir())
	m.pressure.Store(true)
	current.Store(m)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	start := time.Now()
	BeforeWork(ctx)
	if time.Since(start) > 100*time.Millisecond {
		t.Fatal("cancelled work was delayed")
	}
}

func TestOptionalProbeAndPolicyCacheHaveReadBounds(t *testing.T) {
	var probe boundedProbe
	data := []byte(strings.Repeat("x", 128*1024))
	n, err := probe.Write(data)
	if err != nil || n != len(data) || len(probe.bytes) != 64*1024 {
		t.Fatal("unbounded probe")
	}
	path := filepath.Join(t.TempDir(), "policy.json")
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readPolicyCache(path); err == nil {
		t.Fatal("oversized optional cache was read")
	}
}
