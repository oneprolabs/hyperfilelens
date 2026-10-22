<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { lensMcpPath } from '../../lib/lensEngineRoutes'
import { useI18n } from 'vue-i18n'
import {
  ChevronDown,
  CirclePlay,
  CircleStop,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
} from 'lucide-vue-next'
import { ElMessage, type ElTable } from 'element-plus'
import { useListTableLayout } from '../../composables/useListTableLayout'
import { useListSearch } from '../../composables/useListSearch'
import { usePlatformEngineListSearch } from '../../composables/usePlatformEngineListSearch'
import { apiErrorMessage } from '../../lib/api'
import { LIST_ROUTE_REFRESH_KEY, stripListRefreshQuery } from '../../lib/listRouteRefresh'
import {
  deleteLensMcpServer,
  fetchLensHealth,
  listLensMcpServers,
  updateLensMcpServer,
  type LensHealth,
  type LensMcpServer,
} from '../../lib/lensApi'
import InsightMcpServerDetailDrawer from './InsightMcpServerDetailDrawer.vue'
import HflTypeLabel from '../../components/HflTypeLabel.vue'
import HflBooleanStatusTag from '../../components/HflBooleanStatusTag.vue'
import DangerConfirmDialog, {
  type DangerConfirmItem,
  type DangerLevel,
} from '../../components/DangerConfirmDialog.vue'
import PlatformOpsPagination from '../../platform-ops/components/PlatformOpsPagination.vue'

type McpLifecycleAction = 'enable' | 'disable' | 'delete'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const {
  listSearchPlaceholder,
  listSearchClass,
  listSearchSize,
} = usePlatformEngineListSearch('insight.mcpServers.searchPlaceholder', 'mcpServers')

const TABLE_HEADER_STYLE: Record<string, string> = {
  background: 'rgba(248, 250, 252, 0.96)',
  color: 'rgb(71 85 105)',
  fontWeight: '600',
  whiteSpace: 'nowrap',
}

const loading = ref(false)
const health = ref<LensHealth | null>(null)
const rows = ref<LensMcpServer[]>([])
const search = ref('')
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })
const { appliedSearch, clearSearch } = useListSearch(search, () => {
  pagination.page = 1
})
const selectedRows = ref<LensMcpServer[]>([])
const moreActionsOpen = ref(false)
const detailOpen = ref(false)
const detailRow = ref<LensMcpServer | null>(null)
const lifecycleAction = ref<McpLifecycleAction | null>(null)
const lifecycleTarget = ref<LensMcpServer | null>(null)
const lifecycleLoading = ref(false)

const tableRef = ref<InstanceType<typeof ElTable> | null>(null)
const tableBlockRef = ref<HTMLElement | null>(null)
const { tableMaxHeight, layoutTable, handleTableScroll } = useListTableLayout(tableRef, tableBlockRef)

const bridgeReady = computed(
  () => health.value?.lens?.configured && health.value?.lens?.authenticated,
)

