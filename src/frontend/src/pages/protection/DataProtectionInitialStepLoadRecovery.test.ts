import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')

function sourceBetween(startMarker: string, endMarker: string) {
  const start = page.indexOf(startMarker)
  const end = page.indexOf(endMarker, start + 1)

  expect(start).toBeGreaterThan(-1)
  expect(end).toBeGreaterThan(start)
  return page.slice(start, end)
}

describe('backup wizard initial step load recovery', () => {
  it('does not reload Step 1 when remembered pipeline rows change', () => {
    expect(page).not.toContain('watch(step2PipelineSourceIds')
  })

  it('uses the initial Step 1 page count instead of requesting it twice', () => {
    expect(page).toContain('if (flowMainStep.value !== 0) await refreshBackupSourcePoolCount(signal)')
  })

  it('never reuses an aborted Step 1 request', () => {
    const loader = sourceBetween('async function loadBackupSelectable', 'async function loadStep2Selectable')

    expect(page).toContain("new Map<string, { promise: Promise<void>; signal: AbortSignal }>()")
    expect(loader).toContain('if (running && !running.signal.aborted) return running.promise')
    expect(loader).toContain('if (running) backupSelectableRequests.delete(key)')
    expect(loader).toContain('if (current?.promise === request) backupSelectableRequests.delete(key)')
  })

  it('retries Step 1 after a non-abort loading failure', () => {
    const loader = sourceBetween('async function loadBackupSelectable', 'async function loadStep2Selectable')

    expect(loader).toContain('scheduleFlowStepLoadRetry(0)')
    expect(loader).toContain('clearFlowStepLoadRetry(0)')
  })

  it('retries Step 2 after a non-abort loading failure', () => {
    const refresh = sourceBetween('async function refreshFlowStepData', 'function flowRowFromSourceId')

    expect(refresh).toContain('if (step === 1) scheduleFlowStepLoadRetry(1)')
    expect(page).toContain('clearFlowStepLoadRetry(1)')
  })

  it('keeps automatic retries silent after reporting the initial failure', () => {
    expect(page).toContain("{ showLoading: false, showError: false }")
    expect(page).toContain("if (options.showError !== false) showApiError(e)")
  })

  it('limits automatic recovery to one retry per step entry', () => {
    expect(page).toContain('const flowStepLoadRetryAttempted: Record<0 | 1, boolean>')
    expect(page).toContain('|| flowStepLoadRetryAttempted[step]')
    expect(page).toContain('flowStepLoadRetryAttempted[step] = true')
  })

  it('stops retries after navigation and unmount', () => {
    expect(page).toContain('if (step !== 0) clearFlowStepLoadRetry(0)')
    expect(page).toContain('if (step !== 1) clearFlowStepLoadRetry(1)')
    expect(page).toContain('clearFlowStepLoadRetries()')
  })

  it('settles every primed table flag even when the count bootstrap fails', () => {
    const initialization = sourceBetween('async function initializeFlow()', 'onMounted(async () => {')
    expect(initialization).toContain('await flowLoad.initialize(')
    for (const assignment of [
      'backupSelectableLoading.value = false',
      'flowStepDataLoading[1] = false', 'flowStepDataLoading[2] = false',
      'step3EntryLoadingPrimed = false', 'step3InitialLoadPending.value = false',
      'flowBootstrapping.value = false',
    ]) expect(initialization).toContain(assignment)
    expect(initialization).not.toContain('throw err')
  })

  it('cancels the failed bootstrap before releasing its controller', () => {
    const initialization = sourceBetween('async function initializeFlow()', 'onMounted(async () => {')
    const cancel = initialization.indexOf('if (failed && pageRequests.isCurrentSignal(scope, signal)) pageRequests.abortScope(scope)')
    const release = initialization.indexOf('pageRequests.releaseSignal(scope, signal)')
    expect(initialization).toContain('}, (failed) => {')
    expect(cancel).toBeGreaterThan(-1)
    expect(release).toBeGreaterThan(cancel)
  })

  it('does not retry or poll a denied page, or show the normal workspace on error', () => {
    expect(sourceBetween('function scheduleFlowStepLoadRetry', 'function clearFlowStepLoadRetries')).toContain('flowLoadError.value')
    expect(sourceBetween('function ensureStep3AutoRefreshInterval', 'async function refreshStep3SourceList')).toContain('flowLoadError.value')
    expect(sourceBetween('function handleFlowLoadError', 'function flowRowFromSourceId')).toContain('status === 401 || status === 403')
    expect(page).toContain('v-if="flowLoadError"')
    expect(page).toContain('v-if="!flowAccessDenied"')
  })

  it('does not bootstrap source inventory counts for standalone snapshot restore', () => {
    const initialization = sourceBetween('async function initializeFlow()', 'onMounted(async () => {')
    const restoreBranch = initialization.slice(initialization.indexOf('if (isFixedSnapshotRestore.value)'), initialization.indexOf('} else {'))
    expect(restoreBranch).toContain('await initializeFixedSnapshotRestore()')
    expect(restoreBranch).not.toContain('bootstrapPipeline')
  })
})
