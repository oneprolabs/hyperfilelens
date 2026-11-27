package database

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/install"
	"hyperfilelens/agent/internal/platform/lifecycle"
)

func TestExistingDatabaseDoesNotCreateOrInitializeSchema(t *testing.T) {
	path := filepath.Join(t.TempDir(), "missing.db")
	if db, err := OpenExisting(context.Background(), path, false); err == nil {
		db.Close()
		t.Fatal("created missing DB")
	}
	if err := os.WriteFile(path, nil, 0600); err != nil {
		t.Fatal(err)
	}
	db, err := OpenExisting(context.Background(), path, true)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var count int
	if err := db.conn.QueryRow("select count(*) from sqlite_master where type='table'").Scan(&count); err != nil || count != 0 {
		t.Fatalf("initialized schema: count=%d err=%v", count, err)
	}
}

func TestUpgradeFailureBridgeAndOutboxWithoutReconnect(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	db, err := Open(ctx, DefaultPath(root))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewTaskRepo(db)
	start := time.Now().UTC().Add(-time.Minute)
	if err := repo.RecordCommand(ctx, RecordInput{TaskID: "upgrade", Kind: "agent.upgrade", Payload: map[string]any{"target_version": "new", "target_commit": "commit"}, StartedAt: &start, Source: "websocket"}); err != nil {
		t.Fatal(err)
	}
	if err := repo.UpdateRunning(ctx, "upgrade", map[string]any{"mode": "local_detached", "target_version": "new", "detached_started_at": start.Add(time.Second).Format(time.RFC3339Nano)}); err != nil {
		t.Fatal(err)
	}
	task, err := repo.UpgradeCandidate(ctx, "new", "commit")
	if err != nil || task == nil {
		t.Fatalf("candidate missing: %v %v", task, err)
	}
	if task, err := repo.UpgradeCandidate(ctx, "new", "other"); err != nil || task != nil {
		t.Fatalf("wrong build matched: %v %v", task, err)
	}
	a, err := lifecycle.Begin(root, lifecycle.Attempt{TaskID: "upgrade", TaskStartedAt: start.Format(time.RFC3339Nano), TargetVersion: "new", TargetCommit: "commit"})
	if err != nil {
		t.Fatal(err)
	}
	a, err = lifecycle.Fail(root, a.ID, "recovery", "AGENT_UPGRADE_RECOVERY_REQUIRED", "rollback_failed", 2)
	if err != nil {
		t.Fatal(err)
	}
	bridge, err := OpenExisting(ctx, db.Path(), false)
	if err != nil {
		t.Fatal(err)
	}
	defer bridge.Close()
	changed, err := NewTaskRepo(bridge).ApplyUpgradeFailure(ctx, a)
	if err != nil || !changed {
		t.Fatalf("bridge failed: %v %v", changed, err)
	}
	if changed, err := repo.ApplyUpgradeFailure(ctx, a); err != nil || changed {
		t.Fatalf("duplicate changed task: %v %v", changed, err)
	}
	pending, err := repo.ListUnreported(ctx, 10)
	if err != nil || len(pending) != 1 || pending[0].Status != model.TaskStatusFailed || pending[0].Result["error_code"] != "AGENT_UPGRADE_RECOVERY_REQUIRED" {
		t.Fatalf("old Agent outbox cannot deliver: %v %v", pending, err)
	}
}

func TestPeriodicLifecycleRepairDoesNotFailOrdinaryWork(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	db, err := Open(ctx, DefaultPath(root))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewTaskRepo(db)
	now := time.Now().UTC()
	if err := repo.RecordCommand(ctx, RecordInput{TaskID: "backup", Kind: "backup.snapshot.create", StartedAt: &now}); err != nil {
		t.Fatal(err)
	}
	if _, err := repo.RepairInterrupted(ctx, RepairOptions{DataDir: root, LifecycleOnly: true}); err != nil {
		t.Fatal(err)
	}
	task, err := repo.Get(ctx, "backup")
	if err != nil || task.Status != model.TaskStatusRunning {
		t.Fatalf("live backup was repaired: %v %v", task, err)
	}
}

