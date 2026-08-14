<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { resolveErrorMessage } from '../../lib/errors'

const props = withDefaults(
  defineProps<{
    error: unknown
    title?: string
    fallback?: string
    showRetry?: boolean
    traceId?: string
  }>(),
  {
    title: '',
    fallback: '',
    showRetry: true,
    traceId: '',
  },
)

const emit = defineEmits<{
  retry: []
}>()

const { t } = useI18n()

const message = computed(() =>
  resolveErrorMessage(props.error, t, props.fallback || t('errors.generic.requestFailed')),
)

const heading = computed(() => props.title || t('errors.generic.loadFailed'))
</script>

<template>
  <div class="hfl-error-state">
    <div
      class="hfl-error-state__icon"
      aria-hidden="true"
    >
      <svg
        width="22"
        height="22"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
      >
        <circle
          cx="12"
          cy="12"
          r="10"
        />
        <line
          x1="12"
          y1="8"
          x2="12"
          y2="12"
        />
        <line
          x1="12"
          y1="16"
          x2="12.01"
          y2="16"
        />
      </svg>
    </div>
    <h3 class="hfl-error-state__title">
      {{ heading }}
    </h3>
    <p class="hfl-error-state__message">
      {{ message }}
    </p>
    <button
      v-if="showRetry"
      type="button"
      class="hfl-error-state__retry"
      @click="emit('retry')"
    >
      {{ t('common.retry') }}
    </button>
    <p
      v-if="traceId"
      class="hfl-error-state__trace"
    >
      {{ t('errors.generic.traceId') }}: {{ traceId }}
    </p>
  </div>
</template>

<style scoped>
.hfl-error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: 10px;
  padding: 40px 24px;
  min-height: 220px;
}

.hfl-error-state__icon {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-error-light);
  color: var(--color-error);
}

.hfl-error-state__title {
  margin: 0;
  font-size: 15px;
  font-weight: 500;
  color: rgb(38, 38, 38);
}

.hfl-error-state__message {
  margin: 0;
  max-width: 420px;
  font-size: 13px;
  line-height: 1.6;
  color: rgb(89, 89, 89);
}

.hfl-error-state__retry {
  margin-top: 4px;
  padding: 6px 16px;
  border: none;
  border-radius: 6px;
  background: #5c5cff;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}

.hfl-error-state__retry:hover {
  opacity: 0.92;
}

.hfl-error-state__trace {
  margin: 4px 0 0;
  font-size: 11px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: rgb(191, 191, 191);
}
</style>
