<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ArrowUpCircle,
  ChevronDown,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Wrench,
} from 'lucide-vue-next'
import { ElCheckbox, ElInputNumber, ElMessage, type ElTable } from 'element-plus'
import NodeVersionCell from '../../components/node-lifecycle/NodeVersionCell.vue'
import NodeLifecycleBanner from '../../components/node-lifecycle/NodeLifecycleBanner.vue'
import NodeLifecycleUpgradeConfirmDialog from '../../components/NodeLifecycleUpgradeConfirmDialog.vue'
import AgentPlatformBrandIcon from '../../components/agent-deploy/AgentPlatformBrandIcon.vue'
import HflCapacityCell from '../../components/HflCapacityCell.vue'
import DangerConfirmDialog from '../../components/DangerConfirmDialog.vue'
import { useNodeLifecycleOps } from '../../composables/useNodeLifecycleOps'
import { useListTableLayout } from '../../composables/useListTableLayout'
import { useListSearch } from '../../composables/useListSearch'
import { usePageRequestScope } from '../../composables/usePageRequestScope'
import { apiErrorMessage } from '../../lib/api'
import { afterOverlayDismiss } from '../../lib/uiDefer'
import { LIST_ROUTE_REFRESH_KEY, stripListRefreshQuery } from '../../lib/listRouteRefresh'
import {
  fetchLensHealth,
  listLensGateways,
  setPlatformLensGatewayDefault,
  setLensApiScope,
  type LensGatewayInsight,
} from '../../lib/lensApi'
import { lensKnowledgePath } from '../../lib/lensEngineRoutes'
import {
  fetchLatestAgentVersion,
  updateNode,
} from '../../lib/nodeApi'
import { canRemoteAgentUpgrade } from '../../lib/agentVersion'
import {
  formatNodeBytes,
  formatNodeDate,
  nodeCpuCores,
  nodeDiskCount,
  nodeDiskUsageParts,
  nodeEnrollmentOs,
  nodeMemoryTotalBytes,
  nodePlatformLabel,
  type NodeOsPlatformLabels,
} from '../../lib/nodeInventoryDisplay'
import InsightGatewayDetailDrawer from './InsightGatewayDetailDrawer.vue'
import GatewayCompositeStatusCell from './GatewayCompositeStatusCell.vue'
import PlatformOpsPagination from '../../platform-ops/components/PlatformOpsPagination.vue'
import {
  fetchPublicGatewayCapacities,
  patchPublicGatewayCapacity,
  type PlatformGatewayCapacity,
} from '../../platform-ops/lib/platformOpsApi'
import type { ApiNode } from '../../types/node'

const MIB = 1024 ** 2
const GIB = 1024 ** 3
type CapacityUnit = 'mib' | 'gib'

export type InsightGatewayRow = ApiNode & LensGatewayInsight

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const pageRequests = usePageRequestScope()

/** Prefer route over module scope — Admin Engine can race with tenant scope resets. */
const isPlatformEngine = computed(() => route.path.startsWith('/platform-ops/engine'))

const TABLE_HEADER_STYLE: Record<string, string> = {
  background: 'rgba(248, 250, 252, 0.96)',
  color: 'rgb(71 85 105)',
  fontWeight: '600',
  whiteSpace: 'nowrap',
}

const rows = ref<InsightGatewayRow[]>([])
const busy = ref(false)
const listLoaded = ref(false)
const search = ref('')
const bridgeReady = ref(false)
const latestAgentVersion = ref<string | null>(null)
const selectedRows = ref<InsightGatewayRow[]>([])
const deleteOpen = ref(false)
const deleteLoading = ref(false)
const deleteForce = ref(false)
const pendingDelete = ref<InsightGatewayRow[]>([])
const moreActionsOpen = ref(false)
const detailOpen = ref(false)
const detailNodeId = ref<number | null>(null)
const detailInitialTab = ref<'basic' | 'performance' | 'maintenance'>('basic')
const renameDialogOpen = ref(false)
const renameInput = ref('')
const renameTarget = ref<InsightGatewayRow | null>(null)
const capacityByGatewayId = ref<Map<number, PlatformGatewayCapacity>>(new Map())
const capacityDialogOpen = ref(false)
const capacitySaving = ref(false)
const capacityTarget = ref<InsightGatewayRow | null>(null)
const capacityUnlimited = ref(false)
const capacityDraft = ref(100)
const capacityUnit = ref<CapacityUnit>('gib')
const offlinePendingDeleteCount = computed(
  () => pendingDelete.value.filter((row) => row.routable === false || !row.hfl_agent_online).length,
)
const installerManagedPendingDelete = computed(() =>
  pendingDelete.value.some((row) => {
    const metadata = row.metadata as Record<string, unknown> | undefined
    return metadata?.managed_by === 'hfl-installer'
  }),
)
const deleteConfirmationMessage = computed(() => {
  const values = {
    n: pendingDelete.value.length,
    offline: offlinePendingDeleteCount.value,
  }
  if (installerManagedPendingDelete.value) {
    return t('insight.dataGateway.deleteInstallerManagedConfirm', values)
  }
  if (offlinePendingDeleteCount.value > 0) {
    return t('insight.dataGateway.deleteOfflineConfirm', values)
  }
  return t('nodesPage.deleteSelectedConfirm', values)
})

