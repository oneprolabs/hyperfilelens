<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowUpCircle, Plus, RefreshCw, Pencil, ChevronDown, Trash2, TriangleAlert, Search, Wrench } from 'lucide-vue-next'
import { ElCheckbox, ElMessage, type ElTable } from 'element-plus'
import NodeLifecycleStatusCell from '../../components/node-lifecycle/NodeLifecycleStatusCell.vue'
import NodeVersionCell from '../../components/node-lifecycle/NodeVersionCell.vue'
import NodeLifecycleBanner from '../../components/node-lifecycle/NodeLifecycleBanner.vue'
import { useNodeLifecycleOps } from '../../composables/useNodeLifecycleOps'
import { debouncedNodeStatus } from '../../composables/useNodeConnectionDisplay'
import ModulePage from '../../components/ModulePage.vue'
import ProxyNodeDetailDrawer from '../../components/ProxyNodeDetailDrawer.vue'
import ProxyHostDeleteBlockedDialog from '../../components/ProxyHostDeleteBlockedDialog.vue'
import DangerConfirmDialog from '../../components/DangerConfirmDialog.vue'
import NodeLifecycleUpgradeConfirmDialog from '../../components/NodeLifecycleUpgradeConfirmDialog.vue'
import AgentPlatformBrandIcon from '../../components/agent-deploy/AgentPlatformBrandIcon.vue'
import ResourceNameSummaryCell from '../../components/ResourceNameSummaryCell.vue'
import HflCapacityCell from '../../components/HflCapacityCell.vue'
import { useProtectionSideNav } from '../../composables/useProtectionSideNav'
import { useListTableLayout } from '../../composables/useListTableLayout'
import { useListSearch } from '../../composables/useListSearch'
import { usePageRequestScope } from '../../composables/usePageRequestScope'
import { apiErrorMessage } from '../../lib/api'
import { afterOverlayDismiss } from '../../lib/uiDefer'
import { LIST_ROUTE_REFRESH_KEY, stripListRefreshQuery } from '../../lib/listRouteRefresh'
import { listNodesPaged, listAllNodes, updateNode, fetchLatestAgentVersion, getNodeBindings, previewNodeOperationsBatch, type NodeBindings } from '../../lib/nodeApi'
import { canRemoteAgentUpgrade, needsAgentUpgrade } from '../../lib/agentVersion'
import { backupSourceLifecycleDisplay } from '../../lib/backupSourceLifecycleDisplay'
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
import type { ApiNode, NodeRole, NodeStatus } from '../../types/node'

const rows = ref<ApiNode[]>([])
const latestAgentVersion = ref<string | null>(null)
const busy = ref(false)
const nodeListLoaded = ref(false)
const search = ref('')
const searchField = ref<'name' | 'ip'>('name')
const {
  appliedSearch,
  clearSearch,
  handleSearchFieldChange,
  resetSearch,
  runSearchNow: runFilterSearch,
} = useListSearch(search, () => {
  applySearch()
})
const TABLE_HEADER_STYLE: Record<string, string> = {
  background: 'rgba(248, 250, 252, 0.96)',
  color: 'rgb(71 85 105)',
  fontWeight: '600',
  whiteSpace: 'nowrap',
}
const tableRef = ref<InstanceType<typeof ElTable> | null>(null)
const tableBlockRef = ref<HTMLElement | null>(null)
const {
  tableMaxHeight,
  layoutTable,
  handleTableScroll: onTableScroll,
} = useListTableLayout(tableRef, tableBlockRef)

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const pageRequests = usePageRequestScope()

const osPlatformLabels = computed((): NodeOsPlatformLabels => ({
  linux: t('protection.sourceResources.osPlatformLinux'),
  windows: t('protection.sourceResources.osPlatformWindows'),
  macos: t('protection.sourceResources.osPlatformMacos'),
}))

function proxyPlatformLabel(row: ApiNode) {
  return nodePlatformLabel(nodeEnrollmentOs(row), osPlatformLabels.value)
}

function availabilityLabel(value?: ApiNode['availability']) {
  return value === 'online'
    ? t('protection.sourceResources.nodeStatusOnline')
    : t('protection.sourceResources.nodeStatusOffline')
}

function availabilityTagType(value?: ApiNode['availability']): 'success' | 'danger' {
  return value === 'online' ? 'success' : 'danger'
}


const protectionMenus = useProtectionSideNav()

const nodeMenus = computed(() => protectionMenus.value)

