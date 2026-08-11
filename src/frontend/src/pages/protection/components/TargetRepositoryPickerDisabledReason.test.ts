import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const picker = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/components/TargetRepositoryPicker.vue'),
  'utf8',
)
const wizard = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'),
  'utf8',
)
const detailCard = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/components/TargetRepositoryDetailCard.vue'),
  'utf8',
)

describe('target repository disabled reasons', () => {
  it('grays incompatible targets while keeping the repository detail hover available', () => {
    expect(picker).toContain(':disabled="target.disabled"')
    expect(picker).toContain("{ 'is-disabled': target.disabled }")
    expect(picker).toContain(':disabled="!target.repoType"')
    expect(picker).not.toContain('HflHelpTip')
  })

  it('shows a numbered warning below the disabled repository detail title', () => {
    expect(detailCard).toContain('v-if="target.disabledReason"')
    expect(detailCard).toContain('target-repository-detail__disabled-alert')
    expect(detailCard).toContain('target.disabledReasonItems')
    expect(detailCard).toContain('{{ index + 1 }}')
    expect(wizard).toContain('targetIncompatibilityMessageItems(reason)')
  })

  it('applies Linux-only direct NAS compatibility to single and batch assignment', () => {
    expect(wizard).toContain('platform: source?.platform ?? group.platform')
    expect(wizard).toContain('backupTargetIncompatibilityReasonForSources(')
    expect(wizard).toContain('targetDirectNasLinuxOnly')
  })

  it('opens single-target assignment using the reason-based compatibility check', () => {
    expect(wizard).toContain('targetIncompatibilityReasonForGroup(currentTarget, group) === null')
    expect(wizard).not.toContain('isTargetCompatibleWithGroup(')
  })
})
