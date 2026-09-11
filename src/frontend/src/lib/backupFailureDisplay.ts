type RecordValue = Record<string, unknown>

function record(value: unknown): RecordValue {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {}
}

export const backupFailureCopy: Record<string, { reason: string; resolutions: string[] }> = {
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
  const source = record(value)
  const payload = record(source.result_payload)
  const events = Array.isArray(source.recent_events) ? source.recent_events : []
  const eventDetails = events.map(event => record(record(event).metadata).failure_details).find(detail => Object.keys(record(detail)).length)
  const details = record(source.failure_details || payload.failure_details || eventDetails)
  if (Object.keys(details).length) return { ...source, failure_details: details }
  const code = String(source.error_code || '')
  const message = String(source.error_message || '')
  let category = ''
  if (/agent source is offline|agent websocket is reconnecting/i.test(message)) category = 'backup_source_offline'
  else if (/agent source is busy/i.test(message)) category = 'backup_source_busy'
  else if (code === 'BACKUP_PRECHECK_FAILED') category = 'backup_precheck_failed'
  return category ? { ...source, failure_details: { category } } : source
}

export function backupFailurePresentation(value: unknown) {
  const details = record(backupFailureMetadata(value).failure_details)
  return backupFailureCopy[String(details.category || '')]
}
