package database

import (
	"fmt"
	"os"
	"path/filepath"
	"reflect"
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

	unreported, err := repo.ListUnreported(ctx, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(unreported) != 1 {
		t.Fatalf("unreported = %d", len(unreported))
	}

	if err := repo.MarkResultReported(ctx, "task-1"); err != nil {
		t.Fatal(err)
	}
	unreported, err = repo.ListUnreported(ctx, 0)
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
	pending, err := repo.ListUnreported(ctx, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(pending) != 1 || pending[0].Status != model.TaskStatusSucceeded {
		t.Fatalf("pending = %+v", pending)
	}
}

func TestTaskRepoListUnreportedMixesRecentAndOldestWithinLimit(t *testing.T) {
	ctx := t.Context()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewTaskRepo(db)
	base := time.Date(2026, time.August, 20, 1, 0, 0, 0, time.UTC)
	for i := 0; i < 12; i++ {
		taskID := fmt.Sprintf("task-%02d", i)
		if err := repo.RecordCommand(ctx, RecordInput{TaskID: taskID, Kind: "backup.run"}); err != nil {
			t.Fatal(err)
		}
		if err := repo.Finish(ctx, taskID, model.TaskStatusSucceeded, map[string]any{"index": i}, ""); err != nil {
			t.Fatal(err)
		}
		if _, err := db.conn.ExecContext(
			ctx,
			"UPDATE tasks SET updated_at=? WHERE id=?",
			formatTime(base.Add(time.Duration(i)*time.Second)),
			taskID,
		); err != nil {
			t.Fatal(err)
		}
	}

	pending, err := repo.ListUnreported(ctx, 8)
	if err != nil {
		t.Fatal(err)
	}
	got := make([]string, 0, len(pending))
	for _, task := range pending {
		got = append(got, task.ID)
	}
	want := []string{"task-11", "task-10", "task-00", "task-01", "task-02", "task-03", "task-04", "task-05"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("mixed pending IDs = %v, want %v", got, want)
	}
}

func TestTaskRepoFinishIfActiveDoesNotOverwriteTerminalResult(t *testing.T) {
	ctx := t.Context()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	repo := NewTaskRepo(db)
	if err := repo.RecordCommand(ctx, RecordInput{TaskID: "terminal-1", Kind: "backup.run"}); err != nil {
		t.Fatal(err)
	}
	stored, err := repo.FinishIfActive(
		ctx,
		"terminal-1",
		model.TaskStatusSucceeded,
		map[string]any{"kopia_snapshot_id": "snapshot-1"},
		"",
	)
	if err != nil || !stored {
		t.Fatalf("first finish stored=%v err=%v", stored, err)
	}
	stored, err = repo.FinishIfActive(
		ctx,
		"terminal-1",
		model.TaskStatusCancelled,
		map[string]any{},
		"canceled",
	)
	if err != nil || stored {
		t.Fatalf("competing finish stored=%v err=%v", stored, err)
	}
	task, err := repo.Get(ctx, "terminal-1")
	if err != nil {
		t.Fatal(err)
	}
	if task.Status != model.TaskStatusSucceeded || task.Result["kopia_snapshot_id"] != "snapshot-1" {
		t.Fatalf("terminal result changed: %#v", task)
	}
}

func TestTaskRepoSealCancelledExecutorResultRequeuesTerminalEvidence(t *testing.T) {
	ctx := t.Context()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	repo := NewTaskRepo(db)
	if err := repo.RecordCommand(ctx, RecordInput{TaskID: "restore-cancelled", Kind: "restore.run"}); err != nil {
		t.Fatal(err)
	}
	if _, changed, err := repo.MarkCancelledIfActive(ctx, "restore-cancelled"); err != nil || !changed {
		t.Fatalf("cancel changed=%v err=%v", changed, err)
	}
	if err := repo.MarkResultReported(ctx, "restore-cancelled"); err != nil {
		t.Fatal(err)
	}

	stored, err := repo.SealCancelledExecutorResult(
		ctx,
		"restore-cancelled",
		map[string]any{
			"executor_finished": true,
			"completion_source": "agent_executor",
		},
		"canceled",
	)
	if err != nil || !stored {
		t.Fatalf("seal stored=%v err=%v", stored, err)
	}
	task, err := repo.Get(ctx, "restore-cancelled")
	if err != nil {
		t.Fatal(err)
	}
	if task.Status != model.TaskStatusCancelled || task.ResultReported {
		t.Fatalf("sealed task status=%q reported=%v", task.Status, task.ResultReported)
	}
	if task.Result["executor_finished"] != true || task.Result["completion_source"] != "agent_executor" {
		t.Fatalf("sealed result=%#v", task.Result)
	}
}

func TestTaskRepoSealCancelledExecutorResultDoesNotOverwriteSuccess(t *testing.T) {
	ctx := t.Context()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	repo := NewTaskRepo(db)
	if err := repo.RecordCommand(ctx, RecordInput{TaskID: "restore-success", Kind: "restore.run"}); err != nil {
		t.Fatal(err)
	}
	stored, err := repo.FinishIfActive(
		ctx,
		"restore-success",
		model.TaskStatusSucceeded,
		map[string]any{"restored": true},
		"",
	)
	if err != nil || !stored {
		t.Fatalf("finish stored=%v err=%v", stored, err)
	}
	stored, err = repo.SealCancelledExecutorResult(
		ctx,
		"restore-success",
		map[string]any{"executor_finished": true},
		"canceled",
	)
	if err != nil || stored {
		t.Fatalf("seal stored=%v err=%v", stored, err)
	}
	task, err := repo.Get(ctx, "restore-success")
	if err != nil {
		t.Fatal(err)
	}
	if task.Status != model.TaskStatusSucceeded || task.Result["restored"] != true {
		t.Fatalf("successful terminal result changed: %#v", task)
	}
}
