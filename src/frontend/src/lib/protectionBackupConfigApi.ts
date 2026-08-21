import { api } from './api'
import { logger } from './logger'
import { getCorrelationHeaders } from './requestContext'
import { asList, asPagination, unwrapApiPayload } from './parse'

export type CompressionLevel = 'none' | 'balanced' | 'high'

export class InvalidCompressionLevelError extends Error {
  readonly value: unknown

  constructor(value: unknown) {
    super(`Invalid backup compression level: ${String(value)}`)
    this.name = 'InvalidCompressionLevelError'
    this.value = value
  }
}

export function parseCompressionLevel(value: unknown): CompressionLevel {
  if (value === 'none' || value === 'balanced' || value === 'high') return value
  throw new InvalidCompressionLevelError(value)
}

export function isInvalidCompressionLevelError(error: unknown): error is InvalidCompressionLevelError {
  return error instanceof InvalidCompressionLevelError
}

export type BackupConfigDirectoryPayload = {
  path: string
  path_type?: 'directory' | 'file' | 'unknown'
  estimated_size_bytes?: number
}

export type BackupConfigRecoveryPlanPayload = {
  scope?: 'snapshot' | 'paths'
  source_path: string
  backup_config_dir_id?: number | null
  target_type?: 'agent' | 'nas'
  target_ref_id?: number | null
  restore_host_id?: number | null
  restore_dir: string
  conflict_mode: 'skip' | 'overwrite'
}

export type BackupConfigCreatePayload = {
  name: string
  remark?: string
  source_type: 'agent' | 'nas'
  source_ref_id: number
  repository_id: number
  repository_endpoint_type?: 'external' | 'internal'
  backup_policy_id?: number | null
  file_filter_rule_id?: number | null
  compression_level?: CompressionLevel
  directories: BackupConfigDirectoryPayload[]
  recovery_plan_enabled?: boolean
  recovery_plans?: BackupConfigRecoveryPlanPayload[]
}

export type BackupConfigUpdatePayload = Partial<BackupConfigCreatePayload>

export type BackupConfigDirectory = {
  id: number
  path: string
  path_type?: 'directory' | 'file' | 'unknown'
  display_name: string
  estimated_size_bytes: number
  sort_order: number
}

export type BackupConfigRecoveryPlan = {
  id: number
  backup_config_id?: number
  scope: 'snapshot' | 'paths'
  source_type: string
  source_ref_id: number
  backup_config_dir_id: number | null
  source_path: string
  target_type?: 'agent' | 'nas'
  target_ref_id?: number | null
  restore_host_id: number | null
  restore_dir: string
  conflict_mode: string
  enabled?: boolean
  sort_order: number
}

export type BackupConfig = {
  id: number
  name: string
  source_type: string
  source_ref_id: number
  repository_id: number
  repository_endpoint_type: 'external' | 'internal'
  backup_policy_id: number | null
  file_filter_rule_id: number | null
  directory_count: number
  compression_level: CompressionLevel
  status?: 'provisioning' | 'active' | 'provision_failed' | 'resetting' | 'reset_failed' | string
  reset_task_uuid?: string | null
  provisioning_task_uuid?: string | null
  provisioning_error_code?: string
  provisioning_error_message?: string
  recovery_plan_enabled: boolean
  created_at: string
  updated_at: string
}

export type BackupConfigDetail = BackupConfig & {
  remark: string
  directories: BackupConfigDirectory[]
  recovery_plans: BackupConfigRecoveryPlan[]
}

export type BackupConfigListParams = {
  page?: number
  page_size?: number
  search?: string
  source_type?: string
  repository_id?: number
  ordering?: string
}

export type BackupSourceSnapshotDirectory = {
  id: number
  backup_config_dir_id: number
  source_path: string
  path_type?: 'directory' | 'file' | 'unknown'
  display_name: string
  repository_id: number
  kopia_snapshot_id?: string | null
  status: string
  node_task_id?: string | null
  retry_count?: number
  dispatched_at?: string | null
  last_substantive_progress_at?: string | null
  last_progress_snapshot?: Record<string, unknown>
  adopted_late_result?: boolean
  cancel_requested_at?: string | null
  created_at: string
  size_bytes: number
  recoverable_size_bytes?: number
  new_original_content_bytes?: number | null
  new_packed_content_bytes?: number | null
  storage_stats_available?: boolean
  file_count: number
  dir_count: number
  stats?: Record<string, unknown>
  error_code?: string
  error_message?: string
}

