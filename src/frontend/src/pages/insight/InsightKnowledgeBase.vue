<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { lensKnowledgePath } from '../../lib/lensEngineRoutes'
import { useI18n } from 'vue-i18n'
import { ChevronDown, Pencil, Plus, RefreshCw, Search, Trash2 } from 'lucide-vue-next'
import { ElMessage, type ElTable } from 'element-plus'
import { useListTableLayout } from '../../composables/useListTableLayout'
import { useListSearch } from '../../composables/useListSearch'
import { usePlatformEngineListSearch } from '../../composables/usePlatformEngineListSearch'
import { apiErrorMessage } from '../../lib/api'
import { LIST_ROUTE_REFRESH_KEY, stripListRefreshQuery } from '../../lib/listRouteRefresh'
import { lifecycleStatusTagAttrs } from '../../lib/statusTag'
import {
  deleteKnowledgeSource,
  fetchLensHealth,
  listKnowledgeSources,
  syncKnowledgeSource,
  type LensKnowledgeSource,
} from '../../lib/lensApi'
import InsightKnowledgeSourceDetailDrawer from './InsightKnowledgeSourceDetailDrawer.vue'
import HflTypeLabel from '../../components/HflTypeLabel.vue'
import DangerConfirmDialog, {
  type DangerConfirmItem,
  type DangerLevel,
} from '../../components/DangerConfirmDialog.vue'
import PlatformOpsPagination from '../../platform-ops/components/PlatformOpsPagination.vue'

type KnowledgeSourceAction = 'sync' | 'delete'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const {
  listSearchPlaceholder,
  listSearchClass,
  listSearchSize,
} = usePlatformEngineListSearch('insight.kb.searchPlaceholder', 'knowledgeSources')

const TABLE_HEADER_STYLE: Record<string, string> = {
  background: 'rgba(248, 250, 252, 0.96)',
  color: 'rgb(71 85 105)',
  fontWeight: '600',
  whiteSpace: 'nowrap',
}

const loading = ref(false)
const bridgeReady = ref(false)
const rows = ref<LensKnowledgeSource[]>([])
const search = ref('')
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })
const { appliedSearch, clearSearch } = useListSearch(search, () => {
  pagination.page = 1
})
const statusFilter = ref<string>('')
const filterOrgKey = ref('')
const knownOrganizations = ref<Array<{ key: string; name: string }>>([])
const detailOpen = ref(false)
const detailRow = ref<LensKnowledgeSource | null>(null)
const actionType = ref<KnowledgeSourceAction | null>(null)
const actionTarget = ref<LensKnowledgeSource | null>(null)
const actionLoading = ref(false)
const selectedRows = ref<LensKnowledgeSource[]>([])
const moreActionsOpen = ref(false)

const actionOpen = computed(() => Boolean(actionType.value && actionTarget.value))
const actionLevel = computed<DangerLevel>(() => (actionType.value === 'delete' ? 'high' : 'low'))
const actionTitle = computed(() => {
  if (actionType.value === 'sync') return t('insight.kb.syncTitle')
  if (actionType.value === 'delete') return t('insight.kb.deleteTitle')
  return ''
})
const actionMessage = computed(() => {
  const row = actionTarget.value
  if (!row || !actionType.value) return ''
  if (actionType.value === 'sync') return t('insight.kb.syncConfirmMessage')
  if (row.organization_key) {
    return t('insight.kb.deleteConfirmPlatform', { org: organizationConfirmLabel(row) })
  }
  return t('insight.kb.deleteConfirm')
})
const actionConfirmText = computed(() => {
  if (actionType.value === 'sync') return t('insight.kb.syncConfirm')
  if (actionType.value === 'delete') return t('common.delete')
  return ''
})
const actionItems = computed<DangerConfirmItem[]>(() => {
  const row = actionTarget.value
  if (!row) return []
  return [{
    key: row.id,
    name: row.name,
    status: {
      label: statusLabel(row.status),
      tone: statusTone(row.status),
    },
  }]
})
const actionContext = computed(() => JSON.stringify([
  actionType.value,
  actionTarget.value?.id || '',
]))

const tableRef = ref<InstanceType<typeof ElTable> | null>(null)
const tableBlockRef = ref<HTMLElement | null>(null)
const { tableMaxHeight, layoutTable, handleTableScroll } = useListTableLayout(tableRef, tableBlockRef)

