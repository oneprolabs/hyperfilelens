package wire

import (
	"context"
	"path/filepath"
	"testing"

	"hyperfilelens/agent/internal/controller"
	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/model"
)

func TestIsManagedWorkspaceRestoreCommand(t *testing.T) {
	managed := &TaskCommand{
		Kind: "restore.run",
		Payload: map[string]any{
			"path":                   "/workspace/org-1/data/ks-1",
			"workspace_kind":         "managed_restore",
			"workspace_uid":          "8f65d43a-09fd-4ae7-b5f1-159352838a23",
			"managed_workspace_path": "/workspace/org-1/data/ks-1",
		},
	}
	if !isManagedWorkspaceRestoreCommand(managed) {
		t.Fatal("managed workspace restore was not recognized")
	}

	ordinary := *managed
	ordinary.Payload = map[string]any{"path": "/restore", "workspace_kind": "user_data"}
	if isManagedWorkspaceRestoreCommand(&ordinary) {
		t.Fatal("ordinary restore must not receive managed executor evidence")
	}
}

func TestManagedRestoreCancelledBeforeTrackerRegistrationDoesNotExecute(t *testing.T) {
	ctx := t.Context()
	db, err := database.Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := database.NewTaskRepo(db)
	cmd := &TaskCommand{
		TaskID: "managed-restore-pre-cancelled",
		Kind:   "restore.run",
		Payload: map[string]any{
			"path":                   "/workspace/org-1/data/ks-1",
			"workspace_kind":         "managed_restore",
			"workspace_uid":          "8f65d43a-09fd-4ae7-b5f1-159352838a23",
			"managed_workspace_path": "/workspace/org-1/data/ks-1",
		},
	}
	if err := repo.RecordCommand(ctx, database.RecordInput{
		TaskID:  cmd.TaskID,
		Kind:    cmd.Kind,
		Payload: cmd.Payload,
	}); err != nil {
		t.Fatal(err)
	}
	if status, changed, err := repo.MarkCancelledIfActive(ctx, cmd.TaskID); err != nil || !changed || status != model.TaskStatusCancelled {
		t.Fatalf("pre-cancel status=%q changed=%v err=%v", status, changed, err)
	}

	handler := NewHandler(nil, controller.NewTracker(), repo)
	handler.runTask(context.Background(), &captureSender{}, cmd)

	persisted, err := repo.Get(ctx, cmd.TaskID)
	if err != nil {
		t.Fatal(err)
	}
	if persisted.Status != model.TaskStatusCancelled {
		t.Fatalf("task status=%q, want cancelled", persisted.Status)
	}
	if persisted.Result["executor_finished"] != true || persisted.Result["completion_source"] != "agent_executor" {
		t.Fatalf("executor stop evidence=%#v", persisted.Result)
	}
}
