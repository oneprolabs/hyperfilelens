import { api } from './api'
import { unwrapApiPayload, asList } from './parse'

export type AlertPolicy = {
  id: string
  organization?: number
  name: string
  description?: string
  type: string
  severity: string
  enabled: boolean
  resourceType?: string
  resource_type?: string
  scope: string
  resourceIds?: string[]
  resource_ids?: string[]
  monitoringResources?: AlertPolicyResource[]
  monitoring_resources?: AlertPolicyResource[]
  triggerRule?: Record<string, unknown>
  trigger_rule?: Record<string, unknown>
  recoveryRule?: Record<string, unknown>
  recovery_rule?: Record<string, unknown>
  notificationChannelIds?: number[]
  notification_channel_ids?: number[]
  notificationChannels?: Array<{ id: number; name: string; type: string; enabled: boolean }>
  notification_channels?: Array<{ id: number; name: string; type: string; enabled: boolean }>
  createdAt?: string
  created_at?: string
  updatedAt?: string
  updated_at?: string
}

export type AlertPolicyResource = {
  id: string
  name?: string
  status?: string
  available?: boolean
}

export type AlertRecord = {
  id: string
  organization?: number
  policyId?: string | null
  policy_id?: string | null
  type: string
  severity: string
  status: string
  resourceType?: string
  resource_type?: string
  resourceId?: string
  resource_id?: string
  resourceName?: string
  resource_name?: string
  title: string
  message?: string
  currentValue?: number | null
  current_value?: number | null
  thresholdValue?: number | null
  threshold_value?: number | null
  unit?: string
  durationSeconds?: number | null
  duration_seconds?: number | null
  firstTriggeredAt?: string
  first_triggered_at?: string
  lastTriggeredAt?: string
  last_triggered_at?: string
  createdAt?: string
  created_at?: string
}

type Paged<T> = { count: number; results: T[] }

function paged<T>(raw: unknown): Paged<T> {
  const data = unwrapApiPayload<Record<string, unknown>>(raw)
  const results = asList<T>(data)
  const count = typeof data.count === 'number' ? data.count : results.length
  return { count, results }
}

const base = '/api/v1/alerts'

export async function listPolicies(params?: Record<string, string | number | boolean>) {
  const qs = new URLSearchParams()
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== '' && v !== undefined) qs.set(k, String(v))
    }
  }
  const path = qs.toString() ? `${base}/policies/?${qs}` : `${base}/policies/`
  return paged<AlertPolicy>(await api<unknown>(path))
}

export async function getPolicy(id: string) {
  return unwrapApiPayload<AlertPolicy>(await api<unknown>(`${base}/policies/${id}/`))
}

export async function createPolicy(body: Record<string, unknown>) {
  return unwrapApiPayload<AlertPolicy>(
    await api<unknown>(`${base}/policies/`, { method: 'POST', body: JSON.stringify(body) }),
  )
}

export async function updatePolicy(id: string, body: Record<string, unknown>) {
  return unwrapApiPayload<AlertPolicy>(
    await api<unknown>(`${base}/policies/${id}/`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  )
}

export async function deletePolicy(id: string) {
  await api(`${base}/policies/${id}/`, { method: 'DELETE' })
}

export async function enablePolicy(id: string) {
  return unwrapApiPayload<AlertPolicy>(
    await api<unknown>(`${base}/policies/${id}/enable/`, { method: 'POST', body: '{}' }),
  )
}

export async function disablePolicy(id: string) {
  return unwrapApiPayload<AlertPolicy>(
    await api<unknown>(`${base}/policies/${id}/disable/`, { method: 'POST', body: '{}' }),
  )
}

export type BulkPolicyStateResult = {
  updated: string[]
  failed: Array<{ id: string; reason: string }>
}

export type BulkPolicyDeleteResult = {
  deleted: string[]
  failed: Array<{ id: string; reason: string }>
}

export async function bulkStatePolicies(ids: string[], enabled: boolean) {
  return unwrapApiPayload<BulkPolicyStateResult>(
    await api<unknown>(`${base}/policies/bulk-state/`, {
      method: 'POST',
      body: JSON.stringify({ ids, enabled }),
    }),
  )
}

export async function bulkDeletePolicies(ids: string[]) {
  return unwrapApiPayload<BulkPolicyDeleteResult>(
    await api<unknown>(`${base}/policies/bulk-delete/`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
  )
}

export async function duplicatePolicy(id: string) {
  return unwrapApiPayload<AlertPolicy>(
    await api<unknown>(`${base}/policies/${id}/duplicate/`, { method: 'POST', body: '{}' }),
  )
}

export async function listRecords(params?: Record<string, string | number | boolean>) {
  const qs = new URLSearchParams()
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== '' && v !== undefined) qs.set(k, String(v))
    }
  }
  const path = qs.toString() ? `${base}/records/?${qs}` : `${base}/records/`
  return paged<AlertRecord>(await api<unknown>(path))
}

export async function acknowledgeRecord(id: string, note = '') {
  return unwrapApiPayload<AlertRecord>(
    await api<unknown>(`${base}/records/${id}/acknowledge/`, {
      method: 'POST',
      body: JSON.stringify({ note }),
    }),
  )
}

export async function resolveRecord(id: string, note = '') {
  return unwrapApiPayload<AlertRecord>(
    await api<unknown>(`${base}/records/${id}/resolve/`, {
      method: 'POST',
      body: JSON.stringify({ note }),
    }),
  )
}

export async function recordStatistics(params?: Record<string, string | number | boolean>) {
  const qs = new URLSearchParams()
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== '' && v !== undefined) qs.set(k, String(v))
    }
  }
  const path = qs.toString() ? `${base}/records/stats/?${qs}` : `${base}/records/stats/`
  return unwrapApiPayload<{
    total: number
    firing: number
    acknowledged: number
    resolved: number
    critical: number
    warning: number
    info: number
  }>(await api<unknown>(path))
}

export async function policyStatistics(params?: Record<string, string | number | boolean>) {
  const qs = new URLSearchParams()
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== '' && v !== undefined) qs.set(k, String(v))
    }
  }
  const path = qs.toString() ? `${base}/policies/stats/?${qs}` : `${base}/policies/stats/`
  return unwrapApiPayload<{
    total: number
    enabled: number
    disabled: number
    critical: number
    warning: number
    info: number
    enabled_rate: number
  }>(await api<unknown>(path))
}

export async function fetchMetadata(kind: string, params?: Record<string, string>) {
  const qs = new URLSearchParams(params || {})
  const path = qs.toString()
    ? `${base}/metadata/${kind}/?${qs}`
    : `${base}/metadata/${kind}/`
  return unwrapApiPayload<unknown>(await api<unknown>(path))
}

export async function fetchMetadataResources(resourceType: string) {
  return unwrapApiPayload<Array<{ id: string; name: string; status?: string }>>(
    await api<unknown>(`${base}/metadata/resources/?resource_type=${encodeURIComponent(resourceType)}`),
  )
}