const tableRef = ref<InstanceType<typeof ElTable> | null>(null)
const tableBlockRef = ref<HTMLElement | null>(null)
const { tableMaxHeight, layoutTable, handleTableScroll } = useListTableLayout(tableRef, tableBlockRef)

const pagination = reactive({ page: 1, pageSize: isPlatformEngine.value ? 20 : 30, count: 0 })
const visibleRows = computed(() => {
  if (!isPlatformEngine.value) return rows.value
  const start = (pagination.page - 1) * pagination.pageSize
  return rows.value.slice(start, start + pagination.pageSize)
})
const { appliedSearch, clearSearch } = useListSearch(search, () => {
  pagination.page = 1
  void load()
})

const osPlatformLabels = computed((): NodeOsPlatformLabels => ({
  linux: t('protection.sourceResources.osPlatformLinux'),
  windows: t('protection.sourceResources.osPlatformWindows'),
  macos: t('protection.sourceResources.osPlatformMacos'),
}))

const lifecycleOps = useNodeLifecycleOps({
  role: () => 'gateway',
  scope: () => (isPlatformEngine.value ? 'platform' : 'tenant'),
  t,
  onRefresh: () => load(),
  onLifecyclePatch: (patched) => {
    const byId = new Map(patched.map((node) => [node.id, node]))
    const batchIds = lifecycleOps.activeBatchNodeIds.value
    const existingIds = new Set(rows.value.map((row) => row.id))
    rows.value = rows.value.map((row) => {
      const next = byId.get(row.id)
      if (!next) return row
      return {
        ...row,
        status: next.status,
        routable: next.routable,
        version: next.version,
        lifecycle: next.lifecycle,
        is_deleted: next.is_deleted,
      }
    })
    // Remount / empty refresh: re-insert in-flight lifecycle rows from watch payload.
    for (const node of patched) {
      if (existingIds.has(node.id) || !batchIds.has(node.id)) continue
      rows.value.push(
        mergeLensIntoNode({
          id: node.id,
          organization: node.organization ?? 0,
          name: node.name,
          role: node.role || 'gateway',
          status: node.status,
          routable: node.routable,
          version: node.version,
          ip_address: node.ip_address ?? null,
          lifecycle: node.lifecycle,
          is_deleted: node.is_deleted,
          created_at: node.created_at || '',
          updated_at: node.updated_at || '',
        } as ApiNode),
      )
      existingIds.add(node.id)
    }
    pagination.count = Math.max(pagination.count, rows.value.length)
  },
})
const upgradeConfirmOpen = lifecycleOps.upgradeConfirmOpen
const upgradeConfirmPreview = lifecycleOps.upgradeConfirmPreview

const tableLoading = computed(() => !listLoaded.value)
const singleSelected = computed(() => (selectedRows.value.length === 1 ? selectedRows.value[0]! : null))
const batchDisabled = computed(() => selectedRows.value.length === 0)
const batchRenameDisabled = computed(() => selectedRows.value.length !== 1)
const batchUpgradeDisabled = computed(() => {
  const upgradable = selectedRows.value.filter((row) => lifecycleOps.canUpgradeNode(row, canUpgrade))
  return upgradable.length === 0
})

function canUpgrade(row: ApiNode) {
  if ((row as InsightGatewayRow).managed_by_hfl === false) return false
  return row.agent_release?.upgrade_version_allowed
    ?? canRemoteAgentUpgrade(row.version, latestAgentVersion.value)
}

function platformLabel(row: ApiNode) {
  return nodePlatformLabel(nodeEnrollmentOs(row), osPlatformLabels.value)
}

function ipLine(row: ApiNode) {
  return row.ip_address?.trim() || '—'
}

function aiPhase(row: InsightGatewayRow): 'not_provisioned' | 'pending_install' | 'online' | 'offline' | 'error' {
  if (!bridgeReady.value) return 'not_provisioned'
  if (row.managed_by_hfl === false) return 'not_provisioned'
  if (!row.hfl_agent_online) return 'offline'
  if (!row.ai_enabled) {
    if (row.status === 'online') return 'error'
    return 'not_provisioned'
  }
  if (row.sidecar_status === 'online') return 'online'
  if (row.sidecar_status === 'error') return 'error'
  if (row.sidecar_status === 'offline') return 'offline'
  if (row.sidecar_status === 'not_deployed') return 'error'
  return 'pending_install'
}

function openKnowledgeSources() {
  if (isPlatformEngine.value) {
    void router.push({ path: lensKnowledgePath() })
    return
  }
  void router.push({ path: '/insight/copilot' })
}

function mergeLensIntoNode(node: ApiNode, lens?: LensGatewayInsight): InsightGatewayRow {
  return {
    ...node,
    ...lens,
    ai_enabled: lens?.ai_enabled ?? false,
    sl_lensnode_uuid: lens?.sl_lensnode_uuid ?? null,
    lensnode_status: lens?.lensnode_status ?? null,
    knowledge_source_count: lens?.knowledge_source_count ?? 0,
    workspace_root: lens?.workspace_root ?? '',
    sidecar_status: lens?.sidecar_status ?? 'not_deployed',
  }
}

