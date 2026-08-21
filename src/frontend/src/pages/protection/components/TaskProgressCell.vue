<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  formatTaskProgressBarPercent,
  formatTaskProgressPercent,
  parseTaskProgressValue,
  resolveStep3DisplayPercent,
  shouldShowStep3Percent,
  shouldShowTransferMetrics,
  transferProgressLabel,
  transferMetricParts,
  type TransferProgress,
} from '../../../lib/kopiaProgress'

const props = withDefaults(defineProps<{
  progress?: number | string | null
  transferProgress?: TransferProgress | null
  compact?: boolean
  failed?: boolean
  stopping?: boolean
}>(), {
  progress: 0,
  transferProgress: null,
  compact: false,
  failed: false,
  stopping: false,
})

const { t } = useI18n()

const taskProgressValue = computed(() => {
  const taskProgress = Number(props.progress)
  if (!Number.isFinite(taskProgress) || taskProgress <= 0) return null
  return parseTaskProgressValue(taskProgress)
})
const displayPercent = computed(() => {
  const transfer = props.transferProgress
  const step3 = Number(transfer?.step3_display_percent)
  if (props.stopping && taskProgressValue.value != null) {
    return taskProgressValue.value
  }
  if (props.stopping && Number.isFinite(step3)) {
    return parseTaskProgressValue(step3)
  }
  if (shouldShowStep3Percent(transfer)) {
    return resolveStep3DisplayPercent(transfer, 0)
  }
  return 0
})
const barPercent = computed(() => formatTaskProgressBarPercent(displayPercent.value))
const progressText = computed(() => formatTaskProgressPercent(displayPercent.value))
const showRightPercent = computed(() => {
  if (props.stopping && taskProgressValue.value != null) return true
  return shouldShowStep3Percent(props.transferProgress)
})
const orchestrationLabel = computed(() => {
  if (props.stopping) {
    const key = String(props.transferProgress?.label_key || '').trim()
    if (key.includes('restore')) return t('protection.taskProgress.stopping.restore')
    return t('protection.taskProgress.stopping.backup')
  }
  return transferProgressLabel(t, props.transferProgress)
})
const showSpinner = computed(() => {
  if (props.stopping) return false
  if (props.failed) return false
  const state = String(props.transferProgress?.execution_state || '').trim().toLowerCase()
  return state !== 'reconnecting' && state !== 'offline_pending' && state !== 'offline_stale'
})
const metricParts = computed(() => {
  if (props.compact) return []
  if (!shouldShowTransferMetrics(props.transferProgress) && !props.failed) return []
  return transferMetricParts(t, props.transferProgress)
})
const metricLine = computed(() => metricParts.value.join(' · '))
const overflowTitle = computed(() => [orchestrationLabel.value, ...metricParts.value].filter(Boolean).join('\n'))
const labelTitle = computed(() => {
  const label = orchestrationLabel.value
  return label.length > 48 ? label : undefined
})
</script>

<template>
  <div
    class="task-progress-cell"
    :class="{ 'is-compact': compact, 'is-failed': failed, 'is-stopping': stopping }"
  >
    <div
      v-if="orchestrationLabel || showRightPercent"
      class="task-progress-cell__row1"
    >
      <p
        v-if="orchestrationLabel"
        class="task-progress-cell__label"
        :title="labelTitle"
      >
        <span
          v-if="showSpinner"
          class="task-progress-cell__spinner"
          aria-hidden="true"
        />
        <span
          class="task-progress-cell__label-text"
          :data-table-overflow-title="overflowTitle || undefined"
        >{{ orchestrationLabel }}</span>
      </p>
      <span
        v-if="showRightPercent"
        class="task-progress-cell__percent"
      >{{ progressText }}</span>
    </div>
    <el-progress
      class="protection-flow-progress task-progress-cell__bar"
      :percentage="barPercent"
      :status="failed ? 'exception' : stopping ? 'warning' : undefined"
      :stroke-width="compact ? 7 : 8"
      :show-text="false"
    />
    <p
      class="task-progress-cell__metrics"
      :class="{ 'is-empty': !metricParts.length }"
    >
      <span
        v-if="metricLine"
        class="task-progress-cell__metric-line"
        :data-table-overflow-title="overflowTitle || undefined"
      >{{ metricLine }}</span>
    </p>
  </div>
</template>

<style scoped>
.task-progress-cell {
  min-height: 76px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.task-progress-cell__row1 {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0 0 4px;
  min-height: 18px;
}

.task-progress-cell__label {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  min-width: 0;
  flex: 1;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.task-progress-cell__label-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-progress-cell__percent {
  flex-shrink: 0;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-regular);
}

.task-progress-cell__spinner {
  width: 10px;
  height: 10px;
  border: 2px solid rgba(64, 158, 255, 0.25);
  border-top-color: var(--el-color-primary);
  border-radius: 50%;
  animation: task-progress-spin 0.8s linear infinite;
  flex-shrink: 0;
}

.task-progress-cell__bar {
  width: 100%;
  min-height: 8px;
  margin: 0;
}

.task-progress-cell__bar :deep(.el-progress) {
  width: 100%;
}

.task-progress-cell__bar :deep(.el-progress-bar) {
  width: 100%;
}

.task-progress-cell__metrics {
  display: block;
  margin: 4px 0 0;
  min-height: 18px;
  font-size: 12px;
  color: var(--el-text-color-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-progress-cell__metrics.is-empty {
  visibility: hidden;
}

.task-progress-cell__metric-line {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-progress-cell.is-compact {
  min-height: auto;
}

.task-progress-cell.is-compact .task-progress-cell__row1,
.task-progress-cell.is-compact .task-progress-cell__metrics {
  display: none;
}

.task-progress-cell.is-stopping .task-progress-cell__label {
  color: var(--el-color-warning);
}

@keyframes task-progress-spin {
  to { transform: rotate(360deg); }
}
</style>
