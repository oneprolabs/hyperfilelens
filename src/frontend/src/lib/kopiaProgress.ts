export type KopiaProgressAggregate = {
  progress_schema_version?: number
  percent: number | null
  bytes_done: number
  processed_bytes?: number
  bytes_total: number | null
  bytes_total_known: boolean
  bytes_total_reference?: boolean
  bytes_total_estimated?: boolean
  speed_bps: number | null
  processing_speed_bps?: number | null
  hash_speed_bps?: number | null
  upload_speed_bps?: number | null
  eta_seconds: number | null
  lanes_done: number
  lanes_total: number
  slowest_lane?: { id?: string; name?: string; eta_seconds?: number } | null
}

export type KopiaProgressLane = {
  id?: string
  name?: string
  status?: string
  kopia_phase?: string
  progress_schema_version?: number
  bytes_done?: number
  processed_bytes?: number
  bytes_total?: number | null
  bytes_total_known?: boolean
  bytes_total_reference?: boolean
  percent?: number | null
  speed_bps?: number | null
  processing_speed_bps?: number | null
  hash_speed_bps?: number | null
  upload_speed_bps?: number | null
  eta_seconds?: number | null
  progress_text?: string
  path_index?: number | null
  path_total?: number | null
  orchestration_label?: string
}

export type TransferProgress = {
  phase?: string
  label_key?: string | null
  label_args?: Record<string, string | number> | null
  label?: string
  execution_state?: string | null
  progress_schema_version?: number
  bytes_done?: number
  processed_bytes?: number
  bytes_total?: number | null
  bytes_total_known?: boolean
  bytes_total_reference?: boolean
  bytes_total_estimated?: boolean
  bytes_total_source?: 'kopia' | 'du_reference' | 'unknown'
  bytes_total_adjusted?: boolean
  uploaded_bytes?: number
  uploaded_count?: number
  hashed_count?: number
  estimated_bytes?: number
  processed_count?: number
  total_count?: number
  restored_file_count?: number
  restored_directory_count?: number
  restored_symlink_count?: number
  path_index?: number | null
  path_total?: number | null
  du_total?: number
  switch_latched?: boolean
  kopia_total_locked?: number
  speed_bps?: number | null
  processing_speed_bps?: number | null
  hash_speed_bps?: number | null
  upload_speed_bps?: number | null
  speed_source?: string | null
  processing_speed_source?: string | null
  upload_speed_source?: string | null
  display_percent?: number | null
  step3_display_percent?: number | null
  eta_seconds?: number | null
  eta_source?: string | null
  show_metrics?: boolean
  lanes_done?: number
  lanes_total?: number
  estimating_started_at?: string
}

export type TaskRuntimePayload = {
  progress?: number
  transfer_progress?: TransferProgress | null
  kopia_progress?: KopiaProgressPayload
}

export type KopiaProgressPayload = {
  orchestration_label?: string
  orchestration_label_key?: string
  orchestration_label_args?: Record<string, string | number>
  orchestration_phase?: string
  display_percent?: number | null
  percent_source?: 'placeholder' | 'kopia'
  show_metrics?: boolean
  aggregate?: KopiaProgressAggregate
  lanes?: KopiaProgressLane[]
}

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB']

type TranslateFn = (key: string, args?: Record<string, unknown>) => string

export function parseTaskProgressValue(value: number | string | null | undefined): number {
  const n = Number(value ?? 0)
  if (!Number.isFinite(n)) return 0
  return Math.max(0, Math.min(100, n))
}

export function formatTaskProgressPercent(value: number | string | null | undefined): string {
  return `${parseTaskProgressValue(value).toFixed(2)}%`
}

export function formatTaskProgressBarPercent(value: number | string | null | undefined): number {
  return Math.round(parseTaskProgressValue(value) * 100) / 100
}

export function formatCount(value: number | null | undefined): string {
  const n = Number(value || 0)
  if (!Number.isFinite(n) || n < 0) return '0'
  return n.toLocaleString()
}

export function resolveStep3DisplayPercent(
  transfer?: TransferProgress | null,
  fallback?: number | string | null,
): number {
  const step3 = Number(transfer?.step3_display_percent)
  if (Number.isFinite(step3)) return parseTaskProgressValue(step3)
  return parseTaskProgressValue(fallback)
}

export function shouldShowStep3Percent(transfer?: TransferProgress | null): boolean {
  if (!transfer) return false
  const phase = String(transfer.phase || '').toLowerCase()
  if (!['transferring', 'finalizing', 'done'].includes(phase)) return false
  return Boolean(transfer.bytes_total_known && (transfer.bytes_total || 0) > 0)
}