/** Keep in-flight remove/upgrade rows visible when a refresh omits them. */
function applyGatewayRows(next: InsightGatewayRow[], totalHint?: number) {
  const prev = rows.value
  const batchIds = lifecycleOps.activeBatchNodeIds.value
  let merged = next
  if (batchIds.size > 0) {
    const nextIds = new Set(next.map((row) => row.id))
    const ghosts = prev.filter((row) => batchIds.has(row.id) && !nextIds.has(row.id))
    if (ghosts.length) merged = [...next, ...ghosts]
  }
  rows.value = lifecycleOps.mergeNodeListDuringLifecycleBatch(merged, prev, batchIds)
  pagination.count = totalHint != null ? Math.max(totalHint, rows.value.length) : rows.value.length
}

async function loadLatestVersion(signal?: AbortSignal) {
  try {
    latestAgentVersion.value = await fetchLatestAgentVersion({ signal })
  } catch (e) {
    if (pageRequests.isAbortError(e)) return
    latestAgentVersion.value = null
  }
}

async function loadCapacities(signal?: AbortSignal) {
  if (!isPlatformEngine.value) {
    capacityByGatewayId.value = new Map()
    return
  }
  try {
    const payload = await fetchPublicGatewayCapacities({ signal })
    capacityByGatewayId.value = new Map(
      (payload.results || []).map((row) => [row.gateway_id, row]),
    )
  } catch (e) {
    if (pageRequests.isAbortError(e)) return
    // List still usable; capacity column shows empty marks until retry.
  }
}

function capacityFor(row: InsightGatewayRow): PlatformGatewayCapacity | undefined {
  return capacityByGatewayId.value.get(row.id)
}

function capacityTotalBytes(cap: PlatformGatewayCapacity | undefined): number {
  if (!cap || cap.unlimited || cap.capacity_bytes < 0) return 0
  if (cap.limit_bytes != null) return Number(cap.limit_bytes)
  return Number(cap.capacity_bytes)
}

/** Finite capacity including hard-empty (0 bytes); false for unlimited / unknown. */
function capacityHasKnownTotal(cap: PlatformGatewayCapacity | undefined): boolean {
  return Boolean(cap && !cap.unlimited && cap.capacity_bytes >= 0)
}

function openCapacityDialog(row: InsightGatewayRow) {
  if (!canManageGateway(row)) return
  const cap = capacityFor(row)
  capacityTarget.value = row
  const bytes = cap != null && cap.capacity_bytes >= 0 ? Number(cap.capacity_bytes) : -1
  capacityUnlimited.value = Boolean(cap?.unlimited || bytes < 0)
  if (bytes >= 0 && bytes % GIB !== 0) {
    capacityUnit.value = 'mib'
    capacityDraft.value = bytes / MIB
  } else {
    capacityUnit.value = 'gib'
    capacityDraft.value = bytes >= 0 ? bytes / GIB : 100
  }
  capacityDialogOpen.value = true
}

async function submitCapacity() {
  const row = capacityTarget.value
  if (!row || capacitySaving.value) return
  const draft = Number(capacityDraft.value)
  const multiplier = capacityUnit.value === 'mib' ? MIB : GIB
  const next = capacityUnlimited.value ? -1 : draft * multiplier
  if (
    !capacityUnlimited.value
    && (!Number.isInteger(draft) || draft < 0 || !Number.isSafeInteger(next))
  ) {
    ElMessage.warning({ message: t('platformOps.engineGateway.capacitySaveFailed'), grouping: true })
    return
  }
  capacitySaving.value = true
  try {
    const updated = await patchPublicGatewayCapacity(row.id, next)
    const nextMap = new Map(capacityByGatewayId.value)
    nextMap.set(row.id, updated)
    capacityByGatewayId.value = nextMap
    capacityDialogOpen.value = false
    ElMessage.success({ message: t('platformOps.engineGateway.capacitySaved'), grouping: true })
  } catch (error) {
    ElMessage.error({
      message: apiErrorMessage(error, t('platformOps.engineGateway.capacitySaveFailed')),
      grouping: true,
    })
  } finally {
    capacitySaving.value = false
  }
}

