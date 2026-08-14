<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
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
  skillContentPreview,
  skillDescription,
} from '../../lib/lensSkillHelpers'

import InsightSkillDetailDrawer from './InsightSkillDetailDrawer.vue'
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
const rows = ref<LensSkill[]>([])
const search = ref('')
const { appliedSearch, clearSearch } = useListSearch(search)
const selectedRows = ref<LensSkill[]>([])
const moreActionsOpen = ref(false)
const detailOpen = ref(false)
const detailRow = ref<LensSkill | null>(null)
const deleteOpen = ref(false)
const deleteLoading = ref(false)
const deleteTarget = ref<LensSkill | null>(null)

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
      skillDescription(row),
      skillContentPreview(row, 200),
      isWorkspaceGuideSkill(row) ? t('insight.skills.typeWorkspaceGuide') : '',
      row.enabled ? t('insight.skills.statusActive') : t('insight.skills.statusDisabled'),
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return hay.includes(q)
  })
})

const batchDisabled = computed(() => selectedRows.value.length === 0)
const singleSelected = computed(() => (selectedRows.value.length === 1 ? selectedRows.value[0]! : null))

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

async function setEnabled(row: LensSkill, enabled: boolean) {
  if (row.enabled === enabled) return
  try {
    await updateLensSkill(row.uuid, { enabled })
    ElMessage.success(t('insight.skills.saveSuccess'))
    await load()
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, t('errors.generic.requestFailed')))
  }
}

function deleteRow(row: LensSkill) {
  deleteTarget.value = row
  deleteOpen.value = true
}

async function confirmDelete() {
  const row = deleteTarget.value
  if (!row) return
  deleteLoading.value = true
  try {
    await deleteLensSkill(row.uuid)
    ElMessage.success(t('insight.skills.deleteSuccess'))
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
          {{ isPlatformEngine ? t('platformOps.engineActions.addSkill') : t('insight.skills.btnAdd') }}
        </ElButton>

        <ElDropdown
          trigger="click"
          popper-class="hfl-actions-dropdown"
          @visible-change="moreActionsOpen = $event"
        >
          <ElButton :disabled="!bridgeReady">
            {{ isPlatformEngine ? t('platformOps.engineActions.skillActions') : t('insight.skills.btnMoreActions') }}
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
                  <span>{{ t('insight.skills.enable') }}</span>
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

        <div class="hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split">
          <ElInput
            v-model="search"
            clearable
            size="small"
            :placeholder="t('insight.skills.searchPlaceholder')"
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
            :label="t('insight.skills.colName')"
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
                <div class="insight-skills-identity">
                  <span class="insight-skills-identity__head">
                    <strong>{{ row.name }}</strong>
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
            :label="t('insight.skills.colContent')"
            min-width="280"
          >
            <template #default="{ row }">
              <span
                v-if="skillContentPreview(row)"
                class="insight-skills-content-preview"
              >
                {{ skillContentPreview(row) }}
              </span>
              <span
                v-else
                class="hfl-empty-mark"
              >—</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('insight.skills.colEnabled')"
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
              :description="bridgeReady ? t('insight.skills.empty') : t('insight.shared.bridgeNotReady')"
              :image-size="80"
            />
          </template>
        </el-table>
      </div>
    </div>

    <InsightSkillDetailDrawer
      v-model:open="detailOpen"
      :row="detailRow"
      @edit="openEdit"
    />
    <DangerConfirmDialog
      v-model="deleteOpen"
      :title="t('insight.skills.deleteTitle')"
      :message="deleteTarget ? t('insight.skills.deleteConfirm', { name: deleteTarget.name }) : ''"
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
.insight-skills-identity {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  text-align: left;
}

.insight-skills-identity__head {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.insight-skills-identity__head strong {
  color: rgb(15 23 42);
  font-size: 14px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.insight-skills-identity__meta {
  color: rgb(100 116 139);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.insight-skills-content-preview {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.insight-skills-muted {
  color: rgb(148 163 184);
}
</style>
