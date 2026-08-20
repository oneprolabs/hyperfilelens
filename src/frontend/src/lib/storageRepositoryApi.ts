import { getEffectiveOrgKey } from '../composables/useAuth'
import { api, apiErrorMessage, apiErrorMessageI18n } from './api'
import { normalizeThrownError } from './errors'
import { logger } from './logger'
import { asList, asPagination, unwrapApiPayload } from './parse'
import type { TaskRow } from './taskApi'

export type StorageRepositoryType = 's3' | 'nas' | 'proxy_fs'
export type StorageRepositoryStatus =
  | 'creating'
  | 'create_failed'
  | 'created'
  | 'removing'
  | 'remove_failed'
  | 'removed'
export type StorageRepositoryHealth = 'online' | 'offline' | 'unverified'
export type StorageRepositoryInitializationState =
  | 'not_initialized'
  | 'initializing'
  | 'ready'
  | 'attention_required'
  | 'released'
  | 'unverified'
export type StorageRepositoryNasProtocol = 'smb' | 'nfs'
export type StorageRepositoryBindNodeType = 'proxy'
export type StorageRepositoryS3Platform = string
export type StorageRepositoryS3BucketMode = 'existing' | 'new'

export function storageRepositoryS3BucketMode(
  mode: 'existing' | 'new',
): StorageRepositoryS3BucketMode {
  return mode
}
export type StorageRepositoryCrossProxyAccess = {
  enabled: boolean
  ready: boolean
  host: string | null
  reason: 'ready' | 'not_applicable' | 'feature_disabled' | 'proxy_missing' | 'proxy_offline' | 'host_missing' | string
}

export type StorageRepository = {
  id: number
  organization_id: number
  name: string
  repo_type: StorageRepositoryType | string
  status: StorageRepositoryStatus | string
  health: StorageRepositoryHealth | string
  health_failures?: number
  initialization_state?: StorageRepositoryInitializationState | string
  initialized_target_count?: number
  config?: Record<string, unknown>
  credential_id?: number | null
  s3_platform?: StorageRepositoryS3Platform | string | null
  s3_bucket?: string | null
  s3_bucket_mode?: StorageRepositoryS3BucketMode
  capacity_bytes: number
  estimated_usage_bytes: number
  physical_usage_bytes?: number | null
  storage_total_bytes?: number
  storage_used_bytes?: number
  storage_available_bytes?: number
  storage_pool_key?: string
  storage_mount_point?: string
  last_checked_at?: string | null
  usage_probe_status?: string
  capacity_probe_status?: string
  usage_last_success_at?: string | null
  capacity_last_success_at?: string | null
  removed_at?: string | null
  cleanup_result?: 'deleted' | 'force_skipped' | 'preserved' | string
  created_at?: string | null
  updated_at?: string | null
  nas_protocol?: StorageRepositoryNasProtocol | string | null
  bind_node_type?: StorageRepositoryBindNodeType | string | null
  bind_node_id?: number | string | null
  bind_node_display_name?: string | null
  bind_node_ip?: string | null
  cross_proxy_access?: StorageRepositoryCrossProxyAccess
  active_cleanup_task?: Pick<TaskRow, 'task_uuid' | 'status' | 'error_code' | 'error_message' | 'created_at'> | null
  active_create_task?: (Pick<TaskRow, 'task_uuid' | 'status' | 'error_code' | 'error_message' | 'created_at'> & {
    operation_type?: string
  }) | null
}

export type StorageRepositoryAssociatedSource = {
  backup_config_id: number
  backup_config_name: string
  source_type: 'agent' | 'nas' | string
  source_ref_id: number
  source_name: string
  source_kind: 'host' | 'nas' | string
  status: 'online' | 'reconnecting' | 'offline' | string
  availability: 'online' | 'offline' | string
  platform?: string | null
  hostname?: string | null
  node_name?: string | null
  node_ip?: string | null
  protocol?: string | null
  connection_uri?: string | null
  bound_node_id?: number | null
  mount_status?: string | null
  mount_point?: string | null
  registered_at: string | null
  nas_location?: string | null
  repository_subdir?: string | null
  repository_mount_point?: string | null
  health: StorageRepositoryHealth | string
  probe_status?: string | null
  last_checked_at?: string | null
  last_success_checked_at?: string | null
  last_error?: string | null
}

