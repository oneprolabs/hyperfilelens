package wire

import (
	"context"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"hyperfilelens/agent/internal/controller"
	"hyperfilelens/agent/internal/engine"
	"hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/infra/logging"
	"hyperfilelens/agent/internal/model"
)

// Handler routes downlink task frames to the engine and sends uplink progress/result.
type Handler struct {
	provider          config.Provider
	tracker           *controller.Tracker
	tasks             *database.TaskRepo
	snapshotScheduler *controller.Scheduler
	pathSizeScheduler *controller.Scheduler
	nasLeases         *engine.NASLeaseCoordinator
	resultAckEnabled  atomic.Bool
	resultMu          sync.Mutex
	resultInFlight    map[string]resultDelivery
	resultWake        chan struct{}
}

const resultOutboxWindow = 32

var (
	resultOutboxRetryBase = 30 * time.Second
	resultOutboxRetryMax  = 5 * time.Minute
)

type resultDelivery struct {
	attempts int
	deadline time.Time
}

// SetTaskResultAckEnabled selects ACK mode for the current WebSocket session.
func (h *Handler) SetTaskResultAckEnabled(enabled bool) {
	if h != nil {
		h.resultAckEnabled.Store(enabled)
		h.resultMu.Lock()
		clear(h.resultInFlight)
		h.resultMu.Unlock()
	}
}

// TaskResultAckEnabled reports whether task.result requires a control-plane ACK.
func (h *Handler) TaskResultAckEnabled() bool {
	return h != nil && h.resultAckEnabled.Load()
}

// ResultOutboxWake reports ACK-driven capacity becoming available.
func (h *Handler) ResultOutboxWake() <-chan struct{} {
	if h == nil {
		return nil
	}
	return h.resultWake
}

// ResultOutboxPollInterval bounds how long an expired delivery waits for retry.
func ResultOutboxPollInterval() time.Duration {
	return resultOutboxRetryBase
}

func resultRetryDelay(attempt int) time.Duration {
	if attempt < 1 {
		attempt = 1
	}
	delay := resultOutboxRetryBase
	for i := 1; i < attempt && delay < resultOutboxRetryMax; i++ {
		delay *= 2
		if delay >= resultOutboxRetryMax {
			return resultOutboxRetryMax
		}
	}
	return delay
}

func (h *Handler) reserveResultDelivery(taskID string) bool {
	if h == nil || taskID == "" || !h.TaskResultAckEnabled() {
		return true
	}
	now := time.Now()
	h.resultMu.Lock()
	defer h.resultMu.Unlock()
	if delivery, exists := h.resultInFlight[taskID]; exists && delivery.deadline.After(now) {
		return false
	}
	active := 0
	for _, delivery := range h.resultInFlight {
		if delivery.deadline.After(now) {
			active++
		}
	}
	if active >= resultOutboxWindow {
		return false
	}
	delivery := h.resultInFlight[taskID]
	delivery.attempts++
	delivery.deadline = now.Add(resultRetryDelay(delivery.attempts))
	h.resultInFlight[taskID] = delivery
	return true
}

func (h *Handler) releaseResultDelivery(taskID string) {
	if h == nil || taskID == "" {
		return
	}
	h.resultMu.Lock()
	delete(h.resultInFlight, taskID)
	h.resultMu.Unlock()
}

func (h *Handler) acknowledgeResultDelivery(taskID string) {
	h.releaseResultDelivery(taskID)
	select {
	case h.resultWake <- struct{}{}:
	default:
	}
}

func (h *Handler) sendLiveTaskResult(
	ctx context.Context,
	sink Sender,
	taskID string,
	status string,
	result map[string]any,
	errMsg string,
) error {
	if h.TaskResultAckEnabled() && !h.reserveResultDelivery(taskID) {
		return nil
	}
	if err := SendTaskResult(ctx, sink, taskID, status, result, errMsg); err != nil {
		if h.TaskResultAckEnabled() {
			h.releaseResultDelivery(taskID)
		}
		return err
	}
	if h.tasks != nil && !h.TaskResultAckEnabled() {
		return h.tasks.MarkResultReported(ctx, taskID)
	}
	return nil
}

