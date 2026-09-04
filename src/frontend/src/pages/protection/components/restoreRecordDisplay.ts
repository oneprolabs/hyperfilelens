import type { RestoreRecord, RestoreRecordItem } from '../../../lib/restoreApi'
import {
  formatBytes,
  formatCount,
  transferMetricParts,
  type TaskRuntimePayload,
  type TransferProgress,
} from '../../../lib/kopiaProgress'

type TranslateFn = (key: string, args?: Record<string, unknown>) => string

export type RestoreRecordPathMapping = {
  key: string
  item: RestoreRecordItem
  sourcePath: string
  sourceKind: 'file' | 'dir'
}

export type RestoreRecordTimeValueKind = 'value' | 'not_started' | 'not_finished' | 'unavailable'
export type RestoreRecordDurationKind = 'running' | 'fixed' | 'not_applicable' | 'unavailable'

export type RestoreRecordTimeState = {
  status: string
  startedKind: RestoreRecordTimeValueKind
  finishedKind: RestoreRecordTimeValueKind
  durationKind: RestoreRecordDurationKind
  startedAt: string | null
  finishedAt: string | null
  hasStatusTimeConflict: boolean
  hasInvalidTimeData: boolean
}

export type RestoreRecordItemDetail = {
  code: string
  message: string
  remediation: string
}

const RESTORE_RECORD_ACTIVE_STATUSES = ['pending', 'waiting', 'blocked', 'running']
const RESTORE_RECORD_PRESTART_STATUSES = ['pending', 'waiting', 'blocked']
const RESTORE_RECORD_PRESTART_TERMINAL_STATUSES = ['failed', 'cancelled', 'timeout']

export function restoreRecordTaskStatus(record: RestoreRecord) {
  return String(record.task_summary?.status || '').trim().toLowerCase()
}

export function normalizedRestoreRecordTaskStatus(record: RestoreRecord) {
  const status = restoreRecordTaskStatus(record)
  if (status === 'completed' || status === 'succeeded') return 'success'
  if (status === 'canceled') return 'cancelled'
  if (status === 'queued') return 'pending'
  if (status === 'in_progress') return 'running'
  return status
}

export function isRestoreRecordActive(record: RestoreRecord) {
  return RESTORE_RECORD_ACTIVE_STATUSES.includes(normalizedRestoreRecordTaskStatus(record))
}

function restoreRecordTimestamp(raw?: string | null) {
  if (!raw) return null
  const value = new Date(raw).getTime()
  return Number.isFinite(value) ? value : null
}

export function restoreRecordTimeState(record: RestoreRecord): RestoreRecordTimeState {
  const task = record.task_summary
  const status = normalizedRestoreRecordTaskStatus(record)
  const startedAt = task?.started_at || null
  const finishedAt = task?.finished_at || null
  const startedMs = restoreRecordTimestamp(startedAt)
  const finishedMs = restoreRecordTimestamp(finishedAt)
  const hasInvalidTimeData = Boolean(
    (startedAt && startedMs === null)
    || (finishedAt && finishedMs === null)
    || (startedMs !== null && finishedMs !== null && finishedMs < startedMs),
  )
  const hasStatusTimeConflict = Boolean(
    (RESTORE_RECORD_PRESTART_STATUSES.includes(status) && startedMs !== null)
    || (status === 'running' && finishedMs !== null),
  )

  if (!task) {
    return {
      status,
      startedKind: 'unavailable',
      finishedKind: 'unavailable',
      durationKind: 'unavailable',
      startedAt,
      finishedAt,
      hasStatusTimeConflict,
      hasInvalidTimeData,
    }
  }

  const startedKind: RestoreRecordTimeValueKind = startedAt && startedMs === null
    ? 'unavailable'
    : startedMs !== null
      ? 'value'
      : RESTORE_RECORD_PRESTART_STATUSES.includes(status)
        || RESTORE_RECORD_PRESTART_TERMINAL_STATUSES.includes(status)
        ? 'not_started'
        : 'unavailable'
  const finishedKind: RestoreRecordTimeValueKind = finishedAt && finishedMs === null
    ? 'unavailable'
    : finishedMs !== null
      ? 'value'
      : RESTORE_RECORD_ACTIVE_STATUSES.includes(status)
        ? 'not_finished'
        : 'unavailable'

  let durationKind: RestoreRecordDurationKind
  if (hasStatusTimeConflict || hasInvalidTimeData) {
    durationKind = 'unavailable'
  } else if (RESTORE_RECORD_PRESTART_STATUSES.includes(status)) {
    durationKind = 'not_applicable'
  } else if (startedMs === null) {
    durationKind = RESTORE_RECORD_PRESTART_TERMINAL_STATUSES.includes(status)
      ? 'not_applicable'
      : 'unavailable'
  } else if (status === 'running') {
    durationKind = 'running'
  } else if (finishedMs !== null) {
    durationKind = 'fixed'
  } else {
    durationKind = 'unavailable'
  }

  return {
    status,
    startedKind,
    finishedKind,
    durationKind,
    startedAt,
    finishedAt,
    hasStatusTimeConflict,
    hasInvalidTimeData,
  }
}

