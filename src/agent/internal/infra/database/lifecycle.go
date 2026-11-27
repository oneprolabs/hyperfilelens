package database

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/install"
	"hyperfilelens/agent/internal/platform/lifecycle"
)

// UpgradeCandidate binds an incoming installer's failure bridge to the unique
// detached execution already accepted by the authenticated Agent. No log parsing,
// guessed UUID, schema changes, or extra WebSocket connection is involved.
func (r *TaskRepo) UpgradeCandidate(ctx context.Context, version, commit string) (*model.Task, error) {
	tasks, err := r.List(ctx, ListFilter{Status: string(model.TaskStatusRunning)})
	if err != nil {
		return nil, err
	}
	var match *model.Task
	for _, t := range tasks {
		if t.Kind != "agent.upgrade" || t.Source != "websocket" || t.StartedAt == nil {
			continue
		}
		if t.Payload["target_version"] != version || t.Result["mode"] != "local_detached" || t.Result["target_version"] != version {
			continue
		}
		if expected, ok := t.Payload["target_commit"].(string); ok && strings.TrimSpace(expected) != "" && !strings.EqualFold(strings.TrimSpace(expected), strings.TrimSpace(commit)) {
			continue
		}
		raw, _ := t.Result["detached_started_at"].(string)
		detached, err := time.Parse(time.RFC3339Nano, raw)
		if err != nil || detached.Before(*t.StartedAt) || time.Since(detached) < 0 || time.Since(detached) > 10*time.Minute {
			continue
		}
		if match != nil {
			return nil, fmt.Errorf("ambiguous detached upgrade task; failure bridge refused")
		}
		copy := t
		match = &copy
	}
	return match, nil
}

// ApplyUpgradeFailure uses an identity-checked conditional update. It is also
// safe against an old Agent outbox polling the same WAL-backed database.
func (r *TaskRepo) ApplyUpgradeFailure(ctx context.Context, a lifecycle.Attempt) (bool, error) {
	if a.FinishedAt == "" || a.ErrorCode == "" || a.ExitCode == 0 {
		return false, fmt.Errorf("upgrade terminal failure evidence is incomplete")
	}
	t, err := r.Get(ctx, a.TaskID)
	if err != nil {
		return false, err
	}
	started, err := time.Parse(time.RFC3339Nano, a.TaskStartedAt)
	if err != nil || t.Kind != "agent.upgrade" || t.StartedAt == nil || !started.Equal(*t.StartedAt) || t.Payload["target_version"] != a.TargetVersion {
		return false, fmt.Errorf("upgrade attempt does not match accepted task")
	}
	if commit, ok := t.Payload["target_commit"].(string); ok && strings.TrimSpace(commit) != "" && !strings.EqualFold(strings.TrimSpace(commit), strings.TrimSpace(a.TargetCommit)) {
		return false, fmt.Errorf("upgrade attempt build mismatch")
	}
	result := map[string]any{}
	for k, v := range t.Result {
		result[k] = v
	}
	for k, v := range a.Result() {
		result[k] = v
	}
	errMsg := a.ErrorCode
	storeDir := filepath.Dir(r.db.Path())
	if a.ErrorCode == "AGENT_UPGRADE_INSTALL_FAILED" && filepath.Base(storeDir) == "data" {
		if failure := install.ReadUpgradeSpaceFailure(filepath.Dir(storeDir), t.StartedAt); failure != nil {
			for k, v := range failure.Result() {
				result[k] = v
			}
			errMsg = failure.Error()
		}
	}
	raw, err := json.Marshal(result)
	if err != nil {
		return false, err
	}
	updated, err := r.db.conn.ExecContext(ctx, `UPDATE tasks SET status='failed', result=?, error=?, finished_at=?, updated_at=?, result_reported=0 WHERE id=? AND kind='agent.upgrade' AND status='running' AND started_at=?`, string(raw), errMsg, a.FinishedAt, formatTime(time.Now().UTC()), a.TaskID, formatTime(*t.StartedAt))
	if err != nil {
		return false, err
	}
	n, err := updated.RowsAffected()
	return n == 1, err
}
