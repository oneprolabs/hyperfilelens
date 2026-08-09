import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')
const wizard = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'), 'utf8')
const protectionLocale = readFileSync(resolve(process.cwd(), 'src/locales/enProtectionPages.ts'), 'utf8')

function sourceBetween(startMarker: string, endMarker: string) {
  const start = page.indexOf(startMarker)
  const end = page.indexOf(endMarker, start + 1)

  expect(start).toBeGreaterThan(-1)
  expect(end).toBeGreaterThan(start)
  return page.slice(start, end)
}

function functionSource(name: string, nextName: string) {
  return sourceBetween(`function ${name}`, `function ${nextName}`)
}

describe('backup wizard step 3 More Actions refresh', () => {
  it('disables policy-step detail popovers while a configuration selector is open', () => {
    expect(wizard).toContain('const configSelectMenuOpen = ref(false)')
    expect(wizard).toContain('function handleConfigSelectVisibleChange(visible: boolean)')
    expect(wizard).not.toContain('hideConfigTableHoverPopovers()')
    expect(wizard.match(/@visible-change="handleConfigSelectVisibleChange"/g)).toHaveLength(3)
    expect(wizard.match(/:disabled="configSelectMenuOpen"/g)?.length).toBeGreaterThanOrEqual(4)
  })

  it('keeps directory details while excluding generic overflow tooltips', () => {
    const directoriesStart = wizard.indexOf(":label=\"t('protection.backupsPage.labelBackupDirs')\"")
    const directoriesOpeningTagEnd = wizard.indexOf('>', directoriesStart)
    const directoriesEnd = wizard.indexOf('</el-table-column>', directoriesStart)
    const sourceColumnStart = wizard.lastIndexOf(":label=\"t('protection.backupsPage.colBackupSource')\"", directoriesStart)
    const sourceColumnEnd = wizard.indexOf('>', sourceColumnStart)

    expect(directoriesStart).toBeGreaterThan(-1)
    expect(wizard.slice(sourceColumnStart, sourceColumnEnd)).toContain('min-width="162"')
    expect(wizard.slice(directoriesStart, directoriesOpeningTagEnd)).toContain('min-width="198"')
    expect(wizard.slice(directoriesStart, directoriesOpeningTagEnd)).toContain('class-name="hfl-table-no-tooltip"')
    expect(wizard.slice(directoriesStart, directoriesEnd)).toContain('create-source-dir-preview--single-line-paths')
    expect(wizard.slice(directoriesStart, directoriesEnd)).toContain('hfl-table-no-tooltip')
    expect(wizard.slice(directoriesStart, directoriesEnd)).toContain('placement="bottom-start"')
    expect(wizard).toMatch(/\.create-source-dir-preview--single-line-paths \.create-source-dir-preview__path \{[\s\S]*?text-overflow: ellipsis;[\s\S]*?white-space: nowrap;/)
  })

  it('excludes policy, file-filter, and compression cells from generic overflow tooltips', () => {
    const policyColumnStart = wizard.indexOf(":label=\"t('protection.backupsPage.labelBackupPolicy')\"")
    const policyColumnEnd = wizard.indexOf('>', policyColumnStart)
    const filterColumnStart = wizard.indexOf(":label=\"t('protection.backupsPage.labelFileFilter')\"")
    const filterColumnEnd = wizard.indexOf('>', filterColumnStart)
    const compressionColumnStart = wizard.indexOf(":label=\"t('protection.backupsPage.labelCompressionStrategy')\"")
    const compressionColumnEnd = wizard.indexOf('>', compressionColumnStart)

    expect(policyColumnStart).toBeGreaterThan(-1)
    expect(filterColumnStart).toBeGreaterThan(-1)
    expect(compressionColumnStart).toBeGreaterThan(-1)
    expect(wizard.slice(policyColumnStart, policyColumnEnd)).toContain('class-name="hfl-table-no-tooltip"')
    expect(wizard.slice(filterColumnStart, filterColumnEnd)).toContain('class-name="hfl-table-no-tooltip"')
    expect(wizard.slice(compressionColumnStart, compressionColumnEnd)).toContain('class-name="hfl-table-no-tooltip"')
  })

  it('uses operation-specific loading and saving copy for each edit action', () => {
    const waitingStart = wizard.indexOf('const editorWaitingText')
    const waitingEnd = wizard.indexOf('function hideOptionPopovers', waitingStart)
    const waitingCopy = wizard.slice(waitingStart, waitingEnd)

    expect(waitingStart).toBeGreaterThan(-1)
    expect(waitingEnd).toBeGreaterThan(waitingStart)
    expect(waitingCopy).toContain("t('protection.backupsPage.loadingEditWizard')")
    expect(waitingCopy).toContain("t('protection.backupsPage.waitingEditBackupPaths')")
    expect(waitingCopy).toContain("t('protection.backupsPage.waitingEditBackupPolicy')")
    expect(waitingCopy).toContain("t('protection.backupsPage.waitingEditRestorePlan')")
    expect(waitingCopy).toContain("t('protection.backupsPage.waitingCreate')")
    expect(wizard).toContain(':waiting-text="editorWaitingText"')
    expect(protectionLocale).toContain("waitingEditBackupPaths: 'Saving backup paths…'")
    expect(protectionLocale).toContain("waitingEditBackupPolicy: 'Saving backup policy…'")
    expect(protectionLocale).toContain("waitingEditRestorePlan: 'Saving restore plan…'")
  })

  it('uses save semantics when an edit request fails', () => {
    const editHandlerStart = wizard.indexOf('async function runEditBackupConfig')
    const editHandlerEnd = wizard.indexOf('function submitCreateWizard', editHandlerStart)
    const editHandler = wizard.slice(editHandlerStart, editHandlerEnd)

    expect(editHandler).toContain('const section = activeEditSection.value')
    expect(editHandler).toContain("t('protection.backupsPage.msgEditConfigUnavailable')")
    expect(editHandler).toContain('const step = editStepForSection(section)')
    expect(editHandler).toContain('if (createStep.value !== step) createStep.value = step')
    expect(editHandler).toContain('if (!validateCreateStep(step)) return')
    expect(editHandler).toContain('apiErrorMessage(err, editorFailureText.value)')
    expect(protectionLocale).toContain("editFailed: 'Failed to save backup configuration'")
    expect(protectionLocale).toContain("msgEditConfigUnavailable: 'No editable backup configuration is available. Refresh the page and try again.'")
  })

  it('keeps new edit-mode paths attached to the original backup config', () => {
    const editConfigIdStart = wizard.indexOf('function editBackupConfigIdForSource')
    const editConfigIdEnd = wizard.indexOf('function wizardDirEntryKey', editConfigIdStart)
    const editConfigIdSource = wizard.slice(editConfigIdStart, editConfigIdEnd)
    const editablesStart = wizard.indexOf('function editableGroupPayloads')
    const editablesEnd = wizard.indexOf('function directoryPayloadFromGroup', editablesStart)
    const editablesSource = wizard.slice(editablesStart, editablesEnd)

    expect(editConfigIdSource).toContain('...editConfigs.value')
    expect(editConfigIdSource).toContain('sourceIdFromConfig(config) === sourceId')
    expect(editConfigIdSource).toContain('Number(config.id)')
    expect(editConfigIdSource).toContain('...wizardSourceGroups.value')
    expect(editablesSource).toContain('new Map(editConfigs.value.map((config) => [Number(config.id), config]))')
    expect(editablesSource).toContain('const configId = Number(backup.backupConfigId)')
  })

  it('does not show a success notification after manually refreshing the step 3 list', () => {
    const refresh = functionSource('refreshTaskLists', 'syncStep3TableSelection')

    expect(refresh).not.toContain('ElMessage.success')
  })

  it('loads restore records whenever Step 3 loads its source rows', () => {
    const refresh = sourceBetween('async function refreshFlowStepData', 'function flowRowFromSourceId')
    const loadStep3Index = refresh.indexOf('await loadStep3Selectable({ signal })')
    const configsIndex = refresh.indexOf('await refreshBackupConfigs(signal)')
    const toolbarRefresh = functionSource('refreshTaskLists', 'syncStep3TableSelection')

    expect(loadStep3Index).toBeGreaterThan(-1)
    expect(configsIndex).toBeGreaterThan(loadStep3Index)
    expect(toolbarRefresh).toContain('await refreshFlowStepData()')
    expect(toolbarRefresh).not.toContain('loadStep3Selectable({ signal: signal ?? undefined })')
  })

  it('reloads the full step 3 list state after backup configuration edits complete', () => {
    const editStart = wizard.indexOf('async function runEditBackupConfig')
    const editEnd = wizard.indexOf('function submitCreateWizard', editStart)
    const editHandler = wizard.slice(editStart, editEnd)
    const handler = sourceBetween(
      'async function finishCreateAndGoToStep3',
      'const addSourceOpen',
    )

    expect(editStart).toBeGreaterThan(-1)
    expect(editEnd).toBeGreaterThan(editStart)
    expect(editHandler).toContain('emit(\'completed\', editedSourceIds)')
    expect(editHandler).toContain('`${config.source_type}:${config.source_ref_id}`')
    expect(page).toContain('@completed="finishCreateAndGoToStep3"')
    expect(handler).toContain('await refreshStep3AfterMoreAction({ focusIds: requestedFocusIds })')
  })

  it('reloads the full step 3 list state after stopping backup or restore tasks', () => {
    const stopBackup = functionSource('stopSelectedBackupTasks', 'stopSelectedRestoreTasks')
    const stopRestore = functionSource('stopSelectedRestoreTasks', 'onBackupTaskSelection')

    expect(stopBackup).toContain('await refreshStep3AfterMoreAction()')
    expect(stopRestore).toContain('await refreshStep3AfterMoreAction()')
    expect(stopBackup).not.toContain('await refreshStep3State()')
    expect(stopRestore).not.toContain('await refreshStep3State()')
  })

  it('reloads and clears stale selection after reset or unregister succeeds', () => {
    const reset = functionSource('confirmResetBackupConfiguration', 'onBackupSourcesDeleted')
    const unregister = functionSource('onBackupSourcesDeleted', 'deleteSelectedSourcesFromStep1')

    expect(reset).toContain('await clearStep3TableSelection()')
    expect(reset).toContain('await refreshStep3AfterMoreAction({ preserveSelection: false })')
    expect(unregister).toContain('await clearStep3TableSelection()')
    expect(unregister).toContain('refreshStep3AfterMoreAction({ preserveSelection: false })')
  })

  it('waits for an accepted unregister task to reach a terminal state before the final refresh', () => {
    const monitor = functionSource('monitorPendingUnregister', 'affectedBackupIdsForSources')
    const unregister = functionSource('onBackupSourcesDeleted', 'deleteSelectedSourcesFromStep1')

    expect(monitor).toContain('getTask(taskUuid)')
    expect(monitor).toContain("new Set(['success', 'failed', 'cancelled', 'timeout'])")
    expect(monitor).toContain('refreshStep3AfterMoreAction({ preserveSelection: false })')
    expect(unregister).toContain('const bindings = sourceUnregisterTaskBindings(sourceIds, payload)')
    expect(unregister).toContain('const monitoredSourceIds = bindings.map((binding) => binding.sourceId)')
    expect(unregister).toContain('const monitoredTaskUuids = bindings.map((binding) => binding.taskUuid)')
    expect(unregister).toContain('monitorPendingUnregister(monitoredSourceIds, monitoredTaskUuids)')
  })

  it('refreshes source rows, pipeline membership, configurations, and pagination through one helper', () => {
    const refresh = functionSource('refreshStep3AfterMoreAction', 'finishCreateAndGoToStep3')

    expect(refresh).toContain('pageRequests.nextSignal(scope)')
    expect(refresh).toContain('refreshPipelineStep2PlusIds(signal)')
    expect(refresh).not.toContain('refreshPipelineStep3Ids(signal)')
    expect(refresh).toContain('await loadStep3SelectableWithPageClamp(signal)')
    expect(refresh).toContain('await refreshBackupConfigs(signal)')
    expect(refresh).toContain('pageRequests.isCurrentSignal(scope, signal)')
    expect(refresh).toContain('syncStep3TableSelection()')
  })
})