const filteredRows = computed(() => {
  const q = appliedSearch.value.trim().toLowerCase()
  if (!q) return rows.value
  return rows.value.filter((row) => {
    const hay = [
      row.name,
      transportLabel(row.transport),
      row.endpoint,
      row.enabled ? t('insight.mcpServers.statusActive') : t('insight.mcpServers.statusDisabled'),
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

function onPaginationPageChange() {
  layoutTable()
}

function onPaginationSizeChange() {
  pagination.page = 1
  layoutTable()
}

const batchDisabled = computed(() => selectedRows.value.length === 0)
const singleSelected = computed(() => (selectedRows.value.length === 1 ? selectedRows.value[0]! : null))
const lifecycleOpen = computed(() => Boolean(lifecycleAction.value && lifecycleTarget.value))
const lifecycleItems = computed<DangerConfirmItem[]>(() => {
  const row = lifecycleTarget.value
  if (!row) return []
  return [{
    key: row.uuid,
    name: row.name,
    status: {
      label: statusLabel(row.enabled),
      tone: row.enabled ? 'success' : 'info',
    },
  }]
})
const lifecycleLevel = computed<DangerLevel>(() => {
  if (lifecycleAction.value === 'delete') return 'high'
  if (lifecycleAction.value === 'disable') return 'medium'
  return 'low'
})
const lifecycleTitle = computed(() => {
  switch (lifecycleAction.value) {
    case 'enable':
      return t('insight.mcpServers.enableTitle')
    case 'disable':
      return t('insight.mcpServers.disableTitle')
    case 'delete':
      return t('insight.mcpServers.deleteTitle')
    default:
      return ''
  }
})
const lifecycleMessage = computed(() => (
  lifecycleAction.value === 'enable' ? t('insight.mcpServers.enableConfirmMessage') : ''
))
const lifecycleWarning = computed(() => {
  if (lifecycleAction.value === 'disable') return t('insight.mcpServers.disableConfirmWarning')
  if (lifecycleAction.value === 'delete') return t('insight.mcpServers.deleteConfirmWarning')
  return ''
})
const lifecycleConfirmText = computed(() => {
  switch (lifecycleAction.value) {
    case 'enable':
      return t('insight.mcpServers.enable')
    case 'disable':
      return t('insight.mcpServers.disable')
    case 'delete':
      return t('common.delete')
    default:
      return ''
  }
})
const lifecycleContext = computed(() => JSON.stringify([
  lifecycleAction.value,
  lifecycleTarget.value?.uuid || '',
]))

function transportLabel(transport: string) {
  if (transport === 'stdio') return 'STDIO'
  if (transport === 'url') return 'URL'
  return transport.toUpperCase()
}

function statusLabel(enabled: boolean) {
  return enabled ? t('insight.mcpServers.statusActive') : t('insight.mcpServers.statusDisabled')
}

async function load() {
  loading.value = true
  try {
    health.value = await fetchLensHealth()
    if (health.value.lens?.configured && health.value.lens?.authenticated) {
      rows.value = await listLensMcpServers()
    } else {
      rows.value = []
    }
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, t('errors.generic.loadFailed')))
  } finally {
    loading.value = false
    layoutTable()
  }
}

function openCreate() {
  router.push(lensMcpPath() + '/add')
}

function openDetail(row: LensMcpServer) {
  detailRow.value = row
  detailOpen.value = true
}

function openEdit(row: LensMcpServer | string) {
  const uuid = typeof row === 'string' ? row : row.uuid
  router.push(`${lensMcpPath()}/${uuid}/edit`)
}

function onSelectionChange(selection: LensMcpServer[]) {
  selectedRows.value = selection
}

function requestLifecycleAction(action: McpLifecycleAction, row: LensMcpServer) {
  if (lifecycleLoading.value) return
  lifecycleAction.value = action
  lifecycleTarget.value = row
}

function closeLifecycleAction() {
  if (lifecycleLoading.value) return
  lifecycleAction.value = null
  lifecycleTarget.value = null
}

async function confirmLifecycleAction() {
  const action = lifecycleAction.value
  const row = lifecycleTarget.value
  if (!action || !row || lifecycleLoading.value) return
  lifecycleLoading.value = true
  try {
    if (action === 'enable' || action === 'disable') {
      const enabled = action === 'enable'
      if (row.enabled !== enabled) {
        await updateLensMcpServer(row.uuid, { enabled })
        ElMessage.success({
          message: t(enabled ? 'insight.mcpServers.enableSuccess' : 'insight.mcpServers.disableSuccess'),
          grouping: true,
        })
        await load()
      }
    } else {
      await deleteLensMcpServer(row.uuid)
      ElMessage.success({ message: t('insight.mcpServers.deleteSuccess'), grouping: true })
      selectedRows.value = []
      await load()
    }
    lifecycleAction.value = null
    lifecycleTarget.value = null
  } catch (err) {
    ElMessage.error({
      message: apiErrorMessage(
        err,
        action === 'delete' ? t('errors.generic.requestFailed') : t('insight.mcpServers.saveFailed'),
      ),
      grouping: true,
    })
  } finally {
    lifecycleLoading.value = false
  }
}

function editSelected() {
  const row = singleSelected.value
  if (!row) return
  openEdit(row)
}

function enableSelected() {
  const row = singleSelected.value
  if (!row || row.enabled) return
  requestLifecycleAction('enable', row)
}

function disableSelected() {
  const row = singleSelected.value
  if (!row || !row.enabled) return
  requestLifecycleAction('disable', row)
}

function deleteSelected() {
  const row = singleSelected.value
  if (!row) return
  requestLifecycleAction('delete', row)
}

onMounted(() => {
  void load()
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
            {{ t('platformOps.engineActions.addMcpServer') }}
          </ElButton>

          <ElDropdown
            trigger="click"
            popper-class="hfl-actions-dropdown"
            @visible-change="moreActionsOpen = $event"
          >
          <ElButton :disabled="!bridgeReady">
            {{ t('platformOps.engineActions.mcpServerActions') }}
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
                divided
                :disabled="batchDisabled || !singleSelected || singleSelected.enabled"
                @click="enableSelected"
              >
                <span class="el-dropdown-menu__item-content">
                  <CirclePlay
                    :size="14"
                    class="shrink-0"
                  />
                  <span>{{ t('insight.mcpServers.enable') }}</span>
                </span>
              </ElDropdownItem>
              <ElDropdownItem
                :disabled="batchDisabled || !singleSelected || !singleSelected.enabled"
                @click="disableSelected"
              >
                <span class="el-dropdown-menu__item-content">
                  <CircleStop
                    :size="14"
                    class="shrink-0"
                  />
                  <span>{{ t('insight.mcpServers.disable') }}</span>
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
        <el-table
          ref="tableRef"
          v-table-overflow-title
          v-loading="loading"
          row-key="uuid"
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
          />
          <el-table-column
            :label="t('insight.mcpServers.colName')"
            min-width="180"
            fixed="left"
            class-name="hfl-table-name-col"
          >
            <template #default="{ row }">
              <button
                type="button"
                class="hfl-table-name-link hfl-table-name-link--full"
                @click="openDetail(row)"
              >
                {{ row.name }}
              </button>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.mcpServers.colTransport')"
            width="110"
          >
            <template #default="{ row }">
              <HflTypeLabel :label="transportLabel(row.transport)" />
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.mcpServers.colEndpoint')"
            min-width="240"
          >
            <template #default="{ row }">
              <span
                class="insight-mcp-mono"
                :class="{ 'hfl-empty-mark': !row.endpoint }"
              >{{ row.endpoint || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.mcpServers.colStatus')"
            width="110"
            fixed="right"
          >
            <template #default="{ row }">
              <HflBooleanStatusTag
                :value="row.enabled"
                :label="statusLabel(row.enabled)"
              />
            </template>
          </el-table-column>
          <template #empty>
            <el-empty
              :description="bridgeReady ? t('insight.mcpServers.emptyPlatform') : t('insight.shared.bridgeNotReady')"
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
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.pageSize"
          :total="pagination.count"
          @current-change="onPaginationPageChange"
          @size-change="onPaginationSizeChange"
        />
      </div>
    </div>

    <InsightMcpServerDetailDrawer
      v-model:open="detailOpen"
      :row="detailRow"
      @edit="openEdit"
    />
    <DangerConfirmDialog
      v-if="lifecycleOpen"
      :model-value="true"
      :title="lifecycleTitle"
      :level="lifecycleLevel"
      :message="lifecycleMessage"
      :warning="lifecycleWarning"
      :items="lifecycleItems"
      :item-status-label="t('common.dangerItemStatus')"
      :confirm-mode="lifecycleAction === 'delete' ? 'keyword' : 'single'"
      :confirm-keyword="t('common.deleteKeyword')"
      :confirm-keyword-placeholder="t('common.deleteKeyword')"
      :confirm-keyword-hint="t('common.dangerConfirmKeywordHint', { keyword: t('common.deleteKeyword') })"
      :confirmation-context="lifecycleContext"
      :cancel-text="t('common.cancel')"
      :confirm-text="lifecycleConfirmText"
      :loading="lifecycleLoading"
      :confirm-disabled="lifecycleLoading || !lifecycleTarget"
      @confirm="confirmLifecycleAction"
      @cancel="closeLifecycleAction"
    />
  </div>
</template>

<style scoped>
.insight-mcp-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}
</style>
