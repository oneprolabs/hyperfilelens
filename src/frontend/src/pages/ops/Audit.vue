<script setup lang="ts">
import { computed, onMounted, reactive, ref, toRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Download, Filter, RefreshCw, ShieldCheck, AlertCircle, X } from 'lucide-vue-next'
import ModulePage from '../../components/ModulePage.vue'
import OpsStatCard from '../../components/ops/OpsStatCard.vue'
import HflTablePanel from '../../components/HflTablePanel.vue'
import HflDateTimeRangePicker from '../../components/HflDateTimeRangePicker.vue'
import { useOpsMenus } from '../../composables/useOpsMenus'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import { useListSearch } from '../../composables/useListSearch'
import { formatLocalDateTime } from '../../lib/dateTime'
import {
  auditStatistics,
  exportAuditLogs,
  getAuditLog,
  listAuditLogs,
  type AuditLogRow,
} from '../../lib/auditApi'
import { ElMessage } from 'element-plus'
import { getEffectiveOrgKey } from '../../composables/useAuth'
import { apiErrorMessageI18n } from '../../lib/api'

const { t, te } = useI18n()
const route = useRoute()
const router = useRouter()
const opsMenus = useOpsMenus()
const { drawerSize: filterDrawerSize } = useResponsiveDrawerWidth(2)
const { drawerSize: detailDrawerSize } = useResponsiveDrawerWidth()
const rows = ref<AuditLogRow[]>([])
const stats = ref({
  total_count: 0,
  today_count: 0,
  success_rate: 0,
  failure_count: 0,
})
const loading = ref(false)
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })

const filters = reactive({
  search: '',
  search_field: 'all',
  action: '',
  resource_type: '',
  result: '',
  time_range: '',
  start_date: '',
  end_date: '',
  correlation_id: '',
})
const {
  appliedSearch,
  clearSearch,
  handleSearchFieldChange,
  runSearchNow: runFilterSearch,
} = useListSearch(toRef(filters, 'search'), () => applyFilters())
const advancedFilterOpen = ref(false)
const lastQuickTimeRange = ref('')
const advancedFilterDraft = reactive({
  action: '',
  resource_type: '',
  result: '',
  start_date: '',
  end_date: '',
})

const openDetail = ref(false)
const detailLog = ref<AuditLogRow | null>(null)

const actionOptions = [
  'create',
  'update',
  'delete',
  'execute',
  'login',
  'logout',
  'task.create',
  'task.finish',
  'alert.ack',
  'alert.resolve',
  'alert.policy.create',
  'alert.policy.update',
  'alert.policy.delete',
  'restore.restore_plan.run',
  'node_task.dispatch',
  'notification.delivery_attempt',
]

const resourceTypeOptions = [
  'task',
  'alert',
  'alert_policy',
  'restore_plan',
  'node_task',
  'notification_delivery',
  'notification_channel',
  'node',
  'repository',
  'policy',
  'organization',
  'user',
  'system',
]

const searchTypes = computed(() => [
  { value: 'all', label: t('ops.audit.searchAll') },
  { value: 'user', label: t('ops.audit.searchUser') },
  { value: 'resource', label: t('ops.audit.searchResource') },
  { value: 'ip', label: t('ops.audit.searchIp') },
])

const datePresets = [
  { value: '', label: t('ops.audit.dateAll') },
  { value: '1h', label: t('ops.audit.timeRange1h') },
  { value: '24h', label: t('ops.audit.timeRange24h') },
  { value: '7d', label: t('ops.audit.timeRange7d') },
  { value: '30d', label: t('ops.audit.timeRange30d') },
  { value: 'custom', label: t('ops.audit.dateCustom') },
]
const auditDateTimeRangePresets = computed(() => [
  { value: '1h', label: t('ops.audit.timeRange1h'), hours: 1 },
  { value: '24h', label: t('ops.audit.timeRange24h'), hours: 24 },
  { value: '7d', label: t('ops.audit.timeRange7d'), hours: 7 * 24 },
  { value: '30d', label: t('ops.audit.timeRange30d'), hours: 30 * 24 },
])

const advancedFilterCount = computed(() => {
  let count = 0
  if (filters.action) count += 1
  if (filters.resource_type) count += 1
  if (filters.result) count += 1
  if (filters.time_range === 'custom' && (filters.start_date || filters.end_date)) count += 1
  return count
})

