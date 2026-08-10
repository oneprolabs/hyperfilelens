import { api } from './api'
import { unwrapApiPayload, asList } from './parse'
import { getEffectiveOrgKey } from '../composables/useAuth'
import type { BackupConfigDetail } from './protectionBackupConfigApi'
import type { BackupPolicy, FileFilterRule } from './protectionPolicyApi'

export type BackupSelectableRepositoryPreview = {
  id: string
  config_id: number
  repository_id: number
  name: string
  repo_type: string
  location: string
  health: string
  status: string
  used_bytes: number
  capacity_bytes: number
  last_checked_at: string | null
}

export type BackupSelectableRuntimeSnapshot = {
  id: number
  snapshot_uid: string
  source_type: string
  source_ref_id: number
  backup_config_id: number
  repository_id: number
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
  file_count: number
  dir_count: number
  error_code?: string
  error_message?: string
  recoverable?: boolean
}

export type BackupSelectableRuntime = {
  backup?: Record<string, unknown>
  restore?: Record<string, unknown>
  latest_snapshot?: BackupSelectableRuntimeSnapshot | null
  has_restorable_snapshot?: boolean
  restorable_snapshot_count?: number
  latest_restorable_snapshot?: BackupSelectableRuntimeSnapshot | null
}

export type SourceResource = {
  id: number
  name: string
  description?: string
  resource_type: string
  resource_type_display?: string
  config?: Record<string, unknown>
  credentials?: Record<string, unknown>
  bound_node?: number | null
  bound_node_name?: string | null
  /** Bound node lifecycle state; use bound_node_availability for connectivity. */
  bound_node_status?: string | null
  bound_node_availability?: 'online' | 'offline' | null
  mount_status?: string
  mount_point?: string
  status?: string
  status_display?: string
  status_message?: string
  availability?: 'online' | 'offline'
  availability_updated_at?: string | null
  effective_status?: 'removing' | 'remove_failed' | 'error' | 'probing' | 'online' | 'unverified'
  effective_status_message?: string
  connection_test_status?: 'idle' | 'pending' | 'running' | 'success' | 'failed'
  connection_summary?: string
  total_size?: number
  used_size?: number
  free_size?: number
  usage_percentage?: number
  file_count?: number
  requires_mount?: boolean
  last_connection_test?: string | null
  created_at?: string
  updated_at?: string
}

export type SourceStats = {
  total: number
  active: number
  inactive: number
  error: number
  mounted: number
  by_type: Record<string, number>
  total_size: number
  total_files: number
}

export type ProductionSourceSummaryPart = {
  total: number
  available: number
  unavailable: number
}

export type ProductionSourceSummary = ProductionSourceSummaryPart & {
  hosts: ProductionSourceSummaryPart
  nas: ProductionSourceSummaryPart
}

/** Unified backup wizard catalog item (agent + NAS). */
export type BackupSelectableSource = {
  id: string
  kind: 'agent' | 'nas'
  ref_id: number
  type: 'host' | 'nas'
  name: string
  hostname: string
  node_name: string
  node_ip: string
  status: 'active' | 'inactive' | 'error' | 'probing' | 'removing' | 'remove_failed' | 'removed' | 'upgrading' | 'restarting' | 'verifying' | 'verification_pending' | 'cleaning_up' | 'failed' | 'upgrade_failed' | 'deregistration_failed'
  availability?: BackupSelectableAvailability
  protocol?: 'nfs' | 'smb'
  platform?: 'linux' | 'windows' | 'macos'
  connection_uri?: string
  bound_node_id?: number | null
  mount_status?: string
  mount_point?: string
  registered_at?: string | null
  /** Agent inventory summary; omitted for NAS sources. */
  cpu_cores?: number | null
  memory_total_bytes?: number | null
  disk_count?: number | null
  /** Protection wizard step for real sources: 1 pool / 2 config / 3 ready */
  pipeline_step?: 1 | 2 | 3
  backup_configs?: {
    count: number
    ids: number[]
    configs?: BackupConfigDetail[]
    dirs_preview?: Array<Record<string, unknown>>
    dirs_overflow_count?: number
    repos_preview?: BackupSelectableRepositoryPreview[]
    repos_overflow_count?: number
  }
  policies?: {
    count: number
    names: string[]
    items?: BackupPolicy[]
  }
  filters?: {
    count: number
    names: string[]
    items?: FileFilterRule[]
  }
  runtime?: BackupSelectableRuntime
}

