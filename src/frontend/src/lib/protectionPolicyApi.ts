import { getEffectiveOrgKey } from '../composables/useAuth'
import { api } from './api'
import { asList, asPagination, unwrapApiPayload } from './parse'

export type BackupPolicyScheduleMode = 'interval' | 'daily' | 'weekly' | 'monthly' | 'advanced'

export type BackupPolicySchedule = {
  enabled: boolean
  cron_expr: string
  /** Absent on legacy cron-only policies. */
  mode?: BackupPolicyScheduleMode
  /** IANA timezone. Legacy policies without one execute in UTC. */
  timezone?: string
  /** Local wall-clock activation time in the selected timezone, canonically including seconds. */
  starts_at?: string | null
  interval_unit?: 'minute' | 'hour' | 'day'
  interval_value?: number
  time?: string
  /** ISO weekdays: Monday=1 through Sunday=7. */
  weekdays?: number[]
  month_days?: number[]
  month_end?: boolean
}

/** Kopia retention flags: --keep-latest / --keep-hourly / --keep-daily / --keep-weekly / --keep-monthly / --keep-annual */
export type BackupPolicyRetention = {
  enabled: boolean
  recent_points: number
  hourly_enabled: boolean
  /** Maps to Kopia --keep-hourly (hours). Legacy hourly_days is migrated on read. */
  hourly_hours?: number
  /** @deprecated Migrated to hourly_hours (days × 24) on read. */
  hourly_days?: number
  daily_enabled: boolean
  daily_days?: number
  weekly_enabled: boolean
  weekly_weeks?: number
  monthly_enabled: boolean
  monthly_months?: number
  annual_enabled: boolean
  annual_years?: number
}

export type BackupPolicyThrottling = {
  enabled: boolean
  unlimited: boolean
  rate_mbps: number
}

export type BackupPolicyErrorHandling = {
  enabled: boolean
  ignore_directory_read_errors: boolean
  ignore_file_read_errors: boolean
  ignore_unknown_entries: boolean
}

export type BackupPolicy = {
  id: number
  name: string
  is_active: boolean
  schedule: BackupPolicySchedule
  retention: BackupPolicyRetention
  throttling: BackupPolicyThrottling
  error_handling: BackupPolicyErrorHandling
  schedule_summary: string
  retention_summary: string
  related_backup_count: number
  created_at: string
  updated_at: string
}

export type BackupPolicyWritePayload = {
  name: string
  is_active: boolean
  schedule: BackupPolicySchedule
  retention: BackupPolicyRetention
  throttling: BackupPolicyThrottling
  error_handling: BackupPolicyErrorHandling
}

export type FileFilterRule = {
  id: number
  name: string
  is_active: boolean
  ignore_patterns: string
  large_file_limit_enabled: boolean
  large_file_bytes_max: number
  ignore_cache_directories: boolean
  current_filesystem_only: boolean
  summary: string
  related_backup_count: number
  created_at: string
  updated_at: string
}

export type FileFilterRuleWritePayload = {
  name: string
  is_active: boolean
  ignore_patterns: string
  large_file_limit_enabled: boolean
  large_file_bytes_max: number
  ignore_cache_directories: boolean
  current_filesystem_only: boolean
}

export type ProtectionListParams = {
  page?: number
  page_size?: number
  search?: string
  search_field?: string
  is_active?: boolean
  ordering?: string
}

export type PagedResult<T> = {
  count: number
  results: T[]
}

export type BulkStateResult = {
  updated: number[]
  failed: Array<{ id: number; reason: string }>
}

export type BulkDeleteResult = {
  deleted: number[]
  failed: Array<{ id: number; reason: string }>
}

const policyBase = '/api/v1/protection/policies'
const filterBase = '/api/v1/protection/filters'

function orgHeaders(): Record<string, string> {
  return { 'X-Org-Key': getEffectiveOrgKey() || '' }
}

function query(params?: ProtectionListParams) {
  const qs = new URLSearchParams()
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== '') qs.set(key, String(value))
    }
  }
  return qs.toString()
}