const activeCorrelation = computed(() => (filters.correlation_id || '').trim())

function correlationFromRoute(): string {
  const raw = route.query.correlation_id
  if (raw == null) return ''
  return String(Array.isArray(raw) ? raw[0] : raw).trim()
}

function applyRouteCorrelation() {
  filters.correlation_id = correlationFromRoute()
}

function formatTime(iso?: string) {
  return formatLocalDateTime(iso, t('ops.audit.emptyMark'))
}

function displayValue(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return t('ops.audit.emptyMark')
  return String(value)
}

function hasDisplayValue(value?: string | number | null) {
  return value !== null && value !== undefined && value !== ''
}

function resultTagType(result?: string | null): 'success' | 'danger' | 'warning' | 'info' {
  if (result === 'success') return 'success'
  if (result === 'partial') return 'warning'
  if (result === 'failure' || result === 'failed') return 'danger'
  return 'info'
}

function resourcePrimary(row: AuditLogRow) {
  return row.resource_name || row.resource_type || t('ops.audit.emptyMark')
}

function hasObjectValue(value?: Record<string, unknown>) {
  return !!value && Object.keys(value).length > 0
}

function formatJson(value?: Record<string, unknown>) {
  return hasObjectValue(value) ? JSON.stringify(value, null, 2) : t('ops.audit.emptyMark')
}

function actionLabel(action: string) {
  const key = `ops.audit.actions.${action.replace(/\./g, '_')}`
  if (te(key)) return t(key)
  const legacyKey = `ops.audit.action_${action.replace(/\./g, '')}`
  if (te(legacyKey)) return t(legacyKey)
  return action
}

function pad2(n: number) {
  return String(n).padStart(2, '0')
}

