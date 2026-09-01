package database

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/install"
)

const repairError = "agent restarted before task completed"

// TaskRepo persists WebSocket task.command records locally.
type TaskRepo struct {
	db *DB
}

// AcceptCommand durably records a command once and returns whether its engine
// execution should be started. Existing task IDs are never reset or rerun.
func (r *TaskRepo) AcceptCommand(ctx context.Context, cmd RecordInput) (model.Task, bool, error) {
	if cmd.TaskID == "" {
		return model.Task{}, false, fmt.Errorf("empty task id")
	}
	payload, err := json.Marshal(cmd.Payload)
	if err != nil {
		return model.Task{}, false, err
	}
	now := time.Now().UTC()
	started := now
	if cmd.StartedAt != nil {
		started = cmd.StartedAt.UTC()
	}
	source := strings.TrimSpace(cmd.Source)
	if source == "" {
		source = "websocket"
	}

	var task model.Task
	inserted := false
	err = r.db.WithTx(ctx, func(tx *sql.Tx) error {
		result, execErr := tx.ExecContext(ctx, `
INSERT OR IGNORE INTO tasks (
  id, job_id, kind, payload, status, result, error,
  started_at, finished_at, created_at, updated_at, result_reported, source
) VALUES (?, ?, ?, ?, ?, '{}', '', ?, NULL, ?, ?, 0, ?)
`,
			cmd.TaskID,
			cmd.JobID,
			cmd.Kind,
			string(payload),
			string(model.TaskStatusRunning),
			formatTime(started),
			formatTime(now),
			formatTime(now),
			source,
		)
		if execErr != nil {
			return execErr
		}
		rows, rowsErr := result.RowsAffected()
		if rowsErr != nil {
			return rowsErr
		}
		inserted = rows == 1
		row := tx.QueryRowContext(ctx, `
SELECT id, job_id, kind, payload, status, result, error,
       started_at, finished_at, result_reported, source
FROM tasks WHERE id=?
`, cmd.TaskID)
		var scanErr error
		task, scanErr = scanTaskRow(row)
		return scanErr
	})
	if err != nil {
		return model.Task{}, false, err
	}
	if task.Kind != cmd.Kind || task.JobID != cmd.JobID {
		return task, false, fmt.Errorf("task id %s reused with different command identity", cmd.TaskID)
	}
	return task, inserted, nil
}

// NewTaskRepo returns a task repository backed by db.
func NewTaskRepo(db *DB) *TaskRepo {
	return &TaskRepo{db: db}
}

// RecordCommand inserts or updates a task as running when a task.command arrives.
func (r *TaskRepo) RecordCommand(ctx context.Context, cmd RecordInput) error {
	if cmd.TaskID == "" {
		return fmt.Errorf("empty task id")
	}
	payload, err := json.Marshal(cmd.Payload)
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	started := now
	if cmd.StartedAt != nil {
		started = cmd.StartedAt.UTC()
	}
	source := strings.TrimSpace(cmd.Source)
	if source == "" {
		source = "websocket"
	}

	return r.db.WithTx(ctx, func(tx *sql.Tx) error {
		_, err := tx.ExecContext(ctx, `
INSERT INTO tasks (
  id, job_id, kind, payload, status, result, error,
  started_at, finished_at, created_at, updated_at, result_reported, source
) VALUES (?, ?, ?, ?, ?, '{}', '', ?, NULL, ?, ?, 0, ?)
ON CONFLICT(id) DO UPDATE SET
  job_id=excluded.job_id,
  kind=excluded.kind,
  payload=excluded.payload,
  status=excluded.status,
  error='',
  started_at=excluded.started_at,
  finished_at=NULL,
  updated_at=excluded.updated_at,
  result_reported=0,
  source=excluded.source
`,
			cmd.TaskID,
			cmd.JobID,
			cmd.Kind,
			string(payload),
			string(model.TaskStatusRunning),
			formatTime(started),
			formatTime(now),
			formatTime(now),
			source,
		)
		return err
	})
}

