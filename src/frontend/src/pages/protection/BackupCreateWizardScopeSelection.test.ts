import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const wizard = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'),
  'utf8',
)

describe('backup scope two-stage selection', () => {
  it('keeps pending tree checks separate from committed backup entries', () => {
    expect(wizard).toContain('const createSourceDirKeysBySource = reactive<Record<string, string[]>>({})')
    expect(wizard).toContain('const wizardDirEntries = ref<BackupDirEntry[]>([])')
    expect(wizard).toMatch(
      /function createSourceTreeCheckedKeys\(sourceId: string\) \{\s+return createSourceCheckedKeys\(sourceId\)/,
    )
    const checkedKeysBlock = wizard.slice(
      wizard.indexOf('function createSourceTreeCheckedKeys'),
      wizard.indexOf('function syncCreateSourceTreeCheckedKeys'),
    )
    expect(checkedKeysBlock).not.toContain('sourceAddedDirPaths')
    expect(wizard).toContain("t('protection.backupsPage.pathPending')")
    expect(wizard).toContain("t('protection.backupsPage.pathAdded')")
    expect(wizard).toContain("t('protection.backupsPage.dirTreeHint')")
    expect(wizard).toContain("t('protection.backupsPage.backupScopeHint')")
  })

  it('does not restore committed edit paths as pending selections', () => {
    const editBlock = wizard.slice(
      wizard.indexOf('function applyEditConfigToWizard'),
      wizard.indexOf('async function openEditConfigs'),
    )
    expect(editBlock).toContain('wizardDirEntries.value = [...wizardDirEntries.value, ...entries]')
    expect(editBlock).not.toContain('createSourceDirKeysBySource[sourceId] =')
    expect(wizard).toContain('backupScopeBaseline.value = backupScopeSignature()')
  })

  it('blocks unresolved pending and manual paths before missing-scope validation', () => {
    const validationBlock = wizard.slice(
      wizard.indexOf('function validateCreateStep'),
      wizard.indexOf('function clearCreateStepSearch'),
    )
    expect(validationBlock.indexOf('focusUnresolvedCreateSourcePaths()')).toBeLessThan(
      validationBlock.indexOf('focusIncompleteCreateSourceDirs()'),
    )
    expect(wizard).toContain('hasUncommittedManualPath()')
    expect(wizard).toContain('hasInFlightSourceScopeOperations()')
    expect(wizard).toContain('isSourceScopeOperationRunningForSource(row.id)')
    expect(wizard).toContain("t('protection.backupsPage.msgPendingPathsMustBeResolved')")
    expect(wizard).toContain("t('protection.backupsPage.msgScopeOperationMustFinish')")
  })

  it('rejects pending parent-child conflicts instead of replacing the prior choice', () => {
    const checkBlock = wizard.slice(
      wizard.indexOf('function onSourceDirCheckChange'),
      wizard.indexOf('function onSourceTreeNodeClick'),
    )
    expect(checkBlock).toContain('const conflict = next.find')
    expect(checkBlock).toContain("t('protection.backupsPage.msgPathSelectionConflict')")
    expect(checkBlock).not.toContain('withoutOverlaps')
    expect(wizard).toContain("t('protection.backupsPage.dirDisabledChildOfPending')")
    expect(wizard).toContain("t('protection.backupsPage.dirDisabledParentOfPending')")
  })

  it('rejects a stale pending conflict atomically before adding or clearing paths', () => {
    const addBlock = wizard.slice(
      wizard.indexOf('function addPickedSourcesFor'),
      wizard.indexOf('function isManualSourcePathValidating'),
    )
    expect(wizard).toContain('function createSourcePendingConflict(sourceId: string)')
    expect(addBlock.indexOf('createSourcePendingConflict(sourceId)')).toBeLessThan(
      addBlock.indexOf('const existingKeys'),
    )
    expect(addBlock).toContain("t('protection.backupsPage.msgPendingPathConflictDetail'")
    expect(addBlock.indexOf("t('protection.backupsPage.msgPendingPathConflictDetail'")).toBeLessThan(
      addBlock.indexOf('createSourceDirKeysBySource[sourceId] = []'),
    )
    expect(wizard).not.toContain('function preserveShallowestPathOrder')
  })

  it('marks a successful edit as saved before closing or notifying the parent', () => {
    const successBlock = wizard.slice(
      wizard.indexOf("ElMessage.success({ message: t('protection.backupsPage.msgSaveEditDemo')"),
      wizard.indexOf('} catch (err) {', wizard.indexOf('async function runEditBackupConfig')),
    )
    expect(successBlock).toContain('backupScopeBaseline.value = backupScopeSignature()')
    expect(successBlock).toContain('allowCreateLeave.value = true')
    expect(successBlock.indexOf('allowCreateLeave.value = true')).toBeLessThan(
      successBlock.indexOf("emit('editCompleted'"),
    )
    expect(successBlock.indexOf('allowCreateLeave.value = true')).toBeLessThan(
      successBlock.indexOf('closeCreate()'),
    )
  })

  it('protects pending and committed scope changes when leaving', () => {
    expect(wizard).toContain('if (!createSubmissionActive.value && !hasBackupScopeDraft()) return')
    expect(wizard).toContain('if (!await confirmDiscardBackupScopeDraft()) return')
    expect(wizard).toContain('onBeforeRouteLeave(async () => {')
  })

  it('captures current files as an aggregated immutable scope', () => {
    expect(wizard).toContain('captureBackupSourceFiles({')
    expect(wizard).toContain("mode: 'direct' | 'recursive'")
    expect(wizard).toContain('max_files: 10000')
    expect(wizard).toContain('capture.entries.map')
    expect(wizard).toContain('captureDirectoryCount: capture.directory_count')
    expect(wizard).toContain("scopeMode: capture.scope_mode")
    expect(wizard).toContain("key: `capture:${entry.captureGroupId}`")
    expect(wizard).toContain('capture_manifest_hash: entry.captureManifestHash')
    expect(wizard).toContain("t('protection.backupsPage.captureCurrentFiles')")
  })

  it('keeps row actions independent from checkbox selection', () => {
    expect(wizard).toContain(':check-on-click-node="false"')
    const captureBlock = wizard.slice(
      wizard.indexOf('class="create-dir-row__add-wrap create-dir-row__action-control"'),
      wizard.indexOf('</ElDropdown>', wizard.indexOf('class="create-dir-row__add-wrap create-dir-row__action-control"')),
    )
    const buttonStart = captureBlock.indexOf('<button')
    const buttonEnd = captureBlock.indexOf('>', buttonStart)
    expect(buttonStart).toBeGreaterThanOrEqual(0)
    expect(captureBlock.slice(buttonStart, buttonEnd)).toContain('create-dir-row__button-action')
    expect(captureBlock.slice(buttonStart, buttonEnd)).toContain('@click.stop')
    expect(wizard).toContain('class="create-dir-row__button-action create-dir-row__icon-action create-dir-row__refresh-action create-dir-row__action-control"')
    expect(wizard).toContain('@click.stop="refreshCreateSourceDirectory(row.id, data)"')
  })

  it('uses one direct-add menu for all single-folder scope modes', () => {
    expect(wizard).toContain('<ElDropdownItem command="dynamic">')
    expect(wizard).toMatch(/<ElDropdownItem\s+command="direct"\s+divided/)
    expect(wizard).toContain('<ElDropdownItem command="recursive">')
    const handlerBlock = wizard.slice(
      wizard.indexOf('function addSourceDirectoryByMode'),
      wizard.indexOf('async function captureCurrentSourceFiles'),
    )
    expect(handlerBlock).toContain("if (mode === 'dynamic')")
    expect(handlerBlock).toContain('addDynamicSourceDirectory(sourceId, data)')
    expect(handlerBlock).toContain("mode === 'direct' || mode === 'recursive'")
    expect(wizard).toContain("scopeMode: 'dynamic'")
    expect(wizard).toContain("t('protection.backupsPage.pathDynamicSummary')")
  })

  it('blocks overlapping pending, committed, and in-flight scope additions', () => {
    const conflictBlock = wizard.slice(
      wizard.indexOf('function sourceScopePathConflict(sourceId: string, path: string)'),
      wizard.indexOf('function addDynamicSourceDirectory'),
    )
    expect(conflictBlock).toContain("state: 'pending' as const")
    expect(conflictBlock).toContain("state: 'added' as const")
    expect(conflictBlock).toContain('sourceScopeOperationForPath(sourceId, path)')
    expect(conflictBlock).toContain("t('protection.backupsPage.addModeOperationRunning'")
    expect(wizard).toContain(':disabled="Boolean(sourceDirectoryAddDisableReason(row.id, data.path))"')
  })

  it('ignores stale static-capture results without clearing a newer operation', () => {
    const captureFunction = wizard.slice(
      wizard.indexOf('async function captureCurrentSourceFiles'),
      wizard.indexOf('function onSourceDirectoryExpansionChange'),
    )
    expect(captureFunction).toContain('capturingSourceFilesByKey[key]?.token !== operation.token')
    expect(captureFunction).toContain('if (!createOpen.value || createStep.value !== 0) return')
    expect(captureFunction).toContain('sourceScopePathConflictReason(sourceId, data.path)')
    expect(captureFunction).toContain('if (capturingSourceFilesByKey[key]?.token === operation.token)')
  })
})
