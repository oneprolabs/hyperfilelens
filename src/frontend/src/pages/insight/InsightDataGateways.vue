<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ArrowUpCircle,
  ChevronDown,
  Gauge,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Wrench,
} from 'lucide-vue-next'
import { ElCheckbox, ElInputNumber, ElMessage, type ElTable } from 'element-plus'
import NodeVersionCell from '../../components/node-lifecycle/NodeVersionCell.vue'
import NodeLifecycleStatusCell from '../../components/node-lifecycle/NodeLifecycleStatusCell.vue'
import NodeLifecycleBanner from '../../components/node-lifecycle/NodeLifecycleBanner.vue'
import NodeLifecycleUpgradeConfirmDialog from '../../components/NodeLifecycleUpgradeConfirmDialog.vue'
import AgentPlatformBrandIcon from '../../components/agent-deploy/AgentPlatformBrandIcon.vue'
import HflCapacityCell from '../../components/HflCapacityCell.vue'
import DangerOperationHint from '../../components/DangerOperationHint.vue'
import DangerForceOption from '../../components/DangerForceOption.vue'
import DangerAdvancedOptions from '../../components/DangerAdvancedOptions.vue'
import DangerConfirmDialog from '../../components/DangerConfirmDialog.vue'
import { useNodeLifecycleOps } from '../../composables/useNodeLifecycleOps'
import { useResourceCapabilities } from '../../composables/useResourceCapabilities'
import { useListTableLayout } from '../../composables/useListTableLayout'
import { useListSearch } from '../../composables/useListSearch'
import { usePlatformEngineListSearch } from '../../composables/usePlatformEngineListSearch'
import { usePageRequestScope } from '../../composables/usePageRequestScope'
import { fetchDeployProfile, getCachedDeployProfile } from '../../composables/useDeployProfile'
import { sanitizeErrorDetails } from '../../lib/errors/details'
import { apiErrorMessage } from '../../lib/api'
import { applyGatewayDirectoryStatus } from '../../lib/gatewayDirectory'
import { afterOverlayDismiss } from '../../lib/uiDefer'
import { LIST_ROUTE_REFRESH_KEY, stripListRefreshQuery } from '../../lib/listRouteRefresh'
import {
  fetchLensHealth,
  listLensGatewayDirectory,
  listLensGateways,
  refreshLensGatewayDirectoryStatus,
  setLensApiScope,
  type LensGatewayInsight,
} from '../../lib/lensApi'
import {
  fetchLatestAgentVersion,
} from '../../lib/nodeApi'
import { canRemoteAgentUpgrade } from '../../lib/agentVersion'
import {
  isGatewayConnectivityOnline,
  type GatewayAiPhase,
} from '../../lib/gatewayDisplayStatus'
import {
  gatewayInventoryType,
  type GatewayInventoryType,
} from '../../lib/gatewayInventoryType'
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
import { gatewayAiFromInsight } from './gatewayAiStatus'
import GatewayCompositeStatusCell from './GatewayCompositeStatusCell.vue'
import PlatformOpsPagination from '../../platform-ops/components/PlatformOpsPagination.vue'
import {
  fetchPublicGatewayCapacities,
  patchPublicGatewayCapacity,
  type PlatformGatewayCapacity,
} from '../../platform-ops/lib/platformOpsApi'
import type { ApiNode } from '../../types/node'
import '../../components/backupSourceFlowActionDialog.css'

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
const {
  listSearchPlaceholder,
  listSearchClass,
  listSearchSize,
} = usePlatformEngineListSearch('insight.dataGateway.searchPlaceholder', 'gateways')
const platformPermissions = ref(getCachedDeployProfile()?.platform_permissions ?? [])
const canManagePlatformGateways = computed(() => platformPermissions.value.includes('platform.infra.public_gateway.manage'))
const canManagePlatformCapacity = computed(() => platformPermissions.value.includes('platform.infra.public_gateway.capacity.manage'))
let disposed = false

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
const filterType = ref<'' | GatewayInventoryType>('')
const filterOrgKey = ref('')
const knownOrganizations = ref<Array<{ key: string; name: string; id: number }>>([])
const bridgeReady = ref(false)
const latestAgentVersion = ref<string | null>(null)
const selectedRows = ref<InsightGatewayRow[]>([])
const deleteOpen = ref(false)
const deleteLoading = ref(false)
const advancedExpanded = ref(false)
const deleteForce = ref(false)
watch(() => deleteOpen.value, () => { advancedExpanded.value = false })
const pendingDelete = ref<InsightGatewayRow[]>([])
const deleteStartErrors = ref<Array<{ nodeId: number; name: string; message: string; suggestForce: boolean }>>([])
watch(deleteOpen, () => { deleteStartErrors.value = [] })
const moreActionsOpen = ref(false)
const detailOpen = ref(false)
const detailNodeId = ref<number | null>(null)
const detailInitialTab = ref<'basic' | 'performance' | 'maintenance'>('basic')
const detailInitialGatewayAi = ref<ReturnType<typeof gatewayAiFromInsight> | null>(null)
const detailInitialWorkspaceCapacity = ref<PlatformGatewayCapacity | null>(null)
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
let lastSuccessfulDirectoryQuery: {
  page: number
  pageSize: number
  search: string
} | null = null
const organizationFilterOptions = computed(() => {
  const options = knownOrganizations.value
  const selected = filterOrgKey.value.trim()
  if (!selected || options.some((org) => org.key === selected)) return options
  return [{ key: selected, name: selected, id: 0 }, ...options]
})
const inventoryFilteredRows = computed(() => {
  if (!isPlatformEngine.value) return rows.value
  const type = filterType.value
  const orgKey = filterOrgKey.value.trim()
  return rows.value.filter((row) => {
    if (type && gatewayInventoryType(row) !== type) return false
    if (orgKey && String(row.organization_key || '').trim() !== orgKey) return false
    return true
  })
})
const visibleRows = computed(() => {
  if (!isPlatformEngine.value) return rows.value
  const list = inventoryFilteredRows.value
  const start = (pagination.page - 1) * pagination.pageSize
  return list.slice(start, start + pagination.pageSize)
})
const { appliedSearch, clearSearch, resetSearch } = useListSearch(search, () => {
  pagination.page = 1
  void load()
})
watch([filterType, filterOrgKey], () => {
  if (!isPlatformEngine.value) return
  pagination.page = 1
  // reserve-selection keeps hidden rows selected across client filters; clear so
  // batch delete/upgrade cannot target gateways outside the active filter.
  clearSelection()
})
watch(inventoryFilteredRows, (list) => {
  if (!isPlatformEngine.value) return
  pagination.count = list.length
  const lastPage = Math.max(1, Math.ceil(list.length / pagination.pageSize) || 1)
  if (pagination.page > lastPage) pagination.page = lastPage
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
        availability: next.availability,
        routable: next.routable,
        version: next.version,
        lifecycle: next.lifecycle,
        is_deleted: next.is_deleted,
      }
    })
    // Remount / empty refresh: re-insert in-flight lifecycle rows from watch payload.
    for (const node of patched) {
      if (!isPlatformEngine.value) continue
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
    if (isPlatformEngine.value) {
      pagination.count = Math.max(pagination.count, rows.value.length)
    }
  },
})
const upgradeConfirmOpen = lifecycleOps.upgradeConfirmOpen
const upgradeConfirmPreview = lifecycleOps.upgradeConfirmPreview