const organizationFilterOptions = computed(() => {
  const options = knownOrganizations.value
  const selected = filterOrgKey.value.trim()
  if (!selected || options.some((org) => org.key === selected)) return options
  return [{ key: selected, name: selected }, ...options]
})

const filteredRows = computed(() => {
  const q = appliedSearch.value.trim().toLowerCase()
  const orgKey = filterOrgKey.value.trim()
  return rows.value.filter((row) => {
    if (statusFilter.value && row.status !== statusFilter.value) return false
    if (orgKey && String(row.organization_key || '').trim() !== orgKey) return false
    if (!q) return true
    const hay = [
      row.name,
      row.source_path,
      row.gateway_name,
      row.organization_key,
      row.organization_name,
      row.ingest_summary,
      row.status_detail,
      sourceTypeLabel(row),
      ...((row.source_scopes_json || []).map((scope) => scope.source_path)),
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return hay.includes(q)
  })
})

const visibleRows = computed(() => {
  const start = (pagination.page - 1) * pagination.pageSize
  return filteredRows.value.slice(start, start + pagination.pageSize)
})

watch(
  filteredRows,
  (list) => {
    pagination.count = list.length
    const maxPage = Math.max(1, Math.ceil(list.length / pagination.pageSize) || 1)
    if (pagination.page > maxPage) pagination.page = maxPage
  },
  { immediate: true },
)

watch([statusFilter, filterOrgKey], () => {
  pagination.page = 1
})

function onPaginationPageChange() {
  layoutTable()
}

function onPaginationSizeChange() {
  pagination.page = 1
  layoutTable()
}

function organizationLabel(row: LensKnowledgeSource) {
  return row.organization_name?.trim() || ''
}

function organizationConfirmLabel(row: LensKnowledgeSource) {
  return organizationLabel(row) || row.organization_key?.trim() || '—'
}

function organizationId(row: LensKnowledgeSource) {
  return Number(row.organization_id || 0) || null
}

function rememberOrganizations(list: LensKnowledgeSource[]) {
  const byKey = new Map(knownOrganizations.value.map((org) => [org.key, org]))
  for (const row of list) {
    const key = String(row.organization_key || '').trim()
    if (!key) continue
    byKey.set(key, {
      key,
      name: String(row.organization_name || key).trim() || key,
    })
  }
  knownOrganizations.value = [...byKey.values()].sort(
    (a, b) => a.name.localeCompare(b.name) || a.key.localeCompare(b.key),
  )
}

function sourcePathEntries(row: LensKnowledgeSource): string[] {
  const scopes = (row.source_scopes_json || [])
    .map((scope) => scope.source_path?.trim())
    .filter((path): path is string => Boolean(path))
  const primary = row.source_path?.trim()
  const unique: string[] = []
  for (const path of [primary, ...scopes]) {
    if (!path || unique.includes(path)) continue
    unique.push(path)
  }
  return unique
}

function sourcePathLabel(row: LensKnowledgeSource) {
  const paths = sourcePathEntries(row)
  if (paths.length === 0) return ''
  if (paths.length === 1) return paths[0]!
  return t('insight.kb.sourcePathExtra', { path: paths[0], n: paths.length - 1 })
}

function sourcePathTitle(row: LensKnowledgeSource) {
  return sourcePathEntries(row).join('\n')
}

const batchDisabled = computed(() => selectedRows.value.length === 0)
const singleSelected = computed(() => (selectedRows.value.length === 1 ? selectedRows.value[0]! : null))

const syncingPollTimer = ref<ReturnType<typeof setInterval> | null>(null)

const hasSyncingRows = computed(() => rows.value.some((row) => row.status === 'syncing'))

function statusLabel(status: string) {
  if (status === 'ready') return t('insight.kb.statusReady')
  if (status === 'syncing') return t('insight.kb.statusSyncing')
  if (status === 'degraded') return t('insight.kb.statusDegraded')
  if (status === 'error') return t('insight.kb.statusError')
  if (status === 'paused') return t('insight.kb.statusPaused')
  if (status === 'learning' || status === 'provisioning') return t('insight.kb.statusSyncing')
  return status
}

function statusTone(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'ready') return 'success'
  if (status === 'error') return 'danger'
  if (status === 'degraded' || status === 'paused') return 'warning'
  return 'info'
}

