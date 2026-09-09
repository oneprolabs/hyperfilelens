import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import { transpile } from 'typescript'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')
const wizard = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'), 'utf8')
const shell = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupConfigCreateWizard.vue'), 'utf8')

function sourceBetween(source: string, startMarker: string, endMarker: string) {
  const start = source.indexOf(startMarker)
  const end = source.indexOf(endMarker, start + 1)
  expect(start).toBeGreaterThan(-1)
  expect(end).toBeGreaterThan(start)
  return source.slice(start, end)
}

describe('backup configuration fast transition', () => {
  it.each(['success', 'failure', 'leave'] as const)('coordinates delayed reconciliation: %s', async (outcome) => {
    let settle!: () => void
    let fail!: (error: Error) => void
    const pending = new Promise<void>((resolve, reject) => { settle = resolve; fail = reject })
    const refresh = vi.fn().mockReturnValue(pending)
    const normalLoad = vi.fn().mockResolvedValue(undefined)
    const activeStep = { value: 2 }
    const initialLoad = { value: true }
    const source = sourceBetween(page, 'let createdBackupRefresh:', 'function finishCreateAndGoToStep3')
    const createHarness = new Function(
      'refreshStep3AfterMoreAction', 'refreshFlowStepData', 'flowMainStep',
      'step3InitialLoadPending', 'pageRequests', 'showApiError',
      transpile(source) + '\nreturn { reconcileCreatedBackupConfigs, refreshEnteredFlowStep };',
    )
    const harness = createHarness(refresh, normalLoad, activeStep, initialLoad,
      { isAbortError: () => false }, vi.fn())
    harness.reconcileCreatedBackupConfigs(['agent:1'])
    const entry = harness.refreshEnteredFlowStep(2)
    expect(refresh).toHaveBeenCalledOnce()
    expect(normalLoad).not.toHaveBeenCalled()
    if (outcome === 'success') initialLoad.value = false
    if (outcome === 'leave') activeStep.value = 1
    if (outcome === 'failure') fail(new Error('Count request failed'))
    else settle()
    await entry
    expect(normalLoad).toHaveBeenCalledTimes(outcome === 'failure' ? 1 : 0)
    activeStep.value = 2
    await harness.refreshEnteredFlowStep(2)
    expect(normalLoad).toHaveBeenCalledTimes(outcome === 'failure' ? 2 : 1)
  })

  it('uses the authoritative create response without repeating the pipeline update', () => {
    const create = sourceBetween(wizard, 'async function runCreateBackup', 'function editableGroupPayloads')

    expect(create).toContain('const created = await createBackupConfig(apiPayload, createIdempotencyKey(backup.source.id))')
    expect(create).toContain('createdItems.push({ sourceId: backup.source.id, config: created })')
    expect(create).not.toContain('setPipelineStep(')
    expect(wizard).not.toContain('useBackupSourcePipeline')
    expect(wizard).toContain('const existing = createIdempotencyKeys.value[sourceId]')
  })

  it('enters Step 3 before starting non-blocking reconciliation', () => {
    const complete = sourceBetween(page, 'function finishCreateAndGoToStep3', 'function onCreateBackupPartial')
    const reconcile = sourceBetween(page, 'function reconcileCreatedBackupConfigs', 'function finishCreateAndGoToStep3')
    const enter = complete.indexOf('enterStartBackupStep')
    const reconcileCall = complete.indexOf('reconcileCreatedBackupConfigs')

    expect(enter).toBeGreaterThan(-1)
    expect(reconcileCall).toBeGreaterThan(enter)
    expect(complete).not.toContain('await refreshStep3AfterMoreAction')
    expect(reconcile).toContain('showLoading: true')
    expect(page).not.toContain('skipNextFlowStepRefresh')
    expect(page).toContain('void refreshEnteredFlowStep(step)')
  })

  it('shows Step 3 loading for the full post-create refresh chain', () => {
    const complete = sourceBetween(page, 'function finishCreateAndGoToStep3', 'function onCreateBackupPartial')
    const reconcile = sourceBetween(page, 'function reconcileCreatedBackupConfigs', 'function finishCreateAndGoToStep3')
    const refresh = sourceBetween(page, 'async function refreshStep3AfterMoreAction', 'type BackupCreateResultPayload')
    const enter = complete.indexOf('enterStartBackupStep')
    const reconcileCall = complete.indexOf('reconcileCreatedBackupConfigs')
    const loadingOn = refresh.indexOf('if (showLoading) setFlowStepDataLoading(2, true)')
    const firstRequest = refresh.indexOf('await refreshPipelineCounts(signal)')
    const listRequest = refresh.indexOf('await loadStep3SelectableWithPageClamp(signal')
    const loadingOff = refresh.indexOf('if (showLoading) setFlowStepDataLoading(2, false)')

    expect(complete.slice(enter, reconcileCall)).not.toContain('await ')
    expect(reconcile).toContain('showLoading: true')
    expect(loadingOn).toBeGreaterThan(-1)
    expect(loadingOn).toBeLessThan(firstRequest)
    expect(firstRequest).toBeLessThan(listRequest)
    expect(listRequest).toBeLessThan(loadingOff)
    expect(page).toContain('v-loading="flowStepDataLoading[2]"')
  })

  it('preserves successful state when background reconciliation fails', () => {
    const refresh = sourceBetween(page, 'async function refreshBackupConfigs(', 'function displayNameForSource')
    const reconcile = sourceBetween(page, 'function reconcileCreatedBackupConfigs', 'function finishCreateAndGoToStep3')

    expect(refresh).toContain('if (!options.preserveOnError)')
    expect(reconcile).toContain('preserveExpandedState: true')
  })

  it('hydrates repository names independently from Step 3 reconciliation', () => {
    const hydrate = sourceBetween(page, 'function hydrateCreatedConfigRepositories', 'function mergeCreatedBackupConfigs')
    const merge = sourceBetween(page, 'function mergeCreatedBackupConfigs', 'function reconcileCreatedBackupConfigs')

    expect(hydrate).toContain('ensureRepositoryDetailsForConfigs(items.map((item) => item.config))')
    expect(hydrate).not.toContain('flowStepScope(2)')
    expect(merge).toContain('hydrateCreatedConfigRepositories(items)')
  })

  it('keeps partial successes and separates create from edit outcomes', () => {
    const create = sourceBetween(wizard, 'async function runCreateBackup', 'function editableGroupPayloads')

    expect(create).toContain("emit('createPartial', { items: createdItems })")
    expect(create).toContain("errorCode === 'NETWORK.UNAVAILABLE' || errorCode === 'NETWORK.TIMEOUT'")
    expect(create).toContain("createItemStates.value[backup.source.id] = 'unknown'")
    expect(create).not.toContain("createItemStates.value[sourceId] === 'unknown'")
    expect(create).toContain('normalizedError.status === 401 || normalizedError.status === 403')
    expect(create).toContain("createItemStates.value[sourceId] = 'not_attempted'")
    expect(wizard).toContain("emit('editCompleted', { sourceIds: editedSourceIds })")
    expect(page).toContain('@create-partial="onCreateBackupPartial"')
  })

  it('blocks closing and starting backup while creation or provisioning is unresolved', () => {
    expect(shell).toContain(':disabled="!canClose"')
    expect(wizard).toContain('onBeforeRouteLeave(() => !createSubmissionActive.value)')
    expect(wizard).toContain("window.addEventListener('beforeunload', preventUnloadDuringCreate)")
    expect(page).toContain("String(config.status || '').toLowerCase() === 'active'")
    expect(page).toContain(':disabled="!step3StartBackupEnabled || startBackupSubmitting || step3StopActionBusy"')
    expect(page).toContain('if (runnableSources.length !== sources.length)')
  })

  it('self-heals an empty Step 3 after a cancelled or failed first load', () => {
    expect(page).toContain('const step3InitialLoadPending = ref(initialFlowMainStep === 2)')
    expect(page).toContain('if (step3InitialLoadPending.value) return true')
    expect(page).toContain('if (step3SelectableRows.value.length === 0)')
    expect(page).toContain('await loadStep3Selectable({ signal })')
    expect(page).toContain('syncStep3AutoRefresh()')
  })
})
