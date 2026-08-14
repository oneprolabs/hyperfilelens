<script setup lang="ts">
import { computed, onUnmounted, watch } from 'vue'
import { X, ArrowLeft } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  open?: boolean
  modelValue?: boolean
  title: string
  width?: string
  description?: string
  size?: 'default' | 'small' | 'medium' | 'large' | 'xlarge' | 'xxlarge'
  minHeight?: string
  fixed?: boolean
  variant?: 'modal' | 'page'
  theme?: 'default' | 'policy'
  inline?: boolean
  showBack?: boolean
}>(), {
  width: undefined,
  description: '',
  size: 'default',
  minHeight: undefined,
  variant: 'modal',
  showBack: true,
  theme: 'default',
  inline: false,
})

const visible = computed(() => props.open ?? props.modelValue ?? false)
const isPageVariant = computed(() => props.variant === 'page')
const isPolicyTheme = computed(() => isPageVariant.value && props.theme === 'policy')

const shellClass = computed(() => {
  const base = isPageVariant.value
    ? props.inline
      ? 'modal-page-shell--inline relative flex h-full w-full min-w-0 flex-1 flex-col overflow-hidden bg-[var(--modal-bg,#fff)]'
      : 'absolute inset-0 flex flex-col overflow-hidden bg-[var(--modal-bg,#fff)]'
    : 'absolute left-1/2 top-12 flex w-[92vw] -translate-x-1/2 flex-col overflow-hidden rounded-[var(--radius-card)] border border-[var(--color-border-light,#e2e8f0)] bg-[var(--modal-bg,#fff)] shadow-xl'
  const max: Record<NonNullable<typeof props.size>, string> = {
    default: 'max-w-[720px]',
    small: 'max-w-[440px]',
    medium: 'max-w-[800px]',
    large: 'max-w-[960px]',
    xlarge: 'max-w-[1120px] w-[min(96vw,1120px)]',
    xxlarge: 'max-w-[1320px] w-[min(98vw,1320px)]',
  }
  const key = props.size ?? 'default'
  return isPageVariant.value || props.width ? base : `${base} ${max[key] ?? max.default}`
})

const shellStyle = computed(() => {
  const width = props.width ? `min(${props.width}, 92vw)` : undefined
  if (isPageVariant.value) {
    if (props.inline) {
      return {
        height: '100%',
        maxHeight: '100%',
        minHeight: '100%',
      }
    }
    return {
      height: '100%',
      maxHeight: 'var(--app-viewport-height)',
    }
  }
  if (props.fixed) {
    const h = props.minHeight ?? 'min(840px, calc(var(--app-viewport-height) - 3rem))'
    return {
      height: h,
      maxHeight: 'calc(var(--app-viewport-height) - 3rem)',
      width,
    }
  }
  return {
    maxHeight: 'calc(var(--app-viewport-height) - 3.5rem)',
    minHeight: props.minHeight,
    width,
  }
})

const emit = defineEmits<{
  close: []
  'update:modelValue': [value: boolean]
}>()

function requestClose() {
  emit('update:modelValue', false)
  emit('close')
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') requestClose()
}

watch(
  () => visible.value,
  (v) => {
    if (v) window.addEventListener('keydown', onKey)
    else window.removeEventListener('keydown', onKey)
  },
  { immediate: true },
)

