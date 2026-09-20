type RecordValue = Record<string, unknown>

function record(value: unknown): RecordValue {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {}
}

export const backupFailureCopy: Record<string, { reason: string; resolutions: string[] }> = {
  backup_communication_timeout: {
    reason: 'The backup result could not be confirmed in time because the connection to the execution host was temporarily interrupted or delayed.',
    resolutions: ['Wait until the connection is stable, then retry the failed directory.'],
  },
  backup_source_offline: {
    reason: 'The backup source was offline or reconnecting when the backup failed.',
    resolutions: ['Confirm that the backup source host is powered on and connected to the network.', 'Retry the backup after the source is online.'],
  },
  backup_source_busy: {
    reason: 'The backup source was busy when the backup failed.',
    resolutions: ['Wait for the active operations on the backup source to finish, then retry the backup.'],
  },
  backup_precheck_failed: {
    reason: 'The backup could not pass its prerequisite checks.',
    resolutions: ['Check the backup source, backup configuration, and target repository before retrying.', 'Review the original error for troubleshooting details.'],
  },

}

/** Normalize event metadata and legacy task errors without changing stored data. */
export function backupFailureMetadata(value: unknown): RecordValue {
  const input = record(value)
  const payload = record(input.result_payload)
  const source = { ...payload, ...input }
  const events = Array.isArray(source.recent_events) ? source.recent_events : []
  const eventDetails = events.map(event => record(record(event).metadata).failure_details).find(detail => Object.keys(record(detail)).length)
  const details = record(source.failure_details || payload.failure_details || eventDetails)
  if (Object.keys(details).length && details.category) return { ...source, failure_details: details }
  const code = String(source.error_code || '')
  const message = String(source.error_message || '')
  let category = ''
  if (code === 'AGENT_ACK_TIMEOUT' || code === 'RESULT_ACK_TIMEOUT') category = 'backup_communication_timeout'
  else if (/agent source is offline|agent websocket is reconnecting/i.test(message)) category = 'backup_source_offline'
  else if (/agent source is busy/i.test(message)) category = 'backup_source_busy'
  else if (code === 'BACKUP_PRECHECK_FAILED') category = 'backup_precheck_failed'
  else if (code === 'BACKUP_SOURCE_READ_FAILED') category = 'source_read_failed'
  else if (code === 'BACKUP_SOURCE_FILE_LOCKED') category = 'source_file_locked'
  else if (code === 'BACKUP_TARGET_STORAGE_FULL') category = 'BACKUP_TARGET_STORAGE_FULL'
  else if (code === 'RESTORE_TARGET_PERMISSION_DENIED') category = 'restore_permission_denied'
  else if (code === 'MACOS_PRIVACY_DENIED') category = 'macos_privacy_denied'
  else if (code === 'PERMISSION_DENIED') category = 'permission_denied'
  else if (code === 'SOURCE_ITEMS_SKIPPED') category = 'source_items_skipped'
  return category ? { ...source, failure_details: { ...details, category } } : source
}

export function backupFailurePresentation(value: unknown) {
  const details = record(backupFailureMetadata(value).failure_details)
  return backupFailureCopy[String(details.category || '')]
}

export function backupFailureCategory(value: unknown): string {
  const details = record(backupFailureMetadata(value).failure_details)
  return String(details.category || '')
}