const tableLoading = computed(() => !listLoaded.value)
const singleSelected = computed(() => (selectedRows.value.length === 1 ? selectedRows.value[0]! : null))
const resourcePermissions = useResourceCapabilities(computed(() => isPlatformEngine.value ? [] : rows.value
  .filter((row) => row.origin !== 'platform')
  .map((row) => ({ resource_type: 'node' as const, resource_id: row.id }))))
const batchDisabled = computed(() => selectedRows.value.length === 0 || !selectedRows.value.every((row) =>
  isPlatformEngine.value ? canManagePlatformGateways.value : resourcePermissions.permitted('node', row.id, 'delete'),
))
const batchRenameDisabled = computed(() => selectedRows.value.length !== 1 || !selectedRows.value.every(canManageGateway))
const batchCapacityDisabled = computed(() => {
  const row = singleSelected.value
  return !row || !canManageCapacity(row)
})
const batchUpgradeDisabled = computed(() => {
  const upgradable = selectedRows.value.filter((row) => lifecycleOps.canUpgradeNode(row, canUpgrade))
  return upgradable.length === 0
})
const showPlatformMoreActions = computed(() => (
  !isPlatformEngine.value || canManagePlatformGateways.value || canManagePlatformCapacity.value
))

function canUpgrade(row: ApiNode) {
  if ((row as InsightGatewayRow).managed_by_hfl === false) return false
  if (isPlatformEngine.value && !canManagePlatformGateways.value) return false
  if (!isPlatformEngine.value && !resourcePermissions.permitted('node', row.id, 'upgrade')) return false
  return row.agent_release?.upgrade_version_allowed
    ?? canRemoteAgentUpgrade(row.version, latestAgentVersion.value)
}

