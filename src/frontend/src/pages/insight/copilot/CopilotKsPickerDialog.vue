<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { BookOpen, FolderOpen, Search } from 'lucide-vue-next'
import type { LensKnowledgeSource } from '../../../lib/lensApi'

const props = defineProps<{
  open: boolean
  knowledgeSources: LensKnowledgeSource[]
  selectedId: number | null
  title?: string
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  confirm: [ksId: number]
}>()

const { t } = useI18n()
const query = ref('')
const draftId = ref<number | null>(null)

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return props.knowledgeSources
  return props.knowledgeSources.filter((row) => {
    const hay = [row.name, row.source_path, row.gateway_name, row.ingest_summary]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return hay.includes(q)
  })
})

function statusLabel(status: string) {
  if (status === 'ready') return t('insight.kb.statusReady')
  if (status === 'degraded') return t('insight.kb.statusDegraded')
  if (status === 'syncing') return t('insight.kb.statusSyncing')
  if (status === 'learning' || status === 'provisioning') return t('insight.kb.statusSyncing')
  if (status === 'error') return t('insight.kb.statusError')
  return t('insight.kb.statusPaused')
}

function statusPillClass(status: string) {
  if (status === 'ready') return 'hfl-state-pill hfl-state-pill--enabled'
  if (status === 'degraded') return 'hfl-state-pill hfl-state-pill--warning'
  if (status === 'error') return 'hfl-state-pill hfl-state-pill--danger'
  if (status === 'paused') return 'hfl-state-pill hfl-state-pill--disabled'
  return 'hfl-state-pill hfl-state-pill--info'
}

function isBackupSource(row: LensKnowledgeSource) {
  return Boolean(row.backup_source_snapshot_id)
}

function openChanged(open: boolean) {
  if (open) {
    draftId.value = props.selectedId
    query.value = ''
  }
  emit('update:open', open)
}

function confirm() {
  if (draftId.value == null) return
  emit('confirm', draftId.value)
  emit('update:open', false)
}
</script>

<template>
  <ElDialog
    :model-value="open"
    class="source-action-dialog copilot-ks-picker-dialog"
    :title="title || t('insight.copilot.pickKnowledgeSource')"
    width="560px"
    align-center
    append-to-body
    destroy-on-close
    @update:model-value="openChanged"
  >
    <p class="source-action-dialog__hint">
      {{ t('insight.copilot.pickKnowledgeSourceHint') }}
    </p>

    <ElInput
      v-model="query"
      clearable
      class="copilot-ks-picker-dialog__search"
      :placeholder="t('insight.copilot.contextSearchPlaceholder')"
    >
      <template #prefix>
        <Search
          :size="16"
          class="hfl-list-search__icon"
        />
      </template>
    </ElInput>

    <div
      class="copilot-ks-picker-dialog__list"
      role="listbox"
      :aria-label="t('insight.copilot.pickKnowledgeSource')"
    >
      <button
        v-for="row in filtered"
        :key="row.id"
        type="button"
        role="option"
        class="copilot-ks-picker-card"
        :class="{ 'is-active': draftId === row.id }"
        :aria-selected="draftId === row.id"
        @click="draftId = row.id"
      >
        <span
          class="copilot-ks-picker-card__indicator"
          aria-hidden="true"
        />
        <span class="copilot-ks-picker-card__body">
          <span class="copilot-ks-picker-card__head">
            <span
              class="copilot-ks-picker-card__type"
              aria-hidden="true"
            >
              <BookOpen
                v-if="isBackupSource(row)"
                :size="14"
              />
              <FolderOpen
                v-else
                :size="14"
              />
            </span>
            <span class="copilot-ks-picker-card__title">{{ row.name }}</span>
          </span>
          <span class="copilot-ks-picker-card__meta">
            <span class="copilot-ks-picker-card__gateway">{{ row.gateway_name }}</span>
            <span class="copilot-ks-picker-card__sep">·</span>
            <span
              class="copilot-ks-picker-card__path"
              :title="row.source_path"
            >{{ row.source_path }}</span>
          </span>
          <span class="copilot-ks-picker-card__tags">
            <span :class="statusPillClass(row.status)">{{ statusLabel(row.status) }}</span>
            <span
              v-if="row.linked_version_mode === 'latest'"
              class="hfl-state-pill hfl-state-pill--disabled"
            >
              {{ t('insight.kb.versionLatest') }}
            </span>
          </span>
        </span>
      </button>

      <div
        v-if="!filtered.length"
        class="copilot-ks-picker-dialog__empty"
      >
        {{ t('insight.copilot.contextSearchEmpty') }}
      </div>
    </div>

    <template #footer>
      <ElButton @click="emit('update:open', false)">
        {{ t('common.cancel') }}
      </ElButton>
      <ElButton
        type="primary"
        :disabled="draftId == null"
        @click="confirm"
      >
        {{ t('insight.copilot.btnStartChat') }}
      </ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.copilot-ks-picker-dialog__search {
  width: 100%;
}

.copilot-ks-picker-dialog__search :deep(.el-input__wrapper) {
  border-radius: 8px;
  box-shadow: 0 0 0 1px rgba(203, 213, 225, 0.95) inset;
}

.copilot-ks-picker-dialog__search :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px rgb(29 78 216) inset;
}

.copilot-ks-picker-dialog__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 14px;
  max-height: min(52vh, 420px);
  overflow: auto;
  padding-right: 2px;
}

.copilot-ks-picker-card {
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

.copilot-ks-picker-card:hover {
  border-color: rgba(148, 163, 184, 0.95);
  background: rgba(248, 250, 252, 0.98);
}

.copilot-ks-picker-card.is-active {
  border-color: rgb(29 78 216);
  background: linear-gradient(180deg, rgba(219, 234, 254, 0.88) 0%, rgba(239, 246, 255, 0.92) 100%);
  box-shadow: 0 0 0 1px rgba(29, 78, 216, 0.08);
}

.copilot-ks-picker-card__indicator {
  width: 10px;
  height: 10px;
  margin-top: 5px;
  flex-shrink: 0;
  border-radius: 999px;
  border: 2px solid rgba(148, 163, 184, 0.9);
  transition:
    border-color 0.15s ease,
    background 0.15s ease;
}

.copilot-ks-picker-card.is-active .copilot-ks-picker-card__indicator {
  border-color: rgb(29 78 216);
  background: rgb(29 78 216);
}

.copilot-ks-picker-card__body {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 6px;
}

.copilot-ks-picker-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.copilot-ks-picker-card__type {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  color: rgb(100 116 139);
}

.copilot-ks-picker-card.is-active .copilot-ks-picker-card__type {
  color: rgb(29 78 216);
}

.copilot-ks-picker-card__title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 650;
  color: rgb(30 41 59);
}

.copilot-ks-picker-card__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: 12px;
  line-height: 1.5;
  color: rgb(100 116 139);
}

.copilot-ks-picker-card__gateway {
  flex: 0 0 auto;
  max-width: 38%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.copilot-ks-picker-card__sep {
  flex: 0 0 auto;
  color: rgb(148 163 184);
}

.copilot-ks-picker-card__path {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
}

.copilot-ks-picker-card__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.copilot-ks-picker-dialog__empty {
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
.copilot-ks-picker-dialog.el-dialog.source-action-dialog .el-dialog__body {
  padding-top: 8px;
}

.copilot-ks-picker-dialog.el-dialog.source-action-dialog .el-dialog__footer {
  padding-top: 12px;
}
</style>
