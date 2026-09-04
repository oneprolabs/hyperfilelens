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

describe('manual restore wizard layout', () => {
  it('transitions manual mode into the outer wizard layout', () => {
    const handler = sourceBetween(
      'function startManualRecoveryWizard()',
      'function onRecoveryEntryModeChange',
    )

    expect(handler).toContain("recEntryMode.value = 'manual'")
    expect(handler).toContain("recEntryStage.value = 'wizard'")
    expect(handler).toContain('ensureInitialRecoveryDirStepRows()')
    expect(handler).toContain('loadRecoveryTargetHostOptions({ reset: true })')
  })

  it('renders one shared step sidebar and main content panel', () => {
    const restoreDialog = sourceBetween(
      'v-if="recOpen"',
      'v-if="addSourceOpen"',
    )

    expect(restoreDialog.match(/<WizardSteps/g)).toHaveLength(1)
    expect(restoreDialog).toContain('responsive-horizontal')
    expect(restoreDialog).toContain('class="fullscreen-form-layout dp-restore-wizard-layout"')
    expect(restoreDialog.match(/<main class="fullscreen-form-main fullscreen-form-main--wizard">/g)).toHaveLength(1)
    expect(restoreDialog).toContain('fullscreen-form-card fullscreen-form-step-section fullscreen-form-step-section--active dp-restore-wizard-card')
    expect(restoreDialog).toContain('fullscreen-form-section dp-restore-wizard-body')
    expect(restoreDialog).toContain('<div class="fullscreen-form-footer">')
    expect(restoreDialog).toContain('fullscreen-form-footer__actions')
    expect(restoreDialog).toContain('<template v-else>')
    expect(restoreDialog).not.toContain('recovery-manual-inline-layout')
    expect(restoreDialog).not.toContain('create-backup-main')
    expect(restoreDialog).not.toContain('create-backup-footer')
    expect(restoreDialog).not.toContain('create-restore-fullscreen')
    expect(restoreDialog).not.toContain('max-height="calc(var(--app-viewport-height)')
  })

  it('returns to a coherent chooser state only for non-fixed restores', () => {
    const handler = sourceBetween(
      'function returnToRecoveryEntryChooser()',
      'function applyRecoveryPlans',
    )
    const footer = sourceBetween(
      '<div class="fullscreen-form-footer">',
      'v-if="addSourceOpen"',
    )

    expect(handler).toContain("recEntryStage.value = 'chooser'")
    expect(handler).toContain("recEntryMode.value = 'plan'")
    expect(handler).toContain('recStep.value = 0')
    expect(footer).toContain("recEntryStage === 'wizard' && !isFixedSnapshotRestore")
    expect(footer).toContain('@click="returnToRecoveryEntryChooser"')
  })

  it('opens fixed-snapshot restores in the same wizard structure', () => {
    const initializer = sourceBetween(
      'async function initializeFixedSnapshotRestore()',
      'type RecoveryDirNode',
    )

    expect(initializer).toContain("recEntryMode.value = 'manual'")
    expect(initializer).toContain("recEntryStage.value = 'wizard'")
    expect(initializer).toContain('recStep.value = 1')
  })

  it('aligns mapping actions and suppresses the redundant policy hover tooltip', () => {
    expect(page).toContain('class="recovery-conflict-policy-cell hfl-table-no-tooltip"')
    expect(page).toContain('class="recovery-target-host-control hfl-table-no-tooltip"')
    expect(page).toMatch(/\.recovery-dir-selection-row \.create-recovery-dir-plan-actions \{[\s\S]*?min-height: 34px;[\s\S]*?align-items: center;/)
    expect(page.match(/44px !important;/g)).toHaveLength(2)
    expect(page).not.toContain('72px !important;')
  })

  it('uses only the dedicated popover for the restore mapping summary', () => {
    const mappingColumn = sourceBetween(
      `:label="t('protection.backupsPage.colRecoveryDirectoryMapping')"`,
      '</el-table-column>',
    )

    expect(mappingColumn).toContain('class-name="hfl-table-no-tooltip"')
    expect(mappingColumn).toContain('popper-class="recovery-entry-preview-tooltip"')
  })

  it('keeps mapping errors in flow so they do not cover the next row', () => {
    const errorRule = page.match(/\.create-recovery-path-input__error \{([\s\S]*?)\n\}/)?.[1] || ''

    expect(errorRule).toContain('margin: 4px 0 0;')
    expect(errorRule).toContain('background: var(--color-error-light);')
    expect(errorRule).not.toContain('position: absolute;')
  })

  it('relies on field errors without a redundant invalid-row state', () => {
    expect(page).not.toContain('recovery-dir-selection-row--invalid')
  })

  it('keeps the selected Restore Scope visible and explains Entire Snapshot', () => {
    const scopeTree = sourceBetween(
      ':key="`restore-snapshot-picker-',
      ':key="`restore-target-dir-picker-',
    )

    expect(page).toContain('function isRecoverySnapshotPickerNodeSelected')
    expect(page).toContain(".get(recoveryDirectoryTreeKey(hostId, entry.id, 'snapshot'))")
    expect(page).toContain('?.setCurrentKey(null)')
    expect(scopeTree).toContain("'recovery-snapshot-tree-node--selected': isRecoverySnapshotPickerNodeSelected(entry, data)")
    expect(scopeTree).toContain("'create-tree-node-content--snapshot': data.type === 'snapshot'")
    expect(scopeTree).toContain("t('protection.backupsPage.createRecoveryScopeSnapshotDesc')")
    expect(scopeTree).not.toContain('<span v-if="data.path"')
    expect(scopeTree).not.toContain('highlight-current')
    expect(page).toContain('.el-tree-node__content:has(> .recovery-snapshot-tree-node--selected)')
    expect(page).toContain('.el-tree-node__expand-icon.is-leaf) {\n  width: 8px;')
    expect(page).toMatch(/\.source-dir-tree :deep\(\.el-tree-node__expand-icon\) \{[\s\S]*?width: 20px;[\s\S]*?flex: 0 0 20px;/)
    expect(page).toMatch(/\.source-dir-tree :deep\(\.el-tree-node\.is-current > \.el-tree-node__content\) \{[\s\S]*?var\(--el-bg-color-overlay\)/)

    const treeLabelRule = page.match(/\.create-tree-node-content__label \{([\s\S]*?)\n\}/)?.[1] || ''
    const treePathRule = page.match(/\.create-tree-node-content__path \{([\s\S]*?)\n\}/)?.[1] || ''
    expect(treeLabelRule).toContain('overflow-wrap: anywhere;')
    expect(treeLabelRule).toContain('white-space: normal;')
    expect(treePathRule).toContain('overflow-wrap: anywhere;')
    expect(treePathRule).toContain('white-space: normal;')
  })

  it('suppresses the redundant restore-plan action hint', () => {
    expect(page).toContain('class="hfl-btn-with-icon recovery-plan-missing-cell__action hfl-table-no-tooltip"')
  })

  it('fills the visible main area with the shared restore wizard section', () => {
    expect(page).toMatch(/\.dp-restore-wizard-body \{[\s\S]*?flex: 1 0 auto;[\s\S]*?min-height: 0;/)
    expect(page).toMatch(/\.dp-restore-wizard-card \{[\s\S]*?display: flex;[\s\S]*?flex: 1 0 auto;[\s\S]*?flex-direction: column;/)
    expect(page).toMatch(/@media \(min-width: 768px\) \{[\s\S]*?\.dp-restore-wizard-layout \{[\s\S]*?flex-direction: row;[\s\S]*?align-items: stretch;/)
  })
})
