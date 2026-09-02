<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { CheckCircle2, RefreshCw, Search, XCircle } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import ModulePage from '../../components/ModulePage.vue'
import OpsStatCard from '../../components/ops/OpsStatCard.vue'
import HflTablePanel from '../../components/HflTablePanel.vue'
import AlertPolicyTypeLabel from '../../components/AlertPolicyTypeLabel.vue'
import { useOpsMenus } from '../../composables/useOpsMenus'
import { useDebouncedAction } from '../../composables/useDebouncedAction'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import { useAlertPolicyLabels } from '../../composables/useAlertPolicyLabels'
import { formatLocalDateTime } from '../../lib/dateTime'
import { alertStatusTagAttrs, lifecycleStatusTagAttrs, severityStatusTagAttrs } from '../../lib/statusTag'
import { listChannels, listLogs, logStats, type NotificationLog } from '../../lib/notificationApi'

const { t } = useI18n()
const opsMenus = useOpsMenus()
const { drawerSize: detailDrawerSize } = useResponsiveDrawerWidth()
const { policyTypeLabel, resourceTypeLabel, policyTypeOptions } = useAlertPolicyLabels()
const { schedule: scheduleFilterSearch, runNow: runFilterSearch } = useDebouncedAction(() => applyFilters())

const logs = ref<NotificationLog[]>([])
const channels = ref<Array<{ id: number; name: string }>>([])
const stats = ref({ total: 0, success: 0, failed: 0, success_rate: 0 })
const loading = ref(true)
const selected = ref<NotificationLog | null>(null)
const showDetail = ref(false)
const skipNextSearchWatch = ref(false)
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })
const filters = reactive({
  search: '',
  channel_id: '',
  notification_type: '',
  status: '',
  type: '',
  severity: '',
})
const hasActiveFilters = computed(() => (
  Boolean(filters.search.trim())
  || Boolean(filters.channel_id)
  || Boolean(filters.notification_type)
  || Boolean(filters.status)
  || Boolean(filters.type)
  || Boolean(filters.severity)
))
const emptyDescription = computed(() => (
  hasActiveFilters.value ? t('ops.notification.emptyFilteredDeliveries') : t('ops.notification.emptyDeliveries')
))

function formatDate(value?: string) {
  return formatLocalDateTime(value, '—')
}

function responsePreview(row: NotificationLog) {
  return row.errorMessage || row.error_message || '—'
}

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === '') return t('common.empty')
  return String(value)
}

function notificationTypeLabel(type?: string | null) {
  if (type === 'firing') return t('ops.notification.logTypeFiring')
  if (type === 'resolved') return t('ops.notification.logTypeResolved')
  return type || t('common.empty')
}

function notificationTypeTagAttrs(type?: string | null) {
  return alertStatusTagAttrs(type)
}

function severityLabel(severity?: string | null) {
  if (!severity) return t('common.empty')
  const key = `ops.alertsCenter.common.${severity}`
  const translated = t(key)
  return translated !== key ? translated : severity
}

function logAlert(row: NotificationLog) {
  return (row.alert || {}) as Record<string, unknown>
}

function logPolicy(row: NotificationLog) {
  return (row.policy || {}) as Record<string, unknown>
}

function logPolicyName(row: NotificationLog) {
  return String(logPolicy(row).name || t('common.empty'))
}

function logResourceSummary(row: NotificationLog) {
  const alert = logAlert(row)
  const name = String(alert.resource_name || '')
  const type = resourceTypeLabel(String(alert.resource_type || ''))
  if (name && type !== t('common.empty')) return `${name} · ${type}`
  return name || type
}

function logSeverity(row: NotificationLog) {
  const alert = logAlert(row)
  const policy = logPolicy(row)
  return String(alert.severity || policy.severity || '')
}

function logPolicyType(row: NotificationLog) {
  const policy = logPolicy(row)
  return policyTypeLabel(String(policy.type || ''))
}

function logPolicyTypeValue(row: NotificationLog) {
  return String(logPolicy(row).type || logAlert(row).type || '')
}