function formatLocalInputDateTime(date?: Date | null) {
  if (!(date instanceof Date) || !Number.isFinite(date.getTime())) return ''
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`
}

function parseLocalInputDateTime(value: string) {
  if (!value) return null
  const normalized = value.length === 16 ? `${value}:00` : value
  const date = new Date(normalized)
  return Number.isFinite(date.getTime()) ? date : null
}

function isoDateTimeParam(value: string, inclusiveEnd = false) {
  const date = parseLocalInputDateTime(value)
  if (!date) return value
  if (inclusiveEnd) date.setMilliseconds(999)
  return date.toISOString()
}

const auditAdvancedRangeLabel = computed(() => {
  if (!advancedFilterDraft.start_date || !advancedFilterDraft.end_date) return t('ops.audit.dateCustom')
  return `${formatLocalDateTime(isoDateTimeParam(advancedFilterDraft.start_date))} ~ ${formatLocalDateTime(isoDateTimeParam(advancedFilterDraft.end_date))}`
})

function setAdvancedDateTimeRange(start: Date, end: Date) {
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime())) return
  advancedFilterDraft.start_date = formatLocalInputDateTime(start)
  advancedFilterDraft.end_date = formatLocalInputDateTime(end)
}

function onAdvancedDateTimePreset(_value: string, hours?: number) {
  if (!hours) return
  const end = new Date()
  const start = new Date(end.getTime() - hours * 60 * 60 * 1000)
  setAdvancedDateTimeRange(start, end)
}

function onAdvancedDateTimeApply(startValue: string, endValue: string) {
  const start = parseLocalInputDateTime(startValue)
  const end = parseLocalInputDateTime(endValue)
  if (!start || !end) return
  setAdvancedDateTimeRange(start, end)
}

function onAdvancedDateTimeClear() {
  advancedFilterDraft.start_date = ''
  advancedFilterDraft.end_date = ''
}


function buildParams(): Record<string, string | number> {
  const p: Record<string, string | number> = {
    page: pagination.page,
    page_size: pagination.pageSize,
    org: getEffectiveOrgKey() || '',
  }
  if (appliedSearch.value) {
    p.search = appliedSearch.value
    if (filters.search_field !== 'all') p.search_field = filters.search_field
  }
  if (filters.action) p.action = filters.action
  if (filters.resource_type) p.resource_type = filters.resource_type
  if (filters.result) p.result = filters.result
  if (filters.time_range && filters.time_range !== 'custom') p.time_range = filters.time_range
  if (filters.time_range === 'custom') {
    if (filters.start_date) p.start_date = isoDateTimeParam(filters.start_date)
    if (filters.end_date) p.end_date = isoDateTimeParam(filters.end_date, true)
  }
  if (activeCorrelation.value) {
    p.correlation_id = activeCorrelation.value
    p.request_id = activeCorrelation.value
  }
  return p
}

async function fetchStats() {
  try {
    stats.value = await auditStatistics()
  } catch {
    stats.value = { total_count: 0, today_count: 0, success_rate: 0, failure_count: 0 }
  }
}

async function fetchLogs(options: { manageLoading?: boolean } = {}) {
  const manageLoading = options.manageLoading ?? true
  if (manageLoading) loading.value = true
  try {
    const res = await listAuditLogs(buildParams())
    rows.value = res.results
    pagination.count = res.count
  } finally {
    if (manageLoading) loading.value = false
  }
}

async function openDetailModal(row: AuditLogRow) {
  detailLog.value = row
  openDetail.value = true
  try {
    detailLog.value = await getAuditLog(row.id)
  } catch {
    detailLog.value = row
  }
}

function closeDetailModal() {
  openDetail.value = false
}

function clearCorrelationFilter() {
  filters.correlation_id = ''
  const q = { ...route.query }
  delete q.correlation_id
  void router.replace({ path: route.path, query: q })
}

function syncAdvancedFilterDraft() {
  advancedFilterDraft.action = filters.action
  advancedFilterDraft.resource_type = filters.resource_type
  advancedFilterDraft.result = filters.result
  advancedFilterDraft.start_date = filters.start_date
  advancedFilterDraft.end_date = filters.end_date
}

function openAdvancedFilterDrawer() {
  syncAdvancedFilterDraft()
  advancedFilterOpen.value = true
}

function resetAdvancedFilterDraft() {
  advancedFilterDraft.action = ''
  advancedFilterDraft.resource_type = ''
  advancedFilterDraft.result = ''
  advancedFilterDraft.start_date = ''
  advancedFilterDraft.end_date = ''
}

function cancelAdvancedFilter() {
  advancedFilterOpen.value = false
}

function applyAdvancedFilters() {
  filters.action = advancedFilterDraft.action
  filters.resource_type = advancedFilterDraft.resource_type
  filters.result = advancedFilterDraft.result
  filters.start_date = advancedFilterDraft.start_date
  filters.end_date = advancedFilterDraft.end_date
  if (filters.start_date || filters.end_date) filters.time_range = 'custom'
  else if (filters.time_range === 'custom') filters.time_range = lastQuickTimeRange.value
  advancedFilterOpen.value = false
  runFilterSearch()
}

function onAdvancedFilterClosed() {
  if (filters.time_range === 'custom' && !filters.start_date && !filters.end_date) {
    filters.time_range = lastQuickTimeRange.value
  }
  syncAdvancedFilterDraft()
}

function onAuditTimeRangeChange(value: string) {
  if (value === 'custom') {
    openAdvancedFilterDrawer()
    return
  }
  lastQuickTimeRange.value = value
  filters.start_date = ''
  filters.end_date = ''
  runFilterSearch()
}

async function exportLogs(format: 'json' | 'csv') {
  try {
    const params: Record<string, string> = {}
    if (filters.action) params.action = filters.action
    if (filters.resource_type) params.resource_type = filters.resource_type
    if (filters.result) params.result = filters.result
    await exportAuditLogs(format, params)
  } catch (e: unknown) {
    ElMessage.error({
      message: apiErrorMessageI18n(e, t, t('common.exportFailed', 'Export failed')),
      grouping: true,
    })
  }
}

async function applyFilters() {
  pagination.page = 1
  loading.value = true
  try {
    await Promise.all([fetchStats(), fetchLogs({ manageLoading: false })])
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  applyRouteCorrelation()
  void Promise.all([fetchStats(), fetchLogs()])
})

watch(
  () => route.query.correlation_id,
  () => {
    applyRouteCorrelation()
    runFilterSearch()
  },
)

watch(
  () => filters.time_range,
  (value) => {
    if (value !== 'custom') {
      filters.start_date = ''
      filters.end_date = ''
    }
  },
)
</script>

<template>
  <ModulePage
    :title="t('ops.audit.pageTitle')"
    :menus="opsMenus"
    body-fill
  >
    <div class="hfl-ops-page hfl-ops-page--fill">
      <div class="hfl-ops-stats-grid hfl-ops-stats-grid--4">
        <OpsStatCard
          :label="t('ops.audit.statTotal')"
          :value="stats.total_count"
          tone="primary"
          accent-side="left"
        />
        <OpsStatCard
          :label="t('ops.audit.statToday')"
          :value="stats.today_count"
          tone="info"
          accent-side="left"
        />
        <OpsStatCard
          :label="t('ops.audit.statFailures')"
          :value="stats.failure_count"
          tone="danger"
          accent-side="left"
        />
        <OpsStatCard
          :label="t('ops.audit.statSuccessRate')"
          :value="`${stats.success_rate}%`"
          tone="success"
          accent-side="left"
        />
      </div>

      <HflTablePanel fill>
        <template #toolbar>
          <el-dropdown>
            <el-button
              :title="t('ops.audit.export')"
              :aria-label="t('ops.audit.export')"
            >
              <Download :size="16" />
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="exportLogs('json')">
                  JSON
                </el-dropdown-item>
                <el-dropdown-item @click="exportLogs('csv')">
                  CSV
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
        <template #toolbar-actions>
          <el-input
            v-model="filters.search"
            class="hfl-list-search hfl-list-search-group"
            clearable
            :placeholder="t('ops.audit.searchPlaceholder')"
            @clear="clearSearch"
          >
            <template #prepend>
              <el-select
                v-model="filters.search_field"
                @change="handleSearchFieldChange"
              >
                <el-option
                  v-for="st in searchTypes"
                  :key="st.value"
                  :value="st.value"
                  :label="st.label"
                />
              </el-select>
            </template>
          </el-input>
          <el-select
            v-model="filters.time_range"
            clearable
            style="width: 130px"
            @change="onAuditTimeRangeChange"
          >
            <el-option
              v-for="p in datePresets"
              :key="p.value"
              :value="p.value"
              :label="p.label"
            />
          </el-select>
        </template>
        <template #toolbar-utility>
          <el-button
            class="hfl-filter-button"
            :class="{ 'hfl-filter-button--active': advancedFilterCount > 0 }"
            :title="t('ops.audit.advancedFilter')"
            :aria-label="t('ops.audit.advancedFilter')"
            @click="openAdvancedFilterDrawer"
          >
            <el-badge
              v-if="advancedFilterCount > 0"
              :value="advancedFilterCount"
              :max="9"
              class="hfl-filter-badge"
            >
              <Filter :size="16" />
            </el-badge>
            <Filter
              v-else
              :size="16"
            />
          </el-button>
          <el-button
            class="hfl-refresh-button"
            :title="t('ops.task.btnRefresh')"
            :aria-label="t('ops.task.btnRefresh')"
            :disabled="loading"
            @click="applyFilters"
          >
            <RefreshCw
              :size="16"
              :class="{ 'is-spinning': loading }"
            />
          </el-button>
        </template>

        <el-alert
          v-if="activeCorrelation"
          type="info"
          :closable="false"
          show-icon
          class="mx-4 mt-4"
        >
          <div class="flex flex-wrap items-center gap-2">
            <span>{{ t('ops.audit.bannerCorrelation', { id: activeCorrelation }) }}</span>
            <el-button
              size="small"
              text
              type="primary"
              @click="clearCorrelationFilter"
            >
              <X class="mr-1 h-3.5 w-3.5" />
              {{ t('ops.audit.clearCorrelationFilter') }}
            </el-button>
          </div>
        </el-alert>

        <template #table="{ tableMaxHeight }">
          <el-table
            v-table-column-resize="'ops.audit'"
            v-table-overflow-title
            v-loading="loading"
            :data="rows"
            stripe
            flexible
            class="hfl-list-table"
            row-key="id"
            :max-height="tableMaxHeight"
          >
            <el-table-column
              :label="t('ops.audit.colAction')"
              width="220"
              fixed="left"
            >
              <template #default="{ row }">
                <div class="hfl-ops-primary-cell">
                  <div class="min-w-0">
                    <div class="flex min-w-0 items-center gap-1">
                      <button
                        type="button"
                        class="hfl-table-name-link hfl-table-name-link--single min-w-0"
                        @click="openDetailModal(row)"
                      >
                        {{ actionLabel(row.action) }}
                      </button>
                    </div>
                    <div
                      v-if="row.resource_type"
                      class="hfl-ops-cell-stack__meta uppercase"
                    >
                      {{ row.resource_type }}
                    </div>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.audit.colTime')"
              width="170"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-time"
                  :class="{ 'hfl-empty-mark': !(row.timestamp || row.created_at) }"
                >{{ formatTime(row.timestamp || row.created_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.audit.colUser')"
              min-width="150"
              class-name="hfl-audit-user-column"
            >
              <template #default="{ row }">
                <span class="hfl-ops-user-chip hfl-audit-user-cell">
                  <span class="hfl-audit-user-cell__name font-medium">
                    {{ row.user_display || t('ops.audit.systemUser') }}
                  </span>
                </span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.audit.colResult')"
              width="100"
            >
              <template #default="{ row }">
                <el-tag
                  class="hfl-ops-result-tag"
                  :type="resultTagType(row.result)"
                  size="small"
                >
                  <ShieldCheck
                    v-if="row.result === 'success'"
                    class="hfl-ops-result-tag__icon"
                  />
                  <AlertCircle
                    v-else-if="hasDisplayValue(row.result)"
                    class="hfl-ops-result-tag__icon"
                  />
                  <span
                    class="hfl-ops-result-tag__text"
                    :data-table-overflow-title="displayValue(row.result)"
                  >
                    {{ displayValue(row.result) }}
                  </span>
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.audit.colResource')"
              min-width="160"
            >
              <template #default="{ row }">
                <div class="hfl-ops-cell-stack">
                  <div :class="hasDisplayValue(row.resource_name || row.resource_type) ? 'hfl-table-cell-mono' : 'hfl-empty-mark'">
                    {{ resourcePrimary(row) }}
                  </div>
                  <div
                    v-if="row.resource_id"
                    class="hfl-ops-cell-stack__meta hfl-table-cell-mono"
                  >
                    {{ row.resource_id }}
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.audit.colIp')"
              width="130"
              prop="ip_address"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-mono"
                  :class="{ 'hfl-empty-mark': !row.ip_address }"
                >{{ row.ip_address || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column
              v-if="!activeCorrelation"
              :label="t('ops.audit.colCorrelationId')"
              width="120"
            >
              <template #default="{ row }">
                <span :class="hasDisplayValue(row.correlation_id || row.request_id) ? 'hfl-table-cell-mono' : 'hfl-empty-mark'">
                  {{ row.correlation_id || row.request_id || t('ops.audit.emptyMark') }}
                </span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty :description="t('ops.audit.empty')" />
            </template>
          </el-table>
        </template>

        <template #footer>
          <HflPagination
            v-model:current-page="pagination.page"
            v-model:page-size="pagination.pageSize"
            class="hfl-list-footer__pagination"
            layout="total, sizes, prev, pager, next"
            :total="pagination.count"
            :page-sizes="[20, 30, 50, 100]"
            @current-change="fetchLogs"
            @size-change="() => { pagination.page = 1; fetchLogs() }"
          />
        </template>
      </HflTablePanel>
    </div>

    <el-drawer
      v-model="advancedFilterOpen"
      :title="t('ops.audit.advancedFilter')"
      :size="filterDrawerSize"
      class="hfl-audit-filter-drawer"
      @closed="onAdvancedFilterClosed"
    >
      <el-form
        label-position="top"
        class="hfl-audit-filter-form"
      >
        <el-form-item :label="t('ops.audit.phFilterAction')">
          <el-select
            v-model="advancedFilterDraft.action"
            clearable
            class="w-full"
            :placeholder="t('ops.audit.phFilterAction')"
          >
            <el-option
              v-for="a in actionOptions"
              :key="a"
              :value="a"
              :label="actionLabel(a)"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('ops.audit.phFilterResourceType')">
          <el-select
            v-model="advancedFilterDraft.resource_type"
            clearable
            class="w-full"
            :placeholder="t('ops.audit.phFilterResourceType')"
          >
            <el-option
              v-for="rt in resourceTypeOptions"
              :key="rt"
              :value="rt"
              :label="t(`ops.audit.resourceTypes.${rt}`, rt)"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('ops.audit.phFilterResult')">
          <el-select
            v-model="advancedFilterDraft.result"
            clearable
            class="w-full"
            :placeholder="t('ops.audit.phFilterResult')"
          >
            <el-option
              value="success"
              :label="t('ops.audit.resultSuccess')"
            />
            <el-option
              value="failure"
              :label="t('ops.audit.resultFailure')"
            />
            <el-option
              value="partial"
              :label="t('ops.audit.resultPartial')"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('ops.audit.dateCustom')">
          <HflDateTimeRangePicker
            class="hfl-filter-range"
            constrain-to-trigger
            :label="auditAdvancedRangeLabel"
            :presets="auditDateTimeRangePresets"
            :start="advancedFilterDraft.start_date"
            :end="advancedFilterDraft.end_date"
            :clear-text="t('ops.audit.resetFilter')"
            :apply-text="t('ops.audit.applyFilter')"
            @preset="onAdvancedDateTimePreset"
            @apply="onAdvancedDateTimeApply"
            @clear="onAdvancedDateTimeClear"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="el-drawer__footer-main">
          <el-button @click="cancelAdvancedFilter">
            {{ t('ops.audit.cancelFilter') }}
          </el-button>
          <div class="el-drawer__footer-actions">
            <el-button @click="resetAdvancedFilterDraft">
              {{ t('ops.audit.resetFilter') }}
            </el-button>
            <el-button
              type="primary"
              @click="applyAdvancedFilters"
            >
              {{ t('ops.audit.applyFilter') }}
            </el-button>
          </div>
        </div>
      </template>
    </el-drawer>

    <ElDrawer
      v-model="openDetail"
      destroy-on-close
      :size="detailDrawerSize"
      class="hfl-detail-drawer hfl-audit-detail-drawer"
      @closed="closeDetailModal"
    >
      <template #header>
        <span class="hfl-detail-drawer__title">{{ t('ops.audit.detailTitle') }}</span>
      </template>

      <div
        v-if="detailLog"
        class="hfl-detail-drawer__body"
      >
        <div class="hfl-detail-sections">
          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('ops.audit.detailSectionBasic') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.colTime') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !(detailLog.timestamp || detailLog.created_at) }"
                >{{ formatTime(detailLog.timestamp || detailLog.created_at) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.colUser') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !hasDisplayValue(detailLog.user_display) }"
                >{{ displayValue(detailLog.user_display) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.colAction') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !hasDisplayValue(detailLog.action) }"
                >{{ actionLabel(detailLog.action) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.colResult') }}</span>
                <span class="hfl-detail-row__value">
                  <el-tag
                    class="hfl-ops-result-tag"
                    :type="resultTagType(detailLog.result)"
                    size="small"
                  >
                    <ShieldCheck
                      v-if="detailLog.result === 'success'"
                      class="hfl-ops-result-tag__icon"
                    />
                    <AlertCircle
                      v-else-if="hasDisplayValue(detailLog.result)"
                      class="hfl-ops-result-tag__icon"
                    />
                    <span class="hfl-ops-result-tag__text">{{ displayValue(detailLog.result) }}</span>
                  </el-tag>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.colIp') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !hasDisplayValue(detailLog.ip_address) }"
                >{{ displayValue(detailLog.ip_address) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.colOrg') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !hasDisplayValue(detailLog.organization_name || detailLog.organization) }"
                >{{ displayValue(detailLog.organization_name || detailLog.organization) }}</span>
              </div>
            </div>
          </section>

          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('ops.audit.detailSectionResource') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.phFilterResourceType') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !hasDisplayValue(detailLog.resource_type) }"
                >{{ displayValue(detailLog.resource_type) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.colResourceName') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !hasDisplayValue(detailLog.resource_name) }"
                >{{ displayValue(detailLog.resource_name) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.detailResourceId') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="hasDisplayValue(detailLog.resource_id) ? 'hfl-table-cell-mono' : 'hfl-empty-mark'"
                >{{ displayValue(detailLog.resource_id) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.detailTargetType') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !hasDisplayValue(detailLog.target_type) }"
                >{{ displayValue(detailLog.target_type) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.detailTargetId') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="hasDisplayValue(detailLog.target_id) ? 'hfl-table-cell-mono' : 'hfl-empty-mark'"
                >{{ displayValue(detailLog.target_id) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.colId') }}</span>
                <span
                  class="hfl-detail-row__value hfl-table-cell-mono"
                  :class="{ 'hfl-detail-row__empty': !hasDisplayValue(detailLog.id) }"
                >{{ displayValue(detailLog.id) }}</span>
              </div>
            </div>
          </section>

          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('ops.audit.detailSectionRequest') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.detailRequestMethod') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="hasDisplayValue(detailLog.request_method) ? 'hfl-table-cell-mono' : 'hfl-empty-mark'"
                >{{ displayValue(detailLog.request_method) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.requestPath') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="hasDisplayValue(detailLog.request_path) ? 'hfl-table-cell-mono' : 'hfl-empty-mark'"
                >{{ displayValue(detailLog.request_path) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.detailRequestId') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="hasDisplayValue(detailLog.request_id) ? 'hfl-table-cell-mono' : 'hfl-empty-mark'"
                >{{ displayValue(detailLog.request_id) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.colCorrelationId') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="hasDisplayValue(detailLog.correlation_id) ? 'hfl-table-cell-mono' : 'hfl-empty-mark'"
                >{{ displayValue(detailLog.correlation_id) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.detailSessionId') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="hasDisplayValue(detailLog.session_id) ? 'hfl-table-cell-mono' : 'hfl-empty-mark'"
                >{{ displayValue(detailLog.session_id) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.audit.detailErrorCode') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="hasDisplayValue(detailLog.error_code) ? 'hfl-table-cell-mono' : 'hfl-empty-mark'"
                >{{ displayValue(detailLog.error_code) }}</span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('ops.audit.userAgent') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !hasDisplayValue(detailLog.user_agent) }"
                >{{ displayValue(detailLog.user_agent) }}</span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('ops.audit.errorMessage') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-audit-detail-error': detailLog.error_message, 'hfl-detail-row__empty': !hasDisplayValue(detailLog.error_message) }"
                >
                  {{ displayValue(detailLog.error_message) }}
                </span>
              </div>
            </div>
          </section>

          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('ops.audit.detailSectionPayload') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('ops.audit.colDetails') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !hasDisplayValue(detailLog.details) }"
                >{{ displayValue(detailLog.details) }}</span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('ops.audit.changes') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                  <pre
                    v-if="hasObjectValue(detailLog.changes)"
                    class="hfl-audit-detail-json"
                  >{{ formatJson(detailLog.changes) }}</pre>
                  <span
                    v-else
                    class="hfl-empty-mark"
                  >{{ t('ops.audit.emptyMark') }}</span>
                </span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('ops.audit.queryParams') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                  <pre
                    v-if="hasObjectValue(detailLog.request_query)"
                    class="hfl-audit-detail-json"
                  >{{ formatJson(detailLog.request_query) }}</pre>
                  <span
                    v-else
                    class="hfl-empty-mark"
                  >{{ t('ops.audit.emptyMark') }}</span>
                </span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('ops.audit.requestBody') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                  <pre
                    v-if="hasObjectValue(detailLog.request_body)"
                    class="hfl-audit-detail-json"
                  >{{ formatJson(detailLog.request_body) }}</pre>
                  <span
                    v-else
                    class="hfl-empty-mark"
                  >{{ t('ops.audit.emptyMark') }}</span>
                </span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('ops.audit.metadata') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                  <pre
                    v-if="hasObjectValue(detailLog.metadata)"
                    class="hfl-audit-detail-json"
                  >{{ formatJson(detailLog.metadata) }}</pre>
                  <span
                    v-else
                    class="hfl-empty-mark"
                  >{{ t('ops.audit.emptyMark') }}</span>
                </span>
              </div>
            </div>
          </section>
        </div>
      </div>
    </ElDrawer>
  </ModulePage>
</template>

<style scoped>
.hfl-list-table :deep(.hfl-audit-user-column .cell) {
  padding-right: 12px;
  padding-left: 12px;
}

.hfl-audit-user-cell {
  max-width: 100%;
  line-height: 22px;
}

.hfl-audit-user-cell__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-title);
}

.hfl-audit-detail-error {
  color: var(--color-error-text);
}

.hfl-audit-filter-drawer :deep(.el-drawer__body) {
  padding: 18px 20px;
}

.hfl-audit-detail-drawer :deep(.el-drawer__body) {
  padding-top: 0;
}

.hfl-audit-detail-json {
  width: 100%;
  max-height: 220px;
  padding: 10px 12px;
  margin: 0;
  overflow: auto;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-grey-1);
  color: var(--color-text-primary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
}

.hfl-audit-filter-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.hfl-audit-filter-date-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  width: 100%;
}

.hfl-audit-filter-date {
  width: 100%;
}

@media (max-width: 640px) {
  .hfl-audit-filter-date-grid {
    grid-template-columns: 1fr;
  }
}
</style>
