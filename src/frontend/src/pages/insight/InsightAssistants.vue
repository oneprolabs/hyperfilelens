<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { lensAssistantsPath } from '../../lib/lensEngineRoutes'
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
import { lifecycleStatusTagAttrs } from '../../lib/statusTag'
import {
  deleteLensAssistant,
  fetchLensAssistantFormOptions,
  fetchLensHealth,
  listLensAssistants,
  listLensModels,
  updateLensAssistant,
  type LensAssistant,
  type LensAssistantFormOptions,
  type LensHealth,
  type LensLlmConfig,
} from '../../lib/lensApi'

import InsightAssistantDetailDrawer from './InsightAssistantDetailDrawer.vue'
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
const health = ref<LensHealth | null>(null)
const rows = ref<LensAssistant[]>([])
const models = ref<LensLlmConfig[]>([])
const search = ref('')
const { appliedSearch, clearSearch } = useListSearch(search)
const selectedRows = ref<LensAssistant[]>([])
const moreActionsOpen = ref(false)
const formOptions = ref<LensAssistantFormOptions | null>(null)
const detailOpen = ref(false)
const detailRow = ref<LensAssistant | null>(null)
const deleteOpen = ref(false)
const deleteLoading = ref(false)
const deleteTarget = ref<LensAssistant | null>(null)

const tableRef = ref<InstanceType<typeof ElTable> | null>(null)
const tableBlockRef = ref<HTMLElement | null>(null)
const { tableMaxHeight, layoutTable, handleTableScroll } = useListTableLayout(tableRef, tableBlockRef)

const bridgeReady = computed(
  () => health.value?.lens?.configured && health.value?.lens?.authenticated,
)

const modelByUuid = computed(() => new Map(models.value.map((row) => [row.uuid, row])))

const knowledgeSourceById = computed(
  () => new Map((formOptions.value?.knowledge_sources ?? []).map((row) => [row.id, row])),
)