export type BackupSourceSnapshot = {
  id: number
  snapshot_uid: string
  source_type: string
  source_ref_id: number
  source_display_name: string
  backup_config_id: number
  backup_config_name: string
  repository_id: number
  repository_display_name: string
  repository_endpoint_type: 'external' | 'internal'
  repository_endpoint: string
  task_id: number
  task_uuid: string
  trigger_type: string
  status: string
  started_at?: string | null
  finished_at?: string | null
  created_at: string
  directory_count: number
  successful_directory_count: number
  failed_directory_count: number
  kopia_snapshot_count: number
  total_size_bytes: number
  recoverable_size_bytes?: number
  new_original_content_bytes?: number | null
  new_packed_content_bytes?: number | null
  storage_stats_available?: boolean
  data_reuse_ratio?: number | null
  compression_savings_ratio?: number | null
  combined_reduction_ratio?: number | null
  fully_reused?: boolean
  file_count: number
  dir_count: number
  error_code?: string
  error_message?: string
  metadata?: Record<string, unknown>
  directories?: BackupSourceSnapshotDirectory[]
}

export type BackupSnapshotBrowserEntry = {
  name: string
  path: string
  type: 'file' | 'dir' | string
  size_bytes: number
  modified_at?: string | null
  downloadable: boolean
  has_children?: boolean | null
}

export type BackupSnapshotDirectoryBrowseResult = {
  directory_id: number
  snapshot_id: number
  path: string
  parent_path: string
  entries: BackupSnapshotBrowserEntry[]
  has_more: boolean
  next_cursor: string
}

export type SnapshotDownloadTaskResult = {
  id: number
  task_uuid: string
  task_type: string
  status: string
  progress: number | string
  result_payload?: {
    artifact_id?: number
    filename?: string
    content_type?: string
    size_bytes?: number
    expires_at?: string
  } | Record<string, unknown> | null
  error_message?: string | null
}

export type BackupSourceSnapshotListParams = {
  page?: number
  page_size?: number
  search?: string
  source_type?: string
  source_ref_id?: number
  backup_config_id?: number
  repository_id?: number
  status?: string
  exclude_status?: string
  ordering?: string
  include_directory_snapshots?: number
}

export type PagedResult<T> = {
  count: number
  results: T[]
}

export type BackupConfigResetResult = {
  created_count: number
  results: Array<{
    source_id: string
    source_type?: string
    source_ref_id?: number
    status: string
    progress?: number | string
    task_id?: number
    task_uuid?: string
    backup_config_ids?: number[]
    message?: string
  }>
}

export type RecoveryPlanCreatePayload = {
  backup_config_id: number
  scope?: 'snapshot' | 'paths'
  source_type: 'agent' | 'nas'
  source_ref_id: number
  source_path: string
  backup_config_dir_id?: number | null
  target_type?: 'agent' | 'nas'
  target_ref_id?: number | null
  restore_host_id?: number | null
  restore_dir: string
  conflict_mode: 'skip' | 'overwrite'
  sort_order?: number
}

export type RecoveryPlanPatchPayload = {
  backup_config_id?: number
  scope?: 'snapshot' | 'paths'
  backup_config_dir_id?: number | null
  source_path?: string
  target_type?: 'agent' | 'nas'
  target_ref_id?: number | null
  restore_host_id?: number | null
  restore_dir?: string
  conflict_mode?: string
  sort_order?: number
}

const configBase = '/api/v1/protection/backup-configs'
const recoveryPlanBase = '/api/v1/restore/plans'
const sourceSnapshotBase = '/api/v1/protection/backup-source-snapshots'
const sourceSnapshotDirectoryBase = '/api/v1/protection/backup-source-snapshot-directories'
const API_BASE = import.meta.env.VITE_API_BASE?.toString() || ''

function orgHeaders(): Record<string, string> {
  return getCorrelationHeaders()
}

