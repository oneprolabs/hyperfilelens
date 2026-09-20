/**
 * Shared data-transformation logic for backup task failure details.
 *
 * Extracted from TaskEventFailureDetails.vue so buildTaskFailureErrorDetails()
 * (producing ErrorDetailsPayload) and the Vue component can share the same
 * parsing without duplicating logic.
 */
import type { TaskRow } from './taskApi'
import { backupFailureMetadata, backupFailurePresentation } from './backupFailureDisplay'
import type { ErrorDetailsPayload } from './errors/details'
import type { TranslateFn } from './errors/resolver'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type FailureItem = {
  path: string
  error: string
  cause?: string
  item_type?: string
}

export type SkippedItem = FailureItem

export type FailureCauseGroup = {
  code: string
  item_type: string
  count: number
  items: FailureItem[]
}

export type FailureCategory =
  | 'source_read_failed'
  | 'source_file_locked'
  | 'source_items_skipped'
  | 'BACKUP_TARGET_STORAGE_FULL'
  | 'restore_permission_denied'
  | 'macos_privacy_denied'
  | 'permission_denied'
  | 'backup_source_offline'
  | 'backup_source_busy'
  | 'backup_precheck_failed'
  | (string & Record<never, never>)

export type FailureDetails = {
  category: FailureCategory
  total_count?: number
  reported_count?: number
  count?: number
  truncated?: boolean
  items: FailureItem[]
  causes: FailureCauseGroup[]
  remediation: string[]
}

export type SkippedDetails = {
  category: string
  count: number
  reported_count?: number
  truncated?: boolean
  file_count: number
  directory_count: number
  special_count: number
  items: SkippedItem[]
}

export type BackupSummary = {
  snapshot_id: string
  restore_record_id: string
  failed_directories: Array<{ path: string }>
}

const MAX_ITEMS = 10
const EMPTY_FAILURE_DETAILS: FailureDetails = {
  category: 'source_read_failed',
  items: [],
  causes: [],
  remediation: [],
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function positiveCount(value: unknown): number {
  const num = Number(value)
  return Number.isFinite(num) && num > 0 ? num : 0
}

function extractItems(value: unknown): FailureItem[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const rec = item as Record<string, unknown>
    const path = String(rec.path || '').trim()
    const error = String(rec.error || '').trim()
    const cause = String(rec.cause || '').trim()
    const itemType = String(rec.item_type || '').trim()
    return path || error
      ? [{ path, error, cause, item_type: itemType }]
      : []
  })
}

function extractCauses(value: unknown): FailureCauseGroup[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const rec = item as Record<string, unknown>
    const code = String(rec.code || '').trim()
    const itemType = String(rec.item_type || '').trim()
    const count = Number(rec.count)
    const sampled = Array.isArray(rec.items)
      ? rec.items.flatMap((sample) => {
        if (!sample || typeof sample !== 'object' || Array.isArray(sample)) return []
        const sr = sample as Record<string, unknown>
        const path = String(sr.path || '').trim()
        const error = String(sr.error || '').trim()
        return path || error
          ? [{
            path,
            error,
            cause: String(sr.cause || code).trim(),
            item_type: String(sr.item_type || itemType).trim(),
          }]
          : []
      })
      : []
    return code && Number.isFinite(count) && count > 0
      ? [{ code, item_type: itemType, count, items: sampled }]
      : []
  })
}

function extractStrings(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map(it => String(it || '').trim()).filter(Boolean)
}

// ---------------------------------------------------------------------------
// Extracted extractors
// ---------------------------------------------------------------------------

