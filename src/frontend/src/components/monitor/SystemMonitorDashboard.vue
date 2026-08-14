<script setup lang="ts">
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { ChartSpline } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import OpsStatCard from '../ops/OpsStatCard.vue'

use([CanvasRenderer, LineChart, GridComponent, LegendComponent, TooltipComponent])

defineProps<{
  kpiCards: Array<{
    label: string
    value: string
    sub: string
    accent: 'indigo' | 'green' | 'orange' | 'pink'
    icon: unknown
  }>
  hasChartData: (option: unknown) => boolean
  cpuOption: unknown
  loadOption: unknown
  memoryOption: unknown
  diskUsageOption: unknown
  diskThroughputOption: unknown
  diskIopsOption: unknown
  networkBytesOption: unknown
  networkPacketsOption: unknown
  uniqueDiskNames: string[]
  uniqueNetworkNames: string[]
  selectedDisk: string
  selectedNetwork: string
}>()

const emit = defineEmits<{
  'update:selectedDisk': [value: string]
  'update:selectedNetwork': [value: string]
}>()

const { t } = useI18n()
</script>

<template>
  <div class="host-monitor">
    <div class="host-monitor__kpi">
      <OpsStatCard
        v-for="(card, index) in kpiCards"
        :key="index"
        class="host-monitor__kpi-card"
        :label="card.label"
        :value="card.value"
        :sub="card.sub"
        :accent="card.accent"
        accent-side="left"
      >
        <template #icon>
          <component
            :is="card.icon"
            :size="17"
          />
        </template>
      </OpsStatCard>
    </div>

    <section class="host-monitor__section">
      <div class="host-monitor__section-head">
        <span class="host-monitor__section-bar" />
        <h2>{{ t('ops.monitorPage.systemResources') }}</h2>
      </div>
      <div class="host-monitor__charts host-monitor__charts--quad">
        <div class="chart-card">
          <div class="chart-card__head">
            <h3>{{ t('ops.monitorPage.cpuUsage') }}</h3>
          </div>
          <VChart
            v-if="hasChartData(cpuOption)"
            class="chart-card__chart"
            :option="cpuOption"
            autoresize
          />
          <div
            v-else
            class="chart-card__empty"
          >
            <ChartSpline :size="28" />
            <span>{{ t('ops.monitorPage.noChartData') }}</span>
          </div>
        </div>
        <div class="chart-card">
          <div class="chart-card__head">
            <h3>{{ t('ops.monitorPage.loadAverage') }}</h3>
          </div>
          <VChart
            v-if="hasChartData(loadOption)"
            class="chart-card__chart"
            :option="loadOption"
            autoresize
          />
          <div
            v-else
            class="chart-card__empty"
          >
            <ChartSpline :size="28" />
            <span>{{ t('ops.monitorPage.noChartData') }}</span>
          </div>
        </div>
        <div class="chart-card">
          <div class="chart-card__head">
            <h3>{{ t('ops.monitorPage.memoryUsage') }}</h3>
          </div>
          <VChart
            v-if="hasChartData(memoryOption)"
            class="chart-card__chart"
            :option="memoryOption"
            autoresize
          />
          <div
            v-else
            class="chart-card__empty"
          >
            <ChartSpline :size="28" />
            <span>{{ t('ops.monitorPage.noChartData') }}</span>
          </div>
        </div>
        <div class="chart-card">
          <div class="chart-card__head">
            <h3>{{ t('ops.monitorPage.diskUsage') }}</h3>
            <ElSelect
              v-if="uniqueDiskNames.length"
              :model-value="selectedDisk"
              size="small"
              class="chart-card__filter"
              @update:model-value="emit('update:selectedDisk', $event)"
            >
              <ElOption
                :label="t('ops.monitorPage.all')"
                value="all"
              />
              <ElOption
                v-for="name in uniqueDiskNames"
                :key="name"
                :label="name"
                :value="name"
              />
            </ElSelect>
          </div>
          <VChart
            v-if="hasChartData(diskUsageOption)"
            class="chart-card__chart"
            :option="diskUsageOption"
            autoresize
          />
          <div
            v-else
            class="chart-card__empty"
          >
            <ChartSpline :size="28" />
            <span>{{ t('ops.monitorPage.noChartData') }}</span>
          </div>
        </div>
      </div>
    </section>

    <section class="host-monitor__section">
      <div class="host-monitor__section-head">
        <span class="host-monitor__section-bar" />
        <h2>{{ t('ops.monitorPage.storageSection') }}</h2>
      </div>
      <div class="host-monitor__charts host-monitor__charts--pair">
        <div class="chart-card">
          <div class="chart-card__head">
            <h3>{{ t('ops.monitorPage.diskThroughput') }}</h3>
          </div>
          <VChart
            v-if="hasChartData(diskThroughputOption)"
            class="chart-card__chart"
            :option="diskThroughputOption"
            autoresize
          />
          <div
            v-else
            class="chart-card__empty"
          >
            <ChartSpline :size="28" />
            <span>{{ t('ops.monitorPage.noChartData') }}</span>
          </div>
        </div>
        <div class="chart-card">
          <div class="chart-card__head">
            <h3>{{ t('ops.monitorPage.diskIops') }}</h3>
          </div>
          <VChart
            v-if="hasChartData(diskIopsOption)"
            class="chart-card__chart"
            :option="diskIopsOption"
            autoresize
          />
          <div
            v-else
            class="chart-card__empty"
          >
            <ChartSpline :size="28" />
            <span>{{ t('ops.monitorPage.noChartData') }}</span>
          </div>
        </div>
      </div>
    </section>

    <section class="host-monitor__section">
      <div class="host-monitor__section-head">
        <span class="host-monitor__section-bar" />
        <h2>{{ t('ops.monitorPage.networkSection') }}</h2>
      </div>
      <div class="host-monitor__charts host-monitor__charts--pair">
        <div class="chart-card">
          <div class="chart-card__head">
            <h3>{{ t('ops.monitorPage.networkTraffic') }}</h3>
            <ElSelect
              v-if="uniqueNetworkNames.length"
              :model-value="selectedNetwork"
              size="small"
              class="chart-card__filter"
              @update:model-value="emit('update:selectedNetwork', $event)"
            >
              <ElOption
                :label="t('ops.monitorPage.all')"
                value="all"
              />
              <ElOption
                v-for="name in uniqueNetworkNames"
                :key="name"
                :label="name"
                :value="name"
              />
            </ElSelect>
          </div>
          <VChart
            v-if="hasChartData(networkBytesOption)"
            class="chart-card__chart"
            :option="networkBytesOption"
            autoresize
          />
          <div
            v-else
            class="chart-card__empty"
          >
            <ChartSpline :size="28" />
            <span>{{ t('ops.monitorPage.noChartData') }}</span>
          </div>
        </div>
        <div class="chart-card">
          <div class="chart-card__head">
            <h3>{{ t('ops.monitorPage.networkPackets') }}</h3>
          </div>
          <VChart
            v-if="hasChartData(networkPacketsOption)"
            class="chart-card__chart"
            :option="networkPacketsOption"
            autoresize
          />
          <div
            v-else
            class="chart-card__empty"
          >
            <ChartSpline :size="28" />
            <span>{{ t('ops.monitorPage.noChartData') }}</span>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped src="../../styles/system-monitor-dashboard.css"></style>
