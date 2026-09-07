<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { RefreshCw, Search } from 'lucide-vue-next'
import ModulePage from '../../components/ModulePage.vue'
import HflPagination from '../../components/HflPagination.vue'
import HflTablePanel from '../../components/HflTablePanel.vue'
import OpsStatCard from '../../components/ops/OpsStatCard.vue'
import { useOpsMenus } from '../../composables/useOpsMenus'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import { api, apiErrorMessage } from '../../lib/api'
import { formatLocalDateTime } from '../../lib/dateTime'
import { unwrapApiPayload } from '../../lib/parse'

type EventCategory = 'protection' | 'infrastructure' | 'system'
type EventSeverity = 'information' | 'warning' | 'critical'

type OperationalEvent = {
  id: string
  event_type: string
  category: EventCategory
  severity: EventSeverity
  title: string
  details: string
  occurred_at: string
  resource_type: string
  resource_id: string
  resource_name: string
  source: string
  target_path: string
  correlation_id: string
}

type EventResponse = {
  count: number
  stats: {
    total: number
    critical: number
    warning: number
    information: number
  }
  results: OperationalEvent[]
}

const { t } = useI18n()
const router = useRouter()
const opsMenus = useOpsMenus()
const { drawerSize: detailDrawerSize } = useResponsiveDrawerWidth(3)
const loading = ref(false)
const loadError = ref<string | null>(null)
const events = ref<OperationalEvent[]>([])
const selectedEvent = ref<OperationalEvent | null>(null)
const detailOpen = ref(false)
const filters = reactive({
  search: '',
  category: '',
  severity: '',
  period: '24h',
})
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })
const stats = reactive({ total: 0, critical: 0, warning: 0, information: 0 })
let searchTimer: ReturnType<typeof setTimeout> | undefined
let loadRequestSequence = 0

const categoryOptions = computed(() => [
  { value: 'protection', label: t('ops.events.categories.protection') },
  { value: 'infrastructure', label: t('ops.events.categories.infrastructure') },
  { value: 'system', label: t('ops.events.categories.system') },
])

const severityOptions = computed(() => [
  { value: 'critical', label: t('ops.events.severities.critical') },
  { value: 'warning', label: t('ops.events.severities.warning') },
  { value: 'information', label: t('ops.events.severities.information') },
])

const periodOptions = computed(() => [
  { value: '24h', label: t('ops.events.periods.last24Hours') },
  { value: '7d', label: t('ops.events.periods.last7Days') },
  { value: '30d', label: t('ops.events.periods.last30Days') },
  { value: 'all', label: t('ops.events.periods.allTime') },
])

function categoryLabel(category: EventCategory) {
  return t(`ops.events.categories.${category}`)
}

function severityLabel(severity: EventSeverity) {
  return t(`ops.events.severities.${severity}`)
}

function severityTagType(severity: EventSeverity): 'danger' | 'warning' | 'info' {
  if (severity === 'critical') return 'danger'
  if (severity === 'warning') return 'warning'
  return 'info'
}

async function loadEvents() {
  const requestId = ++loadRequestSequence
  loading.value = true
  loadError.value = null
  try {
    const query = new URLSearchParams({
      page: String(pagination.page),
      page_size: String(pagination.pageSize),
      period: filters.period,
    })
    if (filters.search.trim()) query.set('search', filters.search.trim())
    if (filters.category) query.set('category', filters.category)
    if (filters.severity) query.set('severity', filters.severity)
    const data = unwrapApiPayload<EventResponse>(
      await api<unknown>(`/api/v1/monitors/events/?${query}`),
    )
    if (requestId !== loadRequestSequence) return
    events.value = Array.isArray(data.results) ? data.results : []
    pagination.count = Number(data.count) || 0
    stats.total = Number(data.stats?.total) || 0
    stats.critical = Number(data.stats?.critical) || 0
    stats.warning = Number(data.stats?.warning) || 0
    stats.information = Number(data.stats?.information) || 0
  } catch (error) {
    if (requestId !== loadRequestSequence) return
    loadError.value = apiErrorMessage(error)
    events.value = []
    pagination.count = 0
    stats.total = 0
    stats.critical = 0
    stats.warning = 0
    stats.information = 0
  } finally {
    if (requestId === loadRequestSequence) loading.value = false
  }
}

function reloadFromFirstPage() {
  if (pagination.page !== 1) {
    pagination.page = 1
    return
  }
  void loadEvents()
}

function openDetail(event: OperationalEvent) {
  selectedEvent.value = event
  detailOpen.value = true
}

function openResource(event: OperationalEvent) {
  if (event.target_path) void router.push(event.target_path)
}

onMounted(loadEvents)
onUnmounted(() => {
  loadRequestSequence += 1
  if (searchTimer) clearTimeout(searchTimer)
})

watch(
  () => filters.search,
  () => {
    if (searchTimer) clearTimeout(searchTimer)
    searchTimer = setTimeout(reloadFromFirstPage, 350)
  },
)
watch(
  () => [filters.category, filters.severity, filters.period],
  reloadFromFirstPage,
)
watch(
  () => [pagination.page, pagination.pageSize],
  () => void loadEvents(),
)
</script>

