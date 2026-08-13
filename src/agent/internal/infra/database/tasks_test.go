package database

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/install"
)

func TestTaskRepoAcceptCommandIsIdempotentAndRejectsIdentityConflict(t *testing.T) {
	ctx := t.Context()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewTaskRepo(db)

	first, shouldRun, err := repo.AcceptCommand(ctx, RecordInput{
		TaskID: "backup-command-1", JobID: "snapshot-64", Kind: "backup.run",
		Payload: map[string]any{"snapshot_id": 64},
	})
	if err != nil {
		t.Fatal(err)
	}
	if !shouldRun || first.ID != "backup-command-1" {
		t.Fatalf("first acceptance = (%q, %v), want inserted", first.ID, shouldRun)
	}

	duplicate, shouldRun, err := repo.AcceptCommand(ctx, RecordInput{
		TaskID: "backup-command-1", JobID: "snapshot-64", Kind: "backup.run",
		Payload: map[string]any{"snapshot_id": 64},
	})
	if err != nil {
		t.Fatal(err)
	}
	if shouldRun || duplicate.Status != model.TaskStatusRunning {
		t.Fatalf("duplicate shouldRun=%v status=%q", shouldRun, duplicate.Status)
	}

	if _, _, err := repo.AcceptCommand(ctx, RecordInput{
		TaskID: "backup-command-1", JobID: "snapshot-65", Kind: "backup.run",
	}); err == nil {
		t.Fatal("expected reused task id with different identity to fail")
	}
}