export function formatBytes(value: number | null | undefined): string {
  const bytes = Number(value || 0)
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  let unit = 0
  let scaled = bytes
  while (scaled >= 1024 && unit < BYTE_UNITS.length - 1) {
    scaled /= 1024
    unit += 1
  }
  const digits = scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2
  return `${scaled.toFixed(digits)} ${BYTE_UNITS[unit]}`
}

export function formatSpeedBps(value: number | null | undefined): string | null {
  if (value == null) return null
  const bps = Number(value)
  if (!Number.isFinite(bps) || bps < 0) return null
  return `${formatBytes(bps)}/s`
}

export function transferEtaText(t: TranslateFn, value: number | null | undefined): string | null {
  const seconds = Number(value || 0)
  if (!Number.isFinite(seconds) || seconds <= 0) return null
  if (seconds < 60) return t('protection.taskProgress.etaSeconds', { n: seconds })
  if (seconds < 3600) return t('protection.taskProgress.etaMinutes', { n: Math.ceil(seconds / 60) })
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.ceil((seconds % 3600) / 60)
  return minutes > 0
    ? t('protection.taskProgress.etaHoursMinutes', { h: hours, m: minutes })
    : t('protection.taskProgress.etaHours', { n: hours })
}

export function transferCapacityText(t: TranslateFn, transfer?: TransferProgress | null): string | null {
  if (!transfer) return null
  const isRestore = String(transfer.label_key || '').includes('taskProgress.restore.')
  const schemaV2 = Number(transfer.progress_schema_version || 1) >= 2
  const processedBytes = schemaV2
    ? Number(transfer.processed_bytes ?? transfer.bytes_done ?? 0)
    : Number(transfer.bytes_done || 0)
  const done = formatBytes(processedBytes)
  if (transfer.bytes_total_known && transfer.bytes_total != null) {
    const total = formatBytes(transfer.bytes_total)
    if (isRestore) {
      return t('protection.taskProgress.restoreBytesCapacity', { done, total })
    }
    if (schemaV2) {
      return t('protection.taskProgress.bytesProcessedCapacity', { done, total })
    }
    if (transfer.bytes_total_reference) {
      return t('protection.taskProgress.bytesCapacityRef', { done, total })
    }
    if (transfer.bytes_total_estimated || (transfer.estimated_bytes || 0) > 0) {
      return t('protection.taskProgress.bytesCapacityEst', { done, total })
    }
    return t('protection.taskProgress.bytesCapacity', { done, total })
  }
  if (schemaV2 && processedBytes > 0) {
    return t('protection.taskProgress.bytesProcessed', { size: done })
  }
  if (processedBytes > 0) {
    return t('protection.taskProgress.bytesTransferred', { size: done })
  }
  return null
}

export function transferProgressLabel(t: TranslateFn, transfer?: TransferProgress | null): string {
  if (!transfer) return ''
  const executionState = String(transfer.execution_state || '').trim().toLowerCase()
  if (executionState === 'reconnecting') {
    return t('protection.taskProgress.execution.reconnecting')
  }
  if (executionState === 'offline_pending') {
    return t('protection.taskProgress.execution.offlinePending')
  }
  if (executionState === 'offline_stale') {
    return t('protection.taskProgress.execution.offlineStale')
  }
  const key = String(transfer.label_key || '').trim()
  if (key) {
    const rawArgs = transfer.label_args && typeof transfer.label_args === 'object'
      ? transfer.label_args as Record<string, unknown>
      : {}
    const args: Record<string, unknown> = { ...rawArgs }
    if (key.includes('taskProgress.transfer.')) {
      if (args.hashed != null) args.hashed = formatCount(Number(args.hashed))
      if (args.uploaded != null) args.uploaded = formatCount(Number(args.uploaded))
    }
    return t(key, args)
  }
  return String(transfer.label || '').trim()
}

export function shouldShowTransferMetrics(transfer?: TransferProgress | null): boolean {
  if (!transfer) return false
  const executionState = String(transfer.execution_state || '').trim().toLowerCase()
  if (executionState === 'reconnecting' || executionState === 'offline_pending') {
    return false
  }
  if (transfer.show_metrics === true) return true
  const phase = String(transfer.phase || '').toLowerCase()
  if (phase === 'transferring' || phase === 'done') {
    return Boolean(
      (transfer.bytes_done || 0) > 0
      || transfer.speed_bps != null
      || transfer.hash_speed_bps != null
      || transfer.upload_speed_bps != null
      || transfer.eta_seconds
      || (transfer.bytes_total_known && transfer.bytes_total),
    )
  }
  return Boolean(
    transfer.bytes_total_known
    && transfer.bytes_total_reference
    && (transfer.bytes_total || 0) > 0,
  )
}

type TransferMetricOptions = {
  allowUnclassifiedSpeed?: boolean
  labelProcessingSpeed?: boolean
  labelRestoreMetrics?: boolean
}

