<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  capacityLabel,
  formatEtaSeconds,
  formatSpeedBps,
  resolveDisplayPercent,
  shouldShowMetrics,
  shouldShowPercentMetric,
  type KopiaProgressPayload,
} from '../../../lib/kopiaProgress'

const props = withDefaults(defineProps<{
  progress?: KopiaProgressPayload | null
  compact?: boolean
  failed?: boolean
}>(), {
  progress: null,
  compact: false,
  failed: false,
})

const aggregate = computed(() => props.progress?.aggregate ?? null)
const orchestrationLabel = computed(() => String(props.progress?.orchestration_label || '').trim())
const estimatingStartedAt = ref<number | null>(null)

watch(
  () => String(props.progress?.orchestration_phase || '').toLowerCase(),
  (phase, previous) => {
    if (phase === 'estimating' && previous !== 'estimating') {
      estimatingStartedAt.value = Date.now()
      return
    }
    if (phase !== 'estimating') {
      estimatingStartedAt.value = null
    }
  },
  { immediate: true },
)

const percent = computed(() => resolveDisplayPercent(props.progress, {
  estimatingStartedAt: estimatingStartedAt.value,
}))
const capacity = computed(() => capacityLabel(aggregate.value))
const speed = computed(() => formatSpeedBps(aggregate.value?.upload_speed_bps))
const eta = computed(() => formatEtaSeconds(aggregate.value?.eta_seconds))
const showMetrics = computed(() => !props.compact && (shouldShowMetrics(props.progress) || props.failed))
const showPercentMetric = computed(() => shouldShowPercentMetric(props.progress))
</script>

<template>
  <div
    class="kopia-transfer-progress"
    :class="{ 'is-compact': compact, 'is-failed': failed }"
  >
    <p
      v-if="orchestrationLabel"
      class="kopia-transfer-progress__label"
    >
      <span
        v-if="!failed"
        class="kopia-transfer-progress__spinner"
        aria-hidden="true"
      />
      {{ orchestrationLabel }}
    </p>
    <el-progress
      class="protection-flow-progress kopia-transfer-progress__bar"
      :percentage="percent"
      :status="failed ? 'exception' : undefined"
      :stroke-width="compact ? 7 : 8"
      :show-text="compact"
    />
    <p
      v-if="showMetrics"
      class="kopia-transfer-progress__metrics"
    >
      <span v-if="capacity">{{ capacity }}</span>
      <span
        v-else
        class="hfl-empty-mark"
      >—</span>
      <span v-if="showPercentMetric">{{ percent }}%</span>
      <span :class="{ 'hfl-empty-mark': !speed }">{{ speed || '—' }}</span>
      <span :class="{ 'hfl-empty-mark': !eta }">{{ eta || '—' }}</span>
    </p>
  </div>
</template>

<style scoped>
.kopia-transfer-progress__label {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.kopia-transfer-progress__spinner {
  width: 10px;
  height: 10px;
  border: 2px solid rgba(64, 158, 255, 0.25);
  border-top-color: var(--el-color-primary);
  border-radius: 50%;
  animation: kopia-transfer-spin 0.8s linear infinite;
  flex-shrink: 0;
}

.kopia-transfer-progress__metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--el-text-color-regular);
}

.kopia-transfer-progress.is-compact .kopia-transfer-progress__label,
.kopia-transfer-progress.is-compact .kopia-transfer-progress__metrics {
  display: none;
}

@keyframes kopia-transfer-spin {
  to { transform: rotate(360deg); }
}
</style>
