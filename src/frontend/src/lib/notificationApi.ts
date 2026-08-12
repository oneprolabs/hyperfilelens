import { api } from './api'
import { unwrapApiPayload, asList } from './parse'

export type NotificationChannelConfig = Record<string, unknown>

export type NotificationChannel = {
  id: number
  organization?: number
  organizationName?: string
  name: string
  type: string
  enabled: boolean
  config?: NotificationChannelConfig
  policiesCount?: number
  policies_count?: number
  lastDeliveryStatus?: string | null
  last_delivery_status?: string | null
  lastDeliveryAt?: string | null
  last_delivery_at?: string | null
  createdAt?: string
  created_at?: string
  updatedAt?: string
  updated_at?: string
}

type NotificationChannelPayload = Omit<NotificationChannel, 'type' | 'enabled'> &
  Partial<Pick<NotificationChannel, 'type' | 'enabled'>> & {
    channel_type?: string
    is_active?: boolean
  }

function normalizeChannel(payload: NotificationChannelPayload): NotificationChannel {
  return {
    ...payload,
    type: payload.type ?? payload.channel_type ?? '',
    enabled: payload.enabled ?? payload.is_active ?? false,
  }
}

export type NotificationChannelDetailsChannel = NotificationChannel

export type NotificationAssociatedPolicy = {
  id: string
  name: string
  description?: string
  enabled?: boolean
  created_at?: string
}

export type NotificationChannelRecentAlert = {
  id: string
  title?: string
  message?: string
  severity?: string
  status?: string
  created_at?: string
}

export type NotificationChannelDetailsStats = {
  policies_count?: number
  alerts_count?: number
  logs_count?: number
  logs_success?: number
  logs_failed?: number
  success_rate?: number
  last_success_at?: string | null
  last_failed_at?: string | null
}

export type NotificationChannelDetails = {
  channel?: NotificationChannelDetailsChannel
  associated_policies?: NotificationAssociatedPolicy[]
  recent_alerts?: NotificationChannelRecentAlert[]
  notification_logs?: NotificationLog[]
  stats?: NotificationChannelDetailsStats
}

export type NotificationLog = {
  id: string
  channelId?: number
  channel_id?: number
  alertRecordId?: string
  alert_record_id?: string
  notificationType?: string
  notification_type?: string
  status: string
  errorMessage?: string
  error_message?: string
  sentAt?: string
  sent_at?: string
  channel?: { id: number; name: string; type: string; enabled: boolean }
  alert?: Record<string, unknown>
  policy?: Record<string, unknown>
}

export type UserNotification = {
  id: string
  kind: string
  title: string
  summary?: string
  severity: string
  occurred_at?: string
  updated_at?: string
  is_read: boolean
  to: string
}

export type UserNotificationPage = {
  count: number
  unread_count: number
  results: UserNotification[]
}

type Paged<T> = { count: number; results: T[] }

function paged<T>(raw: unknown): Paged<T> {
  const data = unwrapApiPayload<Record<string, unknown>>(raw)
  return { count: typeof data.count === 'number' ? data.count : 0, results: asList<T>(data) }
}

const base = '/api/v1/notifications'

export async function listUserNotifications(pageSize = 10, page = 1) {
  return unwrapApiPayload<UserNotificationPage>(
    await api<unknown>(`${base}/inbox/?page=${page}&page_size=${pageSize}`),
  )
}

export async function markUserNotificationRead(id: string) {
  await api(`${base}/inbox/${encodeURIComponent(id)}/read/`, {
    method: 'POST',
    body: '{}',
  })
}

export async function markAllUserNotificationsRead() {
  await api(`${base}/inbox/mark-all-read/`, { method: 'POST', body: '{}' })
}

export async function listChannels(
  params?: Record<string, string | number>,
  options?: { signal?: AbortSignal },
) {
  const qs = new URLSearchParams()
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== '' && v !== undefined) qs.set(k, String(v))
    }
  }
  const path = qs.toString() ? `${base}/channels/?${qs}` : `${base}/channels/`
  const result = paged<NotificationChannelPayload>(await api<unknown>(path, { signal: options?.signal }))
  return { ...result, results: result.results.map(normalizeChannel) }
}

export async function createChannel(body: Record<string, unknown>) {
  return normalizeChannel(unwrapApiPayload<NotificationChannelPayload>(
    await api<unknown>(`${base}/channels/`, { method: 'POST', body: JSON.stringify(body) }),
  ))
}

