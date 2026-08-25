<script setup lang="ts">
import '../../styles/fullscreen-form-styles'
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, toRef, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowLeft, Plus, RefreshCw, Search, ChevronDown, Pencil, Trash2, TriangleAlert, ArrowUpCircle, Link2, Wrench } from 'lucide-vue-next'
import { ElMessage, type ElTable } from 'element-plus'
import ModulePage from '../../components/ModulePage.vue'
import BackupSourceDeleteDialog from '../../components/BackupSourceDeleteDialog.vue'
import NodeLifecycleStatusCell from '../../components/node-lifecycle/NodeLifecycleStatusCell.vue'
import NodeVersionCell from '../../components/node-lifecycle/NodeVersionCell.vue'
import NodeLifecycleBanner from '../../components/node-lifecycle/NodeLifecycleBanner.vue'
import NodeLifecycleUpgradeConfirmDialog from '../../components/NodeLifecycleUpgradeConfirmDialog.vue'
import ChangeProxyHostDialog from '../../components/ChangeProxyHostDialog.vue'
import { useNodeLifecycleOps } from '../../composables/useNodeLifecycleOps'
import {
  clearWizardPendingByNodeIds,
  clearWizardPendingBySourceIds,
  markWizardPendingBySourceIds,
  readWizardPendingSourceOps,
  type SourcePendingOp,
} from './composables/backupWizardPendingStorage'
import { debouncedNodeStatus } from '../../composables/useNodeConnectionDisplay'
import NodeLifecycleWizard from '../../components/NodeLifecycleWizard.vue'
import AgentPlatformBrandIcon from '../../components/agent-deploy/AgentPlatformBrandIcon.vue'
import HostSourceDetailDrawer from '../../components/HostSourceDetailDrawer.vue'
import HflCapacityCell from '../../components/HflCapacityCell.vue'
import HflHelpTip from '../../components/HflHelpTip.vue'
import NasAddForm from './components/NasAddForm.vue'
import NasSourceDetailDrawer from './components/NasSourceDetailDrawer.vue'
import FlowSourceReadyStatusCell from './components/FlowSourceReadyStatusCell.vue'
import { useProtectionSideNav } from '../../composables/useProtectionSideNav'
import { useListTableLayout } from '../../composables/useListTableLayout'
import { useListSearch } from '../../composables/useListSearch'
import { sourceAgentSidebarIcon, nasMountProtocolIcon } from '../../lib/resourceIcons'
import { usePageRequestScope } from '../../composables/usePageRequestScope'
import { getEffectiveOrgKey } from '../../composables/useAuth'
import { apiErrorMessage, isAbortError } from '../../lib/api'
import { formatAppDateTime } from '../../lib/dateTime'
import { copyTextToClipboard } from '../../lib/clipboard'
import { openErrorDetails, type ErrorDetailsPayload } from '../../lib/errors/details'
import { notifyInfo, notifySuccess } from '../../lib/notify'
import { getTask } from '../../lib/taskApi'
import {
  sourceUnregisterPendingKind,
  sourceUnregisterTaskBindings,
  sourceUnregisterTaskOutcome,
} from '../../lib/sourceUnregisterMonitor'
import {
  notifyUnregisterCleanupWarning,
  notifyUnregisterCleanupWarningBatch,
  notifyUnregisterFailureBatch,
  openUnregisterFailureDetails,
  previousUnregisterFailureDetails,
  unregisterFailureSummaryLine,
  unregisterFailureToErrorDetails,
  warningsForUnregisterSource,
} from '../../lib/unregisterFailureDetails'
import { LIST_ROUTE_REFRESH_KEY, stripListRefreshQuery } from '../../lib/listRouteRefresh'
import { buildGeneratedNasMountDir, buildGeneratedNasName } from '../../lib/nasMountPath'
import { hasNasSourceNameConflict, resolveNasSubmitName } from '../../lib/nasSourceNaming'
import { buildNasSourceCreatePayload } from '../../lib/nasSourceCreate'
import { defaultNasMountOptions } from '../../lib/nasMountOptions'
import { preflightSourceNasCreate, showNasDraftPreflightGuidance } from '../../lib/nasDraftPreflight'
import { listNodes, listNodesPaged, updateNode, fetchLatestAgentVersion, type EnrollmentOs } from '../../lib/nodeApi'
import { canRemoteAgentUpgrade } from '../../lib/agentVersion'
import { backupSourceLifecycleDisplay } from '../../lib/backupSourceLifecycleDisplay'
import type { ApiNode } from '../../types/node'
import {
  createSourceResource,
  getSourceResource,
  listSourceResources,
  sourceStatistics,
  testSourceConnection,
  SOURCE_CONNECTION_TEST_TIMEOUT_MS,
  SOURCE_CONNECTION_TEST_MIN_FEEDBACK_MS,
  SOURCE_CONNECTION_TEST_RESULT_TOAST_MS,
  updateSourceResource,
  type BackupSourceDeleteResult,
  type SourceResource,
  type SourceStats,
} from '../../lib/sourceApi'
import {
  nasMountProtocol,
  nasPathKindLabelKey,
  nasProxyMountPoint,
  nasServerAddress,
  nasShareOrExport,
  sourceNasStatusPresentation,
} from '../../lib/sourceNasDisplay'
import {
  nodeCpuCores,
  nodeDiskCount,
  nodeDiskUsageParts,
  nodeEnrollmentOs,
  nodeMemoryTotalBytes,
  nodePlatformLabel,
  formatNodeBytes,
  proxyNodeStackIpLine,
  proxyNodeStackPrimaryLine,
  type NodeOsPlatformLabels,
} from '../../lib/nodeInventoryDisplay'

const route = useRoute()
const router = useRouter()
const PROXY_DEPLOY_ROUTE = { path: '/node/nodes/deploy', query: { role: 'proxy' } } as const

const { t, te } = useI18n()
const sideNav = useProtectionSideNav()
const pageRequests = usePageRequestScope()

/* ---------- View (sidebar: host / NAS) ---------- */
type SourceViewTab = 'hostFileSystem' | 'nas'

function normalizeSourceTabParam(tab: unknown): SourceViewTab {
  if (tab === 'nas') return 'nas'
  if (tab === 'host' || tab === 'hostFileSystem') return 'hostFileSystem'
  return 'hostFileSystem'
}

function sourceTabQueryValue(tab: SourceViewTab): string {
  return tab === 'nas' ? 'nas' : 'host'
}

const activeTab = ref<SourceViewTab>('hostFileSystem')

const pageTitle = computed(() =>
  activeTab.value === 'nas' ? t('protection.side.sourceNas') : t('protection.side.sourceHosts'),
)

const listSearchPlaceholder = computed(() => t('protection.listSearch.placeholder'))

/* ---------- data ---------- */
const busy = ref(false)
const rows = ref<SourceResource[]>([])
const latestAgentVersion = ref<string | null>(null)
const stats = ref<SourceStats | null>(null)
const agentNodes = ref<ApiNode[]>([])
/** Published agent release from media/agent-releases (upgrade target). */
/** Local source rows keyed by agent node — enrichment only (capacity), not host list data. */
const hostSourceRows = ref<SourceResource[]>([])
const proxyNodes = ref<ApiNode[]>([])
const proxyNodesRefreshing = ref(false)
const shouldRefreshProxyNodesOnReturn = ref(false)
const sourceTabLoaded = reactive<Record<SourceViewTab, boolean>>({
  hostFileSystem: false,
  nas: false,
})
const pagination = reactive({ page: 1, pageSize: 30, count: 0 })

const TABLE_HEADER_STYLE: Record<string, string> = {
  background: 'rgba(248, 250, 252, 0.96)',
  color: 'rgb(71 85 105)',
  fontWeight: '600',
  whiteSpace: 'nowrap',
}
const hostTableRef = ref<InstanceType<typeof ElTable> | null>(null)
const nasTableRef = ref<InstanceType<typeof ElTable> | null>(null)
const hostTableBlockRef = ref<HTMLElement | null>(null)
const nasTableBlockRef = ref<HTMLElement | null>(null)
const {
  tableMaxHeight: hostTableMaxHeight,
  layoutTable: layoutHostTable,
  handleTableScroll: onHostTableScroll,
} = useListTableLayout(hostTableRef, hostTableBlockRef)
const {
  tableMaxHeight: nasTableMaxHeight,
  layoutTable: layoutNasTable,
  handleTableScroll: onNasTableScroll,
} = useListTableLayout(nasTableRef, nasTableBlockRef)
const filters = reactive({ search: '', search_field: 'name', resource_type: '', status: '' })
const {
  appliedSearch,
  clearSearch,
  handleSearchFieldChange,
  resetSearch,
  runSearchNow: runFilterSearch,
} = useListSearch(toRef(filters, 'search'), () => {
  applyFilters()
})
const searchFieldOptions = computed(() => (
  activeTab.value === 'nas'
    ? [
      { value: 'name', label: t('protection.listSearchFields.name') },
      { value: 'server', label: t('protection.listSearchFields.server') },
    ]
    : [
      { value: 'name', label: t('protection.listSearchFields.name') },
      { value: 'ip', label: t('protection.listSearchFields.ip') },
    ]
))

/* ---------- filtered rows per tab ---------- */
const nasRows = computed(() => rows.value.filter(r => r.resource_type === 'nas'))
const hostTableLoading = computed(() =>
  !sourceTabLoaded.hostFileSystem || (busy.value && activeTab.value === 'hostFileSystem'),
)
const nasTableLoading = computed(() =>
  !sourceTabLoaded.nas || (busy.value && activeTab.value === 'nas'),
)

const localSourceByNodeId = computed(() => {
  const map = new Map<number, SourceResource>()
  for (const row of hostSourceRows.value) {
    if (row.bound_node != null) map.set(row.bound_node, row)
  }
  return map
})

const filteredHostAgents = computed(() => {
  let list = agentNodes.value
  return list
})

const pagedHostAgents = computed(() => {
  return filteredHostAgents.value
})

/* ---------- edit / detail ---------- */
const dialogOpen = ref(false)
const editing = ref<SourceResource | null>(null)
const detailOpen = ref(false)
const detail = ref<SourceResource | null>(null)
const hostDetailOpen = ref(false)
const hostDetailNodeId = ref<number | null>(null)
const hostDetailSource = ref<SourceResource | null>(null)
const hostDetailInitialTab = ref<'basic' | 'performance' | 'maintenance'>('basic')
const formBusy = ref(false)

const form = reactive({
  name: '',
  description: '',
  resource_type: 'nfs' as string,
  bound_node_id: undefined as number | undefined,
  server: '',
  export_path: '',
  share: '',
  root_path: '',
  endpoint: '',
  bucket: '',
  username: '',
  password: '',
  access_key: '',
  secret_key: '',
})

const resourceTypes = ['local', 'nfs', 'cifs', 'nas', 's3'] as const
type SourceProtocol = 'smb' | 'nfs' | 'nas'

function typeLabel(code: string) {
  const key = `protection.sourceResources.types.${code}` as const
  return te(key) ? t(key) : code
}




function formatBytes(n?: number) {
  const v = Number(n || 0)
  if (!v) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let u = 0
  let x = v
  while (x >= 1024 && u < units.length - 1) {
    x /= 1024
    u += 1
  }
  return `${x.toFixed(1)} ${units[u]}`
}

function formatDate(iso?: string | null) {
  return formatAppDateTime(iso, '—')
}

function availabilityLabel(value?: string) {
  return value === 'online'
    ? t('protection.sourceResources.nodeStatusOnline')
    : t('protection.sourceResources.nodeStatusOffline')
}

function availabilityTagType(value?: string): 'success' | 'danger' {
  return value === 'online' ? 'success' : 'danger'
}

function agentUsageParts(node: ApiNode) {
  const source = localSourceByNodeId.value.get(node.id)
  if (source?.total_size) {
    return { used: Number(source.used_size || 0), total: Number(source.total_size) }
  }
  return nodeDiskUsageParts(node)
}

function nasProtocolPillClass(row: SourceResource) {
  const protocol = nasProtocolType(row)
  if (protocol === 'smb') return 'repo-protocol-pill repo-protocol-pill--smb'
  if (protocol === 'nfs') return 'repo-protocol-pill repo-protocol-pill--nfs'
  return 'repo-protocol-pill'
}

function onNasDetailUpdated(resource: SourceResource) {
  detail.value = resource
  void load()
}

function applyFilters() {
  pagination.page = 1
  void load()
}

const isNasDetail = computed(() => detail.value?.resource_type === 'nas')

/* ---------- Edit dialog helpers ---------- */
function buildPayload() {
  const config: Record<string, string> = {}
  const credentials: Record<string, string> = {}
  const rt = form.resource_type
  if (rt === 'local') {
    config.root_path = form.root_path.trim()
  } else if (rt === 'nfs') {
    config.server = form.server.trim()
    config.export_path = form.export_path.trim()
  } else if (rt === 'cifs') {
    config.server = form.server.trim()
    config.share = form.share.trim()
    credentials.username = form.username.trim()
    credentials.password = form.password
  } else if (rt === 'nas') {
    config.server = form.server.trim()
    if (form.share.trim()) config.share = form.share.trim()
  } else if (rt === 's3') {
    config.endpoint = form.endpoint.trim()
    config.bucket = form.bucket.trim()
    credentials.access_key = form.access_key.trim()
    credentials.secret_key = form.secret_key
  }
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    resource_type: rt,
    config,
    credentials,
    bound_node_id: form.bound_node_id ?? null,
  }
}



async function openDetail(row: SourceResource) {
  if (row.id > 0) {
    try {
      detail.value = await getSourceResource(row.id)
    } catch {
      detail.value = row
    }
  } else {
    detail.value = row
  }
  detailOpen.value = true
}

