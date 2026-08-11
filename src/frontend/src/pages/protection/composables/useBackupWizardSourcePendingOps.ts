import { ref } from 'vue'
import type { Composer } from 'vue-i18n'
import type { FlowSourceRow } from './useFlowSourceAggregate'
import {
  readWizardPendingStorage,
  writeWizardPendingStorage,
  type SourcePendingOp,
} from './backupWizardPendingStorage'

export type { SourcePendingKind, SourcePendingOp } from './backupWizardPendingStorage'

export function useBackupWizardSourcePendingOps(options: { t: Composer['t'] }) {
  const { t } = options
  const ops = ref(new Map<string, SourcePendingOp>())
  const snapshots = ref(new Map<string, FlowSourceRow>())

  function persist() {
    writeWizardPendingStorage(ops.value, snapshots.value)
  }

  function restorePersisted() {
    const saved = readWizardPendingStorage()
    if (!saved) return
    ops.value = new Map(saved.ops)
    snapshots.value = new Map(saved.snapshots)
  }

  function reconcileWithCatalog(
    apiSourceIds: Set<string>,
    activeRemovalNodeIds: Set<number>,
  ) {
    const nextOps = new Map(ops.value)
    const nextSnaps = new Map(snapshots.value)
    let changed = false
    for (const [id, op] of ops.value) {
      if (apiSourceIds.has(id)) continue
      if (op.kind === 'removing' && op.nodeId != null && activeRemovalNodeIds.has(op.nodeId)) {
        continue
      }
      nextOps.delete(id)
      nextSnaps.delete(id)
      changed = true
    }
    if (!changed) return
    ops.value = nextOps
    snapshots.value = nextSnaps
    persist()
  }

  function pendingLabel(op: SourcePendingOp | undefined): string | undefined {
    if (!op || op.kind === 'removing') return undefined
    switch (op.kind) {
      case 'deleting':
        return t('protection.backupsPage.sourcePendingDeleting')
      case 'delete_waiting':
        return t('protection.backupsPage.sourcePendingDeleteWaiting')
      case 'delete_blocked':
        return t('protection.backupsPage.sourcePendingDeleteBlocked')
      case 'delete_failed':
        return t('protection.backupsPage.sourcePendingDeleteFailed')
      case 'reverting':
        return op.targetStep === 2
          ? t('protection.backupsPage.sourcePendingRevertStep3')
          : t('protection.backupsPage.sourcePendingRevertStep2')
      default:
        return undefined
    }
  }

  function isPending(sourceId: string) {
    return ops.value.has(sourceId)
  }

  function getOp(sourceId: string) {
    return ops.value.get(sourceId)
  }

  function pendingDeleteTasks() {
    return [...ops.value.entries()].flatMap(([sourceId, op]) =>
      ['deleting', 'delete_waiting', 'delete_blocked'].includes(op.kind) && op.taskUuid
        ? [{ sourceId, taskUuid: op.taskUuid, startedAt: op.startedAt }]
        : [],
    )
  }

  function rowPendingLabel(sourceId: string) {
    return pendingLabel(getOp(sourceId))
  }

  function rowPendingSpinning(sourceId: string) {
    const op = getOp(sourceId)
    return !!op && ['deleting', 'delete_waiting', 'reverting'].includes(op.kind)
  }

  function isRowSelectable(sourceId: string) {
    const op = getOp(sourceId)
    if (op?.kind === 'delete_failed' || op?.kind === 'delete_blocked') return true
    return !isPending(sourceId)
  }

  function mark(
    sourceIds: string[],
    op: SourcePendingOp,
    rows?: FlowSourceRow[],
  ) {
    const nextOps = new Map(ops.value)
    const nextSnaps = new Map(snapshots.value)
    const byId = new Map((rows || []).map((row) => [row.id, row]))
    for (const id of sourceIds) {
      nextOps.set(id, op)
      const row = byId.get(id) || nextSnaps.get(id)
      if (row) nextSnaps.set(id, row)
    }
    ops.value = nextOps
    snapshots.value = nextSnaps
    persist()
  }

  function transitionToRemoving(
    items: Array<{ source_id: string; node_id: number }>,
  ) {
    const nextOps = new Map(ops.value)
    for (const item of items) {
      const existing = nextOps.get(item.source_id)
      if (!existing && !snapshots.value.has(item.source_id)) continue
      nextOps.set(item.source_id, {
        kind: 'removing',
        nodeId: item.node_id,
      })
    }
    ops.value = nextOps
    persist()
  }

  function clear(sourceIds: string[]) {
    if (!sourceIds.length) return
    const nextOps = new Map(ops.value)
    const nextSnaps = new Map(snapshots.value)
    for (const id of sourceIds) {
      nextOps.delete(id)
      nextSnaps.delete(id)
    }
    ops.value = nextOps
    snapshots.value = nextSnaps
    persist()
  }

  function clearByNodeId(nodeId: number) {
    const ids = [...ops.value.entries()]
      .filter(([, op]) => op.nodeId === nodeId)
      .map(([id]) => id)
    clear(ids)
  }

  function injectPendingRows(
    rows: FlowSourceRow[],
    ctx?: { activeRemovalNodeIds?: Set<number> },
  ): FlowSourceRow[] {
    if (!ops.value.size) return rows
    const activeRemoval = ctx?.activeRemovalNodeIds ?? new Set<number>()
    const seen = new Set(rows.map((row) => row.id))
    const merged = [...rows]
    for (const [id, snap] of snapshots.value.entries()) {
      if (!ops.value.has(id) || seen.has(id)) continue
      const op = ops.value.get(id)
      if (!op) continue
      if (op.kind === 'removing') {
        if (op.nodeId != null && activeRemoval.has(op.nodeId)) {
          merged.push(snap)
          seen.add(id)
        }
        continue
      }
    }
    return merged
  }

  restorePersisted()

  return {
    isPending,
    getOp,
    pendingDeleteTasks,
    rowPendingLabel,
    rowPendingSpinning,
    isRowSelectable,
    mark,
    transitionToRemoving,
    clear,
    clearByNodeId,
    injectPendingRows,
    restorePersisted,
    reconcileWithCatalog,
  }
}