export async function updateChannel(id: number, body: Record<string, unknown>) {
  return normalizeChannel(unwrapApiPayload<NotificationChannelPayload>(
    await api<unknown>(`${base}/channels/${id}/`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  ))
}

export async function getChannel(id: number) {
  return normalizeChannel(unwrapApiPayload<NotificationChannelPayload>(
    await api<unknown>(`${base}/channels/${id}/`),
  ))
}

export async function deleteChannel(id: number) {
  await api(`${base}/channels/${id}/`, { method: 'DELETE' })
}

export type NotificationBulkFailure = {
  id: number
  reason: string
}

export type NotificationBulkStateResult = {
  updated: number[]
  is_active: boolean
  failed: NotificationBulkFailure[]
}

export type NotificationBulkDeleteResult = {
  deleted: number[]
  failed: NotificationBulkFailure[]
}

export async function bulkStateChannels(ids: number[], isActive: boolean) {
  return unwrapApiPayload<NotificationBulkStateResult>(
    await api<unknown>(`${base}/channels/bulk-state/`, {
      method: 'POST',
      body: JSON.stringify({ ids, is_active: isActive }),
    }),
  )
}

export async function bulkDeleteChannels(ids: number[]) {
  return unwrapApiPayload<NotificationBulkDeleteResult>(
    await api<unknown>(`${base}/channels/bulk-delete/`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
  )
}

export async function testChannel(id: number) {
  return unwrapApiPayload<{ status: string; error?: string }>(
    await api<unknown>(`${base}/channels/${id}/test/`, { method: 'POST', body: '{}' }),
  )
}

export async function getChannelDetails(id: number) {
  const result = unwrapApiPayload<NotificationChannelDetails>(
    await api<unknown>(`${base}/channels/${id}/details/`),
  )
  return result.channel
    ? { ...result, channel: normalizeChannel(result.channel as NotificationChannelPayload) }
    : result
}

export type NotificationChannelAssociationPolicy = {
  id: string
  name: string
  enabled?: boolean
}

export type NotificationChannelAssociationItem = {
  id: number
  name: string
  type: string
  policies_count: number
  policies: NotificationChannelAssociationPolicy[]
}

export type NotificationChannelAssociationSummary = {
  items: NotificationChannelAssociationItem[]
  missing: number[]
}

export async function getChannelAssociationSummary(ids: number[]) {
  const unique = Array.from(new Set(ids.filter((v) => Number.isFinite(v)))).map((v) => Number(v))
  if (!unique.length) {
    return { items: [], missing: [] } as NotificationChannelAssociationSummary
  }
  const qs = new URLSearchParams({ ids: unique.join(',') })
  return unwrapApiPayload<NotificationChannelAssociationSummary>(
    await api<unknown>(`${base}/channels/association-summary/?${qs}`),
  )
}

export type NotificationChannelStatistics = {
  total: number
  enabled: number
  disabled: number
  enabled_rate: number
  failed_recent?: number
  sent_recent?: number
}

export async function channelStatistics(
  params?: Record<string, string | number>,
  options?: { signal?: AbortSignal },
) {
  const qs = new URLSearchParams()
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== '' && v !== undefined) qs.set(k, String(v))
    }
  }
  const path = qs.toString() ? `${base}/channels/stats/?${qs}` : `${base}/channels/stats/`
  return unwrapApiPayload<NotificationChannelStatistics>(
    await api<unknown>(path, { signal: options?.signal }),
  )
}

export async function listLogs(params?: Record<string, string | number>) {
  const qs = new URLSearchParams()
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== '' && v !== undefined) qs.set(k, String(v))
    }
  }
  const path = qs.toString() ? `${base}/logs/?${qs}` : `${base}/logs/`
  return paged<NotificationLog>(await api<unknown>(path))
}

export async function logStats(params?: Record<string, string | number>) {
  const qs = new URLSearchParams()
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== '' && v !== undefined) qs.set(k, String(v))
    }
  }
  const path = qs.toString() ? `${base}/logs/stats/?${qs}` : `${base}/logs/stats/`
  return unwrapApiPayload<{
    total: number
    success: number
    failed: number
    success_rate: number
    channels: number
  }>(await api<unknown>(path))
}
