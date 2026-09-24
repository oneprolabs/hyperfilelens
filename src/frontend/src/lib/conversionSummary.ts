/** Helpers for SourceLens document conversion summary (Phase A UI). */

export type DocumentConversionItem = {
  name: string
  path?: string
  reason: string
  reason_label: string
  is_problem?: boolean
}

export type DocumentConversionWarning = {
  code: string
  label: string
}

export type DocumentConversionCounts = {
  total: number
  candidates: number
  success: number
  failed: number
  skipped: number
  unsupported: number
  unchanged: number
}

export type DocumentConversionPhase = 'pending' | 'running' | 'succeeded' | 'failed' | string

export type DocumentConversionRecovery = {
  resumable?: boolean
  resumed?: boolean
  resume_source?: 'lensnode_executor' | 'checkpoint' | string | null
  restart_required?: boolean
  reason?: string
  original_task_id?: string
  task_id?: string
}

export type DocumentConversion = {
  status: string
  phase?: DocumentConversionPhase
  all_ok?: boolean
  /** Finished with nothing to convert (no candidates / no usable docs). */
  empty_result?: boolean
  progress_step?: string
  progress_message?: string
  progress_percent?: number | null
  error?: string
  recovery?: DocumentConversionRecovery | null
  finished_at?: string
  counts: DocumentConversionCounts
  items: DocumentConversionItem[]
  problem_items?: DocumentConversionItem[]
  warnings: DocumentConversionWarning[]
  usable: boolean
  format_matrix?: {
    recommended: string[]
    also_supported: string[]
    unsupported_mvp: string[]
  }
}

export type SessionDataContext = {
  origin: string
  origin_label: string
  backup_config_id: number | null
  backup_source_snapshot_id: number | null
  snapshot_created_at: string | null
  processing_location: 'private_gateway' | 'public_gateway' | string
  processing_location_label: string
  gateway_name: string
  restore_path: string
  backup_detail_path: string
}

const PROBLEM_REASONS = new Set([
  'PASSWORD_PROTECTED',
  'NO_EXTRACTABLE_TEXT',
  'FILE_TOO_LARGE',
  'PAGE_LIMIT_EXCEEDED',
  'UNSUPPORTED_TYPE',
  'CONVERSION_FAILED',
  'CORRUPT',
])

const OK_REASONS = new Set(['', 'UNCHANGED', 'SUCCESS', 'OK'])

export function conversionCountsLabel(conversion: DocumentConversion | null | undefined): string {
  if (!conversion) return ''
  const { success, failed, unsupported, unchanged, candidates, total } = conversion.counts
  const ready = success + unchanged
  const base = total || candidates
  if (!base && !ready && !failed && !unsupported) return ''
  const parts = [`${ready} ready`]
  if (failed > 0) parts.push(`${failed} failed`)
  if (unsupported > 0) parts.push(`${unsupported} unsupported`)
  return parts.join(' · ')
}

export function conversionPhase(conversion: DocumentConversion | null | undefined): DocumentConversionPhase {
  if (!conversion) return 'pending'
  if (conversion.phase) return conversion.phase
  const status = (conversion.status || '').toUpperCase()
  if (status === 'SUCCESS') return 'succeeded'
  if (status === 'FAILURE' || status === 'REVOKED') return 'failed'
  if (status) return 'running'
  return 'pending'
}

/** True only for a finished conversion with usable docs and no failures. */
export function conversionAllOk(conversion: DocumentConversion | null | undefined): boolean {
  if (!conversion) return false
  if (typeof conversion.all_ok === 'boolean') return conversion.all_ok
  const phase = conversionPhase(conversion)
  if (phase !== 'succeeded' || !conversion.usable) return false
  const { failed, unsupported } = conversion.counts
  return failed === 0 && unsupported === 0 && !conversion.error && conversionProblemItems(conversion).length === 0
}

/** Finished successfully but nothing was convertible. */
export function conversionEmptyResult(conversion: DocumentConversion | null | undefined): boolean {
  if (!conversion) return false
  if (typeof conversion.empty_result === 'boolean') return conversion.empty_result
  if (conversionAllOk(conversion)) return false
  const phase = conversionPhase(conversion)
  if (phase !== 'succeeded' || conversion.usable || conversion.error) return false
  const { failed, unsupported, total, candidates } = conversion.counts
  return failed === 0 && unsupported === 0 && total === 0 && candidates === 0 && conversionProblemItems(conversion).length === 0
}

/** Per-file rows that should appear in the user-facing problem list. */
export function conversionProblemItems(
  conversion: DocumentConversion | null | undefined,
): DocumentConversionItem[] {
  if (!conversion) return []
  if (Array.isArray(conversion.problem_items)) return conversion.problem_items
  return conversion.items.filter((item) => {
    if (typeof item.is_problem === 'boolean') return item.is_problem
    const reason = item.reason || ''
    if (PROBLEM_REASONS.has(reason)) return true
    if (OK_REASONS.has(reason)) return false
    return Boolean(reason)
  })
}

/**
 * Warnings for UI. Drop CONVERSION_PARTIAL_FAILED when per-file problems are
 * already listed, to avoid repeating the same message.
 */
export function conversionWarningsForDisplay(
  conversion: DocumentConversion | null | undefined,
  limit = 8,
): DocumentConversionWarning[] {
  if (!conversion) return []
  const hasProblems = conversionProblemItems(conversion).length > 0
  return (conversion.warnings || [])
    .filter((row) => row.label || row.code)
    .filter((row) => !(hasProblems && row.code === 'CONVERSION_PARTIAL_FAILED'))
    .slice(0, limit)
}
