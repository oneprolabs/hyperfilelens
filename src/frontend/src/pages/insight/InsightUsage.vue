<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import {
  ChartNoAxesCombined,
  CircleDollarSign,
  Coins,
  Cpu,
  DatabaseBackup,
  MessageSquareText,
  RefreshCw,
  Search,
} from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import ModulePage from '../../components/ModulePage.vue'
import HflPagination from '../../components/HflPagination.vue'
import { useInsightSideNav } from '../../composables/useInsightSideNav'
import { apiErrorMessage } from '../../lib/api'
import {
  fetchCopilotUsage,
  type LensCopilotUsageOverview,
} from '../../lib/lensApi'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent])

const { t, locale } = useI18n()
const insightMenus = useInsightSideNav()
const loading = ref(false)
const usage = ref<LensCopilotUsageOverview | null>(null)
const chartMetric = ref<'cost' | 'tokens'>('cost')
const periodPreset = ref<'today' | '7d' | '30d' | 'month' | 'custom'>('today')
const customRange = ref<[string, string] | null>(null)
const startDate = ref('')
const endDate = ref('')
const searchQuery = ref('')
const backupSourceFilter = ref('')
const statusFilter = ref('')
const page = ref(1)
const pageSize = ref(20)
let searchTimer: ReturnType<typeof setTimeout> | null = null
let loadRequestId = 0
const intlLocale = computed(() => locale.value === 'en' ? 'en-US' : locale.value)

const questionHistoryEmptyDescription = computed(() => (
  searchQuery.value.trim() || backupSourceFilter.value || statusFilter.value
    ? t('insight.usage.noFilteredQuestions')
    : t('insight.usage.noQuestionHistory')
))

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
  if (!value?.[0] || !value?.[1]) return
  periodPreset.value = 'custom'
  startDate.value = value[0]
  endDate.value = value[1]
  page.value = 1
  void loadUsage()
}