// NewHandler returns a protocol handler bound to config, tracker, and local task storage.
func NewHandler(
	provider config.Provider,
	tracker *controller.Tracker,
	tasks *database.TaskRepo,
	schedulers ...*controller.Scheduler,
) *Handler {
	snapshotScheduler := controller.NewScheduler(2)
	pathSizeScheduler := controller.NewScheduler(1)
	if len(schedulers) > 0 && schedulers[0] != nil {
		snapshotScheduler = schedulers[0]
	}
	if len(schedulers) > 1 && schedulers[1] != nil {
		pathSizeScheduler = schedulers[1]
	}
	return &Handler{
		provider:          provider,
		tracker:           tracker,
		tasks:             tasks,
		snapshotScheduler: snapshotScheduler,
		pathSizeScheduler: pathSizeScheduler,
		nasLeases:         engine.NewNASLeaseCoordinator(),
		resultInFlight:    make(map[string]resultDelivery),
		resultWake:        make(chan struct{}, 1),
	}
}

// Handle parses one inbound WebSocket text frame and dispatches by type.
func (h *Handler) Handle(ctx context.Context, raw []byte, sink Sender) error {
	dl, err := ParseDownlink(raw)
	if err != nil {
		slog.Warn("wire downlink parse failed", "err", err)
		return nil
	}

	switch dl.Type {
	case TypeTaskCommand:
		if dl.TaskCommand == nil || dl.TaskCommand.TaskID == "" {
			slog.Warn("task.command missing task_id")
			return nil
		}
		cmd := dl.TaskCommand
		logging.InfoTask(ctx, "task.command received", cmd.NodeID, cmd.TaskID, cmd.Kind,
			"trace_id", cmd.TraceID,
			"correlation_id", cmd.CorrelationID,
		)
		shouldRun := true
		if h.tasks != nil {
			now := time.Now().UTC()
			persisted, inserted, acceptErr := h.tasks.AcceptCommand(ctx, database.RecordInput{
				TaskID: cmd.TaskID, JobID: cmd.JobID(), Kind: cmd.Kind, Payload: cmd.Payload,
				Source: string(engine.SourceWebSocket), StartedAt: &now,
			})
			if acceptErr != nil {
				slog.Warn("persist task.command acceptance failed", "task_id", cmd.TaskID, "err", acceptErr)
				return nil
			}
			shouldRun = inserted
			if !inserted && persisted.Status != model.TaskStatusRunning && persisted.Status != model.TaskStatusPending {
				_ = h.sendLiveTaskResult(ctx, sink, persisted.ID, database.WireStatus(persisted.Status), persisted.Result, persisted.Error)
				return nil
			}
		}
		if err := SendTaskAccepted(ctx, sink, cmd.TaskID, "running"); err != nil {
			slog.Warn("send task.accepted failed", "task_id", cmd.TaskID, "err", err)
		}
		if shouldRun {
			go h.runTask(context.WithoutCancel(ctx), sink, cmd)
		}
		return nil
	case TypeTaskCancel:
		if dl.TaskCancel == nil || dl.TaskCancel.TaskID == "" {
			return nil
		}
		logging.InfoTask(ctx, "task.cancel received", dl.TaskCancel.NodeID, dl.TaskCancel.TaskID, "cancel")
		if h.tasks != nil {
			status, changed, cancelErr := h.tasks.MarkCancelledIfActive(ctx, dl.TaskCancel.TaskID)
			if cancelErr != nil {
				slog.Warn("persist task cancellation failed", "task_id", dl.TaskCancel.TaskID, "err", cancelErr)
				return nil
			}
			if !changed {
				slog.Info(
					"late task.cancel ignored for terminal task",
					"task_id", dl.TaskCancel.TaskID,
					"status", status,
				)
				return nil
			}
		}
		engine.New(h.provider).Cancel()
		_ = h.tracker.Cancel(ctx, dl.TaskCancel.TaskID)
		return nil
	case TypeTaskResultAck:
		if dl.TaskResultAck == nil || dl.TaskResultAck.TaskID == "" || h.tasks == nil {
			return nil
		}
		if err := h.tasks.MarkResultReported(ctx, dl.TaskResultAck.TaskID); err != nil {
			slog.Warn("persist task.result ack failed", "task_id", dl.TaskResultAck.TaskID, "err", err)
			return nil
		}
		h.acknowledgeResultDelivery(dl.TaskResultAck.TaskID)
		slog.Info("task.result acknowledged", "task_id", dl.TaskResultAck.TaskID)
		return nil
	default:
		if dl.Type != "" {
			slog.Debug("wire downlink ignored", "type", dl.Type)
		}
		return nil
	}
}

