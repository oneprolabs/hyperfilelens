import type { TaskRow } from './taskApi'
import { formatTaskProgressPercent } from './kopiaProgress'
import { isRestoreTaskType } from './taskType'

export type ProtectionStopConfirmItem = {
  name: string
  description?: string
  /** Progress text, target path, hostname, or other detail line */
  hint?: string
}

export function restoreTargetPathFromTask(task: TaskRow): string {
  const payload = task.request_payload
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return ''
  return String((payload as Record<string, unknown>).target_path || '').trim()
}

export function buildStopConfirmItemFromTask(task: TaskRow): ProtectionStopConfirmItem {
  const progress = formatTaskProgressPercent(task.progress)
  if (isRestoreTaskType(task.task_type)) {
    const target = restoreTargetPathFromTask(task)
    return {
      name: task.display_name || task.task_uuid,
      hint: target || undefined,
      description: progress,
    }
  }
  return {
    name: task.display_name || task.task_uuid,
    description: progress,
  }
}
