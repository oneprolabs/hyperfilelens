import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const page = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'),
  'utf8',
)

describe('restore target repository validation flow', () => {
  it('validates selected snapshot repositories before leaving Restore Targets', () => {
    expect(page).toContain('if (!await validateCurrentRestoreTargets()) return')
    expect(page).toContain('validateRestoreTargets({')
    expect(page).toContain('source_snapshot_id: snapshotId')
    expect(page).toContain('target_type: endpoint.type')
    expect(page).toContain('target_ref_id: endpoint.refId')
  })

  it('shows per-source validation progress and failure details below Restore Targets', () => {
    expect(page).toContain(':loading="recStep === 1 && recTargetValidating"')
    expect(page).toContain('const recTargetValidationResults = ref<Record<string, BackupTargetValidationResult>>({})')
    expect(page).toContain('target-connection-result--pending')
    expect(page).toContain('restoreTargetValidationResultForSource(row.hostId)')
    expect(page).toContain('target-connection-result__details')
    expect(page).toContain('@click.stop="showRestoreTargetValidationDetails(row.hostId)"')
    expect(page).toContain('backupTargetValidationFailureSummary({')
    expect(page).toContain('backupTargetValidationFailureDetails({')
    expect(page).toContain("title: t('protection.backupsPage.restoreTargetValidationFailedTitle')")
    expect(page).toContain('openErrorDetails(backupTargetValidationFailureDetails({')
    expect(page).toContain("t('protection.backupsPage.restoreTargetValidationTimedOut')")
  })

  it('records every result and clears stale validation when a target changes', () => {
    expect(page).toContain('recTargetValidationResults.value = displayResults')
    expect(page).toContain("Object.values(displayResults).some((result) => result.status === 'failed')")
    expect(page.match(/clearRestoreTargetValidationForSource\(hostId\)/g)).toHaveLength(2)
  })

  it('blocks macOS and Windows targets for unbound NAS snapshots with a hover warning', () => {
    expect(page).toContain('directNasTargetPlatformBlocked')
    expect(page).toContain("unavailableReason === 'direct_nas_platform'")
    expect(page).toContain('recoveryTargetDirectNasPlatformUnavailable')
    expect(page).toContain('recovery-target-node-option__warning')
    expect(page).toContain(':disabled="!option.selectable"')
    expect(page).toContain("'recovery-target-node-option--disabled': !option.selectable")
    expect(page).toContain('recovery-target-node-option-item--restricted')
    expect(page).toContain('margin-left: auto')
    expect(page).toContain('recovery-target-node-option__restriction-label')
    expect(page).toContain('recoveryTargetUnsupported')
    expect(page).toContain('background: rgb(248 250 252)')
    expect(page).toContain('color: rgb(148 163 184) !important')
  })
})
