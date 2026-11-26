package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"hyperfilelens/agent/internal/infra/kopialayout"
	"hyperfilelens/agent/internal/model"
)

func TestBackupCachePreflightFailsBeforePreparingRepository(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell fixture is Unix-only; disk semantics have platform tests")
	}
	root := t.TempDir()
	bin := filepath.Join(root, "fake-kopia")
	marker := filepath.Join(root, "backup-started")
	script := fmt.Sprintf("#!/bin/sh\ncase \"$*\" in *--help*) echo --[no-]cache-demand-driven;exit 0;;esac\nprintf started > %q\n", marker)
	if err := os.WriteFile(bin, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	policy := kopialayout.Policy{Version: 1, Revision: 1, Blocking: math.MaxInt64 - 10*kopialayout.GIB, Warning: math.MaxInt64}
	raw := map[string]any{"schema_version": 1, "revision": 1, "blocking_bytes": policy.Blocking, "warning_bytes": policy.Warning}
	e := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: root, KopiaPath: bin, Role: model.RoleAgent}})
	for _, kind := range []string{"backup.run", "backup.snapshot.create", "repository.policy.apply", "repository.server.start"} {
		result := e.Run(context.Background(), Command{ID: kind, Kind: kind, Payload: map[string]any{"cache_policy": raw, "cache_use_context": "backup", "repository": map[string]any{"type": "filesystem", "path": filepath.Join(root, "repository")}}}, nil)
		if result.Status != "failed" || result.Result["error_code"] != "CACHE_SPACE_INSUFFICIENT" {
			t.Fatalf("%s did not fail at preflight: %+v", kind, result)
		}
	}
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Fatal("backup or repository preparation started before blocking")
	}
}

func TestRedeliveredCachePolicyCannotUndoNewerAppliedThreshold(t *testing.T) {
	root := t.TempDir()
	e := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: root}})
	current := kopialayout.Policy{Version: 1, Revision: 2, Blocking: 2 * kopialayout.GIB, Warning: 12 * kopialayout.GIB}
	if err := kopialayout.ApplyPolicy(context.Background(), root, current); err != nil {
		t.Fatal(err)
	}
	old := kopialayout.DefaultPolicy()
	if err := e.applyCachePayload(context.Background(), Payload{Extra: map[string]any{"cache_policy": old}}); err != nil {
		t.Fatal(err)
	}
	applied, err := kopialayout.ReadPolicy(root)
	if err != nil || applied != current {
		t.Fatal("redelivered work reset newer policy")
	}
}

func TestBackupSpaceCheckReportsUnavailableMountBeforeAdmission(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix shell fixture")
	}
	root := t.TempDir()
	bin := filepath.Join(root, "kopia-probe")
	if err := os.WriteFile(bin, []byte("#!/bin/sh\necho --cache-demand-driven\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	state := filepath.Join(root, "data", "kopia", "cache-management")
	if err := os.MkdirAll(state, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(state, "active.json"), []byte(fmt.Sprintf(`{"path":%q,"generation":1,"parent_identity":"missing-mount"}`, filepath.Join(root, "cache-new"))), 0o600); err != nil {
		t.Fatal(err)
	}
	e := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: root, KopiaPath: bin, Role: model.RoleAgent}})
	result := e.Run(context.Background(), Command{Kind: "backup.run", Payload: map[string]any{"repository": map[string]any{"type": "filesystem"}}}, nil)
	if result.Status != "failed" || result.Result["error_code"] != "CACHE_SPACE_CHECK_FAILED" {
		t.Fatalf("unavailable mount was not a space-check failure: %+v", result)
	}
	check, ok := result.Result["cache_space_check"].(kopialayout.SpaceCheck)
	if !ok || check.State != "check_error" || check.Path == "" {
		t.Fatalf("unavailable space lost context: %+v", result)
	}
}

func TestCacheRelocationVerifierDoesNotNeedKopiaCredentialsOrARepositoryProcess(t *testing.T) {
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	config := filepath.Join(kopialayout.ConfigDir(root), "repo-1.config")
	if err := kopialayout.PrepareConfigDir(root, config); err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(map[string]any{
		"storage": map[string]any{"type": "filesystem", "config": map[string]any{"path": filepath.Join(root, "offline-repository")}},
		"caching": map[string]any{"cacheDirectory": kopialayout.CacheDir(root, config)},
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(config, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	e := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: root, Role: model.RoleAgent, KopiaPath: filepath.Join(root, "no-kopia-process-may-run")}})
	if err := e.cacheVerifier(context.Background())(config); err != nil {
		t.Fatal(err)
	}
	after, err := os.ReadFile(config)
	if err != nil || string(after) != string(raw) {
		t.Fatal("verification changed connection/credential material")
	}
}
