<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
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
import { usePlatformEngineListSearch } from '../../composables/usePlatformEngineListSearch'
import { apiErrorMessage } from '../../lib/api'
import { LIST_ROUTE_REFRESH_KEY, stripListRefreshQuery } from '../../lib/listRouteRefresh'
import { lifecycleStatusTagAttrs } from '../../lib/statusTag'
import {
  deleteLensAssistant,
  fetchLensHealth,
  listLensAssistants,
  updateLensAssistant,
  type LensAssistant,
  type LensHealth,
} from '../../lib/lensApi'

import InsightAssistantDetailDrawer from './InsightAssistantDetailDrawer.vue'
import DangerConfirmDialog, {
  type DangerConfirmItem,
  type DangerLevel,
} from '../../components/DangerConfirmDialog.vue'
import PlatformOpsPagination from '../../platform-ops/components/PlatformOpsPagination.vue'

type AssistantLifecycleAction = 'enable' | 'disable' | 'delete'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const {
  listSearchPlaceholder,
  listSearchClass,
  listSearchSize,
} = usePlatformEngineListSearch('insight.assistants.searchPlaceholder', 'assistants')

const TABLE_HEADER_STYLE: Record<string, string> = {
  background: 'rgba(248, 250, 252, 0.96)',
  color: 'rgb(71 85 105)',
  fontWeight: '600',
  whiteSpace: 'nowrap',
}

const loading = ref(false)
const health = ref<LensHealth | null>(null)
const rows = ref<LensAssistant[]>([])
const search = ref('')
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })
const { appliedSearch, clearSearch } = useListSearch(search, () => {
  pagination.page = 1
})
const selectedRows = ref<LensAssistant[]>([])
const moreActionsOpen = ref(false)
const detailOpen = ref(false)
const detailRow = ref<LensAssistant | null>(null)
const lifecycleAction = ref<AssistantLifecycleAction | null>(null)
const lifecycleTarget = ref<LensAssistant | null>(null)
const lifecycleLoading = ref(false)
const filterOrgKey = ref('')
const knownOrganizations = ref<Array<{ key: string; name: string }>>([])

const tableRef = ref<InstanceType<typeof ElTable> | null>(null)
const tableBlockRef = ref<HTMLElement | null>(null)
const { tableMaxHeight, layoutTable, handleTableScroll } = useListTableLayout(tableRef, tableBlockRef)

const bridgeReady = computed(
  () => health.value?.lens?.configured && health.value?.lens?.authenticated,
)

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
    if (orgKey && String(row.organization_key || '').trim() !== orgKey) return false
    if (!q) return true
    const hay = [
      row.name,
      row.slug,
      organizationLabel(row),
      row.organization_key,
      knowledgeSourceName(row),
      row.gateway_name,
      scenarioLabel(row),
      row.selected_task,
      row.status,
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

watch(filterOrgKey, () => {
  pagination.page = 1
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
      label: statusLabel(row.status),
      tone: row.status === 'active' ? 'success' : 'info',
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
      return t('insight.assistants.enableTitle')
    case 'disable':
      return t('insight.assistants.disableTitle')
    case 'delete':
      return t('insight.assistants.deleteTitle')
    default:
      return ''
  }
})
const lifecycleMessage = computed(() => (
  lifecycleAction.value === 'enable' ? t('insight.assistants.enableConfirmMessage') : ''
))
const lifecycleWarning = computed(() => {
  if (lifecycleAction.value === 'disable') return t('insight.assistants.disableConfirmWarning')
  if (lifecycleAction.value === 'delete') return t('insight.assistants.deleteConfirmWarning')
  return ''
})
const lifecycleConfirmText = computed(() => {
  switch (lifecycleAction.value) {
    case 'enable':
      return t('insight.assistants.enable')
    case 'disable':
      return t('insight.assistants.disable')
    case 'delete':
      return t('common.delete')
    default:
      return ''
  }
})
const lifecycleSubtitle = computed(() => {
  const row = lifecycleTarget.value
  if (!row?.organization_key) return ''
  const name = organizationLabel(row) || row.organization_key
  return t('platformOps.governanceConfirm.organizationContext', { name })
})
const lifecycleContext = computed(() => JSON.stringify([
  lifecycleAction.value,
  lifecycleTarget.value?.uuid || '',
]))

function statusLabel(status: string) {
  if (status === 'active') return t('insight.assistants.statusActive')
  if (status === 'disabled') return t('insight.assistants.statusDisabled')
  return status
}

function organizationLabel(row: LensAssistant) {
  return row.organization_name?.trim() || ''
}

function organizationId(row: LensAssistant) {
  return Number(row.organization_id || 0) || null
}

