<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bot, Search } from 'lucide-vue-next'
import { DETAIL_EMPTY } from '../../../lib/nodeInventoryDisplay'
import type { LensCopilotAssistant } from '../../../lib/lensApi'

const props = defineProps<{
  open: boolean
  assistants: LensCopilotAssistant[]
  selectedUuid: string | null
  title?: string
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  confirm: [assistantUuid: string]
}>()

const { t } = useI18n()
const query = ref('')
const draftUuid = ref<string | null>(null)

function isAssistantAvailable(row: LensCopilotAssistant) {
  if (row.status && row.status !== 'active') return false
  const ksStatus = row.knowledge_source_status
  if (!ksStatus) return true
  return ksStatus === 'ready' || ksStatus === 'degraded'
}

const availableAssistants = computed(() => props.assistants.filter(isAssistantAvailable))

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  const base = availableAssistants.value
  if (!q) return base
  return base.filter((row) => {
    const hay = [
      displayName(row),
      row.slug,
      row.knowledge_source_name,
      row.gateway_name,
      row.selected_task,
      row.selected_dir,
      row.source_path,
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return hay.includes(q)
  })
})

function displayName(row: LensCopilotAssistant) {
  const name = row.name?.trim()
  if (name) return name
  const slug = row.slug?.trim()
  if (slug) return slug
  return t('insight.copilot.assistantPickerUnnamed')
}

function ksName(row: LensCopilotAssistant) {
  return row.knowledge_source_name?.trim() || DETAIL_EMPTY
}

function gatewayName(row: LensCopilotAssistant) {
  return row.gateway_name?.trim() || DETAIL_EMPTY
}

function directoryPath(row: LensCopilotAssistant) {
  return row.selected_dir?.trim() || row.source_path?.trim() || DETAIL_EMPTY
}

function ksStatusLabel(status: string | null | undefined) {
  if (status === 'ready') return t('insight.kb.statusReady')
  if (status === 'degraded') return t('insight.kb.statusDegraded')
  if (status === 'syncing') return t('insight.kb.statusSyncing')
  if (status === 'error') return t('insight.kb.statusError')
  if (status === 'paused') return t('insight.kb.statusPaused')
  return status || '—'
}

function ksStatusPillClass(status: string | null | undefined) {
  if (status === 'ready') return 'hfl-state-pill hfl-state-pill--enabled'
  if (status === 'degraded') return 'hfl-state-pill hfl-state-pill--warning'
  if (status === 'error') return 'hfl-state-pill hfl-state-pill--danger'
  if (status === 'paused') return 'hfl-state-pill hfl-state-pill--disabled'
  return 'hfl-state-pill hfl-state-pill--info'
}

function openChanged(open: boolean) {
  if (open) {
    draftUuid.value = props.selectedUuid
    query.value = ''
  }
  emit('update:open', open)
}

function confirm() {
  if (!draftUuid.value) return
  emit('confirm', draftUuid.value)
  emit('update:open', false)
}
</script>