async function saveForm() {
  if (!form.name.trim()) {
    ElMessage.warning({ message: t('protection.sourceResources.formName'), grouping: true })
    return
  }
  formBusy.value = true
  try {
    const payload = buildPayload()
    if (!editing.value) {
      await createSourceResource(payload)
      ElMessage.success({ message: t('protection.sourceResources.created'), grouping: true })
    } else {
      await updateSourceResource(editing.value.id, payload)
      ElMessage.success({ message: t('protection.sourceResources.updated'), grouping: true })
    }
    dialogOpen.value = false
    await load()
  } catch (e) {
    ElMessage.error({ message: apiErrorMessage(e), grouping: true })
  } finally {
    formBusy.value = false
  }
}

/* ---------- actions ---------- */
async function loadAgentNodes(signal?: AbortSignal) {
  try {
    const prev = agentNodes.value
    const next = await listNodes({ role: 'agent' }, { signal })
    agentNodes.value = lifecycleOps.mergeNodeListDuringLifecycleBatch(
      next,
      prev,
      lifecycleOps.activeBatchNodeIds.value,
    )
  } catch (e) {
    if (pageRequests.isAbortError(e)) throw e
    agentNodes.value = []
  }
}

async function loadHostAgentPage(signal?: AbortSignal) {
  const result = await listNodesPaged(
    {
      role: 'agent',
      page: pagination.page,
      page_size: pagination.pageSize,
      search: appliedSearch.value.trim() || undefined,
      search_field: filters.search_field === 'ip' ? 'ip' : 'name',
    },
    { signal },
  )
  agentNodes.value = lifecycleOps.mergeNodeListDuringLifecycleBatch(
    result.results,
    agentNodes.value,
    lifecycleOps.activeBatchNodeIds.value,
  )
  pagination.count = result.count
}

async function loadHostSourceEnrichment(signal?: AbortSignal) {
  try {
    const list = await listSourceResources({ resource_type: 'local', page: 1, page_size: 500 }, { signal })
    hostSourceRows.value = list.results
  } catch (e) {
    if (pageRequests.isAbortError(e)) throw e
    hostSourceRows.value = []
  }
}

async function loadLatestAgentVersion(signal?: AbortSignal) {
  try {
    latestAgentVersion.value = await fetchLatestAgentVersion({ signal })
  } catch (e) {
    if (pageRequests.isAbortError(e)) throw e
    latestAgentVersion.value = null
  }
}

async function loadHostTab(signal?: AbortSignal) {
  await Promise.all([
    loadHostAgentPage(signal),
    loadHostSourceEnrichment(signal),
    loadProxyNodes(signal),
    loadLatestAgentVersion(signal),
  ])
}

const lifecycleOps = useNodeLifecycleOps({
  role: 'agent',
  t,
  onRefresh: () => loadHostTab(),
  onLifecyclePatch: (patched) => {
    const deletedNodeIds: number[] = []
    const byId = new Map(patched.map((node) => [node.id, node]))
    agentNodes.value = agentNodes.value.map((row) => {
      const next = byId.get(row.id)
      if (!next) return row
      if (next.is_deleted) deletedNodeIds.push(next.id)
      return {
        ...row,
        status: next.status,
        availability: next.availability ?? row.availability,
        routable: next.routable,
        version: next.version,
        lifecycle: next.lifecycle,
        is_deleted: next.is_deleted,
      }
    })
    if (deletedNodeIds.length) clearWizardPendingByNodeIds(deletedNodeIds)
  },
})
const upgradeConfirmOpen = lifecycleOps.upgradeConfirmOpen
const upgradeConfirmPreview = lifecycleOps.upgradeConfirmPreview

function resolveBackupSourceLifecycleStatus(node: ApiNode) {
  return backupSourceLifecycleDisplay(lifecycleOps.resolveDisplayStatus(node))
}

async function loadProxyNodes(signal?: AbortSignal) {
  try {
    proxyNodes.value = await listNodes({ role: 'proxy' }, { signal })
  } catch (e) {
    if (pageRequests.isAbortError(e)) throw e
    proxyNodes.value = []
  }
}

async function refreshProxyNodesManually() {
  proxyNodesRefreshing.value = true
  try {
    await loadProxyNodes()
    ElMessage.success({ message: t('protection.sourceResources.proxyRefreshSuccess'), grouping: true })
  } finally {
    proxyNodesRefreshing.value = false
  }
}

async function refreshProxyNodesAfterDeployReturn() {
  if (!shouldRefreshProxyNodesOnReturn.value || proxyNodesRefreshing.value) return
  if (!nasAddOpen.value && !rebindDialogOpen.value) return
  shouldRefreshProxyNodesOnReturn.value = false
  proxyNodesRefreshing.value = true
  try {
    await loadProxyNodes()
  } finally {
    proxyNodesRefreshing.value = false
  }
}

async function loadNasTab(signal?: AbortSignal) {
  const params: Record<string, string | number> = {
    page: pagination.page,
    page_size: pagination.pageSize,
    resource_type: 'nas',
  }
  if (appliedSearch.value.trim()) params.search = appliedSearch.value.trim()
  if (appliedSearch.value.trim()) params.search_field = filters.search_field === 'server' ? 'server' : 'name'
  if (filters.status) params.status = filters.status
  const [list, st] = await Promise.all([
    listSourceResources(params, { signal }),
    sourceStatistics({ signal }),
    loadProxyNodes(signal),
  ])
  rows.value = list.results
  pagination.count = list.count
  stats.value = st
}

function nasShouldRefreshCapacity(row: SourceResource) {
  if (row.resource_type !== 'nas') return false
  if (sourceNodeOnlineStatus(row) !== 'online') return false
  if (!row.bound_node && !row.bound_node_name?.trim()) return false
  if (row.id === nasTestingId.value) return false
  return row.connection_test_status === 'pending' || row.connection_test_status === 'running'
}

const NAS_CAPACITY_POLL_MS = 2000
const NAS_CAPACITY_POLL_TIMEOUT_MS = 3 * 60 * 1000

function waitForNasCapacityPoll(signal?: AbortSignal) {
  return new Promise<void>((resolve) => {
    const finish = () => {
      window.clearTimeout(timer)
      signal?.removeEventListener('abort', finish)
      resolve()
    }
    const timer = window.setTimeout(finish, NAS_CAPACITY_POLL_MS)
    signal?.addEventListener('abort', finish, { once: true })
  })
}

async function syncNasCapacities(list: SourceResource[], signal?: AbortSignal) {
  const targets = list.filter(nasShouldRefreshCapacity)
  if (!targets.length || signal?.aborted) return
  const targetIds = new Set(targets.map((row) => row.id))
  const deadline = Date.now() + NAS_CAPACITY_POLL_TIMEOUT_MS
  while (!signal?.aborted && Date.now() < deadline) {
    await waitForNasCapacityPoll(signal)
    if (signal?.aborted) return
    const params: Record<string, string | number> = {
      page: pagination.page,
      page_size: pagination.pageSize,
      resource_type: 'nas',
    }
    if (appliedSearch.value.trim()) params.search = appliedSearch.value.trim()
    if (appliedSearch.value.trim()) params.search_field = filters.search_field === 'server' ? 'server' : 'name'
    if (filters.status) params.status = filters.status
    const refreshed = await listSourceResources(params, { signal })
    rows.value = refreshed.results
    pagination.count = refreshed.count
    const pending = refreshed.results.some(
      (row) => targetIds.has(row.id) && nasShouldRefreshCapacity(row),
    )
    if (!pending) {
      stats.value = await sourceStatistics({ signal })
      return
    }
  }
}

async function load() {
  const loadingTab = activeTab.value
  const signal = pageRequests.nextSignal('source-table')
  busy.value = true
  try {
    if (loadingTab === 'hostFileSystem') {
      await loadHostTab(signal)
      return
    }
    await loadNasTab(signal)
  } catch (e) {
    if (pageRequests.isAbortError(e)) return
    pagination.page = 1
    if (activeTab.value === 'nas') {
      rows.value = []
      pagination.count = 0
      stats.value = null
      ElMessage.error({ message: apiErrorMessage(e), grouping: true })
    } else {
      rows.value = []
      hostSourceRows.value = []
      agentNodes.value = []
      pagination.count = 0
      stats.value = null
      ElMessage.error({ message: apiErrorMessage(e), grouping: true })
    }
  } finally {
    pageRequests.releaseSignal('source-table', signal)
    if (!signal.aborted) {
      sourceTabLoaded[loadingTab] = true
      busy.value = false
      if (loadingTab === 'nas' && activeTab.value === 'nas') {
        const capacitySignal = pageRequests.nextSignal('nas-capacity')
        void syncNasCapacities(rows.value.filter((row) => row.resource_type === 'nas'), capacitySignal)
          .catch(() => undefined)
          .finally(() => {
            pageRequests.releaseSignal('nas-capacity', capacitySignal)
          })
      }
    }
  }
}

async function onTest(row: SourceResource) {
  if (nasTestingId.value != null) return
  nasTestingId.value = row.id
  const startedAt = Date.now()
  let runningTitle = t('protection.sourceResources.testConnectionRunning')
  let loadingMessage = notifyInfo({
    message: runningTitle,
    duration: 0,
    dedupeKey: `source-connection-test:${row.id}`,
  })
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), SOURCE_CONNECTION_TEST_TIMEOUT_MS)
  let closedLoading = false
  const closeLoading = () => {
    if (closedLoading) return
    closedLoading = true
    loadingMessage.close()
  }
  try {
    let r = await testSourceConnection(row.id, { signal: controller.signal })
    if (!r.success && r.gatewayTimeout) {
      closeLoading()
      closedLoading = false
      runningTitle = t('protection.sourceResources.testConnectionRetrying')
      loadingMessage = notifyInfo({
        message: runningTitle,
        duration: 0,
        dedupeKey: `source-connection-test:${row.id}`,
      })
      await sleep(2500)
      r = await testSourceConnection(row.id, { signal: controller.signal })
    }
    await waitForMinTestFeedback(startedAt)
    closeLoading()
    if (r.success) {
      showConnectionTestSuccess(t('protection.sourceResources.testOk'), row.id)
    } else {
      showConnectionTestError(connectionTestFailureMessage(r), r.details ?? r)
    }
    void load()
  } catch (e) {
    await waitForMinTestFeedback(startedAt)
    closeLoading()
    if (isAbortError(e)) {
      showConnectionTestError(t('protection.sourceResources.testConnectionTimedOut'))
    } else {
      const message = apiErrorMessage(e, t('protection.sourceResources.testFail'))
      const gatewayTimeout = /gateway time/i.test(message)
      showConnectionTestError(
        gatewayTimeout
          ? t('protection.sourceResources.testConnectionGatewayTimeout')
          : message,
        undefined,
        e,
      )
    }
  } finally {
    window.clearTimeout(timeoutId)
    nasTestingId.value = null
    closeLoading()
  }
}

async function onTestSelectedNas() {
  if (selectedNas.value.length !== 1) return
  await onTest(selectedNas.value[0])
}




async function deleteSelectedNasRows(rowsToDelete: SourceResource[]) {
  if (rowsToDelete.length === 0) return
  await openNasDeleteDialog(rowsToDelete)
}

function sourceConfigValue(row: SourceResource, key: string) {
  const value = row.config?.[key]
  return typeof value === 'string' ? value : ''
}

function boundNodeForRow(row: SourceResource) {
  const nodes = row.resource_type === 'local' ? agentNodes.value : proxyNodes.value
  return nodes.find((node) =>
    node.id === row.bound_node || (!!row.bound_node_name && node.name === row.bound_node_name),
  )
}


const osPlatformLabels = computed((): NodeOsPlatformLabels => ({
  linux: t('protection.sourceResources.osPlatformLinux'),
  windows: t('protection.sourceResources.osPlatformWindows'),
  macos: t('protection.sourceResources.osPlatformMacos'),
}))



function agentEnrollmentOs(node: ApiNode): EnrollmentOs {
  return nodeEnrollmentOs(node)
}

function agentPlatformLabel(node: ApiNode) {
  return nodePlatformLabel(agentEnrollmentOs(node), osPlatformLabels.value)
}



function agentInstalledVersion(node: ApiNode) {
  const source = localSourceByNodeId.value.get(node.id)
  const fromSource = source ? sourceConfigValue(source, 'agent_version') : ''
  return (node.version || fromSource || '').trim()
}

function agentVersion(node: ApiNode) {
  const version = agentInstalledVersion(node)
  return version || '—'
}



function agentCanUpgrade(node: ApiNode) {
  return node.agent_release?.upgrade_version_allowed
    ?? canRemoteAgentUpgrade(agentInstalledVersion(node), latestAgentVersion.value)
}

function agentOnlineLabel(node: ApiNode) {
  const status = debouncedNodeStatus(node)
  if (status === 'online') return t('protection.sourceResources.nodeStatusOnline')
  if (status === 'reconnecting') return t('protection.sourceResources.nodeStatusReconnecting')
  return t('protection.sourceResources.nodeStatusOffline')
}

function agentOnlineTagType(node: ApiNode): 'success' | 'info' | 'danger' {
  const status = debouncedNodeStatus(node)
  if (status === 'online') return 'success'
  if (status === 'reconnecting') return 'info'
  return 'danger'
}

function openHostRowDetail(node: ApiNode) {
  hostDetailNodeId.value = node.id
  hostDetailSource.value = localSourceByNodeId.value.get(node.id) ?? null
  hostDetailInitialTab.value = 'basic'
  hostDetailOpen.value = true
}

function openSelectedHostMaintenance() {
  const node = selectedHostAgents.value[0]
  if (!node || selectedHostAgents.value.length !== 1) return
  hostDetailNodeId.value = node.id
  hostDetailSource.value = localSourceByNodeId.value.get(node.id) ?? null
  hostDetailInitialTab.value = 'maintenance'
  hostDetailOpen.value = true
}

function clearOpenNodeQuery() {
  if (route.query.openNode == null) return
  const rest = { ...route.query }
  delete rest.openNode
  void router.replace({ path: route.path, query: rest })
}

