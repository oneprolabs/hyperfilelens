/** Registry code → i18n key under errors.codes.* */
export const ERROR_CODE_I18N_KEYS: Record<string, string> = {
  'NETWORK.UNAVAILABLE': 'errors.codes.networkUnavailable',
  'NETWORK.TIMEOUT': 'errors.codes.networkTimeout',
  'CLIENT.OFFLINE': 'errors.codes.clientOffline',
  'CLIENT.ABORTED': 'errors.codes.clientAborted',
  'UNKNOWN.ERROR': 'errors.codes.unknown',
  'VALIDATION.FAILED': 'errors.codes.validationFailed',
  'AUTH.FORBIDDEN': 'errors.codes.authForbidden',
  'RESOURCE.NOT_FOUND': 'errors.codes.resourceNotFound',
  'RESOURCE.CONFLICT': 'errors.codes.resourceConflict',
  'STORAGE.REPOSITORY_ALREADY_EXISTS': 'errors.codes.storageRepositoryAlreadyExists',
  'STORAGE.REPOSITORY_OPERATION_NOT_CANCELLABLE': 'errors.codes.storageRepositoryOperationNotCancellable',
  'STORAGE.REPOSITORY_OPERATION_NOT_ACTIVE': 'errors.codes.storageRepositoryOperationNotActive',
  'SERVER.INTERNAL_ERROR': 'errors.codes.serverInternal',
  'AGENT.TIMEOUT': 'errors.codes.agentTimeout',
  'AGENT.UNREACHABLE': 'errors.codes.agentUnreachable',
  'AGENT.EXPLORER_LIST_FAILED': 'errors.codes.agentExplorerListFailed',
  'AGENT.PATH_VALIDATE_FAILED': 'errors.codes.agentPathValidateFailed',
  'AGENT.NAS_MOUNT_FAILED': 'errors.codes.agentNasMountFailed',
  'SMB_CHARSET_UNAVAILABLE': 'errors.codes.smbCharsetUnavailable',
  'AGENT.TASK_FAILED': 'errors.codes.agentTaskFailed',
  'BACKUP.QUOTA_EXCEEDED': 'errors.codes.backupQuotaExceeded',
  'SUBSCRIPTION.QUOTA_EXCEEDED': 'errors.codes.subscriptionQuotaExceeded',
  'BACKUP.ALREADY_RUNNING': 'errors.codes.backupAlreadyRunning',
  'RESTORE.ALREADY_RUNNING': 'errors.codes.restoreAlreadyRunning',
}

export const ERROR_CODE_FALLBACK_EN: Record<string, string> = {
  'NETWORK.UNAVAILABLE': 'Unable to connect. Check your network and try again.',
  'NETWORK.TIMEOUT': 'Request timed out. Please try again later.',
  'CLIENT.OFFLINE': 'You are offline. Check your network connection.',
  'UNKNOWN.ERROR': 'Something went wrong. Please try again.',
  'VALIDATION.FAILED': 'Please check the form and try again.',
  'AUTH.FORBIDDEN': "You don't have permission to perform this action.",
  'RESOURCE.NOT_FOUND': "This resource doesn't exist or was removed.",
  'STORAGE.REPOSITORY_ALREADY_EXISTS': 'A Kopia repository already exists at the selected location. Import is not supported in this version. Choose a different storage location.',
  'STORAGE.REPOSITORY_OPERATION_NOT_CANCELLABLE': 'Only controller-managed S3 maintenance tasks can be cancelled.',
  'STORAGE.REPOSITORY_OPERATION_NOT_ACTIVE': 'This maintenance task has already finished. Refresh to see its latest status.',
  'SERVER.INTERNAL_ERROR': 'Service is temporarily unavailable. Please try again later.',
  'AGENT.TIMEOUT': 'Agent timed out. Confirm the node is online and try again.',
  'AGENT.UNREACHABLE': 'Agent is unreachable. Confirm the node is online.',
  'AGENT.EXPLORER_LIST_FAILED': 'Failed to browse directory. Confirm the node is online and try again.',
  'AGENT.PATH_VALIDATE_FAILED': 'Path validation failed. Check the path and try again.',
  'AGENT.NAS_MOUNT_FAILED': 'NAS mount operation failed. Check connection settings.',
  'SMB_CHARSET_UNAVAILABLE': 'SMB UTF-8 filename support is unavailable on the Proxy Host. Install the matching kernel extra-modules package, then remount the share.',
  'AGENT.TASK_FAILED': 'Agent task failed. Please try again.',
  'BACKUP.QUOTA_EXCEEDED': 'Backup quota exceeded. Upgrade your subscription and try again.',
  'SUBSCRIPTION.QUOTA_EXCEEDED':
    'Organization quota is full. Contact your platform administrator to raise limits.',
  'BACKUP.ALREADY_RUNNING': 'A backup is already running for this source.',
  'RESTORE.ALREADY_RUNNING': 'A restore task is already running for this source.',
}