const latestVersion = computed(() => latestAgentVersion.value)

async function loadLatestAgentVersion(signal?: AbortSignal) {
  if (!usesRealNodeList.value) {
    latestAgentVersion.value = null
    return
  }
  try {
    latestAgentVersion.value = await fetchLatestAgentVersion({ signal })
  } catch (e) {
    if (pageRequests.isAbortError(e)) return
    latestAgentVersion.value = null
  }
}

const isProxyNodesPage = computed(() => route.path.startsWith('/node/agents'))
const usesRealNodeList = computed(() => isProxyNodesPage.value)
const deployTarget = computed(() => '/node/nodes/deploy?role=proxy')
const deployButtonLabel = computed(() => t('nodesPage.deployProxy'))

const listSearchPlaceholder = computed(() =>
  isProxyNodesPage.value
    ? t('protection.listSearch.placeholder')
    : t('nodesPage.searchPlaceholder'),
)
const tableLoading = computed(() => !nodeListLoaded.value)

const lifecycleOps = useNodeLifecycleOps({
  role: () => 'proxy',
  t,
  onRefresh: () => load(),
  onLifecyclePatch: (patched) => {
    const byId = new Map(patched.map((node) => [node.id, node]))
    rows.value = rows.value.map((row) => {
      const next = byId.get(row.id)
      if (!next) return row
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
  },
})
const upgradeConfirmOpen = lifecycleOps.upgradeConfirmOpen
const upgradeConfirmPreview = lifecycleOps.upgradeConfirmPreview

function resolveBackupSourceLifecycleStatus(node: ApiNode) {
  return backupSourceLifecycleDisplay(lifecycleOps.resolveDisplayStatus(node))
}

function nodeRoleForPath(path: string): ApiNode['role'] | null {
  if (path.startsWith('/node/agents')) return 'proxy'
  if (path.startsWith('/node/gateways')) return 'gateway'
  return null
}

function applyRowsFromApi(data: ApiNode[]) {
  const role = nodeRoleForPath(route.path)
  rows.value = role ? data.filter((node) => node.role === role) : data
}

async function loadPagedNodes(role: 'proxy' | 'gateway', signal?: AbortSignal) {
  const result = await listNodesPaged(
    {
      role,
      page: pagination.page,
      page_size: pagination.pageSize,
      search: appliedSearch.value.trim() || undefined,
      search_field: searchField.value,
    },
    { signal },
  )
  rows.value = result.results
  pagination.count = result.count
}

async function load() {
  const signal = pageRequests.nextSignal('node-list')
  busy.value = true
  try {
    if (isProxyNodesPage.value) {
      await Promise.all([loadPagedNodes('proxy', signal), loadLatestAgentVersion(signal)])
      return
    }
    const role = nodeRoleForPath(route.path)
    const data = await listAllNodes(role ? { role } : undefined, { signal })
    if (role) {
      rows.value = data.filter((n) => n.role === role)
    } else {
      applyRowsFromApi(data)
    }
  } catch (e) {
    if (pageRequests.isAbortError(e)) return
    rows.value = []
    ElMessage.error({ message: apiErrorMessage(e), grouping: true })
  } finally {
    pageRequests.releaseSignal('node-list', signal)
    if (!signal.aborted) {
      nodeListLoaded.value = true
      busy.value = false
    }
  }
}

watch(
  () => route.query.openNode,
  () => {
    if (nodeListLoaded.value) tryOpenNodeFromQuery()
  },
)

onMounted(async () => {
  await load()
  lifecycleOps.restorePersisted()
  tryOpenNodeFromQuery()
})

const pagination = reactive({ page: 1, pageSize: 30, count: 0 })

watch(
  () => route.path,
  () => {
    resetSearch()
    selectedNodes.value = []
    pagination.page = 1
    nodeListLoaded.value = false
    load()
  },
)

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

const filteredRows = computed(() => {
  const q = appliedSearch.value.trim().toLowerCase()
  if (!q) return rows.value
  return rows.value.filter((r) => {
    const hay = searchField.value === 'ip' ? r.ip_address ?? '' : r.name
    return hay.toLowerCase().includes(q)
  })
})

watch(
  filteredRows,
  (list) => {
    if (usesRealNodeList.value) return
    pagination.count = list.length
    const maxPage = Math.max(1, Math.ceil(list.length / pagination.pageSize) || 1)
    if (pagination.page > maxPage) pagination.page = maxPage
  },
  { immediate: true },
)

function applySearch() {
  pagination.page = 1
  if (usesRealNodeList.value) void load()
}

watch(tableLoading, (loading) => {
  if (!loading) layoutTable()
})

watch(isProxyNodesPage, () => {
  requestAnimationFrame(layoutTable)
})

const tableRows = computed(() => {
  if (usesRealNodeList.value) return rows.value
  const start = (pagination.page - 1) * pagination.pageSize
  return filteredRows.value.slice(start, start + pagination.pageSize)
})

function onPaginationSizeChange() {
  pagination.page = 1
  if (usesRealNodeList.value) void load()
}

function onPaginationPageChange() {
  if (usesRealNodeList.value) void load()
}

function roleLabel(role: NodeRole | string) {
  if (role === 'gateway') return t('nodesPage.roleGateway')
  if (role === 'proxy') return t('nodesPage.roleProxy')
  if (role === 'agent') return t('nodesPage.roleAgent')
  if (role === 'source') return t('nodesPage.roleAgent')
  return String(role || '—')
}

function roleTagType(role: NodeRole | string): 'primary' | 'success' | 'warning' {
  if (role === 'gateway') return 'success'
  if (role === 'proxy') return 'warning'
  return 'primary'
}

function canUpgrade(row: ApiNode): boolean {
  return canRemoteAgentUpgrade(row.version, latestAgentVersion.value)
}

function needsUpgrade(row: ApiNode): boolean {
  return needsAgentUpgrade(row.version, latestAgentVersion.value)
}

function upgradeTargetVersion(row: ApiNode) {
  return needsUpgrade(row) ? latestVersion.value ?? '—' : '—'
}

function statusTagType(status: NodeStatus): 'success' | 'info' | 'danger' {
  if (status === 'online') return 'success'
  if (status === 'reconnecting') return 'info'
  return 'danger'
}

function statusLabel(_status: NodeStatus, row: ApiNode) {
  const status = debouncedNodeStatus(row)
  if (status === 'online') return t('nodesPage.statusOnline')
  if (status === 'reconnecting') return t('nodesPage.statusReconnecting')
  if (row.last_seen_at) {
    return t('nodesPage.statusOfflineWithLastSeen', { time: formatNodeDate(row.last_seen_at) })
  }
  return t('nodesPage.statusOffline')
}

type NodeMoreAction = 'rename' | 'upgrade' | 'maintenance' | 'remove'

async function handleNodeMoreAction(command: NodeMoreAction) {
  if (command === 'rename') {
    openRenameDialog()
    return
  }
  if (command === 'upgrade') {
    await onUpgradeSelected()
    return
  }
  if (command === 'maintenance') {
    openSelectedNodeMaintenance()
    return
  }
  if (command === 'remove') {
    await deleteSelectedNodes()
  }
}

function clearNodeTableSelection() {
  selectedNodes.value = []
  tableRef.value?.clearSelection()
}

async function onUpgradeSelected() {
  await afterOverlayDismiss()
  if (!usesRealNodeList.value) return
  const targets = selectedNodes.value.filter((row) => lifecycleOps.canUpgradeNode(row, canUpgrade))
  if (targets.length === 0) {
    ElMessage.warning({ message: t('nodeLifecycle.nothingEligible'), grouping: true })
    return
  }
  const started = await lifecycleOps.runBatch('upgrade', targets)
  if (started) clearNodeTableSelection()
}

const proxyDetailOpen = ref(false)
const proxyDetailId = ref<number | null>(null)
const proxyDetailInitialTab = ref<'basic' | 'performance' | 'nas' | 'maintenance'>('basic')

function clearOpenNodeQuery() {
  if (route.query.openNode == null) return
  const rest = { ...route.query }
  delete rest.openNode
  void router.replace({ path: route.path, query: rest })
}

function openNodeDetailDrawer(row: ApiNode) {
  proxyDetailId.value = row.id
  proxyDetailInitialTab.value = 'basic'
  proxyDetailOpen.value = true
}

function openSelectedNodeMaintenance() {
  const node = selectedNodes.value[0]
  if (!node || selectedNodes.value.length !== 1) return
  proxyDetailId.value = node.id
  proxyDetailInitialTab.value = 'maintenance'
  proxyDetailOpen.value = true
}

function onNodeDetailDrawerClose(open: boolean) {
  proxyDetailOpen.value = open
  if (!open) clearOpenNodeQuery()
}

function onNodeDetailDrawerSaved() {
  void load()
}

function tryOpenNodeFromQuery() {
  const raw = route.query.openNode
  if (raw == null || Array.isArray(raw)) return
  const id = Number(raw)
  if (!Number.isFinite(id)) return
  proxyDetailId.value = id
  proxyDetailInitialTab.value = 'basic'
  proxyDetailOpen.value = true
}



function ipLine(row: ApiNode) {
  return row.ip_address?.trim() || '—'
}

/* ---------- selection & rename ---------- */
const selectedNodes = ref<ApiNode[]>([])
const moreActionsOpen = ref(false)
const renameDialogOpen = ref(false)
const renameInput = ref('')
const repositoryServerAddressInput = ref('')
const renameTarget = ref<ApiNode | null>(null)

function onSelectionChange(selection: ApiNode[]) {
  selectedNodes.value = selection
}

const batchDisabled = computed(() => selectedNodes.value.length === 0)

const batchRenameDisabled = computed(() => selectedNodes.value.length !== 1)

const batchUpgradeDisabled = computed(() => {
  if (!usesRealNodeList.value) return true
  const upgradable = selectedNodes.value.filter((row) => lifecycleOps.canUpgradeNode(row, canUpgrade))
  return upgradable.length === 0
})

function openRenameDialog() {
  if (batchRenameDisabled.value) return
  const node = selectedNodes.value[0]
  if (!node) return
  renameTarget.value = node
  renameInput.value = node.name
  repositoryServerAddressInput.value = node.repository_server_address || ''
  renameDialogOpen.value = true
}

function closeRenameDialog() {
  renameDialogOpen.value = false
  renameTarget.value = null
}

async function renameNode(node: ApiNode, name: string) {
  await updateNode(node.id, {
    name,
    ...(node.role === 'proxy'
      ? { repository_server_address: repositoryServerAddressInput.value.trim() }
      : {}),
  })
}

const proxyDeleteBlockedOpen = ref(false)
const proxyDeleteBlockedName = ref('')
const proxyDeleteBlockedBindings = ref<NodeBindings | null>(null)
const deleteOpen = ref(false)
const deleteLoading = ref(false)
const pendingDeleteNodes = ref<ApiNode[]>([])
const pendingDeleteForce = ref(false)
const pendingDeleteAuthoritativeOffline = ref(new Map<number, boolean>())
const pendingDeleteUpgradeRequired = ref(new Set<number>())

function nodeDeleteDialogItem(row: ApiNode) {
  const authoritativeOffline = pendingDeleteAuthoritativeOffline.value.get(row.id)
  const status = authoritativeOffline == null
    ? debouncedNodeStatus(row)
    : authoritativeOffline ? 'offline' : 'online'
  const label = status === 'online'
    ? t('nodesPage.statusOnline')
    : status === 'reconnecting'
      ? t('nodesPage.statusReconnecting')
      : row.last_seen_at
        ? t('nodesPage.statusOfflineWithLastSeen', { time: formatNodeDate(row.last_seen_at) })
        : t('nodesPage.statusOffline')
  return {
    key: row.id,
    name: row.name,
    status: {
      label,
      tone: statusTagType(status),
    },
    description: pendingDeleteUpgradeRequired.value.has(row.id)
      ? t('nodesPage.deleteUpgradeRequired')
      : undefined,
  }
}

async function deleteSelectedNodes() {
  if (batchDisabled.value) return
  await afterOverlayDismiss()
  const targets = selectedNodes.value
  const proxyDelete = isProxyNodesPage.value

  if (proxyDelete) {
    for (const node of targets) {
      try {
        const bindings = await getNodeBindings(node.id)
        const blocked =
          bindings.source_nas_resources.length > 0
          || bindings.target_nas_repositories.length > 0
          || bindings.standalone_disk_repositories.length > 0
        if (blocked) {
          proxyDeleteBlockedName.value = node.name
          proxyDeleteBlockedBindings.value = bindings
          proxyDeleteBlockedOpen.value = true
          return
        }
      } catch {
        ElMessage.error({ message: t('nodesPage.proxyDeleteBindingsFailed'), grouping: true })
        return
      }
    }
  }

  try {
    const preview = await previewNodeOperationsBatch({
      kind: 'remove',
      nodeIds: targets.map((node) => node.id),
    })
    if (preview.eligible.length !== targets.length) {
      ElMessage.error({ message: t('nodesPage.deletePreflightBlocked'), grouping: true })
      return
    }
    pendingDeleteAuthoritativeOffline.value = new Map(
      preview.eligible.map((item) => [item.node_id, Boolean(item.offline)]),
    )
    pendingDeleteUpgradeRequired.value = new Set(
      preview.eligible.filter((item) => item.upgrade_required).map((item) => item.node_id),
    )
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err), grouping: true })
    return
  }

  pendingDeleteNodes.value = [...targets]
  pendingDeleteForce.value = false
  deleteOpen.value = true
}

