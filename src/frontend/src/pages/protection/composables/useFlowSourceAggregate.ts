import { type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  useProtectionDemoStore,
  type DemoBackup,
  type DemoGlobalFilter,
  type DemoPolicy,
  type DemoSnapshot,
  type DemoTarget,
  type BackupStatus,
} from '../../../composables/useProtectionDemoStore'
import { formatLocalDateTime } from '../../../lib/dateTime'
import { taskStatusTone } from '../../../lib/taskStatusDisplay'

export type FlowSourceRow = {
  id: string
  name: string
  hostname: string
  nodeName: string
  nodeIp: string
  status: 'active' | 'inactive' | 'error' | 'probing' | 'removing' | 'remove_failed' | 'removed' | 'upgrading' | 'restarting' | 'verifying' | 'verification_pending' | 'cleaning_up' | 'failed' | 'upgrade_failed' | 'deregistration_failed'
  availability?: 'online' | 'offline'
  registeredAt: string
  type: 'host' | 'nas'
  protocol?: 'nfs' | 'smb'
  platform?: 'linux' | 'windows' | 'macos'
  connectionUri?: string
  boundNodeId?: number | null
  mountStatus?: string
  mountPoint?: string
  refId?: number
  bound_node_id?: number | null
  mount_status?: string
  pipeline_step?: 1 | 2 | 3
  /** Agent inventory summary; omitted for NAS sources. */
  cpuCores?: number | null
  memoryTotalBytes?: number | null
  diskCount?: number | null
  osName?: string
  arch?: string
  capacityUsedBytes?: number | null
  capacityTotalBytes?: number | null
  backup_configs?: {
    count: number
    ids: number[]
    configs?: unknown[]
    dirs_preview?: Array<Record<string, unknown>>
    dirs_overflow_count?: number
    repos_preview?: Array<Record<string, unknown>>
    repos_overflow_count?: number
  }
  policies?: {
    count: number
    names: string[]
    items?: Array<Record<string, unknown>>
  }
  filters?: {
    count: number
    names: string[]
    items?: Array<Record<string, unknown>>
  }
  runtime?: Record<string, unknown>
}

export type DemoFlowTaskKind = 'backup_create' | 'restore'
export type DemoFlowTaskStatus = 'running' | 'completed' | 'failed'

export type DemoFlowTask = {
  id: string
  title: string
  kind: DemoFlowTaskKind
  status: DemoFlowTaskStatus
  progress: number
  phaseIndex: number
  startedAt: string
  endedAt: string | null
  backupId?: string
}

export type FlowBackupDirRow = {
  hostId: string
  hostName: string
  hostname: string
  path: string
}

export type FlowSourceTaskStatus = DemoFlowTaskStatus | 'idle'

export type FlowSourceBackupAggregate = {
  backupCount: number
  backupIds: string[]
  dirs: FlowBackupDirRow[]
  targets: DemoTarget[]
  lastBackupAt: string | null
  progress: number
  taskStatus: FlowSourceTaskStatus
  runningCount: number
  totalTaskCount: number
  restoreRunning: number
  restoreTotal: number
  policies: DemoPolicy[]
  filters: DemoGlobalFilter[]
}