function platformLabel(row: ApiNode) {
  return nodePlatformLabel(nodeEnrollmentOs(row), osPlatformLabels.value)
}

function ipLine(row: ApiNode) {
  return row.ip_address?.trim() || '—'
}

function availabilityLabel(row: InsightGatewayRow) {
  return isGatewayConnectivityOnline(row)
    ? t('protection.sourceResources.nodeStatusOnline')
    : t('protection.sourceResources.nodeStatusOffline')
}

function availabilityTagType(row: InsightGatewayRow): 'success' | 'danger' {
  return isGatewayConnectivityOnline(row) ? 'success' : 'danger'
}

function aiPhase(row: InsightGatewayRow): GatewayAiPhase {
  if (isPlatformEngine.value && canManagePlatformGateways.value && !bridgeReady.value) return 'not_provisioned'
  if (row.managed_by_hfl === false) return 'not_provisioned'
  if (!row.hfl_agent_online) return 'agent_offline'
  if (row.sidecar_status === 'error') return 'error'
  if (isPlatformEngine.value ? !row.ai_enabled : !row.sl_lensnode_uuid) {
    if (row.status === 'online') return 'error'
    return 'not_provisioned'
  }
  if (row.sidecar_status === 'online') return 'online'
  if (row.sidecar_status === 'error') return 'error'
  if (row.sidecar_status === 'offline') return 'offline'
  if (row.sidecar_status === 'not_deployed') return 'error'
  return 'pending_install'
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
  if (isPlatformEngine.value && batchIds.size > 0) {
    const nextIds = new Set(next.map((row) => row.id))
    const ghosts = prev.filter((row) => batchIds.has(row.id) && !nextIds.has(row.id))
    if (ghosts.length) merged = [...next, ...ghosts]
  }
  rows.value = lifecycleOps.mergeNodeListDuringLifecycleBatch(merged, prev, batchIds)
  pagination.count = totalHint != null
    ? (isPlatformEngine.value ? Math.max(totalHint, rows.value.length) : totalHint)
    : rows.value.length
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
  if (!canManageCapacity(row)) return
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
  if (!row || !canManageCapacity(row) || capacitySaving.value) return
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

function refreshCurrentPageStatus(force = false) {
  if (isPlatformEngine.value || rows.value.length === 0) return
  const signal = pageRequests.nextSignal('dg-status')
  void refreshLensGatewayDirectoryStatus({
    gateway_ids: rows.value.map((row) => row.id),
    force,
    signal,
  }).then((patches) => {
    if (!pageRequests.isCurrentSignal('dg-status', signal)) return
    rows.value = applyGatewayDirectoryStatus(rows.value, patches)
  }).catch(() => {
    // Status is optional. Preserve the authoritative directory and cached state.
  }).finally(() => {
    pageRequests.releaseSignal('dg-status', signal)
  })
}

async function loadSupplementaryData() {
  const signal = pageRequests.nextSignal('dg-extras')
  try {
    await Promise.all([
      ...(!isPlatformEngine.value || canManagePlatformGateways.value ? [loadLatestVersion(signal)] : []),
      loadCapacities(signal),
    ])
  } finally {
    pageRequests.releaseSignal('dg-extras', signal)
  }
}

async function load(options: { forceStatus?: boolean } = {}) {
  const signal = pageRequests.nextSignal('dg-list')
  const directoryQuery = {
    page: pagination.page,
    pageSize: pagination.pageSize,
    search: appliedSearch.value,
  }
  pageRequests.abortScope('dg-status')
  pageRequests.abortScope('dg-extras')
  busy.value = true
  if (isPlatformEngine.value) {
    setLensApiScope('platform')
  }
  try {
    // The platform's external SL directory keeps its existing health semantics.
    // Private membership never waits for, or invokes, this global SL probe.
    const healthRequest = isPlatformEngine.value && canManagePlatformGateways.value
      ? fetchLensHealth({ signal }).catch(() => null)
      : null
    const lensResult = isPlatformEngine.value
      ? await listLensGateways({ signal }).catch((error) => {
          if (pageRequests.isAbortError(error)) throw error
          return null
        })
      : await listLensGatewayDirectory({
          page: directoryQuery.page,
          page_size: directoryQuery.pageSize,
          search: directoryQuery.search,
          signal,
        })
    if (healthRequest) {
      const health = await healthRequest
      if (pageRequests.isCurrentSignal('dg-list', signal)) {
        bridgeReady.value = Boolean(health?.lens?.configured && health?.lens?.authenticated)
      }
    }
    if (!pageRequests.isCurrentSignal('dg-list', signal)) return
    if (lensResult === null) {
      // Preserve the platform's existing cached-list/health degradation.
      if (rows.value.length === 0) {
        ElMessage.error({ message: t('errors.generic.loadFailed'), grouping: true })
      }
      await loadLatestVersion(signal)
      return
    }
    const lensRows = Array.isArray(lensResult) ? lensResult : lensResult.results
    const directoryTotal = Array.isArray(lensResult) ? null : lensResult.count
    const term = appliedSearch.value.trim().toLowerCase()
    const filtered = isPlatformEngine.value && term
      ? lensRows.filter((row) => {
          const name = (row.name || '').toLowerCase()
          const ip = String(row.ip_address || '').toLowerCase()
          const org = `${row.organization_name || ''} ${row.organization_key || ''}`.toLowerCase()
          return name.includes(term) || ip.includes(term) || org.includes(term)
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
          availability: base.availability,
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
    if (
      !isPlatformEngine.value
      && directoryTotal != null
      && next.length === 0
      && pagination.page > 1
    ) {
      pagination.page = Math.max(1, Math.ceil(directoryTotal / pagination.pageSize))
      await load(options)
      return
    }
    rememberOrganizations(next)
    applyGatewayRows(next, directoryTotal ?? filtered.length)
    if (!isPlatformEngine.value) lastSuccessfulDirectoryQuery = directoryQuery
    refreshCurrentPageStatus(options.forceStatus === true)
    if (isPlatformEngine.value) await loadSupplementaryData()
    else void loadSupplementaryData()
  } catch (e) {
    if (pageRequests.isAbortError(e) || !pageRequests.isCurrentSignal('dg-list', signal)) return
    if (!isPlatformEngine.value && lastSuccessfulDirectoryQuery) {
      // Retained rows and their paging/search controls must describe one result.
      pagination.page = lastSuccessfulDirectoryQuery.page
      pagination.pageSize = lastSuccessfulDirectoryQuery.pageSize
      if (search.value === directoryQuery.search) {
        resetSearch(lastSuccessfulDirectoryQuery.search)
      } else {
        // Do not discard a newer search draft that has not been applied yet.
        appliedSearch.value = lastSuccessfulDirectoryQuery.search
      }
    }
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
  detailInitialGatewayAi.value = gatewayAiFromInsight(row)
  detailInitialWorkspaceCapacity.value = capacityFor(row) ?? null
  detailOpen.value = true
}

function openSelectedMaintenance() {
  const row = singleSelected.value
  if (!row || !canManageGateway(row)) return
  detailNodeId.value = row.id
  detailInitialTab.value = 'maintenance'
  detailInitialGatewayAi.value = gatewayAiFromInsight(row)
  detailInitialWorkspaceCapacity.value = capacityFor(row) ?? null
  detailOpen.value = true
}

function canManageGateway(row: InsightGatewayRow) {
  return row.managed_by_hfl !== false && row.id > 0
    && (isPlatformEngine.value ? canManagePlatformGateways.value : resourcePermissions.permitted('node', row.id, 'manage'))
}

function canManageCapacity(row: InsightGatewayRow) {
  return isPlatformEngine.value && canManagePlatformCapacity.value
    && row.origin === 'platform' && row.managed_by_hfl !== false && row.id > 0
}

function readinessLabel(row: InsightGatewayRow) {
  if (!row.managed_by_hfl) return `SL ${row.sl_runtime_status || row.sl_status || 'Offline'} · HFL Agent Missing`
  if (!row.hfl_agent_online) return 'HFL Agent Offline'
  if (!row.hfl_sidecar_online) return 'HFL LensNode Sidecar Offline'
  return 'HFL Ready'
}

function inventoryTypeLabel(row: InsightGatewayRow) {
  return t(`insight.dataGateway.type.${gatewayInventoryType(row)}`)
}

function organizationLabel(row: InsightGatewayRow) {
  return String(row.organization_name || '').trim()
}

function organizationId(row: InsightGatewayRow) {
  return Number(row.owner_organization_id || row.organization || 0) || null
}

function rememberOrganizations(list: InsightGatewayRow[]) {
  if (!isPlatformEngine.value) return
  const byKey = new Map(knownOrganizations.value.map((org) => [org.key, org]))
  for (const row of list) {
    const key = String(row.organization_key || '').trim()
    if (!key) continue
    byKey.set(key, {
      key,
      name: String(row.organization_name || key).trim() || key,
      id: organizationId(row) || 0,
    })
  }
  knownOrganizations.value = [...byKey.values()].sort(
    (a, b) => a.name.localeCompare(b.name) || a.key.localeCompare(b.key),
  )
}

function onSelectionChange(selection: InsightGatewayRow[]) {
  selectedRows.value = selection
}

function clearSelection() {
  selectedRows.value = []
  tableRef.value?.clearSelection()
}

type MoreAction = 'capacity' | 'upgrade' | 'maintenance' | 'remove'

async function handleMoreAction(command: MoreAction) {
  if (command === 'capacity') openSelectedCapacity()
  else if (command === 'upgrade') await onUpgradeSelected()
  else if (command === 'maintenance') openSelectedMaintenance()
  else if (command === 'remove') await deleteSelected()
}

function openSelectedCapacity() {
  const row = singleSelected.value
  if (!row || !canManageCapacity(row)) return
  openCapacityDialog(row)
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

function cancelDelete() {
  pendingDelete.value = []
  deleteForce.value = false
  deleteStartErrors.value = []
}

async function confirmDelete() {
  const targets = [...pendingDelete.value]
  if (!targets.length || deleteLoading.value) return
  const force = deleteForce.value
  deleteStartErrors.value = []
  deleteLoading.value = true
  try {
    const started = await lifecycleOps.runBatch('remove', targets, {
      skipConfirm: true,
      force,
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
      deleteStartErrors.value = lifecycleOps.lastStartErrors.value.flatMap((item) => {
        const nodeId = Number(item.node_id || 0)
        const target = targets.find(row => row.id === nodeId)
        if (!target) return []
        const message = sanitizeErrorDetails({
          title: '',
          summary: apiErrorMessage(new Error(String(item.error || item.code || '')), t('errors.generic.loadFailed')),
        }).summary
        return [{
          nodeId,
          name: target.name,
          message,
          suggestForce: !force && ['node_offline', 'agent_uninstall_failed', 'completion_callback_timeout']
            .includes(String(item.code || '')),
        }]
      })
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
  const needsPoll = isPlatformEngine.value
    ? rows.value.some((row) => row.ai_enabled && aiPhase(row) !== 'online')
    : rows.value.some((row) => Boolean(row.sl_lensnode_uuid))
  if (!needsPoll) {
    stopListPoll()
    return
  }
  if (listPollTimer != null) return
  listPollTimer = setInterval(() => {
    if (busy.value) return
    if (isPlatformEngine.value) void load()
    else refreshCurrentPageStatus()
  }, isPlatformEngine.value ? 12000 : 30000)
}

watch(rows, () => startListPoll(), { deep: true })

onMounted(async () => {
  if (isPlatformEngine.value) {
    setLensApiScope('platform')
    const profile = await fetchDeployProfile()
    if (disposed) return
    platformPermissions.value = profile?.platform_permissions ?? []
  }
  // Restore in-flight queue before first load so Removing rows can be retained.
  if (!isPlatformEngine.value || canManagePlatformGateways.value) lifecycleOps.restorePersisted()
  await load()
})

onUnmounted(() => {
  disposed = true
  stopListPoll()
})
</script>

<template>
  <div class="hfl-list-shell hfl-list-shell--fill">
    <div class="hfl-list-panel hfl-list-panel--fill">
      <div class="hfl-list-toolbar hfl-list-toolbar--mobile-primary-utility">
        <div class="hfl-list-toolbar__primary">
          <RouterLink
            v-if="!isPlatformEngine || canManagePlatformGateways"
            :to="isPlatformEngine ? '/platform-ops/engine/gateways/add' : '/node/nodes/deploy?role=gateway'"
            class="inline-flex"
          >
            <ElButton type="primary">
              <Plus :size="16" />
              {{ isPlatformEngine ? t('platformOps.engineGateway.addAction') : t('insight.dataGateway.btnAdd') }}
            </ElButton>
          </RouterLink>

          <ElDropdown
            v-if="showPlatformMoreActions"
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
                  v-if="isPlatformEngine && canManagePlatformCapacity"
                  command="capacity"
                  :disabled="batchCapacityDisabled"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Gauge
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('platformOps.engineGateway.editCapacity') }}</span>
                  </span>
                </ElDropdownItem>
                <ElDropdownItem
                  v-if="!isPlatformEngine || canManagePlatformGateways"
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
                  v-if="!isPlatformEngine || canManagePlatformGateways"
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
                  v-if="!isPlatformEngine || canManagePlatformGateways"
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
          <el-select
            v-if="isPlatformEngine"
            v-model="filterType"
            clearable
            size="small"
            class="dg-inventory-filter"
            :placeholder="t('insight.dataGateway.typeFilterPlaceholder')"
            :aria-label="t('insight.dataGateway.typeFilterPlaceholder')"
          >
            <el-option
              value="bundled"
              :label="t('insight.dataGateway.type.bundled')"
            />
            <el-option
              value="installed"
              :label="t('insight.dataGateway.type.installed')"
            />
            <el-option
              value="private"
              :label="t('insight.dataGateway.type.private')"
            />
          </el-select>
          <el-select
            v-if="isPlatformEngine"
            v-model="filterOrgKey"
            clearable
            filterable
            size="small"
            class="dg-inventory-filter dg-inventory-filter--org"
            :placeholder="t('insight.dataGateway.orgFilterPlaceholder')"
            :aria-label="t('insight.dataGateway.orgFilterPlaceholder')"
          >
            <el-option
              v-for="org in organizationFilterOptions"
              :key="org.key"
              :value="org.key"
              :label="org.key && org.key !== org.name ? `${org.name} (${org.key})` : org.name"
            />
          </el-select>
          <ElInput
            v-model="search"
            clearable
            :size="listSearchSize"
            :placeholder="listSearchPlaceholder"
            :class="listSearchClass"
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
              @click="load({ forceStatus: true })"
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
          v-table-column-resize="'insight.dataGateways.list.v2'"
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
            v-if="!isPlatformEngine || canManagePlatformGateways"
            type="selection"
            width="35"
            fixed="left"
            :selectable="(row: InsightGatewayRow) => row.managed_by_hfl !== false && row.id > 0 && (isPlatformEngine || ['manage', 'delete', 'upgrade'].some((action) => resourcePermissions.permitted('node', row.id, action as 'manage' | 'delete' | 'upgrade')))"
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
            :label="t('insight.dataGateway.colType')"
            min-width="150"
          >
            <template #default="{ row }">
              <span>{{ inventoryTypeLabel(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column
            v-if="isPlatformEngine"
            :label="t('insight.dataGateway.colOrganization')"
            min-width="140"
          >
            <template #default="{ row }">
              <RouterLink
                v-if="organizationId(row) && organizationLabel(row)"
                :to="`/platform-ops/orgs/${organizationId(row)}`"
                class="hfl-table-name-link"
              >
                {{ organizationLabel(row) }}
              </RouterLink>
              <span
                v-else
                :class="{ 'hfl-empty-mark': !organizationLabel(row) }"
              >{{ organizationLabel(row) || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.sourceResources.colLifecycleStatus')"
            min-width="140"
            align="center"
            header-align="center"
          >
            <template #default="{ row }">
              <div class="hfl-table-no-tooltip">
                <NodeLifecycleStatusCell
                  :node="row"
                  :resolve-display-status="lifecycleOps.resolveDisplayStatus"
                />
              </div>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.sourceResources.colHostIp')"
            min-width="130"
          >
            <template #default="{ row }">
              {{ ipLine(row) }}
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.sourceResources.colOs')"
            min-width="110"
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
            :label="t('protection.sourceResources.colCpu')"
            min-width="88"
          >
            <template #default="{ row }">
              <span
                class="dg-metric-cell"
                :class="{ 'hfl-empty-mark': nodeCpuCores(row) == null }"
              >{{ nodeCpuCores(row) != null ? t('protection.sourceResources.cpuCoresValue', { n: nodeCpuCores(row) }) : '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.sourceResources.colMemory')"
            min-width="96"
          >
            <template #default="{ row }">
              <span
                class="dg-metric-cell"
                :class="{ 'hfl-empty-mark': nodeMemoryTotalBytes(row) == null }"
              >{{ nodeMemoryTotalBytes(row) != null ? formatNodeBytes(nodeMemoryTotalBytes(row)!) : '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.sourceResources.colDiskCount')"
            min-width="80"
          >
            <template #default="{ row }">
              <span
                class="dg-metric-cell"
                :class="{ 'hfl-empty-mark': nodeDiskCount(row) == null }"
              >{{ nodeDiskCount(row) ?? '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.sourceResources.colCapacity')"
            min-width="180"
            class-name="dg-capacity-col"
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
            v-if="isPlatformEngine"
            :label="t('platformOps.engineGateway.colCapacity')"
            min-width="180"
            class-name="dg-capacity-col"
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
              </div>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.sourceResources.colConnectivity')"
            min-width="100"
            align="center"
            header-align="center"
          >
            <template #default="{ row }">
              <div class="hfl-table-no-tooltip">
                <ElTag
                  :type="availabilityTagType(row)"
                  size="small"
                >
                  {{ availabilityLabel(row) }}
                </ElTag>
              </div>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.dataGateway.colAiEngine')"
            min-width="130"
            align="center"
            header-align="center"
          >
            <template #default="{ row }">
              <div class="hfl-table-no-tooltip">
                <GatewayCompositeStatusCell
                  mode="ai-engine"
                  :ai-phase="aiPhase(row)"
                />
              </div>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.sourceResources.colVersion')"
            width="108"
            align="center"
            header-align="center"
            class-name="dg-version-col"
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
            :label="t('protection.sourceResources.colRegisteredAt')"
            min-width="168"
          >
            <template #default="{ row }">
              <span
                class="hfl-table-cell-time"
                :class="{ 'hfl-empty-mark': !row.created_at }"
              >{{ formatNodeDate(row.created_at) }}</span>
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
      v-model="capacityDialogOpen"
      class="hfl-flow-action-dialog hfl-flow-action-dialog--form dg-capacity-dialog"
      :title="t('platformOps.engineGateway.capacityDialogTitle')"
      align-center
      destroy-on-close
    >
      <p class="hfl-flow-action-dialog__form-hint">
        {{ t('platformOps.engineGateway.capacityDialogHint') }}
      </p>
      <p
        v-if="capacityTarget && capacityFor(capacityTarget)?.used_incomplete"
        class="hfl-flow-action-dialog__form-hint hfl-flow-action-dialog__form-hint--sub dg-capacity-dialog__warn"
      >
        {{ t('platformOps.engineGateway.capacityUsedIncomplete') }}
      </p>
      <ElForm
        label-position="top"
        class="hfl-flow-action-dialog__form dg-capacity-dialog__form"
        @submit.prevent="submitCapacity"
      >
        <ElFormItem
          :label="t('platformOps.engineGateway.capacityLabel')"
          required
        >
          <div class="dg-capacity-dialog__input">
            <ElInputNumber
              v-model="capacityDraft"
              class="dg-capacity-dialog__number"
              :min="0"
              :step="1"
              :precision="0"
              :disabled="capacityUnlimited"
            />
            <el-select
              v-model="capacityUnit"
              class="dg-capacity-dialog__unit"
              :disabled="capacityUnlimited"
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
        <ElFormItem class="dg-capacity-dialog__unlimited">
          <ElCheckbox v-model="capacityUnlimited">
            {{ t('platformOps.engineGateway.capacityUnlimited') }}
          </ElCheckbox>
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
      :initial-gateway-ai="detailInitialGatewayAi"
      :initial-workspace-capacity="detailInitialWorkspaceCapacity"
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
      class="dg-delete-confirm"
      width="640px"
      :title="deleteForce ? t('insight.dataGateway.deleteForceTitle') : t('insight.dataGateway.deleteTitle')"
      :message="deleteForce ? t('insight.dataGateway.deleteForceConfirm', { n: pendingDelete.length }) : deleteConfirmationMessage"
      :warning="deleteForce ? t('insight.dataGateway.deleteForceHint') : ''"
      :items="pendingDelete.map(gatewayDeleteDialogItem)"
      :items-heading="t('nodesPage.selectedCount', { n: pendingDelete.length })"
      :item-name-label="t('nodeLifecycle.entityGateway')"
      :item-status-label="t('common.dangerItemStatus')"
      :show-item-details="false"
      :confirmation-context="pendingDelete.map(row => row.id).sort((a, b) => a - b).join(',')"
      confirm-mode="keyword"
      :confirm-keyword="deleteForce ? 'FORCE DELETE' : t('common.deleteKeyword')"
      :confirm-keyword-placeholder="deleteForce ? 'FORCE DELETE' : t('common.deleteKeyword')"
      :confirm-keyword-hint="t('common.dangerConfirmKeywordHint', { keyword: deleteForce ? 'FORCE DELETE' : t('common.deleteKeyword') })"
      :cancel-text="t('common.cancel')"
      :confirm-text="deleteForce ? t('nodesPage.proxyDeleteForceAction') : t('nodesPage.actionDelete')"
      :loading="deleteLoading"
      :confirm-disabled="pendingDelete.length === 0"
      @confirm="confirmDelete"
      @cancel="cancelDelete"
    >
      <div
        v-if="deleteStartErrors.length"
        class="gateway-delete-errors"
        role="alert"
      >
        <div
          v-for="(error, index) in deleteStartErrors"
          :key="`${error.nodeId}-${index}`"
        >
          <p><strong>{{ error.name }}</strong>: {{ error.message }}</p>
          <p v-if="error.suggestForce">
            {{ t('common.gatewayForceRetryHint') }}
          </p>
        </div>
      </div>
      <DangerOperationHint :text="t('common.gatewayRemovalLimits')" />
      <DangerAdvancedOptions
        v-model:expanded="advancedExpanded"
        :enabled="deleteForce"
        :enabled-label="t('common.forceCleanupEnabled')"
      >
        <DangerForceOption
          v-model="deleteForce"
          :disabled="deleteLoading"
          :label="t('nodesPage.proxyDeleteForceAction')"
          :hint="t('common.gatewayForceExplanation')"
        />
      </DangerAdvancedOptions>
    </DangerConfirmDialog>
  </div>
</template>

<style scoped>
.gateway-delete-errors {
  margin-top: 16px;
  color: var(--el-color-danger);
}


.dg-metric-cell {
  font-variant-numeric: tabular-nums;
}

:deep(.dg-version-col .node-version-cell) {
  align-items: center;
}

:deep(.dg-version-col .node-version-cell__value),
:deep(.dg-version-col .node-version-cell__from),
:deep(.dg-version-col .node-version-cell__to) {
  font-variant-numeric: tabular-nums;
  line-height: 1.25;
}

:deep(.dg-version-col .node-version-cell__hint) {
  justify-content: center;
}

.dg-inventory-filter {
  width: 168px;
}

.dg-inventory-filter--org {
  width: 200px;
}

.dg-capacity-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 0;
  width: 100%;
}

/* Keep Host Capacity and Workspace Quota bars the same visual length. */
:deep(.dg-capacity-col .repo-usage-cell) {
  width: 148px;
  max-width: 100%;
}

:deep(.dg-capacity-col .repo-usage-bar) {
  width: 148px;
  max-width: 100%;
}

.dg-capacity-cell__incomplete {
  color: var(--el-color-warning);
  font-size: 11px;
  line-height: 1.3;
  max-width: 148px;
}

:global(.dg-capacity-dialog.hfl-flow-action-dialog.el-dialog) {
  --el-dialog-width: min(480px, calc(100vw - 32px));
}

.dg-capacity-dialog__warn {
  margin-top: -8px;
  margin-bottom: 12px;
  color: var(--el-color-warning);
}

.dg-capacity-dialog__form :deep(.el-form-item) {
  margin-bottom: 16px;
}

.dg-capacity-dialog__form :deep(.el-form-item:last-child),
.dg-capacity-dialog__unlimited {
  margin-bottom: 0;
}

.dg-capacity-dialog__input {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.dg-capacity-dialog__number {
  flex: 1 1 auto;
  min-width: 0;
  width: 100%;
}

.dg-capacity-dialog__unit {
  flex: 0 0 104px;
  width: 104px;
}
</style>