export type StorageRepositoryCreatePayload = {
  name: string
  repo_type: StorageRepositoryType
  config?: Record<string, unknown>
  s3_platform?: StorageRepositoryS3Platform
  s3_bucket?: string
  s3_bucket_mode?: StorageRepositoryS3BucketMode
  nas_protocol?: StorageRepositoryNasProtocol
  bind_node_type?: StorageRepositoryBindNodeType
  bind_node_id?: number
}

/**
 * PATCH payload for editing an existing storage repository.
 * Only the following are mutable after creation:
 *   - name (display name)
 *   - config.s3_url_style / use_tls / quota_gb / quota_alert_*
 *   - config.access_key_id / secret_access_key (omitting keeps the existing value)
 * repo_type, s3_platform, s3_bucket, config.endpoint, config.region and
 * config.prefix are rejected by the backend when present.
 */
export type StorageRepositoryUpdatePayload = {
  name?: string
  config?: {
    region?: string
    s3_url_style?: 'auto' | 'virtual_hosted' | 'path'
    use_tls?: boolean
    quota_gb?: number
    quota_unit?: 'GB' | 'TB' | 'PB'
    quota_alert_enabled?: boolean
    quota_alert_threshold?: number
    access_key_id?: string
    secret_access_key?: string
    proxy_repository_server_host?: string
    [key: string]: unknown
  }
}

export type StorageRepositoryUpdateResult = {
  repository: StorageRepository
  task: TaskRow | null
}

/**
 * PATCH payload for the NAS repair endpoint.
 *   - `name`                : rename the repository
 *   - `config`              : partial config merge (mount_options / quota_* / smb_*)
 *   - `bind_node_id`        : null = unbind (only valid on unbound repos),
 *                             number = bind/swap a Proxy,
 *                             omit = leave binding unchanged
 *   - `smb_password` empty  : keep current password
 */
export type StorageRepositoryRepairConfig = {
  mount_options?: string
  quota_gb?: number
  quota_unit?: 'GB' | 'TB' | 'PB'
  quota_alert_enabled?: boolean
  quota_alert_threshold?: number
  smb_username?: string
  smb_password?: string
  smb_domain?: string
  proxy_repository_server_host?: string
  [key: string]: unknown
}

export type StorageRepositoryRepairPayload = {
  name?: string
  config?: StorageRepositoryRepairConfig
  bind_node_id?: number | null
}

export type StorageRepositoryListParams = {
  repo_type?: StorageRepositoryType
  status?: StorageRepositoryStatus
  health?: StorageRepositoryHealth
  search?: string
  search_field?: string
  page?: number
  page_size?: number
}

export type StorageRepositoryAssociatedSourcesParams = {
  page?: number
  page_size?: number
}

export type StorageRepositoryTasksParams = {
  search?: string
  search_field?: 'name' | 'uuid'
  operation_type?: string
  status?: string
  trigger_type?: string
  created_after?: string
  created_before?: string
  page?: number
  page_size?: number
}

export type StorageRepositoryCleanupBlocker = {
  code: string
  detail: string
  count?: number
  task_id?: number
  task_uuid?: string
  task_type?: string
}

export type StorageRepositoryCleanupPreflight = {
  allowed: boolean
  force: boolean
  repository_id: number
  repository_status: string
  associated_source_count: number
  active_snapshot_count: number
  blockers: StorageRepositoryCleanupBlocker[]
  warnings: StorageRepositoryCleanupBlocker[]
  targets: Array<{
    id: number
    target_key: string
    repository_subdir: string
    owner_type: string
    owner_node_id?: number | null
    owner_node_name?: string
    owner_online: boolean
    is_active: boolean
  }>
  active_cleanup_task?: TaskRow | null
}

export type PagedResult<T> = {
  count: number
  results: T[]
}

const repositoryBase = '/api/v1/storage/repositories'

function orgHeaders(): Record<string, string> {
  return { 'X-Org-Key': getEffectiveOrgKey() || '' }
}

