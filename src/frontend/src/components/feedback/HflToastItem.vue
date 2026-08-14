<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { AlertTriangle, Check, Copy, Info, X } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import { copyTextToClipboard } from '../../lib/clipboard'
import { openErrorDetails } from '../../lib/errors/details'
import { closeToast, pauseToast, resumeToast, type ToastRecord } from '../../lib/toast/store'

const props = defineProps<{ toast: ToastRecord }>()
const { t } = useI18n()
const copied = ref(false)
const hovered = ref(false)
const keyboardFocused = ref(false)
let keyboardModality = false

const icon = computed(() => {
  if (props.toast.type === 'success') return Check
  if (props.toast.type === 'warning' || props.toast.type === 'error') return AlertTriangle
  return Info
})

const progress = computed(() => {
  if (props.toast.duration === 0) return 100
  return Math.max(0, Math.min(100, (props.toast.remainingMs / props.toast.duration) * 100))
})

const isCompact = computed(() => !props.toast.title && !props.toast.copyText && !props.toast.details)
const detailsActionLabel = computed(() => (
  props.toast.details?.resolutions?.length
    ? t('feedback.toast.howToFix')
    : t('feedback.toast.viewDetails')
))

async function copyDetails() {
  if (!props.toast.copyText) return
  await copyTextToClipboard(props.toast.copyText)
  copied.value = true
  window.setTimeout(() => { copied.value = false }, 1600)
}

function showDetails() {
  if (!props.toast.details) return
  const details = props.toast.details
  closeToast(props.toast.id)
  openErrorDetails(details)
}

function syncPauseState() {
  if (hovered.value || keyboardFocused.value) pauseToast(props.toast.id)
  else resumeToast(props.toast.id)
}

function onMouseEnter() {
  hovered.value = true
  syncPauseState()
}

function onMouseLeave() {
  hovered.value = false
  syncPauseState()
}

function onFocusIn() {
  keyboardFocused.value = keyboardModality
  syncPauseState()
}

function onFocusOut(event: FocusEvent) {
  const root = event.currentTarget as HTMLElement
  if (event.relatedTarget instanceof Node && root.contains(event.relatedTarget)) return
  keyboardFocused.value = false
  syncPauseState()
}

function onDocumentKeydown(event: KeyboardEvent) {
  if (event.key === 'Tab') keyboardModality = true
}

function onDocumentPointerdown() {
  keyboardModality = false
  if (!keyboardFocused.value) return
  keyboardFocused.value = false
  syncPauseState()
}

onMounted(() => {
  document.addEventListener('keydown', onDocumentKeydown, true)
  document.addEventListener('pointerdown', onDocumentPointerdown, true)
})

onUnmounted(() => {
  document.removeEventListener('keydown', onDocumentKeydown, true)
  document.removeEventListener('pointerdown', onDocumentPointerdown, true)
})
</script>

<template>
  <article
    class="hfl-toast"
    :class="[`hfl-toast--${toast.type}`, { 'hfl-toast--compact': isCompact }]"
    :data-toast-id="toast.id"
    :role="toast.type === 'error' || toast.type === 'warning' ? 'alert' : 'status'"
    tabindex="-1"
    @mouseenter="onMouseEnter"
    @mouseleave="onMouseLeave"
    @focusin="onFocusIn"
    @focusout="onFocusOut"
  >
    <span
      class="hfl-toast__icon"
      aria-hidden="true"
    >
      <component
        :is="icon"
        :size="18"
      />
    </span>
    <div class="hfl-toast__content">
      <div
        v-if="toast.title"
        class="hfl-toast__title"
      >
        <span>{{ toast.title }}</span>
        <span
          v-if="toast.repeatCount > 1"
          class="hfl-toast__repeat"
        >×{{ toast.repeatCount }}</span>
      </div>
      <p class="hfl-toast__message">
        {{ toast.message }}
        <span
          v-if="!toast.title && toast.repeatCount > 1"
          class="hfl-toast__repeat"
        >×{{ toast.repeatCount }}</span>
      </p>
      <div
        v-if="toast.copyText || toast.details"
        class="hfl-toast__actions"
      >
        <button
          v-if="toast.copyText"
          type="button"
          @click="copyDetails"
        >
          <Check
            v-if="copied"
            :size="13"
          />
          <Copy
            v-else
            :size="13"
          />
          {{ copied ? t('feedback.toast.copied') : t('feedback.toast.copy') }}
        </button>
        <button
          v-if="toast.details"
          type="button"
          @click="showDetails"
        >
          {{ detailsActionLabel }}
        </button>
      </div>
    </div>
    <button
      type="button"
      class="hfl-toast__close"
      :aria-label="t('common.close')"
      @click="closeToast(toast.id)"
    >
      <X :size="15" />
    </button>
    <span
      v-if="toast.duration > 0"
      class="hfl-toast__progress"
      :style="{ width: `${progress}%` }"
      aria-hidden="true"
    />
  </article>
