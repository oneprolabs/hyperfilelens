<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
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
const health = ref<LensHealth | null>(null)
const rows = ref<LensMcpServer[]>([])
const search = ref('')
const { appliedSearch, clearSearch } = useListSearch(search)
const selectedRows = ref<LensMcpServer[]>([])
const moreActionsOpen = ref(false)
const detailOpen = ref(false)
const detailRow = ref<LensMcpServer | null>(null)
const deleteOpen = ref(false)
const deleteLoading = ref(false)
const deleteTarget = ref<LensMcpServer | null>(null)

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
      runtimeSettingsLabel(row),
      row.enabled ? t('insight.mcpServers.statusActive') : t('insight.mcpServers.statusDisabled'),
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return hay.includes(q)
  })
})

const batchDisabled = computed(() => selectedRows.value.length === 0)
const singleSelected = computed(() => (selectedRows.value.length === 1 ? selectedRows.value[0]! : null))

function transportLabel(transport: string) {
  if (transport === 'stdio') return 'STDIO'
  if (transport === 'url') return 'URL'
  return transport.toUpperCase()
}

function runtimeSettingsCount(row: LensMcpServer) {
  const config = row.config
  if (!config || typeof config !== 'object') return 0
  return Object.keys(config).filter((key) => key.trim()).length
}

function runtimeSettingsLabel(row: LensMcpServer) {
  const count = runtimeSettingsCount(row)
  if (count === 0) return t('insight.mcpServers.runtimeSettingsNone')
  return t('insight.mcpServers.runtimeSettingsCount', { n: count })
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

async function setEnabled(row: LensMcpServer, enabled: boolean) {
  if (row.enabled === enabled) return
  try {
    await updateLensMcpServer(row.uuid, { enabled })
    ElMessage.success(t('insight.mcpServers.saveSuccess'))
    await load()
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, t('errors.generic.requestFailed')))
  }
}

function deleteRow(row: LensMcpServer) {
  deleteTarget.value = row
  deleteOpen.value = true
}

async function confirmDelete() {
  const row = deleteTarget.value
  if (!row) return
  deleteLoading.value = true
  try {
    await deleteLensMcpServer(row.uuid)
    ElMessage.success(t('insight.mcpServers.deleteSuccess'))
    selectedRows.value = []
    await load()
    deleteOpen.value = false
    deleteTarget.value = null
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, t('errors.generic.requestFailed')))
  } finally {
    deleteLoading.value = false
  }
}

async function deleteSelected() {
  const row = singleSelected.value
  if (!row) return
  await deleteRow(row)
}

function editSelected() {
  const row = singleSelected.value
  if (!row) return
  openEdit(row)
}

async function enableSelected() {
  const row = singleSelected.value
  if (!row) return
  await setEnabled(row, true)
}

async function disableSelected() {
  const row = singleSelected.value
  if (!row) return
  await setEnabled(row, false)
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
        <ElButton
          type="primary"
          :disabled="!bridgeReady"
          @click="openCreate"
        >
          <Plus :size="16" />
          {{ isPlatformEngine ? t('platformOps.engineActions.addMcpServer') : t('insight.mcpServers.btnAdd') }}
        </ElButton>

        <ElDropdown
          trigger="click"
          popper-class="hfl-actions-dropdown"
          @visible-change="moreActionsOpen = $event"
        >
          <ElButton :disabled="!bridgeReady">
            {{ isPlatformEngine ? t('platformOps.engineActions.mcpServerActions') : t('insight.mcpServers.btnMoreActions') }}
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
                :disabled="batchDisabled || !singleSelected"
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
                :disabled="batchDisabled || !singleSelected"
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

        <div class="hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split">
          <ElInput
            v-model="search"
            clearable
            size="small"
            :placeholder="t('insight.mcpServers.searchPlaceholder')"
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
          :data="filteredRows"
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
            min-width="200"
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
            :label="t('insight.mcpServers.colRuntimeSettings')"
            width="150"
          >
            <template #default="{ row }">
              <span :class="runtimeSettingsCount(row) ? 'insight-mcp-runtime' : 'insight-mcp-muted'">
                {{ runtimeSettingsLabel(row) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.mcpServers.colEnabled')"
            width="110"
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
              :description="bridgeReady ? t('insight.mcpServers.empty') : t('insight.shared.bridgeNotReady')"
              :image-size="80"
            />
          </template>
        </el-table>
      </div>
    </div>

    <InsightMcpServerDetailDrawer
      v-model:open="detailOpen"
      :row="detailRow"
      @edit="openEdit"
    />
    <DangerConfirmDialog
      v-model="deleteOpen"
      :title="t('insight.mcpServers.deleteTitle')"
      :message="deleteTarget ? t('insight.mcpServers.deleteConfirm', { name: deleteTarget.name }) : ''"
      :items="deleteTarget ? [{ key: deleteTarget.uuid, name: deleteTarget.name }] : []"
      :cancel-text="t('common.cancel')"
      :confirm-text="t('common.delete')"
      :loading="deleteLoading"
      @confirm="confirmDelete"
      @cancel="deleteTarget = null"
    />
  </div>
</template>

<style scoped>
.insight-mcp-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}

.insight-mcp-runtime {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.insight-mcp-muted {
  color: rgb(148 163 184);
  font-size: 13px;
}
</style>