export type BackupPipelineStep = 1 | 2 | 3
export type BackupSelectableSearchField = 'source_name' | 'source_hostname' | 'source_ip'
export type BackupSelectableTaskStatus = 'success' | 'failed' | 'running' | 'none'
export type BackupSelectableAvailability = 'online' | 'offline'
export type BackupSelectableQueryParams = {
  page?: number
  page_size?: number
  search?: string
  search_field?: BackupSelectableSearchField
  source_name?: string
  source_hostname?: string
  source_ip?: string
  status?: BackupSelectableSource['status']
  source_status?: BackupSelectableSource['status']
  availability?: BackupSelectableAvailability
  type?: 'host' | 'nas'
  backup_task_status?: BackupSelectableTaskStatus
  restore_task_status?: BackupSelectableTaskStatus
  running_task?: 'backup' | 'restore'
  backup_running?: boolean
  restore_running?: boolean
  backup_policy_id?: number
  file_filter_rule_id?: number
  repository_id?: number
  exclude?: string
  ids?: string
  step?: BackupPipelineStep
  expand?: string
}

export type BackupSourceDirectoryEntry = {
  label: string
  path: string
  isLeaf: boolean
  is_dir?: boolean
  path_type?: 'directory' | 'file' | 'unknown'
  size?: number
  mod_time?: string
}

export type BackupSourceDirectoryList = {
  source_id: string
  source_kind: 'agent' | 'nas' | 'proxy'
  source_ref_id: number
  node_id: number
  path: string
  root_path: string
  mount_path?: string
  root?: BackupSourceDirectoryEntry
  task_id: string
  count: number
  has_more?: boolean
  next_cursor?: string
  entries: BackupSourceDirectoryEntry[]
}

export type BackupSourcePathInfo = BackupSourceDirectoryEntry & {
  source_id: string
  source_kind: 'agent' | 'nas' | 'proxy'
  source_ref_id: number
  node_id: number
  exists: boolean
  task_id: string
}

const base = '/api/v1/source/resources'
const backupSelectableBase = '/api/v1/source/backup-selectable'

function orgHeaders(): Record<string, string> {
  return { 'X-Org-Key': getEffectiveOrgKey() || '' }
}

function paged<T>(raw: unknown): { count: number; results: T[] } {
  const data = unwrapApiPayload<Record<string, unknown>>(raw)
  return { count: typeof data.count === 'number' ? data.count : 0, results: asList<T>(data) }
}

export async function listSourceResources(params?: Record<string, string | number>, init?: RequestInit) {
  const qs = new URLSearchParams()
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== '' && v !== undefined) qs.set(k, String(v))
    }
  }
  const path = qs.toString() ? `${base}/?${qs}` : `${base}/`
  return paged<SourceResource>(await api<unknown>(path, { ...init, headers: orgHeaders() }))
}

export async function getSourceResource(id: number, init?: RequestInit) {
  return unwrapApiPayload<SourceResource>(
    await api<unknown>(`${base}/${id}/`, { ...init, headers: orgHeaders() }),
  )
}