<template>
  <ModulePage
    :title="t('ops.events.title')"
    :menus="opsMenus"
    body-fill
  >
    <div class="hfl-ops-page hfl-ops-page--fill events-page">
      <div class="hfl-ops-stats-grid hfl-ops-stats-grid--4">
        <OpsStatCard
          :label="t('ops.events.stats.total')"
          :value="stats.total"
          tone="primary"
          accent-side="left"
        />
        <OpsStatCard
          :label="t('ops.events.stats.critical')"
          :value="stats.critical"
          tone="danger"
          accent-side="left"
        />
        <OpsStatCard
          :label="t('ops.events.stats.warning')"
          :value="stats.warning"
          tone="warning"
          accent-side="left"
        />
        <OpsStatCard
          :label="t('ops.events.stats.information')"
          :value="stats.information"
          tone="info"
          accent-side="left"
        />
      </div>

      <p
        v-if="loadError"
        class="events-page__error"
      >
        {{ t('ops.events.loadFailed') }}: {{ loadError }}
      </p>

      <HflTablePanel fill>
        <template #toolbar-actions>
          <el-input
            v-model="filters.search"
            class="hfl-list-search"
            clearable
            :placeholder="t('ops.events.searchPlaceholder')"
            @clear="reloadFromFirstPage"
          >
            <template #prefix>
              <Search :size="16" />
            </template>
          </el-input>
          <el-select
            v-model="filters.category"
            clearable
            style="width: 160px"
            :placeholder="t('ops.events.allCategories')"
          >
            <el-option
              v-for="option in categoryOptions"
              :key="option.value"
              :value="option.value"
              :label="option.label"
            />
          </el-select>
          <el-select
            v-model="filters.severity"
            clearable
            style="width: 150px"
            :placeholder="t('ops.events.allSeverities')"
          >
            <el-option
              v-for="option in severityOptions"
              :key="option.value"
              :value="option.value"
              :label="option.label"
            />
          </el-select>
          <el-select
            v-model="filters.period"
            style="width: 140px"
          >
            <el-option
              v-for="option in periodOptions"
              :key="option.value"
              :value="option.value"
              :label="option.label"
            />
          </el-select>
        </template>
        <template #toolbar-utility>
          <el-button
            class="hfl-refresh-button"
            :disabled="loading"
            :title="t('ops.task.btnRefresh')"
            :aria-label="t('ops.task.btnRefresh')"
            @click="loadEvents"
          >
            <RefreshCw
              :size="16"
              :class="{ 'is-spinning': loading }"
            />
          </el-button>
        </template>

        <template #table="{ tableMaxHeight }">
          <el-table
            v-table-column-resize="'ops.events'"
            v-table-overflow-title
            v-loading="loading"
            :data="events"
            row-key="id"
            class="hfl-list-table"
            stripe
            :max-height="tableMaxHeight"
          >
            <el-table-column
              :label="t('ops.events.columns.event')"
              min-width="280"
              fixed="left"
            >
              <template #default="{ row }">
                <button
                  type="button"
                  class="hfl-table-name-link hfl-table-name-link--single"
                  @click="openDetail(row)"
                >
                  {{ row.title }}
                </button>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.events.columns.time')"
              width="180"
            >
              <template #default="{ row }">
                {{ formatLocalDateTime(row.occurred_at, '—') }}
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.events.columns.category')"
              width="150"
            >
              <template #default="{ row }">
                {{ categoryLabel(row.category) }}
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.events.columns.severity')"
              width="120"
            >
              <template #default="{ row }">
                <el-tag
                  :type="severityTagType(row.severity)"
                  size="small"
                >
                  {{ severityLabel(row.severity) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.events.columns.resource')"
              min-width="180"
            >
              <template #default="{ row }">
                <button
                  v-if="row.resource_name && row.target_path"
                  type="button"
                  class="hfl-table-name-link hfl-table-name-link--single"
                  @click="openResource(row)"
                >
                  {{ row.resource_name }}
                </button>
                <span
                  v-else
                  :class="row.resource_name ? '' : 'hfl-empty-mark'"
                >
                  {{ row.resource_name || '—' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.events.columns.details')"
              min-width="280"
            >
              <template #default="{ row }">
                <span :class="row.details ? '' : 'hfl-empty-mark'">
                  {{ row.details || '—' }}
                </span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty
                v-if="!loading"
                :description="t('ops.events.empty')"
                :image-size="72"
              />
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
          />
        </template>
      </HflTablePanel>
    </div>

    <el-drawer
      v-model="detailOpen"
      :title="t('ops.events.detailTitle')"
      :size="detailDrawerSize"
      destroy-on-close
    >
      <el-descriptions
        v-if="selectedEvent"
        :column="1"
        border
      >
        <el-descriptions-item :label="t('ops.events.columns.event')">
          {{ selectedEvent.title }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('ops.events.columns.time')">
          {{ formatLocalDateTime(selectedEvent.occurred_at, '—') }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('ops.events.columns.category')">
          {{ categoryLabel(selectedEvent.category) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('ops.events.columns.severity')">
          {{ severityLabel(selectedEvent.severity) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('ops.events.columns.resource')">
          {{ selectedEvent.resource_name || '—' }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('ops.events.columns.details')">
          {{ selectedEvent.details || '—' }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('ops.events.correlationId')">
          {{ selectedEvent.correlation_id || '—' }}
        </el-descriptions-item>
      </el-descriptions>
      <template
        v-if="selectedEvent?.target_path"
        #footer
      >
        <el-button
          type="primary"
          @click="openResource(selectedEvent)"
        >
          {{ t('ops.events.openResource') }}
        </el-button>
      </template>
    </el-drawer>
  </ModulePage>
</template>

<style scoped>
.events-page__error {
  margin: 0;
  color: var(--el-color-danger);
  font-size: 14px;
}

@media (max-width: 767.98px) {
  .events-page {
    overflow-y: auto;
  }

  .events-page > :deep(.hfl-list-panel--fill) {
    flex: 0 0 auto;
    overflow: visible;
  }
}
</style>
