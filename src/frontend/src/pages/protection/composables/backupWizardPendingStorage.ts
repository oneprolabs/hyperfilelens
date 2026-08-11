import type { ErrorDetailsPayload } from '../../../lib/errors/details'
import type { FlowSourceRow } from './useFlowSourceAggregate'

export type SourcePendingKind =
  | 'deleting'
  | 'delete_waiting'
  | 'delete_blocked'
  | 'reverting'
  | 'removing'
  | 'delete_failed'

export type SourcePendingOp = {
  kind: SourcePendingKind
  targetStep?: 1 | 2
  nodeId?: number
  taskId?: number
  taskUuid?: string
  startedAt?: number
  errorMessage?: string
  /** Structured unregister failure for refresh-safe View details / retry summary. */
  failureDetails?: ErrorDetailsPayload
}

export const WIZARD_PENDING_STORAGE_KEY = 'hfl-backup-wizard-source-pending'

export type PersistedWizardPending = {
  ops: Array<[string, SourcePendingOp]>
  snapshots: Array<[string, FlowSourceRow]>
}

export function readWizardPendingStorage(): PersistedWizardPending | null {
  try {
    const raw = sessionStorage.getItem(WIZARD_PENDING_STORAGE_KEY)
    if (!raw) return null
    return JSON.parse(raw) as PersistedWizardPending
  } catch {
    return null
  }
}

export function writeWizardPendingStorage(
  ops: Map<string, SourcePendingOp>,
  snapshots: Map<string, FlowSourceRow>,
) {
  if (ops.size === 0 && snapshots.size === 0) {
    sessionStorage.removeItem(WIZARD_PENDING_STORAGE_KEY)
    return
  }
  sessionStorage.setItem(
    WIZARD_PENDING_STORAGE_KEY,
    JSON.stringify({
      ops: [...ops.entries()],
      snapshots: [...snapshots.entries()],
    }),
  )
}

function mutateWizardPendingStorage(
  mutate: (ops: Map<string, SourcePendingOp>, snapshots: Map<string, FlowSourceRow>) => boolean,
) {
  const saved = readWizardPendingStorage()
  if (!saved) return
  const ops = new Map(saved.ops)
  const snapshots = new Map(saved.snapshots)
  if (!mutate(ops, snapshots)) return
  writeWizardPendingStorage(ops, snapshots)
}

export function clearWizardPendingBySourceIds(sourceIds: string[]) {
  if (!sourceIds.length) return
  const drop = new Set(sourceIds)
  mutateWizardPendingStorage((ops, snapshots) => {
    let changed = false
    for (const id of drop) {
      if (ops.delete(id)) changed = true
      if (snapshots.delete(id)) changed = true
    }
    return changed
  })
}

export function clearWizardPendingByNodeIds(nodeIds: number[]) {
  if (!nodeIds.length) return
  const drop = new Set(nodeIds)
  mutateWizardPendingStorage((ops, snapshots) => {
    let changed = false
    for (const [id, op] of [...ops.entries()]) {
      if (op.nodeId != null && drop.has(op.nodeId)) {
        ops.delete(id)
        snapshots.delete(id)
        changed = true
      }
    }
    return changed
  })
}

export function markWizardPendingBySourceIds(sourceIds: string[], op: SourcePendingOp) {
  if (!sourceIds.length) return
  const saved = readWizardPendingStorage()
  const ops = new Map(saved?.ops ?? [])
  const snapshots = new Map(saved?.snapshots ?? [])
  let changed = false
  for (const id of sourceIds) {
    ops.set(id, op)
    changed = true
  }
  if (changed) writeWizardPendingStorage(ops, snapshots)
}

export function readWizardPendingSourceOps(): Map<string, SourcePendingOp> {
  const saved = readWizardPendingStorage()
  return new Map(saved?.ops ?? [])
}