function onHostDetailDrawerClose(open: boolean) {
  hostDetailOpen.value = open
  if (!open) clearOpenNodeQuery()
}

function onHostDetailDrawerSaved() {
  void load()
}

function tryOpenHostFromQuery() {
  const raw = route.query.openNode
  if (raw == null || Array.isArray(raw)) return
  const id = Number(raw)
  if (!Number.isFinite(id)) return
  if (activeTab.value !== 'hostFileSystem') activeTab.value = 'hostFileSystem'
  hostDetailNodeId.value = id
  hostDetailSource.value = localSourceByNodeId.value.get(id) ?? null
  hostDetailInitialTab.value = 'basic'
  hostDetailOpen.value = true
}

function sourceNodeName(row: SourceResource) {
  return row.bound_node_name || boundNodeForRow(row)?.name || '—'
}

function sourceNodeIp(row: SourceResource) {
  return proxyNodeStackIpLine(boundNodeForRow(row), sourceConfigValue(row, 'host_ip')) || '—'
}

function sourceNodeOnlineStatus(row: SourceResource): 'online' | 'reconnecting' | 'offline' {
  const explicit = (row.bound_node_availability || '').trim().toLowerCase()
  if (explicit === 'online' || explicit === 'reconnecting' || explicit === 'offline') {
    return explicit
  }
  const node = boundNodeForRow(row)
  if (node) {
    return debouncedNodeStatus(node)
  }
  return 'offline'
}

function sourceNodeOnlineLabel(row: SourceResource) {
  const pending = resolveSourcePendingStatus(nasSelectableId(row))
  if (pending) return pending.label
  const presentation = sourceNasStatusPresentation(row, sourceNodeOnlineStatus(row))
  return t(presentation.labelKey)
}

function sourceNodeOnlineTagType(row: SourceResource): 'success' | 'warning' | 'info' | 'danger' {
  const pending = resolveSourcePendingStatus(nasSelectableId(row))
  if (pending) return pending.tag
  return sourceNasStatusPresentation(row, sourceNodeOnlineStatus(row)).tone
}

function sourceRegisteredAt(row: SourceResource) {
  const node = boundNodeForRow(row)
  return node?.created_at || row.created_at || row.updated_at || null
}

function nasProtocolType(row: SourceResource): SourceProtocol {
  const explicit = sourceConfigValue(row, 'protocol').toLowerCase()
  if (explicit === 'smb' || explicit === 'nfs') return explicit
  const detected = nasMountProtocol(row)
  if (detected) return detected
  return 'nas'
}

function nasProtocolLabel(row: SourceResource) {
  const protocol = nasProtocolType(row)
  if (protocol === 'smb') return t('repositoriesPage.protocolSmb')
  if (protocol === 'nfs') return t('repositoriesPage.protocolNfs')
  return 'NAS'
}


function nasSourceProxyName(row: SourceResource) {
  const node = boundNodeForRow(row)
  const name = node?.name?.trim() || row.bound_node_name?.trim() || sourceNodeName(row)
  if (!name || name === '—') return ''
  return proxyNodeStackPrimaryLine(name, node, row.bound_node)
}

function nasSourceProxyIp(row: SourceResource) {
  const node = boundNodeForRow(row)
  return proxyNodeStackIpLine(node, sourceNodeIp(row) !== '—' ? sourceNodeIp(row) : '')
}

function sourceNeedsProxyRebind(row: SourceResource) {
  return String(row.status_message || '').trim().toLowerCase() === 'needs_proxy'
}

function openRowDetail(row: SourceResource) {
  openDetail(row)
}


async function onUpgradeSelectedHosts() {
  const targets = selectedHostAgents.value.filter((node) =>
    lifecycleOps.canUpgradeNode(node, agentCanUpgrade),
  )
  if (targets.length === 0) {
    ElMessage.warning({ message: t('nodeLifecycle.nothingEligible'), grouping: true })
    return
  }
  const started = await lifecycleOps.runBatch('upgrade', targets)
  if (started) {
    clearHostTableSelection()
  }
}

async function onRemoveSelectedHosts() {
  const targets = [...selectedHostAgents.value]
  if (!targets.length) return
  await openHostDeleteDialog(targets)
}

async function renameSourceRow(row: SourceResource, name: string) {
  await updateSourceResource(row.id, { name })
}

/* ========== Host Rename (selection + more actions) ========== */
const selectedHostAgents = ref<ApiNode[]>([])
const selectedNas = ref<SourceResource[]>([])
const nasTestingId = ref<number | null>(null)

function connectionTestFailureMessage(result: { message?: string; gatewayTimeout?: boolean }): string {
  if (result.gatewayTimeout) {
    return t('protection.sourceResources.testConnectionGatewayTimeout')
  }
  return result.message || t('protection.sourceResources.testFail')
}

function sleep(ms: number) {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, ms)
  })
}

async function waitForMinTestFeedback(startedAt: number) {
  const elapsed = Date.now() - startedAt
  const remain = SOURCE_CONNECTION_TEST_MIN_FEEDBACK_MS - elapsed
  if (remain > 0) await sleep(remain)
}

function showConnectionTestSuccess(message: string, sourceId: number) {
  notifySuccess({
    message,
    duration: SOURCE_CONNECTION_TEST_RESULT_TOAST_MS,
    dedupeKey: `source-connection-test-result:success:${sourceId}`,
  })
}

function showConnectionTestError(message: string, rawDetail?: unknown, error?: unknown) {
  const title = t('protection.sourceResources.testFail')
  if (error !== undefined) {
    openErrorDetails({
      error,
      overrides: { title, summary: message, issue: message, rawDetail },
    })
    return
  }
  openErrorDetails({ title, summary: message, issue: message, rawDetail })
}
const hostMoreActionsOpen = ref(false)
const nasMoreActionsOpen = ref(false)
const backupSourceDeleteDialogOpen = ref(false)
const backupSourceDeleteIds = ref<string[]>([])
const pendingSourceOps = ref(new Map<string, SourcePendingOp>())
const SOURCE_DELETE_TASK_POLL_MS = 2000
const SOURCE_DELETE_BLOCKED_TASK_POLL_MS = 30_000
const SOURCE_DELETE_TASK_TIMEOUT_MS = 15 * 60 * 1000
let sourceDeleteTaskPollTimer: number | null = null
let sourceDeleteTaskPollInFlight = false

function refreshPendingSourceOps() {
  pendingSourceOps.value = readWizardPendingSourceOps()
}

function stopPendingSourceDeletePoll() {
  if (sourceDeleteTaskPollTimer != null) {
    window.clearTimeout(sourceDeleteTaskPollTimer)
    sourceDeleteTaskPollTimer = null
  }
}

function sourceDeleteTaskNeedsReconcile(op: SourcePendingOp) {
  return Boolean(
    op.taskUuid
    && (
      ['deleting', 'delete_waiting', 'delete_blocked'].includes(op.kind)
      || (op.kind === 'delete_failed' && !op.failureDetails)
    ),
  )
}

function schedulePendingSourceDeletePoll(delay = SOURCE_DELETE_TASK_POLL_MS) {
  stopPendingSourceDeletePoll()
  const hasPendingTask = [...pendingSourceOps.value.values()].some(
    sourceDeleteTaskNeedsReconcile,
  )
  if (!hasPendingTask) return
  sourceDeleteTaskPollTimer = window.setTimeout(() => {
    sourceDeleteTaskPollTimer = null
    void reconcilePendingSourceDeleteTasks()
  }, delay)
}

function sourcePendingDisplayName(sourceId: string) {
  if (sourceId.startsWith('agent:')) {
    const nodeId = Number(sourceId.slice('agent:'.length))
    return agentNodes.value.find((node) => node.id === nodeId)?.name || sourceId
  }
  if (sourceId.startsWith('nas:')) {
    const resourceId = Number(sourceId.slice('nas:'.length))
    return nasRows.value.find((row) => row.id === resourceId)?.name || sourceId
  }
  return sourceId
}

function markSourceDeleteFailed(
  sourceId: string,
  op: SourcePendingOp,
  details: ErrorDetailsPayload,
) {
  markWizardPendingBySourceIds([sourceId], {
    ...op,
    kind: 'delete_failed',
    errorMessage: unregisterFailureSummaryLine(details),
    failureDetails: details,
  })
}

function openSourcePendingFailureDetails(sourceId: string) {
  const op = pendingSourceOps.value.get(sourceId)
  if (op?.kind !== 'delete_failed') {
    if (op?.taskUuid) {
      void router.push({ path: '/ops/tasks', query: { taskUuid: op.taskUuid } })
    }
    return
  }
  if (op.failureDetails) {
    openUnregisterFailureDetails(op.failureDetails)
    return
  }
  openUnregisterFailureDetails(unregisterFailureToErrorDetails({
    t,
    sourceId,
    sourceName: sourcePendingDisplayName(sourceId),
    fallbackMessage: op.errorMessage || t('protection.backupsPage.msgDeleteSourceFailed'),
  }))
}

async function reconcilePendingSourceDeleteTasks() {
  if (sourceDeleteTaskPollInFlight) return
  refreshPendingSourceOps()
  const pending = [...pendingSourceOps.value.entries()].flatMap(([sourceId, op]) =>
    sourceDeleteTaskNeedsReconcile(op) && op.taskUuid
      ? [{ sourceId, op }]
      : [],
  )
  if (!pending.length) return
  sourceDeleteTaskPollInFlight = true
  let changed = false
  const failureNotices: Array<{ sourceId: string; sourceName: string; details: ErrorDetailsPayload }> = []
  const cleanupNotices: Array<{ sourceId: string; sourceName: string; details: ErrorDetailsPayload }> = []
  try {
    const tasks = await Promise.allSettled(pending.map(({ op }) => getTask(op.taskUuid!)))
    const now = Date.now()
    tasks.forEach((settled, index) => {
      const { sourceId, op } = pending[index]
      const sourceName = sourcePendingDisplayName(sourceId)
      if (settled.status === 'rejected') {
        if (op.startedAt && now - op.startedAt >= SOURCE_DELETE_TASK_TIMEOUT_MS) {
          const details = unregisterFailureToErrorDetails({
            t,
            sourceId,
            sourceName,
            fallbackMessage: t('protection.backupsPage.msgDeleteSourceFailed'),
          })
          markSourceDeleteFailed(sourceId, op, details)
          failureNotices.push({ sourceId, sourceName, details })
          changed = true
        }
        return
      }
      const outcome = sourceUnregisterTaskOutcome(settled.value)
      if (['pending', 'running', 'waiting', 'blocked'].includes(outcome.status)) {
        const nextKind = sourceUnregisterPendingKind(outcome.status)
        if (op.kind !== nextKind) {
          markWizardPendingBySourceIds([sourceId], { ...op, kind: nextKind })
          changed = true
        }
      } else if (outcome.status === 'cancelled') {
        clearWizardPendingBySourceIds([sourceId])
        changed = true
      } else if (outcome.success) {
        if (outcome.partialSuccess) {
          cleanupNotices.push({
            sourceId,
            sourceName,
            details: unregisterFailureToErrorDetails({
              t,
              sourceId,
              sourceName,
              task: settled.value,
              outcome,
              partialSuccess: true,
            }),
          })
        }
        clearWizardPendingBySourceIds([sourceId])
        changed = true
      } else if (outcome.terminal) {
        const details = unregisterFailureToErrorDetails({
          t,
          sourceId,
          sourceName,
          task: settled.value,
          outcome,
        })
        markSourceDeleteFailed(sourceId, op, details)
        failureNotices.push({ sourceId, sourceName, details })
        changed = true
      }
    })
    notifyUnregisterFailureBatch({
      t,
      items: failureNotices,
      dedupeKey: `unregister-failure-batch:${failureNotices.map((item) => item.sourceId).join(',')}`,
    })
    notifyUnregisterCleanupWarningBatch({
      t,
      items: cleanupNotices,
      dedupeKey: `unregister-cleanup-warning-batch:${cleanupNotices.map((item) => item.sourceId).join(',')}`,
    })
    refreshPendingSourceOps()
    if (changed) await load()
  } finally {
    sourceDeleteTaskPollInFlight = false
    refreshPendingSourceOps()
    const trackedDeleteOps = [...pendingSourceOps.value.values()].filter(
      sourceDeleteTaskNeedsReconcile,
    )
    const onlyBlocked = trackedDeleteOps.length > 0
      && trackedDeleteOps.every((op) => op.kind === 'delete_blocked')
    schedulePendingSourceDeletePoll(
      onlyBlocked ? SOURCE_DELETE_BLOCKED_TASK_POLL_MS : SOURCE_DELETE_TASK_POLL_MS,
    )
  }
}

type SourcePendingDisplayStatus = {
  label: string
  tag: 'success' | 'warning' | 'danger' | 'info'
  spinning?: boolean
  clickable?: boolean
}

function resolveSourcePendingStatus(selectableId: string): SourcePendingDisplayStatus | null {
  const op = pendingSourceOps.value.get(selectableId)
  if (!op) return null
  if (op.kind === 'deleting') {
    return {
      label: t('protection.backupsPage.sourcePendingDeleting'),
      tag: 'info',
      spinning: true,
    }
  }
  if (op.kind === 'delete_waiting') {
    return {
      label: t('protection.backupsPage.sourcePendingDeleteWaiting'),
      tag: 'warning',
      spinning: true,
      clickable: true,
    }
  }
  if (op.kind === 'delete_blocked') {
    return {
      label: t('protection.backupsPage.sourcePendingDeleteBlocked'),
      tag: 'warning',
      clickable: true,
    }
  }
  if (op.kind === 'delete_failed') {
    return {
      label: t('protection.backupsPage.sourcePendingDeleteFailed'),
      tag: 'danger',
      clickable: true,
    }
  }
  return null
}

