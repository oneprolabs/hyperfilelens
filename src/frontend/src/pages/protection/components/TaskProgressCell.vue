<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  formatSpeedBps,
  shouldShowTransferMetrics,
  transferCapacityText,
  transferProgressLabel,
  transferMetricParts,
  type TransferProgress,
} from '../../../lib/kopiaProgress'

const props = withDefaults(defineProps<{
  transferProgress?: TransferProgress | null
  compact?: boolean
  failed?: boolean
  stopping?: boolean
}>(), {
  transferProgress: null,
  compact: false,
  failed: false,
  stopping: false,
})

const { t } = useI18n()

const isRestore = computed(() => String(props.transferProgress?.label_key || '').includes('taskProgress.restore.'))
const orchestrationLabel = computed(() => {
  if (props.stopping) {
    const key = String(props.transferProgress?.label_key || '').trim()
    if (key.includes('restore')) return t('protection.taskProgress.stopping.restore')
    return t('protection.taskProgress.stopping.backup')
  }
  if (isRestore.value && String(props.transferProgress?.phase || '').toLowerCase() === 'transferring') {
    return t('protection.taskProgress.restore.running')
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
const restoreMetricLine = computed(() => {
  if (!isRestore.value || props.compact) return ''
  const capacity = transferCapacityText(t, props.transferProgress)
  const speed = formatSpeedBps(props.transferProgress?.upload_speed_bps)
  return [capacity, speed].filter(Boolean).join(' · ')
})
const overflowTitle = computed(() => {
  if (!metricParts.value.length) return ''
  const metrics = transferMetricParts(t, props.transferProgress, { labelProcessingSpeed: true, labelRestoreMetrics: isRestore.value })
    .filter(Boolean)
  if (!isRestore.value) return metrics.join('\n')
  return [transferProgressLabel(t, props.transferProgress), ...metrics].filter(Boolean).join('\n')
})
</script>

<template>
  <div
    class="task-progress-cell"
    :class="{ 'is-compact': compact, 'is-failed': failed, 'is-stopping': stopping }"
    data-table-overflow-explicit-only
  >
    <div
      v-if="orchestrationLabel"
      class="task-progress-cell__row1"
    >
      <p
        v-if="orchestrationLabel"
        class="task-progress-cell__label"
      >
        <span
          v-if="showSpinner"
          class="task-progress-cell__spinner"
          aria-hidden="true"
        />
        <span
          class="task-progress-cell__label-text"
          :data-table-overflow-title="isRestore ? overflowTitle || undefined : undefined"
          :data-table-overflow-title-always="isRestore || undefined"
        >{{ orchestrationLabel }}</span>
      </p>
    </div>
    <p
      class="task-progress-cell__metrics"
      :class="{ 'is-empty': !(isRestore ? restoreMetricLine : metricLine) }"
    >
      <span
        v-if="isRestore ? restoreMetricLine : metricLine"
        class="task-progress-cell__metric-line"
        :data-table-overflow-title="isRestore ? undefined : overflowTitle || undefined"
        data-table-overflow-title-always
      >{{ isRestore ? restoreMetricLine : metricLine }}</span>
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

.task-progress-cell__spinner {
  width: 10px;
  height: 10px;
  border: 2px solid rgba(64, 158, 255, 0.25);
  border-top-color: var(--el-color-primary);
  border-radius: 50%;
  animation: task-progress-spin 0.8s linear infinite;
  flex-shrink: 0;
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
