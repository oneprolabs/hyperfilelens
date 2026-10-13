package connection

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/atomicfile"
	"hyperfilelens/agent/internal/platform/lensenv"
)

func gatewayFixture(t *testing.T) (string, *serverInfo, runtimeHooks) {
	t.Helper()
	root, target, ops := fixture(t, model.RoleGateway)
	target.Lens = &lensInfo{UUID: "existing-lens", BasePath: "/sourcelens"}
	values := map[string]string{
		"LENS_BASE_URL": "http://old.invalid/sourcelens", "LENSNODE_UUID": target.Lens.UUID,
		"LENSNODE_TOKEN": "unchanged-secret", "HFL_WORKSPACE_ROOT": "/workspace", "SENTRY_ENABLED": "false",
	}
	if err := atomicfile.Write(filepath.Join(root, "config", "lensnode.env"),
		append(lensenv.Content(values), []byte("UNKNOWN_LOCAL_FIELD=keep\n")...), 0600); err != nil {
		t.Fatal(err)
	}
	if err := atomicfile.Write(filepath.Join(root, "runtime", "lensnode", "docker-compose.yml"), []byte(managedCompose), 0600); err != nil {
		t.Fatal(err)
	}
	markFixtureApplied(t, root)
	return root, target, ops
}

func captureFiles(t *testing.T, root string, gateway bool) []savedFile {
	t.Helper()
	var files []savedFile
	for _, path := range configPaths(root, gateway) {
		f, err := snapshot(path)
		if err != nil {
			t.Fatal(err)
		}
		files = append(files, f)
	}
	return files
}

func assertFilesUnchanged(t *testing.T, files []savedFile) {
	t.Helper()
	for _, f := range files {
		raw, err := os.ReadFile(f.Path)
		if f.Exists && (err != nil || string(raw) != string(f.Data)) {
			t.Fatal("configuration changed", f.Path, err)
		}
	}
}

func TestGatewayAppliedFingerprintCommittedBeforeAgentRestart(t *testing.T) {
	root, target, ops := gatewayFixture(t)
	server := serve(t, target)
	running, held := true, false
	ops.agentRunning = func(string) (bool, error) { return running, nil }
	ops.lockLens = func(context.Context) (func(), error) {
		held = true
		return func() { held = false }, nil
	}
	ops.agentService = func(_ context.Context, _ string, action string) error {
		running = action == "start"
		if action != "start" {
			return nil
		}
		if !held {
			t.Fatal("sidecar lock was released before Agent restarted")
		}
		values, err := config.ParseEnvFile(filepath.Join(root, "config", "lensnode.env"))
		if err != nil {
			return err
		}
		marker, err := os.ReadFile(lensAppliedPath(root))
		if err != nil || strings.TrimSpace(string(marker)) != lensenv.Fingerprint(values) {
			t.Fatal("Agent restarted before canonical applied state was committed", err)
		}
		_, wss, err := NormalizeServer(server.URL)
		if err != nil {
			return err
		}
		raw, _ := json.Marshal(struct {
			URL         string
			ConnectedAt time.Time
		}{wss, time.Now().UTC()})
		return atomicfile.Write(filepath.Join(root, "runtime", "connection.json"), raw, 0600)
	}
	if err := configure(context.Background(), server.URL, ops); err != nil {
		t.Fatal(err)
	}
	if held {
		t.Fatal("sidecar lock was not released after commit")
	}
	raw, err := os.ReadFile(filepath.Join(root, "config", "lensnode.env"))
	if err != nil || !strings.Contains(string(raw), "UNKNOWN_LOCAL_FIELD=keep") {
		t.Fatal("lost unknown local env fields", err)
	}
}

func TestGatewayRejectsUnappliedConfigurationBeforeChanges(t *testing.T) {
	root, target, ops := gatewayFixture(t)
	if err := atomicfile.Write(lensAppliedPath(root), []byte("pending-other-settings\n"), 0600); err != nil {
		t.Fatal(err)
	}
	before := captureFiles(t, root, true)
	ops.applyLens = func(context.Context, string, bool) error {
		t.Fatal("pending configuration was silently marked as applied")
		return nil
	}
	if err := configure(context.Background(), serve(t, target).URL, ops); err == nil {
		t.Fatal("accepted unapplied sidecar configuration")
	}
	assertFilesUnchanged(t, before)
}