function logAlertTitle(row: NotificationLog) {
  return String(logAlert(row).title || '—')
}

function closeDetail() {
  selected.value = null
}

async function loadChannels() {
  try {
    const res = await listChannels({ page_size: 200 })
    channels.value = res.results.map((c) => ({ id: c.id, name: c.name }))
  } catch {
    channels.value = []
  }
}

async function fetchData() {
  loading.value = true
  try {
    const params: Record<string, string | number> = {
      page: pagination.page,
      page_size: pagination.pageSize,
      search: filters.search,
      channel_id: filters.channel_id,
      notification_type: filters.notification_type,
      status: filters.status,
      type: filters.type,
      severity: filters.severity,
    }
    const [logRes, statRes] = await Promise.all([listLogs(params), logStats(params)])
    logs.value = logRes.results
    pagination.count = logRes.count
    stats.value = statRes
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  pagination.page = 1
  fetchData()
}

function clearSearch() {
  skipNextSearchWatch.value = true
  runFilterSearch()
}

function openDetail(row: NotificationLog) {
  selected.value = row
  showDetail.value = true
}

onMounted(async () => {
  await loadChannels()
  await fetchData()
})

watch(
  () => filters.search,
  () => {
    if (skipNextSearchWatch.value) {
      skipNextSearchWatch.value = false
      return
    }
    scheduleFilterSearch()
  },
)

watch(
  () => [filters.channel_id, filters.notification_type, filters.status, filters.type, filters.severity],
  () => runFilterSearch(),
)
</script>

<template>
  <ModulePage
    :title="t('ops.nav.deliveryHistory')"
    :menus="opsMenus"
    body-fill
  >
    <div class="hfl-ops-page hfl-ops-page--fill">
      <div class="hfl-ops-stats-grid hfl-ops-stats-grid--4">
        <OpsStatCard
          :label="t('ops.notification.logsTotal')"
          :value="stats.total"
          tone="primary"
          accent-side="left"
        />
        <OpsStatCard
          :label="t('ops.notification.statusSuccess')"
          :value="stats.success"
          tone="success"
          accent-side="left"
        />
        <OpsStatCard
          :label="t('ops.notification.statusFailed')"
          :value="stats.failed"
          tone="danger"
          accent-side="left"
        />
        <OpsStatCard
          :label="t('ops.notification.successRate')"
          :value="`${stats.success_rate}%`"
          tone="success"
          accent-side="left"
        />
      </div>

      <HflTablePanel fill>
        <template #toolbar-actions>
          <el-input
            v-model="filters.search"
            class="hfl-list-search"
            clearable
            :placeholder="t('ops.notification.logsSearch')"
            @clear="clearSearch"
          >
            <template #prefix>
              <Search class="h-4 w-4 opacity-60" />
            </template>
          </el-input>
          <el-select
            v-model="filters.channel_id"
            clearable
            style="width: 150px"
            :placeholder="t('ops.notification.filterChannel')"
          >
            <el-option
              v-for="ch in channels"
              :key="ch.id"
              :value="String(ch.id)"
              :label="ch.name"
            />
          </el-select>
          <el-select
            v-model="filters.status"
            clearable
            style="width: 120px"
            :placeholder="t('ops.notification.filterAllStatus')"
          >
            <el-option
              value="success"
              :label="t('ops.notification.statusSuccess')"
            />
            <el-option
              value="failed"
              :label="t('ops.notification.statusFailed')"
            />
          </el-select>
          <el-select
            v-model="filters.notification_type"
            clearable
            style="width: 130px"
            :placeholder="t('ops.notification.filterNotificationType')"
          >
            <el-option
              value="firing"
              :label="t('ops.notification.logTypeFiring')"
            />
            <el-option
              value="resolved"
              :label="t('ops.notification.logTypeResolved')"
            />
          </el-select>
          <el-select
            v-model="filters.type"
            clearable
            style="width: 130px"
            :placeholder="t('ops.alertsCenter.common.allTypes')"
          >
            <el-option
              v-for="opt in policyTypeOptions"
              :key="opt.value"
              :value="opt.value"
              :label="opt.label"
            />
          </el-select>
          <el-select
            v-model="filters.severity"
            clearable
            style="width: 130px"
            :placeholder="t('ops.alertsCenter.common.allSeverity')"
          >
            <el-option
              value="critical"
              :label="t('ops.alertsCenter.common.critical')"
            />
            <el-option
              value="warning"
              :label="t('ops.alertsCenter.common.warning')"
            />
            <el-option
              value="info"
              :label="t('ops.alertsCenter.common.info')"
            />
          </el-select>
        </template>
        <template #toolbar-utility>
          <el-button
            class="hfl-refresh-button"
            :title="t('ops.task.btnRefresh')"
            :aria-label="t('ops.task.btnRefresh')"
            :disabled="loading"
            @click="fetchData"
          >
            <RefreshCw
              :size="16"
              :class="{ 'is-spinning': loading }"
            />
          </el-button>
        </template>

        <template #table="{ tableMaxHeight }">
          <el-table
            v-table-column-resize="'ops.notificationRecords'"
            v-loading="loading"
            :data="logs"
            stripe
            flexible
            class="hfl-list-table"
            :max-height="tableMaxHeight"
          >
            <el-table-column
              :label="t('ops.notification.logsAlert')"
              min-width="260"
              fixed="left"
            >
              <template #default="{ row }">
                <div class="hfl-ops-primary-cell">
                  <div class="min-w-0">
                    <div class="flex min-w-0 items-center gap-1">
                      <button
                        type="button"
                        class="hfl-table-name-link hfl-table-name-link--single min-w-0"
                        @click="openDetail(row)"
                      >
                        {{ logAlertTitle(row) }}
                      </button>
                    </div>
                    <div class="hfl-ops-cell-stack__meta line-clamp-1">
                      {{ logResourceSummary(row) }}
                    </div>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.notification.logsPolicy')"
              min-width="150"
            >
              <template #default="{ row }">
                <div
                  class="hfl-ops-cell-stack__title"
                  :class="{ 'hfl-empty-mark': logPolicyName(row) === t('common.empty') }"
                >
                  {{ logPolicyName(row) }}
                </div>
                <div class="hfl-ops-cell-stack__meta">
                  <AlertPolicyTypeLabel :type="logPolicyTypeValue(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.notification.logsChannel')"
              width="160"
            >
              <template #default="{ row }">
                <div
                  class="hfl-ops-cell-stack__title"
                  :class="{ 'hfl-empty-mark': !row.channel?.name }"
                >
                  {{ row.channel?.name || '—' }}
                </div>
                <div
                  class="hfl-ops-cell-stack__meta uppercase"
                  :class="{ 'hfl-empty-mark': !row.channel?.type }"
                >
                  {{ row.channel?.type || '—' }}
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.notification.logsNotificationType')"
              width="110"
            >
              <template #default="{ row }">
                <el-tag
                  v-bind="notificationTypeTagAttrs(row.notificationType || row.notification_type)"
                  size="small"
                >
                  {{ notificationTypeLabel(row.notificationType || row.notification_type) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.alertsCenter.common.severity')"
              width="100"
            >
              <template #default="{ row }">
                <el-tag
                  v-bind="severityStatusTagAttrs(logSeverity(row))"
                  size="small"
                >
                  {{ severityLabel(logSeverity(row)) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.notification.colStatus')"
              width="110"
            >
              <template #default="{ row }">
                <el-tag
                  class="hfl-ops-result-tag"
                  v-bind="lifecycleStatusTagAttrs(row.status)"
                  size="small"
                >
                  <CheckCircle2
                    v-if="row.status === 'success'"
                    class="hfl-ops-result-tag__icon"
                  />
                  <XCircle
                    v-else-if="row.status === 'failed'"
                    class="hfl-ops-result-tag__icon"
                  />
                  {{ displayValue(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.notification.logsSentAt')"
              width="170"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-time"
                  :class="{ 'hfl-empty-mark': !(row.sentAt || row.sent_at) }"
                >{{ formatDate(row.sentAt || row.sent_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.notification.logsResult')"
              min-width="220"
              class-name="hfl-notification-result-column"
            >
              <template #default="{ row }">
                <el-tooltip
                  :content="responsePreview(row)"
                  placement="top-start"
                  popper-class="hfl-notification-result-tooltip"
                  :show-after="250"
                >
                  <button
                    type="button"
                    class="hfl-ops-response-chip"
                    @click.stop="openDetail(row)"
                  >
                    {{ responsePreview(row) }}
                  </button>
                </el-tooltip>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty :description="emptyDescription" />
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
            @current-change="fetchData"
            @size-change="() => { pagination.page = 1; fetchData() }"
          />
        </template>
      </HflTablePanel>
    </div>

    <ElDrawer
      v-model="showDetail"
      destroy-on-close
      :size="detailDrawerSize"
      class="hfl-detail-drawer"
      @closed="closeDetail"
    >
      <template #header>
        <span class="hfl-detail-drawer__title">
          {{ selected ? logAlertTitle(selected) : t('ops.notification.logDetail') }}
        </span>
      </template>

      <div
        v-if="selected"
        class="hfl-detail-drawer__body"
      >
        <div class="hfl-detail-sections">
          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('ops.notification.logDetail') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.notification.colId') }}</span>
                <span class="hfl-detail-row__value hfl-table-cell-mono">{{ selected.id }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.notification.logsNotificationType') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !(selected.notificationType || selected.notification_type) }"
                >{{ notificationTypeLabel(selected.notificationType || selected.notification_type) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.notification.colStatus') }}</span>
                <span class="hfl-detail-row__value">
                  <el-tag
                    class="hfl-ops-result-tag"
                    v-bind="lifecycleStatusTagAttrs(selected.status)"
                    size="small"
                  >
                    <CheckCircle2
                      v-if="selected.status === 'success'"
                      class="hfl-ops-result-tag__icon"
                    />
                    <XCircle
                      v-else-if="selected.status === 'failed'"
                      class="hfl-ops-result-tag__icon"
                    />
                    {{ displayValue(selected.status) }}
                  </el-tag>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.notification.logsSentAt') }}</span>
                <span
                  class="hfl-detail-row__value hfl-table-cell-time"
                  :class="{ 'hfl-detail-row__empty': !(selected.sentAt || selected.sent_at) }"
                >
                  {{ formatDate(selected.sentAt || selected.sent_at) }}
                </span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('ops.notification.logsResult') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--break">
                  <span :class="selected.errorMessage || selected.error_message ? 'hfl-detail-row__text' : 'hfl-empty-mark'">
                    {{ selected.errorMessage || selected.error_message || t('common.empty') }}
                  </span>
                </span>
              </div>
            </div>
          </section>

          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('ops.alertsCenter.common.resourcesAndChannels') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.notification.logsChannel') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !selected.channel?.name }"
                >{{ displayValue(selected.channel?.name) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.notification.colType') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !selected.channel?.type }"
                >{{ displayValue(selected.channel?.type) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.notification.logsPolicy') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': logPolicyName(selected) === t('common.empty') }"
                >{{ logPolicyName(selected) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.type') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': logPolicyType(selected) === t('common.empty') }"
                >{{ logPolicyType(selected) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.severity') }}</span>
                <span class="hfl-detail-row__value">
                  <el-tag
                    v-bind="severityStatusTagAttrs(logSeverity(selected))"
                    size="small"
                  >
                    {{ severityLabel(logSeverity(selected)) }}
                  </el-tag>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.policies.targetColumn') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': logResourceSummary(selected) === t('common.empty') }"
                >{{ logResourceSummary(selected) }}</span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('ops.notification.logsAlert') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--break">
                  <span :class="logAlertTitle(selected) !== '—' ? 'hfl-detail-row__text' : 'hfl-empty-mark'">
                    {{ logAlertTitle(selected) }}
                  </span>
                </span>
              </div>
            </div>
          </section>
        </div>
      </div>
    </ElDrawer>
  </ModulePage>
</template>
