import { api } from './api'
import { unwrapApiPayload, asList } from './parse'
import { getEffectiveOrgKey } from '../composables/useAuth'
import { listRecords, type AlertRecord } from './alertApi'
import { dashboardTaskStatistics, listTasks, type TaskRow, type TaskStatistics } from './taskApi'
import { getRouteRequestSignal } from './routeRequestAbort'
import { toApiError } from './errors/normalizer'
import { withRequestDeadline } from './requestDeadline'
import { fetchCurrentLicense } from './subscriptionApi'
import { buildQuotaRows, type QuotaUsageRow } from './licenseQuotaDisplay'
import { productionSourceSummary, type ProductionSourceSummary } from './sourceApi'
import { listBackupConfigs } from './protectionBackupConfigApi'
import { listBackupPolicies } from './protectionPolicyApi'
import { listRestorePlans } from './restoreApi'

export type StorageSummary = {
  /** Backup data stored across every repository. */
  usedBytes: number
  /** De-duplicated backing-storage metrics for known storage pools. */
  storageUsedBytes: number
  availableBytes: number
  capacityBytes: number
  repoCount: number
  /** NAS and local-disk repositories expected to expose backing storage metrics. */
  capacityRepoCount: number
  coveredRepoCount: number
  pools: StoragePoolSummary[]
  /** known = at least one backing pool is known; pending = awaiting first pool probe. */
  capacityMode: 'empty' | 'known' | 'unlimited' | 'pending' | 'unavailable'
}

export type StoragePoolSummary = {
  key: string
  label: string
  usedBytes: number
  availableBytes: number
  capacityBytes: number
  repoCount: number
}

export type RepoUsageRow = {
  id: number
  name: string
  health: string
  status: string
  usedBytes: number
  capacityBytes: number
  storageTotalBytes: number
  storageAvailableBytes: number
  pct: number | null
  capacityMode: 'known' | 'unlimited' | 'pending' | 'unavailable'
  usageProbeStatus: string
  capacityProbeStatus: string
}

export type { QuotaUsageRow } from './licenseQuotaDisplay'

export function nodeListPathForRole(role?: string): string {
  if (role === 'proxy') return '/node/agents'
  if (role === 'gateway') return '/node/gateways'
  if (role === 'agent' || role === 'source') return '/protection/backup-sources'
  return '/node/agents'
}

export function nodeAttentionLinkForRole(role: string | undefined, nodeId: number): string {
  const path = nodeListPathForRole(role)
  const params = new URLSearchParams()
  if (role === 'agent' || role === 'source') params.set('tab', 'host')
  params.set('openNode', String(nodeId))
  return `${path}?${params.toString()}`
}

export type DashboardEventItem = {
  id: string
  event_type?: string
  title: string
  detail: string
  severity: 'information' | 'warning' | 'critical'
  at?: string
  to: string
}

export type DashboardAlertItem = {
  id: string
  type?: string
  title: string
  detail: string
  severity: string
  status: string
  at?: string
  to: string
}

export type TaskDayBucket = {
  label: string
  finishedAfter: string
  finishedBefore: string
  success: number
  fail: number
  cancel: number
}

export type RecoveryLastRestore = {
  status: string
  at?: string
}

export type RecoverySummary = {
  backupPoliciesEnabled: number
  recoveryConfigCount: number
  restorePlansEnabled: number
  lastRestore: RecoveryLastRestore | null
}

const dashboardSections = ['sources', 'storage', 'running', 'recovery', 'quota', 'repositories', 'alerts', 'events', 'statistics'] as const
export type DashboardSection = typeof dashboardSections[number]
export type DashboardSectionState = 'loading' | 'ready' | 'unavailable' | 'forbidden'

export type DashboardOverview = {
  sections: Record<DashboardSection, DashboardSectionState>
  orgName: string
  /** Whether the quota source responded; false is shown as an inline card error. */
  quotaAvailable: boolean
  quotaRows: QuotaUsageRow[]
  taskStatisticsState: 'loading' | 'ready' | 'unavailable'
  taskStats: {
    total: number
    running: number
    completed: number
    failed: number
    cancelled: number
    byTaskType: Record<string, number>
  }
  recoveryDrill24h: {
    total: number
    running: number
    success: number
    failed: number
    successRate: number | null
  }
  tasks7dBuckets: TaskDayBucket[]
  runningTasks: TaskRow[]
  alertFiring: number
  productionSources: ProductionSourceSummary
  recovery: RecoverySummary
  storage: StorageSummary
  repositories: RepoUsageRow[]
  topRepos: RepoUsageRow[]
  eventsCount: number
  events: DashboardEventItem[]
  activeAlertsCount: number
  activeAlerts: DashboardAlertItem[]
  isEmpty: boolean
}