async function load() {
  const signal = pageRequests.nextSignal('dg-list')
  busy.value = true
  if (isPlatformEngine.value) {
    setLensApiScope('platform')
  }
  try {
    // Admin Engine and Insights share the same SL-admin (platform) DG pool from listLensGateways.
    let lensRows: LensGatewayInsight[] = []
    let listFailed = false
    const [lensResult, health] = await Promise.all([
      listLensGateways()
        .then((next) => {
          lensRows = next
          return next
        })
        .catch((e) => {
          if (pageRequests.isAbortError(e)) throw e
          listFailed = true
          return null
        }),
      fetchLensHealth().catch(() => null),
    ])
    if (lensResult === null && listFailed) {
      if (rows.value.length === 0) {
        ElMessage.error({ message: t('errors.generic.loadFailed'), grouping: true })
      }
      bridgeReady.value = Boolean(health?.lens?.configured && health?.lens?.authenticated)
      await loadLatestVersion(signal)
      return
    }
    bridgeReady.value = Boolean(health?.lens?.configured && health?.lens?.authenticated)
    const term = appliedSearch.value.trim().toLowerCase()
    const filtered = term
      ? lensRows.filter((row) => {
          const name = (row.name || '').toLowerCase()
          const ip = String(row.ip_address || '').toLowerCase()
          return name.includes(term) || ip.includes(term)
        })
      : lensRows
    const next = filtered.map((lens) => {
      const base = lens as unknown as ApiNode
      return mergeLensIntoNode(
        {
          id: lens.id,
          organization: base.organization ?? 0,
          name: lens.name,
          role: (lens.role as ApiNode['role']) || 'gateway',
          status: (lens.status as ApiNode['status']) || 'offline',
          routable: base.routable,
          version: base.version ?? null,
          os_name: base.os_name,
          ip_address: lens.ip_address,
          metadata: base.metadata,
          created_at: base.created_at || '',
          updated_at: base.updated_at || '',
          last_seen_at: base.last_seen_at,
          lifecycle: base.lifecycle ?? null,
          workload: base.workload ?? null,
          is_deleted: (base as ApiNode & { is_deleted?: boolean }).is_deleted,
        } as ApiNode,
        lens,
      )
    })
    applyGatewayRows(next, filtered.length)
    await Promise.all([loadLatestVersion(signal), loadCapacities(signal)])
  } catch (e) {
    if (pageRequests.isAbortError(e)) return
    if (rows.value.length === 0) {
      rows.value = []
    }
    ElMessage.error({ message: apiErrorMessage(e, t('errors.generic.loadFailed')), grouping: true })
  } finally {
    pageRequests.releaseSignal('dg-list', signal)
    if (!signal.aborted) {
      listLoaded.value = true
      busy.value = false
      layoutTable()
    }
  }
}

function openDetail(row: InsightGatewayRow) {
  if (row.managed_by_hfl === false) return
  detailNodeId.value = row.id
  detailInitialTab.value = 'basic'
  detailOpen.value = true
}

function openSelectedMaintenance() {
  const row = singleSelected.value
  if (!row || !canManageGateway(row)) return
  detailNodeId.value = row.id
  detailInitialTab.value = 'maintenance'
  detailOpen.value = true
}

function canManageGateway(row: InsightGatewayRow) {
  return row.managed_by_hfl !== false && row.id > 0
}

function readinessLabel(row: InsightGatewayRow) {
  if (!row.managed_by_hfl) return `SL ${row.sl_runtime_status || row.sl_status || 'Offline'} · HFL Agent Missing`
  if (!row.hfl_agent_online) return 'HFL Agent Offline'
  if (!row.hfl_sidecar_online) return 'HFL LensNode Sidecar Offline'
  return 'HFL Ready'
}

function originLabel(row: InsightGatewayRow) {
  const origin = row.origin || 'external'
  return t(`insight.dataGateway.origin.${origin}`)
}

async function setPlatformDefault(row: InsightGatewayRow) {
  if (row.origin !== 'platform' || !canManageGateway(row) || !row.hfl_usable) return
  try {
    await setPlatformLensGatewayDefault(row.id)
    ElMessage.success({ message: t('insight.dataGateway.defaultSetSuccess'), grouping: true })
    await load()
  } catch (error) {
    ElMessage.error({ message: apiErrorMessage(error, t('errors.generic.loadFailed')), grouping: true })
  }
}

function onSelectionChange(selection: InsightGatewayRow[]) {
  selectedRows.value = selection
}

function clearSelection() {
  selectedRows.value = []
  tableRef.value?.clearSelection()
}

type MoreAction = 'rename' | 'upgrade' | 'maintenance' | 'remove'

async function handleMoreAction(command: MoreAction) {
  if (command === 'rename') openRenameDialog()
  else if (command === 'upgrade') await onUpgradeSelected()
  else if (command === 'maintenance') openSelectedMaintenance()
  else if (command === 'remove') await deleteSelected()
}

function openRenameDialog() {
  const node = singleSelected.value
  if (!node) return
  renameTarget.value = node
  renameInput.value = node.name
  renameDialogOpen.value = true
}

async function submitRename() {
  const node = renameTarget.value
  if (!node) return
  const name = renameInput.value.trim()
  if (!name) {
    ElMessage.warning({ message: t('nodesPage.renamePlaceholder'), grouping: true })
    return
  }
  try {
    await updateNode(node.id, { name }, isPlatformEngine.value ? 'platform' : 'tenant')
    ElMessage.success({ message: t('nodesPage.renameSuccess'), grouping: true })
    renameDialogOpen.value = false
    clearSelection()
    await load()
  } catch {
    ElMessage.error({ message: t('nodesPage.renameFailed'), grouping: true })
  }
}

