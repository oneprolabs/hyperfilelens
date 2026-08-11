<script setup lang="ts">
import type { Component } from 'vue'
import { nextTick, onMounted, watch } from 'vue'
import { ArrowLeft, ArrowRight, Check, X } from 'lucide-vue-next'
import WizardSteps from '../../components/WizardSteps.vue'

type CreatePhase = 'form' | 'waiting' | 'done'
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
  resultTitle: string
  resultSubtitle: string
  cancelLabel: string
  prevLabel: string
  nextLabel: string
  confirmLabel: string
  closeLabel: string
  goToStartBackupLabel: string
  bootstrapping?: boolean
  busy?: boolean
  animated?: boolean
  showSteps?: boolean
}>(), {
  animated: true,
  showSteps: true,
})

const emit = defineEmits<{
  close: []
  prev: []
  next: []
  confirm: []
  goToStartBackup: []
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
          <button type="button" class="fullscreen-form-header__back" @click="emit('close')">
            <ArrowLeft class="fullscreen-form-header__back-icon" :size="18" />
          </button>
          <div class="fullscreen-form-header__content">
            <h1 class="fullscreen-form-header__title">{{ title }}</h1>
            <p class="fullscreen-form-header__desc">{{ description }}</p>
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
            v-if="showSteps && (phase === 'form' || bootstrapping)"
            as="aside"
            class="create-backup-steps"
            :steps="steps"
            :current-step="currentStep"
            :is-done="isStepDone"
            :is-locked="isStepLocked"
            :clickable="false"
            :aria-label="ariaLabel"
          />

          <main class="fullscreen-form-main create-backup-main">
            <div v-if="bootstrapping" class="create-backup-step-body dp-process-page create-backup-loading-pane">
              <div class="fullscreen-form-card create-backup-loading-card">
                <el-progress :percentage="66" :indeterminate="true" status="warning" class="mb-4" />
                <p class="text-slate-600">{{ waitingText }}</p>
              </div>
            </div>

            <div
              v-else-if="phase === 'form'"
              class="create-backup-step-body dp-process-page"
            >
              <slot name="form" />
            </div>

            <div v-else-if="phase === 'waiting'" class="fullscreen-form-card py-10 text-center">
              <el-progress :percentage="66" :indeterminate="true" status="warning" class="mb-4" />
              <p class="text-slate-600">{{ waitingText }}</p>
            </div>

            <div v-else class="fullscreen-form-card py-8 text-center space-y-4">
              <el-result icon="success" :title="resultTitle" :sub-title="resultSubtitle">
                <template #extra>
                  <div class="flex flex-wrap items-center justify-center gap-2">
                    <ElButton type="primary" @click="emit('close')">{{ closeLabel }}</ElButton>
                    <ElButton @click="emit('goToStartBackup')">{{ goToStartBackupLabel }}</ElButton>
                  </div>
                </template>
              </el-result>
            </div>
          </main>
        </div>

        <div v-if="phase === 'form' || bootstrapping" class="fullscreen-form-footer create-backup-footer">
          <div class="create-backup-footer__inner">
            <div class="create-backup-footer__actions">
              <ElButton class="hfl-btn-with-icon" @click="emit('close')">
                <X :size="14" />
                <span>{{ cancelLabel }}</span>
              </ElButton>
              <ElButton v-if="canGoPrev" class="hfl-btn-with-icon" :disabled="bootstrapping || busy" @click="emit('prev')">
                <ArrowLeft :size="14" />
                <span>{{ prevLabel }}</span>
              </ElButton>
              <ElButton v-if="!isLastStep" type="primary" class="hfl-btn-with-icon" :loading="busy" :disabled="bootstrapping || busy" @click="emit('next')">
                <span>{{ nextLabel }}</span>
                <ArrowRight :size="14" />
              </ElButton>
              <ElButton v-else type="primary" class="hfl-btn-with-icon" :disabled="bootstrapping || busy" @click="emit('confirm')">
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

.create-backup-loading-pane {
  min-height: 360px;
  justify-content: center;
  padding: 20px;
}

.create-backup-loading-card {
  width: min(100%, 520px);
  margin: 0 auto;
  padding: 40px 32px;
  text-align: center;
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
