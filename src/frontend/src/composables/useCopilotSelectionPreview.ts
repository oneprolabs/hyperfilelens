import { computed, onScopeDispose, ref, watch, type ComputedRef, type Ref } from 'vue'

import { apiErrorMessage } from '../lib/api'
import {
  cancelCopilotScopePreview,
  fetchCopilotScopePreview,
  previewCopilotAdmission,
  startCopilotScopePreview,
  type LensAdmissionPreview,
  type LensScopePreviewTask,
  type LensScopeSummary,
} from '../lib/lensApi'

export type CopilotSelectionScope = {
  key: string
  revision: number
  directoryId: number
  path: string
  pathType: 'dir' | 'file' | 'unknown'
  knownFileCount: number | null
  knownSizeBytes: number | null
}

export type CopilotSelectionScopeState = {
  status: 'idle' | 'calculating' | 'ready' | 'waiting' | 'error' | 'covered'
  summary: LensScopeSummary | null
  error: string
  retryable: boolean
  coveredBy: string
}

type PreviewOptions = {
  snapshotId: Ref<number | null>
  gatewayLinkId: ComputedRef<number | null>
  gatewayMode: Ref<'auto' | 'manual'>
  scopes: ComputedRef<CopilotSelectionScope[]>
}

// Check once immediately after dispatch, then use a short bounded backoff.
// The Reader task is asynchronous, but a completed task should not add an
// unnecessary full second to the user's selection flow.
const POLL_DELAYS_MS = [250, 400, 650, 1_000]
const MAX_POLLS = 120
const RETRY_DELAYS_MS = [0, 2_000, 5_000, 15_000]
const REQUEST_RETRY_DELAYS_MS = [0, 2_000, 5_000, 15_000]
const RECOVERY_DELAYS_MS = [30_000, 60_000, 120_000, 240_000]
const CHANGE_DEBOUNCE_MS = 400
const CANCEL_TIMEOUT_MS = 5_000

function normalizedPath(value: string): string {
  return value.replace(/\\/g, '/').replace(/\/+$/, '') || '/'
}

function pathContains(parent: string, candidate: string): boolean {
  if (candidate === parent) return true
  if (parent === '/') return candidate.startsWith('/')
  return candidate.startsWith(`${parent}/`)
}

export function canonicalCopilotScopes(scopes: CopilotSelectionScope[]): {
  scopes: CopilotSelectionScope[]
  coveredBy: Record<string, string>
} {
  const canonical: CopilotSelectionScope[] = []
  const coveredBy: Record<string, string> = {}
  const ordered = scopes
    .filter((scope) => scope.directoryId > 0 && scope.path.trim())
    .map((scope, index) => ({ scope, index, normalized: normalizedPath(scope.path) }))
    .sort((left, right) => left.normalized.length - right.normalized.length || left.index - right.index)

  for (const candidate of ordered) {
    const parent = canonical.find((existing) => {
      if (existing.directoryId !== candidate.scope.directoryId) return false
      const parentPath = normalizedPath(existing.path)
      return pathContains(parentPath, candidate.normalized)
    })
    if (parent) {
      coveredBy[candidate.scope.key] = parent.path
      continue
    }
    canonical.push(candidate.scope)
  }
  return { scopes: canonical, coveredBy }
}

