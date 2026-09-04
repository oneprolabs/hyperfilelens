package controller

import (
	"context"
	"log/slog"
	"time"

	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/install"
)

// TaskFixer repairs stale "running" task rows after an Agent restart.
type TaskFixer struct {
	tasks   *database.TaskRepo
	tracker *Tracker
	dataDir string
	logDir  string
}

// NewTaskFixer returns a post-restart task state repairer.
func NewTaskFixer(tasks *database.TaskRepo, tracker *Tracker, dataDir, logDir string) *TaskFixer {
	return &TaskFixer{tasks: tasks, tracker: tracker, dataDir: dataDir, logDir: logDir}
}

// RepairRunning marks orphaned running tasks failed after restart.
func (f *TaskFixer) RepairRunning(ctx context.Context) ([]model.Task, error) {
	if f.tasks == nil {
		return nil, nil
	}
	activeTaskIDs := make(map[string]struct{})
	if f.tracker != nil {
		for _, task := range f.tracker.Active() {
			activeTaskIDs[task.ID] = struct{}{}
		}
	}
	repaired, err := f.tasks.RepairInterrupted(ctx, database.RepairOptions{
		DataDir:       f.dataDir,
		LogDir:        f.logDir,
		ActiveTaskIDs: activeTaskIDs,
	})
	if err != nil {
		return nil, err
	}
	for _, task := range repaired {
		slog.Warn("repaired interrupted task",
			"task_id", task.ID,
			"kind", task.Kind,
			"error", task.Error,
		)
	}
	return repaired, nil
}

// PendingLifecycleStartedAt returns the oldest detached lifecycle task start
// time. It is intentionally limited to lifecycle tasks so the periodic repair
// loop never keeps ordinary work alive.
func (f *TaskFixer) PendingLifecycleStartedAt(ctx context.Context) (*time.Time, error) {
	if f.tasks == nil {
		return nil, nil
	}
	tasks, err := f.tasks.List(ctx, database.ListFilter{Status: string(model.TaskStatusRunning)})
	if err != nil {
		return nil, err
	}
	var oldest *time.Time
	for _, task := range tasks {
		if !install.IsLifecycleRepairKind(task.Kind) {
			continue
		}
		startedAt := task.StartedAt
		if raw, ok := task.Result["detached_started_at"].(string); ok {
			if parsed, parseErr := time.Parse(time.RFC3339Nano, raw); parseErr == nil {
				started := parsed
				startedAt = &started
			}
		}
		if startedAt != nil && (oldest == nil || startedAt.Before(*oldest)) {
			started := *startedAt
			oldest = &started
		}
	}
	return oldest, nil
}