function query(params?: Record<string, string | number | undefined>) {
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

function normalizeRecoveryPlan<T extends Partial<BackupConfigRecoveryPlan>>(plan: T): T {
  const targetType = plan.target_type || 'agent'
  const targetRefId = plan.target_ref_id ?? plan.restore_host_id ?? null
  return {
    ...plan,
    target_type: targetType,
    target_ref_id: targetRefId,
    restore_host_id: targetType === 'agent' ? targetRefId : null,
  }
}

function normalizeBackupConfig<T extends BackupConfig>(config: T): T {
  return {
    ...config,
    compression_level: parseCompressionLevel(config.compression_level),
  }
}

/** Parse the numeric source_ref_id from a wizard sourceId like "agent:5" or "nas:12". */
export function parseSourceRefId(sourceId: string): number {
  const parts = sourceId.split(':')
  const last = parts[parts.length - 1]
  const num = parseInt(last, 10)
  return isNaN(num) ? 0 : num
}

/** Create a backup config. */
export async function createBackupConfig(payload: BackupConfigCreatePayload) {
  logger.info('protectionBackupConfigApi.ts', 269, 'backup config create', {
    source_type: payload.source_type,
    source_ref_id: payload.source_ref_id,
    repository_id: payload.repository_id,
    name: payload.name,
    directory_count: payload.directories?.length ?? 0,
  })
  return normalizeBackupConfig(unwrapApiPayload<BackupConfigDetail>(
    await api<unknown>(`${configBase}/`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  ))
}

/** List backup configs. */
export async function listBackupConfigs(params?: BackupConfigListParams, init?: RequestInit) {
  const qs = query(params as Record<string, string | number | undefined>)
  const path = qs ? `${configBase}/?${qs}` : `${configBase}/`
  const result = paged<BackupConfig>(await api<unknown>(path, { ...init, headers: orgHeaders() }))
  return { ...result, results: result.results.map(normalizeBackupConfig) }
}

/** Get backup config detail. */
export async function getBackupConfig(id: number, init?: RequestInit) {
  return normalizeBackupConfig(unwrapApiPayload<BackupConfigDetail>(
    await api<unknown>(`${configBase}/${id}/`, { ...init, headers: orgHeaders() }),
  ))
}

/** Update a backup config. */
export async function updateBackupConfig(id: number, payload: BackupConfigUpdatePayload) {
  return normalizeBackupConfig(unwrapApiPayload<BackupConfigDetail>(
    await api<unknown>(`${configBase}/${id}/`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  ))
}

/** Retry a failed Direct NAS storage validation after correcting the target or Agent. */
export async function retryBackupConfigProvision(id: number) {
  return unwrapApiPayload<{ backup_config: BackupConfigDetail; task_uuid: string }>(
    await api<unknown>(`${configBase}/${id}/retry-provision/`, {
      method: 'POST',
      headers: orgHeaders(),
    }),
  )
}

/** List backup source snapshots. */
export async function listBackupSourceSnapshots(params?: BackupSourceSnapshotListParams, init?: RequestInit) {
  const qs = query(params as Record<string, string | number | undefined>)
  const path = qs ? `${sourceSnapshotBase}/?${qs}` : `${sourceSnapshotBase}/`
  return paged<BackupSourceSnapshot>(await api<unknown>(path, { ...init, headers: orgHeaders() }))
}

/** Get backup source snapshot detail. */
export async function getBackupSourceSnapshot(id: number, init?: RequestInit) {
  return unwrapApiPayload<BackupSourceSnapshot>(
    await api<unknown>(`${sourceSnapshotBase}/${id}/`, { ...init, headers: orgHeaders() }),
  )
}

/** Soft-delete a backup source snapshot. */
export async function deleteBackupSourceSnapshot(id: number) {
  return unwrapApiPayload<{ deleted: boolean; id: number; task_id?: number; task_uuid?: string }>(
    await api<unknown>(`${sourceSnapshotBase}/${id}/`, {
      method: 'DELETE',
      headers: orgHeaders(),
    }),
  )
}

export async function browseBackupSnapshotDirectory(
  directoryId: number,
  params?: { path?: string; limit?: number },
) {
  const qs = query(params as Record<string, string | number | undefined>)
  const path = qs
    ? `${sourceSnapshotDirectoryBase}/${directoryId}/browse/?${qs}`
    : `${sourceSnapshotDirectoryBase}/${directoryId}/browse/`
  return unwrapApiPayload<BackupSnapshotDirectoryBrowseResult>(
    await api<unknown>(path, { headers: orgHeaders() }),
  )
}

export function backupSnapshotDirectoryDownloadUrl(directoryId: number, filePath: string) {
  const qs = new URLSearchParams()
  qs.set('path', filePath)
  return `${sourceSnapshotDirectoryBase}/${directoryId}/download/?${qs}`
}

function filenameFromDisposition(disposition: string | null) {
  if (!disposition) return ''
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) return decodeURIComponent(utf8Match[1])
  const plainMatch = disposition.match(/filename="?([^";]+)"?/i)
  return plainMatch?.[1] || ''
}

export async function downloadBackupSnapshotDirectoryFile(directoryId: number, filePath: string) {
  const path = backupSnapshotDirectoryDownloadUrl(directoryId, filePath)
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      ...orgHeaders(),
    },
    credentials: 'include',
  })
  if (!res.ok) {
    let message = res.statusText || 'Download failed'
    try {
      const body = await res.json()
      message = String(
        body?.data?.error?.message
          || body?.error?.message
          || body?.message
          || message,
      )
    } catch {
      // Keep the HTTP status text when the response is not JSON.
    }
    throw new Error(message)
  }
  return {
    blob: await res.blob(),
    filename: filenameFromDisposition(res.headers.get('Content-Disposition')),
  }
}

