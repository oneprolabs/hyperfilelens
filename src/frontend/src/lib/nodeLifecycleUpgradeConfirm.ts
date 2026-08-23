import type { ComposerTranslation } from 'vue-i18n'
import type { NodeOperationBatchPreview } from '../types/nodeLifecycle'

type DiskSkipItem = NonNullable<NodeOperationBatchPreview['skipped_disk_full']>[number]

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

export function buildUpgradeConfirmSkipLines(
  t: ComposerTranslation,
  preview: NodeOperationBatchPreview,
): string[] {
  const lines: string[] = []
  if (preview.skipped_offline.length) {
    lines.push(t('nodeLifecycle.confirmSkipOffline', { n: preview.skipped_offline.length }))
  }
  if (preview.skipped_workload.length) {
    lines.push(t('nodeLifecycle.confirmSkipWorkload', { n: preview.skipped_workload.length }))
  }
  if (preview.skipped_not_upgradeable.length) {
    lines.push(
      t('nodeLifecycle.confirmSkipNotUpgradeable', {
        n: preview.skipped_not_upgradeable.length,
      }),
    )
  }
  if (preview.skipped_proxy_bound.length) {
    lines.push(t('nodeLifecycle.confirmSkipProxyBound', { n: preview.skipped_proxy_bound.length }))
  }
  if (preview.skipped_disk_full?.length) {
    lines.push(t('nodeLifecycle.confirmSkipDiskFull', { n: preview.skipped_disk_full.length }))
    lines.push(...buildUpgradeDiskSkipDetails(t, preview))
  }
  return lines
}
