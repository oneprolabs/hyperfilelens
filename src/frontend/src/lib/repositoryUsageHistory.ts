import type { StorageRepositoryUsageHistoryPoint } from './storageRepositoryApi'

export function repositoryCapacitySeries(points: StorageRepositoryUsageHistoryPoint[]) {
  return points.map(point => [point.recorded_at, point.usage_bytes] as const)
}
