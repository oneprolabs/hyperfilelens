<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { lensModelsPath } from '../../lib/lensEngineRoutes'
import { useI18n } from 'vue-i18n'
import { Bot, ChevronDown, CirclePlay, CircleStop, Images, LoaderCircle, Pencil, Plus, RefreshCw, Search, Trash2 } from 'lucide-vue-next'
import { ElMessage, type ElTable } from 'element-plus'
import { useListTableLayout } from '../../composables/useListTableLayout'
import { useListSearch } from '../../composables/useListSearch'
import { apiErrorMessage, apiErrorMessageI18n } from '../../lib/api'
import { resolveErrorCode } from '../../lib/errors/resolver'
import { lifecycleStatusTagAttrs } from '../../lib/statusTag'
import {
  deleteLensModel,
  fetchLensHealth,
  listLensModels,
  patchLensModel,
  setLensDefaultAgentModel,
  setLensDefaultMultimodalModel,
  type LensHealth,
  type LensLlmConfig,
} from '../../lib/lensApi'
import { defaultAiModelDisplayName } from '../../lib/aiModelDisplay'
import { aiProviderLabel } from '../../lib/aiProviderDisplay'
import AiProviderIcon from '../../components/ai-model/AiProviderIcon.vue'
import InsightAiModelDetailDrawer from './InsightAiModelDetailDrawer.vue'
import HflStatusTag from '../../components/HflStatusTag.vue'
import DangerConfirmDialog from '../../components/DangerConfirmDialog.vue'
import PlatformOpsPagination from '../../platform-ops/components/PlatformOpsPagination.vue'

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
const models = ref<LensLlmConfig[]>([])
const search = ref('')
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })
const { appliedSearch, clearSearch } = useListSearch(search, () => {
  pagination.page = 1
})
const selectedRows = ref<LensLlmConfig[]>([])
const testingModelUuids = ref(new Set<string>())
const moreActionsOpen = ref(false)
const detailOpen = ref(false)
const detailUuid = ref<string | null>(null)
const deleteOpen = ref(false)
const deleteLoading = ref(false)
const deleteTarget = ref<LensLlmConfig | null>(null)
const detailRefreshToken = ref(0)
let statusRefresh = Promise.resolve()

const tableRef = ref<InstanceType<typeof ElTable> | null>(null)
const tableBlockRef = ref<HTMLElement | null>(null)
const { tableMaxHeight, layoutTable, handleTableScroll } = useListTableLayout(tableRef, tableBlockRef)

const bridgeReady = computed(
  () => health.value?.lens?.configured && health.value?.lens?.authenticated,
)