const backupSourceDeletePreviousFailure = computed(() =>
  previousUnregisterFailureDetails(
    t,
    backupSourceDeleteIds.value.flatMap((sourceId) => {
      const op = pendingSourceOps.value.get(sourceId)
      return op?.kind === 'delete_failed' && op.failureDetails ? [op.failureDetails] : []
    }),
  ),
)

type BackupSourceDeleteDisplayRow = {
  id: string
  name: string
  type?: string
  sourceType?: 'host' | 'nas'
  platform?: EnrollmentOs
  protocol?: 'nfs' | 'smb'
  statusLabel?: string
  statusTag?: 'success' | 'warning' | 'danger' | 'info'
  registeredAt?: string
  snapshotCount?: string | number
}
const backupSourceDeleteRows = ref<BackupSourceDeleteDisplayRow[]>([])
const unregisterStep3BlockedDialogOpen = ref(false)
const unregisterStep3BlockedRows = ref<BackupSourceDeleteDisplayRow[]>([])

function hostSelectableId(node: ApiNode): string {
  return `agent:${node.id}`
}

function nasSelectableId(row: SourceResource): string {
  return `nas:${row.id}`
}

function sourceDeleteInFlight(selectableId: string): boolean {
  return pendingSourceOps.value.get(selectableId)?.kind === 'deleting'
}

function hostDeleteDisplayRow(node: ApiNode): BackupSourceDeleteDisplayRow {
  const lifecycle = resolveBackupSourceLifecycleStatus(node)
  const lifecycleLabel = te(lifecycle.labelKey) ? t(lifecycle.labelKey) : lifecycle.labelKey
  return {
    id: hostSelectableId(node),
    name: node.name,
    type: t('protection.sourceResources.sourceTypeHost'),
    sourceType: 'host',
    platform: agentEnrollmentOs(node),
    statusLabel: lifecycleLabel || agentOnlineLabel(node),
    statusTag: lifecycleLabel ? lifecycle.tagType : agentOnlineTagType(node),
    registeredAt: formatDate(node.created_at),
  }
}

function nasDeleteDisplayRow(row: SourceResource): BackupSourceDeleteDisplayRow {
  const protocol = nasProtocolType(row)
  return {
    id: nasSelectableId(row),
    name: row.name,
    type: row.resource_type_display || typeLabel(row.resource_type),
    sourceType: 'nas',
    protocol: protocol === 'smb' || protocol === 'nfs' ? protocol : undefined,
    statusLabel: sourceNodeOnlineLabel(row),
    statusTag: sourceNodeOnlineTagType(row),
    registeredAt: formatDate(sourceRegisteredAt(row)),
  }
}





function closeUnregisterStep3BlockedDialog() {
  unregisterStep3BlockedDialogOpen.value = false
}

function goToStartBackupForUnregister() {
  unregisterStep3BlockedDialogOpen.value = false
  void router.push({ path: '/protection/backups', query: { step: 'start-backup' } })
}


async function openHostDeleteDialog(nodes: ApiNode[]) {
  if (!nodes.length) return
  if (nodes.some((node) => sourceDeleteInFlight(hostSelectableId(node)))) return
  backupSourceDeleteIds.value = nodes.map((node) => hostSelectableId(node))
  backupSourceDeleteRows.value = nodes.map(hostDeleteDisplayRow)
  backupSourceDeleteDialogOpen.value = true
}

async function openNasDeleteDialog(rows: SourceResource[]) {
  if (!rows.length) return
  if (rows.some((row) => sourceDeleteInFlight(nasSelectableId(row)))) return
  backupSourceDeleteIds.value = rows.map((row) => nasSelectableId(row))
  backupSourceDeleteRows.value = rows.map(nasDeleteDisplayRow)
  backupSourceDeleteDialogOpen.value = true
}

async function onBackupSourcesDeleted(payload: {
  result: string
  warnings: Array<Record<string, unknown>>
  pending_removals?: Array<{ source_id: string; node_id: number; task_id?: string | null; state?: string }>
  task_id?: number
  task_uuid?: string
  task_ids?: number[]
  task_uuids?: string[]
  tasks?: Array<{ source_id: string; task_id: number; task_uuid: string }>
  rejected?: BackupSourceDeleteResult['rejected']
  accepted?: boolean
}) {
  const deletingIds = new Set(backupSourceDeleteIds.value)
  backupSourceDeleteRows.value = []
  if (
    (payload.accepted && payload.result === 'pending')
    || ['failed', 'partial_failure'].includes(payload.result)
  ) {
    const sourceIds = [...deletingIds]
    const bindings = sourceUnregisterTaskBindings(sourceIds, payload)
    const boundSourceIds = new Set(bindings.map((binding) => binding.sourceId))
    const rejectedBySourceId = new Map(
      (payload.rejected || []).map((item) => [item.source_id, item]),
    )
    bindings.forEach((binding) => {
      markWizardPendingBySourceIds([binding.sourceId], {
        kind: sourceUnregisterPendingKind(binding.status),
        taskId: binding.taskId,
        taskUuid: binding.taskUuid,
        startedAt: Date.now(),
      })
    })
    const unboundNotices: Array<{ sourceId: string; sourceName: string; details: ErrorDetailsPayload }> = []
    sourceIds
      .filter((sourceId) => !boundSourceIds.has(sourceId))
      .forEach((sourceId) => {
        const sourceName = sourcePendingDisplayName(sourceId)
        const rejection = rejectedBySourceId.get(sourceId)
        const details = unregisterFailureToErrorDetails({
          t,
          sourceId,
          sourceName,
          apiError: rejection
            ? { message: t('protection.backupsPage.msgDeleteSourceFailed'), reasons: rejection.reasons }
            : undefined,
          fallbackMessage: t('protection.backupsPage.msgDeleteSourceFailed'),
        })
        markSourceDeleteFailed(sourceId, { kind: 'delete_failed' }, details)
        unboundNotices.push({ sourceId, sourceName, details })
      })
    notifyUnregisterFailureBatch({
      t,
      items: unboundNotices,
      dedupeKey: `unregister-unbound-batch:${unboundNotices.map((item) => item.sourceId).join(',')}`,
    })
    refreshPendingSourceOps()
    schedulePendingSourceDeletePoll(0)
    backupSourceDeleteIds.value = []
    await load()
    if (payload.result === 'pending') {
      ElMessage.info({ message: t('protection.backupsPage.msgDeleteSourcePending'), grouping: true })
    }
    return
  }
  const rejectedBySourceId = new Map(
    (payload.rejected || []).map((item) => [item.source_id, item]),
  )
  const rejectedNotices = [...rejectedBySourceId].map(([sourceId, rejection]) => {
    const sourceName = sourcePendingDisplayName(sourceId)
    const details = unregisterFailureToErrorDetails({
      t,
      sourceId,
      sourceName,
      apiError: {
        message: t('protection.backupsPage.msgDeleteSourceFailed'),
        reasons: rejection.reasons,
      },
    })
    markSourceDeleteFailed(sourceId, { kind: 'delete_failed' }, details)
    return { sourceId, sourceName, details }
  })
  clearWizardPendingBySourceIds(
    [...deletingIds].filter((sourceId) => !rejectedBySourceId.has(sourceId)),
  )
  notifyUnregisterFailureBatch({
    t,
    items: rejectedNotices,
    dedupeKey: `unregister-rejected-batch:${rejectedNotices.map((item) => item.sourceId).join(',')}`,
  })
  refreshPendingSourceOps()
  const hadHosts = backupSourceDeleteIds.value.some((id) => id.startsWith('agent:'))
  const hadNas = backupSourceDeleteIds.value.some((id) => id.startsWith('nas:'))
  if (payload.pending_removals?.length) {
    lifecycleOps.trackPendingRemovals(
      payload.pending_removals.map((row) => ({
        nodeId: row.node_id,
        taskId: row.task_id,
        state: row.state,
      })),
      agentNodes.value.filter((node) =>
        payload.pending_removals!.some((row) => row.node_id === node.id),
      ),
    )
  }
  if (hadNas) clearNasTableSelection()
  if (hadHosts) clearHostTableSelection()
  await load()
  if (payload.pending_removals?.length) {
    ElMessage.info({ message: t('protection.backupsPage.msgDeleteSourcePending'), grouping: true })
  } else if (payload.result === 'partial_success' && payload.warnings.length) {
    const sourceIds = [...deletingIds].filter((sourceId) => !rejectedBySourceId.has(sourceId))
    const items = sourceIds.map((sourceId) => {
      const sourceName = sourcePendingDisplayName(sourceId)
      return {
        sourceId,
        sourceName,
        details: unregisterFailureToErrorDetails({
          t,
          sourceId,
          sourceName,
          partialSuccess: true,
          warnings: warningsForUnregisterSource(payload.warnings, sourceId),
        }),
      }
    })
    if (!items.length) {
      notifyUnregisterCleanupWarning({
        t,
        warnings: payload.warnings,
        dedupeKey: 'unregister-sync-cleanup-warning:bulk',
      })
    } else {
      notifyUnregisterCleanupWarningBatch({
        t,
        items,
        dedupeKey: `unregister-sync-cleanup-warning:${sourceIds.join(',')}`,
      })
    }
  } else if (!rejectedNotices.length) {
    ElMessage.success({ message: t('protection.backupsPage.msgDeleteSourceSuccess'), grouping: true })
  }
}
const renameDialogOpen = ref(false)
const renameInput = ref('')
const renameTarget = ref<SourceResource | null>(null)
const renameTargetNode = ref<ApiNode | null>(null)
const rebindDialogOpen = ref(false)
const rebindNodeId = ref<number | undefined>(undefined)
const nasRebindBusy = ref(false)

function normalizeProxyNodeId(value: unknown): number | undefined {
  const id = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(id) && id > 0 ? id : undefined
}

function nasBoundNodeId(row: SourceResource): number | undefined {
  return normalizeProxyNodeId(row.bound_node)
}

const onlineProxyNodes = computed(() => proxyNodes.value.filter((n) => n.availability === 'online'))
const offlineProxyNodeCount = computed(() => proxyNodes.value.filter((n) => n.availability !== 'online').length)

function sharedNasBoundNodeId(): number | undefined {
  if (selectedNas.value.length === 0) return undefined
  const ids = selectedNas.value.map((r) => nasBoundNodeId(r)).filter((id): id is number => id != null)
  if (ids.length !== selectedNas.value.length) return undefined
  const first = ids[0]
  return ids.every((id) => id === first) ? first : undefined
}

function onHostSelectionChange(selection: ApiNode[]) {
  selectedHostAgents.value = selection
}

function onNasSelectionChange(selection: SourceResource[]) {
  selectedNas.value = selection
}

function clearHostTableSelection() {
  selectedHostAgents.value = []
  hostTableRef.value?.clearSelection()
}

function clearNasTableSelection() {
  selectedNas.value = []
  nasTableRef.value?.clearSelection()
}

const hostBatchDisabled = computed(() => selectedHostAgents.value.length === 0)
const hostDeleteDisabled = computed(
  () =>
    hostBatchDisabled.value
    || selectedHostAgents.value.some((node) => sourceDeleteInFlight(hostSelectableId(node))),
)
const hostRenameDisabled = computed(() => selectedHostAgents.value.length !== 1)
const hostUpgradeDisabled = computed(() => {
  const upgradable = selectedHostAgents.value.filter((node) =>
    lifecycleOps.canUpgradeNode(node, agentCanUpgrade),
  )
  return upgradable.length === 0
})
const nasBatchDisabled = computed(() => selectedNas.value.length === 0)
const nasDeleteDisabled = computed(
  () =>
    nasBatchDisabled.value
    || selectedNas.value.some((row) => sourceDeleteInFlight(nasSelectableId(row))),
)
const nasRenameDisabled = computed(() => selectedNas.value.length !== 1)
const nasTestDisabled = computed(() => selectedNas.value.length !== 1 || nasTestingId.value != null)

const tabSelectedCount = computed(() =>
  activeTab.value === 'hostFileSystem' ? selectedHostAgents.value.length : selectedNas.value.length,
)

function openHostRenameDialog() {
  if (hostRenameDisabled.value) return
  const node = selectedHostAgents.value[0]
  if (!node) return
  renameTarget.value = null
  renameTargetNode.value = node
  renameInput.value = node.name
  renameDialogOpen.value = true
}

function openNasRenameDialog() {
  if (nasRenameDisabled.value) return
  const row = selectedNas.value[0]
  if (!row) return
  renameTargetNode.value = null
  renameTarget.value = row
  renameInput.value = row.name
  renameDialogOpen.value = true
}

function closeRenameDialog() {
  renameDialogOpen.value = false
  renameTarget.value = null
  renameTargetNode.value = null
}

async function submitRename() {
  const name = renameInput.value.trim()
  if (!name) {
    ElMessage.warning({ message: t('protection.sourceResources.renamePlaceholder'), grouping: true })
    return
  }

  const wasHostRename = !!renameTargetNode.value

  try {
    if (renameTargetNode.value) {
      await updateNode(renameTargetNode.value.id, { name })
    } else if (renameTarget.value) {
      await renameSourceRow(renameTarget.value, name)
    } else {
      return
    }
  } catch {
    ElMessage.error({ message: t('protection.sourceResources.renameFailed'), grouping: true })
    return
  }

  ElMessage.success({ message: t('protection.sourceResources.renameSuccess'), grouping: true })
  closeRenameDialog()
  if (wasHostRename) {
    clearHostTableSelection()
  } else {
    clearNasTableSelection()
  }
  await load()
}

function openNasRebindDialog() {
  if (nasBatchDisabled.value) return
  const shared = sharedNasBoundNodeId()
  const preset = shared != null ? proxyNodes.value.find((n) => n.id === shared) : undefined
  rebindNodeId.value = preset?.availability === 'online' ? shared : undefined
  rebindDialogOpen.value = true
}