function rowSyncDisabled(row: LensKnowledgeSource) {
  return row.status === 'syncing'
}

function versionLabel(row: LensKnowledgeSource) {
  if (row.linked_version_mode === 'latest') return t('insight.kb.versionLatest')
  return t('insight.kb.versionPinned', { id: row.pinned_snapshot_id ?? '—' })
}

function sourceTypeLabel(row: LensKnowledgeSource) {
  return row.backup_source_snapshot_id
    ? t('insight.kb.sourceTypeBackup')
    : t('insight.kb.sourceTypeGatewayLocal')
}

async function load() {
  loading.value = true
  try {
    const health = await fetchLensHealth()
    bridgeReady.value = Boolean(health.lens.configured && health.lens.authenticated)
    if (bridgeReady.value) {
      const list = await listKnowledgeSources()
      rows.value = list
      rememberOrganizations(list)
      if (hasSyncingRows.value) startSyncPolling()
    } else {
      rows.value = []
    }
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.loadFailed')), grouping: true })
  } finally {
    loading.value = false
    layoutTable()
  }
}

function openCreate() {
  router.push(lensKnowledgePath() + '/add')
}

function openEdit(row: LensKnowledgeSource) {
  router.push(`${lensKnowledgePath()}/${row.id}/edit`)
}

function openDetail(row: LensKnowledgeSource) {
  detailRow.value = row
  detailOpen.value = true
}

function onSelectionChange(selection: LensKnowledgeSource[]) {
  selectedRows.value = selection
}

function onDetailUpdated(row: LensKnowledgeSource) {
  rows.value = rows.value.map((item) => (item.id === row.id ? row : item))
  detailRow.value = row
}

function requestAction(action: KnowledgeSourceAction, row: LensKnowledgeSource) {
  if (actionLoading.value) return
  if (action === 'sync' && rowSyncDisabled(row)) return
  actionType.value = action
  actionTarget.value = row
}

function closeAction() {
  if (actionLoading.value) return
  actionType.value = null
  actionTarget.value = null
}

async function confirmAction() {
  const action = actionType.value
  const row = actionTarget.value
  if (!action || !row || actionLoading.value) return
  actionLoading.value = true
  try {
    if (action === 'sync') {
      const updated = await syncKnowledgeSource(row.id)
      rows.value = rows.value.map((item) => (item.id === row.id ? updated : item))
      if (detailRow.value?.id === row.id) detailRow.value = updated
      ElMessage.success({ message: t('insight.kb.syncStarted'), grouping: true })
      startSyncPolling()
    } else {
      await deleteKnowledgeSource(row.id)
      ElMessage.success({ message: t('insight.kb.deleteSuccess'), grouping: true })
      selectedRows.value = []
      await load()
    }
    actionType.value = null
    actionTarget.value = null
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
  } finally {
    actionLoading.value = false
  }
}

async function refreshRowsQuietly() {
  if (!bridgeReady.value) return
  try {
    const list = await listKnowledgeSources()
    rows.value = list
    rememberOrganizations(list)
    if (detailRow.value) {
      detailRow.value = rows.value.find((row) => row.id === detailRow.value!.id) ?? detailRow.value
    }
  } catch {
    // ignore background poll errors
  }
}

function stopSyncPolling() {
  if (syncingPollTimer.value != null) {
    clearInterval(syncingPollTimer.value)
    syncingPollTimer.value = null
  }
}

function startSyncPolling() {
  if (syncingPollTimer.value != null) return
  syncingPollTimer.value = setInterval(() => {
    void refreshRowsQuietly().then(() => {
      if (!hasSyncingRows.value) stopSyncPolling()
    })
  }, 4000)
}

function onDetailEdit(id: number) {
  const row = rows.value.find((item) => item.id === id) ?? detailRow.value
  if (row) openEdit(row)
}

function editSelected() {
  const row = singleSelected.value
  if (!row) return
  openEdit(row)
}

function syncSelected() {
  const row = singleSelected.value
  if (!row) return
  requestAction('sync', row)
}

function deleteSelected() {
  const row = singleSelected.value
  if (!row) return
  requestAction('delete', row)
}

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

onMounted(() => {
  void load()
})

onUnmounted(() => {
  stopSyncPolling()
})