onUnmounted(() => {
  window.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div
    v-if="visible"
    :class="isPageVariant ? (props.inline ? 'relative z-auto flex min-h-0 w-full flex-1' : 'absolute inset-0 z-50') : 'fixed inset-0 z-50'"
  >
    <div
      v-if="!isPageVariant"
      class="absolute inset-0 bg-black/30"
      @click="requestClose"
    />
    <div
      :class="[shellClass, { 'modal-page-shell modal-page-shell--policy': isPolicyTheme }]"
      :style="shellStyle"
    >
      <template v-if="isPageVariant">
        <div
          :class="[
            'flex shrink-0 items-center justify-between border-b border-[var(--color-border,#e2e8f0)] bg-[var(--modal-bg,#fff)] px-4 py-3',
            { 'modal-page-shell__header': isPolicyTheme },
          ]"
        >
          <div :class="['flex items-center gap-3', { 'modal-page-shell__header-inner': isPolicyTheme }]">
            <ElButton
              v-if="props.showBack"
              :class="isPolicyTheme ? 'modal-page-shell__back !p-1.5' : '!p-1.5'"
              aria-label="Back"
              text
              @click="requestClose"
            >
              <ArrowLeft
                :size="20"
                class="text-slate-600"
              />
            </ElButton>
            <div
              v-if="isPolicyTheme"
              class="modal-page-shell__headline"
            >
              <span class="modal-page-shell__title">{{ title }}</span>
              <p
                v-if="description"
                class="modal-page-shell__desc"
              >
                {{ description }}
              </p>
            </div>
            <span
              v-else
              class="text-base font-semibold text-slate-900"
            >{{ title }}</span>
          </div>
        </div>
        <div
          :class="[
            'scrollbar min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain px-6 py-4',
            { 'modal-page-shell__body': isPolicyTheme },
          ]"
        >
          <slot />
        </div>
        <div
          v-if="$slots.footer"
          :class="[
            'modal-footer-actions shrink-0 border-t border-[var(--color-border,#e2e8f0)] bg-[var(--modal-bg,#fff)] px-6 py-3',
            { 'modal-page-shell__footer': isPolicyTheme },
          ]"
        >
          <slot name="footer" />
        </div>
      </template>
      <template v-else>
        <div class="flex shrink-0 items-center justify-between border-b border-[var(--color-border-light,#f1f5f9)] px-5 py-3">
          <div class="min-w-0">
            <div class="text-sm font-semibold text-slate-900">
              {{ title }}
            </div>
            <p
              v-if="description"
              class="mt-1 text-xs text-slate-500"
            >
              {{ description }}
            </p>
          </div>
          <ElButton
            class="!p-1.5"
            aria-label="Close"
            text
            @click="requestClose"
          >
            <X :size="16" />
          </ElButton>
        </div>
        <div class="scrollbar min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain px-5 py-4">
          <slot />
        </div>
        <div
          v-if="$slots.footer"
          class="modal-footer-actions shrink-0 border-t border-[var(--color-border-light,#f1f5f9)] bg-[var(--modal-bg,#fff)] px-5 py-3"
        >
          <slot name="footer" />
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>

.modal-page-shell__header {
  gap: 12px;
  min-height: 64px;
  padding-top: 12px;
  padding-bottom: 12px;
  background: rgba(255, 255, 255, 0.96);
  border-bottom: 1px solid rgba(203, 213, 225, 0.95);
  box-shadow: 0 8px 18px rgba(15, 23, 42, 0.04);
}

.modal-page-shell__back {
  width: 34px;
  height: 34px;
  padding: 0;
  margin-right: 0;
  border: 1px solid rgba(203, 213, 225, 0.95);
  border-radius: 8px;
  color: rgb(71 85 105);
  transition:
    border-color 0.18s ease,
    color 0.18s ease,
    background 0.18s ease;
}

.modal-page-shell__back:hover {
  border-color: rgba(59, 130, 246, 0.38);
  color: rgb(30 64 175);
  background: rgb(248 250 252);
}

.modal-page-shell__headline {
  display: flex;
  min-width: 0;
  flex: 1 1 auto;
  flex-direction: column;
}

.modal-page-shell__title {
  font-size: 22px;
  font-weight: 600;
  line-height: 1.3;
  letter-spacing: -0.01em;
  color: rgb(15 23 42);
}

.modal-page-shell__desc {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: rgb(100 116 139);
}

.modal-page-shell__body {
  padding-top: 24px;
}

.modal-page-shell__footer {
  background: rgba(255, 255, 255, 0.96);
  border-top: 1px solid rgba(203, 213, 225, 0.95);
  box-shadow: 0 -8px 18px rgba(15, 23, 42, 0.04);
}

.modal-page-shell--inline .modal-page-shell__header {
  padding-left: 0;
  padding-right: 0;
}

.modal-page-shell--inline .modal-page-shell__body {
  overflow-x: hidden;
  overflow-y: auto;
  padding-left: 0;
  padding-right: 0;
  padding-bottom: 24px;
}

.modal-page-shell--inline .modal-page-shell__footer {
  padding-left: 0;
  padding-right: 0;
  background: transparent;
  position: sticky;
  bottom: 0;
  z-index: 2;
}
</style>
