import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { compactSourceText } from '../../test/sourceText'

const entryPage = readFileSync(resolve(process.cwd(), 'src/pages/protection/SnapshotRestorePage.vue'), 'utf8')
const sharedWizard = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')
const compactSharedWizard = compactSourceText(sharedWizard)

describe('SnapshotRestorePage shared wizard entry', () => {
  it('delegates rendering to the existing restore wizard', () => {
    expect(entryPage).toContain("import DataProtection from './DataProtection.vue'")
    expect(entryPage).toContain('standalone-restore')
    expect(entryPage).toContain(':fixed-restore-snapshot-id="snapshotId"')
    expect(entryPage).not.toContain('<el-table')
    expect(entryPage).not.toContain('<WizardSteps')
  })

  it('uses the exact existing Targets, Directories, and Review panes', () => {
    expect(sharedWizard).toContain('v-show="recStep === 1"')
    expect(sharedWizard).toContain(':data="recRecoveryDestSourceRows"')
    expect(sharedWizard).toContain('v-show="recStep === 2"')
    expect(sharedWizard).toContain(':data="recRecoveryDirSourceRows"')
    expect(sharedWizard).toContain('v-show="recStep === 3"')
    expect(sharedWizard).toContain(':data="recRecoveryConfirmSourceRows"')
  })

  it('hides the chooser and first step in fixed-snapshot mode', () => {
    expect(compactSharedWizard).toContain('v-if="!isFixedSnapshotRestore" v-model="recEntryMode"')
    expect(sharedWizard).toContain('.slice(isFixedSnapshotRestore.value ? 1 : 0)')
    expect(sharedWizard).toContain('recStep > (isFixedSnapshotRestore ? 1 : 0)')
  })

  it('returns to Start Backup after cancel, back, or successful submission', () => {
    expect(sharedWizard).toContain("router.replace({ path: '/protection/backups', query: { step: 'start-backup' } })")
    expect(sharedWizard).toContain('@click="closeRecoveryWizard"')
    expect(sharedWizard).toContain('await leaveFixedRestore()')
  })
})