export function extractFailureDetails(metadata: unknown): FailureDetails | null {
  const meta = backupFailureMetadata(metadata)
  const details = record(meta.failure_details)
  if (Object.keys(details).length === 0) return null

  const items = extractItems(details.items)
  const causes = extractCauses(details.causes)
  const remediation = extractStrings(details.remediation)
  const totalCount = positiveCount(details.total_count ?? details.count) || items.length
  const reportedCount = Number.isFinite(Number(details.reported_count)) ? Number(details.reported_count) : items.length

  return {
    category: String(details.category || 'source_read_failed'),
    total_count: totalCount,
    reported_count: Math.max(0, reportedCount),
    count: totalCount,
    truncated: Boolean(details.truncated) || totalCount > reportedCount,
    items,
    causes,
    remediation,
  }
}

export function extractSkippedDetails(metadata: unknown): SkippedDetails | null {
  const meta = backupFailureMetadata(metadata)
  const details = record(meta.skipped_details)
  const itemCount = positiveCount(details.count ?? meta.skipped_item_count)
  const fileCount = positiveCount(details.file_count ?? meta.skipped_file_count)
  const dirCount = positiveCount(details.directory_count ?? meta.skipped_directory_count)
  const specialCount = positiveCount(details.special_count ?? meta.skipped_special_count)
  const allItems = extractItems(details.items)
  const count = itemCount || fileCount + dirCount + specialCount || allItems.length
  if (!count) return null

  const items = allItems.slice(0, MAX_ITEMS)
  const reportedCount = Math.min(MAX_ITEMS, positiveCount(details.reported_count) || items.length)

  return {
    category: String(details.category || 'source_items_skipped'),
    count,
    reported_count: reportedCount,
    truncated: Boolean(details.truncated) || count > items.length || (Array.isArray(details.items) && details.items.length > MAX_ITEMS),
    file_count: fileCount,
    directory_count: dirCount,
    special_count: specialCount,
    items,
  }
}

export function extractBackupSummary(metadata: unknown): BackupSummary | null {
  const meta = backupFailureMetadata(metadata)
  const summary = record(meta.backup_summary)
  const snapshotId = String(summary.snapshot_id || '').trim()
  const restoreRecordId = String(summary.restore_record_id || '').trim()
  const failedDirs = Array.isArray(summary.failed_directories)
    ? summary.failed_directories.flatMap((item) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return []
      const path = String((item as Record<string, unknown>).path || '').trim()
      return path ? [{ path }] : []
    })
    : []
  if (!snapshotId && !restoreRecordId && !failedDirs.length) return null
  return { snapshot_id: snapshotId, restore_record_id: restoreRecordId, failed_directories: failedDirs }
}

/** Returns true when metadata has any displayable failure or skipped details. */
export function hasFailureDetails(metadata: unknown): boolean {
  const meta = backupFailureMetadata(metadata)
  return Boolean(extractFailureDetails(meta))
    || Boolean(extractSkippedDetails(meta))
    || Boolean(extractBackupSummary(meta)?.failed_directories.length)
    || Number(meta.skipped_item_count || meta.skipped_file_count || meta.skipped_directory_count || meta.skipped_special_count) > 0
}

// ---------------------------------------------------------------------------
// Build ErrorDetailsPayload
// ---------------------------------------------------------------------------

function taskSummary(task: TaskRow, t?: TranslateFn): string {
  const name = task.display_name || task.task_uuid || ''
  const status = String(task.status || '').toLowerCase()
  if (status === 'failed' || status === 'timeout') {
    return t ? t('ops.task.failureDetails.taskFailed', { name }) : `Task "${name}" failed.`
  }
  if (status === 'cancelled') {
    return t ? t('ops.task.failureDetails.taskCancelled', { name }) : `Task "${name}" was cancelled.`
  }
  return name
}

function severityFromTask(task: TaskRow): 'error' | 'warning' {
  const status = String(task.status || '').toLowerCase()
  if (status === 'success' || status === 'partial') return 'warning'
  return 'error'
}

/**
 * Build a unified ErrorDetailsPayload from a TaskRow and optional event
 * metadata, suitable for opening in HflErrorDetailsDialog.
 */
