<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton, ElDialog, ElTable, ElTableColumn, ElTag } from 'element-plus'
import { AlertTriangle } from 'lucide-vue-next'
import {
  buildStopConfirmMessage,
  buildStopConfirmTitle,
  stopConfirmDetailLabel,
} from '../lib/protectionStopConfirmCopy'
import type { ProtectionStopConfirmItem } from '../lib/protectionStopConfirm'
import type { ProtectionStopConfirmKind } from '../composables/useProtectionStopConfirmDialog'
import './backupSourceFlowActionDialog.css'

const props = defineProps<{
  modelValue: boolean
  kind: ProtectionStopConfirmKind
  items: ProtectionStopConfirmItem[]
  loading?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'confirm'): void
  (e: 'cancel'): void
}>()

const { t } = useI18n()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const title = computed(() => (
  props.kind === 'maintenance'
    ? t('ops.task.repositoryCancelConfirmTitle')
    : buildStopConfirmTitle(t, props.kind, props.items.length)
))

const message = computed(() => (
  props.kind === 'maintenance'
    ? t('ops.task.repositoryCancelConfirmMessage')
    : buildStopConfirmMessage(t, props.kind, props.items)
))

const itemsHeading = computed(() =>
  props.kind === 'backup'
    ? t('protection.backupsPage.stopBackupConfirmItemsHeading')
    : props.kind === 'restore'
      ? t('protection.backupsPage.stopRestoreConfirmItemsHeading')
      : t('ops.task.repositoryCancelItemsHeading'),
)

const detailLabel = computed(() => (
  props.kind === 'maintenance'
    ? t('ops.task.repositoryCancelDetailLabel')
    : stopConfirmDetailLabel(t, props.kind)
))

const itemNameLabel = computed(() => (
  props.kind === 'maintenance'
    ? t('ops.task.colName')
    : t('protection.backupsPage.colBackupSource')
))

const confirmLabel = computed(() =>
  props.kind === 'backup'
    ? t('protection.backupsPage.btnConfirmStopBackup')
    : props.kind === 'restore'
      ? t('protection.backupsPage.btnConfirmStopRestore')
      : t('ops.task.repositoryCancelConfirmAction'),
)

const confirmButtonType = computed(() => (props.kind === 'restore' ? 'danger' : 'warning'))

function detailText(item: ProtectionStopConfirmItem) {
  if (props.kind === 'restore') {
    return item.hint || '—'
  }
  return item.hint || '—'
}

function close() {
  visible.value = false
  emit('cancel')
}

function confirm() {
  emit('confirm')
}
</script>

<template>
  <ElDialog
    v-model="visible"
    :title="title"
    :z-index="3700"
    class="hfl-flow-action-dialog hfl-flow-action-dialog--form"
    align-center
    @close="close"
  >
    <div class="hfl-flow-action-dialog__body hfl-flow-action-dialog__body--stop">
      <div class="hfl-flow-action-dialog__lead hfl-flow-action-dialog__lead--stop">
        <div class="hfl-flow-action-dialog__icon-badge hfl-flow-action-dialog__icon-badge--warning">
          <AlertTriangle :size="18" />
        </div>
        <p class="hfl-flow-action-dialog__lead-text">
          {{ message }}
        </p>
      </div>

      <div
        v-if="items.length > 0"
        class="hfl-flow-action-dialog__extras"
      >
        <section class="hfl-flow-action-dialog__section">
          <h3 class="hfl-flow-action-dialog__section-title hfl-flow-action-dialog__section-title--plain">
            {{ itemsHeading }}
          </h3>
          <ElTable
            v-table-overflow-title
            :data="items"
            size="small"
            class="hfl-flow-action-dialog__table hfl-table"
            max-height="260"
          >
            <ElTableColumn
              :label="itemNameLabel"
              min-width="210"
            >
              <template #default="{ row }">
                <div class="hfl-flow-action-dialog__source-cell">
                  <span class="hfl-flow-action-dialog__source-name">{{ row.name }}</span>
                </div>
              </template>
            </ElTableColumn>
            <ElTableColumn
              :label="t('protection.backupsPage.stopConfirmColProgress')"
              width="120"
            >
              <template #default="{ row }">
                <ElTag
                  v-if="row.description"
                  size="small"
                  effect="light"
                  type="warning"
                  class="tabular-nums"
                >
                  {{ row.description }}
                </ElTag>
                <span
                  v-else
                  class="hfl-table-cell-time hfl-empty-mark"
                >—</span>
              </template>
            </ElTableColumn>
            <ElTableColumn
              :label="detailLabel"
              min-width="180"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-time"
                  :class="{ 'hfl-empty-mark': detailText(row) === '—' }"
                >{{ detailText(row) }}</span>
              </template>
            </ElTableColumn>
          </ElTable>
        </section>
      </div>
    </div>

    <template #footer>
      <ElButton
        :disabled="loading"
        @click="close"
      >
        {{ t('common.cancel') }}
      </ElButton>
      <ElButton
        :type="confirmButtonType"
        :loading="loading"
        @click="confirm"
      >
        {{ confirmLabel }}
      </ElButton>
    </template>
  </ElDialog>
</template>
