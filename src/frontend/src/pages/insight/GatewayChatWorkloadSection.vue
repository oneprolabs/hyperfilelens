<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Pencil } from 'lucide-vue-next'
import type { GatewayChatWorkload } from '../../lib/lensApi'

const props = defineProps<{
  workload: GatewayChatWorkload
}>()

const { t } = useI18n()
const concurrency = ref<number | undefined>(1)
const queueCapacity = ref<number | undefined>(10)
const editingConcurrency = ref(false)
const editingQueueCapacity = ref(false)

function resetDraft() {
  concurrency.value = Number(props.workload.chat_prepare_concurrency)
  queueCapacity.value = Number(props.workload.chat_queue_capacity)
  editingConcurrency.value = false
  editingQueueCapacity.value = false
}

watch(
  () => [
    props.workload.chat_prepare_concurrency,
    props.workload.chat_queue_capacity,
  ] as const,
  resetDraft,
  { immediate: true },
)

const hasChanges = computed(() => (
  Number(concurrency.value) !== Number(props.workload.chat_prepare_concurrency)
  || Number(queueCapacity.value) !== Number(props.workload.chat_queue_capacity)
))

const valid = computed(() => (
  Number.isInteger(Number(concurrency.value))
  && Number(concurrency.value) >= 1
  && Number(concurrency.value) <= 32
  && Number.isInteger(Number(queueCapacity.value))
  && Number(queueCapacity.value) >= 0
  && Number(queueCapacity.value) <= 1000
))

function beginConcurrencyEdit() {
  concurrency.value = Number(props.workload.chat_prepare_concurrency)
  editingConcurrency.value = true
}

function beginQueueCapacityEdit() {
  queueCapacity.value = Number(props.workload.chat_queue_capacity)
  editingQueueCapacity.value = true
}

function discardChanges() {
  resetDraft()
}

function getDraftSettings() {
  if (!hasChanges.value || !valid.value) return null
  return {
    chat_prepare_concurrency: Number(concurrency.value),
    chat_queue_capacity: Number(queueCapacity.value),
  }
}

defineExpose({
  hasChanges,
  valid,
  discardChanges,
  getDraftSettings,
})
</script>

<template>
  <section class="hfl-detail-section dg-chat-workload">
    <h4 class="hfl-detail-section__title">
      {{ t('insight.dataGateway.chatWorkloadTitle') }}
    </h4>

    <div class="hfl-detail-grid">
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('insight.dataGateway.chatRunning') }}</span>
        <span class="hfl-detail-row__value">{{ workload.active_chat_preparations }}</span>
      </div>
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('insight.dataGateway.chatQueued') }}</span>
        <span class="hfl-detail-row__value">{{ workload.queued_chat_preparations }}</span>
      </div>
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('insight.dataGateway.chatConcurrency') }}</span>
        <span class="hfl-detail-row__value hfl-detail-row__value--editable">
          <template v-if="editingConcurrency">
            <ElInputNumber
              v-model="concurrency"
              size="small"
              autofocus
              class="hfl-detail-inline-edit__input hfl-detail-inline-edit__input--number"
              :min="1"
              :max="32"
              :precision="0"
              :step="1"
              :controls="false"
              :aria-label="t('insight.dataGateway.chatConcurrency')"
              @keyup.escape="editingConcurrency = false; concurrency = Number(workload.chat_prepare_concurrency)"
            />
          </template>
          <template v-else>
            <span class="hfl-detail-row__text">{{ concurrency }}</span>
            <ElButton
              text
              circle
              size="small"
              class="hfl-detail-row__edit"
              :title="t('protection.sourceResources.editBtn')"
              @click="beginConcurrencyEdit"
            >
              <Pencil :size="13" />
            </ElButton>
          </template>
        </span>
      </div>
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('insight.dataGateway.chatQueueCapacity') }}</span>
        <span class="hfl-detail-row__value hfl-detail-row__value--editable">
          <template v-if="editingQueueCapacity">
            <ElInputNumber
              v-model="queueCapacity"
              size="small"
              autofocus
              class="hfl-detail-inline-edit__input hfl-detail-inline-edit__input--number"
              :min="0"
              :max="1000"
              :precision="0"
              :step="1"
              :controls="false"
              :aria-label="t('insight.dataGateway.chatQueueCapacity')"
              @keyup.escape="editingQueueCapacity = false; queueCapacity = Number(workload.chat_queue_capacity)"
            />
          </template>
          <template v-else>
            <span class="hfl-detail-row__text">{{ queueCapacity }}</span>
            <ElButton
              text
              circle
              size="small"
              class="hfl-detail-row__edit"
              :title="t('protection.sourceResources.editBtn')"
              @click="beginQueueCapacityEdit"
            >
              <Pencil :size="13" />
            </ElButton>
          </template>
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.dg-chat-workload {
  margin-top: 20px;
}

.dg-chat-workload :deep(.hfl-detail-inline-edit__input--number) {
  width: 120px;
}

.dg-chat-workload :deep(.hfl-detail-inline-edit__input--number .el-input__wrapper) {
  width: 100%;
}
</style>
