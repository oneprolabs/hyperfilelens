import { normalizeTaskStatus } from './taskStatusDisplay'

export type TaskStepTimelineTone = 'success' | 'warning' | 'danger' | 'running' | 'muted' | 'pending'

const SNAPSHOT_DOWNLOAD_STEP_KEYS: Record<string, string> = {
  restore: 'snapshot_download_restore',
  transfer: 'snapshot_download_transfer',
  finalize: 'snapshot_download_finalize',
}

export function taskStepTranslationKey(stepName?: string | null, taskType?: string | null) {
  const step = String(stepName || '').trim()
  if (!step) return null
  const translatedStep = taskType === 'snapshot_download'
    ? SNAPSHOT_DOWNLOAD_STEP_KEYS[step] || step
    : step
  return `ops.task.step.${translatedStep}`
}

export function taskStepTimelineTone(status?: string | null): TaskStepTimelineTone {
  const normalized = normalizeTaskStatus(status)
  if (normalized === 'success' || normalized === 'available') return 'success'
  if (['running', 'dispatching', 'creating'].includes(normalized)) return 'running'
  if (normalized === 'failed' || normalized === 'timeout') return 'danger'
  if (['warning', 'partial', 'degraded', 'blocked'].includes(normalized)) return 'warning'
  if (normalized === 'cancelled' || normalized === 'skipped') return 'muted'
  return 'pending'
}
