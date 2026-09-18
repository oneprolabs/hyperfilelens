import type { ComposerTranslation } from 'vue-i18n'
import type { NodeOperationBatchPreview, NodeWorkloadReason } from '../types/nodeLifecycle'

type DiskSkipItem = NonNullable<NodeOperationBatchPreview['skipped_disk_full']>[number]

export type UpgradeConfirmSkipGroup = {
  key: string
  title: string
  names: Array<{ id: number; name: string }>
  guidance?: string
  details?: string[]
}

export type WorkloadBlockerCategory =
  | 'backup_restore'
  | 'knowledge_source'
  | 'workspace'
  | 'node_task'
  | 'mixed'

const BACKUP_RESTORE_BLOCKER_CODES = new Set(['backup_running', 'restore_running'])

/**
 * Classify the detailed workload blockers returned by the lifecycle preview.
 * A null result deliberately preserves the legacy generic copy for older APIs
 * that did not return the optional blockers array.
 */
export function classifyWorkloadBlockers(
  blockers?: NodeWorkloadReason[],
): WorkloadBlockerCategory | null {
  if (!blockers?.length) return null
  const codes = new Set(blockers.map((item) => String(item.code || '')))
  if (codes.size > 0 && [...codes].every((code) => BACKUP_RESTORE_BLOCKER_CODES.has(code))) {
    return 'backup_restore'
  }
  if (codes.size === 1 && codes.has('knowledge_source_bound')) return 'knowledge_source'
  if (codes.size === 1 && codes.has('workspace_cleanup_pending')) return 'workspace'
  if (codes.size === 1 && codes.has('node_task_running')) return 'node_task'
  return 'mixed'
}

export function workloadBlockedMessageKey(blockers?: NodeWorkloadReason[]): string {
  switch (classifyWorkloadBlockers(blockers)) {
    case 'backup_restore':
      return 'nodeLifecycle.workloadBlockedBackupRestore'
    case 'knowledge_source':
      return 'nodeLifecycle.workloadBlockedKnowledgeSource'
    case 'workspace':
      return 'nodeLifecycle.workloadBlockedWorkspace'
    case 'node_task':
      return 'nodeLifecycle.workloadBlockedNodeTask'
    default:
      return 'nodeLifecycle.workloadBlocked'
  }
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
  const workloadGroups = new Map<string, Array<{ id: number; name: string }>>()
  for (const item of preview.skipped_workload) {
    const category = classifyWorkloadBlockers(item.blockers)
    const key = category === 'backup_restore' || category === null
      ? 'workload'
      : category === 'node_task'
        ? 'nodeTask'
        : 'mixed'
    const names = workloadGroups.get(key) || []
    names.push({ id: item.node_id, name: item.name })
    workloadGroups.set(key, names)
  }
  for (const [key, names] of workloadGroups) {
    addGroup(
      key,
      names,
      `nodeLifecycle.confirmSkipGuidance.${key}`,
    )
  }
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
