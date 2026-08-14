package wire

import "context"

// Sender delivers uplink JSON frames over the active WebSocket session.
type Sender interface {
	SendJSON(ctx context.Context, payload any) error
}

// NewHeartbeat returns the periodic liveness uplink frame.
func NewHeartbeat() Heartbeat {
	return Heartbeat{Type: TypeHeartbeat}
}

// NewHeartbeatWithPayload returns heartbeat with inventory metadata.
func NewHeartbeatWithPayload(payload map[string]any) Heartbeat {
	return Heartbeat{Type: TypeHeartbeat, Payload: payload}
}

// NewTaskProgress builds a task.progress uplink frame.
func NewTaskProgress(taskID string, progress map[string]any) TaskProgress {
	if progress == nil {
		progress = map[string]any{}
	}
	return TaskProgress{
		Type:     TypeTaskProgress,
		TaskID:   taskID,
		Progress: progress,
	}
}

// NewTaskAlive builds a task.alive uplink frame.
func NewTaskAlive(taskID string) TaskAlive {
	return TaskAlive{Type: TypeTaskAlive, TaskID: taskID}
}

// NewTaskAccepted builds a durable task.command acknowledgement.
func NewTaskAccepted(taskID, status string) TaskAccepted {
	return TaskAccepted{Type: TypeTaskAccepted, TaskID: taskID, Status: status}
}

// NewTaskResult builds a task.result uplink frame.
func NewTaskResult(taskID, status string, result map[string]any, errMsg string) TaskResult {
	result, _ = boundTaskResult(result)
	return TaskResult{
		Type:   TypeTaskResult,
		TaskID: taskID,
		Status: status,
		Result: result,
		Error:  truncateUTF8(errMsg, maxResultStringBytes),
	}
}

// SendHeartbeat emits heartbeat on sink.
func SendHeartbeat(ctx context.Context, sink Sender) error {
	if sink == nil {
		return nil
	}
	return sink.SendJSON(ctx, NewHeartbeat())
}

// SendHeartbeatWithPayload emits heartbeat with metrics or inventory metadata.
func SendHeartbeatWithPayload(ctx context.Context, sink Sender, payload map[string]any) error {
	if sink == nil {
		return nil
	}
	return sink.SendJSON(ctx, NewHeartbeatWithPayload(payload))
}

// SendTaskProgress emits task.progress on sink.
func SendTaskProgress(ctx context.Context, sink Sender, taskID string, progress map[string]any) error {
	if sink == nil || taskID == "" {
		return nil
	}
	return sink.SendJSON(ctx, NewTaskProgress(taskID, progress))
}

// SendTaskAlive emits task.alive on sink.
func SendTaskAlive(ctx context.Context, sink Sender, taskID string) error {
	if sink == nil || taskID == "" {
		return nil
	}
	return sink.SendJSON(ctx, NewTaskAlive(taskID))
}

// SendTaskAccepted confirms durable local acceptance of task.command.
func SendTaskAccepted(ctx context.Context, sink Sender, taskID, status string) error {
	if sink == nil || taskID == "" {
		return nil
	}
	return sink.SendJSON(ctx, NewTaskAccepted(taskID, status))
}

// SendTaskResult emits task.result on sink.
func SendTaskResult(ctx context.Context, sink Sender, taskID, status string, result map[string]any, errMsg string) error {
	if sink == nil || taskID == "" {
		return nil
	}
	bounded, stats := boundTaskResult(result)
	if stats.Truncated {
		resultBoundLog(taskID, stats)
	}
	return sink.SendJSON(ctx, NewTaskResult(taskID, status, bounded, errMsg))
}
