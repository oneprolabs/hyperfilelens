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

  it('normalizes known Quick stages without turning missing statistics into zero', () => {
    const summary = repositoryMaintenanceSummaryFromMetadata({
      event_type: 'repository_maintenance_summary',
      maintenance_summary: {
        schema_version: 1,
        mode: 'quick',
        source: 'maintenance_info',
        approximate: false,
        content_gc: null,
        pack_gc: null,
        stages: [
          {
            type: 'content_rewrite',
            status: 'completed',
            statistics_available: true,
            metrics: { rewritten_count: 0, rewritten_bytes: 0 },
          },
          {
            type: 'pack_gc',
            status: 'not_run',
            statistics_available: false,
            metrics: null,
          },
          {
            type: 'index_compaction',
            status: 'completed',
            statistics_available: false,
            metrics: null,
          },
          {
            type: 'future_stage',
            status: 'completed',
            statistics_available: true,
            metrics: { secret: 42 },
          },
        ],
      },
    })

    expect(summary?.stages).toEqual([
      {
        type: 'content_rewrite',
        status: 'completed',
        statistics_available: true,
        metrics: { rewritten_count: 0, rewritten_bytes: 0 },
      },
      {
        type: 'pack_gc',
        status: 'not_run',
        statistics_available: false,
        metrics: null,
      },
      {
        type: 'index_compaction',
        status: 'completed',
        statistics_available: false,
        metrics: null,
      },
    ])
  })

  it('preserves a reported false Epoch advancement value', () => {
    const summary = repositoryMaintenanceSummaryFromMetadata({
      event_type: 'repository_maintenance_summary',
      maintenance_summary: {
        schema_version: 1,
        mode: 'quick',
        source: 'maintenance_info',
        approximate: false,
        stages: [{
          type: 'epoch_advance',
          status: 'completed',
          statistics_available: true,
          metrics: { current_epoch: 43, advanced: false },
        }],
      },
    })

    expect(summary?.stages[0]?.metrics).toEqual({ current_epoch: 43, advanced: false })
  })

  it('rejects null, boolean, empty, and unknown values instead of fabricating zero metrics', () => {
    const summary = repositoryMaintenanceSummaryFromMetadata({
      event_type: 'repository_maintenance_summary',
      maintenance_summary: {
        schema_version: 1,
        mode: 'quick',
        source: 'maintenance_info',
        approximate: false,
        content_gc: { deleted_count: null, deleted_bytes: true },
        pack_gc: null,
        stages: [{
          type: 'log_cleanup',
          status: 'completed',
          statistics_available: true,
          metrics: {
            deleted_count: '',
            deleted_bytes: false,
            object_name: 'not part of the contract',
          },
        }],
      },
    })

    expect(summary?.content_gc).toBeNull()
    expect(summary?.stages[0]).toMatchObject({
      status: 'completed',
      statistics_available: false,
      metrics: null,
    })
  })

  it('rejects unrelated and unsupported event metadata', () => {
    expect(repositoryMaintenanceSummaryFromMetadata({ event_type: 'other' })).toBeNull()
    expect(repositoryMaintenanceSummaryFromMetadata({
      event_type: 'repository_maintenance_summary',
      maintenance_summary: { schema_version: 2, mode: 'full' },
    })).toBeNull()
  })
})