export function useFlowSourceAggregate(
  backupFlowTasks: Ref<DemoFlowTask[]>,
  restoreFlowTasks: Ref<DemoFlowTask[]>,
) {
  const { t } = useI18n()
  const store = useProtectionDemoStore()

  function fmtLocalTime(raw: string) {
    return formatLocalDateTime(raw, '—')
  }

  function backupRowLatestSnapshot(backup: DemoBackup | undefined): DemoSnapshot | null {
    if (!backup?.snapshots.length) return null
    return backup.snapshots.reduce<DemoSnapshot | null>((latest, snap) => {
      if (!latest) return snap
      return new Date(snap.endTime).getTime() > new Date(latest.endTime).getTime() ? snap : latest
    }, null)
  }

  function backupRowNodeHostname(hostId: string) {
    return store.getHost(hostId)?.hostname ?? store.getNas(hostId)?.hostname ?? t('protection.backupDetail.durationDash')
  }

  function backupsForSource(sourceId: string) {
    return store.backups.value.filter((b) => b.sources.some((s) => s.hostId === sourceId))
  }

  function backupIdsForSource(sourceId: string) {
    return backupsForSource(sourceId).map((b) => b.id)
  }

  function tasksForSource(sourceId: string) {
    const ids = new Set(backupIdsForSource(sourceId))
    return backupFlowTasks.value.filter((task) => task.backupId && ids.has(task.backupId))
  }

  function restoreTasksForSource(sourceId: string) {
    const ids = new Set(backupIdsForSource(sourceId))
    return restoreFlowTasks.value.filter((task) => task.backupId && ids.has(task.backupId))
  }

  function dirsForSource(sourceId: string): FlowBackupDirRow[] {
    const seen = new Set<string>()
    const dirs: FlowBackupDirRow[] = []
    for (const backup of backupsForSource(sourceId)) {
      const latestSnapshot = backupRowLatestSnapshot(backup)
      const entries = latestSnapshot?.dirs.length
        ? latestSnapshot.dirs
        : backup.sources.map((source) => ({
            hostId: source.hostId,
            path: source.path,
            hostName: store.getNodeName(source.hostId),
          }))
      for (const dir of entries) {
        if (dir.hostId !== sourceId) continue
        const key = `${dir.hostId}::${dir.path}`
        if (seen.has(key)) continue
        seen.add(key)
        dirs.push({
          hostId: dir.hostId,
          hostName: dir.hostName || store.getNodeName(dir.hostId),
          hostname: backupRowNodeHostname(dir.hostId),
          path: dir.path,
        })
      }
    }
    return dirs
  }

  function targetsForSource(sourceId: string): DemoTarget[] {
    const seen = new Set<string>()
    const targets: DemoTarget[] = []
    for (const backup of backupsForSource(sourceId)) {
      if (!backup.targetId || seen.has(backup.targetId)) continue
      const target = store.getTarget(backup.targetId)
      if (!target) continue
      seen.add(backup.targetId)
      targets.push(target)
    }
    return targets
  }

  function aggregateTaskStatus(tasks: DemoFlowTask[]): FlowSourceTaskStatus {
    if (!tasks.length) return 'idle'
    if (tasks.some((task) => task.status === 'running')) return 'running'
    if (tasks.some((task) => task.status === 'failed')) return 'failed'
    if (tasks.every((task) => task.status === 'completed')) return 'completed'
    return 'idle'
  }

  function aggregateProgress(tasks: DemoFlowTask[]) {
    const running = tasks.filter((task) => task.status === 'running')
    if (running.length) return Math.max(...running.map((task) => task.progress))
    if (tasks.length && tasks.every((task) => task.status === 'completed')) return 100
    return 0
  }

  function aggregateForSource(sourceId: string): FlowSourceBackupAggregate {
    const backups = backupsForSource(sourceId)
    const tasks = tasksForSource(sourceId)
    const backupIds = backups.map((b) => b.id)
    const policyMap = new Map<string, DemoPolicy>()
    const filterMap = new Map<string, DemoGlobalFilter>()
    for (const backup of backups) {
      const policy = backup.policyId ? store.getPolicy(backup.policyId) : null
      if (policy) policyMap.set(policy.id, policy)
      const filter = backup.globalFilterId ? store.getGlobalFilter(backup.globalFilterId) : null
      if (filter) filterMap.set(filter.id, filter)
    }
    const restoreTasks = restoreTasksForSource(sourceId)
    let lastBackupAt: string | null = null
    for (const backup of backups) {
      const candidate = backup.latestSnapshotAt
      if (!candidate) continue
      if (!lastBackupAt || new Date(candidate).getTime() > new Date(lastBackupAt).getTime()) {
        lastBackupAt = candidate
      }
    }
    return {
      backupCount: backups.length,
      backupIds,
      dirs: dirsForSource(sourceId),
      targets: targetsForSource(sourceId),
      lastBackupAt,
      progress: aggregateProgress(tasks),
      taskStatus: aggregateTaskStatus(tasks),
      runningCount: tasks.filter((task) => task.status === 'running').length,
      totalTaskCount: tasks.length,
      restoreRunning: restoreTasks.filter((task) => task.status === 'running').length,
      restoreTotal: restoreTasks.length,
      policies: [...policyMap.values()],
      filters: [...filterMap.values()],
    }
  }

  function flowTaskStatusLabel(status: DemoFlowTaskStatus) {
    if (status === 'running') return t('protection.backupsPage.flowTaskStatusRunning')
    if (status === 'completed') return t('protection.backupsPage.flowTaskStatusCompleted')
    return t('protection.backupsPage.flowTaskStatusFailed')
  }

  function flowTaskStatusTag(status: DemoFlowTaskStatus): 'success' | 'info' | 'danger' {
    const tone = taskStatusTone(status)
    if (tone === 'success' || tone === 'danger') return tone
    return 'info'
  }

  function sourceAggregateStatusLabel(status: FlowSourceTaskStatus) {
    if (status === 'idle') return t('protection.backupsPage.flowSourceTaskIdle')
    return flowTaskStatusLabel(status)
  }

  function sourceAggregateStatusTag(status: FlowSourceTaskStatus): 'success' | 'warning' | 'danger' | 'info' {
    if (status === 'idle') return 'info'
    return flowTaskStatusTag(status)
  }

  function sourceAggregateStatusText(sourceId: string) {
    const agg = aggregateForSource(sourceId)
    const label = sourceAggregateStatusLabel(agg.taskStatus)
    if (agg.totalTaskCount > 1 && agg.taskStatus === 'running') {
      return t('protection.backupsPage.flowSourceTaskRunningCount', {
        label,
        running: agg.runningCount,
        total: agg.totalTaskCount,
      })
    }
    return label
  }

  function formatSourceLastBackup(sourceId: string) {
    const agg = aggregateForSource(sourceId)
    if (agg.lastBackupAt) return fmtLocalTime(agg.lastBackupAt)
    if (agg.taskStatus === 'running') return t('protection.backupsPage.flowBackupLastBackupRunning')
    return t('protection.backupsPage.flowBackupLastBackupDash')
  }

  function backupRowDisplayName(row: DemoFlowTask) {
    if (row.backupId) {
      const b = store.getBackup(row.backupId)
      if (b?.name) return b.name
    }
    const sep = ' · '
    const i = row.title.indexOf(sep)
    if (i >= 0) {
      const rest = row.title.slice(i + sep.length).trim()
      if (rest) return rest
    }
    return row.title
  }

  function backupConfigStatusLabel(status: BackupStatus) {
    if (status === 'backing_up') return t('protection.backupsPage.statusBackingUp')
    if (status === 'completed') return t('protection.backupsPage.statusCompleted')
    if (status === 'failed') return t('protection.backupsPage.statusFailed')
    return t('protection.backupsPage.statusIdle')
  }

  function backupConfigStatusTag(status: BackupStatus): 'success' | 'warning' | 'danger' | 'info' {
    if (status === 'backing_up') return 'info'
    if (status === 'completed') return 'success'
    if (status === 'failed') return 'danger'
    return 'info'
  }

  function backupPrimaryPath(backup: DemoBackup, sourceId: string) {
    const match = backup.sources.find((s) => s.hostId === sourceId)
    return match?.path ?? backup.sources[0]?.path ?? t('protection.backupDetail.durationDash')
  }

  return {
    fmtLocalTime,
    backupRowLatestSnapshot,
    backupRowDisplayName,
    backupsForSource,
    backupIdsForSource,
    tasksForSource,
    restoreTasksForSource,
    aggregateForSource,
    sourceAggregateStatusLabel,
    sourceAggregateStatusTag,
    sourceAggregateStatusText,
    formatSourceLastBackup,
    flowTaskStatusLabel,
    flowTaskStatusTag,
    backupConfigStatusLabel,
    backupConfigStatusTag,
    backupPrimaryPath,
  }
}
