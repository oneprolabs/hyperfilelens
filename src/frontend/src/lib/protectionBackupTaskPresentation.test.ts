import { describe, expect, it } from 'vitest'
import { backupQuotaUsageDetails, backupStartResultMessage } from './protectionBackupTaskPresentation'
import type { StartBackupTaskResultItem } from './protectionBackupTaskApi'
import type { StorageRepository } from './storageRepositoryApi'

const t = (key: string) => `translated:${key}`

describe('backup quota usage diagnostics', () => {
  const failed: StartBackupTaskResultItem = {
    source_type: 'agent', source_ref_id: 2, backup_config_id: 40,
    task_id: null, task_uuid: null, source_snapshot_id: null, source_snapshot_status: null,
    status: 'failed', error_code: 'SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE', message: 'Incomplete usage',
    error_meta: { scope: 'organization', blocking_repository_ids: [7, 8] },
  }
  const visible = [{ id: 1, name: 'Healthy target' }, { id: 7, name: 'Unmeasured NAS' }] as StorageRepository[]

  it('identifies only server-reported blockers visible through the repository API', () => {
    const details = backupQuotaUsageDetails(failed, visible, t)
    expect(details.summary).toBe(t('repositoriesPage.quotaUsageOrganization'))
    expect(details.entities).toEqual([{ id: 7, name: 'Unmeasured NAS', type: 'repository' }])
    expect(details.resolutions).toContain(t('repositoriesPage.quotaUsageRepositoryRecovery'))
    expect(details.resolutions).toContain(t('repositoriesPage.quotaUsageContactAdmin'))
  })

  it('does not expose repository names for an instance-level failure', () => {
    const details = backupQuotaUsageDetails({ ...failed, error_meta: { ...failed.error_meta, scope: 'instance' } }, visible, t)
    expect(details.entities).toEqual([])
    expect(details.summary).toBe(t('repositoriesPage.quotaUsageInstance'))
    expect(details.resolutions).toEqual([t('repositoriesPage.quotaUsageContactAdmin')])
  })

  it('presents repository recovery as four separate ordered steps', () => {
    const details = backupQuotaUsageDetails({
      ...failed, error_meta: { scope: 'organization', blocking_repository_ids: [7] },
    }, visible, t)
    expect(details.resolutions).toEqual([
      t('repositoriesPage.quotaUsageRepositoryRecovery'),
      t('repositoriesPage.quotaUsageRefreshStep'),
      t('repositoriesPage.quotaUsageTroubleshootStep'),
      t('repositoriesPage.quotaUsageRetryStep'),
    ])
  })

  it('does not infer an organization scope for responses from older servers', () => {
    const details = backupQuotaUsageDetails({ ...failed, error_meta: undefined }, visible, t)
    expect(details.summary).toBe(t('repositoriesPage.quotaUsageUnknownScope'))
    expect(details.entities).toEqual([])
  })

  it('keeps administrator guidance when the repository list is unavailable', () => {
    expect(backupQuotaUsageDetails(failed, [], t).resolutions)
      .toEqual([t('repositoriesPage.quotaUsageContactAdmin')])
  })
})

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

  it('localizes direct NAS admission guidance', () => {
    expect(
      backupStartResultMessage(
        {
          error_code: 'BACKUP_REPOSITORY_CHECK_TIMEOUT',
          message: 'internal timeout',
        },
        t,
      ),
    ).toBe('translated:protection.backupsPage.backupRepositoryCheckTimeout')
  })

  it('localizes quota usage failures returned as backup results', () => {
    expect(
      backupStartResultMessage(
        {
          error_code: 'SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE',
          message: 'Quota usage cannot be measured completely. Retry after usage is available.',
        },
        t,
      ),
    ).toBe('translated:errors.codes.subscriptionQuotaUsageUnavailable')
  })
})
