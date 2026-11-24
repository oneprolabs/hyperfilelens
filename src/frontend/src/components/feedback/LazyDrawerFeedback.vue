<script setup lang="ts">
import { ElButton } from 'element-plus'
import { LoaderCircle } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'

defineProps<{
  failed: boolean
  layout?: 'task'
}>()

const emit = defineEmits<{ retry: [] }>()
const { t } = useI18n()
</script>

<template>
  <div
    v-if="failed"
    class="lazy-drawer-feedback"
    role="alert"
  >
    <p>{{ t('errors.pageLoad.loadFailed.message') }}</p>
    <ElButton @click="emit('retry')">
      {{ t('common.retry') }}
    </ElButton>
  </div>
  <div
    v-else
    class="lazy-drawer-loading"
    :class="{ 'lazy-drawer-loading--task': layout === 'task' }"
    role="status"
    aria-live="polite"
  >
    <span v-if="layout === 'task'" class="sr-only">{{ t('common.loading') }}</span>
    <div v-else class="lazy-drawer-loading__status">
      <LoaderCircle
        aria-hidden="true"
        class="size-4 motion-safe:animate-spin"
      />
      <span>{{ t('common.loading') }}</span>
    </div>
    <div
      v-if="layout === 'task'"
      class="lazy-drawer-loading__sections"
      aria-hidden="true"
    >
      <div class="lazy-drawer-loading__task-summary">
        <div class="lazy-drawer-loading__heading" />
        <div class="lazy-drawer-loading__metrics">
          <div
            v-for="metric in 2"
            :key="metric"
            class="lazy-drawer-loading__metric"
          >
            <div class="lazy-drawer-loading__metric-icon" />
            <div class="lazy-drawer-loading__metric-copy">
              <div class="lazy-drawer-loading__label" />
              <div class="lazy-drawer-loading__value" />
            </div>
          </div>
        </div>
      </div>
      <div class="lazy-drawer-loading__task-tabs">
        <div class="lazy-drawer-loading__label" />
        <div class="lazy-drawer-loading__label" />
      </div>
      <div class="lazy-drawer-loading__task-rows">
        <div
          v-for="row in 2"
          :key="row"
          class="lazy-drawer-loading__task-row"
        >
          <div class="lazy-drawer-loading__value" />
          <div class="lazy-drawer-loading__label" />
        </div>
      </div>
    </div>
    <div
      v-else
      class="lazy-drawer-loading__sections"
      aria-hidden="true"
    >
      <div
        v-for="rowCount in [3, 2]"
        :key="rowCount"
        class="lazy-drawer-loading__section"
      >
        <div class="lazy-drawer-loading__heading" />
        <div
          v-for="row in rowCount"
          :key="row"
          class="lazy-drawer-loading__row"
        >
          <div class="lazy-drawer-loading__label" />
          <div class="lazy-drawer-loading__value" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lazy-drawer-feedback {
  display: grid;
  min-height: 50%;
  place-content: center;
  justify-items: center;
  gap: 0.75rem;
  text-align: center;
  color: var(--el-text-color-secondary);
}

.lazy-drawer-loading {
  display: grid;
  gap: 0.875rem;
  padding: clamp(0.875rem, 3vw, 1.5rem);
}

.lazy-drawer-loading__status {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: var(--el-text-color-secondary);
  font-size: 0.8125rem;
}

.lazy-drawer-loading__status svg {
  flex: none;
  color: var(--el-color-primary);
}

.lazy-drawer-loading__sections {
  display: grid;
  gap: 0.75rem;
}

.lazy-drawer-loading__section {
  display: grid;
  gap: 0.875rem;
  padding: clamp(0.875rem, 2.5vw, 1.25rem);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--el-border-radius-base);
  background: var(--el-bg-color);
}

.lazy-drawer-loading__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr);
  align-items: center;
  gap: 0.75rem;
}

.lazy-drawer-loading__heading,
.lazy-drawer-loading__label,
.lazy-drawer-loading__value {
  height: 0.625rem;
  border-radius: var(--el-border-radius-round);
  background: var(--el-fill-color);
}

.lazy-drawer-loading__heading {
  width: min(8rem, 52%);
  height: 0.875rem;
  margin-bottom: 0.25rem;
  background: var(--el-fill-color-darker);
}

.lazy-drawer-loading__label {
  width: 78%;
}

.lazy-drawer-loading__value {
  width: 86%;
}

.lazy-drawer-loading__row:nth-child(odd) .lazy-drawer-loading__value {
  width: 62%;
}

.lazy-drawer-loading--task {
  padding: 1rem;
}

.lazy-drawer-loading__task-summary {
  display: grid;
  gap: 0.625rem;
  padding: 0.75rem;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--el-border-radius-base);
  background: var(--el-fill-color-extra-light);
}

.lazy-drawer-loading__task-summary .lazy-drawer-loading__heading {
  width: 4.5rem;
  height: 0.625rem;
  margin: 0;
}

.lazy-drawer-loading__metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 12rem), 1fr));
  gap: 0.5rem;
}

.lazy-drawer-loading__metric {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  min-width: 0;
  padding: 0.625rem;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--el-border-radius-base);
  background: var(--el-bg-color);
}

.lazy-drawer-loading__metric-icon {
  flex: 0 0 1.625rem;
  height: 1.625rem;
  border-radius: var(--el-border-radius-base);
  background: var(--el-color-primary-light-9);
}

.lazy-drawer-loading__metric-copy {
  display: grid;
  flex: 1;
  gap: 0.375rem;
}

.lazy-drawer-loading__metric-copy .lazy-drawer-loading__label {
  width: min(4rem, 64%);
}

.lazy-drawer-loading__metric-copy .lazy-drawer-loading__value {
  width: min(7rem, 90%);
}

.lazy-drawer-loading__task-tabs {
  display: flex;
  gap: 1rem;
  padding: 0.5rem 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.lazy-drawer-loading__task-tabs .lazy-drawer-loading__label {
  width: 3.5rem;
}

.lazy-drawer-loading__task-rows {
  display: grid;
  gap: 0.375rem;
}

.lazy-drawer-loading__task-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 0.625rem;
  border-radius: var(--el-border-radius-base);
  background: var(--el-fill-color-extra-light);
}

.lazy-drawer-loading__task-row .lazy-drawer-loading__value {
  width: min(8rem, 52%);
}

.lazy-drawer-loading__task-row:nth-child(2) .lazy-drawer-loading__value {
  width: min(6rem, 40%);
}

.lazy-drawer-loading__task-row .lazy-drawer-loading__label {
  width: min(3rem, 25%);
}

@media (prefers-reduced-motion: no-preference) {
  .lazy-drawer-loading__task-summary,
  .lazy-drawer-loading__task-row,
  .lazy-drawer-loading__task-tabs .lazy-drawer-loading__label {
    position: relative;
    overflow: hidden;
  }

  .lazy-drawer-loading__task-summary::after,
  .lazy-drawer-loading__task-row::after,
  .lazy-drawer-loading__task-tabs .lazy-drawer-loading__label::after {
    content: '';
    position: absolute;
    inset: 0;
    pointer-events: none;
    transform: translateX(-100%);
    background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--el-color-primary) 10%, transparent), transparent);
    animation: lazy-drawer-skeleton-shimmer 1.15s ease-in-out infinite;
  }
}

@keyframes lazy-drawer-skeleton-shimmer {
  to {
    transform: translateX(100%);
  }
}
</style>
