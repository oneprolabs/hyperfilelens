/** Shared Personal user policy contract. Values cross JSON as exact integers. */
export const PERSONAL_USER_QUOTA_METERS = [
  'max_storage_bytes', 'max_public_gateway_capacity_bytes', 'ai_tokens',
] as const
export type PersonalUserQuotaMeter = typeof PERSONAL_USER_QUOTA_METERS[number]
export type PersonalUserLimits = Record<PersonalUserQuotaMeter, number>
export interface PersonalUserPolicy {
  configured: boolean
  limits: Partial<PersonalUserLimits>
}
export const USER_CAPACITY_UNITS = [
  { label: 'B', value: 1 }, { label: 'MiB', value: 1024 ** 2 },
  { label: 'GiB', value: 1024 ** 3 }, { label: 'TiB', value: 1024 ** 4 },
] as const

export function validPersonalUserPolicy(value: PersonalUserPolicy): boolean {
  if (!value || typeof value.configured !== 'boolean' || !value.limits
    || typeof value.limits !== 'object' || Array.isArray(value.limits)) return false
  const keys = Object.keys(value.limits)
  if (!value.configured) return keys.length === 0
  return keys.length === PERSONAL_USER_QUOTA_METERS.length
    && PERSONAL_USER_QUOTA_METERS.every(key => {
      const limit = value.limits[key]
      return typeof limit === 'number' && Number.isSafeInteger(limit) && limit >= 0
    })
}

export function parseUserLimit(value: string, unit: number): number | undefined {
  if (!/^\d+$/.test(value.trim()) || !Number.isSafeInteger(unit) || unit <= 0) return
  const result = Number(value.trim()) * unit
  return Number.isSafeInteger(result) && result >= 0 ? result : undefined
}

export function splitUserCapacity(bytes: number): { value: string; unit: number } {
  // Keep arbitrary persisted byte values exact, including non-MiB multiples.
  const unit = bytes === 0 ? 1024 ** 3
    : [...USER_CAPACITY_UNITS].reverse().find(unit => bytes % unit.value === 0)!.value
  return { value: String(bytes / unit), unit }
}