<template>
  <ElDialog
    :model-value="open"
    class="source-action-dialog copilot-assistant-picker-dialog"
    :title="title || t('insight.copilot.pickAssistant')"
    width="640px"
    align-center
    append-to-body
    destroy-on-close
    @update:model-value="openChanged"
  >
    <p class="source-action-dialog__hint">
      {{ t('insight.copilot.pickAssistantHint') }}
    </p>

    <div class="copilot-assistant-picker-dialog__toolbar">
      <ElInput
        v-model="query"
        clearable
        class="copilot-assistant-picker-dialog__search"
        :placeholder="t('insight.copilot.assistantSearchPlaceholder')"
      >
        <template #prefix>
          <Search
            :size="16"
            class="hfl-list-search__icon"
          />
        </template>
      </ElInput>
    </div>

    <div
      class="copilot-assistant-picker-dialog__list"
      role="listbox"
      :aria-label="t('insight.copilot.pickAssistant')"
    >
      <button
        v-for="row in filtered"
        :key="row.uuid"
        type="button"
        role="option"
        class="copilot-assistant-picker-card"
        :class="{ 'is-active': draftUuid === row.uuid }"
        :aria-selected="draftUuid === row.uuid"
        @click="draftUuid = row.uuid"
      >
        <span
          class="copilot-assistant-picker-card__indicator"
          aria-hidden="true"
        />
        <span class="copilot-assistant-picker-card__body">
          <span class="copilot-assistant-picker-card__head">
            <span class="copilot-assistant-picker-card__identity">
              <span
                class="copilot-assistant-picker-card__type"
                aria-hidden="true"
              >
                <Bot :size="15" />
              </span>
              <span
                class="copilot-assistant-picker-card__title"
                :title="displayName(row)"
              >
                {{ displayName(row) }}
              </span>
            </span>
            <span
              v-if="row.knowledge_source_status"
              class="copilot-assistant-picker-card__badges"
            >
              <span :class="ksStatusPillClass(row.knowledge_source_status)">
                {{ ksStatusLabel(row.knowledge_source_status) }}
              </span>
            </span>
          </span>

          <span class="copilot-assistant-picker-card__details">
            <span class="copilot-assistant-picker-card__detail-row copilot-assistant-picker-card__detail-row--split">
              <span class="copilot-assistant-picker-card__pair">
                <span class="copilot-assistant-picker-card__label">
                  {{ t('insight.copilot.assistantPickerLabelKs') }}:
                </span>
                <span
                  class="copilot-assistant-picker-card__value"
                  :class="{ 'copilot-assistant-picker-card__value--empty': ksName(row) === DETAIL_EMPTY }"
                  :title="ksName(row) === DETAIL_EMPTY ? undefined : ksName(row)"
                >
                  {{ ksName(row) }}
                </span>
              </span>
              <span class="copilot-assistant-picker-card__pair">
                <span class="copilot-assistant-picker-card__label">
                  {{ t('insight.copilot.assistantPickerLabelGateway') }}:
                </span>
                <span
                  class="copilot-assistant-picker-card__value"
                  :class="{ 'copilot-assistant-picker-card__value--empty': gatewayName(row) === DETAIL_EMPTY }"
                  :title="gatewayName(row) === DETAIL_EMPTY ? undefined : gatewayName(row)"
                >
                  {{ gatewayName(row) }}
                </span>
              </span>
            </span>
            <span class="copilot-assistant-picker-card__detail-row">
              <span class="copilot-assistant-picker-card__pair copilot-assistant-picker-card__pair--full">
                <span class="copilot-assistant-picker-card__label">
                  {{ t('insight.copilot.assistantPickerLabelDirectory') }}:
                </span>
                <span
                  class="copilot-assistant-picker-card__value copilot-assistant-picker-card__value--mono"
                  :class="{ 'copilot-assistant-picker-card__value--empty': directoryPath(row) === DETAIL_EMPTY }"
                  :title="directoryPath(row) === DETAIL_EMPTY ? undefined : directoryPath(row)"
                >
                  {{ directoryPath(row) }}
                </span>
              </span>
            </span>
          </span>
        </span>
      </button>

      <div
        v-if="!availableAssistants.length"
        class="copilot-assistant-picker-dialog__empty"
      >
        {{ t('insight.copilot.assistantPickerAvailableEmpty') }}
      </div>
      <div
        v-else-if="!filtered.length"
        class="copilot-assistant-picker-dialog__empty"
      >
        {{ t('insight.copilot.assistantSearchEmpty') }}
      </div>
    </div>

    <template #footer>
      <ElButton @click="emit('update:open', false)">
        {{ t('common.cancel') }}
      </ElButton>
      <ElButton
        type="primary"
        :disabled="!draftUuid"
        @click="confirm"
      >
        {{ t('insight.copilot.btnStartChat') }}
      </ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.copilot-assistant-picker-dialog__toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
}

