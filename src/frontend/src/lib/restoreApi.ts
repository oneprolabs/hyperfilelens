import { getEffectiveOrgKey } from '../composables/useAuth'
import { api } from './api'
import { logger } from './logger'
import { asList, asPagination, unwrapApiPayload } from './parse'

export type RestoreEndpointType = 'agent' | 'nas'
export type RestoreConflictMode = 'skip' | 'overwrite'
export type RestoreScope = 'snapshot' | 'paths'
export type RestoreRecordSearchField = 'restore_uid' | 'snapshot_uid' | 'task_uuid'

export type PagedResult<T> = {
  count: number
  results: T[]
}

export type RestorePlan = {
  id: number
  backup_config_id: number
  backup_config_dir_id: number | null
  scope: RestoreScope
  source_type: RestoreEndpointType
  source_ref_id: number
  source_path: string
  target_type: RestoreEndpointType
  target_ref_id: number
  restore_dir: string
  conflict_mode: RestoreConflictMode
  enabled: boolean
  sort_order: number
  created_at: string
  updated_at: string
}

export type RestorePlanCreatePayload = {
  backup_config_id: number
  backup_config_dir_id?: number | null
  scope?: RestoreScope
  source_type: RestoreEndpointType
  source_ref_id: number
  source_path?: string
  target_type: RestoreEndpointType
  target_ref_id: number
  restore_dir: string
  conflict_mode: RestoreConflictMode
  enabled?: boolean
  sort_order?: number
}

export type RestorePlanListParams = {
  page?: number
  page_size?: number
  backup_config_id?: number
  source_type?: RestoreEndpointType
  source_ref_id?: number
  enabled?: boolean
}

export type RestorePlanBatchRunPayload = {
  backup_config_id: number
  target_type: RestoreEndpointType
  target_ref_id: number
  restore_dir: string
  conflict_mode: RestoreConflictMode
  source_snapshot_id?: number
  idempotency_key?: string
}

export type RestoreRecordItemCreatePayload = {
  source_snapshot_directory_id: number
  selected_paths?: string[]
  target_path?: string
  conflict_mode?: RestoreConflictMode
}

export type RestoreRecordCreatePayload = {
  source_snapshot_id: number
  source_type: RestoreEndpointType
  source_ref_id: number
  target_type: RestoreEndpointType
  target_ref_id: number
  target_path: string
  scope: RestoreScope
  conflict_mode: RestoreConflictMode
  items: RestoreRecordItemCreatePayload[]
  idempotency_key?: string
}

export type RestoreCreateResult = {
  restore_record_id: number
  restore_uid: string
  task_id: number
  task_uuid: string
  status: string
  source_snapshot_id: number
  item_count: number
}

export type RestoreSourceRunResult = {
  status: string
  record_count: number
  task_count: number
  records: RestoreCreateResult[]
}

export type RestoreRecordItem = {
  id: number
  source_snapshot_directory_id: number
  backup_config_dir_id: number
  repository_id: number
  kopia_snapshot_id: string
  source_path: string
  selected_paths: string[]
  target_path: string
  target_display_path?: string
  conflict_mode: RestoreConflictMode
  status: string
  error_code?: string
  error_message?: string
  created_at: string
  updated_at: string
}

export type RestoreRecordTaskSummary = {
  status: string
  progress: number | string
  started_at: string | null
  finished_at: string | null
}

export type RestoreRecord = {
  id: number
  restore_uid: string
  source_mode: 'plan' | 'manual'
  plan_id: number | null
  task_id: number
  task_uuid: string
  source_type: RestoreEndpointType
  source_ref_id: number
  backup_config_id: number | null
  source_snapshot_id: number
  source_snapshot_uid: string
  target_type: RestoreEndpointType
  target_ref_id: number
  target_path: string
  target_display_path?: string
  scope: RestoreScope
  conflict_mode: RestoreConflictMode
  request_payload?: Record<string, unknown>
  expanded_payload?: Record<string, unknown>
  created_at: string
  updated_at: string
  items: RestoreRecordItem[]
  task_summary: RestoreRecordTaskSummary | null
}

export type RestoreSnapshotBrowseEntry = {
  name: string
  path: string
  type: 'file' | 'dir' | string
  size_bytes: number
  modified_at?: string | null
  downloadable: boolean
  has_children?: boolean | null
}

export type RestoreSnapshotBrowseResult = {
  directory_id: number
  snapshot_id: number
  path: string
  parent_path: string
  entries: RestoreSnapshotBrowseEntry[]
  has_more: boolean
  next_cursor: string
}

export type RestoreSnapshotPathInfo = {
  directory_id: number
  snapshot_id: number
  path: string
  name: string
  type: 'file' | 'dir' | string
  is_dir: boolean
  size_bytes: number
  modified_at?: string | null
  exists: boolean
}

