<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { use } from 'echarts/core'
import { LineChart, ScatterChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import { ChartSpline } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import { apiErrorMessage } from '../lib/api'
import {
  getStorageRepositoryUsageHistory,
  type StorageRepositoryUsageHistory,
  type StorageRepositoryUsageHistoryRange,
} from '../lib/storageRepositoryApi'
import {
  repositoryCapacityIsolatedSeries,
  repositoryCapacityLineSeries,
} from '../lib/repositoryUsageHistory'
import { formatAppDateTime } from '../lib/dateTime'

use([CanvasRenderer, LineChart, ScatterChart, DataZoomComponent, GridComponent, TooltipComponent])

const props = defineProps<{
  repositoryId: number
  active: boolean
}>()

const { t } = useI18n()
const selectedRange = ref<StorageRepositoryUsageHistoryRange>('7d')
const history = ref<StorageRepositoryUsageHistory | null>(null)
const loading = ref(false)
const error = ref('')
let requestController: AbortController | null = null

const rangeOptions = computed(() => [
  { value: '24h' as const, label: t('repositoriesPage.capacityRange24h') },
  { value: '7d' as const, label: t('repositoriesPage.capacityRange7d') },
  { value: '15d' as const, label: t('repositoriesPage.capacityRange15d') },
  { value: '30d' as const, label: t('repositoriesPage.capacityRange30d') },
])

function formatBytes(value: number | null) {
  if (value == null || !Number.isFinite(value)) return '—'
  if (value === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let amount = Math.abs(value)
  let unit = 0
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024
    unit += 1
  }
  const sign = value < 0 ? '-' : ''
  return `${sign}${amount.toFixed(unit >= 2 ? 1 : 0)} ${units[unit]}`
}

const validPoints = computed(() => history.value?.points.filter(point => point.usage_bytes != null) || [])
const latestPoint = computed(() => validPoints.value[validPoints.value.length - 1] || null)
const chartOption = computed(() => ({
  animation: false,
  grid: { left: 12, right: 18, top: 24, bottom: 54, containLabel: true },
  tooltip: {
    trigger: 'axis',
    valueFormatter: (value: unknown) => formatBytes(typeof value === 'number' ? value : Number(value)),
  },
  xAxis: {
    type: 'time',
    boundaryGap: false,
    axisLabel: { hideOverlap: true },
  },
  dataZoom: [{
    type: 'inside',
    xAxisIndex: 0,
    filterMode: 'none',
    zoomOnMouseWheel: true,
    moveOnMouseMove: true,
    moveOnMouseWheel: false,
  }, {
    type: 'slider',
    xAxisIndex: 0,
    filterMode: 'none',
    height: 18,
    bottom: 10,
  }],
  yAxis: {
    type: 'value',
    min: 0,
    axisLabel: {
      formatter: (value: number) => formatBytes(value),
    },
    splitLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.2)' } },
  },
  series: [{
    name: t('repositoriesPage.capacityOccupied'),
    type: 'line',
    showSymbol: false,
    connectNulls: false,
    smooth: false,
    data: repositoryCapacityLineSeries(history.value?.points || []),
    lineStyle: { width: 2, color: '#165fff' },
    itemStyle: { color: '#165fff' },
    areaStyle: { color: 'rgba(22, 95, 255, 0.08)' },
  }, {
    name: t('repositoriesPage.capacityOccupied'),
    type: 'scatter',
    symbolSize: 4,
    data: repositoryCapacityIsolatedSeries(history.value?.points || []),
    itemStyle: { color: '#165fff' },
    z: 3,
  }],
}))

