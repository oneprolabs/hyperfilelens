import type { TaskRow } from './taskApi'

function elapsedSeconds(start: string | null | undefined, end: string | null | undefined): number | null {
  if (!start || !end) return null
  const startMs = Date.parse(start)
  const endMs = Date.parse(end)
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return null
  if (endMs < startMs) return null
  return Math.floor((endMs - startMs) / 1000)
}

export function elapsedTaskTime(start: string | null | undefined, end: string | null | undefined): string | null {
  const seconds = elapsedSeconds(start, end)
  if (seconds === null) return null
  const minutes = Math.floor(seconds / 60)
  return minutes ? `${minutes}m ${seconds % 60}s` : `${seconds}s`
}

function formatTotalDuration(seconds: number, exact: boolean): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  const days = Math.floor(seconds / 86_400)
  const hours = Math.floor((seconds % 86_400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const time = `${pad(hours)}:${pad(minutes)}`
  if (days) return `${days}d ${time}${exact ? `:${pad(seconds % 60)}` : ''}`
  return `${time}:${pad(seconds % 60)}`
}

export function totalTaskDuration(
  task: Pick<TaskRow, 'status' | 'started_at' | 'finished_at'>,
  nowMs: number,
  exact = false,
): string | null {
  // Queue time is not task runtime. Only a real start time can begin the clock.
  if (!task.started_at) return null
  const status = String(task.status || '').toLowerCase()
  let end: string | null | undefined
  if (status === 'running') {
    const startMs = Date.parse(task.started_at)
    if (!Number.isFinite(nowMs) || !Number.isFinite(startMs)) return null
    end = new Date(Math.max(nowMs, startMs)).toISOString()
  } else {
    if (!['success', 'failed', 'timeout', 'cancelled'].includes(status)) return null
    end = task.finished_at
  }
  const seconds = elapsedSeconds(task.started_at, end)
  return seconds === null ? null : formatTotalDuration(seconds, exact)
}