// RecordInput is the persisted view of an inbound task.command.
type RecordInput struct {
	TaskID    string
	JobID     string
	Kind      string
	Payload   map[string]any
	Source    string
	StartedAt *time.Time
}

// UpdateRunning persists in-flight lifecycle work (detached upgrade/uninstall).
func (r *TaskRepo) UpdateRunning(ctx context.Context, taskID string, result map[string]any) error {
	if taskID == "" {
		return fmt.Errorf("empty task id")
	}
	resultJSON, err := json.Marshal(result)
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	_, err = r.db.conn.ExecContext(ctx, `
UPDATE tasks SET
  status=?,
  result=?,
  updated_at=?,
  result_reported=0
WHERE id=? AND status IN (?, ?)
`,
		string(model.TaskStatusRunning),
		string(resultJSON),
		formatTime(now),
		taskID,
		string(model.TaskStatusPending),
		string(model.TaskStatusRunning),
	)
	return err
}

// UpdateProgress merges the latest progress into a task that is still running.
func (r *TaskRepo) UpdateProgress(ctx context.Context, taskID string, progress map[string]any) error {
	if taskID == "" {
		return fmt.Errorf("empty task id")
	}
	return r.db.WithTx(ctx, func(tx *sql.Tx) error {
		var status string
		var resultRaw string
		if err := tx.QueryRowContext(
			ctx,
			`SELECT status, result FROM tasks WHERE id=?`,
			taskID,
		).Scan(&status, &resultRaw); err != nil {
			return err
		}
		if status != string(model.TaskStatusRunning) {
			return nil
		}
		result := map[string]any{}
		if resultRaw != "" {
			if err := json.Unmarshal([]byte(resultRaw), &result); err != nil {
				return fmt.Errorf("decode running task result: %w", err)
			}
		}
		result["last_progress"] = progress
		resultJSON, err := json.Marshal(result)
		if err != nil {
			return err
		}
		_, err = tx.ExecContext(
			ctx,
			`UPDATE tasks SET result=?, updated_at=? WHERE id=? AND status=?`,
			string(resultJSON),
			formatTime(time.Now().UTC()),
			taskID,
			string(model.TaskStatusRunning),
		)
		return err
	})
}

// Finish updates terminal status, result, and error after execution.
func (r *TaskRepo) Finish(ctx context.Context, taskID string, status model.TaskStatus, result map[string]any, errMsg string) error {
	_, err := r.FinishIfActive(ctx, taskID, status, result, errMsg)
	return err
}

// FinishIfActive atomically stores a terminal result only while the task is
// pending/running. The returned flag reports whether this result won a race
// with cancellation or another terminal transition.
func (r *TaskRepo) FinishIfActive(
	ctx context.Context,
	taskID string,
	status model.TaskStatus,
	result map[string]any,
	errMsg string,
) (bool, error) {
	if taskID == "" {
		return false, fmt.Errorf("empty task id")
	}
	resultJSON, err := json.Marshal(result)
	if err != nil {
		return false, err
	}
	now := time.Now().UTC()
	execResult, err := r.db.conn.ExecContext(ctx, `
UPDATE tasks SET
  status=?,
  result=?,
  error=?,
  finished_at=?,
  updated_at=?,
  result_reported=0
WHERE id=? AND status IN (?, ?)
`,
		string(status),
		string(resultJSON),
		errMsg,
		formatTime(now),
		formatTime(now),
		taskID,
		string(model.TaskStatusPending),
		string(model.TaskStatusRunning),
	)
	if err != nil {
		return false, err
	}
	rows, err := execResult.RowsAffected()
	return rows == 1, err
}

