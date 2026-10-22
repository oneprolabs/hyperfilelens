import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { computed, ref } from 'vue'
import { transpile } from 'typescript'
import { describe, expect, it, vi } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')
const drawer = readFileSync(resolve(process.cwd(), 'src/pages/protection/components/FlowBackupSourceDetailDrawer.vue'), 'utf8')
const editor = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'), 'utf8')

function between(source: string, start: string, end: string) {
  const from = source.indexOf(start)
  const to = source.indexOf(end, from + start.length)
  expect(from).toBeGreaterThan(-1)
  expect(to).toBeGreaterThan(from)
  return source.slice(from, to)
}

describe('Unified Backup Wizard access', () => {
  it('routes a configuration-only member to Step 3 even for an old Source-step bookmark', () => {
    const step = ref(0)
    const canViewSources = ref(false)
    const enter = vi.fn(() => { step.value = 2 })
    const apply = new Function(
      'wizardCanViewSources', 'isFixedSnapshotRestore', 'flowMainStep', 'enterStartBackupStep',
      transpile(between(page, 'function applyFlowStep(', 'function syncFlowStepRoute('))
        + '\nreturn applyFlowStep;',
    )(canViewSources, ref(false), step, enter) as (value: number | null) => void
    apply(0)
    expect(enter).toHaveBeenCalledExactlyOnceWith({ syncRoute: false })
    expect(step.value).toBe(2)
    enter.mockClear()
    canViewSources.value = true
    apply(1)
    expect(step.value).toBe(1)
    expect(enter).not.toHaveBeenCalled()
  })

  it('uses configuration Manage for editing without enabling source-wide cleanup', () => {
    const selection = ref<Array<{ id: string }>>([])
    let manageable = true
    let resetting = false
    let selectable = true
    const canEdit = new Function(
      'computed', 'step3SourceSelection', 'sourceConfigsCanAct', 'sourceResetRunning', 'sourcePendingOps',
      transpile(between(page, 'const step3SelectionEditable =', 'function sourceHasRunningBackupOrRestore('))
        + '\nreturn step3SelectionEditable;',
    )(computed, selection, () => manageable, () => resetting, { isRowSelectable: () => selectable })
    expect(canEdit.value).toBe(false)
    selection.value = [{ id: 'agent:1' }]
    expect(canEdit.value).toBe(true)
    // Force recalculation after changing the mocked capabilities.
    manageable = false
    selection.value = [...selection.value]
    expect(canEdit.value).toBe(false)
    manageable = true
    resetting = true
    selection.value = [...selection.value]
    expect(canEdit.value).toBe(false)
    resetting = false
    selectable = false
    selection.value = [...selection.value]
    expect(canEdit.value).toBe(false)
    const reset = between(page, 'const step3ResetEnabled =', 'const step1UnregisterEnabled =')
    expect(reset).toContain('step3LifecycleActionsEnabled.value')
  })

  it('keeps Step 3 list, polling, and detail reads in configuration scope', () => {
    for (const [start, end] of [
      ['async function loadStep3Selectable(', 'async function refreshStep3State('],
      ['async function refreshStep3RuntimeRows(', 'function sameIdList('],
      ['async function refreshRecoverySourceRuntime(', 'async function '],
    ]) {
      const body = between(page, start, end)
      expect(body).toContain("scope: 'backup_configurations'")
    }
    expect(page).toContain(':configuration-scope="flowMainStep === 2"')
    expect(between(drawer, 'async function loadOverviewForSource(', 'async function loadSnapshotsForSource('))
      .toContain("props.configurationScope ? { scope: 'backup_configurations'")
  })

  it('hydrates config edit sources through configuration scope without broadening target discovery', () => {
    const source = between(editor, 'async function loadWizardSources(', 'watch(restoreTargetCatalog.allRecords')
    expect(source).toContain('editConfigs.value.map(sourceIdFromConfig)')
    expect(source).toContain("ids: contextIds.join(','), scope: 'backup_configurations'")
    expect(source).toContain('restoreTargetCatalog.ensureByIds(inventoryIds)')
  })
})