async function executeNodeDelete() {
  const targets = [...pendingDeleteNodes.value]
  if (!targets.length) return
  deleteLoading.value = true
  try {
    const started = await lifecycleOps.runBatch('remove', targets, {
      skipConfirm: true,
      force: pendingDeleteForce.value,
    })
    if (started) {
      clearNodeTableSelection()
      deleteOpen.value = false
      pendingDeleteNodes.value = []
      pendingDeleteForce.value = false
      pendingDeleteAuthoritativeOffline.value = new Map()
      pendingDeleteUpgradeRequired.value = new Set()
    } else if (lifecycleOps.lastStartErrors.value.length) {
      const failedIds = new Set(
        lifecycleOps.lastStartErrors.value.map((item) => Number(item.node_id || 0)),
      )
      pendingDeleteNodes.value = targets.filter((node) => failedIds.has(node.id))
    }
  } finally {
    deleteLoading.value = false
  }
}

function cancelNodeDelete() {
  pendingDeleteNodes.value = []
  pendingDeleteForce.value = false
  pendingDeleteAuthoritativeOffline.value = new Map()
  pendingDeleteUpgradeRequired.value = new Set()
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
    await renameNode(node, name)
  } catch {
    ElMessage.error({ message: t('nodesPage.renameFailed'), grouping: true })
    return
  }

  ElMessage.success({ message: t('nodesPage.renameSuccess'), grouping: true })
  closeRenameDialog()
  selectedNodes.value = []
  await load()
}
</script>

