import { describe, expect, it } from 'vitest'
import type { RestoreRecord, RestoreRecordItem } from '../../../lib/restoreApi'
import {
  restoreRecordPathMappings,
  restoreRecordRuntimeMetricParts,
  restoreRecordSnapshotLabel,
  restoreRecordTargetDisplayPath,
  shouldShowRestoreRecordProgress,
} from './restoreRecordDisplay'

const item = {
  id: 11,
  source_path: '/data',
  selected_paths: [],
  target_path: '/restore/data',
  status: 'success',
} as RestoreRecordItem

function record(overrides: Partial<RestoreRecord> = {}): RestoreRecord {
  return {
    id: 1,
    source_snapshot_id: 81,
    source_snapshot_uid: 'snapshot-uid-81',
    items: [item],
    task_summary: {
      status: 'success',
      progress: 100,
      started_at: null,
      finished_at: null,
    },
    ...overrides,
  } as RestoreRecord
}

describe('restore record display', () => {
  it.each(['pending', 'success', 'failed', 'cancelled', 'timeout', ''])(
    'shows a status tag instead of progress for %s',
    (status) => {
      expect(shouldShowRestoreRecordProgress(record({
        task_summary: { status, progress: 0, started_at: null, finished_at: null },
      }))).toBe(false)
    },
  )

  it('shows progress only while the restore is running', () => {
    expect(shouldShowRestoreRecordProgress(record({
      task_summary: { status: 'RUNNING', progress: 42, started_at: null, finished_at: null },
    }))).toBe(true)
  })

  it('uses the snapshot UID and falls back to the internal ID', () => {
    expect(restoreRecordSnapshotLabel(record())).toBe('snapshot-uid-81')
    expect(restoreRecordSnapshotLabel(record({ source_snapshot_uid: '' }))).toBe('#81')
  })

  it('prefers the NAS-visible target path for records and items', () => {
    const nasRecord = record({
      target_path: '/restore',
      target_display_path: '/nasshare/restore',
    })
    expect(restoreRecordTargetDisplayPath(nasRecord)).toBe('/nasshare/restore')
    expect(restoreRecordTargetDisplayPath(nasRecord, {
      ...item,
      target_path: '/restore/data',
      target_display_path: '/nasshare/restore/data',
    })).toBe('/nasshare/restore/data')
  })

  it('flattens selected paths into one-level mappings', () => {
    const rows = restoreRecordPathMappings(record({
      items: [{
        ...item,
        selected_paths: ['docs/report.pdf', 'images'],
      }],
    }))

    expect(rows.map((row) => ({ path: row.sourcePath, kind: row.sourceKind }))).toEqual([
      { path: '/data/docs/report.pdf', kind: 'file' },
      { path: '/data/images', kind: 'dir' },
    ])
  })

  it('keeps an item without selected paths as a single mapping', () => {
    expect(restoreRecordPathMappings(record())).toMatchObject([
      { sourcePath: '/data', sourceKind: 'dir', item: { id: 11 } },
    ])
  })

  it('formats available restore counts, capacity, speed, and ETA', () => {
    const t = (key: string, args?: Record<string, unknown>) => {
      if (key.endsWith('flowRestoreRecordItemsProgress')) return `${args?.done} / ${args?.total} items processed`
      if (key.endsWith('bytesCapacity')) return `${args?.done} / ${args?.total}`
      if (key.endsWith('etaSeconds')) return `${args?.n}s remaining`
      return key
    }

    expect(restoreRecordRuntimeMetricParts(t, {
      transfer_progress: {
        phase: 'transferring',
        processed_count: 4,
        total_count: 10,
        bytes_done: 2_000_000,
        bytes_total: 8_000_000,
        bytes_total_known: true,
        speed_bps: 500_000,
        eta_seconds: 12,
      },
    })).toEqual([
      '4 / 10 items processed',
      '2.00 MB / 8.00 MB',
      '500 KB/s',
      '12s remaining',
    ])
  })

  it('does not fabricate unavailable runtime metrics', () => {
    expect(restoreRecordRuntimeMetricParts((key) => key, null)).toEqual([])
    expect(restoreRecordRuntimeMetricParts((key) => key, {
      transfer_progress: { phase: 'done' },
    })).toEqual([])
  })

  it('hides total-only metrics for a successful restore', () => {
    expect(restoreRecordRuntimeMetricParts((key) => key, {
      transfer_progress: {
        phase: 'done',
        processed_count: 0,
        total_count: 263,
        bytes_done: 0,
        bytes_total: 649_000_000,
        bytes_total_known: true,
      },
    }, 'success')).toEqual([])
  })

  it('keeps valid final metrics for a successful restore', () => {
    const t = (key: string, args?: Record<string, unknown>) => {
      if (key.endsWith('flowRestoreRecordItemsProgress')) return `${args?.done} / ${args?.total} items processed`
      if (key.endsWith('bytesCapacity')) return `${args?.done} / ${args?.total}`
      return key
    }

    expect(restoreRecordRuntimeMetricParts(t, {
      transfer_progress: {
        phase: 'done',
        processed_count: 263,
        total_count: 263,
        bytes_done: 649_000_000,
        bytes_total: 649_000_000,
        bytes_total_known: true,
      },
    }, 'success')).toEqual([
      '263 / 263 items processed',
      '649 MB / 649 MB',
    ])
  })
})