type ApiOperationalEvent = {
  id?: string | number
  event_type?: string
  title?: string
  details?: string
  severity?: 'information' | 'warning' | 'critical'
  occurred_at?: string
  target_path?: string
}

type EventPreviewPage = {
  count: number
  results: ApiOperationalEvent[]
}

async function loadEventsPreview(init?: RequestInit): Promise<EventPreviewPage> {
  const data = unwrapApiPayload<Record<string, unknown>>(
    await api<unknown>('/api/v1/monitors/events/?period=24h&page=1&page_size=5', init),
  )
  return {
    count: Number(data.count) || 0,
    results: asList<ApiOperationalEvent>(data),
  }
}

function dashboardAlertItem(record: AlertRecord): DashboardAlertItem {
  return {
    id: String(record.id),
    type: record.type,
    title: record.title || 'Alert',
    detail: record.message || record.resourceName || record.resource_name || '',
    severity: record.severity || 'info',
    status: record.status || 'firing',
    at: record.lastTriggeredAt || record.last_triggered_at || record.createdAt || record.created_at,
    to: `/ops/alerts?alertId=${encodeURIComponent(String(record.id))}`,
  }
}

export type StorageRepositoryUsageRefreshResult = {
  queued?: boolean
  deduplicated?: boolean
  task_id?: string
  repo_type?: string | null
  repository_ids?: number[]
  limit?: number
}

export async function syncStorageRepositoryUsage(limit = 200): Promise<StorageRepositoryUsageRefreshResult> {
  return unwrapApiPayload<StorageRepositoryUsageRefreshResult>(
    await api<unknown>('/api/v1/storage/repositories/sync-usage/', {
      method: 'POST',
      body: JSON.stringify({ limit }),
    }),
  )
}

type ApiRepository = {
  id?: number
  name?: string
  repo_type?: string
  status?: string
  health?: string
  config?: Record<string, unknown>
  capacity_bytes?: number
  estimated_usage_bytes?: number
  physical_usage_bytes?: number | null
  storage_total_bytes?: number
  storage_used_bytes?: number
  storage_available_bytes?: number
  storage_pool_key?: string
  storage_mount_point?: string
  bind_node_display_name?: string | null
  usage_probe_status?: string
  capacity_probe_status?: string
}

const RESTORE_TERMINAL = new Set(['success', 'failed', 'timeout', 'cancelled'])

function summarizeRecovery(params: {
  backupPoliciesEnabled: number
  recoveryConfigCount: number
  restorePlansEnabled: number
  restoreTasks: TaskRow[]
}): RecoverySummary {
  let lastRestore: RecoveryLastRestore | null = null
  for (const task of params.restoreTasks) {
    if (!RESTORE_TERMINAL.has(task.status)) continue
    lastRestore = {
      status: task.status,
      at: task.finished_at || task.created_at,
    }
    break
  }

  return {
    backupPoliciesEnabled: params.backupPoliciesEnabled,
    recoveryConfigCount: params.recoveryConfigCount,
    restorePlansEnabled: params.restorePlansEnabled,
    lastRestore,
  }
}

