<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { lensModelsPath } from '../../lib/lensEngineRoutes'
import { useI18n } from 'vue-i18n'
import { Bot, ChevronDown, CirclePlay, CircleStop, Images, LoaderCircle, Pencil, Plus, RefreshCw, Search, Trash2 } from 'lucide-vue-next'
import { ElMessage, type ElTable } from 'element-plus'
import { useListTableLayout } from '../../composables/useListTableLayout'
import { useListSearch } from '../../composables/useListSearch'
import { usePlatformEngineListSearch } from '../../composables/usePlatformEngineListSearch'
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
import DangerConfirmDialog, {
  type DangerConfirmItem,
  type DangerLevel,
} from '../../components/DangerConfirmDialog.vue'
import PlatformOpsPagination from '../../platform-ops/components/PlatformOpsPagination.vue'

type ModelLifecycleAction =
  | 'enable'
  | 'disable'
  | 'default-agent'
  | 'default-multimodal'
  | 'delete'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const isPlatformEngine = computed(() => route.path.startsWith('/platform-ops/engine'))
const {
  listSearchPlaceholder,
  listSearchClass,
  listSearchSize,
} = usePlatformEngineListSearch('insight.aiSettings.searchPlaceholder', 'models')

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
const lifecycleAction = ref<ModelLifecycleAction | null>(null)
const lifecycleTarget = ref<LensLlmConfig | null>(null)
const lifecycleLoading = ref(false)
const detailRefreshToken = ref(0)
let statusRefresh = Promise.resolve()

