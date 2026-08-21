<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { GatewayChatWorkload } from '../../lib/lensApi'

const props = defineProps<{
  workload: GatewayChatWorkload
  saving?: boolean
}>()

const emit = defineEmits<{
  save: [settings: {
    chat_prepare_concurrency: number
    chat_queue_capacity: number
  }]
}>()

const { t } = useI18n()
const concurrencyHintId = `dg-chat-concurrency-hint-${useId()}`
const queueCapacityHintId = `dg-chat-queue-capacity-hint-${useId()}`
const concurrency = ref<number | undefined>(1)
const queueCapacity = ref<number | undefined>(10)

function resetDraft() {
  concurrency.value = Number(props.workload.chat_prepare_concurrency)
  queueCapacity.value = Number(props.workload.chat_queue_capacity)
}

watch(
  () => [
    props.workload.chat_prepare_concurrency,
    props.workload.chat_queue_capacity,
  ] as const,
  resetDraft,
  { immediate: true },
)

const changed = computed(() => (
  concurrency.value !== Number(props.workload.chat_prepare_concurrency)
  || queueCapacity.value !== Number(props.workload.chat_queue_capacity)
))
const valid = computed(() => (
  Number.isInteger(Number(concurrency.value))
  && Number(concurrency.value) >= 1
  && Number(concurrency.value) <= 32
  && Number.isInteger(Number(queueCapacity.value))
  && Number(queueCapacity.value) >= 0
  && Number(queueCapacity.value) <= 1000
))

function save() {
  if (!changed.value || !valid.value || props.saving) return
  emit('save', {
    chat_prepare_concurrency: Number(concurrency.value),
    chat_queue_capacity: Number(queueCapacity.value),
  })
}
</script>

<template>
  <section class="hfl-detail-section dg-chat-workload">
    <div class="dg-chat-workload__heading">
      <div>
        <h4 class="hfl-detail-section__title">
          {{ t('insight.dataGateway.chatWorkloadTitle') }}
        </h4>
        <p>{{ t('insight.dataGateway.chatWorkloadDescription') }}</p>
      </div>
      <ElButton
        type="primary"
        size="small"
        :loading="saving"
        :disabled="!changed || !valid"
        @click="save"
      >
        {{ t('common.save') }}
      </ElButton>
    </div>

    <div class="hfl-detail-grid dg-chat-workload__stats">
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('insight.dataGateway.chatRunning') }}</span>
        <span class="hfl-detail-row__value">{{ workload.active_chat_preparations }}</span>
      </div>
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('insight.dataGateway.chatQueued') }}</span>
        <span class="hfl-detail-row__value">{{ workload.queued_chat_preparations }}</span>
      </div>
    </div>

    <ElForm
      label-position="top"
      class="dg-chat-workload__form"
    >
      <ElFormItem :label="t('insight.dataGateway.chatConcurrency')">
        <ElInputNumber
          v-model="concurrency"
          :aria-label="t('insight.dataGateway.chatConcurrency')"
          :aria-describedby="concurrencyHintId"
          :min="1"
          :max="32"
          :step="1"
          controls-position="right"
        />
        <p
          :id="concurrencyHintId"
          class="dg-chat-workload__hint"
        >
          {{ t('insight.dataGateway.chatConcurrencyHint') }}
        </p>
      </ElFormItem>
      <ElFormItem :label="t('insight.dataGateway.chatQueueCapacity')">
        <ElInputNumber
          v-model="queueCapacity"
          :aria-label="t('insight.dataGateway.chatQueueCapacity')"
          :aria-describedby="queueCapacityHintId"
          :min="0"
          :max="1000"
          :step="1"
          controls-position="right"
        />
        <p
          :id="queueCapacityHintId"
          class="dg-chat-workload__hint"
        >
          {{ t('insight.dataGateway.chatQueueCapacityHint') }}
        </p>
      </ElFormItem>
    </ElForm>
  </section>
</template>

<style scoped>
.dg-chat-workload {
  margin-top: 20px;
}

.dg-chat-workload__heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.dg-chat-workload__heading p,
.dg-chat-workload__hint {
  margin: 4px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.dg-chat-workload__stats {
  margin-top: 12px;
}

.dg-chat-workload__form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 20px;
  margin-top: 16px;
}

.dg-chat-workload__form :deep(.el-input-number) {
  width: 100%;
}

.dg-chat-workload__form :deep(.el-form-item) {
  margin-bottom: 0;
}

@media (max-width: 680px) {
  .dg-chat-workload__form {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 480px) {
  .dg-chat-workload__heading {
    align-items: stretch;
    flex-direction: column;
  }

  .dg-chat-workload__heading :deep(.el-button) {
    align-self: flex-start;
    min-height: 44px;
  }
}
</style>