export function shouldShowRestoreRecordProgress(record: RestoreRecord) {
  return normalizedRestoreRecordTaskStatus(record) === 'running'
}

export function restoreRecordSnapshotLabel(record: RestoreRecord) {
  return String(record.source_snapshot_uid || '').trim() || `#${record.source_snapshot_id}`
}

export function restoreRecordTargetDisplayPath(record: RestoreRecord, item?: RestoreRecordItem) {
  return item?.target_display_path
    || record.target_display_path
    || item?.target_path
    || record.target_path
    || '—'
}

export function joinRestoreRecordSourcePath(basePath: string, selectedPath: string) {
  const base = String(basePath || '').trim()
  const selected = String(selectedPath || '').trim()
  if (!selected) return base || '—'
  if (/^(?:[A-Za-z]:[\\/]|[\\/]{1,2})/.test(selected)) return selected
  if (!base) return selected
  const separator = base.includes('\\') ? '\\' : '/'
  const normalizedBase = base.replace(/[\\/]+$/, '')
  const normalizedSelected = selected.replace(/^[\\/]+/, '')
  return `${normalizedBase}${separator}${normalizedSelected}`
}

function inferredRestoreRecordPathKind(path: string): 'file' | 'dir' {
  const base = path.split(/[\\/]/).filter(Boolean).pop() || ''
  return /\.[A-Za-z0-9]{1,16}$/.test(base) ? 'file' : 'dir'
}

function restoreRecordItemPathKind(record: RestoreRecord, item: RestoreRecordItem): 'file' | 'dir' | null {
  const expandedItems = Array.isArray(record.expanded_payload?.items)
    ? record.expanded_payload.items
    : []
  const expandedItem = expandedItems.find((value) => {
    if (!value || typeof value !== 'object') return false
    const candidate = value as Record<string, unknown>
    return Number(candidate.source_snapshot_directory_id) === item.source_snapshot_directory_id
      || Number(candidate.backup_config_dir_id) === item.backup_config_dir_id
  }) as Record<string, unknown> | undefined
  const pathType = String(expandedItem?.source_path_type || '').trim().toLowerCase()
  if (pathType === 'file') return 'file'
  if (pathType === 'directory') return 'dir'
  return null
}

export function restoreRecordItemSourceKind(record: RestoreRecord, item: RestoreRecordItem) {
  return restoreRecordItemPathKind(record, item) || inferredRestoreRecordPathKind(item.source_path || '')
}

export function restoreRecordPathMappings(record: RestoreRecord): RestoreRecordPathMapping[] {
  return record.items.flatMap((item) => {
    const selectedPaths = Array.isArray(item.selected_paths)
      ? item.selected_paths.map((path) => String(path || '').trim()).filter(Boolean)
      : []
    const paths = selectedPaths.length
      ? selectedPaths.map((path) => joinRestoreRecordSourcePath(item.source_path, path))
      : [item.source_path || '—']
    return paths.map((sourcePath, index) => ({
      key: `${item.id}:${index}:${sourcePath}`,
      item,
      sourcePath,
      sourceKind: selectedPaths.length
        ? inferredRestoreRecordPathKind(sourcePath)
        : restoreRecordItemSourceKind(record, item),
    }))
  })
}

function countRecordItems(record: RestoreRecord, status: string) {
  return (record.items || []).filter(item => String(item.status || '').toLowerCase() === status).length
}

export function restoreRecordOutcomeMetricParts(t: TranslateFn, record: RestoreRecord): string[] {
  const counts = {
    restored: Number(record.restored_item_count ?? countRecordItems(record, 'success')),
    skipped: Number(record.skipped_item_count ?? countRecordItems(record, 'skipped')),
    failed: Number(record.failed_item_count ?? countRecordItems(record, 'failed')),
    cancelled: Number(record.cancelled_item_count ?? countRecordItems(record, 'cancelled')),
  }
  if (!Object.values(counts).some(value => Number.isFinite(value) && value > 0)) return []
  return [t('protection.backupsPage.flowRestoreRecordOutcomeCounts', {
    restored: formatCount(counts.restored),
    skipped: formatCount(counts.skipped),
    failed: formatCount(counts.failed),
    cancelled: formatCount(counts.cancelled),
  })]
}