.copilot-assistant-picker-dialog__search {
  flex: 1 1 auto;
  min-width: 0;
}

.copilot-assistant-picker-dialog__search :deep(.el-input__wrapper) {
  border-radius: 8px;
  box-shadow: 0 0 0 1px rgba(203, 213, 225, 0.95) inset;
}

.copilot-assistant-picker-dialog__search :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px rgb(29 78 216) inset;
}

.copilot-assistant-picker-dialog__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 14px;
  max-height: min(56vh, 480px);
  overflow: auto;
  padding-right: 2px;
}

.copilot-assistant-picker-card {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  min-width: 0;
  padding: 14px;
  border: 1px solid rgba(203, 213, 225, 0.95);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.96);
  text-align: left;
  cursor: pointer;
  transition:
    border-color 0.15s ease,
    background 0.15s ease,
    box-shadow 0.15s ease;
}

.copilot-assistant-picker-card:hover {
  border-color: rgba(148, 163, 184, 0.95);
  background: rgba(248, 250, 252, 0.98);
}

.copilot-assistant-picker-card.is-active {
  border-color: rgb(29 78 216);
  background: linear-gradient(180deg, rgba(219, 234, 254, 0.88) 0%, rgba(239, 246, 255, 0.92) 100%);
  box-shadow:
    0 0 0 1px rgba(29, 78, 216, 0.12),
    inset 0 0 0 1px rgba(29, 78, 216, 0.06);
}

.copilot-assistant-picker-card__indicator {
  width: 10px;
  height: 10px;
  margin-top: 6px;
  flex-shrink: 0;
  border-radius: 999px;
  border: 2px solid rgba(148, 163, 184, 0.9);
  transition:
    border-color 0.15s ease,
    background 0.15s ease;
}

.copilot-assistant-picker-card.is-active .copilot-assistant-picker-card__indicator {
  border-color: rgb(29 78 216);
  background: rgb(29 78 216);
}

.copilot-assistant-picker-card__body {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 10px;
}

.copilot-assistant-picker-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.copilot-assistant-picker-card__identity {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1 1 auto;
}

.copilot-assistant-picker-card__type {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  color: rgb(100 116 139);
}

.copilot-assistant-picker-card.is-active .copilot-assistant-picker-card__type {
  color: rgb(29 78 216);
}

.copilot-assistant-picker-card__title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 650;
  line-height: 1.35;
  color: rgb(30 41 59);
}

.copilot-assistant-picker-card__badges {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
}

.copilot-assistant-picker-card__details {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.copilot-assistant-picker-card__detail-row {
  display: flex;
  min-width: 0;
}

.copilot-assistant-picker-card__detail-row--split {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 16px;
}

.copilot-assistant-picker-card__pair {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.copilot-assistant-picker-card__pair--full {
  width: 100%;
}

.copilot-assistant-picker-card__label {
  flex: 0 0 auto;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.45;
  color: rgb(100 116 139);
  white-space: nowrap;
}

.copilot-assistant-picker-card__value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  line-height: 1.45;
  color: rgb(51 65 85);
}

.copilot-assistant-picker-card__value--mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
}

.copilot-assistant-picker-card__value--empty {
  color: rgb(148 163 184);
}

.copilot-assistant-picker-dialog__empty {
  padding: 28px 12px;
  border: 1px dashed rgba(203, 213, 225, 0.95);
  border-radius: 10px;
  text-align: center;
  font-size: 13px;
  line-height: 1.6;
  color: rgb(100 116 139);
  background: rgba(248, 250, 252, 0.72);
}
</style>

<style>
.copilot-assistant-picker-dialog.el-dialog.source-action-dialog .el-dialog__body {
  padding-top: 8px;
}

.copilot-assistant-picker-dialog.el-dialog.source-action-dialog .el-dialog__footer {
  padding-top: 12px;
}
</style>
