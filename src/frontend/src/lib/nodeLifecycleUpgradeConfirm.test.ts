import { describe, expect, it } from 'vitest'
import type { ComposerTranslation } from 'vue-i18n'
import {
  buildUpgradeConfirmSkipLines,
  buildUpgradeDiskSkipDetail,
  formatDiskCapacity,
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
  if (key.endsWith('confirmSkipDiskFull')) return `${args?.n} disk node(s) skipped.`
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
    expect(buildUpgradeConfirmSkipLines(t, preview([
      {
        node_id: 1,
        name: 'ubuntu2404',
        reason: 'disk_full',
        failure_type: 'minimum_free_bytes',
        disk_free_bytes: 420 * 1024**2,
        required_free_bytes: 512 * 1024**2,
      },
    ]))).toEqual([
      '1 disk node(s) skipped.',
      'ubuntu2404: 420 MiB available; at least 512 MiB required.',
    ])
  })
})