function dayKey(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${m}/${day}`
}

type TaskOutcomeDayRange = {
  label: string
  finishedAfter: string
  finishedBefore: string
}

function taskOutcomeDayRanges(): TaskOutcomeDayRange[] {
  const ranges: TaskOutcomeDayRange[] = []
  const now = new Date()
  for (let i = 6; i >= 0; i--) {
    const d = new Date(now)
    d.setDate(d.getDate() - i)
    d.setHours(0, 0, 0, 0)
    const nextDay = new Date(d)
    nextDay.setDate(nextDay.getDate() + 1)
    ranges.push({
      label: dayKey(d),
      finishedAfter: d.toISOString(),
      finishedBefore: new Date(nextDay.getTime() - 1).toISOString(),
    })
  }
  return ranges
}

function buildTasks7dBuckets(
  ranges: TaskOutcomeDayRange[],
  statistics: TaskStatistics[],
): TaskDayBucket[] {
  return ranges.map((range, index) => {
    const stats = statistics[index]
    return {
      ...range,
      success: stats?.success || 0,
      fail: (stats?.failed || 0) + (stats?.timeout || 0),
      cancel: stats?.cancelled || 0,
    }
  })
}

function summarizeRecoveryDrill24h(stats: TaskStatistics) {
  const success = stats.success
  const failed = stats.failed + stats.timeout
  const done = success + failed
  return {
    total: stats.total,
    running: stats.running,
    success,
    failed,
    successRate: done > 0 ? Math.round((success / done) * 1000) / 10 : null,
  }
}

function repoConfigQuotaGb(repo: ApiRepository): number {
  const config = repo.config
  if (!config || typeof config !== 'object') return 0
  const quota = Number(config.quota_gb)
  return Number.isFinite(quota) ? quota : 0
}

/** NAS / proxy FS expect filesystem sync; S3 with quota_gb=0 is treated as unlimited. */
function isRepoCapacityPending(repo: ApiRepository): boolean {
  const total = Number(repo.storage_total_bytes) || 0
  if (total > 0) return false
  if (repo.repo_type === 's3') return false
  return (repo.repo_type === 'nas' || repo.repo_type === 'proxy_fs') && repo.capacity_probe_status !== 'failed'
}

function plannedLimitBytes(repo: ApiRepository): number {
  const quotaGb = repoConfigQuotaGb(repo)
  return quotaGb > 0 ? Math.round(quotaGb * 1024 ** 3) : 0
}

function storagePoolLabel(repo: ApiRepository): string {
  const proxy = String(repo.bind_node_display_name || '').trim()
  const mountPoint = String(repo.storage_mount_point || '').trim()
  if (proxy && mountPoint) return `${proxy} · ${mountPoint}`
  if (mountPoint) return mountPoint
  return repo.name || 'Storage pool'
}

export function summarizeStorage(repos: ApiRepository[]): StorageSummary {
  let usedBytes = 0
  const pools = new Map<string, StoragePoolSummary>()
  let hasPending = false

  for (const r of repos) {
    usedBytes += Number(r.estimated_usage_bytes) || 0
    const total = Math.max(0, Number(r.storage_total_bytes) || 0)
    if (total > 0) {
      const key = String(r.storage_pool_key || '').trim() || `repository:${Number(r.id) || r.name || pools.size}`
      const current = pools.get(key)
      const nextUsed = Math.max(0, Number(r.storage_used_bytes) || 0)
      const nextAvailable = Math.max(0, Number(r.storage_available_bytes) || 0)
      if (current) {
        current.usedBytes = Math.max(current.usedBytes, nextUsed)
        current.availableBytes = Math.min(current.availableBytes, nextAvailable)
        current.capacityBytes = Math.max(current.capacityBytes, total)
        current.repoCount += 1
      } else {
        pools.set(key, {
          key,
          label: storagePoolLabel(r),
          usedBytes: nextUsed,
          availableBytes: nextAvailable,
          capacityBytes: total,
          repoCount: 1,
        })
      }
      continue
    }
    if (isRepoCapacityPending(r)) hasPending = true
  }

  const poolRows = [...pools.values()]
  const capacityRepoCount = repos.filter(
    (repo) => repo.repo_type === 'nas' || repo.repo_type === 'proxy_fs',
  ).length
  const storageUsedBytes = poolRows.reduce((sum, pool) => sum + pool.usedBytes, 0)
  const availableBytes = poolRows.reduce((sum, pool) => sum + pool.availableBytes, 0)
  const capacityBytes = poolRows.reduce((sum, pool) => sum + pool.capacityBytes, 0)
  const coveredRepoCount = poolRows.reduce((sum, pool) => sum + pool.repoCount, 0)

  let capacityMode: StorageSummary['capacityMode'] = 'empty'
  if (repos.length === 0) {
    capacityMode = 'empty'
  } else if (poolRows.length > 0) {
    capacityMode = 'known'
  } else if (hasPending) {
    capacityMode = 'pending'
  } else {
    capacityMode = 'unlimited'
  }

  return {
    usedBytes,
    storageUsedBytes,
    availableBytes,
    capacityBytes,
    repoCount: repos.length,
    capacityRepoCount,
    coveredRepoCount,
    pools: poolRows,
    capacityMode,
  }
}

function repoCapacityMode(repo: ApiRepository): RepoUsageRow['capacityMode'] {
  const cap = plannedLimitBytes(repo)
  if (cap > 0) return 'known'
  if (isRepoCapacityPending(repo)) return 'pending'
  if (repo.repo_type === 'nas' || repo.repo_type === 'proxy_fs') return 'unavailable'
  return 'unlimited'
}

function repositoryUsageRows(repos: ApiRepository[]): RepoUsageRow[] {
  return repos
    .map((r) => {
      const usedBytes = Number(r.estimated_usage_bytes) || 0
      const capacityBytes = plannedLimitBytes(r)
      const storageTotalBytes = Number(r.storage_total_bytes) || 0
      const storageAvailableBytes = Number(r.storage_available_bytes) || 0
      const capacityMode = repoCapacityMode(r)
      const pct = capacityBytes > 0 ? Math.min(100, Math.round((usedBytes / capacityBytes) * 100)) : null
      return {
        id: Number(r.id) || 0,
        name: r.name || '—',
        health: r.health || '',
        status: r.status || '',
        usedBytes,
        capacityBytes,
        storageTotalBytes,
        storageAvailableBytes,
        pct,
        capacityMode,
        usageProbeStatus: String(r.usage_probe_status || 'pending'),
        capacityProbeStatus: String(r.capacity_probe_status || 'pending'),
      }
    })
    .sort((a, b) => b.usedBytes - a.usedBytes)
}

export function topRepos(repos: ApiRepository[], limit = 5): RepoUsageRow[] {
  return repositoryUsageRows(repos).slice(0, limit)
}

export async function loadDashboardOverview(
  t: (key: string, p?: Record<string, unknown>) => string,
  onProgress?: (overview: DashboardOverview, section: DashboardSection, patch?: Partial<DashboardOverview>) => void,
  options: { signal?: AbortSignal; section?: DashboardSection; previous?: DashboardOverview } = {},
): Promise<DashboardOverview> {
  const signal = options.signal ?? getRouteRequestSignal()
  const taskOutcomeRanges = taskOutcomeDayRanges()
  const statuses = Object.fromEntries(dashboardSections.map(section => [section, 'loading'])) as DashboardOverview['sections']
  let result: DashboardOverview = options.previous ?? {
    sections: statuses,
    orgName: getEffectiveOrgKey() || '—',
    quotaAvailable: false,
    quotaRows: [],
    taskStatisticsState: 'loading',
    taskStats: { total: 0, running: 0, completed: 0, failed: 0, cancelled: 0, byTaskType: {} },
    recoveryDrill24h: { total: 0, running: 0, success: 0, failed: 0, successRate: null },
    tasks7dBuckets: [],
    runningTasks: [],
    alertFiring: 0,
    productionSources: {
      total: 0, available: 0, unavailable: 0,
      hosts: { total: 0, available: 0, unavailable: 0 },
      nas: { total: 0, available: 0, unavailable: 0 },
    },
    recovery: { backupPoliciesEnabled: 0, recoveryConfigCount: 0, restorePlansEnabled: 0, lastRestore: null },
    storage: summarizeStorage([]),
    repositories: [],
    topRepos: [],
    eventsCount: 0,
    events: [],
    activeAlertsCount: 0,
    activeAlerts: [],
    isEmpty: false,
  }
  const sections = dashboardSections
  const publish = (section: DashboardSection, state: DashboardSectionState, patch?: Partial<DashboardOverview>) => {
    result = {
      ...result,
      ...patch,
      sections: { ...result.sections, [section]: state },
      ...(section === 'statistics' ? { taskStatisticsState: state === 'forbidden' ? 'unavailable' : state } : {}),
    }
    result.isEmpty = (['sources', 'storage', 'recovery', 'statistics'] as const).every(key => result.sections[key] === 'ready')
      && !result.productionSources.total && !result.storage.repoCount
      && !result.recovery.backupPoliciesEnabled && !result.taskStats.total
    if (!signal.aborted) onProgress?.(result, section, patch)
  }

  const loaders: Record<DashboardSection, (init: RequestInit) => Promise<Partial<DashboardOverview>>> = {
    sources: async init => ({ productionSources: await productionSourceSummary(init) }),
    storage: async init => ({
      storage: unwrapApiPayload<StorageSummary>(await api<unknown>('/api/v1/storage/repositories/dashboard-summary/', init)),
    }),
    running: async init => ({ runningTasks: (await listTasks({ status: 'running', page_size: 8 }, init)).results }),
    recovery: async init => {
      const [policies, configs, plans, tasks] = await Promise.all([
        listBackupPolicies({ page_size: 1, is_active: true }, init),
        listBackupConfigs({ page_size: 1, recovery_plan_enabled: 'true' }, init),
        listRestorePlans({ enabled: true, page_size: 1 }, init),
        listTasks({ task_type: 'restore', page_size: 30, exclude_insight_workspace_restores: 'true' }, init),
      ])
      return { recovery: summarizeRecovery({
        backupPoliciesEnabled: policies.count,
        recoveryConfigCount: configs.count,
        restorePlansEnabled: plans.count,
        restoreTasks: tasks.results,
      }) }
    },
    quota: async init => {
      const license = await fetchCurrentLicense(init)
      return {
        orgName: license.organization_name || license.license?.organization_name || result.orgName,
        quotaAvailable: true,
        quotaRows: buildQuotaRows(license.usage || {}, license.limits || {},
          license.instance_shared ? undefined : (license.license as Record<string, unknown> | undefined)),
      }
    },
    repositories: async init => {
      const rows = repositoryUsageRows(asList<ApiRepository>(await api<unknown>('/api/v1/storage/repositories/?page_size=200', init)))
      return { repositories: rows, topRepos: rows.slice(0, 5) }
    },
    alerts: async init => {
      const [firing, acknowledged] = await Promise.all([
        listRecords({ status: 'firing', page_size: 5 }, init),
        listRecords({ status: 'acknowledged', page_size: 5 }, init),
      ])
      return {
        alertFiring: firing.count,
        activeAlertsCount: firing.count + acknowledged.count,
        activeAlerts: [...firing.results, ...acknowledged.results].map(dashboardAlertItem)
          .sort((left, right) => Date.parse(right.at || '') - Date.parse(left.at || '')),
      }
    },
    events: async init => {
      const page = await loadEventsPreview(init)
      return {
        eventsCount: page.count,
        events: page.results.map(event => ({
          id: String(event.id ?? ''), event_type: event.event_type, title: event.title || t('dashboard.eventsTitle'),
          detail: event.details || '', severity: event.severity || 'information', at: event.occurred_at,
          to: event.target_path || `/ops/events?eventId=${encodeURIComponent(String(event.id ?? ''))}`,
        })),
      }
    },
    statistics: async init => {
      const summary = await dashboardTaskStatistics(new Date(Date.now() - 86_400_000).toISOString(),
        taskOutcomeRanges.map(range => ({ finished_after: range.finishedAfter, finished_before: range.finishedBefore })), init)
      const stats = summary.total
      return {
        taskStats: { total: stats.total, running: stats.running, completed: stats.success,
          failed: stats.failed, cancelled: stats.cancelled, byTaskType: stats.by_task_type || {} },
        recoveryDrill24h: summarizeRecoveryDrill24h(summary.recovery_24h),
        tasks7dBuckets: buildTasks7dBuckets(taskOutcomeRanges, summary.days),
      }
    },
  }
  const load = async (section: DashboardSection) => {
    if (options.section) publish(section, 'loading')
    try {
      const patch = await withRequestDeadline(requestSignal => loaders[section]({ signal: requestSignal }), signal)
      publish(section, 'ready', patch)
    } catch (error) {
      publish(section, toApiError(error).status === 403 ? 'forbidden' : 'unavailable')
    }
  }
  if (options.section) {
    await load(options.section)
  } else {
    const core = sections.slice(0, 3).map(load)
    await Promise.race(core)
    await Promise.all([...core, ...sections.slice(3).map(load)])
  }
  return result
}