export function restoreRecordItemDetail(t: TranslateFn, item: RestoreRecordItem): RestoreRecordItemDetail | null {
  const status = String(item.status || '').trim().toLowerCase()
  const result = item.result_payload && typeof item.result_payload === 'object'
    ? item.result_payload
    : {}
  if (status === 'skipped') {
    const reason = String(result.skip_reason || '').trim().toLowerCase()
    return {
      code: '',
      message: reason === 'target_exists'
        ? t('protection.backupsPage.flowRestoreRecordTargetExistsSkipped')
        : t('protection.backupsPage.flowRestoreRecordItemSkipped'),
      remediation: '',
    }
  }
  const code = String(item.error_code || result.error_code || '').trim()
  const permissionDenied = code === 'RESTORE_TARGET_PERMISSION_DENIED'
  const message = permissionDenied
    ? t('protection.backupsPage.flowRestoreRecordPermissionDenied')
    : String(item.error_message || result.error_message || '').trim()
  const remediation = permissionDenied
    ? t('protection.backupsPage.flowRestoreRecordPermissionRemediation')
    : String(result.error_remediation || '').trim()
  if (!code && !message && !remediation) return null
  return { code, message, remediation }
}

export function restoreRecordRemediationItems(remediation: string): string[] {
  return String(remediation || '')
    .split('\n')
    .map(item => item.replace(/^\d+\.\s*/, '').trim())
    .filter(Boolean)
}

function positiveCount(value: number | null | undefined) {
  const count = Number(value || 0)
  return Number.isFinite(count) && count > 0 ? count : 0
}

export function restoreRecordRuntimeMetricParts(
  t: TranslateFn,
  runtime?: TaskRuntimePayload | null,
  taskStatus = '',
): string[] {
  const transfer = runtime?.transfer_progress
  if (!transfer) return []

  const parts: string[] = []
  const processed = positiveCount(transfer.processed_count)
  const total = positiveCount(transfer.total_count)
  const status = String(taskStatus).trim().toLowerCase()
  const completed = ['success', 'succeeded', 'completed'].includes(status)
  if (completed) {
    const restoredFiles = positiveCount(transfer.restored_file_count)
    const restoredDirectories = positiveCount(transfer.restored_directory_count)
    const restoredSymlinks = positiveCount(transfer.restored_symlink_count)
    const hasRestoredCounts = [
      transfer.restored_file_count,
      transfer.restored_directory_count,
      transfer.restored_symlink_count,
    ].some((value) => value != null && Number.isFinite(Number(value)))
    if (hasRestoredCounts) {
      parts.push(t('protection.backupsPage.flowRestoreRecordRestoredObjects', {
        files: formatCount(restoredFiles),
        directories: formatCount(restoredDirectories),
        symlinks: formatCount(restoredSymlinks),
      }))
    }
    if (processed > 0) {
      if (!hasRestoredCounts) {
        parts.push(total > 0
          ? t('protection.backupsPage.flowRestoreRecordItemsProgress', {
              done: formatCount(processed),
              total: formatCount(total),
            })
          : t('protection.backupsPage.flowRestoreRecordItemsProcessed', {
              n: formatCount(processed),
            }))
      }
    }
    const processedBytes = Number(transfer.bytes_done ?? transfer.processed_bytes ?? 0)
    if (Number.isFinite(processedBytes) && processedBytes > 0) {
      parts.push(t('protection.backupsPage.flowRestoreRecordRestoredCapacity', {
        size: formatBytes(processedBytes),
      }))
    }
    return parts
  }
  if (total > 0) {
    parts.push(t('protection.backupsPage.flowRestoreRecordItemsProgress', {
      done: formatCount(processed),
      total: formatCount(total),
    }))
  } else if (processed > 0) {
    parts.push(t('protection.backupsPage.flowRestoreRecordItemsProcessed', {
      n: formatCount(processed),
    }))
  }
  parts.push(...transferMetricParts(t, transfer as TransferProgress, {
    allowUnclassifiedSpeed: true,
    labelRestoreMetrics: true,
  }))
  return parts
}
