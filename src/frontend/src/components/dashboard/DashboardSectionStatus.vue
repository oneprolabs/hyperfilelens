<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { DashboardSectionState } from '../../lib/dashboardApi'

withDefaults(defineProps<{
  state: DashboardSectionState
  variant?: 'chip' | 'metric' | 'list' | 'chart'
  retrying?: boolean
}>(), { variant: 'list', retrying: false })
defineEmits<{ retry: [] }>()
const { t } = useI18n()
</script>

<template>
  <div v-if="state !== 'ready'" class="dashboard-section-status">
    <template v-if="state === 'loading' && !retrying">
      <span class="sr-only" role="status">{{ t('common.loading') }}</span>
      <div class="section-skeleton" :class="`section-skeleton--${variant}`" aria-hidden="true">
        <span v-for="slot in variant === 'chip' ? 1 : variant === 'chart' ? 7 : 3" :key="slot" />
      </div>
    </template>
    <div v-else class="section-feedback">
      <span :role="retrying ? undefined : 'status'">
        {{ t(state === 'forbidden' ? 'errors.codes.authForbidden' : 'errors.generic.loadFailed') }}
      </span>
      <span v-if="retrying" class="sr-only" role="status">{{ t('common.loading') }}</span>
      <ElButton
        v-if="state === 'unavailable' || retrying"
        text
        size="small"
        :loading="retrying"
        :disabled="retrying"
        @click="$emit('retry')"
      >
        {{ t('common.retry') }}
      </ElButton>
    </div>
  </div>
</template>

<style scoped>
.dashboard-section-status:has(.section-skeleton--chart) {
  width: 100%;
}

.section-feedback {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  min-height: 2rem;
  padding-inline: 0.75rem;
  font-size: 0.875rem;
}

.section-skeleton {
  display: grid;
  gap: 0.75rem;
  min-width: 0;
  color: var(--color-text-primary, #1d2129);
}

.section-skeleton span {
  display: block;
  height: 0.75rem;
  border-radius: 0.375rem;
  background: currentColor;
  opacity: 0.15;
}

@media (prefers-reduced-motion: no-preference) {
  .section-skeleton {
    animation: section-skeleton-pulse 1.6s ease-in-out infinite;
  }
}

@keyframes section-skeleton-pulse {
  50% {
    opacity: 0.55;
  }
}

.section-skeleton--chip span {
  width: 3.5rem;
}

.section-skeleton--metric {
  min-height: 6.5rem;
  align-content: space-between;
}

.section-skeleton--metric span:first-child {
  width: 3.5rem;
  height: 1.5rem;
}

.section-skeleton--metric span:last-child {
  width: 70%;
}

.section-skeleton--list {
  min-height: 9rem;
  padding-block: 0.75rem;
}

.section-skeleton--list span {
  height: 2rem;
}

.section-skeleton--list span:nth-child(2) {
  width: 88%;
}

.section-skeleton--chart {
  grid-template-columns: repeat(7, minmax(0, 1fr));
  align-items: end;
  width: 100%;
  height: 9rem;
  padding-inline: 0.75rem;
}

.section-skeleton--chart span {
  height: 60%;
}

.section-skeleton--chart span:nth-child(3n) {
  height: 42%;
}

.section-skeleton--chart span:nth-child(3n + 1) {
  height: 76%;
}
</style>
