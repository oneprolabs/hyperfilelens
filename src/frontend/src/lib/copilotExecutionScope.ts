type ExecutionIdentity = {
  delegated_run_uuid?: unknown
  delegatedRunUuid?: unknown
  assistant_name?: unknown
  assistantName?: unknown
  delegated_task?: unknown
  delegatedTask?: unknown
}

// SSE exposes delegated Run IDs; flattened history may expose only the
// assistant/task pair. Never treat an assistant-local step ID as globally unique.
export function copilotExecutionScope(event: ExecutionIdentity): string {
  const run = event.delegated_run_uuid ?? event.delegatedRunUuid
  if (run) return JSON.stringify(['run', run])
  return JSON.stringify([
    'assistant',
    event.assistant_name ?? event.assistantName ?? '',
    event.delegated_task ?? event.delegatedTask ?? '',
  ])
}