async function loadHistory() {
  if (!props.active || !props.repositoryId) return
  requestController?.abort()
  requestController = new AbortController()
  loading.value = true
  error.value = ''
  try {
    history.value = await getStorageRepositoryUsageHistory(
      props.repositoryId,
      selectedRange.value,
      { signal: requestController.signal },
    )
  } catch (err) {
    if (requestController.signal.aborted) return
    history.value = null
    error.value = apiErrorMessage(err, t('repositoriesPage.capacityHistoryLoadFailed'))
  } finally {
    if (!requestController.signal.aborted) loading.value = false
  }
}

watch(
  () => [props.active, props.repositoryId, selectedRange.value] as const,
  () => void loadHistory(),
  { immediate: true },
)

onBeforeUnmount(() => requestController?.abort())
</script>

<template>
  <div
    v-loading="loading"
    class="repository-capacity-trend"
  >
    <div class="repository-capacity-trend__toolbar">
      <ElRadioGroup
        v-model="selectedRange"
        size="small"
      >
        <ElRadioButton
          v-for="option in rangeOptions"
          :key="option.value"
          :value="option.value"
        >
          {{ option.label }}
        </ElRadioButton>
      </ElRadioGroup>
    </div>

    <ElAlert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />

    <template v-else>
      <div class="repository-capacity-trend__summary">
        <div class="repository-capacity-trend__metric">
          <span>{{ t('repositoriesPage.capacityOccupied') }}</span>
          <strong>{{ formatBytes(latestPoint?.usage_bytes ?? null) }}</strong>
          <small>{{ t('repositoriesPage.capacitySourceEstimated') }}</small>
        </div>
        <div class="repository-capacity-trend__metric">
          <span>{{ t('repositoriesPage.capacityLastSample') }}</span>
          <strong class="repository-capacity-trend__time">
            {{ latestPoint ? formatAppDateTime(latestPoint.sampled_at || latestPoint.recorded_at, '—') : '—' }}
          </strong>
          <small>{{ history ? t('repositoriesPage.capacityResolution', { interval: history.interval }) : '—' }}</small>
        </div>
      </div>

      <div class="repository-capacity-trend__chart-card">
        <VChart
          v-if="validPoints.length"
          class="repository-capacity-trend__chart"
          :option="chartOption"
          autoresize
        />
        <div
          v-else-if="!loading"
          class="repository-capacity-trend__empty"
        >
          <ChartSpline :size="30" />
          <span>{{ t('repositoriesPage.capacityHistoryEmpty') }}</span>
        </div>
      </div>

      <p
        v-if="history?.points.some(point => point.coverage === 'missing')"
        class="repository-capacity-trend__gap-note"
      >
        {{ t('repositoriesPage.capacityMissingDataNote') }}
      </p>
    </template>
  </div>
</template>

<style scoped>
.repository-capacity-trend {
  display: grid;
  min-height: 440px;
  gap: 16px;
}

.repository-capacity-trend__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: flex-end;
  gap: 12px;
}

.repository-capacity-trend__summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.repository-capacity-trend__metric {
  display: grid;
  min-width: 0;
  gap: 5px;
  padding: 14px 16px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.repository-capacity-trend__metric span,
.repository-capacity-trend__metric small {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.repository-capacity-trend__metric strong {
  overflow: hidden;
  color: var(--color-text-primary);
  font-size: 20px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.repository-capacity-trend__metric .repository-capacity-trend__time {
  font-size: 14px;
}

.repository-capacity-trend__chart-card {
  min-height: 300px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.repository-capacity-trend__chart {
  width: 100%;
  height: 320px;
}

.repository-capacity-trend__empty {
  display: grid;
  min-height: 300px;
  place-content: center;
  justify-items: center;
  gap: 10px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.repository-capacity-trend__gap-note {
  margin: -4px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
}

@media (max-width: 760px) {
  .repository-capacity-trend__summary {
    grid-template-columns: 1fr;
  }

  .repository-capacity-trend__toolbar :deep(.el-radio-group) {
    width: 100%;
  }

  .repository-capacity-trend__toolbar :deep(.el-radio-button) {
    flex: 1;
  }
}
</style>
