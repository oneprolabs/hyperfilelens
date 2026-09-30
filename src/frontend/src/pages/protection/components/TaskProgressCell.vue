<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  formatTaskProgressBarPercent,
  formatTaskProgressPercent,
  formatSpeedBps,
  resolveStep3DisplayPercent,
  shouldShowStep3Percent,
  shouldShowTransferMetrics,
  transferCapacityText,
  transferPhaseElapsedText,
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

const displayPercent = computed(() => {
  const transfer = props.transferProgress
  if (showBackupBar.value) {
    return resolveStep3DisplayPercent(transfer, 0)
  }
  return 0
})
const barPercent = computed(() => formatTaskProgressBarPercent(displayPercent.value))
const progressText = computed(() => formatTaskProgressPercent(displayPercent.value))
const isRestore = computed(() => String(props.transferProgress?.label_key || '').includes('taskProgress.restore.'))
const backupTransferStage = computed(() => {
  const phase = String(props.transferProgress?.phase || '').toLowerCase()
  const label = String(props.transferProgress?.label_key || '')
  const preparing = /taskProgress\.backup\.(preparingLogic|preparing|dispatching|estimating)$/.test(label)
  return !preparing && ['transferring', 'finalizing', 'done'].includes(phase)
})
const preparingWithBytes = computed(() => {
  const transfer = props.transferProgress
  return transfer?.label_key === 'protection.taskProgress.backup.preparing'
    && ['preparing', 'transferring'].includes(String(transfer.phase || '').toLowerCase())
    && Number(transfer.processed_bytes ?? transfer.bytes_done ?? 0) > 0
})
const showBackupBar = computed(() => {
  const transfer = props.transferProgress
  if (isRestore.value || !transfer) return false
  const state = String(transfer.execution_state || '').toLowerCase()
  if (['reconnecting', 'offline_pending', 'offline_stale'].includes(state)) return false
  const phase = String(transfer.phase || '').toLowerCase()
  return (backupTransferStage.value || phase === 'estimating' || preparingWithBytes.value)
    && shouldShowStep3Percent(transfer)
})
const comparisonLabel = computed(() => {
  const transfer = props.transferProgress
  const state = String(transfer?.execution_state || '').toLowerCase()
  if (props.stopping || props.failed || ['reconnecting', 'offline_pending', 'offline_stale'].includes(state)) return ''
  const comparison = transfer?.comparison
  if (!comparison?.label_key) return ''
  const label = t(comparison.label_key, comparison.label_args || {})
  const elapsed = props.compact ? '' : transferPhaseElapsedText(t, comparison.phase_elapsed_seconds)
  return [label, elapsed].filter(Boolean).join(' · ')
})
const orchestrationLabel = computed(() => {
  if (props.stopping) {
    const key = String(props.transferProgress?.label_key || '').trim()
    if (key.includes('restore')) return t('protection.taskProgress.stopping.restore')
    return t('protection.taskProgress.stopping.backup')
  }
  if (isRestore.value && String(props.transferProgress?.phase || '').toLowerCase() === 'transferring') {
    return t('protection.taskProgress.restore.running')
  }
  return comparisonLabel.value || transferProgressLabel(t, props.transferProgress)
})
const phaseElapsedText = computed(() => {
  if (props.compact || isRestore.value || props.stopping || comparisonLabel.value) return ''
  const phase = String(props.transferProgress?.phase || '').toLowerCase()
  if (!['estimating', 'transferring', 'finalizing'].includes(phase)) return ''
  if (!String(props.transferProgress?.label_key || '').includes('taskProgress.backup.')) return ''
  return transferPhaseElapsedText(t, props.transferProgress?.phase_elapsed_seconds) || ''
})
const displayOrchestrationLabel = computed(() => [orchestrationLabel.value, phaseElapsedText.value]
  .filter(Boolean)
  .join(' · '))
