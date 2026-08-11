import type { BackupSourceDeleteResult } from './sourceApi'
import type { TaskRow } from './taskApi'
import {
  taskCleanupFailures,
  taskCleanupWarnings,
  taskFailedCleanupChildren,
  taskRetainedResources,
  type TaskCleanupFailure,
} from './taskOutcomeDisplay'

export type SourceUnregisterTaskBinding = {
  sourceId: string
  taskId?: number
  taskUuid: string
  status?: string
}

export type SourceUnregisterPendingKind = 'deleting' | 'delete_waiting' | 'delete_blocked'

export function sourceUnregisterPendingKind(status?: string | null): SourceUnregisterPendingKind {
  const normalized = String(status || '').trim().toLowerCase()
  if (normalized === 'waiting') return 'delete_waiting'
  if (normalized === 'blocked') return 'delete_blocked'
  return 'deleting'
}

export type SourceUnregisterTaskOutcome = {
  terminal: boolean
  success: boolean
  partialSuccess: boolean
  cleanupComplete: boolean
  status: string
  pendingRemovals: Array<{ source_id: string; node_id: number }>
  errorMessage: string
  errorCode?: string
  taskUuid?: string
  currentStep?: string
  failedStep?: string
  hint?: string
  reasons: string[]
  cleanupFailures: TaskCleanupFailure[]
  cleanupWarnings: TaskCleanupFailure[]
  retainedResources: string[]
  failedChildren: Array<{ taskUuid: string; error: string }>
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function reasonStrings(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (typeof item === 'string') {
      const text = item.trim()
      return text ? [text] : []
    }
    if (!item || typeof item !== 'object') return []
    const row = item as Record<string, unknown>
    const detail = String(row.detail || row.message || row.code || '').trim()
    return detail ? [detail] : []
  })
}

export function sourceUnregisterTaskBindings(
  sourceIds: string[],
  result: Pick<BackupSourceDeleteResult, 'tasks' | 'task_id' | 'task_uuid' | 'task_ids' | 'task_uuids'>,
): SourceUnregisterTaskBinding[] {
  if (result.tasks?.length) {
    return result.tasks.flatMap((task) =>
      task.source_id && task.task_uuid
        ? [{
            sourceId: task.source_id,
            taskId: task.task_id,
            taskUuid: task.task_uuid,
            ...(task.status ? { status: task.status } : {}),
          }]
        : [],
    )
  }
  const taskIds = result.task_ids?.length ? result.task_ids : [result.task_id]
  const taskUuids = result.task_uuids?.length ? result.task_uuids : [result.task_uuid]
  return sourceIds.flatMap((sourceId, index) => {
    const taskUuid = taskUuids[index] || ''
    return taskUuid ? [{ sourceId, taskId: taskIds[index], taskUuid }] : []
  })
}

export function sourceUnregisterTaskOutcome(task: TaskRow): SourceUnregisterTaskOutcome {
  const status = String(task.status || '').toLowerCase()
  const terminal = ['success', 'failed', 'cancelled', 'timeout'].includes(status)
  const payload = record(task.result_payload)
  const rawRemovals = Array.isArray(payload.pending_removals) ? payload.pending_removals : []
  const result = String(payload.result || '').toLowerCase()
  const cleanupComplete = payload.cleanup_complete !== false
  const pendingRemovals = rawRemovals.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const row = item as Record<string, unknown>
    const sourceId = String(row.source_id || '')
    const nodeId = Number(row.node_id || 0)
    return sourceId && nodeId > 0 ? [{ source_id: sourceId, node_id: nodeId }] : []
  })
  const eventHints = (task.recent_events || []).flatMap((event) => {
    const meta = record(event.metadata)
    return [
      ...reasonStrings(meta.reasons),
      typeof meta.hint === 'string' ? meta.hint.trim() : '',
    ].filter(Boolean)
  })
  const failedStep = String(payload.failed_step || task.current_step || '').trim() || undefined
  const hint = String(payload.hint || '').trim() || undefined
  return {
    terminal,
    success: status === 'success',
    partialSuccess: status === 'success' && (result === 'partial_success' || !cleanupComplete),
    cleanupComplete,
    status,
    pendingRemovals,
    errorMessage: String(task.error_message || task.error_code || '').trim(),
    errorCode: String(task.error_code || '').trim() || undefined,
    taskUuid: String(task.task_uuid || '').trim() || undefined,
    currentStep: String(task.current_step || '').trim() || undefined,
    failedStep,
    hint,
    reasons: [...new Set([...reasonStrings(payload.reasons), ...eventHints])],
    cleanupFailures: taskCleanupFailures(task),
    cleanupWarnings: taskCleanupWarnings(task),
    retainedResources: taskRetainedResources(task),
    failedChildren: taskFailedCleanupChildren(task),
  }
}
