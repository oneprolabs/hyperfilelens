<script setup lang="ts">
import { ref } from 'vue'
import { KeyRound, RotateCcw } from 'lucide-vue-next'

import TurnstileWidget from '../TurnstileWidget.vue'

defineProps<{
  ready: boolean
  blocked: boolean
  verified: boolean
  siteKey: string
  action: string
  blockedMessage: string
  retryLabel: string
  manualRetryLabel: string
  errorCodeLabel?: string
  errorMessage?: string
}>()

const emit = defineEmits<{
  retry: []
  success: [token: string]
  expire: []
  invalidate: []
  error: [errorCode?: string]
  'load-failed': []
}>()

const turnstileWidgetRef = ref<InstanceType<typeof TurnstileWidget> | null>(null)
const showManualRetry = ref(false)

function hideManualRetry() {
  showManualRetry.value = false
}

function retry() {
  hideManualRetry()
  emit('retry')
}

function onSuccess(token: string) {
  hideManualRetry()
  emit('success', token)
}

function onExpire() {
  hideManualRetry()
  emit('expire')
}

function onInvalidate() {
  hideManualRetry()
  emit('invalidate')
}

function onError(errorCode?: string) {
  hideManualRetry()
  emit('error', errorCode)
}

function onLoadFailed() {
  hideManualRetry()
  emit('load-failed')
}

function reset() {
  turnstileWidgetRef.value?.reset()
}

defineExpose({ reset })
</script>

<template>
  <div
    v-if="ready || blocked"
    class="auth-turnstile-field"
  >
    <div
      v-if="ready && siteKey"
      class="auth-turnstile-field__control auth-turnstile-field__widget"
    >
      <div class="auth-turnstile-field__viewport">
        <TurnstileWidget
          ref="turnstileWidgetRef"
          :site-key="siteKey"
          :action="action"
          theme="dark"
          size="flexible"
          @success="onSuccess"
          @expire="onExpire"
          @invalidate="onInvalidate"
          @error="onError"
          @load-failed="onLoadFailed"
          @slow-load="showManualRetry = true"
          @rendered="hideManualRetry"
        />
      </div>
    </div>

    <button
      v-if="ready && siteKey && !verified && showManualRetry"
      type="button"
      class="auth-turnstile-field__manual-retry"
      @click="retry"
    >
      <RotateCcw
        class="auth-turnstile-field__manual-retry-icon"
        :size="14"
        aria-hidden="true"
      />
      <span class="auth-turnstile-field__manual-retry-label">{{ manualRetryLabel }}</span>
    </button>

    <div
      v-if="blocked"
      class="auth-turnstile-field__control auth-turnstile-field__blocked"
      role="alert"
    >
      <KeyRound
        :size="18"
        aria-hidden="true"
      />
      <span class="auth-turnstile-field__blocked-text">
        <span>{{ blockedMessage }}</span>
        <span
          v-if="errorCodeLabel"
          class="auth-turnstile-field__reference-code"
        >
          {{ errorCodeLabel }}
        </span>
      </span>
      <button
        type="button"
        class="auth-turnstile-field__retry"
        @click="retry"
      >
        {{ retryLabel }}
      </button>
    </div>

    <p
      v-if="errorMessage && !blocked"
      class="auth-turnstile-field__error"
      role="alert"
    >
      {{ errorMessage }}
    </p>
  </div>
</template>

<style scoped>
.auth-turnstile-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.auth-turnstile-field__control {
  box-sizing: border-box;
  width: 100%;
  min-height: 65px;
  display: flex;
  align-items: center;
}

.auth-turnstile-field__blocked {
  gap: 10px;
  padding: 0 14px;
  background: #313131;
  border: 1px solid #3a3b40;
  border-radius: var(--radius-card);
  overflow: hidden;
  color: #d4d7dd;
  font-size: 13px;
  line-height: 1.35;
}

.auth-turnstile-field__widget {
  justify-content: center;
  min-height: 65px;
  padding: 0;
  background: transparent;
  border: 0;
  border-radius: 0;
  overflow: visible;
}

.auth-turnstile-field__viewport {
  box-sizing: border-box;
  width: 100%;
  height: 65px;
  border: 1px solid #3a3b40;
  border-radius: var(--radius-card);
  overflow: hidden;
}

.auth-turnstile-field__viewport :deep(.turnstile-widget) {
  width: calc(100% + 2px);
  min-height: 65px;
  margin: -1px;
}

.auth-turnstile-field__viewport :deep(.turnstile-widget__loading) {
  border: 0;
  border-radius: 0;
}

.auth-turnstile-field__blocked-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  white-space: nowrap;
}

.auth-turnstile-field__reference-code {
  color: var(--color-text-secondary, #a0a0a8);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  line-height: 1.25;
}

.auth-turnstile-field__retry {
  min-width: 56px;
  min-height: 32px;
  padding: 0 12px;
  flex: 0 0 auto;
  color: var(--color-brand-violet-soft);
  background: color-mix(in srgb, var(--color-primary) 8%, transparent);
  border: 1px solid var(--color-primary);
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}

.auth-turnstile-field__retry:hover {
  color: #fff;
  background: color-mix(in srgb, var(--color-primary) 16%, transparent);
  border-color: var(--color-brand-violet-soft);
}

.auth-turnstile-field__retry:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
}

.auth-turnstile-field__manual-retry {
  box-sizing: border-box;
  width: 100%;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  color: var(--color-brand-violet-soft);
  background: color-mix(in srgb, var(--color-primary) 6%, transparent);
  border: 1px solid color-mix(in srgb, var(--color-primary) 22%, transparent);
  border-radius: var(--radius-card);
  font-size: 12px;
  line-height: 1.4;
  text-align: center;
  cursor: pointer;
  transition:
    color 160ms ease-out,
    background-color 160ms ease-out,
    border-color 160ms ease-out;
}

.auth-turnstile-field__manual-retry:hover {
  color: #fff;
  background: color-mix(in srgb, var(--color-primary) 12%, transparent);
  border-color: color-mix(in srgb, var(--color-primary) 48%, transparent);
}

.auth-turnstile-field__manual-retry:active {
  background: color-mix(in srgb, var(--color-primary) 18%, transparent);
}

.auth-turnstile-field__manual-retry:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
}

.auth-turnstile-field__manual-retry-icon {
  flex: 0 0 auto;
}

.auth-turnstile-field__error {
  margin: 0;
  color: var(--color-error);
  font-size: 12px;
  line-height: 1.4;
}

@media (max-width: 479.98px) {
  .auth-turnstile-field__blocked-text {
    white-space: normal;
  }

  .auth-turnstile-field__retry {
    min-height: 44px;
  }
}
</style>
