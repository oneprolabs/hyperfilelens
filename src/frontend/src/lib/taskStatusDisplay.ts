import { statusTagAttrs, type StatusTagAttrs, type StatusTagTone } from './statusTag'

const TASK_STATUS_ALIASES: Record<string, string> = {
  queued: 'pending',
  in_progress: 'running',
  completed: 'success',
  canceled: 'cancelled',
}

export function normalizeTaskStatus(status?: string | null) {
  const normalized = String(status || '').trim().toLowerCase()
  return TASK_STATUS_ALIASES[normalized] ?? normalized
}

export function taskStatusTone(status?: string | null): StatusTagTone {
  const normalized = normalizeTaskStatus(status)
  if (normalized === 'success' || normalized === 'available') return 'success'
  if (normalized === 'running' || normalized === 'dispatching' || normalized === 'creating') return 'info'
  if (normalized === 'failed' || normalized === 'timeout') return 'danger'
  if (normalized === 'pending' || normalized === 'waiting' || normalized === 'blocked' || normalized === 'retrying' || normalized === 'partial' || normalized === 'degraded' || normalized === 'warning') return 'warning'
  return 'neutral'
}

export function taskStatusTagAttrs(status?: string | null): StatusTagAttrs {
  return statusTagAttrs(taskStatusTone(status))
}

export function taskStatusI18nValue(status?: string | null) {
  return normalizeTaskStatus(status)
}
