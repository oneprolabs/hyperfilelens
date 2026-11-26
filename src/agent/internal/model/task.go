package model

import (
	"strings"
	"time"
)

// TaskStatus is the persisted local execution state for a job unit.
type TaskStatus string

const (
	TaskStatusPending   TaskStatus = "pending"
	TaskStatusRunning   TaskStatus = "running"
	TaskStatusSucceeded TaskStatus = "succeeded"
	TaskStatusFailed    TaskStatus = "failed"
	TaskStatusCancelled TaskStatus = "cancelled"
)

// Task is the local SQLite persistence model for job execution.
// ID matches the control-plane Task UUID (task_id on the wire).
type Task struct {
	ID             string         `json:"id"`
	JobID          string         `json:"job_id,omitempty"`
	Kind           string         `json:"kind,omitempty"`
	Payload        map[string]any `json:"payload,omitempty"`
	Result         map[string]any `json:"result,omitempty"`
	Status         TaskStatus     `json:"status"`
	PID            int            `json:"pid,omitempty"`
	StartedAt      *time.Time     `json:"started_at,omitempty"`
	FinishedAt     *time.Time     `json:"finished_at,omitempty"`
	Error          string         `json:"error,omitempty"`
	ResultReported bool           `json:"result_reported,omitempty"`
	Source         string         `json:"source,omitempty"`
}

// IsResumableTaskKind reports whether running work should survive reconnect
// and agent-process repair until the control plane reconciles it.
func IsResumableTaskKind(kind string) bool {
	switch strings.ToLower(strings.TrimSpace(kind)) {
	case "backup.run", "backup", "backup.snapshot.create", "restore.run", "cache.relocate", "cache.relocation.retry":
		return true
	default:
		return false
	}
}