func TestPeriodicRepairUsesAttemptEvidenceWithoutReconnect(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	db, err := Open(ctx, DefaultPath(root))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewTaskRepo(db)
	start := time.Now().UTC().Add(-time.Minute)
	if err := repo.RecordCommand(ctx, RecordInput{TaskID: "upgrade", Kind: "agent.upgrade", Payload: map[string]any{"target_version": "new"}, StartedAt: &start}); err != nil {
		t.Fatal(err)
	}
	a, err := lifecycle.Begin(root, lifecycle.Attempt{TaskID: "upgrade", TaskStartedAt: start.Format(time.RFC3339Nano), TargetVersion: "new"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := lifecycle.Fail(root, a.ID, "service_started", "AGENT_UPGRADE_STARTUP_FAILED", "rolled_back", 1); err != nil {
		t.Fatal(err)
	}
	repaired, err := repo.RepairInterrupted(ctx, RepairOptions{DataDir: root, LifecycleOnly: true})
	if err != nil || len(repaired) != 1 || repaired[0].Status != model.TaskStatusFailed {
		t.Fatalf("failure was not repaired: %v %v", repaired, err)
	}
}

func TestFailureBridgeDoesNotOverwriteCancellationOrAmbiguousTasks(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	db, err := Open(ctx, DefaultPath(root))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewTaskRepo(db)
	start := time.Now().UTC().Add(-time.Minute)
	for _, id := range []string{"one", "two"} {
		if err := repo.RecordCommand(ctx, RecordInput{TaskID: id, Kind: "agent.upgrade", Payload: map[string]any{"target_version": "new"}, StartedAt: &start, Source: "websocket"}); err != nil {
			t.Fatal(err)
		}
		if err := repo.UpdateRunning(ctx, id, map[string]any{"mode": "local_detached", "target_version": "new", "detached_started_at": start.Add(time.Second).Format(time.RFC3339Nano)}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := repo.UpgradeCandidate(ctx, "new", ""); err == nil {
		t.Fatal("guessed an ambiguous task")
	}
	if err := repo.Finish(ctx, "one", model.TaskStatusCancelled, nil, "canceled"); err != nil {
		t.Fatal(err)
	}
	a := lifecycle.Attempt{TaskID: "one", TaskStartedAt: start.Format(time.RFC3339Nano), TargetVersion: "new", FinishedAt: time.Now().UTC().Format(time.RFC3339Nano), ErrorCode: "AGENT_UPGRADE_INSTALL_FAILED", ExitCode: 1}
	if changed, err := repo.ApplyUpgradeFailure(ctx, a); err != nil || changed {
		t.Fatalf("overwrote cancellation: %v %v", changed, err)
	}
}

func TestDamagedUpgradeEvidenceDoesNotAbortStartupRepairOrInventSuccess(t *testing.T) {
	ctx := t.Context()
	root := t.TempDir()
	db, err := Open(ctx, DefaultPath(root))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewTaskRepo(db)
	started := time.Now().UTC().Add(-time.Minute)
	if err := repo.RecordCommand(ctx, RecordInput{TaskID: "healthy-task", Kind: "backup.snapshot.create", StartedAt: &started}); err != nil {
		t.Fatal(err)
	}
	if err := repo.RecordCommand(ctx, RecordInput{TaskID: "uncertain-upgrade", Kind: "agent.upgrade", StartedAt: &started}); err != nil {
		t.Fatal(err)
	}
	broken := filepath.Join(root, "lifecycle", "attempts", "00000000000000000000000000000000.json")
	if err := os.MkdirAll(filepath.Dir(broken), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(broken, []byte("{corrupt"), 0600); err != nil {
		t.Fatal(err)
	}
	logs := filepath.Join(root, "logs")
	if err := os.Mkdir(logs, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(logs, "upgrade.log"), []byte("["+time.Now().UTC().Format(time.RFC3339Nano)+"] Upgrade completed successfully\n"), 0600); err != nil {
		t.Fatal(err)
	}
	repaired, err := repo.RepairInterrupted(ctx, RepairOptions{DataDir: root, LogDir: logs})
	if err != nil || len(repaired) != 1 || repaired[0].ID != "healthy-task" {
		t.Fatalf("damaged upgrade journal blocked unrelated repair: %v %v", repaired, err)
	}
	pending, err := repo.Get(ctx, "uncertain-upgrade")
	if err != nil || pending.Status != model.TaskStatusRunning {
		t.Fatalf("uncertain upgrade was incorrectly finalized: %v %v", pending, err)
	}
}

func TestPeriodicUpgradeRepairRequiresHandoffAndCurrentFailure(t *testing.T) {
	for _, tc := range []struct {
		name      string
		handoff   bool
		markerAge time.Duration
		want      model.TaskStatus
	}{
		{"accepted but not handed off", false, 0, model.TaskStatusRunning},
		{"handed off with stale failure", true, time.Hour, model.TaskStatusRunning},
		{"handed off with current failure", true, 0, model.TaskStatusFailed},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx := t.Context()
			root := t.TempDir()
			db, err := Open(ctx, DefaultPath(root))
			if err != nil {
				t.Fatal(err)
			}
			defer db.Close()
			repo := NewTaskRepo(db)
			started := time.Now().UTC().Add(-5 * time.Second)
			if _, _, err := repo.AcceptCommand(ctx, RecordInput{TaskID: "upgrade", Kind: "agent.upgrade", StartedAt: &started}); err != nil {
				t.Fatal(err)
			}
			if tc.handoff {
				if err := repo.UpdateRunning(ctx, "upgrade", map[string]any{"mode": "local_detached", "detached_started_at": started.Add(time.Second).Format(time.RFC3339Nano)}); err != nil {
					t.Fatal(err)
				}
			}
			path := install.LifecycleUpgradeFailedPath(root)
			if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, []byte("failed"), 0600); err != nil {
				t.Fatal(err)
			}
			stamp := time.Now().Add(-tc.markerAge)
			if err := os.Chtimes(path, stamp, stamp); err != nil {
				t.Fatal(err)
			}
			if _, err := repo.RepairInterrupted(ctx, RepairOptions{DataDir: root, LifecycleOnly: true}); err != nil {
				t.Fatal(err)
			}
			row, err := repo.Get(ctx, "upgrade")
			if err != nil || row.Status != tc.want {
				t.Fatalf("got %s, want %s, error %v", row.Status, tc.want, err)
			}
		})
	}
}

func TestInstallerBridgeKeepsCurrentStructuredSpaceFailure(t *testing.T) {
	ctx := t.Context()
	root := t.TempDir()
	db, err := Open(ctx, DefaultPath(root))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewTaskRepo(db)
	started := time.Now().UTC().Add(-time.Second)
	if err := repo.RecordCommand(ctx, RecordInput{TaskID: "upgrade", Kind: "agent.upgrade", StartedAt: &started, Payload: map[string]any{"target_version": "next"}}); err != nil {
		t.Fatal(err)
	}
	if err := install.RecordUpgradeSpaceFailure(root, &install.UpgradeSpaceError{Code: install.UpgradeSpaceInsufficient, CollectedAt: time.Now().UTC(), Checks: []install.UpgradeSpaceCheck{{Scope: "agent_install", Path: root, RequiredBytes: install.DefaultUpgradeMinFreeBytes}}}); err != nil {
		t.Fatal(err)
	}
	a := lifecycle.Attempt{ID: "current", TaskID: "upgrade", TaskStartedAt: started.Format(time.RFC3339Nano), TargetVersion: "next", ErrorCode: "AGENT_UPGRADE_INSTALL_FAILED", ExitCode: 2, FinishedAt: time.Now().UTC().Format(time.RFC3339Nano)}
	if changed, err := repo.ApplyUpgradeFailure(ctx, a); err != nil || !changed {
		t.Fatalf("complete failed: %v %v", changed, err)
	}
	row, err := repo.Get(ctx, "upgrade")
	if err != nil || row.Result["error_code"] != install.UpgradeSpaceInsufficient || row.Result["old_agent_unchanged"] != true || row.Result["attempt_id"] != "current" {
		t.Fatalf("structured diagnostics lost: %#v %v", row.Result, err)
	}
}