// SealCancelledExecutorResult attaches evidence produced after a cancelled
// task's execution goroutine has actually returned. Cancellation is persisted
// before the subprocess exits so reconnects never resurrect work as running;
// this narrow terminal update lets managed workspace cleanup distinguish that
// control-plane state from an executor that has finished shutting down.
func (r *TaskRepo) SealCancelledExecutorResult(
	ctx context.Context,
	taskID string,
	result map[string]any,
	errMsg string,
) (bool, error) {
	if taskID == "" {
		return false, fmt.Errorf("empty task id")
	}
	resultJSON, err := json.Marshal(result)
	if err != nil {
		return false, err
	}
	now := time.Now().UTC()
	execResult, err := r.db.conn.ExecContext(ctx, `
UPDATE tasks SET
  result=?,
  error=?,
  finished_at=COALESCE(finished_at, ?),
  updated_at=?,
  result_reported=0
WHERE id=? AND status=?
`,
		string(resultJSON),
		errMsg,
		formatTime(now),
		formatTime(now),
		taskID,
		string(model.TaskStatusCancelled),
	)
	if err != nil {
		return false, err
	}
	rows, err := execResult.RowsAffected()
	return rows == 1, err
}

// MarkCancelledIfActive cancels pending/running work without overwriting a
// terminal result that may still be awaiting control-plane acknowledgement.
func (r *TaskRepo) MarkCancelledIfActive(
	ctx context.Context,
	taskID string,
) (model.TaskStatus, bool, error) {
	if taskID == "" {
		return "", false, fmt.Errorf("empty task id")
	}
	var status model.TaskStatus
	changed := false
	err := r.db.WithTx(ctx, func(tx *sql.Tx) error {
		var rawStatus string
		if err := tx.QueryRowContext(ctx, `SELECT status FROM tasks WHERE id=?`, taskID).Scan(&rawStatus); err != nil {
			return wrapTaskNotFound(taskID, err)
		}
		status = model.TaskStatus(rawStatus)
		if status != model.TaskStatusPending && status != model.TaskStatusRunning {
			return nil
		}
		now := time.Now().UTC()
		result, err := tx.ExecContext(ctx, `
UPDATE tasks SET
  status=?, result='{}', error='canceled', finished_at=?, updated_at=?, result_reported=0
WHERE id=? AND status IN (?, ?)
`,
			string(model.TaskStatusCancelled),
			formatTime(now),
			formatTime(now),
			taskID,
			string(model.TaskStatusPending),
			string(model.TaskStatusRunning),
		)
		if err != nil {
			return err
		}
		rows, err := result.RowsAffected()
		if err != nil {
			return err
		}
		changed = rows == 1
		if changed {
			status = model.TaskStatusCancelled
		}
		return nil
	})
	return status, changed, err
}

// MarkResultReported marks task.result as delivered to the control plane.
func (r *TaskRepo) MarkResultReported(ctx context.Context, taskID string) error {
	now := time.Now().UTC()
	_, err := r.db.conn.ExecContext(ctx, `
UPDATE tasks SET result_reported=1, updated_at=? WHERE id=?
`, formatTime(now), taskID)
	return err
}

// RepairOptions supplies agent paths for lifecycle task repair heuristics.
type RepairOptions struct {
	DataDir       string
	LogDir        string
	ActiveTaskIDs map[string]struct{}
}