// FlushUnreportedResults sends terminal task.result frames pending upstream acknowledgement.
func (h *Handler) FlushUnreportedResults(ctx context.Context, sink Sender) error {
	if h.tasks == nil || sink == nil {
		return nil
	}
	limit := 0
	if h.TaskResultAckEnabled() {
		limit = resultOutboxWindow
	}
	pending, err := h.tasks.ListUnreported(ctx, limit)
	if err != nil {
		return err
	}
	for _, task := range pending {
		if h.TaskResultAckEnabled() && !h.reserveResultDelivery(task.ID) {
			continue
		}
		wireStatus := database.WireStatus(task.Status)
		errMsg := task.Error
		if errMsg == "" && wireStatus == "failed" {
			errMsg = string(task.Status)
		}
		if err := SendTaskResult(ctx, sink, task.ID, wireStatus, task.Result, errMsg); err != nil {
			if h.TaskResultAckEnabled() {
				h.releaseResultDelivery(task.ID)
			}
			slog.Warn("flush task.result failed", "task_id", task.ID, "err", err)
			continue
		}
		if h.TaskResultAckEnabled() {
			slog.Info("sent task.result awaiting ack", "task_id", task.ID, "status", wireStatus)
			continue
		}
		if err := h.tasks.MarkResultReported(ctx, task.ID); err != nil {
			slog.Warn("mark result reported failed", "task_id", task.ID, "err", err)
		} else {
			slog.Info("flushed task.result", "task_id", task.ID, "status", wireStatus)
		}
	}
	return nil
}

// ReattachRunningTasks re-publishes in-flight backup progress after reconnect.
func (h *Handler) ReattachRunningTasks(ctx context.Context, sink Sender) error {
	if h.tasks == nil || sink == nil {
		return nil
	}
	tasks, err := h.tasks.ListIncomplete(ctx)
	if err != nil {
		return err
	}
	for _, task := range tasks {
		if task.Status != model.TaskStatusRunning {
			continue
		}
		if !model.IsResumableTaskKind(task.Kind) {
			continue
		}
		progress := map[string]any{
			"phase":    "running",
			"status":   "running",
			"reattach": true,
		}
		if len(task.Result) > 0 {
			if last, ok := task.Result["last_progress"].(map[string]any); ok {
				for key, value := range last {
					progress[key] = value
				}
			}
		}
		if err := SendTaskProgress(ctx, sink, task.ID, progress); err != nil {
			slog.Warn("reattach task.progress failed", "task_id", task.ID, "err", err)
			continue
		}
		slog.Info("reattached running task progress", "task_id", task.ID, "kind", task.Kind)
	}
	return nil
}