const filteredRows = computed(() => {
  const q = appliedSearch.value.trim().toLowerCase()
  if (!q) return rows.value
  return rows.value.filter((row) => {
    const hay = [
      row.name,
      row.slug,
      agentModelLabel(row),
      multimodalModelLabel(row),
      knowledgeSourceName(row),
      knowledgeSourceMeta(row),
      scenarioLabel(row),
      row.selected_task,
      analysisDepthLabel(row),
      row.visibility_scope === 'user'
        ? t('insight.assistants.visibilityOnlyMe')
        : t('insight.assistants.visibilityOrganizationShort'),
      row.status,
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return hay.includes(q)
  })
})

const batchDisabled = computed(() => selectedRows.value.length === 0)
const singleSelected = computed(() => (selectedRows.value.length === 1 ? selectedRows.value[0]! : null))

function statusLabel(status: string) {
  if (status === 'active') return t('insight.assistants.statusActive')
  if (status === 'disabled') return t('insight.assistants.statusDisabled')
  return status
}

function knowledgeSourceStatusLabel(status: string) {
  if (status === 'ready') return t('insight.kb.statusReady')
  if (status === 'degraded') return t('insight.kb.statusDegraded')
  if (status === 'syncing') return t('insight.kb.statusSyncing')
  if (status === 'error') return t('insight.kb.statusError')
  if (status === 'paused') return t('insight.kb.statusPaused')
  return status
}

function agentModelLabel(row: LensAssistant) {
  return modelRefLabel(row.agent_model_ref)
}

function multimodalModelLabel(row: LensAssistant) {
  return modelRefLabel(row.multimodal_model_ref)
}

function modelRefLabel(ref: string | null | undefined) {
  if (!ref) return '—'
  const model = modelByUuid.value.get(ref)
  if (!model) return ref
  const provider = model.provider || model.name || 'provider'
  const modelId = model.config?.model || '—'
  return `${provider} · ${modelId}`
}

function visibilityLabel(scope: string) {
  if (scope === 'user') return t('insight.assistants.visibilityOnlyMe')
  return t('insight.assistants.visibilityOrganizationShort')
}

function knowledgeSourceName(row: LensAssistant) {
  if (row.knowledge_source_name) return row.knowledge_source_name
  const ks = row.knowledge_source_id ? knowledgeSourceById.value.get(row.knowledge_source_id) : null
  return ks?.name || '—'
}

function knowledgeSourceMeta(row: LensAssistant) {
  const gateway =
    row.gateway_name ||
    (row.knowledge_source_id
      ? knowledgeSourceById.value.get(row.knowledge_source_id)?.gateway_name
      : null) ||
    ''
  const status = row.knowledge_source_status ||
    (row.knowledge_source_id
      ? knowledgeSourceById.value.get(row.knowledge_source_id)?.status
      : null) ||
    ''
  if (!gateway && !status) return ''
  if (gateway && status) {
    return `${gateway} · ${knowledgeSourceStatusLabel(status)}`
  }
  return gateway || (status ? knowledgeSourceStatusLabel(status) : '')
}

function scenarioLabel(row: LensAssistant) {
  const taskName = row.selected_task
  if (!taskName) return '—'
  const gateway = formOptions.value?.gateways.find((gw) => gw.lensnode_uuid === row.lensnode_uuid)
  const task = gateway?.tasks.find((item) => item.name === taskName)
  return task?.title || taskName
}

function analysisDepthLabel(row: LensAssistant) {
  const value = row.agent_rounds || 'balanced'
  if (value === 'flash') return t('insight.assistants.roundsFlash')
  if (value === 'fast') return t('insight.assistants.roundsFast')
  if (value === 'balanced') return t('insight.assistants.roundsBalanced')
  if (value === 'deep') return t('insight.assistants.roundsDeep')
  if (value === 'max') return t('insight.assistants.roundsMax')
  return value
}

async function loadFormOptions() {
  formOptions.value = await fetchLensAssistantFormOptions()
}

async function loadModels() {
  models.value = await listLensModels().catch(() => [] as LensLlmConfig[])
}

async function load() {
  loading.value = true
  try {
    health.value = await fetchLensHealth()
    if (health.value.lens?.configured && health.value.lens?.authenticated) {
      const [assistantRows] = await Promise.all([
        listLensAssistants(),
        loadFormOptions().catch(() => null),
        loadModels().catch(() => null),
      ])
      rows.value = assistantRows
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
  router.push(lensAssistantsPath() + '/add')
}

function openDetail(row: LensAssistant) {
  detailRow.value = row
  detailOpen.value = true
}

function openEdit(row: LensAssistant | string) {
  const uuid = typeof row === 'string' ? row : row.uuid
  router.push(`${lensAssistantsPath()}/${uuid}/edit`)
}

function onSelectionChange(selection: LensAssistant[]) {
  selectedRows.value = selection
}

async function setStatus(row: LensAssistant, status: 'active' | 'disabled') {
  if (row.status === status) return
  try {
    await updateLensAssistant(row.uuid, { status })
    ElMessage.success(t('insight.assistants.saveSuccess'))
    await load()
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, t('errors.generic.requestFailed')))
  }
}

function deleteRow(row: LensAssistant) {
  deleteTarget.value = row
  deleteOpen.value = true
}

async function confirmDelete() {
  const row = deleteTarget.value
  if (!row) return
  deleteLoading.value = true
  try {
    await deleteLensAssistant(row.uuid)
    ElMessage.success(t('insight.assistants.deleteSuccess'))
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
  await setStatus(row, 'active')
}

async function disableSelected() {
  const row = singleSelected.value
  if (!row) return
  await setStatus(row, 'disabled')
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
          {{ isPlatformEngine ? t('platformOps.engineActions.addAssistant') : t('insight.assistants.btnAdd') }}
        </ElButton>

        <ElDropdown
          trigger="click"
          popper-class="hfl-actions-dropdown"
          @visible-change="moreActionsOpen = $event"
        >
          <ElButton :disabled="!bridgeReady">
            {{ isPlatformEngine ? t('platformOps.engineActions.assistantActions') : t('insight.assistants.btnMoreActions') }}
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
                  <span>{{ t('insight.assistants.enable') }}</span>
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
                  <span>{{ t('insight.assistants.disable') }}</span>
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
            :placeholder="t('insight.assistants.searchPlaceholder')"
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
            :label="t('insight.assistants.colName')"
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
              <div class="insight-assistants-slug">
                {{ row.slug }}
              </div>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.assistants.colAgentModel')"
            min-width="180"
          >
            <template #default="{ row }">
              <span
                class="insight-assistants-model"
                :class="{ 'hfl-empty-mark': agentModelLabel(row) === '—' }"
              >{{ agentModelLabel(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.assistants.colMultimodalModel')"
            min-width="180"
          >
            <template #default="{ row }">
              <span
                class="insight-assistants-model"
                :class="{ 'hfl-empty-mark': multimodalModelLabel(row) === '—' }"
              >{{ multimodalModelLabel(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.assistants.colKnowledgeSource')"
            min-width="220"
          >
            <template #default="{ row }">
              <div class="insight-assistants-identity">
                <strong :class="{ 'hfl-empty-mark': knowledgeSourceName(row) === '—' }">{{ knowledgeSourceName(row) }}</strong>
                <span v-if="knowledgeSourceMeta(row)">{{ knowledgeSourceMeta(row) }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.assistants.colScenario')"
            min-width="140"
          >
            <template #default="{ row }">
              {{ scenarioLabel(row) }}
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.assistants.colAnalysisDepth')"
            width="120"
          >
            <template #default="{ row }">
              <HflTypeLabel :label="analysisDepthLabel(row)" />
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.assistants.colVisibility')"
            min-width="120"
          >
            <template #default="{ row }">
              <div class="insight-assistants-visibility">
                <HflTypeLabel :label="visibilityLabel(row.visibility_scope)" />
              </div>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.assistants.colStatus')"
            width="110"
          >
            <template #default="{ row }">
              <el-tag
                v-bind="lifecycleStatusTagAttrs(row.status)"
                size="small"
              >
                {{ statusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty
              :description="bridgeReady ? t('insight.assistants.empty') : t('insight.shared.bridgeNotReady')"
              :image-size="80"
            />
          </template>
        </el-table>
      </div>
    </div>

    <InsightAssistantDetailDrawer
      v-model:open="detailOpen"
      :row="detailRow"
      @edit="openEdit"
    />
    <DangerConfirmDialog
      v-model="deleteOpen"
      :title="t('insight.assistants.deleteTitle')"
      :message="deleteTarget ? t('insight.assistants.deleteConfirm', { name: deleteTarget.name }) : ''"
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
.insight-assistants-slug {
  margin-top: 2px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  color: var(--color-text-tertiary);
  text-align: left;
}

.insight-assistants-model {
  font-size: 13px;
  color: var(--color-text-title);
}

.insight-assistants-identity {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  text-align: left;
}

.insight-assistants-identity strong {
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.insight-assistants-identity span {
  color: rgb(100 116 139);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.insight-assistants-depth-tag {
  font-weight: 600;
}

.insight-assistants-visibility {
  min-width: 0;
  max-width: 100%;
}

.insight-assistants-visibility-tag {
  max-width: 100%;
}

.insight-assistants-visibility-tag :deep(.el-tag__content) {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