func TestGatewayRollbackAndRecoveryRestoreAppliedFingerprint(t *testing.T) {
	for _, mode := range []string{"rollback", "interrupted"} {
		t.Run(mode, func(t *testing.T) {
			root, target, ops := gatewayFixture(t)
			before := captureFiles(t, root, true)
			if mode == "interrupted" {
				state := transaction{PID: 2147483646, Born: 1, Phase: "applying", Gateway: true, Files: before}
				if err := save(statePath(root), state); err != nil {
					t.Fatal(err)
				}
				for _, f := range before {
					if err := atomicfile.Write(f.Path, []byte("interrupted-write"), 0600); err != nil {
						t.Fatal(err)
					}
				}
				ops.applyLens = func(context.Context, string, bool) error {
					assertFilesUnchanged(t, before)
					return nil
				}
				if err := recoverConnection(context.Background(), root, ops); err != nil {
					t.Fatal(err)
				}
			} else {
				running, starts := true, 0
				ops.agentRunning = func(string) (bool, error) { return running, nil }
				ops.agentService = func(_ context.Context, _ string, action string) error {
					running = action == "start"
					if action == "start" {
						starts++
						if starts == 1 {
							running = false
							return errors.New("simulated restart failure")
						}
					}
					return nil
				}
				if err := configure(context.Background(), serve(t, target).URL, ops); err == nil {
					t.Fatal("expected restart failure")
				}
			}
			assertFilesUnchanged(t, before)
			if _, err := os.Stat(statePath(root)); !os.IsNotExist(err) {
				t.Fatal("completed recovery retained marker", err)
			}
		})
	}
}

func TestUnconfirmedStopsNeverWriteConfiguration(t *testing.T) {
	for _, component := range []string{"Agent", "LensNode"} {
		t.Run(component, func(t *testing.T) {
			root, target, ops := fixture(t, model.RoleAgent)
			gateway := component == "LensNode"
			if gateway {
				root, target, ops = gatewayFixture(t)
				ops.lensRunning = func(context.Context, string) (bool, error) { return true, nil }
			} else {
				ops.agentRunning = func(string) (bool, error) { return true, nil }
			}
			before := captureFiles(t, root, gateway)
			ops.agentService = func(_ context.Context, _ string, action string) error {
				if action != "stop" {
					t.Fatal("still-running Agent was started again")
				}
				return nil
			}
			ops.applyLens = func(context.Context, string, bool) error {
				t.Fatal("still-running LensNode was recreated")
				return nil
			}
			ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
			defer cancel()
			err := configure(ctx, serve(t, target).URL, ops)
			if err == nil || !strings.Contains(err.Error(), "stop was not confirmed") {
				t.Fatal("unconfirmed stop was accepted or not exercised", err)
			}
			assertFilesUnchanged(t, before)
			if _, err := os.Stat(statePath(root)); !os.IsNotExist(err) {
				t.Fatal("unchanged running components remained fenced", err)
			}
		})
	}
}

func TestRollbackCannotOverwriteFilesUntilStopConfirmed(t *testing.T) {
	root, target, ops := fixture(t, model.RoleAgent)
	target.Connected = false
	server := serve(t, target)
	running, stops := true, 0
	ops.agentRunning = func(string) (bool, error) {
		if stops >= 2 {
			return true, errors.New("rollback cannot verify stopped process")
		}
		return running, nil
	}
	ops.agentService = func(_ context.Context, _ string, action string) error {
		if action == "stop" {
			stops++
			if stops == 1 {
				running = false
			}
		} else {
			running = true
		}
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	if err := configure(ctx, server.URL, ops); err == nil {
		t.Fatal("expected verification and rollback stop failure")
	}
	values, err := config.ParseEnvFile(filepath.Join(root, "config", "agent.env"))
	if err != nil || values["HFL_API_BASE"] != server.URL {
		t.Fatal("rollback overwrote files while process was not quiescent", err)
	}
	if _, err := os.Stat(statePath(root)); err != nil {
		t.Fatal("unsafe rollback lost recovery state", err)
	}
}

func TestRecoveryValidatesSnapshotsBeforeLifecycleActions(t *testing.T) {
	root, _, ops := fixture(t, model.RoleAgent)
	files := captureFiles(t, root, false)
	ops.agentService = func(context.Context, string, string) error {
		t.Fatal("corrupted state triggered a lifecycle action")
		return nil
	}
	for _, state := range []transaction{
		{PID: 2147483646, Born: 1, Phase: "invalid", AgentRunning: true, Files: files},
		{PID: 2147483646, Born: 1, Phase: "applying", AgentRunning: true, Files: []savedFile{files[0], files[0]}},
	} {
		if err := save(statePath(root), state); err != nil {
			t.Fatal(err)
		}
		if err := recoverForCommand(context.Background(), root, ops); err == nil {
			t.Fatal("accepted invalid recovery state")
		}
		assertFilesUnchanged(t, files)
	}
}

func TestRecoveryRetainsStateWhenLockMetadataCannotBeVerified(t *testing.T) {
	root, _, ops := fixture(t, model.RoleAgent)
	files := captureFiles(t, root, false)
	if err := os.MkdirAll(configureLockPath(root), 0700); err != nil {
		t.Fatal(err)
	}
	if err := save(statePath(root), transaction{PID: 2147483646, Born: 1, Phase: "applying", Files: files}); err != nil {
		t.Fatal(err)
	}
	if err := recoverConnection(context.Background(), root, ops); err == nil {
		t.Fatal("missing lock metadata was ignored")
	}
	if _, err := os.Stat(statePath(root)); err != nil {
		t.Fatal("recovery state was discarded before lock cleanup", err)
	}
}
