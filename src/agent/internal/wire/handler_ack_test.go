package wire

import (
	"context"
	"encoding/json"
	"path/filepath"
	"testing"
	"time"

	"hyperfilelens/agent/internal/controller"
	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/model"
)

type captureSender struct {
	frames []any
}

func (s *captureSender) SendJSON(_ context.Context, frame any) error {
	s.frames = append(s.frames, frame)
	return nil
}

func newFinishedTaskHandler(t *testing.T) (*Handler, *database.TaskRepo) {
	t.Helper()
	ctx := t.Context()
	db, err := database.Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = db.Close() })
	repo := database.NewTaskRepo(db)
	if err := repo.RecordCommand(ctx, database.RecordInput{TaskID: "task-1", Kind: "backup.run"}); err != nil {
		t.Fatal(err)
	}
	if err := repo.Finish(ctx, "task-1", model.TaskStatusSucceeded, map[string]any{"kopia_snapshot_id": "snap-1"}, ""); err != nil {
		t.Fatal(err)
	}
	return NewHandler(nil, controller.NewTracker(), repo), repo
}

func TestFlushUnreportedWaitsForAckInAckMode(t *testing.T) {
	handler, repo := newFinishedTaskHandler(t)
	handler.SetTaskResultAckEnabled(true)
	sender := &captureSender{}
	if err := handler.FlushUnreportedResults(t.Context(), sender); err != nil {
		t.Fatal(err)
	}
	if len(sender.frames) != 1 {
		t.Fatalf("frames = %d, want 1", len(sender.frames))
	}
	pending, err := repo.ListUnreported(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	if len(pending) != 1 {
		t.Fatalf("pending before ack = %d, want 1", len(pending))
	}
	if err := handler.Handle(
		t.Context(),
		[]byte(`{"type":"task.result.ack","task_id":"task-1"}`),
		sender,
	); err != nil {
		t.Fatal(err)
	}
	pending, err = repo.ListUnreported(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	if len(pending) != 0 {
		t.Fatalf("pending after ack = %d, want 0", len(pending))
	}
	if err := handler.FlushUnreportedResults(t.Context(), sender); err != nil {
		t.Fatal(err)
	}
	if len(sender.frames) != 1 {
		t.Fatalf("frames after ack = %d, want no retransmission", len(sender.frames))
	}
}

func TestFlushUnreportedRetransmitsIdenticalResultUntilAck(t *testing.T) {
	handler, _ := newFinishedTaskHandler(t)
	handler.SetTaskResultAckEnabled(true)
	sender := &captureSender{}
	if err := handler.FlushUnreportedResults(t.Context(), sender); err != nil {
		t.Fatal(err)
	}
	if err := handler.FlushUnreportedResults(t.Context(), sender); err != nil {
		t.Fatal(err)
	}
	if len(sender.frames) != 2 {
		t.Fatalf("frames = %d, want 2", len(sender.frames))
	}
	first, err := json.Marshal(sender.frames[0])
	if err != nil {
		t.Fatal(err)
	}
	second, err := json.Marshal(sender.frames[1])
	if err != nil {
		t.Fatal(err)
	}
	if string(first) != string(second) {
		t.Fatalf("retransmitted result changed:\n%s\n%s", first, second)
	}
}

func TestFlushUnreportedKeepsLegacyMarkOnWrite(t *testing.T) {
	handler, repo := newFinishedTaskHandler(t)
	handler.SetTaskResultAckEnabled(false)
	if err := handler.FlushUnreportedResults(t.Context(), &captureSender{}); err != nil {
		t.Fatal(err)
	}
	pending, err := repo.ListUnreported(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	if len(pending) != 0 {
		t.Fatalf("legacy pending = %d, want 0", len(pending))
	}
}

func TestLateCancelPreservesSucceededResultAwaitingAck(t *testing.T) {
	handler, repo := newFinishedTaskHandler(t)
	handler.SetTaskResultAckEnabled(true)
	sender := &captureSender{}
	if err := handler.Handle(
		t.Context(),
		[]byte(`{"type":"task.cancel","task_id":"task-1","node_id":1}`),
		sender,
	); err != nil {
		t.Fatal(err)
	}
	task, err := repo.Get(t.Context(), "task-1")
	if err != nil {
		t.Fatal(err)
	}
	if task.Status != model.TaskStatusSucceeded || task.Result["kopia_snapshot_id"] != "snap-1" {
		t.Fatalf("late cancel overwrote terminal success: %#v", task)
	}
	if task.ResultReported {
		t.Fatal("late cancel must leave success pending for ACK retry")
	}
}

func TestRunningTaskCancelPersistsCancelledState(t *testing.T) {
	ctx := t.Context()
	db, err := database.Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := database.NewTaskRepo(db)
	if err := repo.RecordCommand(ctx, database.RecordInput{TaskID: "running-1", Kind: "backup.run"}); err != nil {
		t.Fatal(err)
	}
	handler := NewHandler(nil, controller.NewTracker(), repo)
	if err := handler.Handle(
		ctx,
		[]byte(`{"type":"task.cancel","task_id":"running-1","node_id":1}`),
		&captureSender{},
	); err != nil {
		t.Fatal(err)
	}
	task, err := repo.Get(ctx, "running-1")
	if err != nil {
		t.Fatal(err)
	}
	if task.Status != model.TaskStatusCancelled || task.Error != "canceled" {
		t.Fatalf("task = %#v, want cancelled", task)
	}
}

func TestTaskCommandPersistsBeforeAcceptedAndDuplicateOnlyAcks(t *testing.T) {
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	db, err := database.Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := database.NewTaskRepo(db)
	scheduler := controller.NewScheduler(1)
	release, ok := scheduler.TryAcquire()
	if !ok {
		t.Fatal("failed to occupy scheduler")
	}
	defer release()
	handler := NewHandler(nil, controller.NewTracker(), repo, scheduler)
	sender := &channelSender{frames: make(chan any, 8)}
	command := []byte(`{"type":"task.command","task_id":"durable-1","kind":"backup.snapshot.create","node_id":1}`)

	if err := handler.Handle(ctx, command, sender); err != nil {
		t.Fatal(err)
	}
	first := <-sender.frames
	accepted, ok := first.(TaskAccepted)
	if !ok || accepted.TaskID != "durable-1" {
		t.Fatalf("first frame = %#v, want task.accepted", first)
	}
	if _, err := repo.Get(ctx, "durable-1"); err != nil {
		t.Fatalf("ACK was emitted without durable task: %v", err)
	}

	// The only execution publishes one scheduler-wait progress frame.
	select {
	case frame := <-sender.frames:
		if _, ok := frame.(TaskProgress); !ok {
			t.Fatalf("execution frame = %T, want TaskProgress", frame)
		}
	case <-time.After(time.Second):
		t.Fatal("first execution did not reach scheduler")
	}
	if err := handler.Handle(ctx, command, sender); err != nil {
		t.Fatal(err)
	}
	duplicateFrame := <-sender.frames
	if _, ok := duplicateFrame.(TaskAccepted); !ok {
		t.Fatalf("duplicate frame = %T, want TaskAccepted", duplicateFrame)
	}
	select {
	case frame := <-sender.frames:
		t.Fatalf("duplicate started a second execution: %T", frame)
	case <-time.After(50 * time.Millisecond):
	}
}

func TestTerminalDuplicateReplaysStoredResult(t *testing.T) {
	handler, _ := newFinishedTaskHandler(t)
	sender := &captureSender{}
	if err := handler.Handle(
		t.Context(),
		[]byte(`{"type":"task.command","task_id":"task-1","kind":"backup.run"}`),
		sender,
	); err != nil {
		t.Fatal(err)
	}
	if len(sender.frames) != 1 {
		t.Fatalf("frames=%d, want stored result replay", len(sender.frames))
	}
	result, ok := sender.frames[0].(TaskResult)
	if !ok || result.TaskID != "task-1" || result.Status != "success" {
		t.Fatalf("replayed frame = %#v", sender.frames[0])
	}
}

func TestPersistenceFailureEmitsNoAcceptance(t *testing.T) {
	ctx := t.Context()
	db, err := database.Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	repo := database.NewTaskRepo(db)
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	handler := NewHandler(nil, controller.NewTracker(), repo)
	sender := &captureSender{}
	if err := handler.Handle(
		ctx,
		[]byte(`{"type":"task.command","task_id":"not-durable","kind":"backup.run"}`),
		sender,
	); err != nil {
		t.Fatal(err)
	}
	if len(sender.frames) != 0 {
		t.Fatalf("frames=%d, want no ACK after persistence failure", len(sender.frames))
	}
}

func TestWebsocketSinkPersistsLatestResumableProgress(t *testing.T) {
	ctx := t.Context()
	db, err := database.Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := database.NewTaskRepo(db)
	if err := repo.RecordCommand(ctx, database.RecordInput{
		TaskID: "backup-progress-1",
		Kind:   "backup.run",
	}); err != nil {
		t.Fatal(err)
	}
	sink := &websocketSink{
		sink:      &captureSender{},
		taskID:    "backup-progress-1",
		tasks:     repo,
		resumable: true,
	}
	if err := sink.OnProgress(ctx, map[string]any{
		"phase": "uploading",
		"files": float64(42),
	}); err != nil {
		t.Fatal(err)
	}

	task, err := repo.Get(ctx, "backup-progress-1")
	if err != nil {
		t.Fatal(err)
	}
	latest, ok := task.Result["last_progress"].(map[string]any)
	if !ok {
		t.Fatalf("last_progress = %#v", task.Result["last_progress"])
	}
	if latest["phase"] != "uploading" || latest["files"] != float64(42) {
		t.Fatalf("persisted progress = %#v", latest)
	}
}

func TestWebsocketSinkDoesNotRegressTerminalTask(t *testing.T) {
	ctx := t.Context()
	db, err := database.Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := database.NewTaskRepo(db)
	if err := repo.RecordCommand(ctx, database.RecordInput{
		TaskID: "backup-progress-terminal",
		Kind:   "backup.run",
	}); err != nil {
		t.Fatal(err)
	}
	if err := repo.Finish(
		ctx,
		"backup-progress-terminal",
		model.TaskStatusSucceeded,
		map[string]any{"snapshot_id": "snapshot-1"},
		"",
	); err != nil {
		t.Fatal(err)
	}
	sink := &websocketSink{
		sink:      &captureSender{},
		taskID:    "backup-progress-terminal",
		tasks:     repo,
		resumable: true,
	}
	if err := sink.OnProgress(ctx, map[string]any{"phase": "late"}); err != nil {
		t.Fatal(err)
	}

	task, err := repo.Get(ctx, "backup-progress-terminal")
	if err != nil {
		t.Fatal(err)
	}
	if task.Status != model.TaskStatusSucceeded {
		t.Fatalf("status = %s, want succeeded", task.Status)
	}
	if task.Result["snapshot_id"] != "snapshot-1" {
		t.Fatalf("terminal result was overwritten: %#v", task.Result)
	}
}