</template>

<style scoped>
.hfl-toast {
  --toast-accent: var(--color-info, #3b82f6);
  --toast-accent-soft: var(--color-info-light, #eff6ff);
  position: relative;
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) 24px;
  gap: 12px;
  width: 372px;
  max-width: calc(100vw - 32px);
  overflow: hidden;
  padding: 14px;
  color: var(--color-text-title, #14233b);
  background: var(--modal-bg, var(--color-card-bg, #fff));
  border: 1px solid var(--color-border-light, #e6ebf2);
  border-radius: 13px;
  box-shadow: 0 16px 40px -12px rgba(16, 24, 40, 0.28);
  pointer-events: auto;
  animation: hfl-toast-in 220ms cubic-bezier(.2, .8, .2, 1);
}

.hfl-toast--success { --toast-accent: var(--color-success, #15a66a); --toast-accent-soft: var(--color-success-light, #e7f8f0); }
.hfl-toast--warning { --toast-accent: var(--color-warning, #e7920e); --toast-accent-soft: var(--color-warning-light, #fef3e2); }
.hfl-toast--error { --toast-accent: var(--color-error, #e5484d); --toast-accent-soft: var(--color-error-light, #fdecec); }

.hfl-toast__icon {
  display: inline-flex;
  width: 36px;
  height: 36px;
  align-items: center;
  justify-content: center;
  color: var(--toast-accent);
  background: var(--toast-accent-soft);
  border-radius: 10px;
}

.hfl-toast__content { min-width: 0; }
.hfl-toast__title { display: flex; align-items: center; gap: 7px; font-size: 13.5px; font-weight: 700; line-height: 20px; }
.hfl-toast__message { margin: 0; color: var(--color-text-primary, #5a6b80); font-size: 12.5px; line-height: 1.5; overflow-wrap: anywhere; user-select: text; }
.hfl-toast__title + .hfl-toast__message { margin-top: 2px; }
.hfl-toast__repeat { display: inline-flex; flex: none; padding: 1px 6px; color: var(--toast-accent); background: var(--toast-accent-soft); border-radius: 999px; font-size: 10px; font-weight: 700; }
.hfl-toast__actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 7px; }
.hfl-toast__actions button { display: inline-flex; align-items: center; gap: 4px; padding: 0; color: var(--color-primary, #5c5cff); background: transparent; border: 0; font: inherit; font-size: 12px; font-weight: 600; cursor: pointer; }
.hfl-toast__actions button:hover { text-decoration: underline; }
.hfl-toast__close { display: inline-flex; width: 24px; height: 24px; padding: 0; align-items: center; justify-content: center; color: var(--color-text-tertiary, #a6b2c2); background: transparent; border: 0; border-radius: 6px; cursor: pointer; }
.hfl-toast__close:hover, .hfl-toast__close:focus-visible { color: var(--color-text-primary, #5a6b80); background: var(--color-grey-2, #f2f5fa); }
.hfl-toast__progress { position: absolute; bottom: 0; left: 0; height: 3px; background: var(--toast-accent); opacity: .58; transition: width 50ms linear; }

.hfl-toast--compact .hfl-toast__icon,
.hfl-toast--compact .hfl-toast__content,
.hfl-toast--compact .hfl-toast__close { align-self: center; }
.hfl-toast--compact .hfl-toast__message { font-weight: 600; line-height: 20px; }

@keyframes hfl-toast-in { from { opacity: 0; transform: translateX(26px) scale(.97); } }

@media (max-width: 640px) {
  .hfl-toast {
    grid-template-columns: 32px minmax(0, 1fr) 44px;
    gap: 8px;
    width: min(100%, 340px);
    max-width: 100%;
    padding: 12px 0 12px 12px;
    border-radius: 12px;
    box-shadow: 0 10px 28px -12px rgba(16, 24, 40, 0.24);
  }

  .hfl-toast__icon {
    width: 32px;
    height: 32px;
    border-radius: 8px;
  }

  .hfl-toast__close {
    width: 44px;
    height: 44px;
    align-self: center;
  }

  .hfl-toast__actions {
    gap: 8px;
    margin-top: 4px;
  }

  .hfl-toast__actions button {
    min-height: 44px;
  }

  .hfl-toast--compact {
    grid-template-columns: 32px minmax(0, 1fr) 44px;
    padding: 4px 0 4px 8px;
    border-radius: 10px;
  }

  .hfl-toast--compact .hfl-toast__progress {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) { .hfl-toast { animation: none; } .hfl-toast__progress { transition: none; } }
</style>
