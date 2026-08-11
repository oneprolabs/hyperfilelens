import { api } from './api'
import { unwrapApiPayload, asList } from './parse'
import { getEffectiveOrgKey } from '../composables/useAuth'
import { listRecords, listPolicies as listAlertPolicies } from './alertApi'
import { auditStatistics } from './auditApi'
import { taskStatistics, listTasks, type TaskRow, type TaskStatistics } from './taskApi'
import { listAllNodes } from './nodeApi'
import { fetchCurrentLicense } from './subscriptionApi'
import { buildQuotaRows, type QuotaUsageRow } from './licenseQuotaDisplay'
import { productionSourceSummary, type ProductionSourceSummary } from './sourceApi'
import { listBackupConfigs } from './protectionBackupConfigApi'
import { listRestorePlans } from './restoreApi'
import { summarizeNodeAvailability } from './nodeAvailability'
import type { ApiNode } from '../types/node'

export type StorageSummary = {
  usedBytes: number
  capacityBytes: number
  repoCount: number
  /** known = sum of repo capacities; unlimited = object storage without cap; pending = awaiting sync */
  capacityMode: 'empty' | 'known' | 'unlimited' | 'pending' | 'unavailable'
}

export type RepoUsageRow = {
  id: number
  name: string
  health: string
  status: string
  usedBytes: number
  capacityBytes: number
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

export type DashboardAttentionItem = {
  id: string
  kind: 'task' | 'alert' | 'node' | 'source' | 'audit'
  title: string
  detail: string
  to: string
  at?: string
}

export type TaskDayBucket = {
  label: string
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

export type DashboardOverview = {
  orgName: string
  licenseValid: boolean
  licenseKey?: string
  licenseExpiresLabel?: string
  quotaRows: QuotaUsageRow[]
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
    success: number
    failed: number
    successRate: number | null
  }
  tasks7dBuckets: TaskDayBucket[]
  runningTasks: TaskRow[]
  alertFiring: number
  alertAcknowledged: number
  alertPolicies: number
  alertPoliciesEnabled: number
  audit: {
    todayCount: number
    totalCount: number
    successRate: number
    failureCount: number
  }
  nodesOnline: number
  nodesTotal: number
  nodesOffline: number
  nodesByRole: Record<string, number>
  productionSources: ProductionSourceSummary
  protectionPolicies: number
  protectionPoliciesEnabled: number
  recovery: RecoverySummary
  storage: StorageSummary
  topRepos: RepoUsageRow[]
  notificationFailed: number
  attentionCount: number
  attention: DashboardAttentionItem[]
  isEmpty: boolean
}

type AttentionPage = {
  count: number
  results: DashboardAttentionItem[]
}

async function loadAttentionPage(): Promise<AttentionPage> {
  const data = unwrapApiPayload<Record<string, unknown>>(
    await api<unknown>('/api/v1/monitors/attention/?page=1&page_size=10&preview=diverse'),
  )
  return {
    count: Number(data.count) || 0,
    results: asList<DashboardAttentionItem>(data),
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
  usage_probe_status?: string
  capacity_probe_status?: string
}

type ApiPolicy = {
  id: number
  name?: string
  is_active?: boolean
  enabled?: boolean
}

function isBackupPolicyEnabled(policy: ApiPolicy): boolean {
  if (policy.is_active === false) return false
  if (policy.enabled === false) return false
  return true
}

const RESTORE_TERMINAL = new Set(['success', 'failed', 'timeout', 'cancelled'])

function summarizeRecovery(params: {
  policies: ApiPolicy[]
  backupConfigs: Array<{ recovery_plan_enabled?: boolean }>
  restorePlansEnabled: number
  restoreTasks: TaskRow[]
}): RecoverySummary {
  const backupPoliciesEnabled = params.policies.filter(isBackupPolicyEnabled).length
  const recoveryConfigCount = params.backupConfigs.filter((c) => c.recovery_plan_enabled === true).length

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
    backupPoliciesEnabled,
    recoveryConfigCount,
    restorePlansEnabled: params.restorePlansEnabled,
    lastRestore,
  }
}

function dayKey(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${m}/${day}`
}

function buildTasks7dBuckets(tasks: TaskRow[]): TaskDayBucket[] {
  const buckets: TaskDayBucket[] = []
  const map = new Map<string, TaskDayBucket>()
  const now = new Date()
  for (let i = 6; i >= 0; i--) {
    const d = new Date(now)
    d.setDate(d.getDate() - i)
    const label = dayKey(d)
    const b = { label, success: 0, fail: 0, cancel: 0 }
    map.set(label, b)
    buckets.push(b)
  }
  for (const task of tasks) {
    const iso = task.finished_at || task.created_at
    if (!iso) continue
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) continue
    const label = dayKey(d)
    const b = map.get(label)
    if (!b) continue
    if (task.status === 'success') b.success += 1
    else if (task.status === 'failed' || task.status === 'timeout') b.fail += 1
    else if (task.status === 'cancelled') b.cancel += 1
  }
  return buckets
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
  const cap = Number(repo.capacity_bytes) || 0
  if (cap > 0) return false
  if (repo.repo_type === 's3') return repoConfigQuotaGb(repo) > 0
  return (repo.repo_type === 'nas' || repo.repo_type === 'proxy_fs') && repo.capacity_probe_status !== 'failed'
}

function summarizeStorage(repos: ApiRepository[]): StorageSummary {
  let usedBytes = 0
  let capacityBytes = 0
  let hasKnownCapacity = false
  let hasPending = false

  for (const r of repos) {
    usedBytes += Number(r.estimated_usage_bytes) || 0
    const cap = Number(r.capacity_bytes) || 0
    if (cap > 0) {
      capacityBytes += cap
      hasKnownCapacity = true
      continue
    }
    if (isRepoCapacityPending(r)) hasPending = true
  }

  let capacityMode: StorageSummary['capacityMode'] = 'empty'
  if (repos.length === 0) {
    capacityMode = 'empty'
  } else if (hasKnownCapacity) {
    capacityMode = 'known'
  } else if (hasPending) {
    capacityMode = 'pending'
  } else {
    capacityMode = 'unlimited'
  }

  return { usedBytes, capacityBytes, repoCount: repos.length, capacityMode }
}

function repoCapacityMode(repo: ApiRepository): RepoUsageRow['capacityMode'] {
  const cap = Number(repo.capacity_bytes) || 0
  if (cap > 0) return 'known'
  if (isRepoCapacityPending(repo)) return 'pending'
  if (repo.repo_type === 'nas' || repo.repo_type === 'proxy_fs') return 'unavailable'
  return 'unlimited'
}

function topRepos(repos: ApiRepository[], limit = 5): RepoUsageRow[] {
  return repos
    .map((r) => {
      const usedBytes = Number(r.estimated_usage_bytes) || 0
      const capacityBytes = Number(r.capacity_bytes) || 0
      const capacityMode = repoCapacityMode(r)
      const pct = capacityBytes > 0 ? Math.min(100, Math.round((usedBytes / capacityBytes) * 100)) : null
      return {
        id: Number(r.id) || 0,
        name: r.name || '—',
        health: r.health || '',
        status: r.status || '',
        usedBytes,
        capacityBytes,
        pct,
        capacityMode,
        usageProbeStatus: String(r.usage_probe_status || 'pending'),
        capacityProbeStatus: String(r.capacity_probe_status || 'pending'),
      }
    })
    .sort((a, b) => b.usedBytes - a.usedBytes)
    .slice(0, limit)
}

function countNodesByRole(nodes: ApiNode[]): Record<string, number> {
  const out: Record<string, number> = { agent: 0, proxy: 0, gateway: 0 }
  for (const n of nodes) {
    if (n.role in out) out[n.role] += 1
    else out[n.role] = (out[n.role] || 0) + 1
  }
  return out
}

export async function loadDashboardOverview(
  t: (key: string, p?: Record<string, unknown>) => string,
): Promise<DashboardOverview> {
  const orgKey = getEffectiveOrgKey()
  const recoveryDrillStartedAfter = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()

  const [
    license,
    taskStats,
    recoveryDrill24hStats,
    tasks7dPage,
    runningTasksPage,
    productionSources,
    nodes,
    reposRaw,
    alertsFiringPage,
    alertsAckPage,
    alertPoliciesPage,
    auditStats,
    protectionPoliciesRaw,
    notifyStats,
    backupConfigsPage,
    restorePlansEnabledPage,
    restoreTasksPage,
    attentionPage,
  ] = await Promise.all([
    fetchCurrentLicense().catch(() => ({
      is_valid: false,
      organization_name: '',
      license: undefined,
      limits: {},
      usage: {},
    })),
    taskStatistics().catch(() => ({
      total: 0,
      running: 0,
      success: 0,
      failed: 0,
      cancelled: 0,
      timeout: 0,
      by_task_type: {},
    })),
    taskStatistics({ task_type: 'restore', created_after: recoveryDrillStartedAfter }).catch(() => ({
      total: 0,
      running: 0,
      success: 0,
      failed: 0,
      cancelled: 0,
      timeout: 0,
      by_task_type: {},
    })),
    listTasks({ time_range: '7d', page_size: 500 }).catch(() => ({ count: 0, results: [] })),
    listTasks({ status: 'running', page_size: 8 }).catch(() => ({ count: 0, results: [] })),
    productionSourceSummary().catch(() => ({
      total: 0,
      available: 0,
      unavailable: 0,
      hosts: { total: 0, available: 0, unavailable: 0 },
      nas: { total: 0, available: 0, unavailable: 0 },
    })),
    listAllNodes().catch(() => []),
    api<unknown>('/api/v1/storage/repositories/').then((raw) => asList<ApiRepository>(raw)).catch(() => []),
    listRecords({ status: 'firing', page_size: 10 }).catch(() => ({ count: 0, results: [] })),
    listRecords({ status: 'acknowledged', page_size: 1 }).catch(() => ({ count: 0, results: [] })),
    listAlertPolicies({ page_size: 1 }).catch(() => ({ count: 0, results: [] })),
    auditStatistics().catch(() => ({
      total_count: 0,
      today_count: 0,
      success_rate: 0,
      failure_count: 0,
      action_stats: {},
      resource_stats: {},
      result_stats: {},
    })),
    api<unknown>('/api/v1/protection/policies/?page_size=200').then((raw) => asList<ApiPolicy>(raw)).catch(() => []),
    api<unknown>('/api/v1/notification/logs/stats/').then((raw) => unwrapApiPayload<{ failed?: number }>(raw)).catch(() => ({ failed: 0 })),
    listBackupConfigs({ page_size: 200 }).catch(() => ({ count: 0, results: [] })),
    listRestorePlans({ enabled: true, page_size: 1 }).catch(() => ({ count: 0, results: [] })),
    listTasks({ task_type: 'restore', page_size: 30 }).catch(() => ({ count: 0, results: [] })),
    loadAttentionPage().catch(() => ({ count: 0, results: [] })),
  ])

  const nodeAvailability = summarizeNodeAvailability(nodes)
  const nodesTotal = nodes.length
  const orgName =
    license.organization_name ||
    license.license?.organization_name ||
    orgKey ||
    '—'

  const lic = license.license
  let licenseExpiresLabel: string | undefined
  if (lic?.is_perpetual) {
    licenseExpiresLabel = t('dashboard.licensePerpetual')
  } else if (lic?.expires_at) {
    licenseExpiresLabel = t('dashboard.licenseExpires', {
      date: new Date(lic.expires_at).toLocaleDateString(),
      days: lic.days_until_expiry ?? license.days_until_expiry ?? '—',
    })
  } else if (license.is_valid && license.days_until_expiry != null) {
    licenseExpiresLabel = t('dashboard.licenseExpires', {
      date: '—',
      days: license.days_until_expiry,
    })
  }

  const protectionPolicies = protectionPoliciesRaw.length
  const protectionPoliciesEnabled = protectionPoliciesRaw.filter(isBackupPolicyEnabled).length

  const recovery = summarizeRecovery({
    policies: protectionPoliciesRaw,
    backupConfigs: backupConfigsPage.results,
    restorePlansEnabled: restorePlansEnabledPage.count || restorePlansEnabledPage.results.length,
    restoreTasks: restoreTasksPage.results,
  })

  const alertPoliciesCount = alertPoliciesPage.count
  let alertPoliciesEnabled = 0
  try {
    const enabledPage = await listAlertPolicies({ enabled: 'true', page_size: 1 })
    alertPoliciesEnabled = enabledPage.count
  } catch {
    alertPoliciesEnabled = alertPoliciesCount
  }

  const isEmpty =
    nodesTotal === 0 &&
    productionSources.total === 0 &&
    taskStats.total === 0 &&
    protectionPolicies === 0 &&
    reposRaw.length === 0

  return {
    orgName,
    licenseValid: Boolean(license.is_valid),
    licenseKey: license.instance_shared ? undefined : license.license?.license_key,
    licenseExpiresLabel,
    quotaRows: buildQuotaRows(
      license.usage || {},
      license.limits || {},
      license.instance_shared ? undefined : (lic as Record<string, unknown> | undefined),
    ),
    taskStats: {
      total: taskStats.total,
      running: taskStats.running,
      completed: taskStats.success,
      failed: taskStats.failed,
      cancelled: taskStats.cancelled,
      byTaskType: taskStats.by_task_type || {},
    },
    recoveryDrill24h: summarizeRecoveryDrill24h(recoveryDrill24hStats),
    tasks7dBuckets: buildTasks7dBuckets(tasks7dPage.results),
    runningTasks: runningTasksPage.results,
    alertFiring: alertsFiringPage.count || alertsFiringPage.results.length,
    alertAcknowledged: alertsAckPage.count || 0,
    alertPolicies: alertPoliciesCount,
    alertPoliciesEnabled,
    audit: {
      todayCount: auditStats.today_count,
      totalCount: auditStats.total_count,
      successRate: auditStats.success_rate,
      failureCount: auditStats.failure_count,
    },
    nodesOnline: nodeAvailability.online,
    nodesTotal,
    nodesOffline: nodeAvailability.offline,
    nodesByRole: countNodesByRole(nodes),
    productionSources,
    protectionPolicies,
    protectionPoliciesEnabled,
    recovery,
    storage: summarizeStorage(reposRaw),
    topRepos: topRepos(reposRaw),
    notificationFailed: Number(notifyStats.failed) || 0,
    attentionCount: attentionPage.count,
    attention: attentionPage.results,
    isEmpty,
  }
}