export function buildTaskFailureErrorDetails(params: {
  task: TaskRow
  metadata?: unknown
  t?: TranslateFn
}): ErrorDetailsPayload {
  const { task, metadata, t } = params
  const meta = backupFailureMetadata({ ...task, ...record(metadata) })
  const contract = task.error_details
  if (contract) {
    const itemText = (item: unknown): string => {
      if (typeof item === 'string') return item
      const value = record(item)
      return [value.path || value.source_name || value.source_id, value.detail || value.error || value.message]
        .filter(Boolean).map(String).join(': ')
    }
    const skipped = record(contract.skipped_items)
    const skippedItems = Array.isArray(contract.skipped_items) ? contract.skipped_items : skipped.items
    return {
      title: contract.severity === 'warning'
        ? (t ? t('ops.task.failureDetails.warningTitle') : 'Task completed with warnings')
        : (t ? t('ops.task.failureDetails.failureTitle') : 'Task failed'),
      summary: contract.summary || taskSummary(task, t),
      severity: contract.severity,
      taskUuid: contract.task_uuid || task.task_uuid,
      traceId: contract.correlation_id || undefined,
      issue: contract.limited
        ? (t ? t('feedback.errorDetails.limited') : 'Detailed diagnostic information is unavailable for this task.')
        : undefined,
      cleanupResidue: {
        hasResidue: contract.cleanup_complete === false || Boolean(contract.retained_resources?.length),
        retainedResources: contract.retained_resources,
        failures: contract.cleanup_failures?.map(itemText).filter(Boolean),
        skippedItems: Array.isArray(skippedItems) ? skippedItems.map(itemText).filter(Boolean) : undefined,
      },
      taskType: task.task_type,
      failedStep: contract.failed_step || undefined,
      errorCode: contract.error_code || task.error_code || undefined,
      reasons: contract.reasons?.map(reason => reason.count ? `${reason.detail} (${reason.count})` : reason.detail),
      resolutions: contract.suggestions?.map(suggestion => suggestion.detail),
      entities: contract.entities?.map(entity => ({
        id: entity.id,
        name: entity.name,
        type: (['source', 'repository', 'node', 'child_task'].includes(entity.type) ? entity.type : 'source') as 'source' | 'repository' | 'node' | 'child_task',
        error: entity.error,
      })),
      rawDetail: contract.technical_detail,
    }
  }
  const failureDetails = extractFailureDetails(meta) || EMPTY_FAILURE_DETAILS
  const skippedDetails = extractSkippedDetails(meta)
  const backupSummary = extractBackupSummary(meta)
  const friendly = backupFailurePresentation(meta)

  const severity = severityFromTask(task)
  const title = severity === 'warning'
    ? (t ? t('ops.task.failureDetails.warningTitle') : 'Task completed with warnings')
    : (t ? t('ops.task.failureDetails.failureTitle') : 'Task failed')

  // Build reasons from structured details
  const reasons: string[] = []

  if (friendly) {
    reasons.push(friendly.reason)
  } else if (failureDetails.category && extractFailureDetails(meta) && t) {
    const key = `ops.task.failureDetails.summary.${failureDetails.category}`
    const translated = t(key, { count: failureDetails.total_count ?? 0 })
    if (translated && translated !== key) reasons.push(translated)
    else reasons.push(`${failureDetails.total_count} item(s) failed: ${failureDetails.category}`)
  }

  if (skippedDetails && skippedDetails.count > 0 && t) {
    reasons.push(t('ops.task.failureDetails.summary.source_items_skipped', {
      count: skippedDetails.count,
      fileCount: skippedDetails.file_count,
      directoryCount: skippedDetails.directory_count,
      specialCount: skippedDetails.special_count,
    }))
  }

  if (backupSummary && backupSummary.failed_directories.length > 0 && t) {
    reasons.push(t('ops.task.failureDetails.failedDirectoriesReason', {
      count: backupSummary.failed_directories.length,
    }))
  }

  // Fallback to task error_message
  if (!reasons.length && task.error_message) {
    reasons.push(task.error_message)
  }

  // Resolutions from friendly copy or failure remediation codes
  const resolutions: string[] = []
  if (friendly) {
    resolutions.push(...friendly.resolutions)
  } else if (failureDetails.remediation.length && t) {
    resolutions.push(...failureDetails.remediation.map(code => {
      const key = `ops.task.failureDetails.remediation.${code}`
      const translated = t(key)
      return translated !== key ? translated : code
    }))
  }

  // Build entities from affected items
  const entities: ErrorDetailsPayload['entities'] = []

  // Add source entity if source_path is present
  const sourcePath = String(meta.source_path || '').trim()
  if (sourcePath) {
    entities.push({
      id: sourcePath,
      name: sourcePath,
      type: 'source' as const,
      error: String(meta.error_code || '').trim() || undefined,
    })
  }

  // Add failed directory entities from backup_summary
  if (backupSummary) {
    for (const dir of backupSummary.failed_directories) {
      entities.push({
        id: dir.path,
        name: dir.path,
        type: 'source' as const,
      })
    }
  }

  // Add top affected item paths
  for (const item of failureDetails.items) {
    if (item.path && !entities.some(e => e.id === item.path)) {
      entities.push({
        id: item.path,
        name: item.path,
        type: 'source' as const,
        error: item.error || undefined,
      })
    }
  }

  // Build rawDetail for technical section
  const rawDetail = {
    task_uuid: task.task_uuid,
    task_type: task.task_type,
    status: task.status,
    error_code: task.error_code || null,
    error_message: task.error_message || null,
    failure_details: failureDetails,
    ...(skippedDetails ? { skipped_details: skippedDetails } : {}),
    ...(backupSummary ? { backup_summary: backupSummary } : {}),
  }

  return {
    title,
    summary: taskSummary(task, t),
    severity,
    taskUuid: task.task_uuid,
    taskType: task.task_type,
    failedStep: task.current_step || undefined,
    issue: !extractFailureDetails(meta) && !skippedDetails && !backupSummary
      ? (t ? t('feedback.errorDetails.limited') : 'Detailed diagnostic information is unavailable for this task.')
      : undefined,
    errorCode: task.error_code || String(meta.error_code || '').trim() || undefined,
    reasons: reasons.length ? reasons : undefined,
    resolutions: resolutions.length ? resolutions : undefined,
    entities: entities.length ? entities : undefined,
    rawDetail,
  }
}