async function onUpgradeSelected() {
  await afterOverlayDismiss()
  const targets = selectedRows.value.filter((row) => lifecycleOps.canUpgradeNode(row, canUpgrade))
  if (targets.length === 0) {
    ElMessage.warning({ message: t('nodeLifecycle.nothingEligible'), grouping: true })
    return
  }
  const started = await lifecycleOps.runBatch('upgrade', targets)
  if (started) clearSelection()
}

async function deleteSelected() {
  if (batchDisabled.value) return
  await afterOverlayDismiss()
  pendingDelete.value = [...selectedRows.value]
  deleteForce.value = false
  deleteOpen.value = true
}

function gatewayDeleteDialogItem(row: InsightGatewayRow) {
  return {
    key: row.id,
    name: row.name,
    status: {
      label: readinessLabel(row),
      tone: row.hfl_agent_online && row.hfl_sidecar_online ? 'success' as const : 'danger' as const,
    },
  }
}

function gatewayDeleteCanRetryWithForce() {
  return lifecycleOps.lastStartErrors.value.some((item) =>
    ['node_offline', 'agent_uninstall_failed', 'completion_callback_timeout']
      .includes(String(item.code || '')),
  )
}

function cancelDelete() {
  pendingDelete.value = []
  deleteForce.value = false
}

async function confirmDelete() {
  const targets = [...pendingDelete.value]
  if (!targets.length) return
  deleteLoading.value = true
  try {
    const started = await lifecycleOps.runBatch('remove', targets, {
      skipConfirm: true,
      force: deleteForce.value,
    })
    if (started) {
      clearSelection()
      deleteOpen.value = false
      pendingDelete.value = []
      deleteForce.value = false
    } else if (lifecycleOps.lastStartErrors.value.length) {
      const failedIds = new Set(
        lifecycleOps.lastStartErrors.value.map((item) => Number(item.node_id || 0)),
      )
      pendingDelete.value = targets.filter((node) => failedIds.has(node.id))
      if (!deleteForce.value && gatewayDeleteCanRetryWithForce()) {
        deleteForce.value = true
      }
    }
  } finally {
    deleteLoading.value = false
  }
}

function onPaginationSizeChange() {
  pagination.page = 1
  void load()
}

function onPaginationPageChange() {
  void load()
}

watch(tableLoading, (loading) => {
  if (!loading) layoutTable()
})

watch(
  () => route.query[LIST_ROUTE_REFRESH_KEY],
  (token) => {
    if (token == null || Array.isArray(token)) return
    void load().finally(() => {
      router.replace({
        path: route.path,
        query: stripListRefreshQuery(route.query as Record<string, unknown>),
      })
    })
  },
)

let listPollTimer: ReturnType<typeof setInterval> | null = null

function stopListPoll() {
  if (listPollTimer != null) {
    clearInterval(listPollTimer)
    listPollTimer = null
  }
}

function startListPoll() {
  stopListPoll()
  const needsPoll = rows.value.some((row) => row.ai_enabled && aiPhase(row) !== 'online')
  if (!needsPoll) return
  listPollTimer = setInterval(() => {
    if (!busy.value) void load()
  }, 12000)
}

watch(rows, () => startListPoll(), { deep: true })

onMounted(async () => {
  if (isPlatformEngine.value) {
    setLensApiScope('platform')
  }
  // Restore in-flight queue before first load so Removing rows can be retained.
  lifecycleOps.restorePersisted()
  await load()
})

onUnmounted(() => {
  stopListPoll()
})
</script>

