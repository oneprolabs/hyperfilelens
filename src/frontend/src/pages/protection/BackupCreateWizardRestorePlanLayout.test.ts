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
    expect(sourceTreeTemplate).toContain('create-tree-node-content--snapshot')
    expect(sourceTreeTemplate).not.toContain('create-tree-node-content__path')
  })
})
