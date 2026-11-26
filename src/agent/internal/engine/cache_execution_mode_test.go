package engine

import (
	"context"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"hyperfilelens/agent/internal/infra/kopialayout"
	"hyperfilelens/agent/internal/model"
)

func cacheModeTestEngine(t *testing.T, root, script string) *Engine {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("Unix fake command; applied policy and concurrency tests are portable")
	}
	bin := filepath.Join(t.TempDir(), "kopia")
	if err := os.WriteFile(bin, []byte("#!/bin/sh\n"+script), 0o700); err != nil {
		t.Fatal(err)
	}
	return New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: root, KopiaPath: bin, Role: model.RoleAgent}})
}

func TestCacheExecutionModeSeparatesAppliedPolicyFromCapability(t *testing.T) {
	for _, applied := range []bool{false, true} {
		for _, outcome := range []string{"supported", "unsupported", "failed"} {
			t.Run(fmt.Sprintf("applied=%v/%s", applied, outcome), func(t *testing.T) {
				root := t.TempDir()
				if applied {
					if err := kopialayout.ApplyPolicy(t.Context(), root, kopialayout.DefaultPolicy()); err != nil {
						t.Fatal(err)
					}
				}
				script := "echo legacy-help\n"
				if outcome == "supported" {
					script = "echo --cache-demand-driven\n"
				} else if outcome == "failed" {
					script = "exit 1\n"
				}
				e := cacheModeTestEngine(t, root, script)
				mode, err := e.resolveCacheExecutionMode(t.Context())
				if applied && outcome == "unsupported" {
					if !errors.Is(err, errCacheBinaryIncompatible) {
						t.Fatalf("downgraded binary = %+v, %v", mode, err)
					}
					return
				}
				wantEnabled := applied || outcome == "supported"
				if err != nil || mode.enabled != wantEnabled || mode.applyPolicy != (outcome == "supported") {
					t.Fatalf("mode = %+v, %v; want enabled=%v apply=%v", mode, err, wantEnabled, outcome == "supported")
				}
			})
		}
	}
}

func TestAppliedPolicyStillProtectsBackupsWhenCapabilityProbeFails(t *testing.T) {
	root := t.TempDir()
	policy := kopialayout.Policy{Version: 1, Revision: 7, Blocking: math.MaxInt64 - 10*kopialayout.GIB, Warning: math.MaxInt64}
	if err := kopialayout.ApplyPolicy(t.Context(), root, policy); err != nil {
		t.Fatal(err)
	}
	e := cacheModeTestEngine(t, root, "exit 1\n")
	for _, kind := range []string{"backup.run", "backup.snapshot.create", "repository.policy.apply", "repository.server.start"} {
		result := e.Run(t.Context(), Command{Kind: kind, Payload: map[string]any{
			"cache_use_context": "backup",
			"cache_policy":      map[string]any{"schema_version": 1, "revision": 8, "blocking_bytes": 1, "warning_bytes": 10*kopialayout.GIB + 1},
			"repository":        map[string]any{"type": "proxy_fs", "path": filepath.Join(root, "repository")},
		}}, nil)
		if result.Status != "failed" || result.Result["error_code"] != "CACHE_SPACE_INSUFFICIENT" {
			t.Fatalf("%s bypassed preflight after failed probe: %+v", kind, result)
		}
	}
	current, err := kopialayout.ReadPolicy(root)
	if err != nil || current != policy {
		t.Fatalf("unverified capability changed applied policy: %+v %v", current, err)
	}
}

func TestIncompatibleAppliedCacheBinaryReportsUpgradeWithoutDowngrading(t *testing.T) {
	root := t.TempDir()
	if err := kopialayout.ApplyPolicy(t.Context(), root, kopialayout.DefaultPolicy()); err != nil {
		t.Fatal(err)
	}
	e := cacheModeTestEngine(t, root, "echo legacy-help\n")
	result := e.Run(t.Context(), Command{Kind: "backup.run", Payload: map[string]any{"repository": map[string]any{"type": "proxy_fs", "path": filepath.Join(root, "repository")}}}, nil)
	if result.Result["error_code"] != "AGENT_UPGRADE_REQUIRED" {
		t.Fatalf("incompatible binary silently downgraded: %+v", result)
	}
	policy, applied, err := kopialayout.ReadAppliedPolicy(root)
	if err != nil || !applied || policy != kopialayout.DefaultPolicy() {
		t.Fatal("compatibility failure changed applied state")
	}
}

func TestCorruptCachePolicyFailsBackupButDoesNotBlockStopOrLocalBrowse(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "data", "kopia", "cache-management", "policy.json")
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	e := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: root, KopiaPath: filepath.Join(root, "missing-kopia"), Role: model.RoleAgent}})
	result := e.Run(t.Context(), Command{Kind: "backup.run", Payload: map[string]any{"repository": map[string]any{"type": "proxy_fs", "path": filepath.Join(root, "repository")}}}, nil)
	if result.Result["error_code"] != "CACHE_SPACE_CHECK_FAILED" {
		t.Fatalf("corrupt policy bypassed backup protection: %+v", result)
	}
	result = e.Run(t.Context(), Command{Kind: "repository.server.stop", Payload: map[string]any{"session_id": "cache-mode-not-running", "cache_use_context": "backup", "repository": map[string]any{"type": "proxy_fs", "path": filepath.Join(root, "repository")}}}, nil)
	if result.Status != "success" || result.Result["already_stopped"] != true {
		t.Fatalf("cache policy blocked stop: %+v", result)
	}
	source := t.TempDir()
	result = e.Run(t.Context(), Command{Kind: "browse", Payload: map[string]any{"path": source}}, nil)
	if result.Status != "success" {
		t.Fatalf("cache policy blocked local browse: %+v", result)
	}
}

