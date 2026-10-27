/**
 * Numeric metric catalogs by Resource Type.
 * Mirrors backend apps.alert.constants.METRICS_BY_RESOURCE_TYPE so the Metric
 * dropdown still works when the metadata API is unreachable (e.g. Admin Console
 * without an organization membership).
 */
export const METRICS_BY_RESOURCE_TYPE: Record<string, readonly string[]> = {
  // Host metrics: CPU/load → memory → disk → network (common ops monitoring order).
  system: [
    'cpu_usage',
    'load_1m',
    'load_5m',
    'load_15m',
    'memory_usage',
    'swap_usage',
    'disk_usage',
    'disk_read_bytes',
    'disk_write_bytes',
    'network_rx',
    'network_tx',
  ],
  sync_proxy: ['cpu_usage', 'memory_usage', 'disk_usage', 'network_rx', 'network_tx'],
  gateway: ['cpu_usage', 'memory_usage', 'disk_usage', 'network_rx', 'network_tx'],
  agent_proxy: ['cpu_usage', 'memory_usage', 'disk_usage', 'network_rx', 'network_tx'],
  backup_repository: ['capacity_usage', 'used_size', 'free_size'],
  source_resource: ['capacity_usage', 'data_size', 'file_count'],
  target_storage: ['capacity_usage', 'used_size', 'free_size'],
}

/** Byte-sized metrics: threshold is entered in MiB/GiB/TiB; evaluator scales to bytes. */
export const BYTE_METRIC_UNITS = ['MiB', 'GiB', 'TiB'] as const

export function metricsForResourceType(resourceType?: string | null): string[] {
  if (!resourceType) return []
  return [...(METRICS_BY_RESOURCE_TYPE[resourceType] || [])]
}

/**
 * Default display unit for a metric.
 * Byte metrics default to MiB (IO/network) or GiB (capacity)—not raw Bytes.
 */
export const METRIC_DEFAULT_UNIT: Record<string, string> = {
  cpu_usage: '%',
  memory_usage: '%',
  swap_usage: '%',
  disk_usage: '%',
  capacity_usage: '%',
  load_1m: '',
  load_5m: '',
  load_15m: '',
  disk_read_bytes: 'MiB',
  disk_write_bytes: 'MiB',
  network_rx: 'MiB',
  network_tx: 'MiB',
  used_size: 'GiB',
  free_size: 'GiB',
  data_size: 'GiB',
  file_count: '',
}

export function isByteMetric(metricKey?: string | null): boolean {
  const unit = METRIC_DEFAULT_UNIT[metricKey || '']
  return unit === 'MiB' || unit === 'GiB' || unit === 'TiB' || unit === 'B'
}

/** Allowed unit values for a metric (select when more than one). */
export function unitsForMetric(metricKey?: string | null): string[] {
  if (isByteMetric(metricKey)) return [...BYTE_METRIC_UNITS]
  const unit = METRIC_DEFAULT_UNIT[metricKey || '']
  if (unit === '%') return ['%']
  return ['']
}

export function defaultUnitForMetric(metricKey?: string | null): string {
  return METRIC_DEFAULT_UNIT[metricKey || ''] ?? ''
}

/** Keep dropdown order stable even when the API returns an unordered list. */
export function orderMetricKeys(resourceType: string | null | undefined, keys: string[]): string[] {
  const preferred = metricsForResourceType(resourceType)
  if (!preferred.length) return [...keys]
  const rank = new Map(preferred.map((key, index) => [key, index]))
  const known = keys
    .filter((key) => rank.has(key))
    .sort((a, b) => (rank.get(a) ?? 0) - (rank.get(b) ?? 0))
  const unknown = keys.filter((key) => !rank.has(key))
  return [...known, ...unknown]
}

/** Accept a bare string list or `{ results: string[] }` from metadata APIs. */
export function normalizeMetricCatalog(raw: unknown): string[] | null {
  if (Array.isArray(raw) && raw.every((value) => typeof value === 'string')) {
    return raw as string[]
  }
  if (raw && typeof raw === 'object') {
    const results = (raw as { results?: unknown }).results
    if (Array.isArray(results) && results.every((value) => typeof value === 'string')) {
      return results as string[]
    }
  }
  return null
}
