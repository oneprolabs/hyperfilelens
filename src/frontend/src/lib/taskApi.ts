import { api } from './api'
import { unwrapApiPayload, asList, asPagination } from './parse'
import type { TransferProgress } from './kopiaProgress'

export type TaskResourceRow = {
  resource_type: string
  resource_subtype?: string
  resource_id: number
  is_primary?: boolean
}

export type TaskStepRow = {
  id: number
  step_index: number
  step_name: string
  status: string
  progress: number | string
  created_at?: string
}

export type TaskEventRow = {
  id: number
  step_id?: number | null
  seq: number
  level: string
  message: string
  metadata?: unknown
  created_at?: string
}

export type TaskDependencyRow = {
  id: number
  blocking_task_uuid?: string | null
  blocking_task_status?: string | null
  reference_type: 'task' | 'node_task' | 'external' | string
  reference_id?: string
  reference_task_type?: string
  code: string
  detail: string
  auto_resumable: boolean
  is_active: boolean
  created_at?: string
  last_checked_at?: string | null
  next_check_at?: string | null
  resolved_at?: string | null
}

export type TaskActions = {
  can_cancel: boolean
  can_recheck: boolean
  can_retry: boolean
  can_force: boolean
}

export type TaskRow = {
  id: number
  organization_id: number | null
  task_uuid: string
  task_type: string
  display_name: string
  status: string
  progress: number | string
  current_step?: string | null
  retry_count: number
  recovery_attempt: number
  replaces_task_uuid?: string | null
  replacement_task_uuid?: string | null
  trigger_type: string
  group_uuid?: string | null
  request_payload?: unknown
  result_payload?: unknown
  transfer_progress?: TransferProgress | null
  operation_type?: string | null
  repository_owner?: { type: string; node_id?: number | null; identity: string } | null
  repository_target?: { id: number; key: string; repository_subdir?: string } | null
  repository_cleanup?: {
    force: boolean
    triggered_by_task_uuid?: string | null
    triggered_by_task_type?: string | null
  } | null
  repository_cancellation?: {
    supported: boolean
    requested_at?: string | null
  } | null
  error_code?: string | null
  error_message?: string | null
  started_at?: string | null
  finished_at?: string | null
  created_at?: string
  updated_at?: string
  resources?: TaskResourceRow[]
  primary_resource?: TaskResourceRow | null
  steps?: TaskStepRow[]
  recent_events?: TaskEventRow[]
  dependencies?: TaskDependencyRow[]
  actions?: TaskActions
}

export type TaskCreatePayload = {
  task_type: string
  display_name: string
  trigger_type?: string
  request_payload?: unknown
  resources?: TaskResourceRow[]
  steps?: Array<{ step_name: string; status?: string; progress?: number }>
}

export type TaskStatistics = {
  total: number
  running: number
  waiting?: number
  blocked?: number
  success: number
  failed: number
  cancelled: number
  timeout: number
  by_status?: Record<string, number>
  by_task_type?: Record<string, number>
}

export type Paged<T> = { count: number; results: T[] }

const base = '/api/v1/tasks'

function withQuery(path: string, params?: Record<string, string | number | undefined>) {
  const qs = new URLSearchParams()
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== '' && value !== undefined) qs.set(key, String(value))
  }
  const suffix = qs.toString()
  return suffix ? `${path}?${suffix}` : path
}

function paged<T>(raw: unknown): Paged<T> {
  const payload = unwrapApiPayload<Record<string, unknown>>(raw)
  const pagination = asPagination(raw) || asPagination(payload)
  const results = asList<T>(payload)
  const rawCount = pagination?.total ?? Number(payload.count)
  const count = rawCount || results.length
  return { count, results }
}

export async function listTasks(params?: Record<string, string | number | undefined>, init?: RequestInit) {
  return paged<TaskRow>(await api<unknown>(withQuery(`${base}/`, params), init))
}

export async function getTask(taskUuid: string, init?: RequestInit) {
  return unwrapApiPayload<TaskRow>(await api<unknown>(`${base}/${taskUuid}/`, init))
}

export async function taskStatistics(
  params?: Record<string, string | number | undefined>,
  init?: RequestInit,
) {
  return unwrapApiPayload<TaskStatistics>(await api<unknown>(withQuery(`${base}/statistics/`, params), init))
}

export async function listTaskSteps(taskUuid: string, init?: RequestInit) {
  return asList<TaskStepRow>(await api<unknown>(`${base}/${taskUuid}/steps/`, init))
}

export async function listTaskEvents(
  taskUuid: string,
  params?: Record<string, string | number | undefined>,
  init?: RequestInit,
) {
  const raw = await api<unknown>(withQuery(`${base}/${taskUuid}/events/`, params), init)
  return paged<TaskEventRow>(raw)
}

export async function createTask(payload: TaskCreatePayload) {
  return unwrapApiPayload<TaskRow>(
    await api<unknown>(`${base}/`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  )
}

export async function cancelTask(taskUuid: string, reason?: string) {
  return unwrapApiPayload<TaskRow>(
    await api<unknown>(`${base}/${taskUuid}/cancel/`, {
      method: 'POST',
      body: JSON.stringify({ reason: reason || '' }),
    }),
  )
}

export async function retryTask(taskUuid: string, reason?: string) {
  return unwrapApiPayload<TaskRow>(
    await api<unknown>(`${base}/${taskUuid}/retry/`, {
      method: 'POST',
      body: JSON.stringify({ reason: reason || '' }),
    }),
  )
}