// RepairInterrupted marks orphaned running tasks failed after agent restart.
func (r *TaskRepo) RepairInterrupted(ctx context.Context, opts RepairOptions) ([]model.Task, error) {
	now := time.Now().UTC()
	rows, err := r.db.conn.QueryContext(ctx, `
SELECT id, job_id, kind, payload, status, error, started_at, finished_at
FROM tasks WHERE status=?
`, string(model.TaskStatusRunning))
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var repaired []model.Task
	for rows.Next() {
		task, err := scanTask(rows)
		if err != nil {
			return nil, err
		}
		repaired = append(repaired, task)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if len(repaired) == 0 {
		return nil, nil
	}

	var updated []model.Task
	for i := range repaired {
		if opts.ActiveTaskIDs != nil {
			if _, active := opts.ActiveTaskIDs[repaired[i].ID]; active {
				continue
			}
		}
		// A WebSocket reconnect can reattach in-process work, but after an actual
		// Agent process restart the Kopia child cannot be proven alive. Report the
		// interrupted execution attempt instead of advertising zombie backup or
		// restore work. The control plane owns any safe product-level retry.
		if install.InterruptedLifecycleStillRunning(repaired[i].Kind, opts.DataDir) {
			continue
		}
		status := model.TaskStatusFailed
		errMsg := repairError
		result := map[string]any{}
		if ok, repairedResult := install.InterruptedLifecycleSucceeded(
			repaired[i].Kind,
			repaired[i].StartedAt,
			opts.DataDir,
			opts.LogDir,
		); ok {
			status = model.TaskStatusSucceeded
			errMsg = ""
			result = repairedResult
		} else if install.IsLifecycleRepairKind(repaired[i].Kind) {
			if install.PendingUpgradeFailed(opts.DataDir) &&
				strings.Contains(strings.ToLower(repaired[i].Kind), "upgrade") {
				errMsg = "detached upgrade failed"
				result = map[string]any{"mode": "local_detached", "phase": "upgrade_failed"}
			} else {
				// Detached upgrade/uninstall may finish after reconnect; deferred repair retries.
				continue
			}
		}
		resultJSON, marshalErr := json.Marshal(result)
		if marshalErr != nil {
			return nil, marshalErr
		}
		_, err = r.db.conn.ExecContext(ctx, `
UPDATE tasks SET
  status=?,
  error=?,
  result=?,
  finished_at=?,
  updated_at=?,
  result_reported=0
WHERE id=?
`,
			string(status),
			errMsg,
			string(resultJSON),
			formatTime(now),
			formatTime(now),
			repaired[i].ID,
		)
		if err != nil {
			return nil, err
		}
		repaired[i].Status = status
		repaired[i].Error = errMsg
		repaired[i].Result = result
		finished := now
		repaired[i].FinishedAt = &finished
		updated = append(updated, repaired[i])
	}
	return updated, nil
}

// ListFilter selects tasks for CLI listing.
type ListFilter struct {
	Status         string
	UnreportedOnly bool
	Limit          int
}

// UpdateInput patches a persisted task row (CLI / manual repair).
type UpdateInput struct {
	Status         *model.TaskStatus
	Kind           *string
	JobID          *string
	Error          *string
	Result         map[string]any
	ResultReported *bool
}

// Get returns one task by id.
func (r *TaskRepo) Get(ctx context.Context, taskID string) (model.Task, error) {
	if taskID == "" {
		return model.Task{}, fmt.Errorf("empty task id")
	}
	row := r.db.conn.QueryRowContext(ctx, `
SELECT id, job_id, kind, payload, status, result, error, started_at, finished_at, result_reported, source
FROM tasks WHERE id=?
`, taskID)
	task, err := scanTaskRow(row)
	return task, wrapTaskNotFound(taskID, err)
}

// ErrTaskNotFound means the task id is absent from agent.db.
var ErrTaskNotFound = errors.New("task not found")

func wrapTaskNotFound(taskID string, err error) error {
	if errors.Is(err, sql.ErrNoRows) {
		return fmt.Errorf("%w: %s", ErrTaskNotFound, taskID)
	}
	return err
}

// List returns tasks matching filter.
func (r *TaskRepo) List(ctx context.Context, filter ListFilter) ([]model.Task, error) {
	query := `
SELECT id, job_id, kind, payload, status, result, error, started_at, finished_at, result_reported, source
FROM tasks WHERE 1=1`
	var args []any
	if filter.Status != "" {
		query += ` AND status=?`
		args = append(args, filter.Status)
	}
	if filter.UnreportedOnly {
		query += ` AND result_reported=0 AND status IN (?, ?, ?)`
		args = append(args, string(model.TaskStatusFailed), string(model.TaskStatusCancelled), string(model.TaskStatusSucceeded))
	}
	query += ` ORDER BY updated_at DESC`
	if filter.Limit > 0 {
		query += fmt.Sprintf(` LIMIT %d`, filter.Limit)
	}

	rows, err := r.db.conn.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []model.Task
	for rows.Next() {
		task, err := scanTaskRows(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, task)
	}
	return out, rows.Err()
}

// Update applies a manual patch to a task row.
func (r *TaskRepo) Update(ctx context.Context, taskID string, patch UpdateInput) error {
	if taskID == "" {
		return fmt.Errorf("empty task id")
	}
	task, err := r.Get(ctx, taskID)
	if err != nil {
		return err
	}

	if patch.Status != nil {
		task.Status = *patch.Status
	}
	if patch.Kind != nil {
		task.Kind = *patch.Kind
	}
	if patch.JobID != nil {
		task.JobID = *patch.JobID
	}
	if patch.Error != nil {
		task.Error = *patch.Error
	}
	if patch.Result != nil {
		task.Result = patch.Result
	}

	payloadJSON, _ := json.Marshal(task.Payload)
	resultJSON, err := json.Marshal(task.Result)
	if err != nil {
		return err
	}

	reported := 0
	if patch.ResultReported != nil && *patch.ResultReported {
		reported = 1
	} else if patch.ResultReported == nil {
		// preserve existing
		var existing int
		_ = r.db.conn.QueryRowContext(ctx, `SELECT result_reported FROM tasks WHERE id=?`, taskID).Scan(&existing)
		reported = existing
	}

	now := time.Now().UTC()
	finishedAt := task.FinishedAt
	if patch.Status != nil && isTerminal(*patch.Status) && finishedAt == nil {
		finished := now
		finishedAt = &finished
	}
	var finishedRaw any
	if finishedAt != nil {
		finishedRaw = formatTime(*finishedAt)
	}

	_, err = r.db.conn.ExecContext(ctx, `
UPDATE tasks SET
  job_id=?,
  kind=?,
  payload=?,
  status=?,
  result=?,
  error=?,
  finished_at=?,
  updated_at=?,
  result_reported=?
WHERE id=?
`,
		task.JobID,
		task.Kind,
		string(payloadJSON),
		string(task.Status),
		string(resultJSON),
		task.Error,
		finishedRaw,
		formatTime(now),
		reported,
		taskID,
	)
	return err
}

func isTerminal(s model.TaskStatus) bool {
	switch s {
	case model.TaskStatusSucceeded, model.TaskStatusFailed, model.TaskStatusCancelled:
		return true
	default:
		return false
	}
}

// ListUnreported returns terminal results that still require upstream acknowledgement.
// A positive limit mixes recent and oldest rows so live failures are not trapped
// behind a historical backlog while the oldest backlog continues to drain.
func (r *TaskRepo) ListUnreported(ctx context.Context, limit int) ([]model.Task, error) {
	if limit <= 0 {
		return r.listUnreportedOrdered(ctx, "ASC", 0)
	}
	recentLimit := max(1, limit/4)
	oldestLimit := max(0, limit-recentLimit)
	recent, err := r.listUnreportedOrdered(ctx, "DESC", recentLimit)
	if err != nil {
		return nil, err
	}
	oldest, err := r.listUnreportedOrdered(ctx, "ASC", oldestLimit)
	if err != nil {
		return nil, err
	}
	seen := make(map[string]struct{}, limit)
	out := make([]model.Task, 0, limit)
	for _, task := range append(recent, oldest...) {
		if _, exists := seen[task.ID]; exists {
			continue
		}
		seen[task.ID] = struct{}{}
		out = append(out, task)
		if len(out) == limit {
			break
		}
	}
	return out, nil
}

func (r *TaskRepo) listUnreportedOrdered(ctx context.Context, order string, limit int) ([]model.Task, error) {
	query := `
SELECT id, job_id, kind, payload, status, result, error, started_at, finished_at
FROM tasks
WHERE result_reported=0 AND status IN (?, ?, ?)
ORDER BY updated_at ` + order
	if limit > 0 {
		query += fmt.Sprintf(" LIMIT %d", limit)
	}
	rows, err := r.db.conn.QueryContext(
		ctx,
		query,
		string(model.TaskStatusFailed),
		string(model.TaskStatusCancelled),
		string(model.TaskStatusSucceeded),
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []model.Task
	for rows.Next() {
		task, err := scanTaskWithResult(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, task)
	}
	return out, rows.Err()
}

// ListIncomplete returns non-terminal tasks for inspection.
func (r *TaskRepo) ListIncomplete(ctx context.Context) ([]model.Task, error) {
	rows, err := r.db.conn.QueryContext(ctx, `
SELECT id, job_id, kind, payload, status, result, error, started_at, finished_at
FROM tasks
WHERE status IN (?, ?)
ORDER BY updated_at DESC
`, string(model.TaskStatusPending), string(model.TaskStatusRunning))
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []model.Task
	for rows.Next() {
		task, err := scanTaskWithResult(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, task)
	}
	return out, rows.Err()
}

type taskRowScanner interface {
	Scan(dest ...any) error
}

func scanTaskRow(row taskRowScanner) (model.Task, error) {
	var (
		task       model.Task
		payloadRaw string
		resultRaw  string
		started    sql.NullString
		finished   sql.NullString
		reported   int
	)
	if err := row.Scan(
		&task.ID, &task.JobID, &task.Kind, &payloadRaw, &task.Status, &resultRaw, &task.Error, &started, &finished, &reported, &task.Source,
	); err != nil {
		return model.Task{}, err
	}
	_ = json.Unmarshal([]byte(payloadRaw), &task.Payload)
	_ = json.Unmarshal([]byte(resultRaw), &task.Result)
	task.StartedAt = parseTime(started)
	task.FinishedAt = parseTime(finished)
	task.ResultReported = reported != 0
	return task, nil
}

func scanTaskRows(rows *sql.Rows) (model.Task, error) {
	return scanTaskRow(rows)
}

func scanTask(rows *sql.Rows) (model.Task, error) {
	var (
		task       model.Task
		payloadRaw string
		started    sql.NullString
		finished   sql.NullString
	)
	if err := rows.Scan(
		&task.ID, &task.JobID, &task.Kind, &payloadRaw, &task.Status, &task.Error, &started, &finished,
	); err != nil {
		return model.Task{}, err
	}
	_ = json.Unmarshal([]byte(payloadRaw), &task.Payload)
	task.StartedAt = parseTime(started)
	task.FinishedAt = parseTime(finished)
	return task, nil
}

func scanTaskWithResult(rows *sql.Rows) (model.Task, error) {
	var (
		task       model.Task
		payloadRaw string
		resultRaw  string
		started    sql.NullString
		finished   sql.NullString
	)
	if err := rows.Scan(
		&task.ID, &task.JobID, &task.Kind, &payloadRaw, &task.Status, &resultRaw, &task.Error, &started, &finished,
	); err != nil {
		return model.Task{}, err
	}
	_ = json.Unmarshal([]byte(payloadRaw), &task.Payload)
	_ = json.Unmarshal([]byte(resultRaw), &task.Result)
	task.StartedAt = parseTime(started)
	task.FinishedAt = parseTime(finished)
	return task, nil
}

// WireStatus maps local task status to task.result status string.
func WireStatus(status model.TaskStatus) string {
	switch status {
	case model.TaskStatusSucceeded:
		return "success"
	case model.TaskStatusRunning:
		return "running"
	case model.TaskStatusCancelled:
		return "failed"
	default:
		return "failed"
	}
}
