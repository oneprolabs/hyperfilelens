<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Check, ChevronDown, RefreshCw, Search, X } from 'lucide-vue-next'
import { ElMessage, ElMessageBox } from 'element-plus'
import ModulePage from '../../components/ModulePage.vue'
import OpsStatCard from '../../components/ops/OpsStatCard.vue'
import HflTablePanel from '../../components/HflTablePanel.vue'
import AlertPolicyTypeLabel from '../../components/AlertPolicyTypeLabel.vue'
import { useOpsMenus } from '../../composables/useOpsMenus'
import { useDebouncedAction } from '../../composables/useDebouncedAction'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import { formatLocalDateTime } from '../../lib/dateTime'
import { apiErrorMessage } from '../../lib/api'
import { severityStatusTagAttrs } from '../../lib/statusTag'
import {
  acknowledgeRecord,
  listRecords,
  recordStatistics,
  resolveRecord,
  type AlertRecord,
} from '../../lib/alertApi'

const { t, te } = useI18n()
const opsMenus = useOpsMenus()
const { drawerSize: detailDrawerSize } = useResponsiveDrawerWidth()
const { schedule: scheduleFilterSearch, runNow: runFilterSearch } = useDebouncedAction(() => applyFilters())

const loading = ref(false)
const batchActionLoading = ref(false)
const moreActionsOpen = ref(false)
const alerts = ref<AlertRecord[]>([])
const alertsTableRef = ref<{ clearSelection?: () => void } | null>(null)
const selectedAlerts = ref<AlertRecord[]>([])
const selected = ref<AlertRecord | null>(null)
const showDetail = ref(false)
const skipNextSearchWatch = ref(false)
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })
const filters = reactive({ search: '', severity: '', type: '', status: '' })

const stats = ref({
  critical: 0,
  warning: 0,
  firing: 0,
  acknowledged: 0,
})
const hasActiveFilters = computed(() => (
  Boolean(filters.search.trim())
  || Boolean(filters.severity)
  || Boolean(filters.type)
  || Boolean(filters.status)
))
const emptyDescription = computed(() => (
  hasActiveFilters.value ? t('ops.alerts.emptyFiltered') : t('ops.alerts.emptyNoAlerts')
))
const hasSelectedAlerts = computed(() => selectedAlerts.value.length > 0)
const batchAcknowledgeDisabled = computed(() => (
  batchActionLoading.value || !selectedAlerts.value.some((row) => row.status === 'firing')
))
const batchResolveDisabled = computed(() => (
  batchActionLoading.value || !selectedAlerts.value.some((row) => row.status !== 'resolved')
))

function formatDate(value?: string) {
  return formatLocalDateTime(value, '—')
}

function duration(alert: AlertRecord) {
  const sec = alert.durationSeconds ?? alert.duration_seconds
  if (sec == null) return '—'
  const minutes = Math.floor(sec / 60)
  const seconds = sec % 60
  return `${minutes}m ${seconds}s`
}

function resourceLabel(alert: AlertRecord) {
  return alert.resourceName || alert.resource_name || alert.resourceType || alert.resource_type || '—'
}

function hasResourceLabel(alert: AlertRecord) {
  return Boolean(alert.resourceName || alert.resource_name || alert.resourceType || alert.resource_type)
}

function alertTitleMeta(alert: AlertRecord) {
  return alert.message?.trim() || resourceLabel(alert)
}

function severityLabel(severity?: string | null) {
  if (!severity) return t('common.empty')
  const key = `ops.alertsCenter.common.${severity}`
  const translated = t(key)
  return translated !== key ? translated : severity
}

function typeLabel(type?: string | null) {
  if (!type) return '—'
  const key = `ops.alertsCenter.policyTypes.${type}`
  return te(key) ? t(key) : type
}

function resourceTypeLabel(type?: string | null) {
  if (!type) return t('common.empty')
  const key = `ops.alertsCenter.resourceTypes.${type}`
  return te(key) ? t(key) : type
}

function alertNumericValue(value?: number | null) {
  return value === null || value === undefined ? t('common.empty') : String(value)
}

