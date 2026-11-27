<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { LogOut, UserRound } from 'lucide-vue-next'
import { impersonationContext, endImpersonation, markImpersonationEnded } from '../lib/impersonation'
const emit = defineEmits<{ height: [value: number] }>()
const element = ref<HTMLElement>()
const documentHeightProperty = '--hfl-impersonation-height'
let previousDocumentHeight = ''
let observer: ResizeObserver | undefined
const { t } = useI18n()
const busy = ref(false)
const error = ref('')
const info = computed(() => impersonationContext.value)
const now = ref(Date.now())
const remainingMs = computed(() => {
  const expiresAt = Date.parse(info.value?.expires_at || '')
  return Number.isFinite(expiresAt) ? Math.max(0, expiresAt - now.value) : null
})
const expiringSoon = computed(() => remainingMs.value !== null && remainingMs.value < 5 * 60_000)
const remainingTime = computed(() => {
  if (remainingMs.value === null) return ''
  return remainingMs.value < 60_000
    ? t('impersonation.lessThanMinuteLeft')
    : t('impersonation.minutesLeft', { minutes: Math.ceil(remainingMs.value / 60_000) })
})
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  previousDocumentHeight = document.documentElement.style.getPropertyValue(documentHeightProperty)
  const measure = () => {
    const height = Math.ceil(element.value?.getBoundingClientRect().height || 0)
    // Fullscreen forms teleport to body and cannot inherit the shell's offsets.
    document.documentElement.style.setProperty(documentHeightProperty, `${height}px`)
    emit('height', height)
  }
  if (typeof ResizeObserver !== 'undefined' && element.value) {
    observer = new ResizeObserver(measure)
    observer.observe(element.value)
  }
  measure()
  timer = setInterval(() => {
    now.value = Date.now()
    if (info.value?.expires_at && Date.parse(info.value.expires_at) <= now.value) {
      markImpersonationEnded()
      window.location.replace('/impersonation/ended')
    }
  }, 1000)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  observer?.disconnect()
  if (previousDocumentHeight) document.documentElement.style.setProperty(documentHeightProperty, previousDocumentHeight)
  else document.documentElement.style.removeProperty(documentHeightProperty)
  emit('height', 0)
})
async function end() {
  busy.value = true
  error.value = ''
  try { await endImpersonation() } catch { error.value = t('impersonation.endFailed') } finally { busy.value = false }
}
</script>
<template>
  <div
    v-if="info"
    ref="element"
    class="impersonation-banner"
    role="region"
    :aria-label="t('impersonation.session')"
  >
    <div class="impersonation-banner__identity">
      <UserRound
        class="impersonation-banner__icon"
        :size="16"
        :stroke-width="1.75"
        aria-hidden="true"
      />
      <span class="impersonation-banner__label">{{ t('impersonation.actingAsLabel') }}</span>
      <strong class="impersonation-banner__user">{{ info.user_email || info.user_id }}</strong>
      <span
        v-if="info.org_name || info.org_key"
        class="impersonation-banner__organization"
      >
        <span aria-hidden="true">·</span>
        <span>{{ info.org_name || info.org_key }}</span>
      </span>
    </div>
    <div class="impersonation-banner__actions">
      <span
        v-if="remainingTime"
        class="impersonation-banner__remaining"
        :class="{ 'impersonation-banner__remaining--soon': expiringSoon }"
      >{{ remainingTime }}</span>
      <el-button
        class="impersonation-banner__end"
        size="small"
        :loading="busy"
        :disabled="busy"
        @click="end"
      >
        <template #icon>
          <LogOut
            class="impersonation-banner__end-icon"
            :size="14"
            :stroke-width="1.75"
            aria-hidden="true"
          />
        </template>
        {{ t('impersonation.endSession') }}
      </el-button>
    </div>
    <p
      v-if="error"
      class="impersonation-banner__error"
      role="alert"
    >
      {{ error }}
    </p>
  </div>
</template>
<style scoped>
.impersonation-banner {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 6px 24px;
  box-sizing: border-box;
  min-height: 40px;
  padding: 5px max(24px, var(--app-safe-right, 0px)) 5px max(24px, var(--app-safe-left, 0px));
  color: color-mix(in srgb, var(--el-text-color-regular) 50%, var(--el-text-color-secondary));
  background: var(--el-fill-color-light);
  border-bottom: 1px solid var(--el-border-color-light);
  font-size: 13px;
  font-weight: 400;
  line-height: 20px;
}
.impersonation-banner--content {
  padding-right: max(var(--page-gutter, 16px), var(--app-safe-right, 0px));
  padding-left: var(--page-gutter, 16px);
  background: var(--content-bg);
}
.impersonation-banner__identity {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 8px;
  min-width: 0;
}
.impersonation-banner__icon { flex-shrink: 0; color: var(--color-warning-text); }
.impersonation-banner__label { white-space: nowrap; }
.impersonation-banner__user { min-width: 0; color: var(--el-text-color-primary); font-weight: 500; overflow-wrap: anywhere; }
.impersonation-banner__organization { display: inline-flex; align-items: baseline; gap: 8px; min-width: 0; overflow-wrap: anywhere; }
.impersonation-banner__actions { display: flex; align-items: center; gap: 16px; }
.impersonation-banner__remaining { white-space: nowrap; font-variant-numeric: tabular-nums; }
.impersonation-banner__remaining--soon { color: var(--color-warning-text); font-weight: 500; }
.impersonation-banner .impersonation-banner__end.el-button.el-button--small {
  flex-shrink: 0;
  height: 28px;
  min-height: 28px;
  margin: 0;
  padding: 0 12px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 400;
  line-height: 20px;
}
.impersonation-banner__end :deep(.el-icon) { font-size: 14px; }
.impersonation-banner__end :deep(.el-icon + span) { margin-left: 6px; }
.impersonation-banner__error { grid-column: 1 / -1; margin: 0; color: var(--el-color-danger); overflow-wrap: anywhere; }
@media (max-width: 640px) {
  .impersonation-banner { grid-template-columns: minmax(0, 1fr); padding: 8px max(16px, var(--app-safe-right, 0px)) 8px max(16px, var(--app-safe-left, 0px)); }
  .impersonation-banner__actions { justify-content: flex-end; }
  .impersonation-banner__remaining { margin-right: auto; }
  .impersonation-banner .impersonation-banner__end.el-button.el-button--small {
    position: relative;
    isolation: isolate;
    height: 44px;
    min-height: 44px;
    border-width: 0;
    background-color: transparent;
  }
  /* A 32px visible surface inside the native button's 44px touch target. */
  .impersonation-banner__end::after {
    content: '';
    position: absolute;
    inset: 6px 0;
    z-index: -1;
    pointer-events: none;
    border: 1px solid var(--el-button-border-color);
    border-radius: inherit;
    background-color: var(--el-button-bg-color);
  }
  .impersonation-banner__end:not(.is-disabled):hover::after {
    border-color: var(--el-button-hover-border-color);
    background-color: var(--el-button-hover-bg-color);
  }
  .impersonation-banner__end:not(.is-disabled):active::after {
    border-color: var(--el-button-active-border-color);
    background-color: var(--el-button-active-bg-color);
  }
  .impersonation-banner__end.is-disabled::after {
    border-color: var(--el-button-disabled-border-color);
    background-color: var(--el-button-disabled-bg-color);
  }
  .impersonation-banner__end.is-loading::before { inset: 6px 0; }
}
</style>