/** Canonical quota_type → English meter label (fallback when i18n is unavailable). */
export const QUOTA_TYPE_METER_FALLBACK_EN: Record<string, string> = {
  max_users: 'Users',
  users: 'Users',
  max_organizations: 'Organizations',
  max_storage_gb: 'Storage',
  storage: 'Storage',
  max_gateways: 'Private Data Gateways',
  gateways: 'Private Data Gateways',
  max_public_gateways: 'Public Data Gateways',
  max_alert_policies: 'Alert Rules',
  alert_policies: 'Alert Rules',
  max_public_gateway_capacity_gb: 'Public Gateway Capacity',
  public_gateway_capacity: 'Public Gateway Capacity',
  'gateway.public_capacity_gb': 'Public Gateway workspace',
  max_nodes: 'Nodes',
  max_source_hosts: 'Source Hosts',
  hosts: 'Source Hosts',
  agents: 'Source Hosts',
  nodes: 'Source Hosts',
  max_proxies: 'Source Agents',
  proxies: 'Source Agents',
  max_source_nas: 'Source NAS',
  source_nas: 'Source NAS',
  max_object_storage: 'Object Storage',
  object_storage: 'Object Storage',
  max_target_nas: 'Target NAS',
  target_nas: 'Target NAS',
  max_standalone_disk: 'Local Disk',
  standalone_disk: 'Local Disk',
  max_protected_sources: 'Protected Sources',
  protected_sources: 'Protected Sources',
  ai_tokens: 'AI Tokens (lifetime)',
  ai_requests: 'AI Tokens (lifetime)',
  ai: 'AI Tokens (lifetime)',
  ai_insights: 'AI Tokens (lifetime)',
  ai_insights_quota: 'AI Tokens (lifetime)',
  gateway_select_max_files: 'Gateway Select file count',
  gateway_select_max_bytes: 'Gateway Select size',
}

/** Caller aliases → canonical quota / pool keys used in APIs and licenseQuota.* */
const QUOTA_TYPE_ALIASES: Record<string, string> = {
  users: 'max_users',
  storage: 'max_storage_gb',
  gateways: 'max_gateways',
  public_gateway_capacity: 'max_public_gateway_capacity_gb',
  'gateway.public_capacity_gb': 'max_public_gateway_capacity_gb',
  hosts: 'max_source_hosts',
  agents: 'max_source_hosts',
  nodes: 'max_source_hosts',
  proxies: 'max_proxies',
  source_nas: 'max_source_nas',
  object_storage: 'max_object_storage',
  target_nas: 'max_target_nas',
  standalone_disk: 'max_standalone_disk',
  protected_sources: 'max_protected_sources',
  ai: 'ai_tokens',
  ai_tokens: 'ai_tokens',
  ai_requests: 'ai_tokens',
  ai_insights: 'ai_tokens',
  ai_insights_quota: 'ai_tokens',
}

export function canonicalizeQuotaType(quotaType: unknown): string {
  const key = String(quotaType || '').trim().toLowerCase()
  return QUOTA_TYPE_ALIASES[key] || key
}

export function quotaTypeMeterLabel(quotaType: unknown): string {
  const key = canonicalizeQuotaType(quotaType)
  return QUOTA_TYPE_METER_FALLBACK_EN[key] || QUOTA_TYPE_METER_FALLBACK_EN[String(quotaType || '').trim().toLowerCase()] || ''
}

const BROWSER_NETWORK_PATTERNS = [
  'failed to fetch',
  'networkerror when attempting to fetch resource',
  'load failed',
  'network request failed',
]

export function isBrowserNetworkMessage(message: string): boolean {
  const m = message.trim().toLowerCase()
  return BROWSER_NETWORK_PATTERNS.some((p) => m.includes(p))
}

export function isRegistryCode(code: string): boolean {
  return Boolean(code && ERROR_CODE_I18N_KEYS[code])
}