export async function createSourceResource(payload: Record<string, unknown>) {
  return unwrapApiPayload<SourceResource>(
    await api<unknown>(`${base}/`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

export async function updateSourceResource(id: number, payload: Record<string, unknown>) {
  return unwrapApiPayload<SourceResource>(
    await api<unknown>(`${base}/${id}/`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

export type SourceDeleteResult = {
  deleted: boolean
  agent_removal?: {
    uninstall_attempted?: boolean
    uninstall_task_status?: string | null
    uninstall_timed_out?: boolean
  } | null
  result?: string
  warnings?: Array<Record<string, unknown>>
}

export async function listBackupSelectableSources(params?: BackupSelectableQueryParams, init?: RequestInit) {
  const qs = new URLSearchParams()
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== '') qs.set(k, String(v))
    }
  }
  const path = qs.toString() ? `${backupSelectableBase}/?${qs}` : `${backupSelectableBase}/`
  return paged<BackupSelectableSource>(await api<unknown>(path, { ...init, headers: orgHeaders() }))
}

export async function listBackupSourceDirectories(params: {
  source_id: string
  path?: string
  timeout?: number
  limit?: number
  cursor?: string
  include_files?: boolean
  include_metadata?: boolean
}, init?: RequestInit) {
  const qs = new URLSearchParams()
  qs.set('source_id', params.source_id)
  if (params.path !== undefined) qs.set('path', params.path)
  if (params.timeout !== undefined) qs.set('timeout', String(params.timeout))
  if (params.limit !== undefined) qs.set('limit', String(params.limit))
  if (params.cursor !== undefined && params.cursor !== '') qs.set('cursor', params.cursor)
  if (params.include_files !== undefined) qs.set('include_files', params.include_files ? 'true' : 'false')
  if (params.include_metadata !== undefined) qs.set('include_metadata', params.include_metadata ? 'true' : 'false')
  return unwrapApiPayload<BackupSourceDirectoryList>(
    await api<unknown>(`${backupSelectableBase}/directories/?${qs}`, { ...init, headers: orgHeaders() }),
  )
}

export async function getBackupSourcePathInfo(params: {
  source_id: string
  path: string
  timeout?: number
  include_metadata?: boolean
}, init?: RequestInit) {
  const qs = new URLSearchParams()
  qs.set('source_id', params.source_id)
  qs.set('path', params.path)
  if (params.timeout !== undefined) qs.set('timeout', String(params.timeout))
  if (params.include_metadata !== undefined) qs.set('include_metadata', params.include_metadata ? 'true' : 'false')
  return unwrapApiPayload<BackupSourcePathInfo>(
    await api<unknown>(`${backupSelectableBase}/path-info/?${qs}`, { ...init, headers: orgHeaders() }),
  )
}

export async function setBackupSourcePipelineStep(payload: { ids: string[]; step: BackupPipelineStep }) {
  return unwrapApiPayload<{ updated: string[]; step: BackupPipelineStep }>(
    await api<unknown>(`${backupSelectableBase}/pipeline/`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

export async function revertBackupSourcePipelineStep(payload: {
  ids: string[]
  target_step: 1 | 2
  force?: boolean
}) {
  return unwrapApiPayload<{
    updated: string[]
    target_step: 1 | 2
    warnings?: Array<Record<string, unknown>>
    result?: string
  }>(
    await api<unknown>(`${backupSelectableBase}/pipeline/revert/`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

export type BackupSourceDeleteReason = {
  code: string
  detail: string
  source_id?: string
  source_name?: string
  repository_id?: number
  repository_name?: string
}

export type BackupSourceDeletePreflight = {
  risks: BackupSourceDeleteReason[]
  blocking: BackupSourceDeleteReason[]
  strict_may_fail: boolean
  delete_disabled: boolean
}

export type BackupSourceDeleteResult = {
  ok: boolean
  accepted?: boolean
  result: string
  status?: string
  deleted: string[]
  pending_removals?: Array<{
    source_id: string
    node_id: number
    task_id?: string | null
    operation_id?: string | null
    state?: string
  }>
  warnings: Array<Record<string, unknown>>
  cleanup?: Record<string, number>
  sources?: Array<Record<string, unknown>>
  task_id?: number
  task_uuid?: string
  task_ids?: number[]
  task_uuids?: string[]
  tasks?: Array<{ source_id: string; task_id: number; task_uuid: string }>
}

export async function preflightDeleteBackupSources(ids: string[]) {
  return unwrapApiPayload<BackupSourceDeletePreflight>(
    await api<unknown>(`${backupSelectableBase}/delete-preflight/`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
      headers: orgHeaders(),
    }),
  )
}

export type BackupSourceRevertPreflight = BackupSourceDeletePreflight & {
  revert_disabled?: boolean
}

export async function preflightRevertBackupSources(ids: string[], targetStep: 1 | 2) {
  return unwrapApiPayload<BackupSourceRevertPreflight>(
    await api<unknown>(`${backupSelectableBase}/revert-preflight/`, {
      method: 'POST',
      body: JSON.stringify({ ids, target_step: targetStep }),
      headers: orgHeaders(),
    }),
  )
}

export function parseBackupSourceDeleteError(err: unknown): {
  reasons: BackupSourceDeleteReason[]
  message: string
  hint?: string
} {
  const row = err as {
    detail?: unknown
    message?: string
    hint?: string
    reasons?: BackupSourceDeleteReason[]
    meta?: Record<string, unknown>
  }
  if (row.reasons?.length) {
    return {
      reasons: row.reasons,
      message: row.message || 'Delete failed',
      hint: row.hint,
    }
  }
  const payload = unwrapApiPayload<Record<string, unknown>>(row.detail ?? err)
  const nestedMeta = payload.meta as Record<string, unknown> | undefined
  const reasons =
    (payload.reasons as BackupSourceDeleteReason[] | undefined)
    || (nestedMeta?.reasons as BackupSourceDeleteReason[] | undefined)
    || []
  const message =
    (typeof payload.detail === 'string' ? payload.detail : '')
    || (typeof nestedMeta?.diagnostic === 'string' ? nestedMeta.diagnostic : '')
    || (typeof payload.message === 'string' ? payload.message : '')
    || row.message
    || 'Delete failed'
  const hint =
    (typeof payload.hint === 'string' ? payload.hint : undefined)
    || (typeof nestedMeta?.hint === 'string' ? nestedMeta.hint : undefined)
    || row.hint
  return {
    reasons,
    message,
    hint,
  }
}

export async function bulkDeleteBackupSources(ids: string[], force = false, confirmation = '') {
  try {
    return unwrapApiPayload<BackupSourceDeleteResult>(
      await api<unknown>(`${backupSelectableBase}/bulk-delete/`, {
        method: 'POST',
        body: JSON.stringify({ ids, force, confirmation }),
        headers: orgHeaders(),
      }),
    )
  } catch (err: unknown) {
    const parsed = parseBackupSourceDeleteError(err)
    const error = new Error(parsed.message) as Error & {
      reasons?: BackupSourceDeleteReason[]
      hint?: string
    }
    error.reasons = parsed.reasons
    error.hint = parsed.hint
    throw error
  }
}

export async function deleteSourceResource(id: number, force = false): Promise<SourceDeleteResult> {
  const selectableId = `nas:${id}`
  const result = await bulkDeleteBackupSources(
    [selectableId],
    force,
    force ? 'FORCE DEREGISTER' : 'DEREGISTER',
  )
  return {
    deleted: result.deleted.includes(selectableId),
    agent_removal: null,
    result: result.result,
    warnings: result.warnings,
  }
}

export async function sourceStatistics(init?: RequestInit) {
  return unwrapApiPayload<SourceStats>(
    await api<unknown>(`${base}/statistics/`, { ...init, headers: orgHeaders() }),
  )
}

export async function productionSourceSummary(init?: RequestInit) {
  return unwrapApiPayload<ProductionSourceSummary>(
    await api<unknown>(`${base}/production-summary/`, { ...init, headers: orgHeaders() }),
  )
}

export const SOURCE_CONNECTION_TEST_TIMEOUT_MS = 195_000
export const SOURCE_CONNECTION_TEST_MIN_FEEDBACK_MS = 900
export const SOURCE_CONNECTION_TEST_RESULT_TOAST_MS = 6000

export type SourceConnectionTestResult = {
  success: boolean
  message?: string
  details?: unknown
  gatewayTimeout?: boolean
}

function isGatewayTimeoutMessage(message: string): boolean {
  const normalized = message.trim().toLowerCase()
  return normalized.includes('gateway time') || normalized === '504'
}

function parseConnectionTestResult(data: unknown): SourceConnectionTestResult | null {
  const row = unwrapApiPayload<Record<string, unknown>>(data)
  if (!row || typeof row !== 'object') return null
  if (typeof row.success !== 'boolean') return null
  return {
    success: row.success,
    message: typeof row.message === 'string' ? row.message : undefined,
    details: row.details,
  }
}

function connectionTestResultFromThrown(err: unknown): SourceConnectionTestResult | null {
  if (!err || typeof err !== 'object') return null
  const apiErr = err as { detail?: unknown; message?: string; status?: number }
  if (apiErr.status === 504) {
    return { success: false, gatewayTimeout: true }
  }
  const fromDetail = parseConnectionTestResult(apiErr.detail)
  if (fromDetail) return fromDetail
  const message = typeof apiErr.message === 'string' ? apiErr.message.trim() : ''
  if (message && isGatewayTimeoutMessage(message)) {
    return { success: false, gatewayTimeout: true }
  }
  if (message && message !== 'Bad Request' && message !== 'Request failed') {
    return { success: false, message }
  }
  return null
}

export async function testSourceConnection(id: number, init?: RequestInit): Promise<SourceConnectionTestResult> {
  try {
    const parsed = parseConnectionTestResult(
      await api<unknown>(`${base}/${id}/test-connection/`, {
        method: 'POST',
        headers: orgHeaders(),
        ...init,
      }),
    )
    if (parsed) return parsed
    return { success: false, message: 'Invalid connection test response.' }
  } catch (err) {
    const fromError = connectionTestResultFromThrown(err)
    if (fromError) return fromError
    throw err
  }
}

export async function testSourceDraft(payload: Record<string, unknown>) {
  return unwrapApiPayload<{ success: boolean; message?: string }>(
    await api<unknown>(`${base}/test-draft/`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

export async function mountSource(id: number) {
  return unwrapApiPayload<{ success: boolean; message?: string; error_code?: string; mount_point?: string }>(
    await api<unknown>(`${base}/${id}/mount/`, { method: 'POST', headers: orgHeaders() }),
  )
}

export async function unmountSource(id: number) {
  return unwrapApiPayload<{ success: boolean; message?: string }>(
    await api<unknown>(`${base}/${id}/unmount/`, { method: 'POST', headers: orgHeaders() }),
  )
}