export async function createBackupSnapshotDirectoryDownloadTask(directoryId: number, targetPath: string) {
  return unwrapApiPayload<SnapshotDownloadTaskResult>(
    await api<unknown>(`${sourceSnapshotDirectoryBase}/${directoryId}/download-tasks/`, {
      method: 'POST',
      body: JSON.stringify({ path: targetPath }),
      headers: orgHeaders(),
    }),
  )
}

export async function createBackupSnapshotDirectoryBatchDownloadTask(directoryId: number, paths: string[]) {
  return unwrapApiPayload<SnapshotDownloadTaskResult>(
    await api<unknown>(`${sourceSnapshotDirectoryBase}/${directoryId}/batch-download-tasks/`, {
      method: 'POST',
      body: JSON.stringify({ paths }),
      headers: orgHeaders(),
    }),
  )
}

export function snapshotDownloadArtifactFileUrl(artifactId: number) {
  return `/api/v1/protection/snapshot-download-artifacts/${artifactId}/file/`
}

export async function createSnapshotArtifactDownloadUrl(artifactId: number) {
  const result = unwrapApiPayload<{ url: string }>(
    await api<unknown>(`/api/v1/protection/snapshot-download-artifacts/${artifactId}/download-url/`, {
      headers: orgHeaders(),
    }),
  )
  const url = String(result.url || '')
  return {
    url: /^(?:https?:)?\/\//.test(url) ? url : `${API_BASE}${url}`,
  }
}

export async function downloadSnapshotArtifactFile(artifactId: number) {
  const path = snapshotDownloadArtifactFileUrl(artifactId)
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      ...orgHeaders(),
    },
    credentials: 'include',
  })
  if (!res.ok) {
    let message = res.statusText || 'Download failed'
    try {
      const body = await res.json()
      message = String(
        body?.data?.error?.message
          || body?.error?.message
          || body?.message
          || message,
      )
    } catch {
      // Keep the HTTP status text when the response is not JSON.
    }
    throw new Error(message)
  }
  return {
    blob: await res.blob(),
    filename: filenameFromDisposition(res.headers.get('Content-Disposition')),
  }
}

/** Delete a backup config. */
export async function deleteBackupConfig(id: number) {
  return unwrapApiPayload<{ deleted: boolean; id: number }>(
    await api<unknown>(`${configBase}/${id}/`, {
      method: 'DELETE',
      headers: orgHeaders(),
    }),
  )
}

/** Reset backup configuration and queue resource cleanup tasks. */
export async function resetBackupConfigs(sourceIds: string[], confirmation: string) {
  return unwrapApiPayload<BackupConfigResetResult>(
    await api<unknown>(`${configBase}/reset/`, {
      method: 'POST',
      headers: orgHeaders(),
      body: JSON.stringify({
        source_ids: sourceIds,
        confirmation,
      }),
    }),
  )
}

/** List recovery plans by source. */
export async function listRecoveryPlans(sourceType: string, sourceRefId: number) {
  const qs = query({ source_type: sourceType, source_ref_id: sourceRefId })
  const result = paged<BackupConfigRecoveryPlan>(
    await api<unknown>(`${recoveryPlanBase}/?${qs}`, { headers: orgHeaders() }),
  )
  return { ...result, results: result.results.map(normalizeRecoveryPlan) }
}

/** Create a recovery plan. */
export async function createRecoveryPlan(payload: RecoveryPlanCreatePayload) {
  const body = {
    ...payload,
    target_type: payload.target_type || 'agent',
    target_ref_id: payload.target_ref_id || payload.restore_host_id,
  }
  return normalizeRecoveryPlan(unwrapApiPayload<BackupConfigRecoveryPlan>(
    await api<unknown>(`${recoveryPlanBase}/`, {
      method: 'POST',
      body: JSON.stringify(body),
      headers: orgHeaders(),
    }),
  ))
}

/** Update a recovery plan. */
export async function updateRecoveryPlan(id: number, payload: RecoveryPlanPatchPayload) {
  const body = {
    ...payload,
    target_type: payload.target_type || (payload.restore_host_id ? 'agent' : undefined),
    target_ref_id: payload.target_ref_id || payload.restore_host_id,
  }
  return normalizeRecoveryPlan(unwrapApiPayload<BackupConfigRecoveryPlan>(
    await api<unknown>(`${recoveryPlanBase}/${id}/`, {
      method: 'PATCH',
      body: JSON.stringify(body),
      headers: orgHeaders(),
    }),
  ))
}

/** Delete a recovery plan. */
export async function deleteRecoveryPlan(id: number) {
  return unwrapApiPayload<{ deleted: boolean; id: number }>(
    await api<unknown>(`${recoveryPlanBase}/${id}/`, {
      method: 'DELETE',
      headers: orgHeaders(),
    }),
  )
}
