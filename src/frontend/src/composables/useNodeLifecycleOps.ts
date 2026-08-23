import { computed, onUnmounted, ref } from 'vue'
import type { Composer } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { afterOverlayDismiss, releaseStalePopupLock } from '../lib/uiDefer'
import {
  NODE_LIFECYCLE_MAX_CONCURRENT,
  fetchLifecycleWatch,
  previewNodeOperationsBatch,
  startNodeOperation,
  startNodeOperationsBatch,
  NodeLifecycleApiError,
} from '../lib/nodeApi'
import type { NodeLifecycleScope } from '../lib/nodeApi'
import { apiErrorMessage } from '../lib/api'
import { parseSemver, semverCompare } from '../lib/agentVersion'
import { logger } from '../lib/logger'
import { buildUpgradeDiskSkipDetails } from '../lib/nodeLifecycleUpgradeConfirm'
import type { ApiNode, NodeRole } from '../types/node'
import type {
  LifecycleQueueItem,
  LifecycleQueueSnapshot,
  NodeLifecycleKind,
  NodeOperationBatchPreview,
} from '../types/nodeLifecycle'
import { mergeNodeListDuringLifecycleBatch } from './useNodeConnectionDisplay'

const STORAGE_KEY = 'hfl-node-lifecycle-queue'
const PERSISTED_QUEUE_MAX_AGE_MS = 30 * 60 * 1000

type PersistedQueue = {
  batchId: string
  kind: NodeLifecycleKind
  role: NodeRole
  scope?: NodeLifecycleScope
  maxConcurrent: number
  savedAt: number
  queued: LifecycleQueueItem[]
  running: LifecycleQueueItem[]
}

