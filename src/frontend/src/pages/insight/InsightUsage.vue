<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Search } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import ModulePage from '../../components/ModulePage.vue'
import HflPagination from '../../components/HflPagination.vue'
import InsightUsageRequestDrawer from './InsightUsageRequestDrawer.vue'
import { useInsightSideNav } from '../../composables/useInsightSideNav'
import { apiErrorMessage } from '../../lib/api'
import { formatLocalDateTime } from '../../lib/dateTime'
import {
  fetchCopilotUsage,
  type LensCopilotUsageOverview,
  type LensCopilotUsageItem,
} from '../../lib/lensApi'

const { t } = useI18n()
const insightMenus = useInsightSideNav()
const loading = ref(false)
const drawerOpen = ref(false)
const selectedRequest = ref<LensCopilotUsageItem | null>(null)
const usage = ref<LensCopilotUsageOverview | null>(null)
const periodPreset = ref<'today' | '7d' | '30d' | 'month' | 'custom'>('today')
const customRange = ref<[string, string] | null>(null)
const startDate = ref('')
const endDate = ref('')
const searchQuery = ref('')
const statusFilter = ref('')
const page = ref(1)
const pageSize = ref(20)
let searchTimer: ReturnType<typeof setTimeout> | null = null
let loadRequestId = 0

const questionHistoryEmptyDescription = computed(() => (
  searchQuery.value.trim() || statusFilter.value
    ? t('insight.usage.noFilteredQuestions')
    : t('insight.usage.noQuestionHistory')
))

function openRequest(row: LensCopilotUsageItem) {
  selectedRequest.value = row
  drawerOpen.value = true
}

function localDateValue(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function presetRange(preset: 'today' | '7d' | '30d' | 'month') {
  const end = new Date()
  const start = new Date(end)
  if (preset === '7d') start.setDate(end.getDate() - 6)
  if (preset === '30d') start.setDate(end.getDate() - 29)
  if (preset === 'month') start.setDate(1)
  return [localDateValue(start), localDateValue(end)] as const
}

function selectPreset(preset: 'today' | '7d' | '30d' | 'month') {
  periodPreset.value = preset
  const range = presetRange(preset)
  startDate.value = range[0]
  endDate.value = range[1]
  customRange.value = null
  page.value = 1
  void loadUsage()
}

function applyCustomRange(value: [string, string] | null) {
  if (!value?.[0] || !value?.[1]) {
    // Clearing the picker must leave custom mode; fall back to the default preset.
    if (periodPreset.value === 'custom') {
      selectPreset('today')
    }
    return
  }
  periodPreset.value = 'custom'
  startDate.value = value[0]
  endDate.value = value[1]
  page.value = 1
  void loadUsage()
}

function changePage() {
  void loadUsage()
}

function changePageSize() {
  page.value = 1
  void loadUsage()
}

async function loadUsage() {
  const requestId = ++loadRequestId
  loading.value = true
  try {
    const nextUsage = await fetchCopilotUsage({
      start_date: startDate.value,
      end_date: endDate.value,
      q: searchQuery.value.trim(),
      status: statusFilter.value,
      page: page.value,
      page_size: pageSize.value,
    })
    if (requestId === loadRequestId) usage.value = nextUsage
  } catch (error) {
    if (requestId === loadRequestId) {
      ElMessage.error({ message: apiErrorMessage(error, t('insight.usage.loadFailed')), grouping: true })
    }
  } finally {
    if (requestId === loadRequestId) loading.value = false
  }
}

function statusLabel(status: string) {
  if (status === 'done') return t('insight.usage.statusCompleted')
  if (status === 'failed') return t('insight.usage.statusFailed')
  if (status === 'cancelled') return t('insight.usage.statusCancelled')
  if (status === 'queued') return t('insight.usage.statusQueued')
  if (status === 'running' || status === 'streaming') return t('insight.usage.statusInProgress')
  return status || t('insight.usage.statusUnknown')
}

watch(statusFilter, () => {
  page.value = 1
  void loadUsage()
})

watch(searchQuery, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    void loadUsage()
  }, 320)
})

onMounted(() => {
  const range = presetRange('today')
  startDate.value = range[0]
  endDate.value = range[1]
  void loadUsage()
})

onBeforeUnmount(() => {
  loadRequestId += 1
  if (searchTimer) clearTimeout(searchTimer)
})
</script>

