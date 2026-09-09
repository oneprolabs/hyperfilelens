<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton, ElDialog } from 'element-plus'
import { AlertTriangle, ArrowUpCircle } from 'lucide-vue-next'
import {
  buildUpgradeConfirmSkipGroups,
  upgradePreviewSkippedCount,
} from '../lib/nodeLifecycleUpgradeConfirm'
import type { NodeOperationBatchPreview } from '../types/nodeLifecycle'
import './backupSourceFlowActionDialog.css'

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

const message = computed(() =>
  t('nodeLifecycle.confirmUpgradeAgents', {
    selected: requestedCount.value,
    eligible: eligibleCount.value,
    skipped: skippedCount.value,
  }),
)

const skipGroups = computed(() =>
  props.preview ? buildUpgradeConfirmSkipGroups(t, props.preview) : [],
)

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
    :title="t('nodeLifecycle.upgradeTitle')"
    class="hfl-flow-action-dialog hfl-flow-action-dialog--confirm hfl-upgrade-confirm"
    align-center
    @close="close"
  >
    <div class="hfl-flow-action-dialog__body hfl-flow-action-dialog__body--lead-only">
      <div class="hfl-flow-action-dialog__lead">
        <div
          class="hfl-flow-action-dialog__icon-badge"
          :class="hasSkipped
            ? 'hfl-flow-action-dialog__icon-badge--warning'
            : 'hfl-flow-action-dialog__icon-badge--primary'"
        >
          <AlertTriangle
            v-if="hasSkipped"
            :size="18"
          />
          <ArrowUpCircle
            v-else
            :size="18"
          />
        </div>
        <div class="hfl-flow-action-dialog__icon-stack-content">
          <div
            class="hfl-upgrade-confirm__stats"
            role="status"
            :aria-label="message"
          >
            <div class="hfl-upgrade-confirm__stat">
              <span>{{ t('nodeLifecycle.confirmStatSelected') }}</span>
              <strong>{{ requestedCount }}</strong>
            </div>
            <div class="hfl-upgrade-confirm__stat hfl-upgrade-confirm__stat--upgrade">
              <span>{{ t('nodeLifecycle.confirmStatUpgrade') }}</span>
              <strong>{{ eligibleCount }}</strong>
            </div>
            <div
              v-if="hasSkipped"
              class="hfl-upgrade-confirm__stat hfl-upgrade-confirm__stat--skipped"
            >
              <span>{{ t('nodeLifecycle.confirmStatSkipped') }}</span>
              <strong>{{ skippedCount }}</strong>
            </div>
          </div>
          <p class="hfl-upgrade-confirm__impact">
            {{ t('nodeLifecycle.confirmImpactUpgrade') }}
          </p>
          <div
            v-if="skipGroups.length"
            class="hfl-upgrade-confirm__groups"
            :aria-labelledby="'hfl-upgrade-confirm-skipped-title'"
          >
            <p
              id="hfl-upgrade-confirm-skipped-title"
              class="hfl-upgrade-confirm__groups-title"
            >
              {{ t('nodeLifecycle.confirmSkippedTitle') }}
            </p>
            <div
              class="hfl-upgrade-confirm__group-list"
              role="list"
            >
              <section
                v-for="group in skipGroups"
                :key="group.key"
                class="hfl-upgrade-confirm__group"
                role="listitem"
              >
                <p class="hfl-upgrade-confirm__group-title">
                  {{ group.title }}
                </p>
                <div class="hfl-upgrade-confirm__names">
                  <span
                    v-for="name in group.names"
                    :key="name.id"
                    class="hfl-upgrade-confirm__name"
                  >
                    {{ name.name }}
                  </span>
                </div>
                <p
                  v-for="detail in group.details || []"
                  :key="detail"
                  class="hfl-upgrade-confirm__guidance"
                >
                  {{ detail }}
                </p>
                <p
                  v-if="group.guidance"
                  class="hfl-upgrade-confirm__guidance"
                >
                  <strong>{{ t('nodeLifecycle.confirmRecommendation') }}</strong>
                  {{ group.guidance }}
                </p>
              </section>
            </div>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <ElButton @click="close">
        {{ t('common.cancel') }}
      </ElButton>
      <ElButton
        type="primary"
        @click="confirm"
      >
        {{ t('nodeLifecycle.confirmUpgradeCount', { n: eligibleCount }) }}
      </ElButton>
    </template>
  </ElDialog>
</template>
