import { api, getLocale } from './api'
import { getCorrelationHeaders } from './requestContext'
import { getEffectiveOrgKey } from '../composables/useAuth'
import { unwrapApiPayload, asList } from './parse'

const API_BASE = import.meta.env.VITE_API_BASE?.toString() || ''

export type AuditLogRow = {
  id: number
  timestamp?: string
  created_at?: string
  organization?: number
  organization_name?: string
  user?: number | null
  user_display?: string
  user_email?: string
  user_name?: string
  action: string
  resource_type?: string
  resource_id?: string
  resource_name?: string
  target_type?: string
  target_id?: string
  result?: string
  error_message?: string
  error_code?: string
  ip_address?: string | null
  request_method?: string
  request_path?: string
  correlation_id?: string
  request_id?: string
  details?: string
  changes?: Record<string, unknown>
  request_query?: Record<string, unknown>
  request_body?: Record<string, unknown>
  user_agent?: string
  session_id?: string
  metadata?: Record<string, unknown>
}

type Paged<T> = { count: number; results: T[] }

function paged<T>(raw: unknown): Paged<T> {
  const data = unwrapApiPayload<Record<string, unknown>>(raw)
  return { count: typeof data.count === 'number' ? data.count : 0, results: asList<T>(data) }
}

const base = '/api/v1/audits'

export async function listAuditLogs(params?: Record<string, string | number>) {
  const qs = new URLSearchParams()
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== '' && v !== undefined) qs.set(k, String(v))
    }
  }
  const path = qs.toString() ? `${base}/?${qs}` : `${base}/`
  return paged<AuditLogRow>(await api<unknown>(path))
}

export async function getAuditLog(id: number) {
  return unwrapApiPayload<AuditLogRow>(await api<unknown>(`${base}/${id}/`))
}

export async function auditStatistics() {
  return unwrapApiPayload<{
    total_count: number
    today_count: number
  }>(await api<unknown>(`${base}/statistics/`))
}

export function auditExportUrl(format: 'json' | 'csv' = 'json', params?: Record<string, string>) {
  const qs = new URLSearchParams({ export_format: format, ...(params || {}) })
  return `${API_BASE}${base}/export/?${qs}`
}

export async function exportAuditLogs(
  format: 'json' | 'csv' = 'json',
  params?: Record<string, string>,
) {
  const qs = new URLSearchParams({
    export_format: format,
    org: getEffectiveOrgKey() || '',
    ...(params || {}),
  })
  const res = await fetch(`${API_BASE}${base}/export/?${qs}`, {
    credentials: 'include',
    headers: getCorrelationHeaders({
      'Accept-Language': getLocale(),
    }),
  })
  if (!res.ok) throw new Error(`export failed: ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `audit_logs.${format}`
  a.click()
  URL.revokeObjectURL(url)
}