function statusLabel(status: string) {
  if (status === 'firing') return t('ops.alertsCenter.active.firing')
  if (status === 'acknowledged') return t('ops.alertsCenter.active.acknowledged')
  if (status === 'resolved') return t('ops.alerts.status.resolved')
  return status
}

function statusTagType(status: string): 'success' | 'warning' | 'info' {
  if (status === 'resolved') return 'success'
  if (status === 'firing') return 'warning'
  return 'info'
}

async function fetchAlerts() {
  loading.value = true
  try {
    const [res, statRes] = await Promise.all([
      listRecords({
        page: pagination.page,
        page_size: pagination.pageSize,
        search: filters.search,
        severity: filters.severity,
        type: filters.type,
        status: filters.status,
      }),
      recordStatistics({
        search: filters.search,
        severity: filters.severity,
        type: filters.type,
      }),
    ])
    alerts.value = res.results
    pagination.count = res.count
    stats.value = {
      critical: statRes.critical,
      warning: statRes.warning,
      firing: statRes.firing,
      acknowledged: statRes.acknowledged,
    }
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err), grouping: true })
  } finally {
    loading.value = false
  }
}

function onAlertSelectionChange(rows: AlertRecord[]) {
  selectedAlerts.value = rows
  if (!rows.length) moreActionsOpen.value = false
}

function clearAlertSelection() {
  selectedAlerts.value = []
  alertsTableRef.value?.clearSelection?.()
  moreActionsOpen.value = false
}

type BatchAlertAction = 'acknowledge' | 'resolve'

function batchTargets(kind: BatchAlertAction) {
  return selectedAlerts.value.filter((row) => (
    kind === 'acknowledge' ? row.status === 'firing' : row.status !== 'resolved'
  ))
}

async function executeBatchAction(kind: BatchAlertAction, targets: AlertRecord[], selectedCount: number) {
  batchActionLoading.value = true
  moreActionsOpen.value = false
  const settled = await Promise.allSettled(
    targets.map((row) => (
      kind === 'acknowledge' ? acknowledgeRecord(row.id) : resolveRecord(row.id)
    )),
  )
  const ok = settled.filter((item) => item.status === 'fulfilled').length
  const fail = settled.length - ok
  const skipped = Math.max(0, selectedCount - targets.length)

  batchActionLoading.value = false
  clearAlertSelection()
  await fetchAlerts()

  if (fail > 0) {
    ElMessage.warning({
message: t(
      kind === 'acknowledge' ? 'ops.alerts.msgAckPartial' : 'ops.alerts.msgResolvePartial',
      { ok, skipped, fail },
    ),
grouping: true,
})
    return
  }
  ElMessage.success({
message: t(
    kind === 'acknowledge' ? 'ops.alerts.msgAckedCount' : 'ops.alerts.msgResolvedCount',
    { n: ok },
  ),
grouping: true,
})
  if (skipped > 0) {
    ElMessage.info({
message: t(
      kind === 'acknowledge' ? 'ops.alerts.msgAckSkipped' : 'ops.alerts.msgResolveSkipped',
      { n: skipped },
    ),
grouping: true,
})
  }
}

async function runBatchAcknowledge() {
  const selectedCount = selectedAlerts.value.length
  if (!selectedCount) {
    ElMessage.warning({ message: t('ops.alerts.msgSelectAckFirst'), grouping: true })
    return
  }
  const targets = batchTargets('acknowledge')
  if (!targets.length) {
    ElMessage.info({ message: t('ops.alerts.msgNoFiringInSelection'), grouping: true })
    return
  }
  await executeBatchAction('acknowledge', targets, selectedCount)
}

