<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import DangerConfirmDialog from './DangerConfirmDialog.vue'
import {
  buildUpgradeConfirmDialogItems,
  upgradePreviewSkippedCount,
} from '../lib/nodeLifecycleUpgradeConfirm'
import type { NodeOperationBatchPreview } from '../types/nodeLifecycle'

const props = defineProps<{
  modelValue: boolean
  preview: NodeOperationBatchPreview | null
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

const eligibleCount = computed(() => props.preview?.eligible.length ?? 0)
const requestedCount = computed(() => props.preview?.requested ?? 0)
const skippedCount = computed(() =>
  props.preview ? upgradePreviewSkippedCount(props.preview) : 0,
)
const hasSkipped = computed(() => skippedCount.value > 0)

const items = computed(() =>
  props.preview ? buildUpgradeConfirmDialogItems(t, props.preview) : [],
)

const itemsHeading = computed(() =>
  hasSkipped.value
    ? t('nodeLifecycle.confirmUpgradeItemsHeading', {
      selected: requestedCount.value,
      eligible: eligibleCount.value,
      skipped: skippedCount.value,
    })
    : t('nodesPage.selectedCount', { n: requestedCount.value }),
)

const message = computed(() =>
  hasSkipped.value
    ? t('nodeLifecycle.confirmUpgradeAgents', {
      selected: requestedCount.value,
      eligible: eligibleCount.value,
      skipped: skippedCount.value,
    })
    : '',
)

const warning = computed(() => t('nodeLifecycle.confirmImpactUpgrade'))

const confirmationContext = computed(() => JSON.stringify({
  requested: requestedCount.value,
  eligible: props.preview?.eligible.map((item) => item.node_id) || [],
  skipped: skippedCount.value,
}))

function close() {
  visible.value = false
  emit('cancel')
}

function confirm() {
  emit('confirm')
}
</script>

<template>
  <DangerConfirmDialog
    v-model="visible"
    class="hfl-upgrade-confirm"
    width="640px"
    :title="t('nodeLifecycle.upgradeTitle')"
    level="medium"
    :message="message"
    :warning="warning"
    :items="items"
    :items-heading="itemsHeading"
    :item-name-label="t('nodeLifecycle.entityHost')"
    :item-status-label="t('common.dangerItemStatus')"
    :item-details-label="t('common.dangerItemDetails')"
    :confirmation-context="confirmationContext"
    confirm-mode="single"
    :cancel-text="t('common.cancel')"
    :confirm-text="t('nodesPage.actionUpgrade')"
    :confirm-disabled="eligibleCount === 0"
    @confirm="confirm"
    @cancel="close"
  />
</template>
