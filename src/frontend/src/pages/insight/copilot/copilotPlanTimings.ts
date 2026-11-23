export type PlanTiming = { startedAt: number; endedAt?: number }

// Only upstream timestamps establish timing windows. Receiving a history or
// reconnect snapshot is not evidence that a step started at that moment.
export function recordPlanTiming(
  previous: PlanTiming | undefined,
  status: string,
  timestamp: string | undefined,
): PlanTiming | undefined {
  const at = Date.parse(timestamp || '')
  if (!Number.isFinite(at)) return previous
  if (status === 'in_progress') {
    return previous || { startedAt: at }
  }
  if (['completed', 'failed', 'skipped'].includes(status) && previous && previous.endedAt == null) {
    return { ...previous, endedAt: Math.max(previous.startedAt, at) }
  }
  return previous
}

// An unfinished window can tick only while both this step and its Run are active.
export function planDuration(timing: PlanTiming | undefined, active: boolean, now: number): string {
  if (!timing || (timing.endedAt == null && !active)) return ''
  const seconds = Math.max(0, Math.round(((timing.endedAt ?? now) - timing.startedAt) / 1000))
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}