func (h *Handler) runTask(ctx context.Context, sink Sender, cmd *TaskCommand) {
	taskCtx, cancel := context.WithCancel(ctx)
	taskCtx = logging.WithTraceID(taskCtx, cmd.TraceID)
	defer cancel()

	logging.InfoTask(taskCtx, "task execution started", cmd.NodeID, cmd.TaskID, cmd.Kind)

	eng := engine.NewWithNASLeaseCoordinator(h.provider, h.nasLeases)

	now := time.Now().UTC()

	task := model.Task{
		ID:        cmd.TaskID,
		JobID:     cmd.JobID(),
		Kind:      cmd.Kind,
		Status:    model.TaskStatusRunning,
		Payload:   cmd.Payload,
		StartedAt: &now,
		Source:    string(engine.SourceWebSocket),
	}
	h.tracker.Register(task, cancel)
	defer h.tracker.Unregister(cmd.TaskID)

	aliveDone := make(chan struct{})
	go h.aliveLoop(taskCtx, sink, cmd.TaskID, aliveDone)
	defer close(aliveDone)

	if engine.NormalizeKind(cmd.Kind) == "backup.snapshot.create" {
		releaseSlot, acquired := h.snapshotScheduler.TryAcquire()
		if !acquired {
			queuedAt := time.Now()
			_ = SendTaskProgress(taskCtx, sink, cmd.TaskID, map[string]any{
				"phase":               "orchestration",
				"orchestration_phase": "dispatching",
				"kopia_phase":         "waiting_for_snapshot_slot",
				"orchestration_label": "Waiting for backup execution slot",
				"status":              "queued",
			})
			logging.InfoTask(taskCtx, "backup snapshot queued", cmd.NodeID, cmd.TaskID, cmd.Kind)
			var acquireErr error
			releaseSlot, acquireErr = h.snapshotScheduler.Acquire(taskCtx)
			if acquireErr != nil {
				persistCtx := context.WithoutCancel(ctx)
				if h.tasks != nil {
					_ = h.tasks.Finish(
						persistCtx, cmd.TaskID, model.TaskStatusCancelled, nil, "canceled",
					)
				}
				_ = h.sendLiveTaskResult(persistCtx, sink, cmd.TaskID, "failed", nil, "canceled")
				return
			}
			logging.InfoTask(
				taskCtx,
				"backup snapshot execution slot acquired",
				cmd.NodeID,
				cmd.TaskID,
				cmd.Kind,
				"wait_ms",
				time.Since(queuedAt).Milliseconds(),
			)
		}
		defer releaseSlot()
	}
	if engine.NormalizeKind(cmd.Kind) == "path.size" {
		releaseSlot, acquired := h.pathSizeScheduler.TryAcquire()
		if !acquired {
			persistCtx := context.WithoutCancel(ctx)
			status := "failed"
			busyResult := map[string]any{"error_code": "PATH_SIZE_BUSY"}
			errMsg := "path size capacity is busy"
			if h.tasks != nil {
				stored, err := h.tasks.FinishIfActive(
					persistCtx,
					cmd.TaskID,
					model.TaskStatusFailed,
					busyResult,
					errMsg,
				)
				if err != nil {
					slog.Warn("persist path size busy result failed", "task_id", cmd.TaskID, "err", err)
					return
				}
				if !stored {
					persisted, getErr := h.tasks.Get(persistCtx, cmd.TaskID)
					if getErr != nil {
						slog.Warn("load competing path size result failed", "task_id", cmd.TaskID, "err", getErr)
						return
					}
					status = database.WireStatus(persisted.Status)
					busyResult = persisted.Result
					errMsg = persisted.Error
				}
			}
			_ = h.sendLiveTaskResult(persistCtx, sink, cmd.TaskID, status, busyResult, errMsg)
			return
		}
		defer releaseSlot()
	}

	_ = SendTaskProgress(taskCtx, sink, cmd.TaskID, map[string]any{
		"phase":  "started",
		"kind":   cmd.Kind,
		"status": "running",
	})

	progressDone := make(chan struct{})
	go h.progressLoop(taskCtx, sink, cmd.TaskID, progressDone)
	defer close(progressDone)

	wsSink := &websocketSink{
		sink:      sink,
		taskID:    cmd.TaskID,
		tasks:     h.tasks,
		resumable: model.IsResumableTaskKind(cmd.Kind),
	}
	out := eng.Run(taskCtx, engine.Command{
		ID:      cmd.TaskID,
		JobID:   cmd.JobID(),
		Kind:    cmd.Kind,
		Payload: cmd.Payload,
		Source:  engine.SourceWebSocket,
	}, wsSink)

	status := out.Status
	result, resultStats := boundTaskResult(out.Result)
	if resultStats.Truncated {
		resultBoundLog(cmd.TaskID, resultStats)
	}
	errMsg := out.Error
	if taskCtx.Err() != nil && status != "success" {
		status = "failed"
		errMsg = "canceled"
	}

	if status == "success" {
		logging.InfoTask(taskCtx, "task execution finished", cmd.NodeID, cmd.TaskID, cmd.Kind, "status", status)
	} else {
		logging.WarnTask(taskCtx, "task execution finished", cmd.NodeID, cmd.TaskID, cmd.Kind, "status", status, "err", errMsg)
	}

	if status == "running" {
		if h.tasks != nil {
			if err := h.tasks.UpdateRunning(taskCtx, cmd.TaskID, result); err != nil {
				slog.Warn("persist running task failed", "task_id", cmd.TaskID, "err", err)
			}
		}
		if err := h.sendLiveTaskResult(taskCtx, sink, cmd.TaskID, status, result, errMsg); err != nil {
			slog.Warn("send task.result failed", "task_id", cmd.TaskID, "err", err)
		}
		return
	}

	localStatus := model.TaskStatusFailed
	if status == "success" {
		localStatus = model.TaskStatusSucceeded
	}
	if errMsg == "canceled" {
		localStatus = model.TaskStatusCancelled
	}

	persistCtx := context.WithoutCancel(ctx)
	if h.tasks != nil {
		stored, err := h.tasks.FinishIfActive(persistCtx, cmd.TaskID, localStatus, result, errMsg)
		if err != nil {
			slog.Warn("persist task result failed", "task_id", cmd.TaskID, "err", err)
			return
		}
		if !stored {
			persisted, getErr := h.tasks.Get(persistCtx, cmd.TaskID)
			if getErr != nil {
				slog.Warn("load competing terminal task result failed", "task_id", cmd.TaskID, "err", getErr)
				return
			}
			status = database.WireStatus(persisted.Status)
			result = persisted.Result
			errMsg = persisted.Error
			slog.Info(
				"task result send converged to persisted terminal state",
				"task_id", cmd.TaskID,
				"status", persisted.Status,
			)
		}
	}

	if err := h.sendLiveTaskResult(persistCtx, sink, cmd.TaskID, status, result, errMsg); err != nil {
		slog.Warn("send task.result failed", "task_id", cmd.TaskID, "err", err)
	}
}