function filterByBackupSource(
  row: LensCopilotUsageOverview['by_backup_source'][number],
) {
  backupSourceFilter.value = row.backup_source_name
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
      backup_source: backupSourceFilter.value,
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

function formatNumber(value: number | null | undefined) {
  return new Intl.NumberFormat(intlLocale.value).format(Number(value || 0))
}

function formatCompact(value: number | null | undefined) {
  return new Intl.NumberFormat(intlLocale.value, {
    notation: 'compact',
    maximumFractionDigits: 2,
  }).format(Number(value || 0))
}

function formatCost(value: number | null | undefined, currency = 'USD') {
  if (value == null) return t('insight.usage.unavailable')
  return new Intl.NumberFormat(intlLocale.value, {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: value < 0.01 ? 6 : 4,
  }).format(value)
}

function formatShortDateTime(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(intlLocale.value, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

function statusLabel(status: string) {
  if (status === 'done') return t('insight.usage.statusCompleted')
  if (status === 'failed') return t('insight.usage.statusFailed')
  if (status === 'cancelled') return t('insight.usage.statusCancelled')
  if (status === 'queued') return t('insight.usage.statusQueued')
  if (status === 'running' || status === 'streaming') return t('insight.usage.statusInProgress')
  return status || t('insight.usage.statusUnknown')
}

const summary = computed(() => usage.value?.summary ?? {
  estimated_cost: null,
  cost_currency: 'USD',
  total_tokens: 0,
  prompt_tokens: 0,
  completion_tokens: 0,
  cached_tokens: 0,
  reasoning_tokens: 0,
  model_calls: 0,
  q_and_a_requests: 0,
  average_cost_per_q_and_a: null,
})

const usageFreshness = computed(() => {
  const freshness = usage.value?.data_freshness
  if (!freshness) return ''
  if (freshness.pending_runs > 0) {
    return t('insight.usage.freshnessPending', { n: formatNumber(freshness.pending_runs) })
  }
  if (freshness.last_source_sync_at) {
    return t('insight.usage.freshnessUpdatedAt', {
      time: formatShortDateTime(freshness.last_source_sync_at),
    })
  }
  if (summary.value.q_and_a_requests > 0) {
    return t('insight.usage.freshnessRecorded')
  }
  return t('insight.usage.freshnessEmpty')
})

const tokenBreakdown = computed(() => {
  const primaryTotal = summary.value.prompt_tokens + summary.value.completion_tokens
  return [
    {
      label: t('insight.usage.inputTokens'),
      value: summary.value.prompt_tokens,
      color: '#6d5dfc',
      percent: primaryTotal > 0 ? (summary.value.prompt_tokens / primaryTotal) * 100 : 0,
    },
    {
      label: t('insight.usage.outputTokens'),
      value: summary.value.completion_tokens,
      color: '#16a085',
      percent: primaryTotal > 0 ? (summary.value.completion_tokens / primaryTotal) * 100 : 0,
    },
    {
      label: t('insight.usage.cachedInputTokens'),
      value: summary.value.cached_tokens,
      color: '#2f7cf6',
      percent: null,
    },
    {
      label: t('insight.usage.reasoningTokens'),
      value: summary.value.reasoning_tokens,
      color: '#e39a24',
      percent: null,
    },
  ]
})

const chartOption = computed(() => {
  const items = usage.value?.trend ?? []
  const costMode = chartMetric.value === 'cost'
  const hourly = startDate.value === endDate.value
  return {
    animationDuration: 240,
    grid: { left: 16, right: 18, top: 20, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      renderMode: 'richText',
      borderWidth: 1,
      formatter: (params: Array<{ axisValueLabel?: string; value?: number | null }>) => {
        const point = params[0]
        const rawValue = point?.value
        const value = rawValue == null ? null : Number(rawValue)
        const formattedValue = costMode
          ? formatCost(value)
          : t('insight.usage.tokenCount', { n: formatNumber(value) })
        return `${point?.axisValueLabel || ''}\n${formattedValue}`
      },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: items.map((row) => {
        if (!row.bucket) return ''
        const date = new Date(row.bucket)
        return Number.isNaN(date.getTime())
          ? row.bucket
          : new Intl.DateTimeFormat(intlLocale.value, hourly
            ? { hour: '2-digit', minute: '2-digit', hour12: false }
            : { month: 'short', day: 'numeric' }).format(date)
      }),
      axisLine: { lineStyle: { color: '#d9dce5' } },
      axisTick: { show: false },
      axisLabel: { color: '#85899a', fontSize: 12 },
    },
    yAxis: {
      type: 'value',
      minInterval: costMode ? undefined : 1,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: '#eceef4', type: 'dashed' } },
      axisLabel: {
        color: '#85899a',
        fontSize: 12,
        formatter: (value: number) => costMode ? `$${value}` : formatCompact(value),
      },
    },
    series: [{
      type: 'line',
      smooth: 0.3,
      symbol: 'circle',
      symbolSize: 6,
      showSymbol: items.length < 16,
      lineStyle: { width: 2.5, color: '#6d5dfc' },
      itemStyle: { color: '#6d5dfc' },
      areaStyle: { color: 'rgba(109, 93, 252, 0.09)' },
      data: items.map((row) => costMode ? row.total_cost : Number(row.total_tokens || 0)),
    }],
  }
})

watch([backupSourceFilter, statusFilter], () => {
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
  <ModulePage :menus="insightMenus">
    <div
      v-loading="loading && !usage"
      class="usage-page"
    >
      <section
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
          <span
            v-if="usageFreshness"
            class="usage-freshness"
          >{{ usageFreshness }}</span>
          <button
            class="usage-icon-button"
            type="button"
            :aria-label="t('insight.usage.refresh')"
            :disabled="loading"
            @click="loadUsage"
          >
            <RefreshCw
              :size="17"
              :class="{ 'is-spinning': loading }"
            />
          </button>
        </div>
      </section>

      <section class="usage-summary-grid">
        <article class="usage-summary-card">
          <span class="usage-summary-card__icon is-cost"><CircleDollarSign :size="19" /></span>
          <div><span>{{ t('insight.usage.totalCost') }}</span><strong>{{ formatCost(summary.estimated_cost, summary.cost_currency) }}</strong></div>
        </article>
        <article class="usage-summary-card">
          <span class="usage-summary-card__icon is-calls"><Cpu :size="19" /></span>
          <div><span>{{ t('insight.usage.aiCalls') }}</span><strong>{{ formatNumber(summary.model_calls) }}</strong></div>
        </article>
        <article class="usage-summary-card">
          <span class="usage-summary-card__icon is-token"><Coins :size="19" /></span>
          <div><span>{{ t('insight.usage.totalTokens') }}</span><strong>{{ formatCompact(summary.total_tokens) }}</strong></div>
        </article>
        <article class="usage-summary-card">
          <span class="usage-summary-card__icon is-qa"><MessageSquareText :size="19" /></span>
          <div><span>{{ t('insight.usage.questions') }}</span><strong>{{ formatNumber(summary.q_and_a_requests) }}</strong></div>
        </article>
      </section>

      <section class="usage-panel usage-trend-panel">
        <div class="usage-panel__head">
          <div><h2>{{ t('insight.usage.usageTrend') }}</h2></div>
          <div class="usage-segments usage-segments--small">
            <button
              :class="{ active: chartMetric === 'cost' }"
              type="button"
              @click="chartMetric = 'cost'"
            >
              {{ t('insight.usage.cost') }}
            </button>
            <button
              :class="{ active: chartMetric === 'tokens' }"
              type="button"
              @click="chartMetric = 'tokens'"
            >
              {{ t('insight.usage.tokens') }}
            </button>
          </div>
        </div>
        <VChart
          v-if="usage?.trend.length"
          class="usage-chart"
          :option="chartOption"
          autoresize
        />
        <div
          v-else
          class="usage-chart-empty"
        >
          <ChartNoAxesCombined :size="25" /><span>{{ t('insight.usage.noUsageForPeriod') }}</span>
        </div>
      </section>

      <div class="usage-breakdown-grid">
        <section class="usage-panel">
          <div class="usage-panel__head">
            <div><h2>{{ t('insight.usage.tokenUsage') }}</h2></div>
          </div>
          <div class="token-breakdown">
            <div
              v-for="row in tokenBreakdown"
              :key="row.label"
              class="token-breakdown__row"
              :class="{ 'is-detail': row.percent == null }"
            >
              <div><span>{{ row.label }}</span><strong>{{ formatCompact(row.value) }}</strong></div>
              <template v-if="row.percent != null">
                <div class="token-breakdown__track">
                  <i :style="{ width: `${row.percent}%`, background: row.color }" />
                </div>
                <small>{{ row.percent.toFixed(1) }}%</small>
              </template>
            </div>
          </div>
        </section>

        <section class="usage-panel">
          <div class="usage-panel__head">
            <div><h2>{{ t('insight.usage.usageByBackupSource') }}</h2></div>
          </div>
          <ElTable
            :data="usage?.by_backup_source || []"
            class="hfl-list-table usage-source-table"
            @row-click="filterByBackupSource"
          >
            <ElTableColumn
              :label="t('insight.usage.backupSource')"
              min-width="240"
            >
              <template #default="{ row }">
                <div class="source-usage-list__identity">
                  <span class="source-usage-list__icon"><DatabaseBackup :size="16" /></span>
                  <span class="source-usage-list__name">{{ row.backup_source_name }}</span>
                </div>
              </template>
            </ElTableColumn>
            <ElTableColumn
              :label="t('insight.usage.questions')"
              width="110"
              align="right"
            >
              <template #default="{ row }">
                {{ formatNumber(row.q_and_a_requests) }}
              </template>
            </ElTableColumn>
            <ElTableColumn
              :label="t('insight.usage.aiCalls')"
              width="110"
              align="right"
            >
              <template #default="{ row }">
                {{ formatNumber(row.model_calls) }}
              </template>
            </ElTableColumn>
            <ElTableColumn
              :label="t('insight.usage.totalTokens')"
              width="120"
              align="right"
            >
              <template #default="{ row }">
                {{ formatCompact(row.total_tokens) }}
              </template>
            </ElTableColumn>
            <ElTableColumn
              :label="t('insight.usage.cost')"
              width="130"
              align="right"
            >
              <template #default="{ row }">
                <strong>{{ formatCost(row.estimated_cost) }}</strong>
              </template>
            </ElTableColumn>
            <template #empty>
              <ElEmpty :description="t('insight.usage.noBackupSourceUsage')" />
            </template>
          </ElTable>
        </section>
      </div>

      <section class="usage-panel usage-table-panel">
        <div class="usage-panel__head usage-history-head">
          <h2>{{ t('insight.usage.questionHistory') }}</h2>
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
              v-model="backupSourceFilter"
              clearable
              :placeholder="t('insight.usage.allBackupSources')"
              class="usage-filter-select"
            >
              <ElOption
                v-for="name in usage?.backup_sources || []"
                :key="name"
                :label="name"
                :value="name"
              />
            </ElSelect>
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
        </div>

        <ElTable
          v-loading="loading"
          :data="usage?.results || []"
          stripe
          flexible
          class="hfl-list-table usage-history-table"
          row-key="run_uuid"
        >
          <ElTableColumn
            :label="t('insight.usage.question')"
            min-width="280"
            fixed="left"
          >
            <template #default="{ row }">
              <span class="usage-history-question">{{ row.question || t('insight.usage.questionUnavailable') }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('insight.usage.time')"
            width="150"
          >
            <template #default="{ row }">
              <span
                class="usage-table__time"
                :class="{ 'hfl-empty-mark': !row.time }"
              >{{ formatShortDateTime(row.time) }}</span>
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
            :label="t('insight.usage.aiCalls')"
            width="100"
            align="right"
          >
            <template #default="{ row }">
              {{ formatNumber(row.model_calls) }}
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('insight.usage.totalTokens')"
            width="120"
            align="right"
          >
            <template #default="{ row }">
              {{ formatCompact(row.total_tokens) }}
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('insight.usage.cost')"
            width="120"
            align="right"
          >
            <template #default="{ row }">
              {{ formatCost(row.estimated_cost, row.cost_currency) }}
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
      </section>
    </div>
  </ModulePage>
</template>

<style scoped>
.usage-page { min-height: 100%; padding: 24px 28px 36px; background: var(--color-grey-2); color: var(--color-text-title); }
.usage-icon-button { display: inline-flex; width: 34px; height: 34px; align-items: center; justify-content: center; padding: 0; border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-card-bg); color: var(--color-text-secondary); cursor: pointer; }
.usage-icon-button:hover { border-color: var(--color-primary-light); color: var(--color-primary); }
.usage-icon-button:disabled { cursor: default; opacity: .55; }
.usage-period { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
.usage-period__label { color: var(--color-text-tertiary); font-size: 12px; font-weight: 500; }
.usage-segments { display: inline-flex; padding: 3px; border-radius: 8px; background: var(--color-grey-3); }
.usage-segments button { min-height: 29px; padding: 0 12px; border: 0; border-radius: 6px; background: transparent; color: var(--color-text-tertiary); font-size: 13px; font-weight: 400; cursor: pointer; }
.usage-segments button:hover { color: var(--color-text-title); }
.usage-segments button.active { background: var(--color-card-bg); color: var(--color-primary); box-shadow: 0 1px 3px rgb(16 24 40 / 10%); }
.usage-segments--small button { min-height: 27px; padding: 0 10px; font-size: 11px; }
.usage-date-picker { width: 250px !important; }
.usage-freshness { overflow: hidden; color: var(--color-text-tertiary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.usage-summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }
.usage-summary-card { display: flex; min-width: 0; align-items: center; gap: 13px; padding: 17px; border: 1px solid var(--color-border-light); border-radius: 10px; background: var(--color-card-bg); }
.usage-summary-card__icon { display: inline-flex; width: 36px; height: 36px; flex: 0 0 36px; align-items: center; justify-content: center; border-radius: 9px; }
.usage-summary-card__icon.is-cost { background: #fff6df; color: #b66b00; }.usage-summary-card__icon.is-token { background: #f0edff; color: #654cf0; }.usage-summary-card__icon.is-qa { background: #eaf8f3; color: #087a5b; }.usage-summary-card__icon.is-calls { background: #eaf2ff; color: #2869d8; }
.usage-summary-card div { display: grid; min-width: 0; gap: 4px; }
.usage-summary-card span { color: var(--color-text-tertiary); font-size: 12px; font-weight: 500; }
.usage-summary-card strong { overflow: hidden; color: var(--color-text-title); font-size: 24px; font-weight: 700; line-height: 29px; text-overflow: ellipsis; white-space: nowrap; }
.usage-panel { border: 1px solid var(--color-border-light); border-radius: 10px; background: var(--color-card-bg); }
.usage-panel__head { display: flex; min-height: 60px; align-items: center; justify-content: space-between; gap: 16px; padding: 13px 16px; border-bottom: 1px solid var(--color-border-light); }
.usage-panel__head h2 { margin: 0; font-size: 14px; font-weight: 600; line-height: 21px; }
.usage-panel__head p { margin: 2px 0 0; color: var(--color-text-tertiary); font-size: 12px; line-height: 18px; }
.usage-trend-panel { margin-bottom: 14px; }
.usage-chart { width: 100%; height: 250px; }
.usage-chart-empty { display: flex; height: 250px; flex-direction: column; align-items: center; justify-content: center; gap: 9px; color: var(--color-text-tertiary); font-size: 12px; }
.usage-breakdown-grid { display: grid; grid-template-columns: minmax(0, .9fr) minmax(0, 1.1fr); gap: 14px; margin-bottom: 14px; }
.token-breakdown { display: grid; gap: 17px; padding: 18px; }
.token-breakdown__row { display: grid; grid-template-columns: minmax(0, 1fr) 42px; gap: 7px 10px; }
.token-breakdown__row > div:first-child { grid-column: 1 / -1; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.token-breakdown__row span { color: var(--color-text-secondary); font-size: 12px; }.token-breakdown__row strong { font-size: 12px; font-weight: 500; }
.token-breakdown__track { height: 6px; overflow: hidden; border-radius: 999px; background: var(--color-grey-3); }.token-breakdown__track i { display: block; height: 100%; min-width: 0; border-radius: inherit; }
.token-breakdown__row small { color: var(--color-text-tertiary); font-size: 11px; text-align: right; }
.token-breakdown__row.is-detail { grid-template-columns: minmax(0, 1fr); }
.token-breakdown__row:nth-child(3) { padding-top: 17px; border-top: 1px solid var(--color-border-light); }
.source-usage-list__icon { display: inline-flex; width: 26px; height: 26px; align-items: center; justify-content: center; border-radius: 7px; background: var(--color-grey-3); color: var(--color-primary); }
.source-usage-list__name { overflow: hidden; color: var(--color-text-title); font-size: 12px; font-weight: 500; text-align: left; text-overflow: ellipsis; white-space: nowrap; }
.usage-table-panel { min-width: 0; }
.usage-search { display: flex; width: 245px; height: 32px; align-items: center; gap: 7px; padding: 0 10px; border: 1px solid var(--color-border); border-radius: 7px; background: var(--color-card-bg); color: var(--color-text-tertiary); }
.usage-search:focus-within { border-color: var(--color-primary-light); box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-primary) 10%, transparent); }
.usage-search input { width: 100%; min-width: 0; border: 0; outline: 0; background: transparent; color: var(--color-text-title); font-size: 13px; }
.usage-history-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; margin-left: auto; }
.usage-history-question { display: block; overflow: hidden; width: 100%; color: var(--color-text-title); font-size: 12px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.usage-history-source { display: grid; min-width: 0; gap: 2px; }
.usage-history-source strong,.usage-history-source span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.usage-history-source strong { color: var(--color-text-title); font-size: 12px; font-weight: 500; }
.usage-history-source span { color: var(--color-text-tertiary); font-size: 11px; }
.usage-source-table :deep(.el-table__row) { cursor: pointer; }
.usage-source-table { width: 100%; }
.usage-filter-select { width: 150px; }.usage-filter-select--status { width: 120px; }
.usage-table__time { color: var(--color-text-tertiary) !important; white-space: nowrap; }
.usage-status { display: inline-flex !important; width: fit-content; align-items: center; gap: 5px; margin: 0 !important; padding: 2px 7px; border-radius: 999px; background: #ecfdf3; color: #027a48 !important; font-size: 11px !important; font-weight: 600; }.usage-status i { width: 6px; height: 6px; border-radius: 999px; background: currentColor; }.usage-status.is-failed { background: #fef3f2; color: #d92d20 !important; }.usage-status.is-cancelled { background: var(--color-grey-3); color: var(--color-text-tertiary) !important; }.usage-status.is-queued,.usage-status.is-running,.usage-status.is-streaming { background: #f2f0ff; color: #654cf0 !important; }
.is-spinning { animation: usage-spin .8s linear infinite; }
@keyframes usage-spin { to { transform: rotate(360deg); } }
/* Dashboard layout aligned with Operations list pages. */
.usage-page {
  box-sizing: border-box;
  display: grid;
  width: 100%;
  max-width: none;
  min-height: 0;
  grid-template-areas:
    "period period"
    "summary summary"
    "trend tokens"
    "sources sources"
    "history history";
  grid-template-columns: minmax(0, 1.8fr) minmax(320px, .8fr);
  gap: 16px;
  align-content: start;
  margin: 0;
  padding: 0 0 8px;
  background: transparent;
}

.usage-icon-button {
  width: 36px;
  height: 36px;
  flex: 0 0 36px;
  border-radius: 9px;
  transition: border-color .16s ease, background-color .16s ease, color .16s ease;
}

.usage-icon-button:hover:not(:disabled) {
  background: color-mix(in srgb, var(--color-primary) 6%, var(--color-card-bg));
}

.usage-icon-button:focus-visible,
.usage-segments button:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--color-primary) 55%, transparent);
  outline-offset: 2px;
}

.usage-period {
  grid-area: period;
  min-height: 40px;
  gap: 10px;
  margin: 0;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}

.usage-period__label {
  margin: 0 4px;
  font-size: 12px;
  font-weight: 500;
  text-transform: none;
  letter-spacing: 0;
}

.usage-segments {
  padding: 3px;
  border: 1px solid var(--color-border-light);
  background: var(--color-grey-2);
}

.usage-segments button {
  min-height: 30px;
  padding: 0 13px;
  font-size: 13px;
  font-weight: 400;
  transition: background-color .16s ease, color .16s ease, box-shadow .16s ease;
}

.usage-segments button.active {
  font-weight: 500;
}

.usage-segments--small button {
  font-size: 12px;
}

.usage-period__tail {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.usage-period :deep(.usage-date-picker.el-date-editor) {
  width: 260px !important;
  max-width: 100%;
  height: 34px;
  flex: 0 0 260px;
}

.usage-period :deep(.usage-date-picker .el-input__wrapper) {
  min-height: 34px;
}

.usage-period :deep(.usage-date-picker .el-range-input) {
  font-size: 13px;
}

.usage-period :deep(.usage-date-picker .el-range-separator) {
  padding: 0 4px;
}

.usage-summary-grid {
  grid-area: summary;
  gap: 14px;
  margin: 0;
}

.usage-summary-card {
  min-height: 88px;
  gap: 14px;
  padding: 17px 18px;
  border-color: var(--color-border-light);
  border-radius: 11px;
  box-shadow: none;
}

.usage-summary-card__icon {
  width: 40px;
  height: 40px;
  flex-basis: 40px;
  border-radius: 10px;
}

.usage-summary-card div {
  gap: 5px;
}

.usage-summary-card span {
  font-size: 12px;
  font-weight: 500;
}

.usage-summary-card strong {
  font-size: 24px;
  font-weight: 700;
  line-height: 29px;
  font-variant-numeric: tabular-nums;
}

.usage-panel {
  min-width: 0;
  overflow: hidden;
  border-color: var(--color-border-light);
  border-radius: 11px;
  box-shadow: none;
}

.usage-panel__head {
  min-height: 64px;
  padding: 13px 16px;
}

.usage-panel__head h2 {
  font-size: 14px;
  font-weight: 600;
  line-height: 21px;
}

.usage-panel__head p {
  margin-top: 2px;
  font-size: 12px;
  line-height: 18px;
}

.usage-trend-panel {
  grid-area: trend;
  margin: 0;
}

.usage-chart,
.usage-chart-empty {
  height: 292px;
}

.usage-breakdown-grid {
  display: contents;
}

.usage-breakdown-grid > .usage-panel:first-child {
  grid-area: tokens;
}

.usage-breakdown-grid > .usage-panel:last-child {
  grid-area: sources;
}

.token-breakdown {
  min-height: 292px;
  align-content: center;
  gap: 22px;
  padding: 22px 20px;
}

.token-breakdown__row {
  grid-template-columns: minmax(0, 1fr) 46px;
  gap: 8px 12px;
}

.token-breakdown__row span,
.token-breakdown__row strong {
  font-size: 12px;
}

.token-breakdown__row strong {
  font-weight: 500;
}

.token-breakdown__track {
  height: 7px;
}

.source-usage-list__identity {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}

.source-usage-list__icon {
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  border-radius: 8px;
}

.source-usage-list__name {
  font-size: 12px;
  font-weight: 500;
}

.usage-table-panel {
  grid-area: history;
}

.usage-history-footer {
  border-top: 1px solid var(--color-border-light);
}

.usage-search {
  width: 260px;
  height: 34px;
  border-radius: 8px;
}

.usage-filter-select {
  width: 180px;
}

.usage-filter-select--status {
  width: 140px;
}


@media (max-width: 1280px) {
  .usage-page {
    grid-template-columns: minmax(0, 1.45fr) minmax(300px, .75fr);
  }

  .usage-summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1040px) {
  .usage-page {
    grid-template-areas:
      "period"
      "summary"
      "trend"
      "tokens"
      "sources"
      "history";
    grid-template-columns: minmax(0, 1fr);
  }

  .usage-period {
    flex-wrap: wrap;
  }

  .usage-period :deep(.usage-date-picker.el-date-editor) {
    margin-left: 0;
  }

  .token-breakdown {
    min-height: auto;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    align-content: start;
  }

  .usage-history-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .usage-history-actions {
    width: 100%;
    justify-content: flex-start;
    margin-left: 0;
  }

  .usage-search {
    flex: 1 1 260px;
  }
}

@media (max-width: 760px) {
  .usage-page {
    gap: 12px;
    padding-bottom: 4px;
  }

  .usage-summary-grid,
  .token-breakdown {
    grid-template-columns: minmax(0, 1fr);
  }

  .usage-period {
    align-items: stretch;
    flex-direction: column;
  }

  .usage-segments {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .usage-segments button {
    padding: 0 6px;
  }

  .usage-period :deep(.usage-date-picker.el-date-editor) {
    width: 100% !important;
    flex-basis: auto;
  }

  .usage-period__tail {
    width: 100%;
    margin-left: 0;
  }

  .usage-period__tail :deep(.usage-date-picker) {
    min-width: 0;
    flex: 1;
  }

  .usage-freshness {
    display: none;
  }

  .usage-chart,
  .usage-chart-empty {
    height: 250px;
  }

  .usage-search,
  .usage-filter-select,
  .usage-filter-select--status {
    width: 100%;
    flex-basis: auto;
  }

  .usage-history-actions {
    align-items: stretch;
    flex-direction: column;
  }

}
</style>
