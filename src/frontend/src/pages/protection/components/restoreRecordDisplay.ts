import type { RestoreRecord, RestoreRecordItem } from '../../../lib/restoreApi'
import {
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

export function restoreRecordTaskStatus(record: RestoreRecord) {
  return String(record.task_summary?.status || '').trim().toLowerCase()
}

export function shouldShowRestoreRecordProgress(record: RestoreRecord) {
  return restoreRecordTaskStatus(record) === 'running'
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

function restoreRecordPathKind(path: string): 'file' | 'dir' {
  const base = path.split(/[\\/]/).filter(Boolean).pop() || ''
  return /\.[A-Za-z0-9]{1,16}$/.test(base) ? 'file' : 'dir'
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
      sourceKind: restoreRecordPathKind(sourcePath),
    }))
  })
}

function positiveCount(value: number | null | undefined) {
  const count = Number(value || 0)
  return Number.isFinite(count) && count > 0 ? count : 0
}

export function restoreRecordRuntimeMetricParts(
  t: TranslateFn,
  runtime?: TaskRuntimePayload | null,
): string[] {
  const transfer = runtime?.transfer_progress
  if (!transfer) return []

  const parts: string[] = []
  const processed = positiveCount(transfer.processed_count)
  const total = positiveCount(transfer.total_count)
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
  }))
  return parts
}