<template>
  <ModulePage
    :menus="insightMenus"
    body-fill
  >
    <div
      v-loading="loading && !usage"
      class="usage-page"
    >
      <div class="usage-table-panel">
        <div class="usage-toolbar">
          <div class="usage-history-actions">
            <div class="usage-search">
              <Search
                :size="15"
                aria-hidden="true"
              />
              <input
                id="usage-question-search"
                v-model="searchQuery"
                type="search"
                name="usage_question_search"
                :aria-label="t('insight.usage.searchAria')"
                :placeholder="t('insight.usage.searchPlaceholder')"
              >
            </div>
            <ElSelect
              v-model="statusFilter"
              clearable
              :placeholder="t('insight.usage.allStatuses')"
              class="usage-filter-select usage-filter-select--status"
            >
              <ElOption
                :label="t('insight.usage.statusCompleted')"
                value="done"
              />
              <ElOption
                :label="t('insight.usage.statusFailed')"
                value="failed"
              />
              <ElOption
                :label="t('insight.usage.statusCancelled')"
                value="cancelled"
              />
            </ElSelect>
          </div>

          <div
            class="usage-period"
            :aria-label="t('insight.usage.periodAria')"
          >
            <span class="usage-period__label">{{ t('insight.usage.dateRange') }}</span>
            <div class="usage-segments">
              <button
                :class="{ active: periodPreset === 'today' }"
                type="button"
                @click="selectPreset('today')"
              >
                {{ t('insight.usage.today') }}
              </button>
              <button
                :class="{ active: periodPreset === '7d' }"
                type="button"
                @click="selectPreset('7d')"
              >
                {{ t('insight.usage.last7Days') }}
              </button>
              <button
                :class="{ active: periodPreset === '30d' }"
                type="button"
                @click="selectPreset('30d')"
              >
                {{ t('insight.usage.last30Days') }}
              </button>
              <button
                :class="{ active: periodPreset === 'month' }"
                type="button"
                @click="selectPreset('month')"
              >
                {{ t('insight.usage.thisMonth') }}
              </button>
            </div>
            <div class="usage-period__tail">
              <ElDatePicker
                :id="['usage-start-date', 'usage-end-date']"
                v-model="customRange"
                type="daterange"
                unlink-panels
                format="YYYY-MM-DD"
                value-format="YYYY-MM-DD"
                :name="['usage_start_date', 'usage_end_date']"
                :aria-label="t('insight.usage.dateRangeAria')"
                range-separator="~"
                :start-placeholder="t('insight.usage.startDate')"
                :end-placeholder="t('insight.usage.endDate')"
                class="usage-date-picker"
                @change="applyCustomRange"
              />
            </div>
          </div>
        </div>

        <ElTable
          v-loading="loading"
          :data="usage?.results || []"
          stripe
          flexible
          height="100%"
          class="hfl-list-table usage-history-table"
          row-key="run_uuid"
        >
          <ElTableColumn
            :label="t('insight.usage.question')"
            min-width="280"
            fixed="left"
          >
            <template #default="{ row }">
              <button
                type="button"
                class="hfl-table-name-link hfl-table-name-link--single usage-history-question"
                @click="openRequest(row)"
              >
                {{ row.question || t('insight.usage.questionUnavailable') }}
              </button>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('insight.usage.chat')"
            min-width="180"
            prop="chat_title"
          />
          <ElTableColumn
            :label="t('insight.usage.backupSource')"
            min-width="220"
          >
            <template #default="{ row }">
              <div class="usage-history-source">
                <strong>{{ row.backup_source_name }}</strong>
                <span>{{ row.scope_summary }}</span>
              </div>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('insight.usage.status')"
            width="110"
          >
            <template #default="{ row }">
              <span
                class="usage-status"
                :class="`is-${row.status}`"
              ><i />{{ statusLabel(row.status) }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('insight.usage.time')"
            width="180"
          >
            <template #default="{ row }">
              <span
                class="usage-table__time"
                :class="{ 'hfl-empty-mark': !row.time }"
              >{{ formatLocalDateTime(row.time) }}</span>
            </template>
          </ElTableColumn>
          <template #empty>
            <ElEmpty :description="questionHistoryEmptyDescription" />
          </template>
        </ElTable>

        <div
          v-if="usage && usage.total > 0"
          class="hfl-list-footer usage-history-footer"
        >
          <HflPagination
            v-model:current-page="page"
            v-model:page-size="pageSize"
            class="hfl-list-footer__pagination"
            layout="total, sizes, prev, pager, next"
            :page-sizes="[20, 30, 50, 100]"
            :total="usage.total"
            @current-change="changePage"
            @size-change="changePageSize"
          />
        </div>
      </div>
    </div>
    <InsightUsageRequestDrawer
      v-model="drawerOpen"
      :row="selectedRequest"
    />
  </ModulePage>
</template>

<style scoped>
.usage-page {
  display: flex;
  min-width: 0;
  flex-direction: column;
  flex: 1 1 0;
  min-height: 0;
  color: var(--color-text-title);
}

.usage-period {
  display: flex;
  min-height: 36px;
  justify-content: flex-end;
  margin-left: auto;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.usage-period__label {
  margin: 0 4px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  font-weight: 500;
}

.usage-segments {
  display: inline-flex;
  padding: 3px;
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  background: var(--color-grey-2);
}

.usage-segments button {
  min-height: 30px;
  padding: 0 13px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
  font: inherit;
  font-size: 13px;
}

.usage-segments button:hover { color: var(--color-text-title); }
.usage-segments button.active {
  background: var(--color-card-bg);
  color: var(--color-primary);
  font-weight: 500;
  box-shadow: 0 1px 3px rgb(16 24 40 / 10%);
}

.usage-period__tail {
  display: flex;
  align-items: center;
  gap: 8px;
}

.usage-period :deep(.usage-date-picker.el-date-editor) {
  width: 260px;
  max-width: 100%;
  height: 34px;
}

.usage-period :deep(.usage-date-picker .el-range-input) { font-size: 13px; }
.usage-period :deep(.usage-date-picker .el-range-separator) { padding: 0 4px; }

.usage-segments button:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
}

.usage-table-panel {
  display: flex;
  flex: 1 1 0;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
}

.usage-history-table {
  flex: 1 1 0;
  min-height: 0;
  width: 100%;
}

.usage-toolbar {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 16px;
  flex: 0 0 auto;
  flex-wrap: wrap;
  padding-bottom: 12px;
  white-space: nowrap;
}

.usage-history-actions {
  min-width: 0;
  flex: 0 1 auto;
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  margin: 0;
}

.usage-search {
  display: flex;
  box-sizing: border-box;
  width: 260px;
  height: 34px;
  align-items: center;
  gap: 7px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-card-bg);
  color: var(--color-text-tertiary);
}