watch(rebindDialogOpen, (open) => {
  if (!open) return
  void (async () => {
    proxyNodesRefreshing.value = true
    try {
      await loadProxyNodes()
      const shared = sharedNasBoundNodeId()
      if (rebindNodeId.value == null && shared != null) {
        const node = proxyNodes.value.find((n) => n.id === shared)
        if (node?.availability === 'online') rebindNodeId.value = shared
      }
    } finally {
      proxyNodesRefreshing.value = false
    }
  })()
})

async function submitNasRebind() {
  if (nasRebindBusy.value) return

  const nodeId = normalizeProxyNodeId(rebindNodeId.value)
  if (!nodeId) {
    ElMessage.warning({ message: t('protection.sourceResources.selectSourceProxy'), grouping: true })
    return
  }
  const target = proxyNodes.value.find((n) => n.id === nodeId)
  if (!target || target.availability !== 'online') {
    ElMessage.warning({ message: t('protection.sourceResources.rebindProxyNodeOffline'), grouping: true })
    return
  }

  const rowsToUpdate = selectedNas.value.filter((row) => nasBoundNodeId(row) !== nodeId)
  if (rowsToUpdate.length === 0) {
    ElMessage.warning({ message: t('protection.sourceResources.rebindSameProxy'), grouping: true })
    rebindDialogOpen.value = false
    return
  }

  const rows = [...rowsToUpdate]
  rebindDialogOpen.value = false
  clearNasTableSelection()

  nasRebindBusy.value = true
  try {
    for (const row of rows) {
      await updateSourceResource(row.id, { bound_node_id: nodeId })
    }
    ElMessage.success({ message: t('protection.sourceResources.rebindQueued'), grouping: true })
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('protection.sourceResources.rebindFailed')), grouping: true })
  } finally {
    nasRebindBusy.value = false
    void load()
  }
}

/* ========== Agent Deploy Fullscreen (Host File System) ========== */
const agentDeployOpen = ref(false)
const agentDeployShellRef = ref<HTMLElement | null>(null)
const deployOrgKey = ref('')
const deploySelectedOs = ref<EnrollmentOs>('linux')

function startDeploySession() {
  deployOrgKey.value = getEffectiveOrgKey()
}

function openAgentDeploy() {
  agentDeployOpen.value = true
  deploySelectedOs.value = 'linux'
  void nextTick(() => agentDeployShellRef.value?.focus())
}

function closeAgentDeploy() {
  agentDeployOpen.value = false
  void refreshListAfterAddReturn()
}

async function refreshListAfterAddReturn() {
  if (activeTab.value === 'hostFileSystem') {
    await loadHostTab()
    return
  }
  await load()
}

async function copyDeployScript(text?: string) {
  if (!text) {
    ElMessage.warning({ message: t('nodesDeploy.scriptNotReady'), grouping: true })
    return
  }
  try {
    await copyTextToClipboard(text)
    ElMessage.success({ message: t('nodesDeploy.copied'), grouping: true })
  } catch {
    ElMessage.error({ message: t('nodesDeploy.copyFailed'), grouping: true })
  }
}

watch(agentDeployOpen, (open) => {
  if (typeof document === 'undefined') return
  document.body.style.overflow = open ? 'hidden' : ''
  if (open) {
    startDeploySession()
  }
})

/* ========== NAS Add Fullscreen ========== */
const nasAddOpen = ref(false)
const nasAddShellRef = ref<HTMLElement | null>(null)
const nasAddFormRef = ref<InstanceType<typeof NasAddForm> | null>(null)
type NasProtocol = 'smb' | 'nfs'
const nasProtocol = ref<NasProtocol>('smb')
const nasBindNodeId = ref<number | undefined>(undefined)
const nasBindNodeError = ref('')
const nasName = ref('')
const nasNameTouched = ref(false)
const existingNasNamesForAdd = ref<string[]>([])
const nasDir = ref('')
const nasDirTouched = ref(false)

/* SMB */
const nasSmbServer = ref('')
const nasSmbShare = ref('')
const nasSmbUsername = ref('')
const nasSmbPassword = ref('')
const nasSmbDomain = ref('')

/* NFS */
const nasNfsHost = ref('')
const nasNfsExport = ref('')
const nasNfsOptions = ref(defaultNasMountOptions('smb'))

const nasBusy = ref(false)

const generatedNasDir = computed(() =>
  buildGeneratedNasMountDir({
    protocol: nasProtocol.value,
    smbServer: nasSmbServer.value,
    smbShare: nasSmbShare.value,
    nfsHost: nasNfsHost.value,
    nfsExport: nasNfsExport.value,
  }),
)

const generatedNasName = computed(() =>
  buildGeneratedNasName({
    protocol: nasProtocol.value,
    smbServer: nasSmbServer.value,
    smbShare: nasSmbShare.value,
    nfsHost: nasNfsHost.value,
    nfsExport: nasNfsExport.value,
  }),
)

const nasEffectiveName = computed(() =>
  resolveNasSubmitName(nasName.value, generatedNasName.value, nasNameTouched.value),
)

const nasNameConflictMessage = computed(() => {
  const candidate = nasEffectiveName.value
  if (!candidate) return ''
  if (!hasNasSourceNameConflict(existingNasNamesForAdd.value, candidate)) return ''
  return t('protection.sourceResources.nasWarnNameExists', { name: candidate })
})

async function loadExistingNasNamesForAdd() {
  try {
    const page = await listSourceResources({ resource_type: 'nas', page_size: 500 })
    existingNasNamesForAdd.value = page.results.map((row) => row.name)
  } catch {
    existingNasNamesForAdd.value = nasRows.value.map((row) => row.name)
  }
}

function clearNasBindNodeError() {
  nasBindNodeError.value = ''
}

function validateNasBindNode(): boolean {
  clearNasBindNodeError()
  if (proxyNodes.value.length === 0) {
    nasBindNodeError.value = t('protection.sourceResources.nasNoProxy')
    ElMessage.warning({ message: t('protection.sourceResources.nasNoProxy'), grouping: true })
    nasAddFormRef.value?.scrollToBindNode()
    return false
  }
  if (onlineProxyNodes.value.length === 0) {
    nasBindNodeError.value = t('protection.sourceResources.rebindProxyNoOnline')
    ElMessage.warning({ message: t('protection.sourceResources.rebindProxyNoOnline'), grouping: true })
    nasAddFormRef.value?.scrollToBindNode()
    return false
  }
  if (nasBindNodeId.value == null) {
    nasBindNodeError.value = t('protection.sourceResources.errNasProxyRequired')
    ElMessage.warning({ message: t('protection.sourceResources.errNasProxyRequired'), grouping: true })
    nasAddFormRef.value?.scrollToBindNode()
    return false
  }
  return true
}

function openNasAdd() {
  nasAddOpen.value = true
  nasProtocol.value = 'smb'
  nasBindNodeId.value = undefined
  nasBindNodeError.value = ''
  nasName.value = ''
  nasNameTouched.value = false
  nasDir.value = ''
  nasDirTouched.value = false
  nasSmbServer.value = ''
  nasSmbShare.value = ''
  nasSmbUsername.value = ''
  nasSmbPassword.value = ''
  nasSmbDomain.value = ''
  nasNfsHost.value = ''
  nasNfsExport.value = ''
  nasNfsOptions.value = defaultNasMountOptions('smb')
  void loadProxyNodes()
  void loadExistingNasNamesForAdd()
  void nextTick(() => nasAddShellRef.value?.focus())
}

function closeNasAdd() {
  nasAddOpen.value = false
  void refreshListAfterAddReturn()
}

type NasWizardStep = 0 | 1 | 2

function validateNasStep(step: NasWizardStep): boolean {
  if (step === 0) {
    if (!nasProtocol.value) {
      ElMessage.warning({ message: t('repositoriesPage.errProtocol'), grouping: true })
      return false
    }
    if (nasProtocol.value === 'smb') {
      if (!nasSmbServer.value.trim()) { ElMessage.warning({ message: t('addNasRepo.errSmbHost'), grouping: true }); return false }
      if (!nasSmbShare.value.trim()) { ElMessage.warning({ message: t('addNasRepo.errSmbShare'), grouping: true }); return false }
      if (!nasSmbUsername.value.trim()) { ElMessage.warning({ message: t('repositoriesPage.errSmbUsername'), grouping: true }); return false }
      if (!nasSmbPassword.value.trim()) { ElMessage.warning({ message: t('repositoriesPage.errSmbPassword'), grouping: true }); return false }
    } else if (nasProtocol.value === 'nfs') {
      if (!nasNfsHost.value.trim()) { ElMessage.warning({ message: t('repositoriesPage.errNfsHost'), grouping: true }); return false }
      if (!nasNfsExport.value.trim()) { ElMessage.warning({ message: t('repositoriesPage.errNfsExport'), grouping: true }); return false }
    }
    return true
  }
  if (step === 1) {
    return validateNasBindNode()
  }
  if (step === 2) {
    if (!nasName.value.trim()) {
      ElMessage.warning({ message: t('repositoriesPage.errName'), grouping: true })
      return false
    }
    if (!nasDir.value.trim()) {
      ElMessage.warning({ message: t('repositoriesPage.errRepoDir'), grouping: true })
      return false
    }
    return true
  }
  return false
}

function validateNasForm(): boolean {
  return validateNasStep(0) && validateNasStep(1) && validateNasStep(2)
}

async function nasSubmit() {
  nasName.value = resolveNasSubmitName(nasName.value, generatedNasName.value, nasNameTouched.value)
  if (!nasDir.value.trim() || !nasDirTouched.value) {
    nasDir.value = generatedNasDir.value
  }
  if (hasNasSourceNameConflict(existingNasNamesForAdd.value, nasName.value)) {
    ElMessage.warning({ message: t('protection.sourceResources.nasErrNameExists', { name: nasName.value }), grouping: true })
    return
  }
  if (!validateNasForm()) return
  nasBusy.value = true
  try {
    const payload = buildNasSourceCreatePayload({
      name: nasName.value.trim(),
      protocol: nasProtocol.value,
      mountPath: nasDir.value.trim(),
      boundNodeId: nasBindNodeId.value ?? null,
      smb: nasProtocol.value === 'smb'
        ? {
            server: nasSmbServer.value,
            share: nasSmbShare.value,
            username: nasSmbUsername.value,
            password: nasSmbPassword.value,
            domain: nasSmbDomain.value,
            options: nasNfsOptions.value,
          }
        : undefined,
      nfs: nasProtocol.value === 'nfs'
        ? {
            server: nasNfsHost.value,
            exportPath: nasNfsExport.value,
            options: nasNfsOptions.value,
          }
        : undefined,
    })
    await preflightSourceNasCreate(payload)
    await createSourceResource(payload)
    ElMessage.success({ message: t('protection.sourceResources.nasCreated'), grouping: true })
    nasAddOpen.value = false
    selectedNas.value = []
    await load()
  } catch (e) {
    const proxyName = proxyNodes.value.find((node) => node.id === nasBindNodeId.value)?.name || ''
    if (await showNasDraftPreflightGuidance(e, t, proxyName)) return
    ElMessage.error({ message: apiErrorMessage(e), grouping: true })
  } finally {
    nasBusy.value = false
  }
}

watch(nasBindNodeId, () => {
  clearNasBindNodeError()
})

watch(nasProtocol, (protocol) => {
  nasSmbServer.value = ''
  nasSmbShare.value = ''
  nasSmbUsername.value = ''
  nasSmbPassword.value = ''
  nasSmbDomain.value = ''
  nasNfsHost.value = ''
  nasNfsExport.value = ''
  nasNfsOptions.value = defaultNasMountOptions(protocol)
  nasDirTouched.value = false
  nasNameTouched.value = false
})

watch(generatedNasDir, (value) => {
  if (!nasDirTouched.value || !nasDir.value.trim()) {
    nasDir.value = value
  }
})

watch(generatedNasName, (value) => {
  if (!nasNameTouched.value || !nasName.value.trim()) {
    nasName.value = value
  }
})

function openProxyDeploy() {
  shouldRefreshProxyNodesOnReturn.value = true
  const { href } = router.resolve(PROXY_DEPLOY_ROUTE)
  window.open(href, '_blank', 'noopener,noreferrer')
}

function handleProxyDeployReturnFocus() {
  void refreshProxyNodesAfterDeployReturn()
}

function handleProxyDeployReturnVisibility() {
  if (typeof document !== 'undefined' && document.visibilityState === 'visible') {
    void refreshProxyNodesAfterDeployReturn()
  }
}

watch(nasAddOpen, (open) => {
  if (typeof document === 'undefined') return
  document.body.style.overflow = open ? 'hidden' : ''
})

watch(
  () => [filters.resource_type, filters.status],
  () => runFilterSearch(),
)

watch(
  () => route.query.openNode,
  () => {
    if (sourceTabLoaded.hostFileSystem) tryOpenHostFromQuery()
  },
)

watch(
  () => route.query.tab,
  (tab) => {
    activeTab.value = normalizeSourceTabParam(tab)
  },
  { immediate: true },
)