const lifecycleOpen = computed(() => Boolean(lifecycleAction.value && lifecycleTarget.value))
const lifecycleItems = computed<DangerConfirmItem[]>(() => {
  const row = lifecycleTarget.value
  if (!row) return []
  return [{
    key: row.uuid,
    name: modelName(row),
    status: {
      label: t(row.is_active === false ? 'insight.aiSettings.statusInactive' : 'insight.aiSettings.statusActive'),
      tone: row.is_active === false ? 'info' : 'success',
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
      return t('insight.aiSettings.enableTitle')
    case 'disable':
      return t('insight.aiSettings.disableTitle')
    case 'default-agent':
      return t('insight.aiSettings.setDefaultAgentTitle')
    case 'default-multimodal':
      return t('insight.aiSettings.setDefaultMultimodalTitle')
    case 'delete':
      return t('insight.aiSettings.deleteTitle')
    default:
      return ''
  }
})
const lifecycleMessage = computed(() => {
  switch (lifecycleAction.value) {
    case 'enable':
      return t('insight.aiSettings.enableConfirmMessage')
    case 'default-agent':
      return t('insight.aiSettings.setDefaultAgentConfirmMessage')
    case 'default-multimodal':
      return t('insight.aiSettings.setDefaultMultimodalConfirmMessage')
    default:
      return ''
  }
})
const lifecycleWarning = computed(() => {
  switch (lifecycleAction.value) {
    case 'disable':
      return t('insight.aiSettings.disableConfirmWarning')
    case 'delete':
      return t('insight.aiSettings.deleteConfirmWarning')
    default:
      return ''
  }
})
const lifecycleConfirmText = computed(() => {
  switch (lifecycleAction.value) {
    case 'enable':
      return t('insight.aiSettings.enable')
    case 'disable':
      return t('insight.aiSettings.disable')
    case 'default-agent':
    case 'default-multimodal':
      return t('insight.aiSettings.confirmSetAsDefault')
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

async function setActive(row: LensLlmConfig, isActive: boolean): Promise<boolean> {
  if (row.is_active === isActive || testingModelUuids.value.has(row.uuid)) return false
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
    return true
  } catch (err) {
    const detail = apiErrorMessageI18n(err, t, t('errors.generic.requestFailed'))
    ElMessage.error({
      message: isActive && resolveErrorCode(err) === 'AI_MODEL.CONNECTION_TEST_FAILED'
        ? `${t('insight.aiSettings.enableTestFailed')} ${detail}`
        : detail,
      grouping: true,
    })
    return false
  } finally {
    if (isActive) testingModelUuids.value.delete(row.uuid)
  }
}

async function setAgentDefault(row: LensLlmConfig): Promise<boolean> {
  if (row.is_default_agent || row.is_active === false) return false
  try {
    await setLensDefaultAgentModel(row.uuid)
    ElMessage.success({ message: t('insight.aiSettings.defaultAgentModelSaved'), grouping: true })
    await load()
    return true
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
    return false
  }
}

async function setMultimodalDefault(row: LensLlmConfig): Promise<boolean> {
  if (row.is_default_multimodal || row.is_active === false) return false
  try {
    await setLensDefaultMultimodalModel(row.uuid)
    ElMessage.success({ message: t('insight.aiSettings.defaultMultimodalModelSaved'), grouping: true })
    await load()
    return true
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
    return false
  }
}

function requestLifecycleAction(action: ModelLifecycleAction, row: LensLlmConfig) {
  if (lifecycleLoading.value || testingModelUuids.value.has(row.uuid)) return
  lifecycleAction.value = action
  lifecycleTarget.value = { ...row }
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
  if (testingModelUuids.value.has(row.uuid)) return
  lifecycleLoading.value = true
  let ok = false
  try {
    if (action === 'enable') {
      ok = await setActive(row, true)
    } else if (action === 'disable') {
      ok = await setActive(row, false)
    } else if (action === 'default-agent') {
      ok = await setAgentDefault(row)
    } else if (action === 'default-multimodal') {
      ok = await setMultimodalDefault(row)
    } else {
      await deleteLensModel(row.uuid)
      ElMessage.success({ message: t('insight.aiSettings.deleteSuccess'), grouping: true })
      selectedRows.value = []
      await load()
      ok = true
    }
    if (ok) {
      lifecycleAction.value = null
      lifecycleTarget.value = null
    }
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
  } finally {
    lifecycleLoading.value = false
  }
}

function deleteSelected() {
  const row = singleSelected.value
  if (!row || selectedTesting.value) return
  requestLifecycleAction('delete', row)
}

function enableSelected() {
  const row = singleSelected.value
  if (!row || row.is_active !== false || selectedTesting.value) return
  requestLifecycleAction('enable', row)
}

function disableSelected() {
  const row = singleSelected.value
  if (!row || row.is_active === false || selectedTesting.value) return
  requestLifecycleAction('disable', row)
}

function editSelected() {
  const row = singleSelected.value
  if (!row || selectedTesting.value) return
  openEdit(row)
}

function setSelectedAgentDefault() {
  const row = singleSelected.value
  if (!row || !selectedCanSetAgentDefault.value) return
  requestLifecycleAction('default-agent', row)
}

function setSelectedMultimodalDefault() {
  const row = singleSelected.value
  if (!row || !selectedCanSetMultimodalDefault.value) return
  requestLifecycleAction('default-multimodal', row)
}

onMounted(() => {
  void load()
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
          class="hfl-list-table insight-ai-models-table"
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
              <button
                type="button"
                class="hfl-table-name-link hfl-table-name-link--single"
                :disabled="testingModelUuids.has(row.uuid)"
                @click="openDetail(row)"
              >
                {{ modelName(row) }}
              </button>
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
    />
    <DangerConfirmDialog
      v-if="lifecycleOpen"
      :model-value="true"
      :title="lifecycleTitle"
      :level="lifecycleLevel"
      :message="lifecycleMessage"
      :warning="lifecycleWarning"
      :items="lifecycleItems"
      :item-status-label="t('insight.aiSettings.labelStatus')"
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
.insight-ai-models-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}

.insight-ai-models-table :deep(td.hfl-table-name-col .cell) {
  display: flex;
  align-items: center;
  gap: 8px;
}

.insight-ai-models-table :deep(td.hfl-table-name-col .hfl-table-name-link) {
  min-width: 0;
}

.insight-ai-models-table :deep(td.hfl-table-name-col .hfl-status-tag) {
  flex: 0 0 auto;
  align-items: center;
  line-height: 1;
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
