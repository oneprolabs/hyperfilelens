<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import {
  CloudOff,
  Network,
  Plus,
  Sparkles,
} from 'lucide-vue-next'

export type CopilotEmptyPhase = 'loading' | 'network_error' | 'bridge_not_ready' | 'onboarding'

export type CopilotReadiness = {
  hasModels: boolean
  hasGateways: boolean
  hasKnowledgeSources: boolean
  hasAssistants: boolean
  canStartChat: boolean
}

defineProps<{
  phase: CopilotEmptyPhase
  readiness?: CopilotReadiness
}>()

const emit = defineEmits<{
  retry: []
  newChat: []
}>()

const { t } = useI18n()
</script>

<template>
  <div
    v-loading="phase === 'loading'"
    class="copilot-empty-state flex min-h-0 flex-1 flex-col items-center justify-center px-6 py-10"
  >
    <template v-if="phase === 'network_error'">
      <div class="empty-card">
        <div class="empty-icon empty-icon--error">
          <Network :size="28" />
        </div>
        <h2 class="empty-title">
          {{ t('insight.copilot.emptyNetworkTitle') }}
        </h2>
        <p class="empty-desc">
          {{ t('insight.copilot.emptyNetworkDesc') }}
        </p>
        <ElButton
          type="primary"
          @click="emit('retry')"
        >
          {{ t('insight.copilot.btnRetry') }}
        </ElButton>
      </div>
    </template>

    <template v-else-if="phase === 'bridge_not_ready'">
      <div class="empty-card">
        <div class="empty-icon empty-icon--offline">
          <CloudOff
            :size="26"
            stroke-width="1.75"
          />
        </div>
        <h2 class="empty-title">
          {{ t('insight.copilot.emptyBridgeTitle') }}
        </h2>
        <p class="empty-desc">
          {{ t('insight.shared.bridgeNotReady') }}
        </p>
        <ElButton
          type="primary"
          @click="emit('retry')"
        >
          {{ t('insight.copilot.btnRetry') }}
        </ElButton>
      </div>
    </template>

    <template v-else-if="phase === 'onboarding'">
      <div class="empty-card empty-card--wide">
        <div class="empty-icon empty-icon--primary">
          <Sparkles :size="28" />
        </div>
        <h2 class="empty-title">
          {{ t('insight.copilot.onboardingTitle') }}
        </h2>
        <p class="empty-desc">
          {{ t('insight.copilot.onboardingSubtitle') }}
        </p>

        <ElButton
          type="primary"
          class="onboarding-primary"
          @click="emit('newChat')"
        >
          <Plus :size="16" />
          {{ t('insight.copilot.startNewChat') }}
        </ElButton>
      </div>
    </template>
  </div>
</template>

<style scoped>
.copilot-empty-state {
  background: var(--color-card-bg);
}

.empty-card {
  display: flex;
  max-width: 420px;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  text-align: center;
}

.empty-card--wide {
  max-width: 460px;
}

.empty-icon {
  display: flex;
  height: 56px;
  width: 56px;
  align-items: center;
  justify-content: center;
  border-radius: 16px;
}

.empty-icon--error {
  background: color-mix(in srgb, var(--el-color-danger) 12%, transparent);
  color: var(--el-color-danger);
}

.empty-icon--offline {
  background: var(--color-primary-light, rgba(99, 102, 241, 0.1));
  color: var(--color-primary);
  border: 1px solid color-mix(in srgb, var(--color-primary) 16%, transparent);
  box-shadow: 0 1px 2px color-mix(in srgb, var(--color-primary) 8%, transparent);
}

.empty-icon--primary {
  background: var(--color-primary-light, rgba(99, 102, 241, 0.12));
  color: var(--color-primary);
}

.empty-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text-title);
}

.empty-desc {
  margin: 0;
  max-width: 36em;
  font-size: 14px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}

.empty-card :deep(.el-button--primary) {
  margin-top: 4px;
  min-width: 96px;
}

.onboarding-primary {
  margin-top: 8px;
}
</style>
