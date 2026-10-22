type SavedTarget = {
  id: number
  target_type?: string
  target_ref_id?: number | null
}

type EditedConfiguration = {
  id: number
  recovery_plans?: readonly SavedTarget[]
}

type PlanDraft = { id: string; targetHostId: string }

type TargetInventory = {
  getRecord: (id: string) => unknown
  isSelectable: (id: string) => boolean
}

/** A saved reference belongs to one persisted plan in one edited config. */
export function savedRestoreTargetReference(
  configurations: readonly EditedConfiguration[], configId: number | undefined, draftId: string,
): string | undefined {
  const match = /^restore-plan-([1-9]\d*)$/.exec(draftId)
  if (!match || !Number.isSafeInteger(configId) || !configId || configId <= 0) return
  const planId = Number(match[1])
  if (!Number.isSafeInteger(planId)) return
  const configuration = configurations.find((row) => row.id === configId)
  const plan = configuration?.recovery_plans?.find((row) => row.id === planId)
  const targetId = plan?.target_ref_id
  if (!Number.isSafeInteger(targetId) || !targetId || targetId <= 0) return
  if (plan?.target_type !== 'agent' && plan?.target_type !== 'nas') return
  return `${plan.target_type}:${targetId}`
}

/**
 * Validate an editor reference, not execution authorization.
 * Known targets keep normal readiness checks. An undiscoverable saved target
 * may be retained only in its original plan; the backend still checks writes.
 * This never adds the target to discovery or authorizes a newly selected one.
 */
export function restorePlanTargetReferenceIsValid(
  configurations: readonly EditedConfiguration[], configId: number | undefined,
  draft: PlanDraft, inventory: TargetInventory,
): boolean {
  if (!draft.targetHostId) return false
  if (inventory.getRecord(draft.targetHostId)) return inventory.isSelectable(draft.targetHostId)
  return savedRestoreTargetReference(configurations, configId, draft.id) === draft.targetHostId
}