async function runBatchResolve() {
  const selectedCount = selectedAlerts.value.length
  if (!selectedCount) {
    ElMessage.warning({ message: t('ops.alerts.msgSelectResolveFirst'), grouping: true })
    return
  }
  const targets = batchTargets('resolve')
  if (!targets.length) {
    ElMessage.info({ message: t('ops.alerts.msgAllResolved'), grouping: true })
    return
  }
  const skipped = Math.max(0, selectedCount - targets.length)
  try {
    await ElMessageBox.confirm(
      t('ops.alerts.confirmBatchResolveMessage', { n: targets.length, skipped }),
      t('ops.alerts.confirmBatchResolveTitle'),
      {
        confirmButtonText: t('ops.alerts.resolve'),
        cancelButtonText: t('common.cancel'),
        type: 'warning',
      },
    )
  } catch {
    return
  }
  await executeBatchAction('resolve', targets, selectedCount)
}

function openDetail(alert: AlertRecord) {
  selected.value = alert
  showDetail.value = true
}

function closeDetail() {
  selected.value = null
}

function applyFilters() {
  pagination.page = 1
  fetchAlerts()
}

function runSearchImmediately() {
  skipNextSearchWatch.value = true
  runFilterSearch()
}

function clearSearch() {
  runSearchImmediately()
}

onMounted(fetchAlerts)

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
  () => [filters.severity, filters.type, filters.status],
  () => runFilterSearch(),
)
</script>

