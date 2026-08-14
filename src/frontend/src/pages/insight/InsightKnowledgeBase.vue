<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { lensKnowledgePath } from '../../lib/lensEngineRoutes'
import { useI18n } from 'vue-i18n'
import { ChevronDown, Pencil, Plus, RefreshCw, Search, Trash2 } from 'lucide-vue-next'
import { ElMessage, type ElTable } from 'element-plus'
import { useListTableLayout } from '../../composables/useListTableLayout'
import { useListSearch } from '../../composables/useListSearch'
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
import DangerConfirmDialog from '../../components/DangerConfirmDialog.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const isPlatformEngine = computed(() => route.path.startsWith('/platform-ops/engine'))

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
const { appliedSearch, clearSearch } = useListSearch(search)
const statusFilter = ref<string>('')
const detailOpen = ref(false)
const detailRow = ref<LensKnowledgeSource | null>(null)
const deleteOpen = ref(false)
const deleteLoading = ref(false)
const deleteTarget = ref<LensKnowledgeSource | null>(null)
const selectedRows = ref<LensKnowledgeSource[]>([])
const moreActionsOpen = ref(false)

const tableRef = ref<InstanceType<typeof ElTable> | null>(null)
const tableBlockRef = ref<HTMLElement | null>(null)
const { tableMaxHeight, layoutTable, handleTableScroll } = useListTableLayout(tableRef, tableBlockRef)

const filteredRows = computed(() => {
  const q = appliedSearch.value.trim().toLowerCase()
  return rows.value.filter((row) => {
    if (statusFilter.value && row.status !== statusFilter.value) return false
    if (!q) return true
    const hay = [
      row.name,
      row.source_path,
      row.gateway_name,
      row.ingest_summary,
      row.status_detail,
      sourceTypeLabel(row),
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return hay.includes(q)
  })
})

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
      rows.value = await listKnowledgeSources()
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

async function syncRow(row: LensKnowledgeSource) {
  if (rowSyncDisabled(row)) return
  try {
    const updated = await syncKnowledgeSource(row.id)
    rows.value = rows.value.map((item) => (item.id === row.id ? updated : item))
    if (detailRow.value?.id === row.id) detailRow.value = updated
    ElMessage.success({ message: t('insight.kb.syncStarted'), grouping: true })
    startSyncPolling()
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
  }
}

async function refreshRowsQuietly() {
  if (!bridgeReady.value) return
  try {
    rows.value = await listKnowledgeSources()
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

function deleteRow(row: LensKnowledgeSource) {
  deleteTarget.value = row
  deleteOpen.value = true
}

async function confirmDelete() {
  const row = deleteTarget.value
  if (!row) return
  deleteLoading.value = true
  try {
    await deleteKnowledgeSource(row.id)
    ElMessage.success({ message: t('insight.kb.deleteSuccess'), grouping: true })
    selectedRows.value = []
    await load()
    deleteOpen.value = false
    deleteTarget.value = null
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
  } finally {
    deleteLoading.value = false
  }
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

async function syncSelected() {
  const row = singleSelected.value
  if (!row) return
  await syncRow(row)
}

async function deleteSelected() {
  const row = singleSelected.value
  if (!row) return
  await deleteRow(row)
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
        <ElButton
          type="primary"
          :disabled="!bridgeReady"
          @click="openCreate"
        >
          <Plus :size="16" />
          {{ isPlatformEngine ? t('platformOps.engineActions.addKnowledgeSource') : t('insight.kb.btnAdd') }}
        </ElButton>

        <ElDropdown
          trigger="click"
          popper-class="hfl-actions-dropdown"
          @visible-change="moreActionsOpen = $event"
        >
          <ElButton :disabled="!bridgeReady">
            {{ isPlatformEngine ? t('platformOps.engineActions.knowledgeSourceActions') : t('insight.kb.btnMoreActions') }}
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

        <div class="hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split">
          <ElSelect
            v-model="statusFilter"
            clearable
            size="small"
            :placeholder="t('insight.kb.filterRunState')"
            style="width: 160px"
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
            size="small"
            :placeholder="t('insight.kb.searchPlaceholder')"
            class="hfl-list-search"
            @clear="clearSearch"
          >
            <template #prefix>
              <Search :size="14" />
            </template>
          </ElInput>
          <div class="hfl-list-toolbar__utility">
            <ElButton
              class="hfl-refresh-button"
              :title="t('common.refresh')"
              :loading="loading"
              @click="load"
            >
              <RefreshCw :size="16" />
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
          :data="filteredRows"
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
            min-width="220"
            fixed="left"
            class-name="hfl-table-name-col"
          >
            <template #default="{ row }">
              <button
                type="button"
                class="hfl-table-name-link hfl-table-name-link--full"
                @click="openDetail(row)"
              >
                <div class="ks-identity">
                  <strong>{{ row.name }}</strong>
                  <span>{{ row.source_path }}</span>
                </div>
              </button>
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
              :description="bridgeReady ? t('insight.kb.emptyList') : t('insight.shared.bridgeNotReady')"
              :image-size="80"
            />
          </template>
        </ElTable>
      </div>
    </div>

    <InsightKnowledgeSourceDetailDrawer
      v-model:open="detailOpen"
      :row="detailRow"
      @updated="onDetailUpdated"
      @edit="onDetailEdit"
    />
    <DangerConfirmDialog
      v-model="deleteOpen"
      :title="t('insight.kb.deleteTitle')"
      :message="deleteTarget ? t('insight.kb.deleteConfirm', { name: deleteTarget.name }) : ''"
      :items="deleteTarget ? [{ key: deleteTarget.id, name: deleteTarget.name }] : []"
      :cancel-text="t('common.cancel')"
      :confirm-text="t('common.delete')"
      :loading="deleteLoading"
      @confirm="confirmDelete"
      @cancel="deleteTarget = null"
    />
  </div>
</template>

<style scoped>
.ks-identity {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  text-align: left;
}

.ks-identity strong {
  min-width: 0;
  overflow: hidden;
  color: rgb(15 23 42);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ks-identity span {
  color: rgb(100 116 139);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ks-list-muted {
  color: rgb(148 163 184);
}
</style>
