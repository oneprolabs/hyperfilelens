import { describe, expect, it } from 'vitest'
import { backupStartResultMessage } from './protectionBackupTaskPresentation'

const t = (key: string) => `translated:${key}`

describe('backup start result presentation', () => {
  it('localizes known storage validation errors', () => {
    expect(
      backupStartResultMessage(
        {
          error_code: 'NAS_REPOSITORY_READ_ONLY',
          message: 'NAS repository is mounted read-only.',
        },
        t,
      ),
    ).toBe('translated:protection.backupsPage.provisionStatusReadOnly')
  })

  it('keeps the backend message for unknown errors', () => {
    expect(
      backupStartResultMessage(
        { error_code: 'REPOSITORY_PROVISION_FAILED', message: 'Try again later.' },
        t,
      ),
    ).toBe('Try again later.')
  })

  it('keeps the same ownership guidance as the configuration drawer', () => {
    expect(
      backupStartResultMessage(
        { error_code: 'AGENT_PROTOCOL_INVALID', message: 'Agent protocol mismatch.' },
        t,
      ),
    ).toBe('translated:protection.backupsPage.provisionStatusOwnershipConflict')
  })
})