<template>
  <ModulePage :title="t('ops.alertsCenter.active.title')" :menus="opsMenus" body-fill>
    <div class="hfl-ops-page hfl-ops-page--fill alert-incidents-page">
      <div class="hfl-ops-stats-grid hfl-ops-stats-grid--4">
        <OpsStatCard
          :label="t('ops.alertsCenter.common.critical')"
          :value="stats.critical"
          accent="red"
          accent-side="left"
          value-class="text-red-600"
        />
        <OpsStatCard
          :label="t('ops.alertsCenter.common.warning')"
          :value="stats.warning"
          accent="yellow"
          accent-side="left"
          value-class="text-amber-600"
        />
        <OpsStatCard
          :label="t('ops.alertsCenter.active.firing')"
          :value="stats.firing"
          accent="orange"
          accent-side="left"
          value-class="text-orange-600"
          :pulse="stats.firing > 0"
        />
        <OpsStatCard
          :label="t('ops.alertsCenter.active.acknowledged')"
          :value="stats.acknowledged"
          accent="blue"
          accent-side="left"
          value-class="text-blue-600"
        />
      </div>

      <HflTablePanel fill>
        <template #toolbar>
          <el-dropdown
            trigger="click"
            @visible-change="moreActionsOpen = $event"
          >
            <el-button :loading="batchActionLoading">
              {{ t('ops.alertsCenter.common.moreActions') }}
              <ChevronDown
                :size="16"
                class="hfl-list-more__chev"
                :class="{ 'hfl-list-more__chev--open': moreActionsOpen }"
              />
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item
                  :disabled="batchAcknowledgeDisabled"
                  @click="runBatchAcknowledge"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Check :size="14" class="shrink-0" />
                    <span>{{ t('ops.alertsCenter.common.acknowledge') }}</span>
                  </span>
                </el-dropdown-item>
                <el-dropdown-item
                  :disabled="batchResolveDisabled"
                  @click="runBatchResolve"
                >
                  <span class="el-dropdown-menu__item-content">
                    <X :size="14" class="shrink-0" />
                    <span>{{ t('ops.alerts.resolve') }}</span>
                  </span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
        <template #toolbar-actions>
          <el-input
            v-model="filters.search"
            class="hfl-list-search"
            clearable
            :placeholder="t('ops.alertsCenter.active.searchPlaceholder')"
            @clear="clearSearch"
          >
            <template #prefix><Search class="h-4 w-4 opacity-60" /></template>
          </el-input>
          <el-select
            v-model="filters.type"
            clearable
            style="width: 150px"
            :placeholder="t('ops.alertsCenter.common.allTypes')"
          >
            <el-option value="metric" :label="t('ops.alertsCenter.policyTypes.metric')" />
            <el-option value="availability" :label="t('ops.alertsCenter.policyTypes.availability')" />
            <el-option value="task" :label="t('ops.alertsCenter.policyTypes.task')" />
            <el-option value="event" :label="t('ops.alertsCenter.policyTypes.event')" />
            <el-option value="system" :label="t('ops.alertsCenter.policyTypes.system')" />
          </el-select>
          <el-select
            v-model="filters.status"
            clearable
            style="width: 130px"
            :placeholder="t('ops.alertsCenter.common.allStatus')"
          >
            <el-option value="firing" :label="t('ops.alertsCenter.active.firing')" />
            <el-option value="acknowledged" :label="t('ops.alertsCenter.active.acknowledged')" />
            <el-option value="resolved" :label="t('ops.alerts.status.resolved')" />
          </el-select>
          <el-select
            v-model="filters.severity"
            clearable
            style="width: 130px"
            :placeholder="t('ops.alertsCenter.common.allSeverity')"
          >
            <el-option value="critical" :label="t('ops.alertsCenter.common.critical')" />
            <el-option value="warning" :label="t('ops.alertsCenter.common.warning')" />
            <el-option value="info" :label="t('ops.alertsCenter.common.info')" />
          </el-select>
        </template>
        <template #toolbar-utility>
          <el-button class="hfl-refresh-button" :title="t('ops.task.btnRefresh')" :aria-label="t('ops.task.btnRefresh')" :disabled="loading" @click="fetchAlerts">
            <RefreshCw :size="16" :class="{ 'is-spinning': loading }" />
          </el-button>
        </template>

        <template #table="{ tableMaxHeight }">
        <el-table
          ref="alertsTableRef"
          v-table-column-resize="'ops.alertIncidents'"
          v-loading="loading"
          :data="alerts"
          stripe
          class="hfl-list-table"
          row-key="id"
          :max-height="tableMaxHeight"
          @selection-change="onAlertSelectionChange"
        >
          <el-table-column
            type="selection"
            width="35"
            fixed="left"
          />
          <el-table-column :label="t('ops.alertsCenter.common.title')" min-width="260" fixed="left">
            <template #default="{ row }">
              <div class="hfl-ops-primary-cell">
                <div class="min-w-0">
                  <div class="flex min-w-0 items-center gap-1">
                    <button
                      type="button"
                      class="hfl-table-name-link hfl-table-name-link--single min-w-0"
                      @click="openDetail(row)"
                    >
                      {{ row.title }}
                    </button>
                  </div>
                  <div class="hfl-ops-cell-stack__meta line-clamp-1">
                    {{ alertTitleMeta(row) }}
                  </div>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column :label="t('ops.alertsCenter.common.severity')" width="100">
            <template #default="{ row }">
              <el-tag v-bind="severityStatusTagAttrs(row.severity)" size="small">
                {{ severityLabel(row.severity) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="t('ops.alertsCenter.common.type')" width="130">
            <template #default="{ row }">
              <AlertPolicyTypeLabel :type="row.type" />
            </template>
          </el-table-column>
          <el-table-column :label="t('ops.alertsCenter.common.resource')" min-width="160">
            <template #default="{ row }">
              <span :class="hasResourceLabel(row) ? 'hfl-table-cell-mono' : 'hfl-empty-mark'">{{ resourceLabel(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('ops.alertsCenter.common.status')" width="120">
            <template #default="{ row }">
              <el-tag :type="statusTagType(row.status)" size="small">
                {{ statusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="t('ops.alertsCenter.active.firstTriggered')" width="170">
            <template #default="{ row }">
              <span class="hfl-table-cell-time" :class="{ 'hfl-empty-mark': !(row.firstTriggeredAt || row.first_triggered_at) }">
                {{ formatDate(row.firstTriggeredAt || row.first_triggered_at) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column :label="t('ops.alertsCenter.common.duration')" width="100">
            <template #default="{ row }"><span :class="{ 'hfl-empty-mark': (row.durationSeconds ?? row.duration_seconds) == null }">{{ duration(row) }}</span></template>
          </el-table-column>
          <template #empty>
            <el-empty :description="emptyDescription" />
          </template>
        </el-table>
        </template>

        <template #footer>
          <span
            v-if="hasSelectedAlerts"
            class="hfl-list-footer__selected"
          >
            {{ t('ops.alertsCenter.common.selectedCount', { n: selectedAlerts.length }) }}
          </span>
          <HflPagination
            v-model:current-page="pagination.page"
            v-model:page-size="pagination.pageSize"
            class="hfl-list-footer__pagination"
            layout="total, sizes, prev, pager, next"
            :total="pagination.count"
            :page-sizes="[20, 30, 50, 100]"
            @current-change="fetchAlerts"
            @size-change="() => { pagination.page = 1; fetchAlerts() }"
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
        <span class="hfl-detail-drawer__title">{{ selected?.title || t('ops.nav.alerts') }}</span>
      </template>

      <div v-if="selected" class="hfl-detail-drawer__body">
        <div class="hfl-detail-sections">
          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">{{ t('ops.alertsCenter.common.basicInfo') }}</h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.status') }}</span>
                <span class="hfl-detail-row__value">
                  <el-tag :type="statusTagType(selected.status)" size="small">
                    {{ statusLabel(selected.status) }}
                  </el-tag>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.severity') }}</span>
                <span class="hfl-detail-row__value">
                  <el-tag v-bind="severityStatusTagAttrs(selected.severity)" size="small">
                    {{ severityLabel(selected.severity) }}
                  </el-tag>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.type') }}</span>
                <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__empty': !selected.type }">{{ typeLabel(selected.type) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.id') }}</span>
                <span class="hfl-detail-row__value hfl-table-cell-mono">{{ selected.id }}</span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">Message</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--break">
                  <span :class="selected.message ? 'hfl-detail-row__text' : 'hfl-empty-mark'">
                    {{ selected.message || t('common.empty') }}
                  </span>
                </span>
              </div>
            </div>
          </section>

          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">{{ t('ops.alertsCenter.common.resource') }}</h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.policies.targetColumn') }}</span>
                <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__empty': !(selected.resourceType || selected.resource_type) }">{{ resourceTypeLabel(selected.resourceType || selected.resource_type) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.resource') }}</span>
                <span class="hfl-detail-row__value" :class="hasResourceLabel(selected) ? 'hfl-table-cell-mono' : 'hfl-empty-mark'">
                  {{ resourceLabel(selected) }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.task.resourceId') }}</span>
                <span class="hfl-detail-row__value" :class="selected.resourceId || selected.resource_id ? 'hfl-table-cell-mono' : 'hfl-empty-mark'">
                  {{ selected.resourceId || selected.resource_id || t('common.empty') }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.policies.duration') }}</span>
                <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__empty': (selected.durationSeconds ?? selected.duration_seconds) == null }">{{ duration(selected) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.active.firstTriggered') }}</span>
                <span class="hfl-detail-row__value hfl-table-cell-time" :class="{ 'hfl-detail-row__empty': !(selected.firstTriggeredAt || selected.first_triggered_at) }">
                  {{ formatDate(selected.firstTriggeredAt || selected.first_triggered_at) }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.updatedAt') }}</span>
                <span class="hfl-detail-row__value hfl-table-cell-time" :class="{ 'hfl-detail-row__empty': !(selected.lastTriggeredAt || selected.last_triggered_at || selected.createdAt || selected.created_at) }">
                  {{ formatDate(selected.lastTriggeredAt || selected.last_triggered_at || selected.createdAt || selected.created_at) }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">Current</span>
                <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__empty': (selected.currentValue ?? selected.current_value) == null }">
                  {{ alertNumericValue(selected.currentValue ?? selected.current_value) }}
                  <template v-if="selected.unit"> {{ selected.unit }}</template>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">Threshold</span>
                <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__empty': (selected.thresholdValue ?? selected.threshold_value) == null }">
                  {{ alertNumericValue(selected.thresholdValue ?? selected.threshold_value) }}
                  <template v-if="selected.unit"> {{ selected.unit }}</template>
                </span>
              </div>
            </div>
          </section>
        </div>
      </div>
    </ElDrawer>
  </ModulePage>
</template>