const restorePlanBase = '/api/v1/restore/plans'
const restoreRecordBase = '/api/v1/restore/records'
const restoreSnapshotDirectoryBase = '/api/v1/restore/snapshot-directories'

function orgHeaders(): Record<string, string> {
  return { 'X-Org-Key': getEffectiveOrgKey() || '' }
}

function query(params?: Record<string, string | number | boolean | undefined>) {
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

export async function listRestorePlans(params?: RestorePlanListParams, init?: RequestInit) {
  const qs = query(params as Record<string, string | number | boolean | undefined>)
  const path = qs ? `${restorePlanBase}/?${qs}` : `${restorePlanBase}/`
  return paged<RestorePlan>(await api<unknown>(path, { ...init, headers: orgHeaders() }))
}

export async function createRestorePlan(payload: RestorePlanCreatePayload) {
  logger.info('restoreApi.ts', 190, 'restore plan create', {
    backup_config_id: payload.backup_config_id,
    source_type: payload.source_type,
    source_ref_id: payload.source_ref_id,
    target_ref_id: payload.target_ref_id,
  })
  return unwrapApiPayload<RestorePlan>(
    await api<unknown>(`${restorePlanBase}/`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

export async function runRestorePlan(id: number, idempotencyKey?: string) {
  logger.info('restoreApi.ts', 206, 'restore plan run', { plan_id: id, idempotency_key: idempotencyKey || '' })
  return unwrapApiPayload<RestoreCreateResult>(
    await api<unknown>(`${restorePlanBase}/${id}/run/`, {
      method: 'POST',
      body: JSON.stringify({ idempotency_key: idempotencyKey || '' }),
      headers: orgHeaders(),
    }),
  )
}

export async function runRestorePlanBatch(payload: RestorePlanBatchRunPayload) {
  return unwrapApiPayload<RestoreCreateResult>(
    await api<unknown>(`${restorePlanBase}/run-batch/`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

export async function runRestorePlansForSource(payload: {
  source_type: RestoreEndpointType
  source_ref_id: number
  source_snapshot_id?: number
  idempotency_key?: string
}) {
  return unwrapApiPayload<RestoreSourceRunResult>(
    await api<unknown>(`${restorePlanBase}/run-source/`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

export async function createRestoreRecord(payload: RestoreRecordCreatePayload) {
  logger.info('restoreApi.ts', 216, 'restore record create', {
    source_snapshot_id: payload.source_snapshot_id,
    source_type: payload.source_type,
    source_ref_id: payload.source_ref_id,
    target_type: payload.target_type,
    target_ref_id: payload.target_ref_id,
    item_count: payload.items?.length ?? 0,
  })
  return unwrapApiPayload<RestoreCreateResult>(
    await api<unknown>(`${restoreRecordBase}/`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

export async function listRestoreRecords(params?: {
  page?: number
  page_size?: number
  source_type?: RestoreEndpointType
  source_ref_id?: number
  task_uuid?: string
  search?: string
  search_fields?: string
  status?: string
  source_mode?: 'plan' | 'manual'
  created_from?: string
  created_to?: string
}, init?: RequestInit) {
  const qs = query(params as Record<string, string | number | boolean | undefined>)
  const path = qs ? `${restoreRecordBase}/?${qs}` : `${restoreRecordBase}/`
  return paged<RestoreRecord>(await api<unknown>(path, { ...init, headers: orgHeaders() }))
}

export async function fetchRestoreRecordRuntime(recordId: number, init?: RequestInit) {
  return unwrapApiPayload<import('./kopiaProgress').TaskRuntimePayload>(
    await api<unknown>(`${restoreRecordBase}/${recordId}/runtime/`, { ...init, headers: orgHeaders() }),
  )
}

export async function browseRestoreSnapshotDirectory(
  directoryId: number,
  params: { target_node_id: number; path?: string; limit?: number },
  init?: RequestInit,
) {
  const qs = query(params as Record<string, string | number | boolean | undefined>)
  return unwrapApiPayload<RestoreSnapshotBrowseResult>(
    await api<unknown>(`${restoreSnapshotDirectoryBase}/${directoryId}/browse/?${qs}`, {
      ...init,
      headers: orgHeaders(),
    }),
  )
}

export async function getRestoreSnapshotPathInfo(
  directoryId: number,
  params: { target_node_id: number; path: string },
  init?: RequestInit,
) {
  const qs = query(params as Record<string, string | number | boolean | undefined>)
  return unwrapApiPayload<RestoreSnapshotPathInfo>(
    await api<unknown>(`${restoreSnapshotDirectoryBase}/${directoryId}/path-info/?${qs}`, {
      ...init,
      headers: orgHeaders(),
    }),
  )
}