/**
 * Build an ErrorDetailsPayload from a BackupSourceSnapshot for partial/failed
 * snapshot outcomes that may not have an associated TaskRow.
 */
export function buildSnapshotFailureErrorDetails(params: {
  task?: TaskRow | null
  t?: TranslateFn
  snapshotId?: string
  snapshotStatus?: string
  sourceName?: string
  errorCode?: string
  errorMessage?: string
  failedDirectoryCount?: number
  successfulDirectoryCount?: number
}): ErrorDetailsPayload | null {
  const { task, t, snapshotId, snapshotStatus, sourceName, errorCode, errorMessage, failedDirectoryCount, successfulDirectoryCount } = params
  const status = String(snapshotStatus || '').toLowerCase()
  if (!['partial', 'failed', 'delete_failed'].includes(status)) return null

  const severity: 'error' | 'warning' = status === 'partial' ? 'warning' : 'error'
  const title = severity === 'warning'
    ? (t ? t('ops.task.failureDetails.snapshotPartialTitle') : 'Snapshot completed with partial results')
    : (t ? t('ops.task.failureDetails.snapshotFailedTitle') : 'Snapshot failed')

  const summary = sourceName
    ? (t ? t(severity === 'warning' ? 'ops.task.failureDetails.snapshotPartialForSource' : 'ops.task.failureDetails.snapshotFailedForSource', { source: sourceName }) : `Snapshot for "${sourceName}" ${severity === 'warning' ? 'completed with partial results' : 'failed'}.`)
    : (t ? t(severity === 'warning' ? 'ops.task.failureDetails.snapshotPartialSummary' : 'ops.task.failureDetails.snapshotSummary', { id: snapshotId }) : `Snapshot ${snapshotId} ${severity === 'warning' ? 'is partial' : 'failed'}.`)

  const reasons: string[] = []
  if (failedDirectoryCount && failedDirectoryCount > 0 && t) {
    reasons.push(t('ops.task.failureDetails.snapshotFailedDirs', {
      failed: failedDirectoryCount,
      total: failedDirectoryCount + (successfulDirectoryCount ?? 0),
    }))
  }
  if (errorMessage) reasons.push(errorMessage)

  // Use the task's failure details if available
  let resolutions: string[] | undefined
  let rawDetail: unknown = { snapshot_id: snapshotId, snapshot_status: snapshotStatus, source_name: sourceName, error_code: errorCode, error_message: errorMessage }
  if (task) {
    const meta = backupFailureMetadata({ error_code: task.error_code, error_message: task.error_message, result_payload: task.result_payload, recent_events: task.recent_events })
    const friendly = backupFailurePresentation(meta)
    if (friendly) resolutions = friendly.resolutions
    rawDetail = { ...(rawDetail as Record<string, unknown>), task_uuid: task.task_uuid, task_error_code: task.error_code, task_error_message: task.error_message }
  }

  return {
    title,
    summary,
    severity,
    taskUuid: task?.task_uuid,
    taskType: task?.task_type,
    errorCode: errorCode || task?.error_code || undefined,
    reasons: reasons.length ? reasons : undefined,
    resolutions,
    entities: sourceName ? [{ id: sourceName, name: sourceName, type: 'source' as const }] : undefined,
    rawDetail,
  }
}

