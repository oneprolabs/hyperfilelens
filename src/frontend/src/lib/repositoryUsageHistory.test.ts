import { describe, expect, it } from 'vitest'
import type { StorageRepositoryUsageHistoryPoint } from './storageRepositoryApi'
import { repositoryCapacitySeries } from './repositoryUsageHistory'

function point(recordedAt: string, usageBytes: number | null): StorageRepositoryUsageHistoryPoint {
  return {
    recorded_at: recordedAt,
    sampled_at: usageBytes == null ? null : recordedAt,
    usage_bytes: usageBytes,
    usage_source: usageBytes == null ? null : 'estimated',
    coverage: usageBytes == null ? 'missing' : 'complete',
  }
}

describe('repository usage history', () => {
  const points = [
    point('2026-08-21T10:00:00Z', 100),
    point('2026-08-21T10:15:00Z', null),
    point('2026-08-21T10:30:00Z', 160),
  ]

  it('keeps missing values as disconnected chart points', () => {
    expect(repositoryCapacitySeries(points)).toEqual([
      ['2026-08-21T10:00:00Z', 100],
      ['2026-08-21T10:15:00Z', null],
      ['2026-08-21T10:30:00Z', 160],
    ])
  })
})