watch(hasSyncingRows, (syncing) => {
  if (syncing) startSyncPolling()
  else stopSyncPolling()
})
</script>

<template>
  <div class="hfl-list-shell hfl-list-shell--fill">
    <div class="hfl-list-panel hfl-list-panel--fill">
      <div class="hfl-list-toolbar">
        <div class="hfl-list-toolbar__actions">
          <ElButton
            type="primary"
            :disabled="!bridgeReady"
            @click="openCreate"
          >
            <Plus :size="16" />
            {{ t('platformOps.engineActions.addKnowledgeSource') }}
          </ElButton>

          <ElDropdown
            trigger="click"
            popper-class="hfl-actions-dropdown"
            @visible-change="moreActionsOpen = $event"
          >
          <ElButton :disabled="!bridgeReady">
            {{ t('platformOps.engineActions.knowledgeSourceActions') }}
            <ChevronDown
              :size="16"
              class="hfl-list-more__chev"
              :class="{ 'hfl-list-more__chev--open': moreActionsOpen }"
            />
          </ElButton>
          <template #dropdown>
            <ElDropdownMenu>
              <ElDropdownItem
                :disabled="batchDisabled || !singleSelected"
                @click="editSelected"
              >
                <span class="el-dropdown-menu__item-content">
                  <Pencil
                    :size="14"
                    class="shrink-0"
                  />
                  <span>{{ t('common.edit') }}</span>
                </span>
              </ElDropdownItem>
              <ElDropdownItem
                :disabled="batchDisabled || !singleSelected || (singleSelected && rowSyncDisabled(singleSelected))"
                @click="syncSelected"
              >
                <span class="el-dropdown-menu__item-content">
                  <RefreshCw
                    :size="14"
                    class="shrink-0"
                  />
                  <span>{{ t('insight.kb.sync') }}</span>
                </span>
              </ElDropdownItem>
              <ElDropdownItem
                divided
                class="el-dropdown-menu__item--danger"
                :disabled="batchDisabled || !singleSelected"
                @click="deleteSelected"
              >
                <span class="el-dropdown-menu__item-content">
                  <Trash2
                    :size="14"
                    class="shrink-0"
                  />
                  <span>{{ t('common.delete') }}</span>
                </span>
              </ElDropdownItem>
            </ElDropdownMenu>
          </template>
          </ElDropdown>
        </div>

        <div class="hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split">
          <ElSelect
            v-model="filterOrgKey"
            clearable
            filterable
            size="small"
            class="ks-org-filter"
            :placeholder="t('insight.kb.orgFilterPlaceholder')"
            :aria-label="t('insight.kb.orgFilterPlaceholder')"
          >
            <ElOption
              v-for="org in organizationFilterOptions"
              :key="org.key"
              :value="org.key"
              :label="org.name || org.key"
            />
          </ElSelect>
          <ElSelect
            v-model="statusFilter"
            clearable
            size="small"
            class="ks-status-filter"
            :placeholder="t('insight.kb.filterRunState')"
            :aria-label="t('insight.kb.filterRunState')"
          >
            <ElOption
              value="ready"
              :label="t('insight.kb.statusReady')"
            />
            <ElOption
              value="syncing"
              :label="t('insight.kb.statusSyncing')"
            />
            <ElOption
              value="degraded"
              :label="t('insight.kb.statusDegraded')"
            />
            <ElOption
              value="paused"
              :label="t('insight.kb.statusPaused')"
            />
            <ElOption
              value="error"
              :label="t('insight.kb.statusError')"
            />
          </ElSelect>
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
              :title="t('common.refresh')"
              :disabled="loading"
              @click="load"
            >
              <RefreshCw
                :size="16"
                :class="{ 'is-spinning': loading }"
              />
            </ElButton>
          </div>
        </div>
      </div>

      <div
        ref="tableBlockRef"
        class="hfl-list-table-block"
      >
        <ElTable
          ref="tableRef"
          v-table-overflow-title
          v-loading="loading"
          :data="visibleRows"
          :max-height="tableMaxHeight"
          :header-cell-style="TABLE_HEADER_STYLE"
          row-key="id"
          stripe
          class="hfl-list-table"
          @scroll="handleTableScroll"
          @selection-change="onSelectionChange"
        >
          <ElTableColumn
            type="selection"
            width="35"
            fixed="left"
          />
          <ElTableColumn
            :label="t('insight.kb.colName')"
            min-width="180"
            fixed="left"
            class-name="hfl-table-name-col"
          >
            <template #default="{ row }">
              <button
                type="button"
                class="hfl-table-name-link hfl-table-name-link--full"
                :title="row.name"
                @click="openDetail(row)"
              >
                {{ row.name }}
              </button>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('insight.kb.colSourcePath')"
            min-width="200"
            class-name="ks-source-path-col"
          >
            <template #default="{ row }">
              <span
                class="ks-source-path"
                :class="{ 'hfl-empty-mark': !sourcePathLabel(row) }"
                :title="sourcePathTitle(row) || undefined"
              >{{ sourcePathLabel(row) || '—' }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('insight.kb.colOrganization')"
            min-width="160"
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
          </ElTableColumn>
          <ElTableColumn
            :label="t('insight.kb.colSourceType')"
            min-width="140"
          >
            <template #default="{ row }">
              <HflTypeLabel :label="sourceTypeLabel(row)" />
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('insight.kb.colGateway')"
            prop="gateway_name"
            min-width="140"
          />
          <ElTableColumn
            :label="t('insight.kb.colLinkedVersion')"
            min-width="120"
          >
            <template #default="{ row }">
              <span v-if="row.backup_source_snapshot_id">{{ versionLabel(row) }}</span>
              <span
                v-else
                class="hfl-empty-mark"
              >—</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('insight.kb.colRetrieval')"
            prop="ingest_summary"
            min-width="140"
          />
          <ElTableColumn
            :label="t('insight.kb.colLearnStatus')"
            min-width="110"
          >
            <template #default="{ row }">
              <ElTag
                v-bind="lifecycleStatusTagAttrs(row.status)"
                size="small"
              >
                {{ statusLabel(row.status) }}
              </ElTag>
            </template>
          </ElTableColumn>
          <template #empty>
            <ElEmpty
              :description="bridgeReady ? t('insight.kb.emptyPlatform') : t('insight.shared.bridgeNotReady')"
              :image-size="80"
            />
          </template>
        </ElTable>
      </div>

      <div class="hfl-list-footer">
        <span
          v-if="selectedRows.length > 0"
          class="hfl-list-footer__selected"
        >
          {{ t('nodesPage.selectedCount', { n: selectedRows.length }) }}
        </span>
        <PlatformOpsPagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.pageSize"
          :total="pagination.count"
          @current-change="onPaginationPageChange"
          @size-change="onPaginationSizeChange"
        />
      </div>
    </div>

    <InsightKnowledgeSourceDetailDrawer
      v-model:open="detailOpen"
      :row="detailRow"
      @updated="onDetailUpdated"
      @edit="onDetailEdit"
    />
    <DangerConfirmDialog
      v-if="actionOpen"
      :model-value="true"
      class="ks-action-confirm"
      width="640px"
      :title="actionTitle"
      :level="actionLevel"
      :message="actionMessage"
      :items="actionItems"
      :items-heading="t('nodesPage.selectedCount', { n: actionItems.length })"
      :item-name-label="t('insight.kb.entityName')"
      :item-status-label="t('common.dangerItemStatus')"
      :show-item-details="false"
      :confirmation-context="actionContext"
      :confirm-mode="actionType === 'delete' ? 'keyword' : 'single'"
      :confirm-keyword="t('common.deleteKeyword')"
      :confirm-keyword-placeholder="t('common.deleteKeyword')"
      :confirm-keyword-hint="t('common.dangerConfirmKeywordHint', { keyword: t('common.deleteKeyword') })"
      :cancel-text="t('common.cancel')"
      :confirm-text="actionConfirmText"
      :loading="actionLoading"
      :confirm-disabled="actionLoading || !actionTarget"
      @confirm="confirmAction"
      @cancel="closeAction"
    />
  </div>
</template>

<style scoped>
.ks-org-filter {
  width: 200px;
}

.ks-status-filter {
  width: 140px;
}

.ks-source-path {
  display: block;
  min-width: 0;
  overflow: hidden;
  color: rgb(71 85 105);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.ks-source-path-col .cell) {
  min-width: 0;
}

.ks-list-muted {
  color: rgb(148 163 184);
}
</style>
