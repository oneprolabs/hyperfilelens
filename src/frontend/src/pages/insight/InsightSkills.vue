<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { lensSkillsPath } from '../../lib/lensEngineRoutes'
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
  deleteLensSkill,
  fetchLensHealth,
  listLensSkills,
  updateLensSkill,
  type LensHealth,
  type LensSkill,
} from '../../lib/lensApi'
import {
  isWorkspaceGuideSkill,
  skillDescription,
} from '../../lib/lensSkillHelpers'

import InsightSkillDetailDrawer from './InsightSkillDetailDrawer.vue'
import HflBooleanStatusTag from '../../components/HflBooleanStatusTag.vue'
import DangerConfirmDialog, {
  type DangerConfirmItem,
  type DangerLevel,
} from '../../components/DangerConfirmDialog.vue'
import PlatformOpsPagination from '../../platform-ops/components/PlatformOpsPagination.vue'

type SkillLifecycleAction = 'enable' | 'disable' | 'delete'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const {
  listSearchPlaceholder,
  listSearchClass,
  listSearchSize,
} = usePlatformEngineListSearch('insight.skills.searchPlaceholder', 'skills')

const TABLE_HEADER_STYLE: Record<string, string> = {
  background: 'rgba(248, 250, 252, 0.96)',
  color: 'rgb(71 85 105)',
  fontWeight: '600',
  whiteSpace: 'nowrap',
}

const loading = ref(false)
const health = ref<LensHealth | null>(null)
const rows = ref<LensSkill[]>([])
const search = ref('')
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })
const { appliedSearch, clearSearch } = useListSearch(search, () => {
  pagination.page = 1
})
const selectedRows = ref<LensSkill[]>([])
const moreActionsOpen = ref(false)
const detailOpen = ref(false)
const detailRow = ref<LensSkill | null>(null)
const lifecycleAction = ref<SkillLifecycleAction | null>(null)
const lifecycleTarget = ref<LensSkill | null>(null)
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
      row.slug,
      skillDescription(row),
      isWorkspaceGuideSkill(row) ? t('insight.skills.typeWorkspaceGuide') : '',
      row.enabled ? t('insight.skills.statusActive') : t('insight.skills.statusDisabled'),
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
      return t('insight.skills.enableTitle')
    case 'disable':
      return t('insight.skills.disableTitle')
    case 'delete':
      return t('insight.skills.deleteTitle')
    default:
      return ''
  }
})
const lifecycleMessage = computed(() => (
  lifecycleAction.value === 'enable' ? t('insight.skills.enableConfirmMessage') : ''
))
const lifecycleWarning = computed(() => {
  if (lifecycleAction.value === 'disable') return t('insight.skills.disableConfirmWarning')
  if (lifecycleAction.value === 'delete') return t('insight.skills.deleteConfirmWarning')
  return ''
})
const lifecycleConfirmText = computed(() => {
  switch (lifecycleAction.value) {
    case 'enable':
      return t('insight.skills.enable')
    case 'disable':
      return t('insight.skills.disable')
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

function statusLabel(enabled: boolean) {
  return enabled ? t('insight.skills.statusActive') : t('insight.skills.statusDisabled')
}

async function load() {
  loading.value = true
  try {
    health.value = await fetchLensHealth()
    if (health.value.lens?.configured && health.value.lens?.authenticated) {
      rows.value = await listLensSkills()
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
  router.push(lensSkillsPath() + '/add')
}

function openDetail(row: LensSkill) {
  detailRow.value = row
  detailOpen.value = true
}

function openEdit(row: LensSkill | string) {
  const uuid = typeof row === 'string' ? row : row.uuid
  router.push(`${lensSkillsPath()}/${uuid}/edit`)
}

function onSelectionChange(selection: LensSkill[]) {
  selectedRows.value = selection
}

function requestLifecycleAction(action: SkillLifecycleAction, row: LensSkill) {
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
        await updateLensSkill(row.uuid, { enabled })
        ElMessage.success({
          message: t(enabled ? 'insight.skills.enableSuccess' : 'insight.skills.disableSuccess'),
          grouping: true,
        })
        await load()
      }
    } else {
      await deleteLensSkill(row.uuid)
      ElMessage.success({ message: t('insight.skills.deleteSuccess'), grouping: true })
      selectedRows.value = []
      await load()
    }
    lifecycleAction.value = null
    lifecycleTarget.value = null
  } catch (err) {
    ElMessage.error({
      message: apiErrorMessage(
        err,
        action === 'delete' ? t('errors.generic.requestFailed') : t('insight.skills.saveFailed'),
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
            {{ t('platformOps.engineActions.addSkill') }}
          </ElButton>

          <ElDropdown
            trigger="click"
            popper-class="hfl-actions-dropdown"
            @visible-change="moreActionsOpen = $event"
          >
          <ElButton :disabled="!bridgeReady">
            {{ t('platformOps.engineActions.skillActions') }}
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
                  <span>{{ t('insight.skills.enable') }}</span>
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
                  <span>{{ t('insight.skills.disable') }}</span>
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
            :label="t('insight.skills.colName')"
            min-width="320"
            fixed="left"
            class-name="hfl-table-name-col"
          >
            <template #default="{ row }">
              <button
                type="button"
                class="hfl-table-name-link hfl-table-name-link--full"
                @click="openDetail(row)"
              >
                <div class="insight-skills-identity">
                  <span class="insight-skills-identity__head">
                    <span class="insight-skills-identity__name">{{ row.name }}</span>
                    <ElTag
                      v-if="isWorkspaceGuideSkill(row)"
                      size="small"
                      type="primary"
                      effect="plain"
                    >
                      {{ t('insight.skills.typeWorkspaceGuide') }}
                    </ElTag>
                  </span>
                  <span
                    v-if="skillDescription(row)"
                    class="insight-skills-identity__meta"
                  >
                    {{ skillDescription(row) }}
                  </span>
                </div>
              </button>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.skills.colStatus')"
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
              :description="bridgeReady ? t('insight.skills.emptyPlatform') : t('insight.shared.bridgeNotReady')"
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

    <InsightSkillDetailDrawer
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
.insight-skills-identity {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  text-align: left;
  white-space: normal;
}

.insight-skills-identity__head {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.insight-skills-identity__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}

.insight-skills-identity__meta {
  color: rgb(100 116 139);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
