import { api } from './api'
import { unwrapApiPayload, asList } from './parse'
import { getEffectiveOrgKey } from '../composables/useAuth'

export type LicenseRecord = {
  id: string
  license_key: string
  version?: number
  change_type?: string
  machine_code?: string
  organization?: number
  organization_name?: string
  organization_key?: string
  max_organizations?: number
  max_users?: number
  max_nodes?: number
  max_storage_gb?: number
  max_gateways?: number
  ai_insights_quota?: number
  max_tasks?: number
  max_alert_policies?: number
  issued_at?: string
  expires_at?: string | null
  activated_at?: string
  status?: string
  is_valid?: boolean
  is_perpetual?: boolean
  days_until_expiry?: number
}

export type LicenseHistoryRow = {
  id: string
  license_key: string
  change_type?: string
  change_reason?: string
  archived_at?: string
  activated_at?: string
  expires_at?: string | null
  is_perpetual?: boolean
  status?: string
  max_users?: number
  max_nodes?: number
  max_storage_gb?: number
  limits?: Record<string, number>
}

export type LicenseUsage = Record<string, number>

export type EffectiveQuotaUsage = {
  key: string
  limit: number
  unit: string
  used: number
  plan_key: string
  plan_limit: number
  override_limit: number | null
  limit_source: 'plan' | 'override'
  overridden: boolean
  remaining: number | null
  usage_percent: number | null
  usage_status:
    | 'ok'
    | 'warning'
    | 'exhausted'
    | 'exceeded'
    | 'unlimited'
    | 'unknown'
    | 'not_applicable'
}

export type EffectiveQuotaPayload = {
  organization_id: number
  organization_key: string
  quota_usage: EffectiveQuotaUsage[]
}

const base = '/api/v1/subscription/licenses'

function orgHeaders(): Record<string, string> {
  return { 'X-Org-Key': getEffectiveOrgKey() || '' }
}

export async function fetchCurrentLicense() {
  return unwrapApiPayload<{
    is_valid: boolean
    message?: string
    license?: LicenseRecord
    limits?: Record<string, number>
    usage?: LicenseUsage
    machine_code?: string
    organization_name?: string
    days_until_expiry?: number
    enforcement_enabled?: boolean
    entitlement_source?: 'builtin_unlimited' | 'license' | 'license_inactive'
    instance_shared?: boolean
    can_manage_instance_license?: boolean
  }>(await api<unknown>(`${base}/current/`, { headers: orgHeaders() }))
}

export async function fetchMachineCode(regenerate = false) {
  const path = `${base}/machine_code/`
  const init = regenerate
    ? { method: 'POST' as const, headers: orgHeaders() }
    : { headers: orgHeaders() }
  return unwrapApiPayload<{ machine_code: string; organization_name?: string; message?: string }>(
    await api<unknown>(path, init),
  )
}

export async function activateLicense(activation_code: string) {
  return unwrapApiPayload<{ success: boolean; change_type?: string; license: LicenseRecord }>(
    await api<unknown>(`${base}/activate/`, {
      method: 'POST',
      body: JSON.stringify({ activation_code }),
      headers: orgHeaders(),
    }),
  )
}

export async function fetchLicenseHistory() {
  const data = unwrapApiPayload<{ count: number; results: LicenseHistoryRow[] }>(
    await api<unknown>(`${base}/history/`, { headers: orgHeaders() }),
  )
  return data.results ?? asList<LicenseHistoryRow>(data)
}

export async function fetchEffectiveQuotaUsage() {
  return unwrapApiPayload<EffectiveQuotaPayload>(
    await api<unknown>('/api/v1/subscription/quotas/effective/', {
      headers: orgHeaders(),
    }),
  )
}