func TestCacheManagementProbeFailureIsNotUpgradeRequired(t *testing.T) {
	e := cacheModeTestEngine(t, t.TempDir(), "echo 'secret=not-for-users' >&2\nexit 1\n")
	result := e.Run(t.Context(), Command{Kind: "cache.status"}, nil)
	if result.Status != "failed" || strings.Contains(result.Error, "UNSUPPORTED") || strings.Contains(result.Error, "not-for-users") {
		t.Fatalf("unknown capability was misreported or leaked output: %+v", result)
	}
}

func TestBackupCheckpointObservesPolicyEnabledAfterCapabilityResolution(t *testing.T) {
	root := t.TempDir()
	e := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: root, Role: model.RoleAgent}})
	if data, err := e.backupCacheCheck(t.Context(), Payload{}, nil, false); err != nil || data != nil {
		t.Fatalf("legacy node was given a new threshold: %v %v", data, err)
	}
	policy := kopialayout.Policy{Version: 1, Revision: 1, Blocking: math.MaxInt64 - 10*kopialayout.GIB, Warning: math.MaxInt64}
	if err := kopialayout.ApplyPolicy(t.Context(), root, policy); err != nil {
		t.Fatal(err)
	}
	data, err := e.backupCacheCheck(t.Context(), Payload{}, nil, false)
	if err == nil || data["error_code"] != "CACHE_SPACE_INSUFFICIENT" {
		t.Fatalf("checkpoint ignored newly applied policy: %v %v", data, err)
	}
}

func TestCacheInventoryPreservesAppliedModeWithoutAdvertisingUnverifiedCapability(t *testing.T) {
	root := t.TempDir()
	if report, supported := CacheInventory(context.Background(), root, filepath.Join(root, "missing")); report != nil || supported {
		t.Fatalf("implicit default was reported as enabled: %+v %v", report, supported)
	}
	if err := kopialayout.ApplyPolicy(t.Context(), root, kopialayout.DefaultPolicy()); err != nil {
		t.Fatal(err)
	}
	report, supported := CacheInventory(context.Background(), root, filepath.Join(root, "missing"))
	if supported || report["mode"] != "demand_driven" || report["application_state"] != "failed" {
		t.Fatalf("unknown probe lost applied mode or advertised support: %+v %v", report, supported)
	}
}

func TestRestoreKeepsAppliedModeWithoutEnforcingBackupSpaceThreshold(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix fake command")
	}
	for _, probeFails := range []bool{false, true} {
		t.Run(fmt.Sprintf("probeFails=%v", probeFails), func(t *testing.T) {
			base := t.TempDir()
			root := filepath.Join(base, "agent")
			log := filepath.Join(base, "commands.log")
			bin := writeManagedRestoreTestKopia(t, base, log, "file", "")
			script, err := os.ReadFile(bin)
			if err != nil {
				t.Fatal(err)
			}
			probe := "echo --cache-demand-driven; exit 0"
			if probeFails {
				probe = "exit 1"
			}
			script = []byte(strings.Replace(string(script), "#!/bin/sh\n", "#!/bin/sh\ncase \"$*\" in *--help*) "+probe+";; esac\n", 1))
			if err := os.WriteFile(bin, script, 0o700); err != nil {
				t.Fatal(err)
			}
			policy := kopialayout.Policy{Version: 1, Revision: 4, Blocking: math.MaxInt64 - 10*kopialayout.GIB, Warning: math.MaxInt64}
			if err := kopialayout.ApplyPolicy(t.Context(), root, policy); err != nil {
				t.Fatal(err)
			}
			e := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: root, Role: model.RoleAgent, KopiaPath: bin}})
			payload := managedRestoreTestPayload(base, filepath.Join(base, "restore-target"), "file", "overwrite")
			result := e.Run(t.Context(), Command{Kind: "restore", Payload: payload.Extra}, nil)
			if result.Status != "success" {
				t.Fatalf("backup-only threshold or unknown probe blocked restore: %+v", result)
			}
			commands, err := os.ReadFile(log)
			if err != nil {
				t.Fatal(err)
			}
			if !strings.Contains(string(commands), "--cache-demand-driven") || strings.Contains(string(commands), "--content-cache-size-mb=") {
				t.Fatalf("restore overwrote the applied cache mode: %s", commands)
			}
			current, err := kopialayout.ReadPolicy(root)
			if err != nil || current != policy {
				t.Fatalf("restore changed applied policy: %+v %v", current, err)
			}
		})
	}
}
