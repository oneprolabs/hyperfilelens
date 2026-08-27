import { ref } from 'vue'
import type { RestoreCreateResult } from '../lib/restoreApi'

export type RestoreTaskLifecyclePhase = 'accepted' | 'running' | 'stopping'

export type RestoreTaskLifecycleEntry = {
  sourceId: string
  taskUuid: string
  restoreRecordId: number
  acceptedAt: number
  phase: RestoreTaskLifecyclePhase
}

export type RestoreTaskObservation = {
  taskUuid: string
  status: string
  stopping?: boolean
}

const ACTIVE_TASK_STATUSES = new Set(['pending', 'queued', 'running', 'stopping'])
const TERMINAL_TASK_STATUSES = new Set(['success', 'completed', 'done', 'failed', 'cancelled', 'timeout'])

function normalizedTaskStatus(status: string) {
  return String(status || '').trim().toLowerCase()
}

export function useRestoreTaskLifecycle(now: () => number = Date.now) {
  const entries = ref<Record<string, RestoreTaskLifecycleEntry>>({})

  function get(sourceId: string) {
    return entries.value[sourceId] ?? null
  }

  function accept(sourceId: string, result: RestoreCreateResult) {
    entries.value = {
      ...entries.value,
      [sourceId]: {
        sourceId,
        taskUuid: result.task_uuid,
        restoreRecordId: result.restore_record_id,
        acceptedAt: now(),
        phase: 'accepted',
      },
    }
  }

  function markStopping(sourceId: string, taskUuid: string) {
    const current = get(sourceId)
    entries.value = {
      ...entries.value,
      [sourceId]: {
        sourceId,
        taskUuid,
        restoreRecordId: current?.taskUuid === taskUuid ? current.restoreRecordId : 0,
        acceptedAt: current?.taskUuid === taskUuid ? current.acceptedAt : now(),
        phase: 'stopping',
      },
    }
  }

  function rejectStopping(sourceId: string, taskUuid: string) {
    const current = get(sourceId)
    if (!current || current.taskUuid !== taskUuid || current.phase !== 'stopping') return
    entries.value = {
      ...entries.value,
      [sourceId]: { ...current, phase: 'running' },
    }
  }

  function clear(sourceId: string, taskUuid?: string) {
    const current = get(sourceId)
    if (!current || (taskUuid && current.taskUuid !== taskUuid)) return
    const next = { ...entries.value }
    delete next[sourceId]
    entries.value = next
  }

  function reconcile(observations: RestoreTaskObservation[]) {
    if (!observations.length) return
    const byUuid = new Map(observations.map((item) => [item.taskUuid, {
      status: normalizedTaskStatus(item.status),
      stopping: item.stopping,
    }]))
    const next = { ...entries.value }
    let changed = false
    for (const [sourceId, entry] of Object.entries(entries.value)) {
      const observation = byUuid.get(entry.taskUuid)
      if (!observation) continue
      const { status, stopping } = observation
      if (status === 'cancelled' && stopping !== false) {
        if (entry.phase !== 'stopping') {
          next[sourceId] = { ...entry, phase: 'stopping' }
          changed = true
        }
        continue
      }
      if (TERMINAL_TASK_STATUSES.has(status)) {
        delete next[sourceId]
        changed = true
        continue
      }
      if (ACTIVE_TASK_STATUSES.has(status) && entry.phase !== 'stopping' && entry.phase !== 'running') {
        next[sourceId] = { ...entry, phase: 'running' }
        changed = true
      }
    }
    if (changed) entries.value = next
  }

  function isActive(sourceId: string) {
    return get(sourceId) !== null
  }

  function isStopping(sourceId: string) {
    return get(sourceId)?.phase === 'stopping'
  }

  function staleEntries(maxAgeMs: number) {
    const cutoff = now() - maxAgeMs
    return Object.values(entries.value).filter((entry) => entry.acceptedAt <= cutoff)
  }

  return {
    entries,
    get,
    accept,
    markStopping,
    rejectStopping,
    clear,
    reconcile,
    isActive,
    isStopping,
    staleEntries,
  }
}

export function isExplicitRestoreCancelRejection(error: {
  status?: number
  errorCode?: string
}) {
  const status = Number(error.status || 0)
  if (![400, 403, 405, 409, 422].includes(status)) return false
  return error.errorCode !== 'CLIENT.ABORTED'
}

export type RestoreSubmissionJob = {
  sourceId: string
  run: () => Promise<RestoreCreateResult>
}

export type RestoreSubmissionSuccess = {
  sourceId: string
  result: RestoreCreateResult
}

export type RestoreSubmissionFailure = {
  sourceId: string
  error: unknown
}

export async function runRestoreJobsSequentially(
  jobs: RestoreSubmissionJob[],
  onAccepted: (success: RestoreSubmissionSuccess) => void,
) {
  const succeeded: RestoreSubmissionSuccess[] = []
  const failed: RestoreSubmissionFailure[] = []
  const skippedSourceIds: string[] = []
  const acceptedSourceIds = new Set<string>()

  for (const job of jobs) {
    if (acceptedSourceIds.has(job.sourceId)) {
      skippedSourceIds.push(job.sourceId)
      continue
    }
    try {
      const success = { sourceId: job.sourceId, result: await job.run() }
      succeeded.push(success)
      acceptedSourceIds.add(job.sourceId)
      onAccepted(success)
    } catch (error) {
      failed.push({ sourceId: job.sourceId, error })
    }
  }

  return { succeeded, failed, skippedSourceIds }
}