/**
 * Build an ErrorDetailsPayload for a backup_config_provision task failure.
 * Provision tasks have a `provisioning_error_code` separate from the task's
 * own error_code and may carry `backup_config_discarded: true`.
 */
export function buildProvisionFailureErrorDetails(params: {
  task: TaskRow
  provisioningErrorCode?: string | null
  provisioningErrorMessage?: string | null
  backupConfigDiscarded?: boolean
  t?: TranslateFn
}): ErrorDetailsPayload {
  const { task, provisioningErrorCode, provisioningErrorMessage, backupConfigDiscarded, t } = params
  const code = provisioningErrorCode || task.error_code
  const message = provisioningErrorMessage || task.error_message

  const title = t ? t('ops.task.failureDetails.provisionFailedTitle') : 'Provision failed'
  const summary = t ? t('ops.task.failureDetails.provisionFailedSummary', { name: task.display_name }) : `Provision "${task.display_name}" failed.`

  const reasons: string[] = []
  if (message) reasons.push(message)
  if (code) reasons.push(`[${code}]`)

  const resolutions: string[] = []
  if (code === 'AGENT_UPGRADE_REQUIRED' && t) {
    resolutions.push(t('ops.task.failureDetails.provisionUpgradeAgent'))
  }
  if (backupConfigDiscarded) {
    reasons.push(t ? t('ops.task.failureDetails.provisionConfigDiscarded') : 'Backup configuration was discarded.')
  }

  return {
    title,
    summary,
    severity: 'error',
    taskUuid: task.task_uuid,
    taskType: task.task_type,
    errorCode: code || undefined,
    reasons: reasons.length ? reasons : undefined,
    resolutions: resolutions.length ? resolutions : undefined,
    rawDetail: {
      task_uuid: task.task_uuid,
      task_type: task.task_type,
      status: task.status,
      provisioning_error_code: provisioningErrorCode,
      provisioning_error_message: provisioningErrorMessage,
      backup_config_discarded: backupConfigDiscarded,
      task_error_code: task.error_code,
      task_error_message: task.error_message,
    },
  }
}