const filteredModels = computed(() => {
  const q = appliedSearch.value.trim().toLowerCase()
  if (!q) return models.value
  return models.value.filter((row) => {
    const hay = [
      row.provider,
      row.name,
      row.config?.model,
      row.config?.api_base,
      row.uuid,
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return hay.includes(q)
  })
})

const visibleModels = computed(() => {
  if (!isPlatformEngine.value) return filteredModels.value
  const start = (pagination.page - 1) * pagination.pageSize
  return filteredModels.value.slice(start, start + pagination.pageSize)
})

watch(
  filteredModels,
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
const singleSelected = computed(() => {
  const selected = selectedRows.value.length === 1 ? selectedRows.value[0] : null
  return selected ? models.value.find((row) => row.uuid === selected.uuid) || null : null
})
const selectedTesting = computed(() => Boolean(
  singleSelected.value && testingModelUuids.value.has(singleSelected.value.uuid),
))
const selectedCanSetAgentDefault = computed(() => Boolean(
  singleSelected.value &&
  !selectedTesting.value &&
  singleSelected.value.is_active !== false &&
  !singleSelected.value.is_default_agent,
))
const selectedCanSetMultimodalDefault = computed(() => Boolean(
  singleSelected.value &&
  !selectedTesting.value &&
  singleSelected.value.is_active !== false &&
  !singleSelected.value.is_default_multimodal,
))

function modelName(row: LensLlmConfig) {
  if (row.name?.trim()) return row.name.trim()
  const provider = row.provider || 'provider'
  const model = row.config?.model || '—'
  return defaultAiModelDisplayName(
    provider,
    model,
    aiProviderLabel(provider, provider),
    model,
  )
}

async function load(showLoading = true) {
  if (showLoading) loading.value = true
  try {
    health.value = await fetchLensHealth()
    if (health.value.lens?.configured && health.value.lens?.authenticated) {
      models.value = await listLensModels()
    } else {
      models.value = []
    }
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.loadFailed')), grouping: true })
  } finally {
    if (showLoading) loading.value = false
    if (detailOpen.value) detailRefreshToken.value += 1
    if (showLoading) layoutTable()
  }
}

function openCreate() {
  router.push(lensModelsPath() + '/add')
}

function openDetail(row: LensLlmConfig) {
  if (testingModelUuids.value.has(row.uuid)) return
  detailUuid.value = row.uuid
  detailOpen.value = true
}

function openEdit(row: LensLlmConfig | string) {
  const uuid = typeof row === 'string' ? row : row.uuid
  if (testingModelUuids.value.has(uuid)) return
  router.push(`${lensModelsPath()}/${uuid}/edit`)
}

function onSelectionChange(rows: LensLlmConfig[]) {
  selectedRows.value = rows
}

async function setActive(row: LensLlmConfig, isActive: boolean) {
  if (row.is_active === isActive || testingModelUuids.value.has(row.uuid)) return
  if (isActive) testingModelUuids.value.add(row.uuid)
  try {
    await patchLensModel(row.uuid, { is_active: isActive })
    // Keep refreshes ordered so a slow earlier response cannot overwrite a
    // newer enable/disable result for another model.
    statusRefresh = statusRefresh.then(() => load(false))
    await statusRefresh
    if (isActive) {
      models.value = models.value.map((model) => (
        model.uuid === row.uuid ? { ...model, is_active: true } : model
      ))
    }
    ElMessage.success({
      message: t(isActive ? 'insight.aiSettings.modelEnabled' : 'insight.aiSettings.saveSuccess'),
      grouping: true,
    })
  } catch (err) {
    const detail = apiErrorMessageI18n(err, t, t('errors.generic.requestFailed'))
    ElMessage.error({
      message: isActive && resolveErrorCode(err) === 'AI_MODEL.CONNECTION_TEST_FAILED'
        ? `${t('insight.aiSettings.enableTestFailed')} ${detail}`
        : detail,
      grouping: true,
    })
  } finally {
    if (isActive) testingModelUuids.value.delete(row.uuid)
  }
}

async function setAgentDefault(row: LensLlmConfig) {
  if (row.is_default_agent || row.is_active === false) return
  try {
    await setLensDefaultAgentModel(row.uuid)
    ElMessage.success({ message: t('insight.aiSettings.defaultAgentModelSaved'), grouping: true })
    await load()
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
  }
}

async function setMultimodalDefault(row: LensLlmConfig) {
  if (row.is_default_multimodal || row.is_active === false) return
  try {
    await setLensDefaultMultimodalModel(row.uuid)
    ElMessage.success({ message: t('insight.aiSettings.defaultMultimodalModelSaved'), grouping: true })
    await load()
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
  }
}

function deleteRow(row: LensLlmConfig) {
  if (testingModelUuids.value.has(row.uuid)) return
  deleteTarget.value = row
  deleteOpen.value = true
}

async function confirmDelete() {
  const row = deleteTarget.value
  if (!row || testingModelUuids.value.has(row.uuid)) return
  deleteLoading.value = true
  try {
    await deleteLensModel(row.uuid)
    ElMessage.success({ message: t('insight.aiSettings.deleteSuccess'), grouping: true })
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

async function deleteSelected() {
  const row = singleSelected.value
  if (!row) return
  await deleteRow(row)
}

async function enableSelected() {
  const row = singleSelected.value
  if (!row || row.is_active !== false || selectedTesting.value) return
  await setActive(row, true)
}

async function disableSelected() {
  const row = singleSelected.value
  if (!row || selectedTesting.value) return
  await setActive(row, false)
}

function editSelected() {
  const row = singleSelected.value
  if (!row || selectedTesting.value) return
  openEdit(row)
}

async function setSelectedAgentDefault() {
  const row = singleSelected.value
  if (!row || selectedTesting.value) return
  await setAgentDefault(row)
}

async function setSelectedMultimodalDefault() {
  const row = singleSelected.value
  if (!row || selectedTesting.value) return
  await setMultimodalDefault(row)
}

onMounted(() => {
  void load()
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
          {{ isPlatformEngine ? t('platformOps.engineActions.addModel') : t('insight.aiSettings.btnAdd') }}
        </ElButton>

        <ElDropdown
          trigger="click"
          popper-class="hfl-actions-dropdown"
          @visible-change="moreActionsOpen = $event"
        >
          <ElButton :disabled="!bridgeReady || selectedTesting">
            {{ isPlatformEngine ? t('platformOps.engineActions.modelActions') : t('insight.aiSettings.btnMoreActions') }}
            <ChevronDown
              :size="16"
              class="hfl-list-more__chev"
              :class="{ 'hfl-list-more__chev--open': moreActionsOpen }"
            />
          </ElButton>
          <template #dropdown>
            <ElDropdownMenu>
              <ElDropdownItem
                :disabled="batchDisabled || !singleSelected || selectedTesting"
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
                :disabled="batchDisabled || !singleSelected || selectedTesting || singleSelected.is_active !== false"
                @click="enableSelected"
              >
                <span class="el-dropdown-menu__item-content">
                  <CirclePlay
                    :size="14"
                    class="shrink-0"
                  />
                  <span>{{ t('insight.aiSettings.enable') }}</span>
                </span>
              </ElDropdownItem>
              <ElDropdownItem
                :disabled="batchDisabled || !singleSelected || selectedTesting || singleSelected.is_active === false"
                @click="disableSelected"
              >
                <span class="el-dropdown-menu__item-content">
                  <CircleStop
                    :size="14"
                    class="shrink-0"
                  />
                  <span>{{ t('insight.aiSettings.disable') }}</span>
                </span>
              </ElDropdownItem>
              <ElDropdownItem
                divided
                :disabled="!selectedCanSetAgentDefault"
                @click="setSelectedAgentDefault"
              >
                <span class="el-dropdown-menu__item-content">
                  <Bot
                    :size="14"
                    class="shrink-0"
                  />
                  <span>{{ t('insight.aiSettings.setDefaultAgent') }}</span>
                </span>
              </ElDropdownItem>
              <ElDropdownItem
                :disabled="!selectedCanSetMultimodalDefault"
                @click="setSelectedMultimodalDefault"
              >
                <span class="el-dropdown-menu__item-content">
                  <Images
                    :size="14"
                    class="shrink-0"
                  />
                  <span>{{ t('insight.aiSettings.setDefaultMultimodal') }}</span>
                </span>
              </ElDropdownItem>
              <ElDropdownItem
                divided
                class="el-dropdown-menu__item--danger"
                :disabled="batchDisabled || !singleSelected || selectedTesting"
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
            :placeholder="t('insight.aiSettings.searchPlaceholder')"
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
              @click="load()"
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
          :data="visibleModels"
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
            :label="t('insight.aiSettings.colName')"
            min-width="220"
            fixed="left"
            class-name="hfl-table-name-col"
          >
            <template #default="{ row }">
              <div class="insight-ai-models-name">
                <button
                  type="button"
                  class="hfl-table-name-link hfl-table-name-link--full"
                  :disabled="testingModelUuids.has(row.uuid)"
                  @click="openDetail(row)"
                >
                  {{ modelName(row) }}
                </button>
                <div
                  v-if="row.is_default_agent || row.is_default_multimodal"
                  class="insight-ai-models-badges"
                >
                  <HflStatusTag
                    v-if="row.is_default_agent"
                    tone="primary"
                    :label="t('insight.aiSettings.defaultAgentBadge')"
                  />
                  <HflStatusTag
                    v-if="row.is_default_multimodal"
                    tone="primary"
                    :label="t('insight.aiSettings.defaultMultimodalBadge')"
                  />
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.aiSettings.labelProvider')"
            min-width="120"
          >
            <template #default="{ row }">
              <span
                v-if="row.provider || row.name"
                class="insight-ai-models-provider"
              >
                <AiProviderIcon
                  :provider="row.provider || row.name || ''"
                  size="md"
                />
                <span class="insight-ai-models-provider__label">
                  {{ aiProviderLabel(row.provider || row.name || '') }}
                </span>
              </span>
              <span
                v-else
                class="hfl-empty-mark"
              >—</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.aiSettings.labelModel')"
            min-width="160"
          >
            <template #default="{ row }">
              <span :class="{ 'hfl-empty-mark': !row.config?.model }">{{ row.config?.model || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.aiSettings.labelApiBase')"
            min-width="200"
          >
            <template #default="{ row }">
              <span
                class="insight-ai-models-mono"
                :class="{ 'hfl-empty-mark': !row.config?.api_base }"
              >{{ row.config?.api_base || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.aiSettings.labelStatus')"
            width="130"
            class-name="hfl-table-no-tooltip"
          >
            <template #default="{ row }">
              <span class="hfl-table-type-label insight-ai-models-status">
                <ElTag
                  :type="testingModelUuids.has(row.uuid)
                    ? lifecycleStatusTagAttrs('testing').type
                    : row.is_active !== false ? 'success' : undefined"
                  size="small"
                  class="insight-ai-models-status__tag"
                  :class="{ 'hfl-tag--neutral': !testingModelUuids.has(row.uuid) && row.is_active === false }"
                  role="status"
                  aria-live="polite"
                >
                  <span class="insight-ai-models-status__content">
                    <LoaderCircle
                      v-if="testingModelUuids.has(row.uuid)"
                      :size="13"
                      class="insight-ai-models-status__icon"
                      aria-hidden="true"
                    />
                    {{ testingModelUuids.has(row.uuid)
                      ? t('insight.aiSettings.statusTesting')
                      : row.is_active !== false
                        ? t('insight.aiSettings.statusActive')
                        : t('insight.aiSettings.statusInactive') }}
                  </span>
                </ElTag>
              </span>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty
              :description="bridgeReady ? t('insight.aiSettings.emptyModels') : t('insight.shared.bridgeNotReady')"
              :image-size="80"
            />
          </template>
        </el-table>
      </div>

      <div
        v-if="isPlatformEngine"
        class="hfl-list-footer"
      >
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

    <InsightAiModelDetailDrawer
      v-model="detailOpen"
      :model-uuid="detailUuid"
      :refresh-token="detailRefreshToken"
      @edit="openEdit"
    />
    <DangerConfirmDialog
      v-model="deleteOpen"
      :title="t('insight.aiSettings.deleteTitle')"
      :message="deleteTarget ? t('insight.aiSettings.deleteConfirm', { name: modelName(deleteTarget) }) : ''"
      :items="deleteTarget ? [{ key: deleteTarget.uuid, name: modelName(deleteTarget) }] : []"
      :cancel-text="t('common.cancel')"
      :confirm-text="t('common.delete')"
      :loading="deleteLoading"
      @confirm="confirmDelete"
      @cancel="deleteTarget = null"
    />
  </div>
</template>

<style scoped>
.insight-ai-models-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}

.insight-ai-models-name {
  display: flex;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 5px;
}

.insight-ai-models-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.insight-ai-models-status {
  display: inline-flex;
  width: 100%;
  align-items: center;
  justify-content: flex-start;
}

.insight-ai-models-status__tag {
  box-sizing: border-box;
  max-width: 100%;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 500;
  line-height: 18px;
  vertical-align: middle;
  white-space: nowrap;
}

.insight-ai-models-status :deep(.el-tag.el-tag--success:not(.el-tag--dark):not(.el-tag--plain)) {
  --el-tag-text-color: var(--color-success-text);
  --el-tag-bg-color: color-mix(in srgb, var(--color-success) 6%, var(--color-card-bg));
  --el-tag-border-color: color-mix(in srgb, var(--color-success) 22%, var(--color-border));
  color: var(--el-tag-text-color);
  background-color: var(--el-tag-bg-color);
  border-color: var(--el-tag-border-color);
}

.insight-ai-models-status :deep(.el-tag.hfl-tag--neutral:not(.el-tag--dark):not(.el-tag--plain)) {
  --el-tag-text-color: var(--color-text-secondary);
  --el-tag-bg-color: color-mix(in srgb, var(--color-grey-2) 72%, var(--color-card-bg));
  --el-tag-border-color: var(--color-border);
  color: var(--el-tag-text-color);
  background-color: var(--el-tag-bg-color);
  border-color: var(--el-tag-border-color);
}

.insight-ai-models-status__content {
  display: inline-flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 5px;
  line-height: 16px;
  white-space: nowrap;
}

.insight-ai-models-status__icon {
  flex: 0 0 auto;
  animation: insight-ai-models-spin 1s linear infinite;
}

@keyframes insight-ai-models-spin {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .insight-ai-models-status__icon { animation: none; }
}

.insight-ai-models-provider {
  display: inline-flex !important;
  min-height: 24px;
  align-items: center;
  gap: 8px;
  vertical-align: middle;
}

.insight-ai-models-provider__label {
  line-height: normal;
}
</style>
