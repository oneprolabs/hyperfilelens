import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const wizard = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'),
  'utf8',
)

describe('BackupCreateWizard SMB filename encoding warnings', () => {
  it('renders suspected entries as selectable yellow tooltip rows', () => {
    expect(wizard).toContain('filenameEncodingSuspected: isLikelySmbFilenameEncodingIssue')
    expect(wizard).toContain(':disabled="!data.filenameEncodingSuspected"')
    expect(wizard).toContain('popper-class="smb-filename-encoding-tooltip"')
    expect(wizard).toContain("dirTreeFilenameEncodingWarningTitle")
    expect(wizard).toContain("dirTreeFilenameEncodingWarningReasonLabel")
    expect(wizard).toContain("dirTreeFilenameEncodingWarningReason")
    expect(wizard).toContain("dirTreeFilenameEncodingWarningSolutionLabel")
    expect(wizard).toContain("dirTreeFilenameEncodingWarningSolution")
    expect(wizard).toContain(':global(.smb-filename-encoding-tooltip)')
    expect(wizard).toContain('white-space: normal')
    expect(wizard).toContain("'create-dir-row--filename-encoding-warning': Boolean(data.filenameEncodingSuspected)")
    expect(wizard).toContain('create-dir-row__filename-encoding-icon')
    expect(wizard).not.toContain('disabled: Boolean(item.filenameEncodingSuspected)')
  })

  it('uses the contextual path-not-found toast for load, refresh, and pagination', () => {
    expect(wizard.match(/showSourceTreeFilenamePathError\(/g)).toHaveLength(4)
    expect(wizard).toContain("dedupeKey: `smb-filename-path-not-found:${sourceId}:${path}`")
    expect(wizard).toContain("title: t('protection.backupsPage.dirTreeFilenameEncodingErrorTitle')")
    expect(wizard).toContain("message: t('protection.backupsPage.dirTreeFilenameEncodingErrorMessage')")
  })
})