function query(params?: StorageRepositoryListParams) {
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

export async function listStorageRepositories(params?: StorageRepositoryListParams, init?: RequestInit) {
  const qs = query(params)
  const path = qs ? `${repositoryBase}/?${qs}` : `${repositoryBase}/`
  return paged<StorageRepository>(await api<unknown>(path, { ...init, headers: orgHeaders() }))
}

export async function listAllStorageRepositories(
  params?: StorageRepositoryListParams,
  init?: RequestInit,
) {
  const pageSize = Math.max(1, Number(params?.page_size || 10))
  const firstPage = await listStorageRepositories(
    { ...params, page: 1, page_size: pageSize },
    init,
  )
  const rows = [...firstPage.results]
  let page = 1

  while (rows.length < firstPage.count && firstPage.results.length > 0) {
    page += 1
    const nextPage = await listStorageRepositories(
      { ...params, page, page_size: pageSize },
      init,
    )
    if (!nextPage.results.length) break
    rows.push(...nextPage.results)
    if (page > 1000) break
  }

  const unique = new Map<number, StorageRepository>()
  for (const row of rows) unique.set(row.id, row)
  return [...unique.values()]
}

export async function getStorageRepository(id: number, init?: RequestInit) {
  return unwrapApiPayload<StorageRepository>(
    await api<unknown>(`${repositoryBase}/${id}/`, { ...init, headers: orgHeaders() }),
  )
}

export async function listStorageRepositoryAssociatedSources(
  id: number,
  params?: StorageRepositoryAssociatedSourcesParams,
  init?: RequestInit,
) {
  const qs = query(params)
  const path = `${repositoryBase}/${id}/associated-sources/${qs ? `?${qs}` : ''}`
  const raw = unwrapApiPayload<{ results?: StorageRepositoryAssociatedSource[], count?: number }>(
    await api<unknown>(path, { ...init, headers: orgHeaders() }),
  )
  return {
    count: Number(raw.count ?? raw.results?.length ?? 0),
    results: Array.isArray(raw.results) ? raw.results : [],
  }
}

export async function listStorageRepositoryTasks(
  id: number,
  params?: StorageRepositoryTasksParams,
  init?: RequestInit,
) {
  const qs = query(params)
  const path = `${repositoryBase}/${id}/tasks/${qs ? `?${qs}` : ''}`
  return paged<TaskRow>(await api<unknown>(path, { ...init, headers: orgHeaders() }))
}

export async function cancelStorageRepositoryTask(taskUuid: string, reason?: string) {
  return unwrapApiPayload<TaskRow>(
    await api<unknown>(`/api/v1/storage/repository-tasks/${taskUuid}/cancel/`, {
      method: 'POST',
      body: JSON.stringify({ reason: reason || '' }),
      headers: orgHeaders(),
    }),
  )
}

export async function preflightStorageRepositoryCleanup(id: number, force = false) {
  return unwrapApiPayload<StorageRepositoryCleanupPreflight>(
    await api<unknown>(`${repositoryBase}/${id}/cleanup/preflight/`, {
      method: 'POST',
      body: JSON.stringify({ force }),
      headers: orgHeaders(),
    }),
  )
}

export async function deleteStorageRepository(id: number, force = false) {
  return unwrapApiPayload<TaskRow>(
    await api<unknown>(`${repositoryBase}/${id}/`, {
      method: 'DELETE',
      body: JSON.stringify({
        force,
        confirmation: force ? 'FORCE CLEANUP' : '',
      }),
      headers: orgHeaders(),
    }),
  )
}

export async function createStorageRepository(payload: StorageRepositoryCreatePayload) {
  logger.info('storageRepositoryApi.ts', 157, 'storage repository create', {
    name: payload.name,
    repo_type: payload.repo_type,
    bind_node_id: payload.bind_node_id,
  })
  return unwrapApiPayload<StorageRepository>(
    await api<unknown>(`${repositoryBase}/`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

const GENERIC_REPOSITORY_EXIT_MESSAGE = /^(?:exit\s+\d+\s*:\s*)?(?:exit status\s+\d+|exit\s+\d+)$/i

export function storageRepositoryCreateErrorMessage(
  err: unknown,
  t: (key: string) => string,
  fallback = 'Request failed',
): string {
  const normalized = normalizeThrownError(err)
  const resolved = apiErrorMessageI18n(err, t, fallback)
  if ((normalized.errorCode || normalized.code) !== 'VALIDATION.FAILED') return resolved
  const hasFieldError = Object.values(normalized.fields || {})
    .some(messages => messages.some(message => message.trim()))
  if (hasFieldError) return resolved

  const diagnostic = stringifyDetail(normalized.meta?.diagnostic)
  if (!diagnostic || GENERIC_REPOSITORY_EXIT_MESSAGE.test(diagnostic)) return resolved
  return diagnostic
}

export async function updateStorageRepository(
  id: number,
  payload: StorageRepositoryUpdatePayload,
): Promise<StorageRepositoryUpdateResult> {
  logger.info('storageRepositoryApi.ts', 173, 'storage repository update', { repository_id: id, fields: Object.keys(payload) })
  const result = unwrapApiPayload<StorageRepository | { repository: StorageRepository, task: TaskRow }>(
    await api<unknown>(`${repositoryBase}/${id}/`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
  if ('repository' in result) {
    return { repository: result.repository, task: result.task }
  }
  return { repository: result, task: null }
}

/**
 * PATCH /api/v1/storage/repositories/{id}/repair/
 * Used by the NAS "repair" page. Only valid for NAS repositories.
 */
export async function repairStorageRepository(
  id: number,
  payload: StorageRepositoryRepairPayload,
) {
  logger.info('storageRepositoryApi.ts', 190, 'storage repository repair', { repository_id: id, fields: Object.keys(payload) })
  return unwrapApiPayload<StorageRepository>(
    await api<unknown>(`${repositoryBase}/${id}/repair/`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
      headers: orgHeaders(),
    }),
  )
}

export type StorageRepositoryRetryInitializationResult = {
  repository: StorageRepository
  task: TaskRow
}

export async function retryStorageRepositoryInitialization(id: number) {
  return unwrapApiPayload<StorageRepositoryRetryInitializationResult>(
    await api<unknown>(`${repositoryBase}/${id}/retry-initialization/`, {
      method: 'POST',
      body: JSON.stringify({}),
      headers: orgHeaders(),
    }),
  )
}

export async function releaseStorageRepositoryResidualLocation(id: number) {
  return unwrapApiPayload<StorageRepository>(
    await api<unknown>(`${repositoryBase}/${id}/release-residual-location/`, {
      method: 'POST',
      body: JSON.stringify({ confirmation: 'RELEASE LOCATION' }),
      headers: orgHeaders(),
    }),
  )
}

export type StorageRepositoryVerifyAccessResult = {
  ok: boolean
  bucket?: string
  probe_key?: string
  message?: string
}

/**
 * Optional draft overrides for verify-access. Endpoint, prefix and bucket are
 * always locked to the saved values; only the connection/auth fields below
 * may be overridden to verify a not-yet-saved edit.
 */
export type StorageRepositoryVerifyAccessOverrides = {
  region?: string
  s3_url_style?: 'auto' | 'virtual_hosted' | 'path'
  use_tls?: boolean
  access_key_id?: string
  secret_access_key?: string
}

/**
 * Verify that the saved S3 credentials can read/write/delete a probe object in
 * the bucket. Returns {ok: true, bucket, probe_key} on success, or
 * a stable, user-facing message on failure.
 */
export async function verifyStorageRepositoryAccess(
  id: number,
  overrides: StorageRepositoryVerifyAccessOverrides = {},
  init?: RequestInit,
): Promise<StorageRepositoryVerifyAccessResult> {
  logger.info('storageRepositoryApi.ts', 237, 'storage repository verify access', { repository_id: id })
  try {
    const data = await api<{ ok?: boolean; bucket?: string; probe_key?: string; detail?: string | unknown[] }>(
      `${repositoryBase}/${id}/verify-access/`,
      {
        method: 'POST',
        body: JSON.stringify(overrides),
        headers: orgHeaders(),
        ...init,
      },
    )
    if (data?.ok === false) {
      return { ok: false, message: 'Object storage validation failed. Check the connection settings and IAM permissions, then try again.' }
    }
    return { ok: true, bucket: data?.bucket, probe_key: data?.probe_key }
  } catch (err) {
    return {
      ok: false,
      message: apiErrorMessage(err, 'Object storage validation failed. Check the connection settings and IAM permissions, then try again.'),
    }
  }
}

function stringifyDetail(detail: unknown): string | undefined {
  if (detail == null) return undefined
  if (typeof detail === 'string') {
    const t = detail.trim()
    return t ? t : undefined
  }
  if (Array.isArray(detail)) {
    for (const part of detail) {
      const text = stringifyDetail(part)
      if (text) return text
    }
    return undefined
  }
  if (typeof detail === 'object') {
    const obj = detail as Record<string, unknown>
    for (const key of ['detail', 'message', 'error']) {
      const text = stringifyDetail(obj[key])
      if (text) return text
    }
  }
  return undefined
}