function newBatchId() {
  return `batch-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function loadPersisted(): PersistedQueue | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<PersistedQueue>
    if (
      typeof parsed.batchId !== 'string' ||
      (parsed.kind !== 'upgrade' && parsed.kind !== 'remove') ||
      typeof parsed.role !== 'string' ||
      !Array.isArray(parsed.running) ||
      !Array.isArray(parsed.queued)
    ) {
      sessionStorage.removeItem(STORAGE_KEY)
      return null
    }
    return {
      ...parsed,
      // Pre-expiry snapshots are safe to read but must be server-validated before reuse.
      savedAt: typeof parsed.savedAt === 'number' ? parsed.savedAt : 0,
    } as PersistedQueue
  } catch {
    sessionStorage.removeItem(STORAGE_KEY)
    return null
  }
}

function savePersisted(snapshot: PersistedQueue | null) {
  if (!snapshot || (snapshot.running.length === 0 && snapshot.queued.length === 0)) {
    sessionStorage.removeItem(STORAGE_KEY)
    return
  }
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ ...snapshot, savedAt: Date.now() }))
}

const LIFECYCLE_FAILED_CONFIRM_POLLS = 2
const UPGRADE_ACTIVE_STATES = ['upgrading', 'restarting', 'verifying', 'queued'] as const
const LIFECYCLE_SPINNING_STATES = [
  'upgrading',
  'restarting',
  'verifying',
  'removing',
  'cleaning_up',
  'queued',
] as const

function isUpgradeLifecycleState(state: string | undefined | null): boolean {
  return !!state && UPGRADE_ACTIVE_STATES.includes(state as (typeof UPGRADE_ACTIVE_STATES)[number])
}

function versionReachedTarget(node: ApiNode, targetVersion: string | undefined): boolean {
  const target = String(targetVersion || '').trim()
  const current = String(node.version || '').trim()
  if (!parseSemver(target) || !parseSemver(current)) return false
  return semverCompare(current, target) >= 0
}

function localLifecycleItem(
  running: LifecycleQueueItem[],
  queued: LifecycleQueueItem[],
  nodeId: number,
): LifecycleQueueItem | undefined {
  return (
    running.find((item) => item.nodeId === nodeId) ||
    queued.find((item) => item.nodeId === nodeId)
  )
}

function lifecycleDone(
  node: ApiNode,
  kind: NodeLifecycleKind,
  item?: LifecycleQueueItem,
): boolean {
  const lc = node.lifecycle
  if (lc?.kind === kind && lc.state !== 'failed') {
    return false
  }
  if (kind === 'remove') {
    return node.is_deleted
  }
  const target = item?.targetVersion || lc?.target_version
  if (!target || !versionReachedTarget(node, target)) {
    return false
  }
  return node.availability === 'online' && node.routable === true
}

function queueItemMeta(
  node: ApiNode | undefined,
  nodeId: number,
  fallbackRole: NodeRole,
): Pick<LifecycleQueueItem, 'name' | 'ipAddress' | 'role'> {
  return {
    name: node?.name || String(nodeId),
    ipAddress: node?.ip_address ?? null,
    role: node?.role || fallbackRole,
  }
}

export function useNodeLifecycleOps(options: {
  role: NodeRole | (() => NodeRole)
  scope?: NodeLifecycleScope | (() => NodeLifecycleScope)
  t: Composer['t']
  onRefresh?: () => Promise<void> | void
  onLifecyclePatch?: (nodes: ApiNode[]) => void
}) {
  const { t } = options
  const resolveRole = (): NodeRole =>
    typeof options.role === 'function' ? options.role() : options.role
  const resolveScope = (): NodeLifecycleScope =>
    typeof options.scope === 'function' ? options.scope() : options.scope || 'tenant'
  const batchId = ref<string | null>(null)
  const activeKind = ref<NodeLifecycleKind | null>(null)
  const maxConcurrent = ref(NODE_LIFECYCLE_MAX_CONCURRENT)
  const running = ref<LifecycleQueueItem[]>([])
  const queued = ref<LifecycleQueueItem[]>([])
  const completed = ref<LifecycleQueueItem[]>([])
  const failed = ref<LifecycleQueueItem[]>([])
  const skipped = ref<Array<{ nodeId: number; name: string; reason: string }>>([])
  const lastStartErrors = ref<Array<Record<string, unknown>>>([])
  const polling = ref(false)
  const pollRole = ref<NodeRole | null>(null)
  const upgradeConfirmOpen = ref(false)
  const upgradeConfirmPreview = ref<NodeOperationBatchPreview | null>(null)
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let pollInFlight = false
  let restoreExpiryAt: number | null = null
  let upgradeConfirmResolver: ((confirmed: boolean) => void) | null = null
  let upgradeConfirmSettled = false

  function lifecycleEntity(item: LifecycleQueueItem): string {
    return item.role === 'gateway'
      ? t('nodeLifecycle.entityGateway')
      : t('nodeLifecycle.entityHost')
  }

  function notifyLifecycleResult(item: LifecycleQueueItem, outcome: 'success' | 'failed') {
    if (item.notified) return
    item.notified = true
    const entity = lifecycleEntity(item)
    const name = item.name
    const ip = String(item.ipAddress || '').trim()
    const hasIp = ip.length > 0
    const error = item.error || 'failed'

    if (item.kind === 'remove') {
      if (outcome === 'success') {
        ElMessage.success(
          hasIp
            ? t('nodeLifecycle.removeSuccess', { entity, name, ip })
            : t('nodeLifecycle.removeSuccessNoIp', { entity, name }),
        )
      } else {
        ElMessage.error(
          hasIp
            ? t('nodeLifecycle.removeFailed', { entity, name, ip, error })
            : t('nodeLifecycle.removeFailedNoIp', { entity, name, error }),
        )
      }
      return
    }

    const version = String(item.targetVersion || '').trim() || '—'
    if (outcome === 'success') {
      ElMessage.success(
        hasIp
          ? t('nodeLifecycle.upgradeSuccess', { entity, name, ip, version })
          : t('nodeLifecycle.upgradeSuccessNoIp', { entity, name, version }),
      )
    } else {
      ElMessage.error(
        hasIp
          ? t('nodeLifecycle.upgradeFailed', { entity, name, ip, error })
          : t('nodeLifecycle.upgradeFailedNoIp', { entity, name, error }),
      )
    }
  }

  function markLifecycleCompleted(item: LifecycleQueueItem) {
    completed.value.push(item)
    notifyLifecycleResult(item, 'success')
  }

  function markLifecycleFailed(item: LifecycleQueueItem) {
    failed.value.push(item)
    notifyLifecycleResult(item, 'failed')
  }

  const hasActiveOps = computed(
    () => running.value.length > 0 || queued.value.length > 0,
  )

  const activeBatchNodeIds = computed(
    () =>
      new Set([
        ...running.value.map((item) => item.nodeId),
        ...queued.value.map((item) => item.nodeId),
      ]),
  )

  const snapshot = computed((): LifecycleQueueSnapshot | null => {
    if (!activeKind.value) return null
    return {
      batchId: batchId.value || '',
      kind: activeKind.value,
      maxConcurrent: maxConcurrent.value,
      running: running.value,
      queued: queued.value,
      completed: completed.value,
      failed: failed.value,
      skipped: skipped.value,
    }
  })

  function persistQueue() {
    if (!activeKind.value || !batchId.value) {
      savePersisted(null)
      return
    }
    savePersisted({
      batchId: batchId.value,
      kind: activeKind.value,
      role: pollRole.value || resolveRole(),
      scope: resolveScope(),
      maxConcurrent: maxConcurrent.value,
      savedAt: Date.now(),
      running: running.value,
      queued: queued.value,
    })
  }

  function stopPolling() {
    if (pollTimer != null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
    polling.value = false
  }

  async function syncNodesFromServer(nodes: ApiNode[]) {
    const byId = new Map(nodes.map((n) => [n.id, n]))
    running.value = running.value.filter((item) => {
      const node = byId.get(item.nodeId)
      if (!node) {
        if (activeKind.value === 'remove') {
          markLifecycleCompleted({ ...item, state: 'completed' })
          return false
        }
        return true
      }
      const lc = node.lifecycle
      if (lc && lc.kind === activeKind.value) {
        item.taskId = lc.task_id
        if (lc.target_version) {
          item.targetVersion = lc.target_version
        }
        if (lc.state === 'failed') {
          const polls = (item.failedConfirmPolls ?? 0) + 1
          item.failedConfirmPolls = polls
          if (polls < LIFECYCLE_FAILED_CONFIRM_POLLS) {
            return true
          }
          item.state = 'failed'
          item.error = lc.error || 'failed'
          markLifecycleFailed(item)
          return false
        }
        item.failedConfirmPolls = 0
        item.state = lc.state
      } else if (activeKind.value && lifecycleDone(node, activeKind.value, item)) {
        markLifecycleCompleted({ ...item, state: 'completed' })
        return false
      }
      return true
    })
  }

  async function pollOnce() {
    if (restoreExpiryAt != null && Date.now() > restoreExpiryAt) {
      stopPolling()
      finishBatch()
      await options.onRefresh?.()
      return
    }
    if (!hasActiveOps.value) {
      stopPolling()
      return
    }
    if (pollInFlight) return
    pollInFlight = true
    polling.value = true
    try {
      const nodeIds = [
        ...new Set([
          ...running.value.map((item) => item.nodeId),
          ...queued.value.map((item) => item.nodeId),
        ]),
      ]
      if (nodeIds.length === 0) {
        stopPolling()
        finishBatch()
        return
      }
      const role = pollRole.value || resolveRole()
      const watched = await fetchLifecycleWatch(nodeIds, resolveScope())
      const nodes: ApiNode[] = watched.map((row) => {
        const item = localLifecycleItem(running.value, queued.value, row.id)
        return {
          id: row.id,
          organization: 0,
          name: item?.name || String(row.id),
          role: item?.role || role,
          status: row.status,
          availability: row.availability,
          routable: row.routable,
          version: row.version,
          is_deleted: row.is_deleted ?? false,
          ip_address: item?.ipAddress ?? null,
          lifecycle: row.lifecycle,
        }
      })
      restoreExpiryAt = null
      await syncNodesFromServer(nodes)
      options.onLifecyclePatch?.(nodes)
      await drainQueue()
      if (!hasActiveOps.value) {
        stopPolling()
        finishBatch()
        await options.onRefresh?.()
      }
    } finally {
      pollInFlight = false
      polling.value = false
    }
  }

  function startPolling() {
    stopPolling()
    pollTimer = setInterval(() => {
      void pollOnce()
    }, 4000)
    void pollOnce()
  }

  async function dispatchItem(item: LifecycleQueueItem) {
    try {
      logger.info('useNodeLifecycleOps.ts', 188, 'node lifecycle operation start', {
        node_id: item.nodeId,
        kind: item.kind,
        name: item.name,
      })
      const result = await startNodeOperation(item.nodeId, item.kind, {
        scope: resolveScope(),
      })
      item.state = result.state
      item.taskId = result.task_id
      if (result.target_version) {
        item.targetVersion = result.target_version
      }
      if (result.state === 'completed') {
        markLifecycleCompleted(item)
        return false
      }
      running.value.push(item)
      return true
    } catch (e) {
      logger.warn('useNodeLifecycleOps.ts', 198, 'node lifecycle operation failed', {
        node_id: item.nodeId,
        kind: item.kind,
        error: e instanceof Error ? e.message : String(e),
      })
      item.state = 'failed'
      item.error = e instanceof Error ? e.message : String(e)
      markLifecycleFailed(item)
      return false
    }
  }

  async function drainQueue() {
    while (running.value.length < maxConcurrent.value && queued.value.length > 0) {
      const next = queued.value.shift()
      if (!next) break
      await dispatchItem(next)
    }
    persistQueue()
  }

  function finishBatch() {
    const skip = skipped.value.length
    if (skip > 0) {
      ElMessage.warning({ message: t('nodeLifecycle.batchSkipped', { n: skip }), grouping: true })
    }
    batchId.value = null
    activeKind.value = null
    restoreExpiryAt = null
    savePersisted(null)
  }

  function waitForUpgradeConfirm(preview: NodeOperationBatchPreview): Promise<boolean> {
    return new Promise((resolve) => {
      upgradeConfirmSettled = false
      upgradeConfirmPreview.value = preview
      upgradeConfirmOpen.value = true
      upgradeConfirmResolver = resolve
    })
  }

  function settleUpgradeConfirm(confirmed: boolean) {
    if (upgradeConfirmSettled) return
    upgradeConfirmSettled = true
    upgradeConfirmOpen.value = false
    upgradeConfirmPreview.value = null
    const resolve = upgradeConfirmResolver
    upgradeConfirmResolver = null
    resolve?.(confirmed)
    if (!confirmed) {
      releaseStalePopupLock()
    }
  }

  function resolveUpgradeConfirm() {
    settleUpgradeConfirm(true)
  }

  function cancelUpgradeConfirm() {
    settleUpgradeConfirm(false)
  }

  function explainIneligiblePreview(preview: NodeOperationBatchPreview) {
    if (preview.skipped_disk_full?.length) {
      return [
        t('nodeLifecycle.nothingEligibleDiskFull', { n: preview.skipped_disk_full.length }),
        ...buildUpgradeDiskSkipDetails(t, preview),
      ].join(' ')
    }
    if (preview.skipped_proxy_bound.length) {
      return t('nodeLifecycle.nothingEligibleProxyBound', {
        n: preview.skipped_proxy_bound.length,
      })
    }
    if (preview.skipped_workload.length) {
      return t('nodeLifecycle.nothingEligibleWorkload', { n: preview.skipped_workload.length })
    }
    if (preview.skipped_in_progress.length) {
      return t('nodeLifecycle.nothingEligibleInProgress', { n: preview.skipped_in_progress.length })
    }
    return t('nodeLifecycle.nothingEligible')
  }

  function previewStartErrors(preview: NodeOperationBatchPreview): Array<Record<string, unknown>> {
    const fromRows = (
      rows: Array<{ node_id: number; name: string; reason: string; message?: string }>,
      fallbackCode: string,
    ) => rows.map((item) => ({
      node_id: item.node_id,
      name: item.name,
      code: item.reason || fallbackCode,
      error: item.message || item.reason || fallbackCode,
    }))

    return [
      ...fromRows(preview.skipped_offline || [], 'node_offline'),
      ...fromRows(preview.skipped_workload || [], 'node_workload_active'),
      ...fromRows(preview.skipped_in_progress || [], 'lifecycle_in_progress'),
      ...fromRows(preview.skipped_not_upgradeable || [], 'not_upgradeable'),
      ...fromRows(preview.skipped_proxy_bound || [], 'proxy_has_bindings'),
      ...(preview.skipped_disk_full || []).map((item) => ({
        node_id: item.node_id,
        name: item.name,
        code: item.reason || 'disk_full',
        error: buildUpgradeDiskSkipDetails(t, {
          ...preview,
          skipped_disk_full: [item],
        })[0],
      })),
      ...(preview.missing_node_ids || []).map((nodeId) => ({
        node_id: nodeId,
        code: 'node_not_found',
        error: 'Node was not found.',
      })),
    ]
  }

  function runBatch(
    kind: 'remove',
    nodes: ApiNode[],
    runOptions: { skipConfirm: true; force?: boolean },
  ): Promise<boolean>
  function runBatch(
    kind: 'upgrade',
    nodes: ApiNode[],
    runOptions?: { skipConfirm?: boolean; force?: boolean },
  ): Promise<boolean>
  async function runBatch(
    kind: NodeLifecycleKind,
    nodes: ApiNode[],
    runOptions?: { skipConfirm?: boolean; force?: boolean },
  ): Promise<boolean> {
    if (nodes.length === 0) return false
    lastStartErrors.value = []
    const nodeIds = nodes.map((n) => n.id)
    try {
      const preview = await previewNodeOperationsBatch({
        kind,
        nodeIds,
        maxConcurrent: maxConcurrent.value,
        scope: resolveScope(),
      })
      const previewErrors = previewStartErrors(preview)
      lastStartErrors.value = previewErrors
      if (preview.eligible.length === 0) {
        ElMessage.warning({ message: explainIneligiblePreview(preview), grouping: true })
        return false
      }
      if (kind === 'remove' && !runOptions?.skipConfirm) {
        logger.error('useNodeLifecycleOps.ts', 480, 'remove operation requires external DangerConfirmDialog')
        return false
      }
      if (kind === 'upgrade' && !runOptions?.skipConfirm) {
        await afterOverlayDismiss()
        const confirmed = await waitForUpgradeConfirm(preview)
        if (!confirmed) return false
      }

      batchId.value = newBatchId()
      activeKind.value = kind
      pollRole.value = resolveRole()
      completed.value = []
      failed.value = []
      running.value = []
      queued.value = []
      skipped.value = [
        ...(preview.skipped_offline || []).map((x) => ({ nodeId: x.node_id, name: x.name, reason: 'offline' })),
        ...(preview.skipped_workload || []).map((x) => ({ nodeId: x.node_id, name: x.name, reason: 'workload' })),
        ...(preview.skipped_in_progress || []).map((x) => ({ nodeId: x.node_id, name: x.name, reason: 'in_progress' })),
        ...(preview.skipped_not_upgradeable || []).map((x) => ({
          nodeId: x.node_id,
          name: x.name,
          reason: x.reason,
        })),
        ...(preview.skipped_proxy_bound || []).map((x) => ({ nodeId: x.node_id, name: x.name, reason: 'proxy_bound' })),
      ]

      logger.info('useNodeLifecycleOps.ts', 344, 'node lifecycle batch start', {
        kind,
        node_ids: preview.eligible.map((x) => x.node_id),
        max_concurrent: maxConcurrent.value,
      })
      const batchResult = await startNodeOperationsBatch({
        kind,
        nodeIds: preview.eligible.map((x) => x.node_id),
        maxConcurrent: maxConcurrent.value,
        force: Boolean(runOptions?.force),
        scope: resolveScope(),
      })
      lastStartErrors.value = [...previewErrors, ...(batchResult.errors || [])]

      for (const started of batchResult.started || []) {
        const node = nodes.find((n) => n.id === started.node_id)
        const item: LifecycleQueueItem = {
          nodeId: started.node_id,
          ...queueItemMeta(node, started.node_id, resolveRole()),
          kind,
          targetVersion: started.target_version || undefined,
          state: started.state,
          taskId: started.task_id,
        }
        if (started.state === 'completed') {
          markLifecycleCompleted(item)
        } else {
          running.value.push(item)
        }
      }
      for (const item of batchResult.queued || []) {
        const node = nodes.find((n) => n.id === item.node_id)
        queued.value.push({
          nodeId: item.node_id,
          ...queueItemMeta(node, item.node_id, resolveRole()),
          kind,
          targetVersion: item.target_version,
          state: 'queued',
        })
      }

      if (lastStartErrors.value.length) {
        const first = lastStartErrors.value[0] || {}
        const message = String(first.error || first.code || 'Node operation could not be started.')
        ElMessage.error({ message, grouping: true })
      }

      persistQueue()
      if (hasActiveOps.value) {
        startPolling()
      } else {
        finishBatch()
        await options.onRefresh?.()
      }
      return lastStartErrors.value.length === 0
    } catch (e) {
      if (e instanceof NodeLifecycleApiError) {
        ElMessage.error({ message: e.message, grouping: true })
      } else {
        ElMessage.error({ message: apiErrorMessage(e), grouping: true })
      }
      return false
    }
  }

  async function runSingle(kind: NodeLifecycleKind, node: ApiNode) {
    await runBatch(kind, [node])
  }

  function trackPendingRemovals(
    items: Array<{ nodeId: number; taskId?: string | null; state?: string }>,
    nodes?: ApiNode[],
  ) {
    if (!items.length) return
    batchId.value = batchId.value || newBatchId()
    activeKind.value = 'remove'
    pollRole.value = resolveRole()
    for (const item of items) {
      if (
        running.value.some((row) => row.nodeId === item.nodeId) ||
        queued.value.some((row) => row.nodeId === item.nodeId)
      ) {
        continue
      }
      const node = nodes?.find((row) => row.id === item.nodeId)
      running.value.push({
        nodeId: item.nodeId,
        ...queueItemMeta(node, item.nodeId, resolveRole()),
        kind: 'remove',
        state: item.state || 'removing',
        taskId: item.taskId || undefined,
      })
    }
    persistQueue()
    if (hasActiveOps.value) {
      startPolling()
    }
  }

  function cancelQueued() {
    queued.value = []
    persistQueue()
    if (!hasActiveOps.value) {
      stopPolling()
      finishBatch()
    }
  }

  function restorePersisted() {
    const saved = loadPersisted()
    if (!saved) return
    if (saved.role !== resolveRole() || (saved.scope || 'tenant') !== resolveScope()) {
      savePersisted(null)
      return
    }
    if (Date.now() - saved.savedAt > PERSISTED_QUEUE_MAX_AGE_MS) {
      savePersisted(null)
      return
    }
    restoreExpiryAt = saved.savedAt + PERSISTED_QUEUE_MAX_AGE_MS
    batchId.value = saved.batchId
    activeKind.value = saved.kind
    pollRole.value = saved.role
    maxConcurrent.value = saved.maxConcurrent
    running.value = saved.running
    queued.value = saved.queued
    if (!hasActiveOps.value) return
    void (async () => {
      try {
        const nodeIds = [
          ...new Set([
            ...running.value.map((item) => item.nodeId),
            ...queued.value.map((item) => item.nodeId),
          ]),
        ]
        const watched = await fetchLifecycleWatch(nodeIds, resolveScope())
        const nodes: ApiNode[] = watched.map((row) => {
          const item = localLifecycleItem(running.value, queued.value, row.id)
          return {
            id: row.id,
            organization: 0,
            name: item?.name || String(row.id),
            role: item?.role || saved.role,
            status: row.status,
            availability: row.availability,
            routable: row.routable,
            version: row.version,
            is_deleted: row.is_deleted ?? false,
            ip_address: item?.ipAddress ?? null,
            lifecycle: row.lifecycle,
          }
        })
        await syncNodesFromServer(nodes)
        options.onLifecyclePatch?.(nodes)
        persistQueue()
        if (!hasActiveOps.value) {
          stopPolling()
          finishBatch()
          await options.onRefresh?.()
          return
        }
        startPolling()
      } catch {
        // Keep an unexpired queue and retry lifecycle-watch before showing a stale local state.
        startPolling()
      }
    })()
  }

  function resolveDisplayStatus(node: ApiNode): {
    labelKey: string
    tagType: 'success' | 'warning' | 'danger' | 'info'
    spinning?: boolean
  } {
    const lc = node.lifecycle
    if (lc) {
      const local = localLifecycleItem(running.value, queued.value, node.id)
      if (
        lc.state === 'failed' &&
        local &&
        (local.failedConfirmPolls ?? 0) > 0 &&
        (local.failedConfirmPolls ?? 0) < LIFECYCLE_FAILED_CONFIRM_POLLS
      ) {
        const pendingState =
          local.state && local.state !== 'failed'
            ? local.state
            : activeKind.value === 'upgrade'
              ? 'verifying'
              : 'removing'
        return {
          labelKey: `nodeLifecycle.state.${pendingState}`,
          tagType: 'info',
          spinning: true,
        }
      }
      const key = lc.state === 'failed'
        ? lc.kind === 'upgrade'
          ? 'nodeLifecycle.state.upgrade_failed'
          : 'nodeLifecycle.state.deregistration_failed'
        : `nodeLifecycle.state.${lc.state}`
      const spinning = LIFECYCLE_SPINNING_STATES.includes(
        lc.state as (typeof LIFECYCLE_SPINNING_STATES)[number],
      )
      const tagType =
        lc.state === 'failed' || lc.state === 'verification_pending'
          ? 'danger'
          : 'info'
      return {
        labelKey: key,
        tagType,
        tagClass: lc.state === 'queued' ? 'hfl-tag--neutral' : undefined,
        spinning,
      }
    }

    const local = localLifecycleItem(running.value, queued.value, node.id)
    if (local && activeKind.value) {
      const state = local.state || (activeKind.value === 'upgrade' ? 'upgrading' : 'removing')
      const spinning = LIFECYCLE_SPINNING_STATES.includes(
        state as (typeof LIFECYCLE_SPINNING_STATES)[number],
      )
      const labelKey = state === 'failed'
        ? activeKind.value === 'upgrade'
          ? 'nodeLifecycle.state.upgrade_failed'
          : 'nodeLifecycle.state.deregistration_failed'
        : `nodeLifecycle.state.${state}`
      return {
        labelKey,
        tagType: state === 'failed' ? 'danger' : 'info',
        tagClass: state === 'queued' ? 'hfl-tag--neutral' : undefined,
        spinning,
      }
    }

    // Old rows may be read before the data migration runs. The migration maps
    // both legacy connection values to Active, so present the same semantics.
    const status = node.status === 'online' || node.status === 'offline' ? 'active' : node.status
    return {
      labelKey: `nodeLifecycle.state.${status}`,
      tagType: status === 'active'
        ? 'success'
        : status === 'failed' || status === 'upgrade_failed' || status === 'deregistration_failed'
          ? 'danger'
          : 'info',
    }
  }

  function resolveVersionDisplay(node: ApiNode, versionLabel: string) {
    const lc = node.lifecycle
    if (lc?.kind === 'upgrade' && isUpgradeLifecycleState(lc.state)) {
      return {
        upgrading: true,
        versionLabel,
        targetVersion: lc.target_version || '',
      }
    }
    const local =
      activeKind.value === 'upgrade'
        ? localLifecycleItem(running.value, queued.value, node.id)
        : undefined
    if (local && local.state !== 'failed' && local.state !== 'completed') {
      return {
        upgrading: true,
        versionLabel,
        targetVersion: local.targetVersion || '',
      }
    }
    return { upgrading: false, versionLabel, targetVersion: '' }
  }

  function isNodeBusy(node: ApiNode): boolean {
    const lc = node.lifecycle
    if (lc) {
      return LIFECYCLE_SPINNING_STATES.includes(
        lc.state as (typeof LIFECYCLE_SPINNING_STATES)[number],
      )
    }
    const local = localLifecycleItem(running.value, queued.value, node.id)
    if (local && activeKind.value) {
      const state = local.state || (activeKind.value === 'upgrade' ? 'upgrading' : 'removing')
      return LIFECYCLE_SPINNING_STATES.includes(
        state as (typeof LIFECYCLE_SPINNING_STATES)[number],
      )
    }
    return false
  }

  function canUpgradeNode(node: ApiNode, canUpgradeFn: (node: ApiNode) => boolean): boolean {
    if (isNodeBusy(node)) return false
    if (node.workload?.blocked) return false
    return canUpgradeFn(node)
  }

  onUnmounted(() => {
    stopPolling()
  })

  return {
    batchId,
    activeKind,
    maxConcurrent,
    running,
    queued,
    completed,
    failed,
    skipped,
    lastStartErrors,
    polling,
    hasActiveOps,
    activeBatchNodeIds,
    mergeNodeListDuringLifecycleBatch,
    snapshot,
    runBatch,
    runSingle,
    trackPendingRemovals,
    cancelQueued,
    restorePersisted,
    resolveDisplayStatus,
    resolveVersionDisplay,
    isNodeBusy,
    canUpgradeNode,
    stopPolling,
    upgradeConfirmOpen,
    upgradeConfirmPreview,
    resolveUpgradeConfirm,
    cancelUpgradeConfirm,
  }
}

export function formatWorkloadBlockedMessage(
  t: Composer['t'],
  node: ApiNode,
): string | undefined {
  if (!node.workload?.blocked) return undefined
  return t('nodeLifecycle.workloadBlocked')
}
