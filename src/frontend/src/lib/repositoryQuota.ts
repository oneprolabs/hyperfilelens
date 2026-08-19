export const REPOSITORY_QUOTA_UNITS = ['GB', 'TB', 'PB'] as const

export type RepositoryQuotaUnit = typeof REPOSITORY_QUOTA_UNITS[number]

const QUOTA_UNIT_GB_MULTIPLIER: Record<RepositoryQuotaUnit, number> = {
  GB: 1,
  TB: 1024,
  PB: 1024 ** 2,
}

export function normalizeRepositoryQuotaUnit(value: unknown): RepositoryQuotaUnit {
  const unit = String(value || '').toUpperCase()
  return REPOSITORY_QUOTA_UNITS.includes(unit as RepositoryQuotaUnit)
    ? unit as RepositoryQuotaUnit
    : 'GB'
}

export function repositoryQuotaToGb(value: unknown, unit: unknown): number {
  const amount = Number(value || 0)
  if (!Number.isFinite(amount) || amount <= 0) return 0
  return amount * QUOTA_UNIT_GB_MULTIPLIER[normalizeRepositoryQuotaUnit(unit)]
}

export function repositoryQuotaValueFromGb(quotaGb: unknown, unit: unknown): number {
  const amount = Number(quotaGb || 0)
  if (!Number.isFinite(amount) || amount <= 0) return 0
  return amount / QUOTA_UNIT_GB_MULTIPLIER[normalizeRepositoryQuotaUnit(unit)]
}

export function repositoryQuotaDisplay(config: {
  quota_gb?: number | string | null
  quota_unit?: unknown
} | null | undefined): string | null {
  const quotaGb = Number(config?.quota_gb || 0)
  if (!Number.isFinite(quotaGb) || quotaGb <= 0) return null
  const unit = normalizeRepositoryQuotaUnit(config?.quota_unit)
  return `${repositoryQuotaValueFromGb(quotaGb, unit)} ${unit}`
}

export function isValidRepositoryQuotaValue(value: unknown): boolean {
  const amount = Number(value || 0)
  return Number.isInteger(amount) && amount >= 0
}
