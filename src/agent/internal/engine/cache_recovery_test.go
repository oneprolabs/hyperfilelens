package engine

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"hyperfilelens/agent/internal/infra/kopialayout"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

func TestCacheRestartRecoveryDoesNotRequireRepositoryCredentials(t *testing.T) {
	base, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	root, parent := filepath.Join(base, "agent"), filepath.Join(base, "destination")
	for _, dir := range []string{root, parent, vfs.AgentCacheDir(root), kopialayout.ConfigDir(root), filepath.Dir(kopialayout.StatePath(root))} {
		if err := os.MkdirAll(dir, 0700); err != nil {
			t.Fatal(err)
		}
	}
	writeJSON := func(path string, value any) {
		t.Helper()
		data, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(path, data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	writeJSON(kopialayout.StatePath(root), map[string]any{"version": 1, "status": "completed", "entries": map[string]any{}})
	if err := kopialayout.ApplyPolicy(context.Background(), root, kopialayout.Policy{Version: 1, Revision: 1, Blocking: 1, Warning: 10*kopialayout.GIB + 1}); err != nil {
		t.Fatal(err)
	}
	config := filepath.Join(kopialayout.ConfigDir(root), "repo-1.config")
	writeJSON(config, map[string]any{"storage": map[string]any{"type": "s3", "config": map[string]any{"bucket": "example", "prefix": "personal/repositories/example/"}}, "caching": map[string]any{"cacheDirectory": kopialayout.CacheDir(root, config)}})
	// No Kopia executable, repository credentials or reachable object storage.
	e := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: root, Role: model.RoleAgent, KopiaPath: filepath.Join(base, "missing-kopia")}})
	if err := e.cacheVerifier(context.Background())(config); err != nil {
		t.Fatalf("local verification failed: %v", err)
	}
	logs := filepath.Join(root, "logs")
	validation, err := kopialayout.ValidateDestination(context.Background(), root, parent, logs)
	if err != nil {
		t.Fatal(err)
	}
	journal := kopialayout.Relocation{TaskID: "interrupted-personal-cache-relocation", Stage: "preparing", Validation: validation}
	writeJSON(filepath.Join(root, "data", "kopia", "cache-management", "relocation.json"), journal)
	if err := RecoverCacheRelocation(context.Background(), root, logs); err != nil {
		t.Fatal(err)
	}
	state := kopialayout.RelocationStatus(root)
	if state["stage"] != "completed" {
		t.Fatalf("restart recovery must use local verification without Kopia/credentials; stage=%v error=%v", state["stage"], state["error"])
	}
}
