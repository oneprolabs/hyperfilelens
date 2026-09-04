<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatBytes } from '../lib/kopiaProgress'
import {
  repositoryMaintenanceSummaryFromMetadata,
  type MaintenanceStage,
  type MaintenanceStageMetrics,
  type MaintenanceStageType,
} from '../lib/repositoryMaintenanceSummary'

const props = defineProps<{
  metadata?: unknown
}>()

const { t } = useI18n()
const summary = computed(() => repositoryMaintenanceSummaryFromMetadata(props.metadata))
const quickStages = computed(() => summary.value?.mode === 'quick' ? summary.value.stages : [])
const packStage = computed(() => quickStages.value.find(stage => stage.type === 'pack_gc'))

type CountKind = 'content' | 'pack' | 'log' | 'indexBlob'

type MetricSection = {
  type: MaintenanceStageType
  titleKey: string
  countLabelKey: string
  countKind: CountKind
  rows: Array<{ labelKey: string, prefix: string }>
  metrics: MaintenanceStageMetrics
}

const quickMetricSections = computed<MetricSection[]>(() => {
  const definitions: Array<Omit<MetricSection, 'metrics'>> = [
    {
      type: 'content_rewrite',
      titleKey: 'ops.task.maintenanceSummary.quickContentRewrite',
      countLabelKey: 'ops.task.maintenanceSummary.contentCount',
      countKind: 'content',
      rows: [
        { labelKey: 'ops.task.maintenanceSummary.rewriteFound', prefix: 'found' },
        { labelKey: 'ops.task.maintenanceSummary.rewriteCompleted', prefix: 'rewritten' },
        { labelKey: 'ops.task.maintenanceSummary.rewriteRetained', prefix: 'retained' },
      ],
    },
    {
      type: 'log_cleanup',
      titleKey: 'ops.task.maintenanceSummary.logCleanupDetails',
      countLabelKey: 'ops.task.maintenanceSummary.logCount',
      countKind: 'log',
      rows: [
        { labelKey: 'ops.task.maintenanceSummary.logsCandidates', prefix: 'candidate' },
        { labelKey: 'ops.task.maintenanceSummary.logsDeleted', prefix: 'deleted' },
        { labelKey: 'ops.task.maintenanceSummary.logsRetained', prefix: 'retained' },
      ],
    },
    {
      type: 'epoch_compaction',
      titleKey: 'ops.task.maintenanceSummary.epochCompactionDetails',
      countLabelKey: 'ops.task.maintenanceSummary.indexBlobCount',
      countKind: 'indexBlob',
      rows: [
        { labelKey: 'ops.task.maintenanceSummary.supersededIndexes', prefix: 'superseded_index' },
      ],
    },
  ]

  return definitions.flatMap((definition) => {
    const stage = quickStages.value.find(item => item.type === definition.type)
    if (!stage?.statistics_available || !stage.metrics) return []
    if (!definition.rows.some(row => hasMetric(stage.metrics, row.prefix))) return []
    return [{ ...definition, metrics: stage.metrics }]
  })
})

function numberValue(group: Record<string, number | boolean | undefined>, key: string): number | undefined {
  const value = group[key]
  return typeof value === 'number' ? value : undefined
}

function hasMetric(group: Record<string, number | boolean | undefined>, prefix: string): boolean {
  return numberValue(group, `${prefix}_count`) !== undefined
    || numberValue(group, `${prefix}_bytes`) !== undefined
}

function countValue(
  group: Record<string, number | boolean | undefined>,
  prefix: string,
  kind: CountKind,
): string {
  const count = numberValue(group, `${prefix}_count`)
  if (count === undefined) return '—'
  return t(`ops.task.maintenanceSummary.${kind}CountValue`, {
    count: count.toLocaleString(),
  })
}

function sizeValue(group: Record<string, number | boolean | undefined>, prefix: string): string {
  const bytes = numberValue(group, `${prefix}_bytes`)
  return bytes === undefined ? '—' : formatBytes(bytes)
}

function stageLabel(stage: MaintenanceStage): string {
  return t(`ops.task.maintenanceSummary.stage.${stage.type}`)
}

function stageStatisticsLabel(stage: MaintenanceStage): string {
  if (stage.status === 'not_run') return '—'
  return t(stage.statistics_available
    ? 'ops.task.maintenanceSummary.statisticsAvailable'
    : 'ops.task.maintenanceSummary.statisticsNotReported')
}

function epochAdvanceDetail(stage: MaintenanceStage): string | null {
  if (stage.type !== 'epoch_advance' || !stage.statistics_available || !stage.metrics) return null
  const epoch = numberValue(stage.metrics, 'current_epoch')
  const advanced = stage.metrics.advanced
  if (epoch === undefined || typeof advanced !== 'boolean') return null
  return t(advanced
    ? 'ops.task.maintenanceSummary.advancedToEpoch'
    : 'ops.task.maintenanceSummary.stayedAtEpoch', {
    epoch: epoch.toLocaleString(),
  })
}