func TestTaskRepoRepairLifecycleUpgrade(t *testing.T) {
	ctx := t.Context()
	dir := t.TempDir()
	dataDir := filepath.Join(dir, "data")
	pending := install.LifecycleUpgradeDir(dataDir)
	if err := os.MkdirAll(pending, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(pending, "package.tar.gz"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}

	db, err := Open(ctx, filepath.Join(dataDir, "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	repo := NewTaskRepo(db)
	started := time.Now().UTC()
	if err := repo.RecordCommand(ctx, RecordInput{
		TaskID:    "upgrade-1",
		Kind:      "agent.upgrade",
		StartedAt: &started,
	}); err != nil {
		t.Fatal(err)
	}

	// Pending upgrade artifacts indicate detached upgrade was scheduled.
	repaired, err := repo.RepairInterrupted(ctx, RepairOptions{DataDir: dataDir})
	if err != nil {
		t.Fatal(err)
	}
	if len(repaired) != 0 {
		t.Fatalf("repaired = %d, want 0 while upgrade is still pending", len(repaired))
	}
	task, err := repo.Get(ctx, "upgrade-1")
	if err != nil {
		t.Fatal(err)
	}
	if task.Status != model.TaskStatusRunning {
		t.Fatalf("status = %q, want running", task.Status)
	}
}

func TestTaskRepoRepairDeferredLifecycleUpgradeWithoutArtifacts(t *testing.T) {
	ctx := t.Context()
	dir := t.TempDir()
	dataDir := filepath.Join(dir, "data")
	if err := os.MkdirAll(dataDir, 0o755); err != nil {
		t.Fatal(err)
	}

	db, err := Open(ctx, filepath.Join(dataDir, "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	repo := NewTaskRepo(db)
	started := time.Now().UTC()
	if err := repo.RecordCommand(ctx, RecordInput{
		TaskID:    "upgrade-deferred",
		Kind:      "agent.upgrade",
		StartedAt: &started,
	}); err != nil {
		t.Fatal(err)
	}

	repaired, err := repo.RepairInterrupted(ctx, RepairOptions{DataDir: dataDir})
	if err != nil {
		t.Fatal(err)
	}
	if len(repaired) != 0 {
		t.Fatalf("repaired = %d, want 0 while lifecycle logs are not ready", len(repaired))
	}

	task, err := repo.Get(ctx, "upgrade-deferred")
	if err != nil {
		t.Fatal(err)
	}
	if task.Status != model.TaskStatusRunning {
		t.Fatalf("status = %q, want running", task.Status)
	}
}

func TestTaskRepoRepairDetachedUpgradeSucceeded(t *testing.T) {
	ctx := t.Context()
	dir := t.TempDir()
	dataDir := filepath.Join(dir, "data")
	logDir := filepath.Join(dataDir, "logs")
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		t.Fatal(err)
	}
	started := time.Date(2026, 7, 2, 10, 54, 40, 0, time.UTC)
	log := `[2026-07-02T10:55:32.000Z] [ OK  ] Upgrade completed successfully.
`
	if err := os.WriteFile(install.UpgradeLogPath(logDir), []byte(log), 0o644); err != nil {
		t.Fatal(err)
	}

	db, err := Open(ctx, filepath.Join(dataDir, "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	repo := NewTaskRepo(db)
	if err := repo.RecordCommand(ctx, RecordInput{
		TaskID:    "upgrade-detached",
		Kind:      "agent.upgrade",
		StartedAt: &started,
	}); err != nil {
		t.Fatal(err)
	}

	repaired, err := repo.RepairInterrupted(ctx, RepairOptions{DataDir: dataDir, LogDir: logDir})
	if err != nil {
		t.Fatal(err)
	}
	if len(repaired) != 1 {
		t.Fatalf("repaired = %d", len(repaired))
	}
	if repaired[0].Status != model.TaskStatusSucceeded {
		t.Fatalf("status = %q, want succeeded", repaired[0].Status)
	}
	if repaired[0].Error != "" {
		t.Fatalf("error = %q, want empty", repaired[0].Error)
	}

	task, err := repo.Get(ctx, "upgrade-detached")
	if err != nil {
		t.Fatal(err)
	}
	if task.Status != model.TaskStatusSucceeded {
		t.Fatalf("persisted status = %q, want succeeded", task.Status)
	}
}

func TestTaskRepoRepairSkippedForActiveTask(t *testing.T) {
	ctx := t.Context()
	dir := t.TempDir()
	db, err := Open(ctx, filepath.Join(dir, "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	repo := NewTaskRepo(db)
	if err := repo.RecordCommand(ctx, RecordInput{
		TaskID: "backup-active",
		Kind:   "backup.run",
	}); err != nil {
		t.Fatal(err)
	}

	repaired, err := repo.RepairInterrupted(ctx, RepairOptions{
		ActiveTaskIDs: map[string]struct{}{"backup-active": {}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(repaired) != 0 {
		t.Fatalf("repaired = %d, want 0 for active task", len(repaired))
	}

	task, err := repo.Get(ctx, "backup-active")
	if err != nil {
		t.Fatal(err)
	}
	if task.Status != model.TaskStatusRunning {
		t.Fatalf("status = %q, want running", task.Status)
	}
}

func TestTaskRepoRepairAndFlush(t *testing.T) {
	ctx := t.Context()
	dir := t.TempDir()
	db, err := Open(ctx, filepath.Join(dir, "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	repo := NewTaskRepo(db)

	if err := repo.RecordCommand(ctx, RecordInput{
		TaskID:  "task-1",
		JobID:   "job-1",
		Kind:    "agent.ping",
		Payload: map[string]any{"path": "/data"},
	}); err != nil {
		t.Fatal(err)
	}

	repaired, err := repo.RepairInterrupted(ctx, RepairOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if len(repaired) != 1 {
		t.Fatalf("repaired = %d", len(repaired))
	}
	if repaired[0].Status != model.TaskStatusFailed {
		t.Fatalf("status = %q", repaired[0].Status)
	}

	unreported, err := repo.ListUnreported(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(unreported) != 1 {
		t.Fatalf("unreported = %d", len(unreported))
	}

	if err := repo.MarkResultReported(ctx, "task-1"); err != nil {
		t.Fatal(err)
	}
	unreported, err = repo.ListUnreported(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(unreported) != 0 {
		t.Fatalf("expected 0 unreported, got %d", len(unreported))
	}
}

func TestTaskRepoRepairFailsInterruptedBackupAndRestoreAttempts(t *testing.T) {
	ctx := t.Context()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	repo := NewTaskRepo(db)
	for _, kind := range []string{"backup.run", "backup", "backup.snapshot.create", "restore.run"} {
		taskID := "task-" + kind
		if err := repo.RecordCommand(ctx, RecordInput{
			TaskID: taskID,
			JobID:  "job-" + kind,
			Kind:   kind,
		}); err != nil {
			t.Fatal(err)
		}
	}

	repaired, err := repo.RepairInterrupted(ctx, RepairOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if len(repaired) != 4 {
		t.Fatalf("expected four data tasks repaired, got %d", len(repaired))
	}
	for _, task := range repaired {
		if task.Status != model.TaskStatusFailed || task.Error != repairError {
			t.Fatalf("repaired task = (%q, %q), want restart failure", task.Status, task.Error)
		}
	}
}

func TestTaskRepoFinish(t *testing.T) {
	ctx := t.Context()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	repo := NewTaskRepo(db)
	if err := repo.RecordCommand(ctx, RecordInput{
		TaskID: "t2",
		Kind:   "agent.ping",
	}); err != nil {
		t.Fatal(err)
	}
	if err := repo.Finish(ctx, "t2", model.TaskStatusSucceeded, map[string]any{"pong": true}, ""); err != nil {
		t.Fatal(err)
	}
	pending, err := repo.ListUnreported(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(pending) != 1 || pending[0].Status != model.TaskStatusSucceeded {
		t.Fatalf("pending = %+v", pending)
	}
}