<template>
  <div class="hfl-list-shell hfl-list-shell--fill">
    <div class="hfl-list-panel hfl-list-panel--fill">
      <div class="hfl-list-toolbar hfl-list-toolbar--mobile-primary-utility">
        <div class="hfl-list-toolbar__primary">
          <RouterLink
            :to="isPlatformEngine ? '/platform-ops/engine/gateways/add' : '/node/nodes/deploy?role=gateway'"
            class="inline-flex"
          >
            <ElButton type="primary">
              <Plus :size="16" />
              {{ isPlatformEngine ? t('platformOps.engineGateway.addAction') : t('insight.dataGateway.btnAdd') }}
            </ElButton>
          </RouterLink>

          <ElDropdown
            trigger="click"
            popper-class="hfl-actions-dropdown"
            @visible-change="moreActionsOpen = $event"
            @command="handleMoreAction"
          >
            <ElButton>
              {{ isPlatformEngine ? t('platformOps.engineGateway.actions') : t('insight.dataGateway.btnMoreActions') }}
              <ChevronDown
                :size="16"
                class="hfl-list-more__chev"
                :class="{ 'hfl-list-more__chev--open': moreActionsOpen }"
              />
            </ElButton>
            <template #dropdown>
              <ElDropdownMenu>
                <ElDropdownItem
                  command="rename"
                  :disabled="batchRenameDisabled"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Pencil
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('nodesPage.btnBatchRename') }}</span>
                  </span>
                </ElDropdownItem>
                <ElDropdownItem
                  command="upgrade"
                  :disabled="batchUpgradeDisabled"
                >
                  <span class="el-dropdown-menu__item-content">
                    <ArrowUpCircle
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('nodesPage.actionUpgrade') }}</span>
                  </span>
                </ElDropdownItem>
                <ElDropdownItem
                  command="maintenance"
                  :disabled="batchRenameDisabled"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Wrench
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('nodeLifecycle.maintenance') }}</span>
                  </span>
                </ElDropdownItem>
                <ElDropdownItem
                  command="remove"
                  divided
                  class="el-dropdown-menu__item--danger"
                  :disabled="batchDisabled"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Trash2
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('nodesPage.actionDelete') }}</span>
                  </span>
                </ElDropdownItem>
              </ElDropdownMenu>
            </template>
          </ElDropdown>
        </div>

        <div class="hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split">
          <ElInput
            v-model="search"
            clearable
            size="small"
            :placeholder="t('insight.dataGateway.searchPlaceholder')"
            class="hfl-list-search"
            @clear="clearSearch"
          >
            <template #prefix>
              <Search
                :size="16"
                class="hfl-list-search__icon"
              />
            </template>
          </ElInput>
          <div class="hfl-list-toolbar__utility">
            <ElButton
              class="hfl-refresh-button"
              :title="t('protection.sourceResources.refresh')"
              :disabled="busy"
              @click="load()"
            >
              <RefreshCw
                :size="16"
                :class="{ 'is-spinning': busy }"
              />
            </ElButton>
          </div>
        </div>
      </div>

      <NodeLifecycleBanner
        :snapshot="lifecycleOps.snapshot"
        @cancel-queued="lifecycleOps.cancelQueued"
      />

      <div
        ref="tableBlockRef"
        class="hfl-list-table-block"
      >
        <el-table
          ref="tableRef"
          v-table-overflow-title
          v-table-header-scroll-sync
          v-table-column-resize="'insight.dataGateways.list'"
          v-loading="tableLoading"
          row-key="id"
          :data="visibleRows"
          stripe
          class="hfl-list-table"
          :max-height="tableMaxHeight"
          :header-cell-style="TABLE_HEADER_STYLE"
          @scroll="handleTableScroll"
          @selection-change="onSelectionChange"
        >
          <el-table-column
            type="selection"
            width="35"
            fixed="left"
            :selectable="canManageGateway"
            :reserve-selection="true"
          />
          <el-table-column
            :label="t('protection.sourceResources.colName')"
            min-width="200"
            fixed="left"
            class-name="hfl-table-name-col"
          >
            <template #default="{ row }">
              <button
                v-if="canManageGateway(row)"
                type="button"
                class="hfl-table-name-link hfl-table-name-link--full"
                @click="openDetail(row)"
              >
                {{ row.name }}
              </button>
              <span v-else>{{ row.name }}</span>
            </template>
          </el-table-column>
          <el-table-column
            v-if="isPlatformEngine"
            :label="t('insight.dataGateway.colOrigin')"
            min-width="110"
          >
            <template #default="{ row }">
              <span>{{ originLabel(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column
            v-if="isPlatformEngine"
            label="HFL Readiness"
            min-width="190"
          >
            <template #default="{ row }">
              <span>{{ readinessLabel(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.sourceResources.colHostIp')"
            min-width="140"
          >
            <template #default="{ row }">
              {{ ipLine(row) }}
            </template>
          </el-table-column>
          <el-table-column
            v-if="isPlatformEngine"
            :label="t('platformOps.engineGateway.colCapacity')"
            min-width="220"
          >
            <template #default="{ row }">
              <div class="dg-capacity-cell">
                <HflCapacityCell
                  v-if="capacityFor(row)"
                  :used-bytes="capacityFor(row)!.used_bytes"
                  :total-bytes="capacityTotalBytes(capacityFor(row))"
                  :known-total="capacityHasKnownTotal(capacityFor(row))"
                  variant="compact"
                  :format-bytes="formatNodeBytes"
                  :unlimited-total-label="
                    capacityFor(row)!.unlimited || capacityFor(row)!.capacity_bytes < 0
                      ? t('platformOps.engineGateway.capacityUnlimited')
                      : undefined
                  "
                />
                <span
                  v-else
                  class="hfl-empty-mark"
                >—</span>
                <span
                  v-if="capacityFor(row)?.used_incomplete"
                  class="dg-capacity-cell__incomplete"
                >
                  {{ t('platformOps.engineGateway.capacityUsedIncomplete') }}
                </span>
                <ElButton
                  v-if="canManageGateway(row)"
                  link
                  type="primary"
                  class="dg-capacity-cell__edit"
                  @click="openCapacityDialog(row)"
                >
                  {{ t('platformOps.engineGateway.editCapacity') }}
                </ElButton>
              </div>
            </template>
          </el-table-column>
          <el-table-column
            v-if="!isPlatformEngine"
            label="OS"
            min-width="120"
          >
            <template #default="{ row }">
              <div class="source-os-cell source-os-cell--compact hfl-table-no-tooltip">
                <span class="source-os-cell__icon-wrap">
                  <AgentPlatformBrandIcon :os="nodeEnrollmentOs(row)" />
                </span>
                <span class="source-os-cell__platform">{{ platformLabel(row) }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column
            v-if="!isPlatformEngine"
            :label="t('protection.sourceResources.colCpu')"
            min-width="88"
          >
            <template #default="{ row }">
              <span :class="{ 'hfl-empty-mark': nodeCpuCores(row) == null }">{{ nodeCpuCores(row) != null ? t('protection.sourceResources.cpuCoresValue', { n: nodeCpuCores(row) }) : '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            v-if="!isPlatformEngine"
            :label="t('protection.sourceResources.colMemory')"
            min-width="100"
          >
            <template #default="{ row }">
              <span :class="{ 'hfl-empty-mark': nodeMemoryTotalBytes(row) == null }">{{ nodeMemoryTotalBytes(row) != null ? formatNodeBytes(nodeMemoryTotalBytes(row)!) : '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            v-if="!isPlatformEngine"
            :label="t('protection.sourceResources.colDiskCount')"
            min-width="96"
          >
            <template #default="{ row }">
              <span :class="{ 'hfl-empty-mark': nodeDiskCount(row) == null }">{{ nodeDiskCount(row) ?? '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            v-if="!isPlatformEngine"
            :label="t('protection.sourceResources.colCapacity')"
            min-width="200"
          >
            <template #default="{ row }">
              <HflCapacityCell
                :used-bytes="nodeDiskUsageParts(row).used"
                :total-bytes="nodeDiskUsageParts(row).total"
                variant="compact"
                :format-bytes="formatNodeBytes"
              />
            </template>
          </el-table-column>
          <el-table-column
            v-if="!isPlatformEngine"
            :label="t('insight.dataGateway.colKnowledgeSources')"
            min-width="168"
            align="center"
          >
            <template #default="{ row }">
              <button
                v-if="row.knowledge_source_count > 0"
                type="button"
                class="dg-ks-link"
                @click="openKnowledgeSources(row)"
              >
                {{ row.knowledge_source_count }}
              </button>
              <span
                v-else
                class="dg-muted"
              >0</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.sourceResources.colStatus')"
            min-width="120"
          >
            <template #default="{ row }">
              <GatewayCompositeStatusCell
                :node="row"
                :ai-phase="aiPhase(row)"
                :resolve-display-status="lifecycleOps.resolveDisplayStatus"
              />
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.sourceResources.colVersion')"
            min-width="130"
          >
            <template #default="{ row }">
              <NodeVersionCell
                :node="row"
                :version-label="row.version || '—'"
                :target-version="row.agent_release?.target_version"
                :update-available="row.agent_release?.update_available"
                :show-update-hint="row.managed_by_hfl !== false"
                :resolve-version-display="lifecycleOps.resolveVersionDisplay"
              />
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.sourceResources.colRegistered')"
            min-width="170"
          >
            <template #default="{ row }">
              <span
                class="hfl-table-cell-time"
                :class="{ 'hfl-empty-mark': !row.created_at }"
              >{{ formatNodeDate(row.created_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column
            v-if="isPlatformEngine"
            :label="t('platformOps.engineGateway.columnActions')"
            width="150"
            fixed="right"
            align="right"
          >
            <template #default="{ row }">
              <ElButton
                v-if="canManageGateway(row)"
                link
                type="primary"
                @click="openDetail(row)"
              >
                {{ t('platformOps.engineGateway.viewDetails') }}
              </ElButton>
              <ElButton
                v-if="row.origin === 'platform' && canManageGateway(row) && row.hfl_usable && !row.is_platform_default"
                link
                type="primary"
                @click="setPlatformDefault(row)"
              >
                {{ t('insight.dataGateway.defaultSet') }}
              </ElButton>
              <span
                v-else-if="!canManageGateway(row)"
                class="hfl-empty-mark"
              >—</span>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty
              :description="t('nodesPage.emptyGateway')"
              :image-size="80"
            />
          </template>
        </el-table>
      </div>

      <div class="hfl-list-footer">
        <span
          v-if="selectedRows.length > 0"
          class="hfl-list-footer__selected"
        >
          {{ t('nodesPage.selectedCount', { n: selectedRows.length }) }}
        </span>
        <PlatformOpsPagination
          v-if="isPlatformEngine"
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.pageSize"
          :total="pagination.count"
          @current-change="onPaginationPageChange"
          @size-change="onPaginationSizeChange"
        />
        <HflPagination
          v-else
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.pageSize"
          class="hfl-list-footer__pagination"
          :total="pagination.count"
          @current-change="onPaginationPageChange"
          @size-change="onPaginationSizeChange"
        />
      </div>
    </div>

    <el-dialog
      v-model="renameDialogOpen"
      class="source-action-dialog"
      :title="t('nodesPage.renameTitle')"
      width="480px"
      align-center
      destroy-on-close
    >
      <ElForm
        label-position="top"
        class="source-action-dialog__form"
        @submit.prevent="submitRename"
      >
        <ElFormItem
          :label="t('nodesPage.renameLabel')"
          required
        >
          <ElInput
            v-model="renameInput"
            :placeholder="t('nodesPage.renamePlaceholder')"
            maxlength="128"
            show-word-limit
          />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <el-button @click="renameDialogOpen = false">
          {{ t('common.cancel') }}
        </el-button>
        <el-button
          type="primary"
          @click="submitRename"
        >
          {{ t('common.save') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="capacityDialogOpen"
      class="source-action-dialog"
      :title="t('platformOps.engineGateway.capacityDialogTitle')"
      width="480px"
      align-center
      destroy-on-close
    >
      <p class="dg-capacity-dialog__hint">
        {{ t('platformOps.engineGateway.capacityDialogHint') }}
      </p>
      <p
        v-if="capacityTarget && capacityFor(capacityTarget)?.used_incomplete"
        class="dg-capacity-dialog__warn"
      >
        {{ t('platformOps.engineGateway.capacityUsedIncomplete') }}
      </p>
      <ElForm
        label-position="top"
        class="source-action-dialog__form"
        @submit.prevent="submitCapacity"
      >
        <ElFormItem>
          <ElCheckbox v-model="capacityUnlimited">
            {{ t('platformOps.engineGateway.capacityUnlimited') }}
          </ElCheckbox>
        </ElFormItem>
        <ElFormItem
          v-if="!capacityUnlimited"
          :label="t('platformOps.engineGateway.capacityLabel')"
          required
        >
          <div class="dg-capacity-dialog__input">
            <ElInputNumber
              v-model="capacityDraft"
              class="w-full"
              :min="0"
              :step="1"
              :precision="0"
            />
            <el-select
              v-model="capacityUnit"
              class="dg-capacity-dialog__unit"
            >
              <el-option
                label="MB"
                value="mib"
              />
              <el-option
                label="GB"
                value="gib"
              />
            </el-select>
          </div>
        </ElFormItem>
      </ElForm>
      <template #footer>
        <el-button
          :disabled="capacitySaving"
          @click="capacityDialogOpen = false"
        >
          {{ t('common.cancel') }}
        </el-button>
        <el-button
          type="primary"
          :loading="capacitySaving"
          @click="submitCapacity"
        >
          {{ t('common.save') }}
        </el-button>
      </template>
    </el-dialog>

    <InsightGatewayDetailDrawer
      v-model="detailOpen"
      :node-id="detailNodeId"
      :initial-tab="detailInitialTab"
      :gateway-scope="isPlatformEngine ? 'platform' : 'user'"
      @saved="load"
    />
    <NodeLifecycleUpgradeConfirmDialog
      v-model="upgradeConfirmOpen"
      :preview="upgradeConfirmPreview"
      @confirm="lifecycleOps.resolveUpgradeConfirm()"
      @cancel="lifecycleOps.cancelUpgradeConfirm()"
    />
    <DangerConfirmDialog
      v-model="deleteOpen"
      :title="deleteForce ? t('insight.dataGateway.deleteForceTitle') : t('nodesPage.deleteTitle')"
      :message="deleteForce ? t('insight.dataGateway.deleteForceConfirm', { n: pendingDelete.length }) : deleteConfirmationMessage"
      :items="pendingDelete.map(gatewayDeleteDialogItem)"
      confirm-mode="keyword"
      :confirm-keyword="deleteForce ? 'FORCE CLEANUP' : t('common.deleteKeyword')"
      :cancel-text="t('common.cancel')"
      :confirm-text="deleteForce ? t('nodesPage.proxyDeleteForceAction') : t('nodesPage.actionDelete')"
      :loading="deleteLoading"
      @confirm="confirmDelete"
      @cancel="cancelDelete"
    >
      <div class="gateway-delete-force-option">
        <ElCheckbox v-model="deleteForce">
          {{ t('nodesPage.proxyDeleteForceAction') }}
        </ElCheckbox>
        <p>{{ t('insight.dataGateway.deleteForceHint') }}</p>
      </div>
    </DangerConfirmDialog>
  </div>
</template>

<style scoped>
.dg-ks-link {
  border: none;
  background: none;
  padding: 0;
  color: var(--color-primary, #2563eb);
  cursor: pointer;
  font-size: 13px;
}

.gateway-delete-force-option {
  margin-top: 16px;
  padding: 12px 14px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-fill-color-lighter);
}

.gateway-delete-force-option p {
  margin: 4px 0 0 24px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.dg-muted {
  color: var(--color-text-tertiary, #999);
}

.dg-capacity-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 0;
}

.dg-capacity-cell__edit {
  padding: 0;
  height: auto;
  font-size: 12px;
}

.dg-capacity-cell__incomplete {
  color: var(--el-color-warning);
  font-size: 11px;
  line-height: 1.3;
}

.dg-capacity-dialog__hint {
  margin: 0 0 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.45;
}

.dg-capacity-dialog__warn {
  margin: 0 0 12px;
  color: var(--el-color-warning);
  font-size: 12px;
  line-height: 1.4;
}

.dg-capacity-dialog__input {
  display: flex;
  gap: 10px;
  width: 100%;
}

.dg-capacity-dialog__unit {
  flex: 0 0 104px;
}
</style>
