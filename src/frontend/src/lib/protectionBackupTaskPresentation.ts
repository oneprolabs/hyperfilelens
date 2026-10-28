import type { StartBackupTaskResultItem } from './protectionBackupTaskApi'
import type { StorageRepository } from './storageRepositoryApi'
import type { ErrorDetailsPayload } from './errors/details'

type Translate = (key: string) => string

const validationErrorLocaleKeys: Record<string, string> = {
  AGENT_UPGRADE_REQUIRED: 'protection.backupsPage.provisionStatusUpgradeRequired',
  NAS_REPOSITORY_READ_ONLY: 'protection.backupsPage.provisionStatusReadOnly',
  NAS_REPOSITORY_WRITE_DENIED: 'protection.backupsPage.provisionStatusWriteDenied',
  NAS_MOUNT_SOURCE_MISMATCH: 'protection.backupsPage.provisionStatusMountMismatch',
  REPOSITORY_OWNERSHIP_INVALID: 'protection.backupsPage.provisionStatusOwnershipConflict',
  AGENT_PROTOCOL_INVALID: 'protection.backupsPage.provisionStatusOwnershipConflict',
  BACKUP_REPOSITORY_UNREACHABLE: 'protection.backupsPage.backupRepositoryUnreachable',
  BACKUP_REPOSITORY_CHECK_TIMEOUT: 'protection.backupsPage.backupRepositoryCheckTimeout',
  BACKUP_REPOSITORY_CHECK_UNAVAILABLE: 'protection.backupsPage.backupRepositoryCheckUnavailable',
  'SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE': 'errors.codes.subscriptionQuotaUsageUnavailable',
}

export function backupStartResultMessage(
  result: Pick<StartBackupTaskResultItem, 'error_code' | 'message'>,
  t: Translate,
) {
  const key = validationErrorLocaleKeys[String(result.error_code || '').trim()]
  return key ? t(key) : result.message
}

export function backupQuotaUsageDetails(
  result: StartBackupTaskResultItem,
  repositories: StorageRepository[],
  t: Translate,
): ErrorDetailsPayload {
  const instance = result.error_meta?.scope === 'instance'
  const organization = result.error_meta?.scope === 'organization'
  const ids = new Set(result.error_meta?.blocking_repository_ids || [])
  const visible = organization ? repositories.filter((repository) => ids.has(repository.id)) : []
  const summary = t(instance
    ? 'repositoriesPage.quotaUsageInstance'
    : organization
      ? 'repositoriesPage.quotaUsageOrganization'
      : 'repositoriesPage.quotaUsageUnknownScope')
  return {
    title: summary,
    summary,
    severity: 'warning',
    errorCode: 'SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE',
    reasons: [t('repositoriesPage.quotaUsageTargetNotEnough')],
    resolutions: [
      ...(visible.length
        ? [
            'repositoriesPage.quotaUsageRepositoryRecovery',
            'repositoriesPage.quotaUsageRefreshStep',
            'repositoriesPage.quotaUsageTroubleshootStep',
            'repositoriesPage.quotaUsageRetryStep',
          ].map(t)
        : [t('repositoriesPage.quotaUsageContactAdmin')]),
      ...(visible.length < ids.size || result.error_meta?.blocking_repositories_truncated
        ? [t('repositoriesPage.quotaUsageContactAdmin')]
        : []),
    ].filter((value, index, values) => values.indexOf(value) === index),
    entities: visible.map((repository) => ({
      id: repository.id,
      name: repository.name,
      type: 'repository',
    })),
  }
}