.usage-search:focus-within { border-color: var(--color-primary); }
.usage-search input {
  width: 100%;
  min-width: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--color-text-title);
  font: inherit;
  font-size: 13px;
}

.usage-filter-select { width: 140px; flex: 0 0 140px; }
.usage-filter-select--status { width: 140px; }
.usage-history-question {
  display: block;
  overflow: hidden;
  width: 100%;
  color: var(--el-color-primary);
  font-size: 12px;
  font-weight: 500;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.usage-history-source { display: grid; min-width: 0; gap: 2px; }
.usage-history-source strong,
.usage-history-source span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.usage-history-source strong { color: var(--color-text-title); font-size: 12px; font-weight: 500; }
.usage-history-source span { color: var(--color-text-tertiary); font-size: 11px; }
.usage-table__time { color: var(--color-text-tertiary); white-space: nowrap; }
.usage-history-footer { flex: 0 0 auto; border-top: 1px solid var(--color-border-light); }

.usage-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 7px;
  border-radius: 999px;
  background: #ecfdf3;
  color: #027a48;
  font-size: 11px;
  font-weight: 600;
}
.usage-status i { width: 6px; height: 6px; border-radius: 999px; background: currentColor; }
.usage-status.is-failed { background: #fef3f2; color: #d92d20; }
.usage-status.is-cancelled { background: var(--color-grey-3); color: var(--color-text-tertiary); }
.usage-status.is-queued,
.usage-status.is-running,
.usage-status.is-streaming { background: #f2f0ff; color: #654cf0; }
@media (max-width: 1040px) {
  .usage-toolbar { align-items: flex-start; flex-wrap: wrap; }
  .usage-period { max-width: 100%; }
  .usage-history-actions { max-width: 100%; }
  .usage-search { flex: 1 1 260px; }
}

@media (max-width: 760px) {
  .usage-page { min-height: 420px; }
  .usage-period { width: 100%; align-items: stretch; flex-direction: column; margin-left: 0; }
  .usage-segments { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .usage-segments button { padding: 0 6px; }
  .usage-period__tail { width: 100%; margin-left: 0; }
  .usage-period__tail :deep(.usage-date-picker) { width: 0; min-width: 0; flex: 1; }
  .usage-history-actions { width: 100%; align-items: stretch; flex-direction: column; }
  .usage-search,
  .usage-filter-select { width: 100%; flex-basis: auto; }
}
</style>