function paged<T>(raw: unknown): PagedResult<T> {
  const data = unwrapApiPayload<Record<string, unknown>>(raw)
  const pagination = asPagination(data)
  const results = asList<T>(data)
  const count =
    typeof data.count === 'number'
      ? data.count
      : pagination?.total ?? results.length
  return { count, results }
}

export async function listBackupPolicies(params?: ProtectionListParams, init?: RequestInit) {
  const qs = query(params)
  const path = qs ? `${policyBase}/?${qs}` : `${policyBase}/`
  return paged<BackupPolicy>(await api<unknown>(path, { ...init, headers: orgHeaders() }))
}

export async function createBackupPolicy(payload: BackupPolicyWritePayload) {
  return unwrapApiPayload<BackupPolicy>(
    await api<unknown>(`${policyBase}/`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

export async function getBackupPolicy(id: number, init?: RequestInit) {
  return unwrapApiPayload<BackupPolicy>(
    await api<unknown>(`${policyBase}/${id}/`, { ...init, headers: orgHeaders() }),
  )
}

export async function updateBackupPolicy(id: number, payload: Partial<BackupPolicyWritePayload>) {
  return unwrapApiPayload<BackupPolicy>(
    await api<unknown>(`${policyBase}/${id}/`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

export async function deleteBackupPolicy(id: number) {
  return unwrapApiPayload<{ deleted: boolean; id: number }>(
    await api<unknown>(`${policyBase}/${id}/`, {
      method: 'DELETE',
      headers: orgHeaders(),
    }),
  )
}

export async function bulkSetBackupPolicyState(ids: number[], isActive: boolean) {
  return unwrapApiPayload<BulkStateResult>(
    await api<unknown>(`${policyBase}/bulk-state/`, {
      method: 'POST',
      body: JSON.stringify({ ids, is_active: isActive }),
      headers: orgHeaders(),
    }),
  )
}

export async function bulkDeleteBackupPolicies(ids: number[]) {
  return unwrapApiPayload<BulkDeleteResult>(
    await api<unknown>(`${policyBase}/bulk-delete/`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
      headers: orgHeaders(),
    }),
  )
}

export async function listFileFilterRules(params?: ProtectionListParams, init?: RequestInit) {
  const qs = query(params)
  const path = qs ? `${filterBase}/?${qs}` : `${filterBase}/`
  return paged<FileFilterRule>(await api<unknown>(path, { ...init, headers: orgHeaders() }))
}

export async function createFileFilterRule(payload: FileFilterRuleWritePayload) {
  return unwrapApiPayload<FileFilterRule>(
    await api<unknown>(`${filterBase}/`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

export async function getFileFilterRule(id: number, init?: RequestInit) {
  return unwrapApiPayload<FileFilterRule>(
    await api<unknown>(`${filterBase}/${id}/`, { ...init, headers: orgHeaders() }),
  )
}

export async function updateFileFilterRule(id: number, payload: Partial<FileFilterRuleWritePayload>) {
  return unwrapApiPayload<FileFilterRule>(
    await api<unknown>(`${filterBase}/${id}/`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

export async function deleteFileFilterRule(id: number) {
  return unwrapApiPayload<{ deleted: boolean; id: number }>(
    await api<unknown>(`${filterBase}/${id}/`, {
      method: 'DELETE',
      headers: orgHeaders(),
    }),
  )
}

export async function bulkSetFileFilterState(ids: number[], isActive: boolean) {
  return unwrapApiPayload<BulkStateResult>(
    await api<unknown>(`${filterBase}/bulk-state/`, {
      method: 'POST',
      body: JSON.stringify({ ids, is_active: isActive }),
      headers: orgHeaders(),
    }),
  )
}

export async function bulkDeleteFileFilters(ids: number[]) {
  return unwrapApiPayload<BulkDeleteResult>(
    await api<unknown>(`${filterBase}/bulk-delete/`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
      headers: orgHeaders(),
    }),
  )
}
