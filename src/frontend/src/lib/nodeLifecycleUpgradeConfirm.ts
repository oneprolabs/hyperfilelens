import type { ComposerTranslation } from 'vue-i18n'
import type { NodeOperationBatchPreview } from '../types/nodeLifecycle'

type DiskSkipItem = NonNullable<NodeOperationBatchPreview['skipped_disk_full']>[number]

export type UpgradeConfirmSkipGroup = {
  key: string
  title: string
  names: Array<{ id: number; name: string }>
  guidance?: string
  details?: string[]
}

export function upgradePreviewSkippedCount(preview: NodeOperationBatchPreview): number {
  return Math.max(0, preview.requested - preview.eligible.length)
}

/** Format capacity using binary units because the upgrade threshold is configured in MiB. */
export function formatDiskCapacity(value: number | null | undefined): string | null {
  if (value == null) return null
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes < 0) return null
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let scaled = bytes
  let unit = 0
  while (scaled >= 1024 && unit < units.length - 1) {
    scaled /= 1024
    unit += 1
  }
  const digits = unit === 0 ? 0 : scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2
  return `${Number(scaled.toFixed(digits))} ${units[unit]}`
}

export function buildUpgradeDiskSkipDetail(
  t: ComposerTranslation,
  item: DiskSkipItem,
): string {
  const free = formatDiskCapacity(item.disk_free_bytes)
  const required = formatDiskCapacity(item.required_free_bytes)
  if (item.failure_type === 'minimum_free_bytes' && free && required) {
    return t('nodeLifecycle.confirmSkipDiskFree', {
      name: item.name,
      free,
      required,
    })
  }

  const used = Number(item.disk_used_percent)
  const maxUsed = Number(item.max_disk_used_percent)
  if (
    item.failure_type === 'maximum_used_percent' &&
    Number.isFinite(used) &&
    Number.isFinite(maxUsed)
  ) {
    return t('nodeLifecycle.confirmSkipDiskUsed', {
      name: item.name,
      used: used.toFixed(1),
      max: maxUsed.toFixed(0),
    })
  }

  return t('nodeLifecycle.confirmSkipDiskUnknown', { name: item.name })
}

export function buildUpgradeDiskSkipDetails(
  t: ComposerTranslation,
  preview: NodeOperationBatchPreview,
): string[] {
  return (preview.skipped_disk_full || []).map((item) => buildUpgradeDiskSkipDetail(t, item))
}

export function buildUpgradeConfirmSkipGroups(
  t: ComposerTranslation,
  preview: NodeOperationBatchPreview,
): UpgradeConfirmSkipGroup[] {
  const groups: UpgradeConfirmSkipGroup[] = []
  const addGroup = (
    key: string,
    names: Array<{ id: number; name: string }>,
    guidanceKey?: string,
    details?: string[],
  ) => {
    if (!names.length) return
    groups.push({
      key,
      title: t(`nodeLifecycle.confirmSkipGroup.${key}`, { n: names.length }),
      names,
      ...(guidanceKey ? { guidance: t(guidanceKey) } : {}),
      ...(details?.length ? { details } : {}),
    })
  }

  addGroup(
    'offline',
    preview.skipped_offline.map((item) => ({ id: item.node_id, name: item.name })),
    'nodeLifecycle.confirmSkipGuidance.offline',
  )
  addGroup(
    'workload',
    preview.skipped_workload.map((item) => ({ id: item.node_id, name: item.name })),
    'nodeLifecycle.confirmSkipGuidance.workload',
  )
  addGroup(
    'inProgress',
    preview.skipped_in_progress.map((item) => ({ id: item.node_id, name: item.name })),
    'nodeLifecycle.confirmSkipGuidance.inProgress',
  )

  for (const [reason, key] of [
    ['local_admin_required', 'localAdmin'],
    ['release_unavailable', 'releaseUnavailable'],
    ['downgrade_not_supported', 'downgrade'],
  ] as const) {
    addGroup(
      key,
      preview.skipped_not_upgradeable
        .filter((item) => item.reason === reason)
        .map((item) => ({ id: item.node_id, name: item.name })),
      `nodeLifecycle.confirmSkipGuidance.${key}`,
    )
  }
  addGroup(
    'notUpgradeable',
    preview.skipped_not_upgradeable
      .filter((item) => ![
        'local_admin_required',
        'release_unavailable',
        'downgrade_not_supported',
      ].includes(item.reason))
      .map((item) => ({ id: item.node_id, name: item.name })),
  )
  addGroup(
    'proxyBound',
    preview.skipped_proxy_bound.map((item) => ({ id: item.node_id, name: item.name })),
    'nodeLifecycle.confirmSkipGuidance.proxyBound',
  )
  addGroup(
    'diskFull',
    (preview.skipped_disk_full || []).map((item) => ({ id: item.node_id, name: item.name })),
    undefined,
    buildUpgradeDiskSkipDetails(t, preview),
  )
  addGroup(
    'missing',
    preview.missing_node_ids.map((id) => ({ id, name: t('nodeLifecycle.confirmSkipMissingNode', { id }) })),
  )
  return groups
}