function knowledgeSourceName(row: LensAssistant) {
  return row.knowledge_source_name?.trim() || ''
}

function scenarioLabel(row: LensAssistant) {
  const title = row.selected_task_title?.trim()
  if (title) return title
  return row.selected_task || '—'
}

function rememberOrganizations(list: LensAssistant[]) {
  const byKey = new Map(knownOrganizations.value.map((org) => [org.key, org]))
  for (const row of list) {
    const key = String(row.organization_key || '').trim()
    if (!key) continue
    byKey.set(key, {
      key,
      name: organizationLabel(row) || key,
    })
  }
  knownOrganizations.value = [...byKey.values()].sort(
    (a, b) => a.name.localeCompare(b.name) || a.key.localeCompare(b.key),
  )
}

async function load() {
  loading.value = true
  try {
    health.value = await fetchLensHealth()
    if (health.value.lens?.configured && health.value.lens?.authenticated) {
      const list = await listLensAssistants()
      rows.value = list
      rememberOrganizations(list)
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

function requestLifecycleAction(action: AssistantLifecycleAction, row: LensAssistant) {
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
      const status = action === 'enable' ? 'active' : 'disabled'
      if (row.status !== status) {
        await updateLensAssistant(row.uuid, { status })
        ElMessage.success({
          message: t(action === 'enable'
            ? 'insight.assistants.enableSuccess'
            : 'insight.assistants.disableSuccess'),
          grouping: true,
        })
        await load()
      }
    } else {
      await deleteLensAssistant(row.uuid)
      ElMessage.success({ message: t('insight.assistants.deleteSuccess'), grouping: true })
      selectedRows.value = []
      await load()
    }
    lifecycleAction.value = null
    lifecycleTarget.value = null
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
  } finally {
    lifecycleLoading.value = false
  }
}

function deleteSelected() {
  const row = singleSelected.value
  if (!row) return
  requestLifecycleAction('delete', row)
}

function editSelected() {
  const row = singleSelected.value
  if (!row) return
  openEdit(row)
}

function enableSelected() {
  const row = singleSelected.value
  if (!row || row.status === 'active') return
  requestLifecycleAction('enable', row)
}

function disableSelected() {
  const row = singleSelected.value
  if (!row || row.status === 'disabled') return
  requestLifecycleAction('disable', row)
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
            {{ t('platformOps.engineActions.addAssistant') }}
          </ElButton>

          <ElDropdown
            trigger="click"
            popper-class="hfl-actions-dropdown"
            @visible-change="moreActionsOpen = $event"
          >
          <ElButton :disabled="!bridgeReady">
            {{ t('platformOps.engineActions.assistantActions') }}
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
                :disabled="batchDisabled || !singleSelected || singleSelected.status === 'active'"
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
                :disabled="batchDisabled || !singleSelected || singleSelected.status === 'disabled'"
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
        </div>

        <div class="hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split">
          <ElSelect
            v-model="filterOrgKey"
            clearable
            filterable
            size="small"
            class="assistants-org-filter"
            :placeholder="t('insight.assistants.orgFilterPlaceholder')"
            :aria-label="t('insight.assistants.orgFilterPlaceholder')"
          >
            <ElOption
              v-for="org in organizationFilterOptions"
              :key="org.key"
              :value="org.key"
              :label="org.key && org.key !== org.name ? `${org.name} (${org.key})` : org.name"
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
            :label="t('insight.assistants.colName')"
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
            :label="t('insight.assistants.colOrganization')"
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
          </el-table-column>
          <el-table-column
            :label="t('insight.assistants.colKnowledgeSource')"
            min-width="200"
          >
            <template #default="{ row }">
              <span :class="{ 'hfl-empty-mark': !knowledgeSourceName(row) }">
                {{ knowledgeSourceName(row) || '—' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.assistants.colScenario')"
            min-width="160"
          >
            <template #default="{ row }">
              <span :class="{ 'hfl-empty-mark': scenarioLabel(row) === '—' }">
                {{ scenarioLabel(row) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.assistants.colStatus')"
            width="110"
            fixed="right"
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
              :description="bridgeReady
                ? t('insight.assistants.emptyPlatform')
                : t('insight.shared.bridgeNotReady')"
              :image-size="80"
            />
          </template>
        </el-table>
      </div>

      <div
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

    <InsightAssistantDetailDrawer
      v-model:open="detailOpen"
      :row="detailRow"
      @edit="openEdit"
    />
    <DangerConfirmDialog
      v-if="lifecycleOpen"
      :model-value="true"
      :title="lifecycleTitle"
      :subtitle="lifecycleSubtitle"
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
.assistants-org-filter {
  width: 200px;
}
</style>