func (h *Handler) progressLoop(ctx context.Context, sink Sender, taskID string, done <-chan struct{}) {
	t := time.NewTicker(TaskProgressInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-done:
			return
		case <-t.C:
			_ = SendTaskProgress(ctx, sink, taskID, map[string]any{
				"phase":  "running",
				"status": "running",
			})
		}
	}
}

func (h *Handler) aliveLoop(ctx context.Context, sink Sender, taskID string, done <-chan struct{}) {
	t := time.NewTicker(TaskAliveInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-done:
			return
		case <-t.C:
			_ = SendTaskAlive(ctx, sink, taskID)
		}
	}
}

type websocketSink struct {
	sink        Sender
	taskID      string
	tasks       *database.TaskRepo
	resumable   bool
	persistMu   sync.Mutex
	lastPersist time.Time
}

func (w *websocketSink) OnProgress(ctx context.Context, progress map[string]any) error {
	w.persistLatestProgress(ctx, progress)
	return SendTaskProgress(ctx, w.sink, w.taskID, progress)
}

func (w *websocketSink) persistLatestProgress(ctx context.Context, progress map[string]any) {
	if w == nil || w.tasks == nil || !w.resumable || len(progress) == 0 {
		return
	}
	w.persistMu.Lock()
	defer w.persistMu.Unlock()
	if !w.lastPersist.IsZero() && time.Since(w.lastPersist) < TaskProgressInterval {
		return
	}
	snapshot := make(map[string]any, len(progress))
	for key, value := range progress {
		snapshot[key] = value
	}
	if err := w.tasks.UpdateProgress(
		context.WithoutCancel(ctx),
		w.taskID,
		snapshot,
	); err != nil {
		slog.Warn("persist latest task progress failed", "task_id", w.taskID, "err", err)
		return
	}
	w.lastPersist = time.Now()
}