export function transferSpeedParts(
  t: TranslateFn,
  transfer?: TransferProgress | null,
  options: TransferMetricOptions = {},
): string[] {
  if (!transfer) return []
  const isRestore = String(transfer.label_key || '').includes('taskProgress.restore.')
  const processingSpeed = formatSpeedBps(transfer.processing_speed_bps)
  if (processingSpeed && !isRestore) {
    return options.labelProcessingSpeed
      ? [t('protection.taskProgress.processingSpeed', { speed: processingSpeed })]
      : [processingSpeed]
  }
  const uploadSpeed = formatSpeedBps(transfer.upload_speed_bps)
  if (uploadSpeed) {
    return isRestore
      ? [t('protection.taskProgress.restoreSpeed', { speed: uploadSpeed })]
      : []
  }
  if (Number(transfer.progress_schema_version || 1) >= 2) return []
  const hashSpeed = formatSpeedBps(transfer.hash_speed_bps)
  if (hashSpeed) return [t('protection.taskProgress.hashSpeed', { speed: hashSpeed })]
  const unclassifiedSpeed = options.allowUnclassifiedSpeed
    ? formatSpeedBps(transfer.speed_bps)
    : ''
  if (unclassifiedSpeed) return [unclassifiedSpeed]
  return []
}

export function transferMetricParts(
  t: TranslateFn,
  transfer?: TransferProgress | null,
  options: TransferMetricOptions = {},
): string[] {
  if (!transfer) return []
  const parts: string[] = []
  const capacity = transferCapacityText(t, transfer)
  if (capacity) parts.push(capacity)
  parts.push(...transferSpeedParts(t, transfer, options))
  const phase = String(transfer.phase || '').toLowerCase()
  const eta = phase === 'finalizing' || !transfer.bytes_total_known
    ? null
    : options.labelRestoreMetrics
      ? restoreTransferEtaText(t, transfer.eta_seconds)
      : transferEtaText(t, transfer.eta_seconds)
  if (eta) parts.push(eta)
  return parts
}

function restoreTransferEtaText(t: TranslateFn, value: number | null | undefined): string | null {
  const seconds = Number(value || 0)
  if (!Number.isFinite(seconds) || seconds <= 0) return null
  if (seconds < 60) return t('protection.taskProgress.restoreEtaSeconds', { n: seconds })
  if (seconds < 3600) return t('protection.taskProgress.restoreEtaMinutes', { n: Math.ceil(seconds / 60) })
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.ceil((seconds % 3600) / 60)
  return minutes > 0
    ? t('protection.taskProgress.restoreEtaHoursMinutes', { h: hours, m: minutes })
    : t('protection.taskProgress.restoreEtaHours', { n: hours })
}

export function isTransferProgress(value: unknown): value is TransferProgress {
  if (!value || typeof value !== 'object') return false
  const row = value as Record<string, unknown>
  return Boolean(row.label_key || row.label || row.phase)
}

export function isKopiaProgressPayload(value: unknown): value is KopiaProgressPayload {
  return Boolean(value && typeof value === 'object' && 'aggregate' in (value as Record<string, unknown>))
}

export function progressPercent(aggregate?: KopiaProgressAggregate | null): number {
  const percent = Number(aggregate?.percent)
  if (Number.isFinite(percent)) return Math.max(0, Math.min(100, Math.round(percent)))
  return 0
}

export function capacityLabel(aggregate?: KopiaProgressAggregate | null): string | null {
  if (!aggregate) return null
  const done = formatBytes(aggregate.bytes_done)
  if (aggregate.bytes_total_known && aggregate.bytes_total != null) {
    const suffix = aggregate.bytes_total_reference ? ' (ref.)' : ''
    return `${done} / ${formatBytes(aggregate.bytes_total)}${suffix}`
  }
  if (aggregate.bytes_done > 0) return `${done} transferred`
  return null
}

export function resolveDisplayPercent(payload?: KopiaProgressPayload | null): number {
  if (!payload) return 0
  const phase = String(payload.orchestration_phase || '').toLowerCase()
  if (phase === 'done') return 100
  const display = Number(payload.display_percent)
  if (Number.isFinite(display)) return Math.max(0, Math.min(100, display))
  return progressPercent(payload.aggregate)
}

export function shouldShowMetrics(payload?: KopiaProgressPayload | null): boolean {
  if (!payload) return false
  if (payload.show_metrics === true) return true
  const phase = String(payload.orchestration_phase || '').toLowerCase()
  if (phase === 'transferring' || phase === 'done') return true
  const aggregate = payload.aggregate
  return Boolean(
    aggregate?.bytes_total_known
    && aggregate.bytes_total_reference
    && (aggregate.bytes_total || 0) > 0,
  )
}
