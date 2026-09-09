import { describe, expect, it } from 'vitest'
import type { ComposerTranslation } from 'vue-i18n'
import {
  buildUpgradeConfirmSkipGroups,
  buildUpgradeDiskSkipDetail,
  formatDiskCapacity,
  upgradePreviewSkippedCount,
} from './nodeLifecycleUpgradeConfirm'
import type { NodeOperationBatchPreview } from '../types/nodeLifecycle'

const t = ((key: string, args?: Record<string, unknown>) => {
  if (key.endsWith('confirmSkipDiskFree')) {
    return `${args?.name}: ${args?.free} available; at least ${args?.required} required.`
  }
  if (key.endsWith('confirmSkipDiskUsed')) {
    return `${args?.name}: usage ${args?.used}%; max ${args?.max}%.`
  }
  if (key.endsWith('confirmSkipDiskUnknown')) return `${args?.name}: disk space is insufficient.`
  if (key.includes('confirmSkipGroup.')) return `${key.split('.').at(-1)} (${args?.n})`
  if (key.endsWith('confirmSkipMissingNode')) return `Node ${args?.id}`
  if (key.includes('confirmSkipGuidance.')) return `${key.split('.').at(-1)} guidance`
  return key
}) as unknown as ComposerTranslation

function preview(
  skipped_disk_full: NonNullable<NodeOperationBatchPreview['skipped_disk_full']>,
): NodeOperationBatchPreview {
  return {
    kind: 'upgrade',
    requested: skipped_disk_full.length,
    eligible: [],
    skipped_offline: [],
    skipped_workload: [],
    skipped_in_progress: [],
    skipped_not_upgradeable: [],
    skipped_proxy_bound: [],
    skipped_disk_full,
    missing_node_ids: [],
    max_concurrent: 5,
  }
}

describe('node lifecycle upgrade disk guidance', () => {
  it('formats thresholds in binary units', () => {
    expect(formatDiskCapacity(512 * 1024**2)).toBe('512 MiB')
    expect(formatDiskCapacity(1536 * 1024**2)).toBe('1.5 GiB')
    expect(formatDiskCapacity(null)).toBeNull()
  })

  it('derives the skipped count from the complete requested selection', () => {
    const value = preview([])
    value.requested = 3
    value.eligible = [{ node_id: 1, name: 'mhm' }, { node_id: 2, name: 'ubuntu2404' }]
    expect(upgradePreviewSkippedCount(value)).toBe(1)
  })

  it('explains the current and required free space', () => {
    expect(buildUpgradeDiskSkipDetail(t, {
      node_id: 1,
      name: 'ubuntu2404',
      reason: 'disk_full',
      failure_type: 'minimum_free_bytes',
      disk_free_bytes: 420 * 1024**2,
      required_free_bytes: 512 * 1024**2,
    })).toBe('ubuntu2404: 420 MiB available; at least 512 MiB required.')
  })

  it('identifies every skipped host and its reason', () => {
    const value = preview([])
    value.requested = 7
    value.skipped_offline = [
      { node_id: 1, name: 'offline-host', reason: 'offline' },
      { node_id: 6, name: 'offline-host-2', reason: 'offline' },
    ]
    value.skipped_workload = [{ node_id: 2, name: 'busy-host', reason: 'node_workload_active' }]
    value.skipped_in_progress = [{ node_id: 3, name: 'upgrading-host', reason: 'lifecycle_in_progress' }]
    value.skipped_not_upgradeable = [
      { node_id: 4, name: 'OnePro', reason: 'local_admin_required' },
      { node_id: 7, name: 'OnePro-2', reason: 'local_admin_required' },
    ]
    value.missing_node_ids = [5]

    expect(buildUpgradeConfirmSkipGroups(t, value)).toEqual([
      {
        key: 'offline',
        title: 'offline (2)',
        names: [{ id: 1, name: 'offline-host' }, { id: 6, name: 'offline-host-2' }],
        guidance: 'offline guidance',
      },
      {
        key: 'workload',
        title: 'workload (1)',
        names: [{ id: 2, name: 'busy-host' }],
        guidance: 'workload guidance',
      },
      {
        key: 'inProgress',
        title: 'inProgress (1)',
        names: [{ id: 3, name: 'upgrading-host' }],
        guidance: 'inProgress guidance',
      },
      {
        key: 'localAdmin',
        title: 'localAdmin (2)',
        names: [{ id: 4, name: 'OnePro' }, { id: 7, name: 'OnePro-2' }],
        guidance: 'localAdmin guidance',
      },
      {
        key: 'missing',
        title: 'missing (1)',
        names: [{ id: 5, name: 'Node 5' }],
      },
    ])
  })

  it('explains a usage limit and keeps a generic fallback for old previews', () => {
    expect(buildUpgradeDiskSkipDetail(t, {
      node_id: 2,
      name: 'mhm-28',
      reason: 'disk_full',
      failure_type: 'maximum_used_percent',
      disk_used_percent: 91.2,
      max_disk_used_percent: 90,
    })).toBe('mhm-28: usage 91.2%; max 90%.')
    expect(buildUpgradeDiskSkipDetail(t, {
      node_id: 3,
      name: 'legacy-agent',
      reason: 'disk_full',
    })).toBe('legacy-agent: disk space is insufficient.')
  })

  it('adds per-node disk details to the confirmation skip list', () => {
    expect(buildUpgradeConfirmSkipGroups(t, preview([
      {
        node_id: 1,
        name: 'ubuntu2404',
        reason: 'disk_full',
        failure_type: 'minimum_free_bytes',
        disk_free_bytes: 420 * 1024**2,
        required_free_bytes: 512 * 1024**2,
      },
    ]))).toEqual([{
      key: 'diskFull',
      title: 'diskFull (1)',
      names: [{ id: 1, name: 'ubuntu2404' }],
      details: ['ubuntu2404: 420 MiB available; at least 512 MiB required.'],
    }])
  })
})
