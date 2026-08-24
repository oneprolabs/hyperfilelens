import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

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
    const enter = complete.indexOf('enterStartBackupStep')
    const reconcile = complete.indexOf('reconcileCreatedBackupConfigs')

    expect(enter).toBeGreaterThan(-1)
    expect(reconcile).toBeGreaterThan(enter)
    expect(complete).not.toContain('await refreshStep3AfterMoreAction')
    expect(page).toContain('skipNextFlowStepRefresh = flowMainStep.value !== 2')
    expect(page).toContain('if (skipNextFlowStepRefresh)')
  })

  it('preserves successful state when background reconciliation fails', () => {
    const refresh = sourceBetween(page, 'async function refreshBackupConfigs(', 'function displayNameForSource')
    const reconcile = sourceBetween(page, 'function reconcileCreatedBackupConfigs', 'function finishCreateAndGoToStep3')

    expect(refresh).toContain('if (!options.preserveOnError)')
    expect(reconcile).toContain('preserveExpandedState: true')
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
})