function packGcAbsentMessage(): string {
  if (packStage.value?.status === 'not_run') {
    return t('ops.task.maintenanceSummary.packGcNotRun')
  }
  if (packStage.value?.status === 'completed') {
    return t('ops.task.maintenanceSummary.packGcStatisticsNotReported')
  }
  return t('ops.task.maintenanceSummary.packGcNotReported')
}
</script>

<template>
  <div
    v-if="summary"
    class="repository-maintenance-summary"
  >
    <section
      v-if="quickStages.length"
      class="repository-maintenance-summary__section"
    >
      <strong>{{ t('ops.task.maintenanceSummary.quickOperations') }}</strong>
      <table class="repository-maintenance-summary__operations">
        <thead>
          <tr>
            <th scope="col">
              {{ t('ops.task.maintenanceSummary.operation') }}
            </th>
            <th scope="col">
              {{ t('ops.task.maintenanceSummary.executionStatus') }}
            </th>
            <th scope="col">
              {{ t('ops.task.maintenanceSummary.statistics') }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="stage in quickStages"
            :key="stage.type"
          >
            <td>{{ stageLabel(stage) }}</td>
            <td :data-label="t('ops.task.maintenanceSummary.executionStatus')">
              <span
                class="repository-maintenance-summary__status"
                :class="`repository-maintenance-summary__status--${stage.status}`"
              >
                {{ t(stage.status === 'completed'
                  ? 'ops.task.maintenanceSummary.completed'
                  : 'ops.task.maintenanceSummary.notRun') }}
              </span>
            </td>
            <td :data-label="t('ops.task.maintenanceSummary.statistics')">
              <span>{{ stageStatisticsLabel(stage) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p
        v-for="stage in quickStages"
        :key="`${stage.type}-detail`"
        class="repository-maintenance-summary__stage-detail"
      >
        {{ epochAdvanceDetail(stage) }}
      </p>
    </section>
    <section
      v-if="summary.content_gc"
      class="repository-maintenance-summary__section"
    >
      <strong>{{ t('ops.task.maintenanceSummary.contentGc') }}</strong>
      <table>
        <thead>
          <tr>
            <th scope="col">
              {{ t('ops.task.maintenanceSummary.result') }}
            </th>
            <th scope="col">
              {{ t('ops.task.maintenanceSummary.contentCount') }}
            </th>
            <th scope="col">
              {{ t('ops.task.maintenanceSummary.dataSize') }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-if="hasMetric(summary.content_gc, 'deleted')"
            class="repository-maintenance-summary__row--success"
          >
            <td>{{ t('ops.task.maintenanceSummary.contentRemoved') }}</td>
            <td :data-label="t('ops.task.maintenanceSummary.contentCount')">
              <span>{{ countValue(summary.content_gc, 'deleted', 'content') }}</span>
            </td>
            <td :data-label="t('ops.task.maintenanceSummary.dataSize')">
              <span>{{ sizeValue(summary.content_gc, 'deleted') }}</span>
            </td>
          </tr>
          <tr
            v-if="hasMetric(summary.content_gc, 'deferred')"
            class="repository-maintenance-summary__row--warning"
          >
            <td>{{ t('ops.task.maintenanceSummary.contentDeferred') }}</td>
            <td :data-label="t('ops.task.maintenanceSummary.contentCount')">
              <span>{{ countValue(summary.content_gc, 'deferred', 'content') }}</span>
            </td>
            <td :data-label="t('ops.task.maintenanceSummary.dataSize')">
              <span>{{ sizeValue(summary.content_gc, 'deferred') }}</span>
            </td>
          </tr>
          <tr v-if="hasMetric(summary.content_gc, 'in_use')">
            <td>{{ t('ops.task.maintenanceSummary.contentInUse') }}</td>
            <td :data-label="t('ops.task.maintenanceSummary.contentCount')">
              <span>{{ countValue(summary.content_gc, 'in_use', 'content') }}</span>
            </td>
            <td :data-label="t('ops.task.maintenanceSummary.dataSize')">
              <span>{{ sizeValue(summary.content_gc, 'in_use') }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
    <section
      v-for="section in quickMetricSections"
      :key="section.type"
      class="repository-maintenance-summary__section"
    >
      <strong>{{ t(section.titleKey) }}</strong>
      <table>
        <thead>
          <tr>
            <th scope="col">
              {{ t('ops.task.maintenanceSummary.result') }}
            </th>
            <th scope="col">
              {{ t(section.countLabelKey) }}
            </th>
            <th scope="col">
              {{ t('ops.task.maintenanceSummary.dataSize') }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in section.rows.filter(item => hasMetric(section.metrics, item.prefix))"
            :key="row.prefix"
          >
            <td>{{ t(row.labelKey) }}</td>
            <td :data-label="t(section.countLabelKey)">
              <span>{{ countValue(section.metrics, row.prefix, section.countKind) }}</span>
            </td>
            <td :data-label="t('ops.task.maintenanceSummary.dataSize')">
              <span>{{ sizeValue(section.metrics, row.prefix) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
    <section
      v-if="summary.pack_gc || summary.mode === 'full' || summary.content_gc || packStage"
      class="repository-maintenance-summary__section"
    >
      <strong>{{ t('ops.task.maintenanceSummary.packGc') }}</strong>
      <table v-if="summary.pack_gc">
        <thead>
          <tr>
            <th scope="col">
              {{ t('ops.task.maintenanceSummary.result') }}
            </th>
            <th scope="col">
              {{ t('ops.task.maintenanceSummary.packCount') }}
            </th>
            <th scope="col">
              {{ t('ops.task.maintenanceSummary.dataSize') }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-if="hasMetric(summary.pack_gc, 'deleted')"
            class="repository-maintenance-summary__row--success"
          >
            <td>{{ t('ops.task.maintenanceSummary.packsDeleted') }}</td>
            <td :data-label="t('ops.task.maintenanceSummary.packCount')">
              <span>{{ countValue(summary.pack_gc, 'deleted', 'pack') }}</span>
            </td>
            <td :data-label="t('ops.task.maintenanceSummary.dataSize')">
              <span>{{ sizeValue(summary.pack_gc, 'deleted') }}</span>
            </td>
          </tr>
          <tr
            v-if="hasMetric(summary.pack_gc, 'retained')"
            class="repository-maintenance-summary__row--warning"
          >
            <td>{{ t('ops.task.maintenanceSummary.packsRetained') }}</td>
            <td :data-label="t('ops.task.maintenanceSummary.packCount')">
              <span>{{ countValue(summary.pack_gc, 'retained', 'pack') }}</span>
            </td>
            <td :data-label="t('ops.task.maintenanceSummary.dataSize')">
              <span>{{ sizeValue(summary.pack_gc, 'retained') }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p
        v-else
        class="repository-maintenance-summary__note"
      >
        {{ packGcAbsentMessage() }}
      </p>
    </section>
    <p
      v-if="summary.approximate"
      class="repository-maintenance-summary__note"
    >
      {{ t('ops.task.maintenanceSummary.approximate') }}
    </p>
  </div>
</template>

<style scoped>
.repository-maintenance-summary {
  display: grid;
  gap: 8px;
  margin-top: 6px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-light);
  font-size: 12px;
  box-sizing: border-box;
  container-type: inline-size;
  width: min(100%, 440px);
}

.repository-maintenance-summary__section {
  display: grid;
  gap: 4px;
}

.repository-maintenance-summary table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}

.repository-maintenance-summary th {
  padding: 2px 6px 4px 0;
  color: var(--el-text-color-secondary);
  font-weight: 400;
  text-align: right;
}

.repository-maintenance-summary th:first-child {
  width: 50%;
  text-align: left;
}

.repository-maintenance-summary td {
  padding: 2px 6px 2px 0;
  color: var(--el-text-color-primary);
  text-align: right;
  white-space: nowrap;
}

.repository-maintenance-summary td:first-child {
  overflow: hidden;
  text-align: left;
  text-overflow: ellipsis;
}

.repository-maintenance-summary__status {
  display: inline-flex;
  align-items: center;
  min-height: 18px;
  padding: 0 6px;
  border: 1px solid var(--el-border-color);
  border-radius: 9px;
  line-height: 16px;
}

.repository-maintenance-summary__status--completed {
  border-color: var(--el-color-success-light-5);
  color: var(--el-color-success);
  background: var(--el-color-success-light-9);
}

.repository-maintenance-summary__status--not_run {
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-blank);
}

.repository-maintenance-summary__stage-detail:empty {
  display: none;
}

.repository-maintenance-summary__stage-detail {
  margin: 0;
  color: var(--el-text-color-secondary);
}

.repository-maintenance-summary__row--success td:first-child {
  color: var(--el-color-success);
}

.repository-maintenance-summary__row--warning td:first-child {
  color: var(--el-color-warning);
}

.repository-maintenance-summary__note {
  margin: 0;
  color: var(--el-text-color-secondary);
}

@container (max-width: 359px) {
  .repository-maintenance-summary thead {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  .repository-maintenance-summary tbody,
  .repository-maintenance-summary tr,
  .repository-maintenance-summary td {
    display: block;
  }

  .repository-maintenance-summary tr {
    padding: 3px 0;
  }

  .repository-maintenance-summary td {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 6px;
    padding-right: 0;
    white-space: normal;
  }

  .repository-maintenance-summary td:first-child {
    display: block;
    padding-bottom: 1px;
    font-weight: 500;
  }

  .repository-maintenance-summary td:not(:first-child)::before {
    content: attr(data-label);
    overflow: hidden;
    color: var(--el-text-color-secondary);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .repository-maintenance-summary td > span {
    white-space: nowrap;
  }
}
</style>
