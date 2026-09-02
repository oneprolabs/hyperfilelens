import { describe, expect, it } from 'vitest'
import { repositoryMaintenanceSummaryFromMetadata } from './repositoryMaintenanceSummary'

describe('repository maintenance summary metadata', () => {
  it('normalizes the versioned event contract', () => {
    expect(repositoryMaintenanceSummaryFromMetadata({
      event_type: 'repository_maintenance_summary',
      maintenance_summary: {
        schema_version: 1,
        mode: 'full',
        source: 'maintenance_info',
        approximate: false,
        content_gc: { deleted_count: 55, deleted_bytes: 3_145_728 },
        pack_gc: { deleted_count: 10, deleted_bytes: 10_000 },
      },
    })).toMatchObject({
      mode: 'full',
      approximate: false,
      content_gc: { deleted_count: 55 },
      pack_gc: { deleted_bytes: 10_000 },
    })
  })

  it('preserves an absent physical Pack stage', () => {
    const summary = repositoryMaintenanceSummaryFromMetadata({
      event_type: 'repository_maintenance_summary',
      maintenance_summary: {
        schema_version: 1,
        mode: 'full',
        source: 'stderr',
        approximate: true,
        content_gc: { deferred_count: 7_214, deferred_bytes: 7_408_351_232 },
        pack_gc: null,
      },
    })

    expect(summary?.pack_gc).toBeNull()
    expect(summary?.approximate).toBe(true)
  })

  it('rejects unrelated and unsupported event metadata', () => {
    expect(repositoryMaintenanceSummaryFromMetadata({ event_type: 'other' })).toBeNull()
    expect(repositoryMaintenanceSummaryFromMetadata({
      event_type: 'repository_maintenance_summary',
      maintenance_summary: { schema_version: 2, mode: 'full' },
    })).toBeNull()
  })
})
