export type MaintenanceMetricGroup = {
  [key: string]: number | undefined
}

export type RepositoryMaintenanceSummary = {
  schema_version: 1
  mode: 'quick' | 'full'
  source: 'maintenance_info' | 'stderr'
  approximate: boolean
  content_gc: MaintenanceMetricGroup | null
  pack_gc: MaintenanceMetricGroup | null
}

function record(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function nonnegativeNumber(value: unknown): number | undefined {
  const parsed = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(parsed) || parsed < 0) return undefined
  return parsed
}

function metricGroup(value: unknown): MaintenanceMetricGroup | null {
  const raw = record(value)
  if (!raw) return null
  const normalized: MaintenanceMetricGroup = {}
  for (const [key, item] of Object.entries(raw)) {
    const parsed = nonnegativeNumber(item)
    if (parsed !== undefined) normalized[key] = parsed
  }
  return Object.keys(normalized).length ? normalized : null
}

export function repositoryMaintenanceSummaryFromMetadata(
  metadata: unknown,
): RepositoryMaintenanceSummary | null {
  const event = record(metadata)
  if (event?.event_type !== 'repository_maintenance_summary') return null
  const raw = record(event.maintenance_summary)
  if (!raw || Number(raw.schema_version) !== 1) return null
  const mode = raw.mode === 'quick' ? 'quick' : raw.mode === 'full' ? 'full' : null
  if (!mode) return null
  return {
    schema_version: 1,
    mode,
    source: raw.source === 'maintenance_info' ? 'maintenance_info' : 'stderr',
    approximate: raw.approximate === true,
    content_gc: metricGroup(raw.content_gc),
    pack_gc: metricGroup(raw.pack_gc),
  }
}
