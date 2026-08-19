import { describe, expect, it } from 'vitest'

import {
  isValidRepositoryQuotaValue,
  normalizeRepositoryQuotaUnit,
  repositoryQuotaDisplay,
  repositoryQuotaToGb,
  repositoryQuotaValueFromGb,
} from './repositoryQuota'

describe('repository quota units', () => {
  it('normalizes supported units and treats missing or invalid legacy values as GB', () => {
    expect(normalizeRepositoryQuotaUnit('TB')).toBe('TB')
    expect(normalizeRepositoryQuotaUnit('pb')).toBe('PB')
    expect(normalizeRepositoryQuotaUnit(undefined)).toBe('GB')
    expect(normalizeRepositoryQuotaUnit('MB')).toBe('GB')
  })

  it('converts the unchanged input value to and from normalized GB', () => {
    expect(repositoryQuotaToGb(10, 'GB')).toBe(10)
    expect(repositoryQuotaToGb(10, 'TB')).toBe(10 * 1024)
    expect(repositoryQuotaToGb(10, 'PB')).toBe(10 * 1024 ** 2)
    expect(repositoryQuotaValueFromGb(10 * 1024, 'TB')).toBe(10)
    expect(repositoryQuotaValueFromGb(10 * 1024 ** 2, 'PB')).toBe(10)
  })

  it('formats persisted units while keeping legacy records in GB', () => {
    expect(repositoryQuotaDisplay({ quota_gb: 2048, quota_unit: 'TB' })).toBe('2 TB')
    expect(repositoryQuotaDisplay({ quota_gb: 2048 })).toBe('2048 GB')
    expect(repositoryQuotaDisplay({ quota_gb: 2048, quota_unit: 'invalid' })).toBe('2048 GB')
    expect(repositoryQuotaDisplay({ quota_gb: 0, quota_unit: 'PB' })).toBeNull()
  })

  it('allows zero for unlimited and positive integers only', () => {
    expect(isValidRepositoryQuotaValue(0)).toBe(true)
    expect(isValidRepositoryQuotaValue(10)).toBe(true)
    expect(isValidRepositoryQuotaValue(1.5)).toBe(false)
    expect(isValidRepositoryQuotaValue(-1)).toBe(false)
  })
})
