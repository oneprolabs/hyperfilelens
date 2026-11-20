import { api } from './api'
import { unwrapApiPayload } from './parse'
import { PERSONAL_USER_QUOTA_METERS } from './personalUserQuota'

export const USER_USAGE_METERS = [
  'max_source_hosts', 'max_source_nas', 'max_proxies', 'max_object_storage',
  'max_target_nas', 'max_standalone_disk', 'max_protected_sources',
  'max_storage_bytes', 'max_gateways', 'max_public_gateway_capacity_bytes',
  'ai_tokens', 'max_alert_policies',
] as const

export interface UserUsageItem {
  key: string
  unit: 'count' | 'bytes' | 'tokens'
  limit: number
  limit_source: 'default_unlimited' | 'personal_default'
  used: number | null
  known_used: number
  status: 'complete' | 'partial' | 'unknown'
  reason: string
  measured_at?: string | null
}
export interface UserUsage {
  org_key: string
  user_id: number
  generated_at: string
  limit_scope: 'organization_user'
  items: UserUsageItem[]
}

/** Fail closed: a malformed or cross-context response is not a zero-usage view. */
export function validUserUsage(data: UserUsage, orgKey: string, userId: number): boolean {
  if (!data || data.org_key !== orgKey || data.user_id !== userId
    || data.limit_scope !== 'organization_user' || !Array.isArray(data.items)
    || data.items.length !== USER_USAGE_METERS.length) return false
  const remaining = new Set<string>(USER_USAGE_METERS)
  const nonnegative = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0
  return data.items.every(item => {
    if (!item || !remaining.delete(item.key) || typeof item.reason !== 'string'
      || !nonnegative(item.known_used)) return false
    const finitePersonalLimit = item.limit_source === 'personal_default'
      && PERSONAL_USER_QUOTA_METERS.some(key => key === item.key)
      && Number.isSafeInteger(item.limit) && item.limit >= 0
    if (!(item.limit === -1 && item.limit_source === 'default_unlimited') && !finitePersonalLimit) return false
    const unit = item.key.endsWith('_bytes') ? 'bytes' : item.key === 'ai_tokens' ? 'tokens' : 'count'
    if (item.unit !== unit) return false
    if (item.status === 'complete') return nonnegative(item.used) && item.used === item.known_used
    return ['partial', 'unknown'].includes(item.status) && item.used === null
  })
}

export async function fetchUserUsage(userId: number | undefined, signal: AbortSignal): Promise<UserUsage> {
  const path = userId === undefined
    ? '/api/v1/subscription/my-usage/'
    : `/api/v1/subscription/member-usage/${userId}/`
  return unwrapApiPayload<UserUsage>(await api(path, { signal }))
}
