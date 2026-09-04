export type MaintenanceMetricGroup = {
  [key: string]: number | undefined
}

export type MaintenanceStageType =
  | 'content_rewrite'
  | 'pack_gc'
  | 'index_compaction'
  | 'log_cleanup'
  | 'epoch_compaction'
  | 'epoch_advance'

export type MaintenanceStageMetrics = {
  [key: string]: number | boolean | undefined
}

export type MaintenanceStage = {
  type: MaintenanceStageType
  status: 'completed' | 'not_run'
  statistics_available: boolean
  metrics: MaintenanceStageMetrics | null
}

export type RepositoryMaintenanceSummary = {
  schema_version: 1
  mode: 'quick' | 'full'
  source: 'maintenance_info' | 'stderr'
  approximate: boolean
  content_gc: MaintenanceMetricGroup | null
  pack_gc: MaintenanceMetricGroup | null
  stages: MaintenanceStage[]
}

const stageNumericMetricKeys: Record<MaintenanceStageType, readonly string[]> = {
  content_rewrite: [
    'found_count',
    'found_bytes',
    'rewritten_count',
    'rewritten_bytes',
    'retained_count',
    'retained_bytes',
  ],
  pack_gc: [
    'unreferenced_count',
    'unreferenced_bytes',
    'deleted_count',
    'deleted_bytes',
    'retained_count',
    'retained_bytes',
  ],
  index_compaction: [],
  log_cleanup: [
    'candidate_count',
    'candidate_bytes',
    'deleted_count',
    'deleted_bytes',
    'retained_count',
    'retained_bytes',
  ],
  epoch_compaction: ['superseded_index_count', 'superseded_index_bytes', 'epoch'],
  epoch_advance: ['current_epoch'],
}

const stageTypes = new Set<MaintenanceStageType>(
  Object.keys(stageNumericMetricKeys) as MaintenanceStageType[],
)

function record(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function nonnegativeNumber(value: unknown): number | undefined {
  if (typeof value !== 'number' && typeof value !== 'string') return undefined
  if (typeof value === 'string' && value.trim() === '') return undefined
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

function maintenanceStage(value: unknown): MaintenanceStage | null {
  const raw = record(value)
  if (!raw || typeof raw.type !== 'string' || !stageTypes.has(raw.type as MaintenanceStageType)) {
    return null
  }
  const type = raw.type as MaintenanceStageType
  const status = raw.status === 'completed' ? 'completed' : raw.status === 'not_run' ? 'not_run' : null
  if (!status) return null

  const rawMetrics = record(raw.metrics)
  const metrics: MaintenanceStageMetrics = {}
  if (rawMetrics) {
    for (const key of stageNumericMetricKeys[type]) {
      const parsed = nonnegativeNumber(rawMetrics[key])
      if (parsed !== undefined) metrics[key] = parsed
    }
    if (type === 'epoch_advance' && typeof rawMetrics.advanced === 'boolean') {
      metrics.advanced = rawMetrics.advanced
    }
  }
  const normalizedMetrics = status === 'completed' && Object.keys(metrics).length ? metrics : null
  return {
    type,
    status,
    statistics_available: raw.statistics_available === true && normalizedMetrics !== null,
    metrics: normalizedMetrics,
  }
}

function maintenanceStages(value: unknown): MaintenanceStage[] {
  if (!Array.isArray(value)) return []
  const stages: MaintenanceStage[] = []
  const seen = new Set<MaintenanceStageType>()
  for (const item of value) {
    const stage = maintenanceStage(item)
    if (!stage || seen.has(stage.type)) continue
    seen.add(stage.type)
    stages.push(stage)
  }
  return stages
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
    stages: maintenanceStages(raw.stages),
  }
}
