import type { StartBackupTaskResultItem } from './protectionBackupTaskApi'

type Translate = (key: string) => string

const validationErrorLocaleKeys: Record<string, string> = {
  AGENT_UPGRADE_REQUIRED: 'protection.backupsPage.provisionStatusUpgradeRequired',
  NAS_REPOSITORY_READ_ONLY: 'protection.backupsPage.provisionStatusReadOnly',
  NAS_REPOSITORY_WRITE_DENIED: 'protection.backupsPage.provisionStatusWriteDenied',
  NAS_MOUNT_SOURCE_MISMATCH: 'protection.backupsPage.provisionStatusMountMismatch',
  REPOSITORY_OWNERSHIP_INVALID: 'protection.backupsPage.provisionStatusOwnershipConflict',
  AGENT_PROTOCOL_INVALID: 'protection.backupsPage.provisionStatusOwnershipConflict',
}

export function backupStartResultMessage(
  result: Pick<StartBackupTaskResultItem, 'error_code' | 'message'>,
  t: Translate,
) {
  const key = validationErrorLocaleKeys[String(result.error_code || '').trim()]
  return key ? t(key) : result.message
}
