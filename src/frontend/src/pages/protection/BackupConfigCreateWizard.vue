<script setup lang="ts">
import type { Component } from 'vue'
import { nextTick, onMounted, watch } from 'vue'
import { ArrowLeft, ArrowRight, Check, X } from 'lucide-vue-next'
import WizardSteps from '../../components/WizardSteps.vue'

type CreatePhase = 'form' | 'waiting'
type WizardStepValue = string | number
type WizardStepItem = {
  step: WizardStepValue
  label: string
  icon?: Component
}

const props = withDefaults(defineProps<{
  phase: CreatePhase
  steps: WizardStepItem[]
  currentStep: WizardStepValue
  isStepDone: (step: WizardStepValue, index: number) => boolean
  isStepLocked: (step: WizardStepValue, index: number) => boolean
  isLastStep: boolean
  canGoPrev: boolean
  title: string
  description: string
  ariaLabel: string
  waitingText: string
  cancelLabel: string
  prevLabel: string
  nextLabel: string
  confirmLabel: string
  bootstrapping?: boolean
  busy?: boolean
  canClose?: boolean
  animated?: boolean
  showSteps?: boolean
}>(), {
  animated: true,
  canClose: true,
  showSteps: true,
})

const emit = defineEmits<{
  close: []
  prev: []
  next: []
  confirm: []
  visible: []
}>()

let visibleEmitted = false

function nextAnimationFrame() {
  return new Promise<void>((resolve) => {
    if (typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
      resolve()
      return
    }
    window.requestAnimationFrame(() => resolve())
  })
}

async function emitVisibleWhenReady() {
  if (visibleEmitted || props.bootstrapping) return
  await nextTick()
  await nextAnimationFrame()
  await nextAnimationFrame()
  if (visibleEmitted || props.bootstrapping) return
  visibleEmitted = true
  emit('visible')
}

onMounted(() => {
  void emitVisibleWhenReady()
})

watch(
  () => [props.bootstrapping, props.phase, props.currentStep],
  () => {
    void emitVisibleWhenReady()
  },
  { flush: 'post' },
)
</script>

<template>
  <Teleport to="body">
    <div
      class="fullscreen-form-fullscreen create-backup-fullscreen"
      :class="{ 'fullscreen-form-animated': animated }"
    >
      <div class="fullscreen-form-page create-backup-page">
        <div class="fullscreen-form-header">
          <button
            type="button"
            class="fullscreen-form-header__back"
            :disabled="bootstrapping || !canClose"
            @click="emit('close')"
          >
            <ArrowLeft
              class="fullscreen-form-header__back-icon"
              :size="18"
            />
          </button>
          <div class="fullscreen-form-header__content">
            <h1 class="fullscreen-form-header__title">
              {{ title }}
            </h1>
            <p class="fullscreen-form-header__desc">
              {{ description }}
            </p>
          </div>
        </div>

        <div
          class="fullscreen-form-layout create-backup-layout"
          :class="{
            'create-backup-layout--steps': showSteps,
            'create-backup-layout--single': !showSteps,
          }"
        >
          <WizardSteps
            v-if="showSteps && (phase === 'form' || phase === 'waiting' || bootstrapping)"
            as="aside"
            class="create-backup-steps"
            :steps="steps"
            :current-step="currentStep"
            :is-done="isStepDone"
            :is-locked="isStepLocked"
            :clickable="false"
            :aria-label="ariaLabel"
          />

          <main
            v-loading="bootstrapping"
            class="fullscreen-form-main create-backup-main"
            :aria-busy="bootstrapping"
            :element-loading-text="waitingText"
          >
            <div
              v-if="phase === 'form' || phase === 'waiting'"
              class="create-backup-step-body dp-process-page"
              :inert="bootstrapping || phase === 'waiting' ? true : undefined"
            >
              <div
                v-if="phase === 'waiting'"
                class="create-backup-submission-status"
                role="status"
                aria-live="polite"
              >
                <span
                  class="create-backup-submission-status__spinner"
                  aria-hidden="true"
                />
                <span>{{ waitingText }}</span>
              </div>
              <slot name="form" />
            </div>
          </main>
        </div>

        <div
          v-if="phase === 'form' || phase === 'waiting' || bootstrapping"
          class="fullscreen-form-footer create-backup-footer"
        >
          <div class="create-backup-footer__inner">
            <div class="create-backup-footer__actions">
              <ElButton
                class="hfl-btn-with-icon"
                :disabled="bootstrapping || phase === 'waiting'"
                @click="emit('close')"
              >
                <X :size="14" />
                <span>{{ cancelLabel }}</span>
              </ElButton>
              <ElButton
                v-if="canGoPrev"
                class="hfl-btn-with-icon"
                :disabled="bootstrapping || busy || phase === 'waiting'"
                @click="emit('prev')"
              >
                <ArrowLeft :size="14" />
                <span>{{ prevLabel }}</span>
              </ElButton>
              <ElButton
                v-if="!isLastStep"
                type="primary"
                class="hfl-btn-with-icon"
                :loading="busy"
                :disabled="bootstrapping || busy || phase === 'waiting'"
                @click="emit('next')"
              >
                <span>{{ nextLabel }}</span>
                <ArrowRight :size="14" />
              </ElButton>
              <ElButton
                v-else
                type="primary"
                class="hfl-btn-with-icon"
                :loading="phase === 'waiting'"
                :disabled="bootstrapping || busy || phase === 'waiting'"
                @click="emit('confirm')"
              >
                <Check :size="14" />
                <span>{{ confirmLabel }}</span>
              </ElButton>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.create-backup-page {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.create-backup-submission-status {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 16px;
  padding: 12px 16px;
  color: var(--el-color-primary-dark-2);
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.5;
}

.create-backup-submission-status__spinner {
  width: 16px;
  height: 16px;
  flex: 0 0 auto;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: create-backup-spin 0.8s linear infinite;
}

@keyframes create-backup-spin {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .create-backup-submission-status__spinner {
    animation-duration: 2s;
  }
}

.create-backup-layout {
  width: min(100%, 1560px);
}

.create-backup-fullscreen .create-backup-layout--steps {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  gap: 24px;
  min-height: 0;
}

.create-backup-fullscreen .create-backup-layout--single {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}

@media (min-width: 1024px) {
  .create-backup-fullscreen .create-backup-layout--steps {
    flex-direction: row;
    align-items: stretch;
  }
}

.create-backup-steps {
  align-self: flex-start;
}

.create-backup-main {
  --create-backup-primary: var(--color-primary);
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  background: #fff;
  overflow-x: hidden;
  overflow-y: auto;
  border: 1px solid rgba(203, 213, 225, 0.9);
  border-radius: 8px;
  border-color: color-mix(in srgb, var(--create-backup-primary) 55%, transparent);
  box-shadow:
    inset 3px 0 0 color-mix(in srgb, var(--create-backup-primary) 85%, transparent),
    0 8px 20px rgba(15, 23, 42, 0.04);
}

.create-backup-step-body {
  display: flex;
  flex-direction: column;
  gap: 0;
  min-height: 0;
  padding: 20px;
}

.create-backup-footer__inner,
.create-backup-footer__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  width: 100%;
}

.create-backup-footer__actions :deep(.el-button + .el-button) {
  margin-left: 0;
}
</style>