watch(activeTab, (tab) => {
  resetSearch()
  filters.search_field = 'name'
  const queryTab = sourceTabQueryValue(tab)
  if (route.query.tab !== queryTab) {
    router.replace({
      path: route.path,
      query: {
        ...route.query,
        tab: queryTab,
      },
    })
  }
  pagination.page = 1
  void load()
  requestAnimationFrame(() => {
    if (tab === 'hostFileSystem') layoutHostTable()
    else layoutNasTable()
  })
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

watch([hostTableLoading, nasTableLoading], ([hostLoading, nasLoading]) => {
  if (!hostLoading) layoutHostTable()
  if (!nasLoading) layoutNasTable()
})

onMounted(async () => {
  if (typeof window !== 'undefined') {
    window.addEventListener('focus', handleProxyDeployReturnFocus)
  }
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', handleProxyDeployReturnVisibility)
  }

  const signal = pageRequests.nextSignal('source-init')
  try {
    await Promise.all([loadProxyNodes(signal), loadAgentNodes(signal)])
  } catch (e) {
    if (!pageRequests.isAbortError(e)) throw e
  } finally {
    pageRequests.releaseSignal('source-init', signal)
  }
  if (signal.aborted) return
  await load()
  lifecycleOps.restorePersisted()
  refreshPendingSourceOps()
  schedulePendingSourceDeletePoll(0)
  tryOpenHostFromQuery()
})

onUnmounted(() => {
  stopPendingSourceDeletePoll()
  if (typeof window !== 'undefined') {
    window.removeEventListener('focus', handleProxyDeployReturnFocus)
  }
  if (typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', handleProxyDeployReturnVisibility)
    document.body.style.overflow = ''
  }
})
</script>

<template>
  <ModulePage
    :menus="sideNav"
    :page-title-override="pageTitle"
    body-fill
  >
    <div class="hfl-list-shell hfl-list-shell--fill">
      <div class="hfl-list-panel hfl-list-panel--fill">
        <div class="hfl-list-toolbar">
          <ElButton
            v-if="activeTab === 'hostFileSystem'"
            type="primary"
            @click="openAgentDeploy"
          >
            <Plus :size="16" />
            {{ t('protection.sourceResources.btnAdd') }}
          </ElButton>
          <ElButton
            v-else
            type="primary"
            @click="openNasAdd"
          >
            <Plus :size="16" />
            {{ t('protection.sourceResources.btnAdd') }}
          </ElButton>

          <ElDropdown
            v-if="activeTab === 'hostFileSystem'"
            trigger="click"
            popper-class="hfl-actions-dropdown"
            @visible-change="hostMoreActionsOpen = $event"
          >
            <ElButton>
              {{ t('protection.sourceResources.btnMoreActions') }}
              <ChevronDown
                :size="16"
                class="hfl-list-more__chev"
                :class="{ 'hfl-list-more__chev--open': hostMoreActionsOpen }"
              />
            </ElButton>
            <template #dropdown>
              <ElDropdownMenu>
                <ElDropdownItem
                  :disabled="hostRenameDisabled"
                  @click="openHostRenameDialog"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Pencil
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('protection.sourceResources.btnBatchRename') }}</span>
                  </span>
                </ElDropdownItem>
                <ElDropdownItem
                  :disabled="hostUpgradeDisabled"
                  @click="onUpgradeSelectedHosts"
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
                  :disabled="hostRenameDisabled"
                  @click="openSelectedHostMaintenance"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Wrench
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('nodeLifecycle.maintenanceCommands') }}</span>
                  </span>
                </ElDropdownItem>
                <ElDropdownItem
                  divided
                  class="el-dropdown-menu__item--danger"
                  :disabled="hostDeleteDisabled"
                  @click="onRemoveSelectedHosts()"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Trash2
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('protection.sourceResources.deleteBtn') }}</span>
                  </span>
                </ElDropdownItem>
              </ElDropdownMenu>
            </template>
          </ElDropdown>

          <ElDropdown
            v-else
            trigger="click"
            popper-class="hfl-actions-dropdown"
            @visible-change="nasMoreActionsOpen = $event"
          >
            <ElButton>
              {{ t('protection.sourceResources.btnMoreActions') }}
              <ChevronDown
                :size="16"
                class="hfl-list-more__chev"
                :class="{ 'hfl-list-more__chev--open': nasMoreActionsOpen }"
              />
            </ElButton>
            <template #dropdown>
              <ElDropdownMenu>
                <ElDropdownItem
                  :disabled="nasRenameDisabled"
                  @click="openNasRenameDialog"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Pencil
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('protection.sourceResources.btnBatchRename') }}</span>
                  </span>
                </ElDropdownItem>
                <ElDropdownItem
                  :disabled="nasBatchDisabled"
                  @click="openNasRebindDialog"
                >
                  <span class="el-dropdown-menu__item-content">
                    <component
                      :is="sourceAgentSidebarIcon"
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('protection.sourceResources.changeProxyNode') }}</span>
                  </span>
                </ElDropdownItem>
                <ElDropdownItem
                  :disabled="nasTestDisabled"
                  @click="onTestSelectedNas"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Link2
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('protection.sourceResources.testConnection') }}</span>
                  </span>
                </ElDropdownItem>
                <ElDropdownItem
                  divided
                  class="el-dropdown-menu__item--danger"
                  :disabled="nasDeleteDisabled"
                  @click="deleteSelectedNasRows(selectedNas)"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Trash2
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('protection.sourceResources.deleteBtn') }}</span>
                  </span>
                </ElDropdownItem>
              </ElDropdownMenu>
            </template>
          </ElDropdown>

          <div class="hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split">
            <ElInput
              v-model="filters.search"
              :placeholder="listSearchPlaceholder"
              clearable
              size="small"
              class="hfl-list-search hfl-list-search-group"
              @keyup.enter="runFilterSearch"
              @clear="clearSearch"
            >
              <template #prepend>
                <ElSelect
                  v-model="filters.search_field"
                  @change="handleSearchFieldChange"
                >
                  <ElOption
                    v-for="option in searchFieldOptions"
                    :key="option.value"
                    :value="option.value"
                    :label="option.label"
                  />
                </ElSelect>
              </template>
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
                :aria-label="t('protection.sourceResources.refresh')"
                :disabled="busy"
                @click="load"
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
          v-if="activeTab === 'hostFileSystem'"
          :snapshot="lifecycleOps.snapshot"
          @cancel-queued="lifecycleOps.cancelQueued"
        />

        <div
          v-show="activeTab === 'hostFileSystem'"
          ref="hostTableBlockRef"
          class="hfl-list-table-block"
        >
          <el-table
            ref="hostTableRef"
            v-table-overflow-title
            v-table-header-scroll-sync
            v-table-column-resize="'protection.backupSources.host'"
            v-loading="hostTableLoading"
            row-key="id"
            :data="pagedHostAgents"
            stripe
            class="hfl-list-table"
            :max-height="hostTableMaxHeight"
            :header-cell-style="TABLE_HEADER_STYLE"
            @scroll="onHostTableScroll"
            @selection-change="onHostSelectionChange"
          >
            <el-table-column
              type="selection"
              width="35"
              fixed="left"
              reserve-selection
            />
            <el-table-column
              :label="t('protection.sourceResources.colName')"
              min-width="170"
              fixed="left"
              class-name="hfl-table-name-col"
            >
              <template #default="{ row }">
                <button
                  type="button"
                  class="hfl-table-name-link hfl-table-name-link--full"
                  @click="openHostRowDetail(row)"
                >
                  {{ row.name }}
                </button>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colLifecycleStatus')"
              min-width="155"
              align="center"
              header-align="center"
            >
              <template #default="{ row }">
                <div class="hfl-table-no-tooltip">
                  <FlowSourceReadyStatusCell
                    v-if="resolveSourcePendingStatus(hostSelectableId(row))"
                    v-bind="resolveSourcePendingStatus(hostSelectableId(row))!"
                    @click="openSourcePendingFailureDetails(hostSelectableId(row))"
                  />
                  <NodeLifecycleStatusCell
                    v-else
                    :node="row"
                    :resolve-display-status="resolveBackupSourceLifecycleStatus"
                  />
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colHostIp')"
              min-width="120"
            >
              <template #default="{ row }">
                <span :class="{ 'hfl-empty-mark': !row.ip_address?.trim() }">{{ row.ip_address?.trim() || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column
              label="OS"
              min-width="105"
            >
              <template #default="{ row }">
                <div class="source-os-cell source-os-cell--compact hfl-table-no-tooltip">
                  <span class="source-os-cell__icon-wrap">
                    <AgentPlatformBrandIcon :os="agentEnrollmentOs(row)" />
                  </span>
                  <span class="source-os-cell__platform">{{ agentPlatformLabel(row) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colCpu')"
              min-width="76"
            >
              <template #default="{ row }">
                <span :class="{ 'hfl-empty-mark': nodeCpuCores(row) == null }">{{ nodeCpuCores(row) != null ? t('protection.sourceResources.cpuCoresValue', { n: nodeCpuCores(row) }) : '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colMemory')"
              min-width="90"
            >
              <template #default="{ row }">
                <span :class="{ 'hfl-empty-mark': nodeMemoryTotalBytes(row) == null }">{{ nodeMemoryTotalBytes(row) != null ? formatNodeBytes(nodeMemoryTotalBytes(row)!) : '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colDiskCount')"
              min-width="78"
            >
              <template #default="{ row }">
                <span :class="{ 'hfl-empty-mark': nodeDiskCount(row) == null }">{{ nodeDiskCount(row) ?? '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colCapacity')"
              min-width="170"
            >
              <template #default="{ row }">
                <HflCapacityCell
                  :used-bytes="agentUsageParts(row).used"
                  :total-bytes="agentUsageParts(row).total"
                  variant="compact"
                  :format-bytes="formatBytes"
                />
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colConnectivity')"
              min-width="110"
              align="center"
              header-align="center"
            >
              <template #default="{ row }">
                <div class="hfl-table-no-tooltip">
                  <ElTag
                    :type="availabilityTagType(row.availability)"
                    size="small"
                  >
                    {{ availabilityLabel(row.availability) }}
                  </ElTag>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colVersion')"
              min-width="115"
            >
              <template #default="{ row }">
                <NodeVersionCell
                  :node="row"
                  :version-label="agentVersion(row)"
                  :target-version="row.agent_release?.target_version"
                  :update-available="row.agent_release?.update_available"
                  :resolve-version-display="lifecycleOps.resolveVersionDisplay"
                />
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colRegisteredAt')"
              min-width="145"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-time"
                  :class="{ 'hfl-empty-mark': !row.created_at }"
                >{{ formatDate(row.created_at) }}</span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty
                :description="t('protection.sourceResources.emptyHostSources')"
                :image-size="80"
              />
            </template>
          </el-table>
        </div>

        <div
          v-show="activeTab === 'nas'"
          ref="nasTableBlockRef"
          class="hfl-list-table-block"
        >
          <el-table
            ref="nasTableRef"
            v-table-overflow-title
            v-table-header-scroll-sync
            v-table-column-resize="'protection.backupSources.nas'"
            v-loading="nasTableLoading"
            row-key="id"
            :data="nasRows"
            stripe
            class="hfl-list-table"
            :max-height="nasTableMaxHeight"
            :header-cell-style="TABLE_HEADER_STYLE"
            @scroll="onNasTableScroll"
            @selection-change="onNasSelectionChange"
          >
            <el-table-column
              type="selection"
              width="35"
              fixed="left"
            />
            <el-table-column
              :label="t('protection.sourceResources.colName')"
              min-width="170"
              fixed="left"
              class-name="hfl-table-name-col"
            >
              <template #default="{ row }">
                <button
                  type="button"
                  class="hfl-table-name-link hfl-table-name-link--full"
                  @click="openRowDetail(row)"
                >
                  {{ row.name }}
                </button>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colLifecycleStatus')"
              min-width="165"
              align="center"
              header-align="center"
            >
              <template #default="{ row }">
                <div class="hfl-table-no-tooltip">
                  <FlowSourceReadyStatusCell
                    v-if="resolveSourcePendingStatus(nasSelectableId(row))"
                    v-bind="resolveSourcePendingStatus(nasSelectableId(row))!"
                  />
                  <el-tag
                    v-else
                    :type="sourceNodeOnlineTagType(row)"
                    size="small"
                  >
                    {{ sourceNodeOnlineLabel(row) }}
                  </el-tag>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colProtocol')"
              width="85"
            >
              <template #default="{ row }">
                <span
                  v-if="nasProtocolType(row) !== 'nas'"
                  :class="nasProtocolPillClass(row)"
                >
                  <component
                    :is="nasMountProtocolIcon(nasProtocolType(row))"
                    :size="12"
                    stroke-width="2.25"
                  />
                  {{ nasProtocolLabel(row) }}
                </span>
                <span
                  v-else
                  class="hfl-empty-mark"
                >—</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colNasServer')"
              min-width="115"
            >
              <template #default="{ row }">
                {{ nasServerAddress(row) }}
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colNasShareExport')"
              min-width="120"
            >
              <template #default="{ row }">
                <div class="table-stack-cell">
                  <span class="table-stack-cell__secondary">{{ t(nasPathKindLabelKey(row)) }}</span>
                  <span class="table-stack-cell__primary">{{ nasShareOrExport(row) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colSourceProxy')"
              min-width="120"
            >
              <template #header>
                <span class="hfl-table-header-with-tip">
                  <span>{{ t('protection.sourceResources.colSourceProxy') }}</span>
                  <HflHelpTip
                    :content="t('protection.sourceResources.sourceProxyColumnTip')"
                    :aria-label="t('protection.sourceResources.sourceProxyColumnHelp')"
                  />
                </span>
              </template>
              <template #default="{ row }">
                <div class="table-stack-cell">
                  <span
                    class="table-stack-cell__primary"
                    :class="{ 'hfl-empty-mark': !nasSourceProxyName(row) }"
                  >{{ nasSourceProxyName(row) || '—' }}</span>
                  <span
                    v-if="sourceNeedsProxyRebind(row)"
                    class="table-stack-cell__secondary source-needs-proxy"
                  >
                    {{ t('protection.sourceResources.needsProxyRebind') }}
                  </span>
                  <span
                    v-else-if="nasSourceProxyIp(row)"
                    class="table-stack-cell__secondary"
                  >{{ nasSourceProxyIp(row) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colProxyMountPoint')"
              min-width="165"
            >
              <template #default="{ row }">
                <span class="hfl-table-cell-full hfl-table-no-tooltip">{{ nasProxyMountPoint(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colCapacity')"
              min-width="170"
            >
              <template #default="{ row }">
                <HflCapacityCell
                  v-if="row.total_size"
                  :used-bytes="Number(row.used_size || 0)"
                  :total-bytes="Number(row.total_size || 0)"
                  variant="compact"
                  :format-bytes="formatBytes"
                />
                <span
                  v-else-if="nasShouldRefreshCapacity(row)"
                  class="source-capacity-pending"
                >
                  {{ t('protection.sourceResources.capacitySyncing') }}
                </span>
                <span
                  v-else
                  class="hfl-empty-mark"
                >—</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colConnectivity')"
              min-width="110"
              align="center"
              header-align="center"
            >
              <template #default="{ row }">
                <div class="hfl-table-no-tooltip">
                  <ElTag
                    :type="availabilityTagType(row.availability)"
                    size="small"
                  >
                    {{ availabilityLabel(row.availability) }}
                  </ElTag>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colRegisteredAt')"
              min-width="145"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-time"
                  :class="{ 'hfl-empty-mark': !sourceRegisteredAt(row) }"
                >{{ formatDate(sourceRegisteredAt(row)) }}</span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty
                :description="t('protection.sourceResources.emptyNasSources')"
                :image-size="80"
              />
            </template>
          </el-table>
        </div>

        <div class="hfl-list-footer">
          <span
            v-if="tabSelectedCount > 0"
            class="hfl-list-footer__selected"
          >
            {{ t('protection.sourceResources.selectedCount', { n: tabSelectedCount }) }}
          </span>
          <HflPagination
            v-model:current-page="pagination.page"
            v-model:page-size="pagination.pageSize"
            class="hfl-list-footer__pagination"
            :total="pagination.count"
            @current-change="load"
            @size-change="load"
          />
        </div>
      </div>
    </div>

    <!-- ===== Edit Dialog ===== -->
    <el-dialog
      v-model="dialogOpen"
      :title="editing ? t('protection.sourceResources.edit') : t('protection.sourceResources.add')"
      width="560px"
      destroy-on-close
    >
      <el-form
        label-width="120px"
        @submit.prevent="saveForm"
      >
        <el-form-item
          :label="t('protection.sourceResources.formName')"
          required
        >
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item :label="t('protection.sourceResources.formType')">
          <el-select
            v-model="form.resource_type"
            :disabled="!!editing"
          >
            <el-option
              v-for="rt in resourceTypes"
              :key="rt"
              :label="typeLabel(rt)"
              :value="rt"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('protection.sourceResources.formDesc')">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="2"
          />
        </el-form-item>
        <el-form-item :label="t('protection.sourceResources.formNode')">
          <el-select
            v-model="form.bound_node_id"
            clearable
            :placeholder="t('protection.sourceResources.selectNode')"
          >
            <el-option
              v-for="n in proxyNodes"
              :key="n.id"
              :label="n.name"
              :value="n.id"
            />
          </el-select>
          <p
            v-if="!proxyNodes.length"
            class="hint-warn"
          >
            {{ t('protection.sourceResources.noProxy') }}
            <RouterLink
              class="hint-link"
              to="/node/nodes/deploy?role=proxy"
            >
              {{ t('protection.sourceResources.deployProxy') }}
            </RouterLink>
          </p>
        </el-form-item>

        <template v-if="form.resource_type === 'local'">
          <el-form-item
            :label="t('protection.sourceResources.rootPath')"
            required
          >
            <el-input
              v-model="form.root_path"
              placeholder="/data/backup"
            />
          </el-form-item>
        </template>
        <template v-else-if="form.resource_type === 'nfs'">
          <el-form-item
            :label="t('protection.sourceResources.server')"
            required
          >
            <el-input v-model="form.server" />
          </el-form-item>
          <el-form-item
            :label="t('protection.sourceResources.exportPath')"
            required
          >
            <el-input v-model="form.export_path" />
          </el-form-item>
        </template>
        <template v-else-if="form.resource_type === 'cifs'">
          <el-form-item
            :label="t('protection.sourceResources.server')"
            required
          >
            <el-input v-model="form.server" />
          </el-form-item>
          <el-form-item
            :label="t('protection.sourceResources.share')"
            required
          >
            <el-input v-model="form.share" />
          </el-form-item>
          <el-form-item
            :label="t('protection.sourceResources.username')"
            required
          >
            <el-input v-model="form.username" />
          </el-form-item>
          <el-form-item
            :label="t('protection.sourceResources.password')"
            required
          >
            <el-input
              v-model="form.password"
              type="password"
              show-password
            />
          </el-form-item>
        </template>
        <template v-else-if="form.resource_type === 'nas'">
          <el-form-item
            :label="t('protection.sourceResources.server')"
            required
          >
            <el-input v-model="form.server" />
          </el-form-item>
          <el-form-item :label="t('protection.sourceResources.share')">
            <el-input v-model="form.share" />
          </el-form-item>
        </template>
        <template v-else-if="form.resource_type === 's3'">
          <el-form-item
            :label="t('protection.sourceResources.endpoint')"
            required
          >
            <el-input v-model="form.endpoint" />
          </el-form-item>
          <el-form-item
            :label="t('protection.sourceResources.bucket')"
            required
          >
            <el-input v-model="form.bucket" />
          </el-form-item>
          <el-form-item
            :label="t('protection.sourceResources.accessKey')"
            required
          >
            <el-input v-model="form.access_key" />
          </el-form-item>
          <el-form-item
            :label="t('protection.sourceResources.secretKey')"
            required
          >
            <el-input
              v-model="form.secret_key"
              type="password"
              show-password
            />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">
          {{ t('common.cancel') }}
        </el-button>
        <el-button
          type="primary"
          :loading="formBusy"
          @click="saveForm"
        >
          {{ t('common.save') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ===== Rename Dialog ===== -->
    <el-dialog
      v-model="renameDialogOpen"
      class="source-action-dialog"
      :title="t('protection.sourceResources.renameTitle')"
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
          :label="t('protection.sourceResources.renameLabel')"
          required
        >
          <ElInput
            v-model="renameInput"
            :placeholder="t('protection.sourceResources.renamePlaceholder')"
            maxlength="128"
            show-word-limit
          />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <el-button @click="closeRenameDialog">
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

    <ChangeProxyHostDialog
      v-model="rebindDialogOpen"
      v-model:node-id="rebindNodeId"
      :selected-count="selectedNas.length"
      :offline-proxy-count="offlineProxyNodeCount"
      :proxy-nodes-refreshing="proxyNodesRefreshing"
      :online-proxy-nodes="onlineProxyNodes"
      :saving="nasRebindBusy"
      @refresh="refreshProxyNodesManually"
      @deploy="openProxyDeploy"
      @save="submitNasRebind"
    />

    <!-- ===== Host Detail Drawer ===== -->
    <HostSourceDetailDrawer
      :model-value="hostDetailOpen"
      :node-id="hostDetailNodeId"
      :source="hostDetailSource"
      :initial-tab="hostDetailInitialTab"
      :resolve-display-status="resolveBackupSourceLifecycleStatus"
      @update:model-value="onHostDetailDrawerClose"
      @saved="onHostDetailDrawerSaved"
    />

    <!-- ===== NAS Detail Drawer ===== -->
    <NasSourceDetailDrawer
      v-if="detailOpen && isNasDetail"
      v-model="detailOpen"
      :resource="detail"
      :proxy-nodes="proxyNodes"
      @updated="onNasDetailUpdated"
    />

    <!-- ===== Agent Deploy Fullscreen (Host File System) ===== -->
    <Teleport to="body">
      <div
        v-if="agentDeployOpen"
        ref="agentDeployShellRef"
        class="fullscreen-form-fullscreen fullscreen-form-animated resource-add-fullscreen source-deploy-fullscreen agent-deploy-fullscreen"
        role="dialog"
        aria-modal="true"
        tabindex="-1"
        @keydown.escape.prevent="closeAgentDeploy"
      >
        <div class="fullscreen-form-page source-deploy-page">
          <div class="fullscreen-form-header">
            <button
              type="button"
              class="fullscreen-form-header__back"
              @click="closeAgentDeploy"
            >
              <ArrowLeft
                class="fullscreen-form-header__back-icon"
                :size="18"
              />
            </button>
            <div class="fullscreen-form-header__content">
              <h1 class="fullscreen-form-header__title">
                {{ t('protection.sourceResources.deployAgent') }}
              </h1>
              <p class="fullscreen-form-header__desc">
                {{ t('protection.sourceResources.deployAgentHint') }}
              </p>
            </div>
          </div>

          <div class="fullscreen-form-layout">
            <div class="fullscreen-form-main">
              <NodeLifecycleWizard
                install-only
                :org-key="deployOrgKey"
                role="agent"
                :os="deploySelectedOs"
                role-locked
                @update:os="deploySelectedOs = $event"
                @copy="copyDeployScript"
              />

              <div class="fullscreen-form-footer fullscreen-form-action-footer">
                <ElButton @click="closeAgentDeploy">
                  {{ t('common.back') }}
                </ElButton>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ===== NAS Add Fullscreen ===== -->
    <Teleport to="body">
      <div
        v-if="nasAddOpen"
        ref="nasAddShellRef"
        class="fullscreen-form-fullscreen fullscreen-form-animated resource-add-fullscreen source-deploy-fullscreen source-deploy-nas-add"
        role="dialog"
        aria-modal="true"
        tabindex="-1"
        @keydown.escape.prevent="closeNasAdd"
      >
        <div class="fullscreen-form-page source-deploy-page">
          <div class="fullscreen-form-header">
            <button
              type="button"
              class="fullscreen-form-header__back"
              @click="closeNasAdd"
            >
              <ArrowLeft
                class="fullscreen-form-header__back-icon"
                :size="18"
              />
            </button>
            <div class="fullscreen-form-header__content">
              <h1 class="fullscreen-form-header__title">
                {{ t('protection.sourceResources.addNas') }}
              </h1>
              <p class="fullscreen-form-header__desc">
                {{ t('protection.sourceResources.addSourceTypeNasHint') }}
              </p>
            </div>
          </div>

          <div class="fullscreen-form-layout">
            <div class="fullscreen-form-main">
              <NasAddForm
                ref="nasAddFormRef"
                :protocol="nasProtocol"
                :smb-server="nasSmbServer"
                :smb-share="nasSmbShare"
                :smb-username="nasSmbUsername"
                :smb-password="nasSmbPassword"
                :smb-domain="nasSmbDomain"
                :nfs-host="nasNfsHost"
                :nfs-export="nasNfsExport"
                :nfs-options="nasNfsOptions"
                :bind-node-id="nasBindNodeId"
                :bind-node-error="nasBindNodeError"
                :name="nasName"
                :generated-name="generatedNasName"
                :name-conflict-message="nasNameConflictMessage"
                :dir="nasDir"
                :generated-dir="generatedNasDir"
                :proxy-nodes="proxyNodes"
                :proxy-nodes-refreshing="proxyNodesRefreshing"
                :busy="nasBusy"
                @update:protocol="nasProtocol = $event"
                @update:smb-server="nasSmbServer = $event"
                @update:smb-share="nasSmbShare = $event"
                @update:smb-username="nasSmbUsername = $event"
                @update:smb-password="nasSmbPassword = $event"
                @update:smb-domain="nasSmbDomain = $event"
                @update:nfs-host="nasNfsHost = $event"
                @update:nfs-export="nasNfsExport = $event"
                @update:nfs-options="nasNfsOptions = $event"
                @update:bind-node-id="nasBindNodeId = $event"
                @update:name="nasName = $event"
                @name-touched="nasNameTouched = true"
                @update:dir="nasDir = $event"
                @dir-touched="nasDirTouched = true"
                @clear-bind-node-error="clearNasBindNodeError"
                @refresh-proxy-nodes="refreshProxyNodesManually"
                @open-proxy-deploy="openProxyDeploy"
                @cancel="closeNasAdd"
                @submit="nasSubmit"
              />
            </div>
          </div>
        </div>
      </div>
    </Teleport>
    <el-dialog
      v-model="unregisterStep3BlockedDialogOpen"
      class="source-unregister-blocked-dialog"
      :title="t('protection.sourceResources.unregisterBlockedTitle')"
      width="640px"
      align-center
      destroy-on-close
    >
      <div class="source-unregister-blocked-dialog__body">
        <div class="source-unregister-blocked-dialog__lead">
          <span
            class="source-unregister-blocked-dialog__icon"
            aria-hidden="true"
          >
            <TriangleAlert :size="18" />
          </span>
          <div class="source-unregister-blocked-dialog__copy">
            <p class="source-unregister-blocked-dialog__title">
              {{ t('protection.sourceResources.unregisterBlockedLead', { n: unregisterStep3BlockedRows.length }) }}
            </p>
            <p class="source-unregister-blocked-dialog__desc">
              {{ t('protection.sourceResources.unregisterBlockedDesc') }}
            </p>
          </div>
        </div>

        <div
          class="source-unregister-blocked-dialog__impact"
          role="alert"
        >
          <ol class="source-unregister-blocked-dialog__impact-list">
            <li class="source-unregister-blocked-dialog__impact-item">
              <span class="source-unregister-blocked-dialog__impact-index">1</span>
              <span>{{ t('protection.sourceResources.unregisterBlockedImpactStep3') }}</span>
            </li>
            <li class="source-unregister-blocked-dialog__impact-item">
              <span class="source-unregister-blocked-dialog__impact-index">2</span>
              <span>{{ t('protection.sourceResources.unregisterBlockedImpactUseWizard') }}</span>
            </li>
          </ol>
        </div>

        <section class="source-unregister-blocked-dialog__section">
          <h3 class="source-unregister-blocked-dialog__section-title">
            {{ t('protection.sourceResources.unregisterBlockedListTitle') }}
          </h3>
          <ul class="source-unregister-blocked-dialog__list">
            <li
              v-for="row in unregisterStep3BlockedRows"
              :key="row.id"
              class="source-unregister-blocked-dialog__row"
            >
              <span class="source-unregister-blocked-dialog__row-main">
                <span class="source-unregister-blocked-dialog__row-name">{{ row.name }}</span>
                <span class="source-unregister-blocked-dialog__row-meta">{{ row.type }}</span>
              </span>
              <ElTag
                v-if="row.statusLabel"
                size="small"
                :type="row.statusTag || 'info'"
                effect="plain"
              >
                {{ row.statusLabel }}
              </ElTag>
            </li>
          </ul>
        </section>
      </div>
      <template #footer>
        <el-button @click="closeUnregisterStep3BlockedDialog">
          {{ t('common.close') }}
        </el-button>
        <el-button
          type="primary"
          @click="goToStartBackupForUnregister"
        >
          {{ t('protection.backupsPage.btnGoToStartBackup') }}
        </el-button>
      </template>
    </el-dialog>
    <BackupSourceDeleteDialog
      v-model="backupSourceDeleteDialogOpen"
      :source-ids="backupSourceDeleteIds"
      :sources="backupSourceDeleteRows"
      :previous-failure-details="backupSourceDeletePreviousFailure"
      @deleted="onBackupSourcesDeleted"
    />
    <NodeLifecycleUpgradeConfirmDialog
      v-model="upgradeConfirmOpen"
      :preview="upgradeConfirmPreview"
      @confirm="lifecycleOps.resolveUpgradeConfirm()"
      @cancel="lifecycleOps.cancelUpgradeConfirm()"
    />
  </ModulePage>
</template>

<style src="../../styles/source-deploy-ui.css"></style>
<style src="../../styles/agent-install-wizard.css"></style>
<style scoped>
.hfl-table-header-with-tip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}

.source-shell {
  background: transparent;
  border: none;
  border-radius: 0;
  box-shadow: none;
  overflow: visible;
}

.source-underline-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: 0 18px;
  background: transparent;
  border-bottom: none;
}
.source-underline-tabs :deep(.el-tabs__nav-wrap::after) {
  display: none;
}
.source-underline-tabs :deep(.el-tabs__nav) {
  padding-left: 0;
}
.source-underline-tabs :deep(.el-tabs__item) {
  height: 48px;
  line-height: 48px;
  padding: 0 24px;
  font-size: 14px;
  font-weight: 400;
  color: rgb(71 85 105);
  border: none;
  transition: color var(--transition-fast, 150ms ease);
}
.source-underline-tabs :deep(.el-tabs__item:hover) {
  color: rgb(30 41 59);
}
.source-underline-tabs :deep(.el-tabs__item.is-active) {
  color: rgb(29 78 216);
  font-weight: 500;
}
.source-underline-tabs :deep(.el-tabs__active-bar) {
  height: 3px;
  border-radius: 2px 2px 0 0;
  background: linear-gradient(180deg, #3b82f6 0%, #1d4ed8 100%);
}
.source-underline-tabs :deep(.el-tabs__content) {
  display: none;
}

.text-muted {
  color: var(--el-text-color-secondary);
}
.hint-link {
  margin-left: 0.25rem;
  color: var(--el-color-primary);
}

.source-table {
  width: 100%;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 12px;
  overflow: hidden;
  background: #fff;
}
.source-table :deep(th.el-table__cell) {
  border-bottom: 1px solid rgba(226, 232, 240, 0.95);
  white-space: nowrap;
  height: 48px;
}
.source-table :deep(td.el-table__cell) {
  padding-top: 14px;
  padding-bottom: 14px;
  vertical-align: middle;
}
.source-table :deep(.el-table__row:hover > td.el-table__cell) {
  background: rgba(248, 250, 252, 0.76);
}

.source-footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
  padding-top: 8px;
  border-top: 1px solid var(--color-border-light, #ebeef5);
}
.source-footer__selected {
  font-size: 13px;
  color: var(--color-text-secondary, #606266);
}
.source-node-name {
  color: rgb(15 23 42);
  font-weight: 500;
}
.icon-sm {
  width: 1rem;
  height: 1rem;
  vertical-align: middle;
}
.hint-warn {
  margin: 0.25rem 0 0;
  font-size: 0.8rem;
  color: var(--el-color-warning);
}
.detail-dl {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 0.5rem 0.75rem;
  margin: 0;
}
.detail-dl dt {
  color: var(--el-text-color-secondary);
  font-size: 0.85rem;
}
.detail-dl dd {
  margin: 0;
}

.source-action-dialog__form :deep(.el-form-item) {
  margin-bottom: 0;
}

.source-action-dialog__form :deep(.el-form-item__label) {
  height: auto;
  padding: 0 0 8px;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.4;
  color: rgb(51 65 85);
}

.source-action-dialog__form :deep(.el-form-item.is-required:not(.is-no-asterisk) > .el-form-item__label::before) {
  margin-right: 4px;
  color: var(--el-color-danger);
}

.source-action-dialog__form :deep(.el-input),
.source-action-dialog__form :deep(.el-select) {
  width: 100%;
}

.source-unregister-blocked-dialog :deep(.el-dialog__body) {
  padding-top: 8px;
}

.source-unregister-blocked-dialog__body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.source-unregister-blocked-dialog__lead {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  align-items: start;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid rgb(254 202 202);
  border-radius: 8px;
  background: rgb(254 242 242);
}

.source-unregister-blocked-dialog__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 999px;
  background: #fff;
  color: rgb(220 38 38);
}

.source-unregister-blocked-dialog__copy {
  min-width: 0;
}

.source-unregister-blocked-dialog__title {
  margin: 0;
  color: rgb(127 29 29);
  font-size: 14px;
  font-weight: 700;
  line-height: 1.45;
}

.source-unregister-blocked-dialog__desc {
  margin: 4px 0 0;
  color: rgb(153 27 27);
  font-size: 13px;
  line-height: 1.55;
}

.source-unregister-blocked-dialog__impact {
  width: 100%;
  padding: 12px 14px;
  border: 1px solid var(--color-warning-border);
  border-radius: 6px;
  background: var(--color-warning-light);
  color: var(--color-warning);
}

.source-unregister-blocked-dialog__impact-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.source-unregister-blocked-dialog__impact-item {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  align-items: flex-start;
  gap: 8px;
  color: rgb(120 75 12);
  font-size: 13px;
  line-height: 1.55;
}

.source-unregister-blocked-dialog__impact-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: 1px solid var(--color-warning-border);
  border-radius: 999px;
  background: #fff;
  color: var(--color-warning);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
}

.source-unregister-blocked-dialog__section {
  display: grid;
  gap: 10px;
}

.source-unregister-blocked-dialog__section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 700;
}

.source-unregister-blocked-dialog__section-title::before {
  display: inline-block;
  width: 3px;
  height: 14px;
  border-radius: 999px;
  background: var(--color-primary);
  content: '';
}

.source-unregister-blocked-dialog__list {
  display: grid;
  max-height: 220px;
  margin: 0;
  padding: 0;
  overflow: auto;
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  list-style: none;
}

.source-unregister-blocked-dialog__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  min-height: 48px;
  padding: 8px 12px;
  background: #fff;
}

.source-unregister-blocked-dialog__row + .source-unregister-blocked-dialog__row {
  border-top: 1px solid var(--color-border-light);
}

.source-unregister-blocked-dialog__row-main {
  display: grid;
  min-width: 0;
  gap: 2px;
}

.source-unregister-blocked-dialog__row-name {
  min-width: 0;
  overflow: hidden;
  color: rgb(15 23 42);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-unregister-blocked-dialog__row-meta {
  min-width: 0;
  overflow: hidden;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Agent Deploy Dialog body */
.agent-deploy-body {
  max-height: 60vh;
  overflow-y: auto;
}
/* Source fullscreen cards */
.add-source-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
  width: 100%;
  min-width: 0;
}
.source-hero-card {
  margin-bottom: 14px;
}
.source-hero {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}
.source-hero__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  flex-shrink: 0;
  border-radius: 14px;
  background: linear-gradient(180deg, #eaf3ff 0%, #dbeafe 100%);
  color: #2563eb;
  border: 1px solid rgba(37, 99, 235, 0.14);
}
.source-hero__icon--nas {
  background: linear-gradient(180deg, #eefbf6 0%, #dff7ec 100%);
  color: #0f766e;
  border-color: rgba(15, 118, 110, 0.14);
}
.source-hero__body {
  min-width: 0;
  flex: 1 1 auto;
}
.source-hero__title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--el-text-color-primary, #0f172a);
}
.source-hero__desc {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--el-text-color-secondary, #64748b);
}
.add-nas-deploy-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  padding-inline: 14px;
  border-radius: 10px;
}
/* NAS Add Step styles */
.add-nas-steps {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0;
}
.add-nas-steps--panel {
  margin-bottom: 10px;
}
.add-nas-step {
  display: flex;
  align-items: center;
  gap: 10px;
  opacity: 0.5;
  transition: opacity 0.2s ease;
}
.add-nas-step--active,
.add-nas-step--done {
  opacity: 1;
}
.add-nas-step__num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 600;
  background: rgb(248 250 252);
  border: 1px solid rgba(203, 213, 225, 0.95);
  color: rgb(100 116 139);
  transition: all 0.2s ease;
}
.add-nas-step--active .add-nas-step__num,
.add-nas-step--done .add-nas-step__num {
  background: linear-gradient(180deg, rgb(37 99 235) 0%, rgb(29 78 216) 100%);
  border-color: rgb(29 78 216);
  color: #fff;
}
.add-nas-step__label {
  font-size: 14px;
  font-weight: 500;
  color: rgb(30 41 59);
  white-space: nowrap;
}
.add-nas-step__connector {
  flex: 1;
  max-width: 60px;
  height: 2px;
  background: rgba(203, 213, 225, 0.9);
  border-radius: 1px;
}
.add-nas-section {
  padding: 16px 24px;
}
.add-nas-section__head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.add-nas-section__indicator {
  width: 4px;
  height: 16px;
  border-radius: 999px;
  background: linear-gradient(180deg, #3b82f6 0%, #1d4ed8 100%);
}
.add-nas-section__title-wrap {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.add-nas-section__title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: rgb(15 23 42);
  margin: 0 0 18px;
}
.add-nas-section__subtitle {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: rgb(30 41 59);
  margin: 22px 0 14px;
  padding-top: 18px;
  border-top: 1px solid rgba(226, 232, 240, 0.95);
}
.add-nas-section__icon {
  color: var(--el-color-primary);
}
.add-nas-form {
  margin-top: 4px;
  width: 100%;
}
.add-nas-form :deep(.el-form-item__label) {
  font-weight: 600;
  color: rgb(30 41 59);
  padding-bottom: 6px;
}
.add-nas-form :deep(.el-form-item) {
  margin-bottom: 18px;
}
.add-nas-form-row {
  display: flex;
  gap: 16px;
}
.add-nas-form-row > * {
  min-width: 0;
}
.add-nas-form-row--responsive {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 20px;
  margin-bottom: 2px;
}
.add-nas-form-row--responsive :deep(.el-form-item) {
  margin-bottom: 18px;
}
.add-nas-form-row--triple {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0 20px;
  margin-bottom: 2px;
}
.add-nas-form-row--triple :deep(.el-form-item) {
  margin-bottom: 18px;
}
.add-nas-form-row--pair {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: center;
}
@media (min-width: 1200px) {
  .add-nas-form-row--pair {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    max-width: calc((100% - 40px) * 2 / 3 + 20px);
  }
}
.add-nas-form-item--wide {
  max-width: min(100%, 560px);
}
.add-nas-field-hint {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-tertiary, #64748b);
}
.add-nas-inline-note {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary, #64748b);
}
.add-nas-select-row__refresh {
  width: 32px;
  height: 32px;
  padding: 0;
  flex-shrink: 0;
  color: var(--el-text-color-secondary, #64748b);
}
.add-nas-select-row__refresh .is-spinning {
  animation: source-refresh-spin 0.85s linear infinite;
}
.add-nas-select-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.add-nas-select-row__select {
  flex: 1 1 auto;
  min-width: 0;
  width: 100%;
}
.add-nas-bind-form-item :deep(.el-form-item__content) {
  width: 100%;
}
.nas-no-proxy-warn {
  padding: 16px;
  border-radius: 6px;
  background: var(--el-color-warning-light-9);
  border: 1px solid var(--el-color-warning-light-5);
  font-size: 13px;
  color: var(--el-color-warning);
}
.nas-no-proxy-warn .nas-bind-error {
  color: var(--el-color-danger);
}
.add-nas-select-row__select.is-error :deep(.el-select__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}
.nas-no-proxy-warn .hint-link {
  display: inline-block;
  margin-top: 8px;
  font-weight: 600;
}
@media (max-width: 1199.98px) {
  .add-nas-form-row--triple {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .add-nas-form-row--triple :deep(.el-form-item:last-child) {
    grid-column: 1 / -1;
    max-width: none;
  }
  .add-nas-form-row--pair {
    max-width: none;
  }
}
@media (max-width: 767.98px) {
  .source-underline-tabs :deep(.el-tabs__header) {
    padding-right: 12px;
    padding-left: 12px;
  }
  .source-hero,
  .add-nas-form-row--responsive,
  .add-nas-form-row--triple,
  .nas-review-grid {
    grid-template-columns: 1fr;
    flex-direction: column;
  }
  .add-nas-form-row--responsive,
  .add-nas-form-row--triple {
    gap: 0;
  }
  .add-nas-form-item--wide {
    max-width: none;
  }
  .add-nas-select-row {
    align-items: stretch;
  }
  .add-nas-steps {
    gap: 8px;
  }
  .add-nas-step__connector {
    max-width: 36px;
  }
  .source-hero__icon {
    width: 42px;
    height: 42px;
    border-radius: 12px;
  }
  .source-hero__title {
    font-size: 16px;
  }
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.source-fullscreen--form-shell .add-nas-form {
  max-width: none;
}

</style>