const showSpinner = computed(() => {
  if (props.stopping) return false
  if (props.failed) return false
  const state = String(props.transferProgress?.execution_state || '').trim().toLowerCase()
  return state !== 'reconnecting' && state !== 'offline_pending' && state !== 'offline_stale'
})
const metricParts = computed(() => {
  if (props.compact) return []
  const transfer = props.transferProgress
  if (!transfer) return []
  const state = String(transfer.execution_state || '').toLowerCase()
  if (['reconnecting', 'offline_pending', 'offline_stale'].includes(state)) return []
  const comparingWithBytes = comparisonLabel.value && Number(transfer.processed_bytes ?? transfer.bytes_done ?? 0) > 0
  if (!isRestore.value && !backupTransferStage.value && !comparingWithBytes && !preparingWithBytes.value) return []
  const finalizingBackup = !isRestore.value && transfer?.phase === 'finalizing'
    && !['reconnecting', 'offline_pending'].includes(String(transfer.execution_state || '').toLowerCase())
  if (!shouldShowTransferMetrics(transfer) && !props.failed && !finalizingBackup && !comparingWithBytes && !preparingWithBytes.value) return []
  return transferMetricParts(t, props.transferProgress)
})
const metricLine = computed(() => metricParts.value.join(' · '))
const restoreMetricLine = computed(() => {
  if (!isRestore.value || props.compact) return ''
  const capacity = transferCapacityText(t, props.transferProgress)
  const speed = formatSpeedBps(props.transferProgress?.upload_speed_bps)
  return [capacity, speed].filter(Boolean).join(' · ')
})
const overflowTitle = computed(() => {
  if (props.compact) return ''

  const label = isRestore.value
    ? transferProgressLabel(t, props.transferProgress)
    : displayOrchestrationLabel.value
  const percent = showBackupBar.value
    ? progressText.value
    : ''
  const metrics = isRestore.value
    ? transferMetricParts(t, props.transferProgress, {
      labelProcessingSpeed: true,
      labelRestoreMetrics: true,
    })
    : metricParts.value

  return [label, percent, ...metrics].filter(Boolean).join('\n')
})
</script>

<template>
  <div
    class="task-progress-cell"
    :class="{ 'is-compact': compact, 'is-failed': failed, 'is-stopping': stopping }"
    data-table-overflow-explicit-only
    :data-table-overflow-title="overflowTitle || undefined"
    :data-table-overflow-title-always="!compact && overflowTitle ? '' : undefined"
  >
    <div
      v-if="displayOrchestrationLabel"
      class="task-progress-cell__row1"
      :class="{ 'is-last': !showBackupBar && !(isRestore ? restoreMetricLine : metricLine) }"
    >
      <p
        v-if="displayOrchestrationLabel"
        class="task-progress-cell__label"
      >
        <span
          v-if="showSpinner"
          class="task-progress-cell__spinner"
          aria-hidden="true"
        />
        <span
          class="task-progress-cell__label-text"
        >{{ displayOrchestrationLabel }}</span>
      </p>
    </div>
    <div
      v-if="showBackupBar"
      class="task-progress-cell__bar-row"
    >
      <el-progress
        class="protection-flow-progress task-progress-cell__bar"
        :percentage="barPercent"
        :status="failed ? 'exception' : stopping ? 'warning' : undefined"
        :stroke-width="compact ? 7 : 8"
        :show-text="false"
      />
      <span
        v-if="!compact"
        class="task-progress-cell__percent"
      >{{ progressText }}</span>
    </div>
    <p
      v-if="isRestore ? restoreMetricLine : metricLine"
      class="task-progress-cell__metrics"
    >
      <span
        class="task-progress-cell__metric-line"
      >{{ isRestore ? restoreMetricLine : metricLine }}</span>
    </p>
  </div>
</template>

<style scoped>
.task-progress-cell {
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

.task-progress-cell__row1.is-last {
  margin-bottom: 0;
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

.task-progress-cell__bar-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.task-progress-cell__bar {
  flex: 1;
  min-width: 0;
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
