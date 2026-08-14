import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'

const wizardSource = readFileSync(new URL('./BackupCreateWizard.vue', import.meta.url), 'utf8')
const shellSource = readFileSync(new URL('./BackupConfigCreateWizard.vue', import.meta.url), 'utf8')

describe('BackupCreateWizard restore plan layout', () => {
  it('uses the available desktop width without forcing the mapping editor to scroll', () => {
    expect(shellSource).toContain('.create-backup-layout {\n  width: min(100%, 1560px);')
    expect(wizardSource).toContain('.create-recovery-dir-plan-stack {\n  display: flex;\n  min-width: 0;')
    expect(wizardSource).toContain('container-type: inline-size;')
    expect(wizardSource).toContain('minmax(190px, 1.1fr) 64px;')
    expect(wizardSource).toContain('.create-recovery-dir-plan-header > span:last-child {\n  text-align: center;')
    expect(wizardSource).toContain('@container (max-width: 900px)')
    expect(wizardSource).toContain('@container (max-width: 620px)')
    expect(wizardSource).toContain('.create-recovery-dir-plan-cell--actions {\n    grid-column: 3;')
  })

  it('keeps inline validation in document flow', () => {
    const errorRule = wizardSource.match(/\.create-recovery-path-input__error \{([\s\S]*?)\n\}/)?.[1] || ''
    const invalidRowRule = wizardSource.match(/\.create-recovery-dir-plan-row--invalid \{([\s\S]*?)\n\}/)?.[1] || ''

    expect(errorRule).toContain('margin: 4px 0 0;')
    expect(errorRule).not.toContain('position: absolute;')
    expect(invalidRowRule).not.toContain('background:')
  })

  it('clears the failed-save row marker as soon as the mapping is complete', () => {
    expect(wizardSource).toContain("'create-recovery-dir-plan-row--invalid': highlightedRecoveryDirPlanIds.includes(dirPlan.id)")
    expect(wizardSource).toContain('&& !isRecoveryDirPlanComplete(group, dirPlan)')
  })

  it('renders one primary Restore Scope label and exposes its path as a title', () => {
    const sourceTreeTemplate = wizardSource.slice(
      wizardSource.indexOf(':key="`create-recovery-source-'),
      wizardSource.indexOf(':key="`create-recovery-dest-'),
    )

    expect(sourceTreeTemplate).toContain(':title="createRecoverySourceTreePathLabel(data)"')
    expect(wizardSource).toMatch(/isCreateRecoverySourcePathPickerVisible\(group, dirPlan\)[\s\S]*?:width="460"/)
    expect(sourceTreeTemplate).toContain('create-tree-node-content--snapshot')
    expect(sourceTreeTemplate).toContain("'create-tree-node-content--selected': dirPlan.sourcePath === data.path")
    expect(sourceTreeTemplate).toContain('v-if="isWholeSnapshotRecoveryPath(data.path)"')
    expect(sourceTreeTemplate).toContain("t('protection.backupsPage.createRecoveryScopeSnapshotDesc')")
    expect(wizardSource).toContain('.el-tree-node__content:has(> .create-tree-node-content--selected)')
    expect(sourceTreeTemplate).not.toContain('create-tree-node-content__path')
  })

  it('keeps long restore picker values available instead of silently clipping them', () => {
    const treeLabelRule = wizardSource.match(/\.create-tree-node-content__label \{([\s\S]*?)\n\}/)?.[1] || ''

    expect(wizardSource).toContain(':title="recoverySourcePathInputValue(dirPlan.sourcePath) || undefined"')
    expect(wizardSource).toContain('<template #label="{ label }">')
    expect(wizardSource).toContain('class="create-recovery-target-selected-label" :title="label"')
    expect(wizardSource).toContain(':title="dirPlan.restoreDir || undefined"')
    expect(treeLabelRule).toContain('overflow-wrap: anywhere;')
    expect(treeLabelRule).toContain('white-space: normal;')
    expect(treeLabelRule).not.toContain('text-overflow: ellipsis;')
    expect(wizardSource).toContain('.el-tree-node__expand-icon.is-leaf) {\n  width: 8px;')
    expect(wizardSource).toMatch(/\.source-dir-tree :deep\(\.el-tree-node\.is-current > \.el-tree-node__content\) \{[\s\S]*?var\(--el-bg-color-overlay\)/)
    expect(wizardSource).not.toContain('width: min(360px, calc(100vw - 48px)) !important;')
    expect(wizardSource).toContain('class="create-recovery-plan-action hfl-table-no-tooltip"')
  })
})