function emptyState(): CopilotSelectionScopeState {
  return {
    status: 'idle',
    summary: null,
    error: '',
    retryable: false,
    coveredBy: '',
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      window.clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

function isAbortError(error: unknown): boolean {
  return (error as { name?: string } | null)?.name === 'AbortError'
}

function isRetryableError(error: unknown): boolean {
  const value = error as { retryable?: boolean; status?: number } | null
  return Boolean(value?.retryable || Number(value?.status || 0) >= 500)
}

export function useCopilotSelectionPreview(options: PreviewOptions) {
  const scopeStates = ref<Record<string, CopilotSelectionScopeState>>({})
  const admission = ref<LensAdmissionPreview | null>(null)
  const admissionLoading = ref(false)
  const admissionError = ref('')
  const admissionRetryable = ref(false)
  const currentGeneration = ref(0)
  const completedSummaryCache = new Map<string, LensScopeSummary>()
  const nextAttemptByScope = new Map<string, number>()
  const requestTokenByScope = new Map<string, string>()
  const activeTaskIds = new Set<string>()
  const activeTaskByScope = new Map<string, string>()
  let activeController: AbortController | null = null
  let changeTimer: number | null = null
  let recoveryTimer: number | null = null
  let recoveryAttempt = 0
  let recoverOnExternalSignal = false
  let scheduledTaskResume = false
  let cancellationChain: Promise<void> = Promise.resolve()
  let disposed = false

  const canonical = computed(() => canonicalCopilotScopes(options.scopes.value))
  const totals = computed(() => {
    let fileCount = 0
    let sizeBytes = 0
    for (const scope of canonical.value.scopes) {
      const summary = scopeStates.value[scope.key]?.summary
      if (!summary) return null
      fileCount += Math.max(0, Number(summary.file_count || 0))
      sizeBytes += Math.max(0, Number(summary.size_bytes || 0))
    }
    return { fileCount, sizeBytes }
  })

  const calculationStatus = computed<'idle' | 'calculating' | 'waiting' | 'error' | 'ready'>(() => {
    if (!canonical.value.scopes.length) return 'idle'
    const states = canonical.value.scopes.map((scope) => scopeStates.value[scope.key] || emptyState())
    if (states.some((state) => state.status === 'error')) return 'error'
    if (states.some((state) => state.status === 'waiting')) return 'waiting'
    if (states.every((state) => state.status === 'ready')) return 'ready'
    return 'calculating'
  })

  const ready = computed(() => (
    calculationStatus.value === 'ready'
    && !admissionLoading.value
    && Boolean(admission.value?.admission.allowed)
  ))

  function scopeDataCacheKey(scope: CopilotSelectionScope): string {
    return [
      options.snapshotId.value || 0,
      scope.directoryId,
      normalizedPath(scope.path),
      options.gatewayLinkId.value || 0,
    ].join(':')
  }

  function scopeRevisionKey(scope: CopilotSelectionScope): string {
    return `${scopeDataCacheKey(scope)}:${scope.revision}`
  }

  function requestTokenForScope(cacheKey: string): string {
    const existing = requestTokenByScope.get(cacheKey)
    if (existing) return existing
    const token = globalThis.crypto?.randomUUID
      ? globalThis.crypto.randomUUID()
      : `00000000-0000-4000-8000-${Math.random().toString(16).slice(2).padEnd(12, '0').slice(0, 12)}`
    requestTokenByScope.set(cacheKey, token)
    return token
  }

  function setScopeState(key: string, state: Partial<CopilotSelectionScopeState>) {
    scopeStates.value = {
      ...scopeStates.value,
      [key]: { ...(scopeStates.value[key] || emptyState()), ...state },
    }
  }

  async function fetchTaskWithRetry(taskId: string, signal: AbortSignal): Promise<LensScopePreviewTask> {
    let lastError: unknown = null
    for (const delay of REQUEST_RETRY_DELAYS_MS) {
      if (delay) await sleep(delay, signal)
      try {
        return await fetchCopilotScopePreview(taskId, signal)
      } catch (error) {
        if (isAbortError(error) || !isRetryableError(error)) throw error
        lastError = error
      }
    }
    throw lastError
  }

  async function startTaskWithRetry(
    body: Parameters<typeof startCopilotScopePreview>[0],
    signal: AbortSignal,
  ): Promise<LensScopePreviewTask> {
    let lastError: unknown = null
    for (const delay of REQUEST_RETRY_DELAYS_MS) {
      if (delay) await sleep(delay, signal)
      try {
        return await startCopilotScopePreview(body, signal)
      } catch (error) {
        if (isAbortError(error) || !isRetryableError(error)) throw error
        lastError = error
      }
    }
    throw lastError
  }

  async function previewAdmissionWithRetry(
    body: Parameters<typeof previewCopilotAdmission>[0],
    signal: AbortSignal,
  ): Promise<LensAdmissionPreview> {
    let lastError: unknown = null
    for (const delay of REQUEST_RETRY_DELAYS_MS) {
      if (delay) await sleep(delay, signal)
      try {
        return await previewCopilotAdmission(body, signal)
      } catch (error) {
        if (isAbortError(error) || !isRetryableError(error)) throw error
        lastError = error
      }
    }
    throw lastError
  }

  async function pollTask(task: LensScopePreviewTask, signal: AbortSignal): Promise<LensScopePreviewTask> {
    let current = task
    let polls = 0
    while (current.status === 'pending' || current.status === 'running') {
      if (!current.task_id || polls >= MAX_POLLS) {
        return {
          ...current,
          status: 'client_timeout',
          retryable: false,
          error: 'Selected data calculation is taking longer than expected.',
        }
      }
      if (polls > 0) {
        const delay = POLL_DELAYS_MS[Math.min(polls - 1, POLL_DELAYS_MS.length - 1)]
        await sleep(delay, signal)
      }
      polls += 1
      current = await fetchTaskWithRetry(current.task_id, signal)
    }
    return current
  }

  function trackTask(cacheKey: string, taskId: string) {
    activeTaskByScope.set(cacheKey, taskId)
    activeTaskIds.add(taskId)
  }

  function untrackTask(cacheKey: string, taskId: string) {
    if (activeTaskByScope.get(cacheKey) === taskId) activeTaskByScope.delete(cacheKey)
    activeTaskIds.delete(taskId)
  }

  async function cancelTasks(taskIds: string[]): Promise<boolean> {
    if (!taskIds.length) return true
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), CANCEL_TIMEOUT_MS)
    try {
      const results = await Promise.allSettled(
        taskIds.map((taskId) => cancelCopilotScopePreview(taskId, controller.signal)),
      )
      return results.every((result) => result.status === 'fulfilled')
    } finally {
      window.clearTimeout(timer)
    }
  }

  function enqueueTaskCancellation(taskIds: string[]): Promise<void> {
    if (!taskIds.length) return cancellationChain
    cancellationChain = cancellationChain
      .then(() => cancelTasks(taskIds))
      .then(() => undefined)
    return cancellationChain
  }

  async function summarizeScope(
    scope: CopilotSelectionScope,
    generation: number,
    signal: AbortSignal,
  ): Promise<void> {
    const dataCacheKey = scopeDataCacheKey(scope)
    const revisionKey = scopeRevisionKey(scope)
    const cached = completedSummaryCache.get(dataCacheKey)
    if (cached) {
      setScopeState(scope.key, { status: 'ready', summary: cached, error: '', retryable: false })
      return
    }
    if (scope.knownFileCount != null && scope.knownSizeBytes != null) {
      const summary: LensScopeSummary = {
        path_type: scope.pathType === 'file' ? 'file' : 'dir',
        file_count: Math.max(0, Number(scope.knownFileCount)),
        size_bytes: Math.max(0, Number(scope.knownSizeBytes)),
        skipped_special_count: 0,
      }
      completedSummaryCache.set(dataCacheKey, summary)
      setScopeState(scope.key, { status: 'ready', summary, error: '', retryable: false })
      return
    }

    let attempt = nextAttemptByScope.get(revisionKey) || 0
    while (attempt < RETRY_DELAYS_MS.length) {
      const delay = RETRY_DELAYS_MS[attempt]
      if (delay) await sleep(delay, signal)
      if (generation !== currentGeneration.value || signal.aborted) return
      setScopeState(scope.key, { status: 'calculating', error: '', retryable: false })
      let task: LensScopePreviewTask | null = null
      try {
        const activeTaskId = activeTaskByScope.get(revisionKey)
        if (activeTaskId) {
          task = await fetchTaskWithRetry(activeTaskId, signal)
        } else {
          task = await startTaskWithRetry({
            directory_id: scope.directoryId,
            backup_source_snapshot_id: options.snapshotId.value as number,
            gateway_link_id: options.gatewayLinkId.value as number,
            source_path: scope.path,
            request_token: requestTokenForScope(revisionKey),
            attempt,
          }, signal)
          if (generation !== currentGeneration.value || signal.aborted) {
            if (task.task_id) void enqueueTaskCancellation([task.task_id])
            return
          }
          attempt += 1
          nextAttemptByScope.set(revisionKey, attempt)
          if (task.task_id) trackTask(revisionKey, task.task_id)
        }
        task = await pollTask(task, signal)
        if (generation !== currentGeneration.value || signal.aborted) return
        if (task.status === 'client_timeout') {
          setScopeState(scope.key, {
            status: 'waiting',
            summary: null,
            error: 'Selected data calculation is still running. The system will check it again automatically.',
            retryable: true,
          })
          return
        }
        if (task.status === 'waiting') {
          setScopeState(scope.key, {
            status: 'waiting',
            summary: null,
            error: task.error || 'Waiting for the Repository Reader. Calculation will resume automatically.',
            retryable: true,
          })
          return
        }
        if (task.task_id) untrackTask(revisionKey, task.task_id)
        if (task.status === 'success' && task.summary) {
          completedSummaryCache.set(dataCacheKey, task.summary)
          setScopeState(scope.key, {
            status: 'ready',
            summary: task.summary,
            error: '',
            retryable: false,
          })
          return
        }
        if (!task.retryable) {
          setScopeState(scope.key, {
            status: 'error',
            summary: null,
            error: task.error || 'Unable to calculate the selected data.',
            retryable: false,
          })
          return
        }
      } catch (error) {
        if (
          isAbortError(error)
          || generation !== currentGeneration.value
          || signal.aborted
        ) return
        const waitingTaskId = task?.task_id || activeTaskByScope.get(revisionKey)
        if (waitingTaskId && isRetryableError(error)) {
          trackTask(revisionKey, waitingTaskId)
          setScopeState(scope.key, {
            status: 'waiting',
            summary: null,
            error: 'Waiting for the Repository Reader. Calculation will resume automatically.',
            retryable: true,
          })
          return
        }
        if (isRetryableError(error)) {
          recoverOnExternalSignal = true
          setScopeState(scope.key, {
            status: 'error',
            summary: null,
            error: 'Unable to start the selected data calculation after automatic recovery attempts.',
            retryable: false,
          })
          return
        }
        setScopeState(scope.key, {
          status: 'error',
          summary: null,
          error: (error as { message?: string })?.message || 'Unable to calculate the selected data.',
          retryable: false,
        })
        return
      }
    }
    recoverOnExternalSignal = true
    setScopeState(scope.key, {
      status: 'error',
      summary: null,
      error: 'Unable to calculate the selected data after automatic recovery attempts.',
      retryable: false,
    })
  }

  async function cancelActiveTasks() {
    const ids = [...activeTaskIds]
    activeTaskIds.clear()
    activeTaskByScope.clear()
    await enqueueTaskCancellation(ids)
  }

  async function runPreview({ resumeTasks = false } = {}) {
    currentGeneration.value += 1
    const generation = currentGeneration.value
    activeController?.abort()
    admission.value = null
    admissionLoading.value = false
    admissionError.value = ''
    admissionRetryable.value = false

    const nextStates: Record<string, CopilotSelectionScopeState> = {}
    for (const scope of options.scopes.value) {
      const coveredBy = canonical.value.coveredBy[scope.key]
      nextStates[scope.key] = coveredBy
        ? { ...emptyState(), status: 'covered', coveredBy }
        : { ...emptyState(), status: 'calculating' }
    }
    scopeStates.value = nextStates

    if (!resumeTasks) await cancelActiveTasks()
    if (generation !== currentGeneration.value || disposed) return
    if (!resumeTasks) {
      nextAttemptByScope.clear()
      requestTokenByScope.clear()
    }
    const controller = new AbortController()
    activeController = controller

    if (!options.snapshotId.value || !options.gatewayLinkId.value || !canonical.value.scopes.length) return
    const queue = [...canonical.value.scopes]
    const worker = async () => {
      while (queue.length && generation === currentGeneration.value && !controller.signal.aborted) {
        const scope = queue.shift()
        if (scope) await summarizeScope(scope, generation, controller.signal)
      }
    }
    await Promise.all([worker(), worker()])
    if (
      generation !== currentGeneration.value
      || controller.signal.aborted
      || calculationStatus.value !== 'ready'
      || !totals.value
    ) {
      if (
        generation === currentGeneration.value
        && !controller.signal.aborted
        && calculationStatus.value === 'waiting'
      ) scheduleAutomaticRecovery()
      return
    }

    admissionLoading.value = true
    try {
      const nextAdmission = await previewAdmissionWithRetry({
        gateway_mode: options.gatewayMode.value,
        gateway_link_id: options.gatewayMode.value === 'manual' ? options.gatewayLinkId.value : null,
        file_count: totals.value.fileCount,
        size_bytes: totals.value.sizeBytes,
      }, controller.signal)
      if (generation === currentGeneration.value && !controller.signal.aborted) {
        admission.value = nextAdmission
      }
    } catch (error) {
      if (
        !isAbortError(error)
        && generation === currentGeneration.value
        && !controller.signal.aborted
      ) {
        admissionError.value = apiErrorMessage(error, 'Unable to verify Chat capacity.')
        admissionRetryable.value = isRetryableError(error)
      }
    } finally {
      if (generation === currentGeneration.value) {
        admissionLoading.value = false
        if (automaticRecoveryNeeded()) scheduleAutomaticRecovery()
        else resetAutomaticRecovery()
      }
    }
  }

  function automaticRecoveryNeeded(): boolean {
    return calculationStatus.value === 'waiting'
      || admissionRetryable.value
      || Boolean(
        admission.value?.admission.reasons.includes('organization_capacity_unavailable'),
      )
  }

  function clearRecoveryTimer() {
    if (recoveryTimer != null) window.clearTimeout(recoveryTimer)
    recoveryTimer = null
  }

  function resetAutomaticRecovery() {
    clearRecoveryTimer()
    recoveryAttempt = 0
    recoverOnExternalSignal = false
  }

  async function exhaustAutomaticRecovery() {
    clearRecoveryTimer()
    if (calculationStatus.value === 'waiting') {
      await cancelActiveTasks()
      recoverOnExternalSignal = true
      const nextStates = { ...scopeStates.value }
      for (const [key, state] of Object.entries(nextStates)) {
        if (state.status !== 'waiting') continue
        nextStates[key] = {
          ...state,
          status: 'error',
          error: 'Unable to calculate the selected data after automatic recovery attempts.',
          retryable: false,
        }
      }
      scopeStates.value = nextStates
      return
    }
    if (automaticRecoveryNeeded()) {
      recoverOnExternalSignal = true
      admission.value = null
      admissionRetryable.value = false
      admissionError.value = 'Unable to verify organization capacity after automatic recovery attempts.'
    }
  }

  function scheduleAutomaticRecovery() {
    if (disposed || recoveryTimer != null || !automaticRecoveryNeeded()) return
    if (document.hidden) return
    if (recoveryAttempt >= RECOVERY_DELAYS_MS.length) {
      void exhaustAutomaticRecovery()
      return
    }
    const delay = RECOVERY_DELAYS_MS[recoveryAttempt]
    recoveryAttempt += 1
    recoveryTimer = window.setTimeout(() => {
      recoveryTimer = null
      if (disposed || document.hidden || !automaticRecoveryNeeded()) return
      void runPreview({ resumeTasks: true })
    }, delay)
  }

  function schedulePreview(delay = CHANGE_DEBOUNCE_MS, { resumeTasks = false } = {}) {
    if (changeTimer != null) window.clearTimeout(changeTimer)
    scheduledTaskResume = resumeTasks
    changeTimer = window.setTimeout(() => {
      changeTimer = null
      const shouldResumeTasks = scheduledTaskResume
      scheduledTaskResume = false
      void runPreview({ resumeTasks: shouldResumeTasks })
    }, delay)
  }

  function resumeWaitingPreview() {
    if (
      disposed
      || document.hidden
      || (!automaticRecoveryNeeded() && !recoverOnExternalSignal)
    ) return
    const restartExhaustedPreview = recoverOnExternalSignal
    recoverOnExternalSignal = false
    clearRecoveryTimer()
    recoveryAttempt = 0
    schedulePreview(0, { resumeTasks: !restartExhaustedPreview })
  }

  const fingerprint = computed(() => JSON.stringify({
    snapshotId: options.snapshotId.value,
    gatewayLinkId: options.gatewayLinkId.value,
    gatewayMode: options.gatewayMode.value,
    scopes: options.scopes.value.map((scope) => ({
      key: scope.key,
      revision: scope.revision,
      directoryId: scope.directoryId,
      path: scope.path,
      summaryHint: `${scope.pathType}:${scope.knownFileCount}:${scope.knownSizeBytes}`,
    })),
  }))

  watch(fingerprint, () => {
    resetAutomaticRecovery()
    schedulePreview()
  }, { immediate: true })
  window.addEventListener('online', resumeWaitingPreview)
  document.addEventListener('visibilitychange', resumeWaitingPreview)

  onScopeDispose(() => {
    disposed = true
    if (changeTimer != null) window.clearTimeout(changeTimer)
    clearRecoveryTimer()
    activeController?.abort()
    void cancelActiveTasks()
    window.removeEventListener('online', resumeWaitingPreview)
    document.removeEventListener('visibilitychange', resumeWaitingPreview)
  })

  function stateForScope(key: string): CopilotSelectionScopeState {
    return scopeStates.value[key] || emptyState()
  }

  return {
    admission,
    admissionError,
    admissionLoading,
    calculationStatus,
    ready,
    scopeStates,
    stateForScope,
    totals,
  }
}