<template>
  <ModulePage :menus="nodeMenus" body-fill>
    <div class="hfl-list-shell hfl-list-shell--fill">
      <div class="hfl-list-panel hfl-list-panel--fill">
        <div class="hfl-list-toolbar">
          <RouterLink :to="deployTarget" class="inline-flex">
            <ElButton type="primary">
              <Plus :size="16" />
              {{ deployButtonLabel }}
            </ElButton>
          </RouterLink>
          <ElDropdown trigger="click" popper-class="hfl-actions-dropdown" @visible-change="moreActionsOpen = $event" @command="handleNodeMoreAction">
            <ElButton>
              {{ t('nodesPage.btnMoreActions') }}
              <ChevronDown
                :size="16"
                class="hfl-list-more__chev"
                :class="{ 'hfl-list-more__chev--open': moreActionsOpen }"
              />
            </ElButton>
              <template #dropdown>
                <ElDropdownMenu>
                  <ElDropdownItem command="rename" :disabled="batchRenameDisabled">
                    <span class="el-dropdown-menu__item-content">
                      <Pencil :size="14" class="shrink-0" />
                      <span>{{ t('nodesPage.btnBatchRename') }}</span>
                    </span>
                  </ElDropdownItem>
                  <ElDropdownItem command="upgrade" :disabled="batchUpgradeDisabled">
                    <span class="el-dropdown-menu__item-content">
                      <ArrowUpCircle :size="14" class="shrink-0" />
                      <span>{{ t('nodesPage.actionUpgrade') }}</span>
                    </span>
                  </ElDropdownItem>
                  <ElDropdownItem command="maintenance" :disabled="batchRenameDisabled">
                    <span class="el-dropdown-menu__item-content">
                      <Wrench :size="14" class="shrink-0" />
                      <span>{{ t('nodeLifecycle.maintenanceCommands') }}</span>
                    </span>
                  </ElDropdownItem>
                  <ElDropdownItem
                    command="remove"
                    divided
                    class="el-dropdown-menu__item--danger"
                    :disabled="batchDisabled"
                  >
                    <span class="el-dropdown-menu__item-content">
                      <Trash2 :size="14" class="shrink-0" />
                      <span>{{ t('nodesPage.actionDelete') }}</span>
                    </span>
                  </ElDropdownItem>
                </ElDropdownMenu>
                </template>
          </ElDropdown>

          <div class="hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split">
            <ElInput
              v-model="search"
              clearable
              size="small"
              :placeholder="listSearchPlaceholder"
              class="hfl-list-search hfl-list-search-group"
              @keyup.enter="runFilterSearch"
              @clear="clearSearch"
            >
              <template #prepend>
                <ElSelect v-model="searchField" @change="handleSearchFieldChange">
                  <ElOption value="name" :label="t('protection.listSearchFields.name')" />
                  <ElOption value="ip" :label="t('protection.listSearchFields.ip')" />
                </ElSelect>
              </template>
              <template #prefix>
                <Search :size="16" class="hfl-list-search__icon" />
              </template>
            </ElInput>
            <div class="hfl-list-toolbar__utility">
              <ElButton
                class="hfl-refresh-button"
                :title="isProxyNodesPage ? t('protection.sourceResources.refresh') : t('nodesPage.refreshStatus')"
                :aria-label="isProxyNodesPage ? t('protection.sourceResources.refresh') : t('nodesPage.refreshStatus')"
                :disabled="busy"
                @click="load()"
              >
                <RefreshCw :size="16" :class="{ 'is-spinning': busy }" />
              </ElButton>
            </div>
          </div>
        </div>

        <NodeLifecycleBanner
          v-if="usesRealNodeList"
          :snapshot="lifecycleOps.snapshot"
          @cancel-queued="lifecycleOps.cancelQueued"
        />

        <div ref="tableBlockRef" class="hfl-list-table-block">
        <el-table
          v-table-overflow-title
          v-table-header-scroll-sync
          v-table-column-resize="isProxyNodesPage ? 'nodes.proxy.list' : 'nodes.agent.list'"
          ref="tableRef"
          v-loading="tableLoading"
          :data="tableRows"
          stripe
          row-key="id"
          class="hfl-list-table"
          :max-height="tableMaxHeight"
          :header-cell-style="TABLE_HEADER_STYLE"
          @scroll="onTableScroll"
          @selection-change="onSelectionChange"
        >
          <el-table-column
            type="selection"
            width="35"
            :fixed="isProxyNodesPage ? 'left' : undefined"
            :reserve-selection="true"
          />
          <el-table-column
            :label="isProxyNodesPage ? t('protection.sourceResources.colName') : t('nodesPage.colName')"
            :min-width="isProxyNodesPage ? 170 : 116"
            :fixed="isProxyNodesPage ? 'left' : undefined"
            class-name="hfl-table-name-col"
          >
            <template #default="{ row }">
              <button
                v-if="isProxyNodesPage"
                type="button"
                class="hfl-table-name-link hfl-table-name-link--full"
                @click="openNodeDetailDrawer(row)"
              >
                {{ row.name }}
              </button>
              <ResourceNameSummaryCell
                v-else
                :name="row.name"
                kind="host"
                :platform="nodeEnrollmentOs(row)"
                :show-icon="false"
                @open="openNodeDetailDrawer(row)"
              />
            </template>
          </el-table-column>
          <template v-if="isProxyNodesPage">
            <el-table-column :label="t('protection.sourceResources.colLifecycleStatus')" width="126" align="center" header-align="center">
              <template #default="{ row }">
                <div class="hfl-table-no-tooltip">
                  <NodeLifecycleStatusCell
                    :node="row"
                    :resolve-display-status="resolveBackupSourceLifecycleStatus"
                  />
                </div>
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.sourceResources.colHostIp')" min-width="120">
              <template #default="{ row }">
                <span>{{ ipLine(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="OS" min-width="105">
              <template #default="{ row }">
                <div class="source-os-cell source-os-cell--compact hfl-table-no-tooltip">
                  <span class="source-os-cell__icon-wrap">
                    <AgentPlatformBrandIcon :os="nodeEnrollmentOs(row)" />
                  </span>
                  <span class="source-os-cell__platform">{{ proxyPlatformLabel(row) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.sourceResources.colCpu')" min-width="76">
              <template #default="{ row }">
                <span :class="{ 'hfl-empty-mark': nodeCpuCores(row) == null }">{{ nodeCpuCores(row) != null ? t('protection.sourceResources.cpuCoresValue', { n: nodeCpuCores(row) }) : '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.sourceResources.colMemory')" min-width="90">
              <template #default="{ row }">
                <span :class="{ 'hfl-empty-mark': nodeMemoryTotalBytes(row) == null }">{{ nodeMemoryTotalBytes(row) != null ? formatNodeBytes(nodeMemoryTotalBytes(row)!) : '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.sourceResources.colDiskCount')" min-width="78">
              <template #default="{ row }">
                <span :class="{ 'hfl-empty-mark': nodeDiskCount(row) == null }">{{ nodeDiskCount(row) ?? '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.sourceResources.colCapacity')" min-width="170">
              <template #default="{ row }">
                <HflCapacityCell
                  :used-bytes="nodeDiskUsageParts(row).used"
                  :total-bytes="nodeDiskUsageParts(row).total"
                  variant="compact"
                  :format-bytes="formatNodeBytes"
                />
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.sourceResources.colConnectivity')" min-width="110" align="center" header-align="center">
              <template #default="{ row }">
                <div class="hfl-table-no-tooltip">
                  <ElTag :type="availabilityTagType(row.availability)" size="small">
                    {{ availabilityLabel(row.availability) }}
                  </ElTag>
                </div>
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.sourceResources.colVersion')" min-width="115">
              <template #default="{ row }">
                <NodeVersionCell
                  :node="row"
                  :version-label="row.version || '—'"
                  :resolve-version-display="lifecycleOps.resolveVersionDisplay"
                />
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.sourceResources.colRegisteredAt')" min-width="145">
              <template #default="{ row }">
                <span class="hfl-table-cell-time" :class="{ 'hfl-empty-mark': !row.created_at }">{{ formatNodeDate(row.created_at) }}</span>
              </template>
            </el-table-column>
          </template>
          <template v-else>
          <el-table-column :label="t('nodesPage.colIp')" min-width="148">
            <template #default="{ row }">
              <span class="nodes-ip-cell hfl-table-cell-mono">{{ ipLine(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('nodesPage.colOs')" min-width="168">
            <template #default="{ row }"><span :class="{ 'hfl-empty-mark': !row.os_name }">{{ row.os_name || '—' }}</span></template>
          </el-table-column>
          <el-table-column :label="t('nodesPage.colRole')" min-width="108">
            <template #default="{ row }">
              <ElTag
                :type="roleTagType(row.role)"
                size="small"
                effect="light"
                class="nodes-role-tag"
              >
                {{ roleLabel(row.role) }}
              </ElTag>
            </template>
          </el-table-column>
          <el-table-column :label="t('nodesPage.colStatus')" min-width="120">
            <template #default="{ row }">
              <NodeLifecycleStatusCell
                v-if="usesRealNodeList"
                :node="row"
                :resolve-display-status="lifecycleOps.resolveDisplayStatus"
              />
              <ElTag v-else :type="statusTagType(debouncedNodeStatus(row))" size="small">
                {{ statusLabel(row.status, row) }}
              </ElTag>
            </template>
          </el-table-column>
          <el-table-column :label="t('nodesPage.colVersion')" min-width="176">
            <template #default="{ row }">
              <NodeVersionCell
                v-if="usesRealNodeList"
                :node="row"
                :version-label="row.version || '—'"
                :resolve-version-display="lifecycleOps.resolveVersionDisplay"
              />
              <div v-else class="nodes-version-cell" :class="{ 'nodes-version-cell--stacked': needsUpgrade(row) && latestVersion }">
                <span class="nodes-version-cell__value" :class="{ 'hfl-empty-mark': !row.version }">{{ row.version || '—' }}</span>
                <ElTooltip
                  v-if="needsUpgrade(row) && latestVersion"
                  :content="t('nodesPage.latestVersionTip', { version: upgradeTargetVersion(row) })"
                  placement="top"
                >
                  <span class="nodes-version-cell__hint">
                    <TriangleAlert :size="14" stroke-width="2.1" />
                    <span>{{ t('nodesPage.versionUpgradeAvailable') }}</span>
                  </span>
                </ElTooltip>
              </div>
            </template>
          </el-table-column>
          </template>
          <template #empty>
            <el-empty
              :description="
                isProxyNodesPage
                  ? t('protection.sourceResources.emptyProxySources')
                  : t('nodesPage.emptyNodes')
              "
              :image-size="80"
            />
          </template>
        </el-table>
        </div>

        <div class="hfl-list-footer">
          <span v-if="selectedNodes.length > 0" class="hfl-list-footer__selected">
            {{ t('nodesPage.selectedCount', { n: selectedNodes.length }) }}
          </span>
          <HflPagination
            v-model:current-page="pagination.page"
            v-model:page-size="pagination.pageSize"
            class="hfl-list-footer__pagination"
            :total="pagination.count"
            @current-change="onPaginationPageChange"
            @size-change="onPaginationSizeChange"
          />
        </div>
      </div>
    </div>

    <!-- Rename Dialog -->
    <el-dialog
      v-model="renameDialogOpen"
      class="source-action-dialog"
      :title="t('nodesPage.renameTitle')"
      width="min(520px, calc(100vw - 32px))"
      align-center
      destroy-on-close
    >
      <ElForm
        label-position="top"
        class="source-action-dialog__form proxy-host-edit-form"
        @submit.prevent="submitRename"
      >
        <ElFormItem :label="t('nodesPage.renameLabel')" required>
          <ElInput
            v-model="renameInput"
            :placeholder="t('nodesPage.renamePlaceholder')"
            maxlength="128"
            show-word-limit
          />
        </ElFormItem>
        <ElFormItem
          v-if="renameTarget?.role === 'proxy'"
          :label="t('nodesPage.repositoryServerAddress')"
        >
          <ElInput
            v-model="repositoryServerAddressInput"
            :placeholder="t('nodesPage.repositoryServerAddressAutoPlaceholder', { ip: renameTarget?.ip_address || '—' })"
          />
          <p class="proxy-host-edit-form__hint">
            {{ t('nodesPage.repositoryServerAddressHint') }}
          </p>
        </ElFormItem>
      </ElForm>
      <template #footer>
        <el-button @click="closeRenameDialog">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="submitRename">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <ProxyNodeDetailDrawer
      :model-value="proxyDetailOpen"
      :node-id="proxyDetailId"
      :initial-tab="proxyDetailInitialTab"
      @update:model-value="onNodeDetailDrawerClose"
      @saved="onNodeDetailDrawerSaved"
    />
    <ProxyHostDeleteBlockedDialog
      v-model="proxyDeleteBlockedOpen"
      :proxy-name="proxyDeleteBlockedName"
      :bindings="proxyDeleteBlockedBindings"
    />
    <NodeLifecycleUpgradeConfirmDialog
      v-model="upgradeConfirmOpen"
      :preview="upgradeConfirmPreview"
      @confirm="lifecycleOps.resolveUpgradeConfirm()"
      @cancel="lifecycleOps.cancelUpgradeConfirm()"
    />
    <DangerConfirmDialog
      v-model="deleteOpen"
      :title="pendingDeleteForce ? t('nodesPage.proxyDeleteForceTitle') : (isProxyNodesPage ? t('nodesPage.proxyDeleteTitle') : t('nodesPage.deleteTitle'))"
      :message="pendingDeleteForce
        ? t('nodesPage.proxyDeleteForceConfirm', { name: pendingDeleteNodes[0]?.name || '' })
        : (isProxyNodesPage && pendingDeleteNodes.length === 1
          ? t('nodesPage.proxyDeleteConfirm', { name: pendingDeleteNodes[0]?.name || '' })
          : isProxyNodesPage
            ? t('nodesPage.proxyDeleteSelectedConfirm', { n: pendingDeleteNodes.length })
            : t('nodesPage.deleteSelectedConfirm', { n: pendingDeleteNodes.length }))"
      :items="pendingDeleteNodes.map(nodeDeleteDialogItem)"
      confirm-mode="keyword"
      :confirm-keyword="pendingDeleteForce ? 'FORCE CLEANUP' : t('common.deleteKeyword')"
      :cancel-text="t('common.cancel')"
      :confirm-text="pendingDeleteForce ? t('nodesPage.proxyDeleteForceAction') : t('common.delete')"
      :loading="deleteLoading"
      @confirm="executeNodeDelete"
      @cancel="cancelNodeDelete"
    >
      <div v-if="isProxyNodesPage" class="node-delete-force-option">
        <ElCheckbox v-model="pendingDeleteForce">
          {{ t('nodesPage.proxyDeleteForceAction') }}
        </ElCheckbox>
        <p>{{ t('nodesPage.proxyDeleteForceNote') }}</p>
      </div>
    </DangerConfirmDialog>
  </ModulePage>
</template>

<style scoped>
.nodes-role-tag {
  font-weight: 600;
}

.node-delete-force-option {
  margin-top: 16px;
  padding: 12px 14px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-fill-color-lighter);
}

.node-delete-force-option p {
  margin: 4px 0 0 24px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.proxy-host-edit-form {
  display: grid;
  gap: 20px;
}

.proxy-host-edit-form__hint {
  width: 100%;
  margin: 6px 0 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.5;
}

</style>
