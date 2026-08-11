import type { RepoUsageRow } from './dashboardApi'

export type CapacityPlanStatus = 'ok' | 'warning' | 'danger' | 'neutral'

export type RepositoryCapacityPlan = {
  projectedBytes: number
  projectedPct: number | null
  remainingBytes: number | null
  status: CapacityPlanStatus
}

export function planRepositoryCapacity(
  repository: Pick<RepoUsageRow, 'usedBytes' | 'capacityBytes' | 'capacityMode'>,
  plannedAddBytes: number,
  safePct = 80,
): RepositoryCapacityPlan {
  const usedBytes = Math.max(0, Number(repository.usedBytes) || 0)
  const addedBytes = Math.max(0, Number(plannedAddBytes) || 0)
  const projectedBytes = usedBytes + addedBytes
  if (repository.capacityMode !== 'known' || repository.capacityBytes <= 0) {
    return { projectedBytes, projectedPct: null, remainingBytes: null, status: 'neutral' }
  }

  const projectedPct = (projectedBytes / repository.capacityBytes) * 100
  return {
    projectedBytes,
    projectedPct,
    remainingBytes: repository.capacityBytes - projectedBytes,
    status: projectedPct >= 100 ? 'danger' : projectedPct >= safePct ? 'warning' : 'ok',
  }
}
