import { describe, expect, it } from 'vitest'
import type { RestoreRecord, RestoreRecordItem } from '../../../lib/restoreApi'
import {
  isRestoreRecordActive,
  normalizedRestoreRecordTaskStatus,
  restoreRecordItemSourceKind,
  restoreRecordPathMappings,
  restoreRecordRuntimeMetricParts,
  restoreRecordSnapshotLabel,
  restoreRecordTargetDisplayPath,
  restoreRecordTimeState,
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

  it.each([
    ['queued', 'pending', true],
    ['pending', 'pending', true],
    ['waiting', 'waiting', true],
    ['blocked', 'blocked', true],
    ['in_progress', 'running', true],
    ['running', 'running', true],
    ['completed', 'success', false],
    ['failed', 'failed', false],
  ])('normalizes %s and preserves its active state', (status, normalized, active) => {
    const value = record({
      task_summary: { status, progress: 0, started_at: null, finished_at: null },
    })
    expect(normalizedRestoreRecordTaskStatus(value)).toBe(normalized)
    expect(isRestoreRecordActive(value)).toBe(active)
  })

  it('distinguishes lifecycle time states without falling back to submission time', () => {
    expect(restoreRecordTimeState(record({
      task_summary: { status: 'waiting', progress: 0, started_at: null, finished_at: null },
    }))).toMatchObject({
      startedKind: 'not_started',
      finishedKind: 'not_finished',
      durationKind: 'not_applicable',
    })
    expect(restoreRecordTimeState(record({
      task_summary: {
        status: 'running',
        progress: 42,
        started_at: '2026-09-03T09:00:00Z',
        finished_at: null,
      },
    }))).toMatchObject({
      startedKind: 'value',
      finishedKind: 'not_finished',
      durationKind: 'running',
    })
    expect(restoreRecordTimeState(record({
      task_summary: {
        status: 'success',
        progress: 100,
        started_at: '2026-09-03T09:00:00Z',
        finished_at: '2026-09-03T09:02:00Z',
      },
    }))).toMatchObject({
      startedKind: 'value',
      finishedKind: 'value',
      durationKind: 'fixed',
    })
  })

  it('separates a pre-start terminal outcome from missing successful timing data', () => {
    expect(restoreRecordTimeState(record({
      task_summary: {
        status: 'failed',
        progress: 0,
        started_at: null,
        finished_at: '2026-09-03T09:02:00Z',
      },
    }))).toMatchObject({
      startedKind: 'not_started',
      finishedKind: 'value',
      durationKind: 'not_applicable',
    })
    expect(restoreRecordTimeState(record({
      task_summary: {
        status: 'success',
        progress: 100,
        started_at: null,
        finished_at: '2026-09-03T09:02:00Z',
      },
    }))).toMatchObject({
      startedKind: 'unavailable',
      finishedKind: 'value',
      durationKind: 'unavailable',
    })
  })

  it('marks conflicting and invalid time combinations unavailable', () => {
    expect(restoreRecordTimeState(record({
      task_summary: {
        status: 'running',
        progress: 42,
        started_at: '2026-09-03T09:00:00Z',
        finished_at: '2026-09-03T09:02:00Z',
      },
    }))).toMatchObject({
      durationKind: 'unavailable',
      hasStatusTimeConflict: true,
    })
    expect(restoreRecordTimeState(record({
      task_summary: {
        status: 'success',
        progress: 100,
        started_at: '2026-09-03T09:03:00Z',
        finished_at: '2026-09-03T09:02:00Z',
      },
    }))).toMatchObject({
      durationKind: 'unavailable',
      hasInvalidTimeData: true,
    })
    expect(restoreRecordTimeState(record({
      task_summary: {
        status: 'running',
        progress: 42,
        started_at: 'not-a-time',
        finished_at: null,
      },
    }))).toMatchObject({
      startedKind: 'unavailable',
      finishedKind: 'not_finished',
      durationKind: 'unavailable',
      hasInvalidTimeData: true,
    })
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

  it('uses the recorded source type instead of guessing from the filename', () => {
    const extensionlessFile = { ...item, source_snapshot_directory_id: 11, source_path: '/tmp/hfl-agent-new' }
    const dottedDirectory = { ...item, id: 12, source_snapshot_directory_id: 22, source_path: '/data/releases.v2' }
    const value = record({
      items: [extensionlessFile, dottedDirectory],
      expanded_payload: {
        items: [
          { source_snapshot_directory_id: 11, source_path_type: 'file' },
          { source_snapshot_directory_id: 22, source_path_type: 'directory' },
        ],
      },
    })

    expect(restoreRecordItemSourceKind(value, extensionlessFile)).toBe('file')
    expect(restoreRecordItemSourceKind(value, dottedDirectory)).toBe('dir')
    expect(restoreRecordPathMappings(value).map((row) => row.sourceKind)).toEqual(['file', 'dir'])
  })

  it('formats available restore counts, capacity, speed, and ETA', () => {
    const t = (key: string, args?: Record<string, unknown>) => {
      if (key.endsWith('flowRestoreRecordItemsProgress')) return `${args?.done} / ${args?.total} items processed`
      if (key.endsWith('flowRestoreRecordRestoredObjects')) return `Restored: ${args?.files} files, ${args?.directories} directories, ${args?.symlinks} symbolic links`
      if (key.endsWith('flowRestoreRecordRestoredCapacity')) return `Restore capacity: ${args?.size}`
      if (key.endsWith('bytesCapacity')) return `${args?.done} / ${args?.total}`
      if (key.endsWith('etaSeconds')) return `${args?.n}s remaining`
      if (key.endsWith('restoreEtaSeconds')) return `${args?.n}s remaining`
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
      '1.91 MB / 7.63 MB',
      '488 KB/s',
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
      if (key.endsWith('flowRestoreRecordRestoredObjects')) return `Restored: ${args?.files} files, ${args?.directories} directories, ${args?.symlinks} symbolic links`
      if (key.endsWith('flowRestoreRecordRestoredCapacity')) return `Restore capacity: ${args?.size}`
      if (key.endsWith('bytesCapacity')) return `${args?.done} / ${args?.total}`
      return key
    }

    expect(restoreRecordRuntimeMetricParts(t, {
      transfer_progress: {
        phase: 'done',
        restored_file_count: 240,
        restored_directory_count: 22,
        restored_symlink_count: 1,
        bytes_done: 649_000_000,
        bytes_total: 649_000_000,
        bytes_total_known: true,
      },
    }, 'success')).toEqual([
      'Restored: 240 files, 22 directories, 1 symbolic links',
      'Restore capacity: 619 MB',
    ])
  })

  it('shows processed items while a restore is running', () => {
    const t = (key: string, args?: Record<string, unknown>) => {
      if (key.endsWith('flowRestoreRecordItemsProgress')) return `${args?.done} / ${args?.total} items processed`
      return key
    }
    expect(restoreRecordRuntimeMetricParts(t, {
      transfer_progress: {
        phase: 'transferring',
        processed_count: 12884,
        total_count: 12898,
      },
    }, 'running')).toEqual(['12,884 / 12,898 items processed'])
  })

  it('shows explicit zero terminal object counts', () => {
    const t = (key: string, args?: Record<string, unknown>) => {
      if (key.endsWith('flowRestoreRecordRestoredObjects')) return `Restored: ${args?.files} files, ${args?.directories} directories, ${args?.symlinks} symbolic links`
      return key
    }
    expect(restoreRecordRuntimeMetricParts(t, {
      transfer_progress: {
        phase: 'done',
        restored_file_count: 0,
        restored_directory_count: 0,
        restored_symlink_count: 0,
      },
    }, 'success')).toEqual(['Restored: 0 files, 0 directories, 0 symbolic links'])
  })
})
