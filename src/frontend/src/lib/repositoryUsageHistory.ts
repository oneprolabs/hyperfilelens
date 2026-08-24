import type { StorageRepositoryUsageHistoryPoint } from './storageRepositoryApi'

export function repositoryCapacitySeries(points: StorageRepositoryUsageHistoryPoint[]) {
  return points.map(point => [point.recorded_at, point.usage_bytes] as const)
}

function isValidCapacityPoint(point: StorageRepositoryUsageHistoryPoint | undefined) {
  return point?.usage_bytes != null
}

export function isIsolatedCapacityPoint(
  points: StorageRepositoryUsageHistoryPoint[],
  index: number,
) {
  return isValidCapacityPoint(points[index])
    && !isValidCapacityPoint(points[index - 1])
    && !isValidCapacityPoint(points[index + 1])
}

export function repositoryCapacityLineSeries(points: StorageRepositoryUsageHistoryPoint[]) {
  return points.map((point, index) => [
    point.recorded_at,
    isIsolatedCapacityPoint(points, index) ? null : point.usage_bytes,
  ] as const)
}

export function repositoryCapacityIsolatedSeries(points: StorageRepositoryUsageHistoryPoint[]) {
  return points
    .filter((_point, index) => isIsolatedCapacityPoint(points, index))
    .map(point => [point.recorded_at, point.usage_bytes] as const)
}
