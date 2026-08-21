<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton, ElTag } from 'element-plus'
import { Info } from 'lucide-vue-next'
import { isRemovedRepositoryWithResidualLocation } from '../lib/repositoryResidualState'
import HflPopover from './HflPopover.vue'

const props = defineProps<{
  status: string
  initializationState: string
  label: string
  tagType: 'success' | 'warning' | 'info' | 'primary' | 'danger'
  actionable?: boolean
}>()
const emit = defineEmits<{
  open: []
}>()

const { t } = useI18n()
const popoverRef = ref<InstanceType<typeof HflPopover> | null>(null)
const residualActionRequired = computed(() => isRemovedRepositoryWithResidualLocation({
  status: props.status,
  initialization_state: props.initializationState,
}))
const showRecoveryAction = computed(() => residualActionRequired.value && props.actionable)

function openDetails() {
  popoverRef.value?.hide()
  emit('open')
}
</script>

<template>
  <div
    class="repository-lifecycle-status"
    :class="{ 'repository-lifecycle-status--residual': residualActionRequired }"
  >
    <div class="repository-lifecycle-status__heading">
      <ElTag
        :type="residualActionRequired ? 'warning' : tagType"
        size="small"
      >
        {{ residualActionRequired ? t('repositoriesPage.statusResidualActionRequired') : label }}
      </ElTag>
      <HflPopover
        v-if="showRecoveryAction"
        ref="popoverRef"
        trigger="hover"
        placement="top-start"
        :width="320"
        popper-class="repository-residual-status-popper"
      >
        <template #reference>
          <button
            type="button"
            class="repository-lifecycle-status__info"
            :aria-label="t('repositoriesPage.residualAttentionTitle')"
            @click.stop
          >
            <Info
              :size="14"
              aria-hidden="true"
            />
          </button>
        </template>
        <div class="repository-lifecycle-status__popover">
          <strong>{{ t('repositoriesPage.residualAttentionTitle') }}</strong>
          <p>{{ t('repositoriesPage.residualAttentionDescription') }}</p>
          <ElButton
            link
            type="warning"
            class="repository-lifecycle-status__popover-action"
            @click.stop="openDetails"
          >
            {{ t('repositoriesPage.residualReviewAction') }}
          </ElButton>
        </div>
      </HflPopover>
    </div>
    <span
      v-if="residualActionRequired"
      class="repository-lifecycle-status__context"
    >
      {{ t('repositoriesPage.statusRepositoryRecordRemoved') }}
    </span>
    <ElButton
      v-if="showRecoveryAction"
      link
      type="warning"
      class="repository-lifecycle-status__action"
      @click.stop="openDetails"
    >
      {{ t('repositoriesPage.residualReviewAction') }}
    </ElButton>
  </div>
</template>

<style scoped>
.repository-lifecycle-status {
  display: inline-flex;
  max-width: 100%;
  align-items: flex-start;
}

.repository-lifecycle-status--residual {
  flex-direction: column;
  gap: 4px;
}

.repository-lifecycle-status__heading {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.repository-lifecycle-status__info {
  display: inline-flex;
  width: 18px;
  height: 18px;
  padding: 0;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--el-color-warning-dark-2);
  line-height: 1;
  cursor: help;
}

.repository-lifecycle-status__info:hover {
  background: var(--el-color-warning-light-9);
}

.repository-lifecycle-status__info:focus-visible {
  background: var(--el-color-warning-light-9);
  outline: 2px solid var(--el-color-warning-light-5);
  outline-offset: 1px;
}

.repository-lifecycle-status__context {
  color: var(--color-text-secondary);
  font-size: 11px;
  line-height: 1.35;
}

.repository-lifecycle-status__action {
  align-self: flex-start;
  height: auto;
  padding: 0;
  font-size: 12px;
}

.repository-lifecycle-status__action.el-button,
.repository-lifecycle-status__popover-action.el-button {
  color: var(--el-color-warning-dark-2) !important;
  text-underline-offset: 3px;
}

.repository-lifecycle-status__action.el-button:hover,
.repository-lifecycle-status__action.el-button:focus-visible,
.repository-lifecycle-status__popover-action.el-button:hover,
.repository-lifecycle-status__popover-action.el-button:focus-visible {
  color: var(--el-color-warning-dark-2) !important;
  text-decoration: underline;
}

.repository-lifecycle-status__action.el-button:focus-visible,
.repository-lifecycle-status__popover-action.el-button:focus-visible {
  border-radius: 2px;
  outline: 2px solid var(--el-color-warning-light-5);
  outline-offset: 2px;
}

.repository-lifecycle-status__popover strong {
  display: block;
  color: var(--el-text-color-primary);
  line-height: 1.4;
}

.repository-lifecycle-status__popover p {
  margin: 8px 0 10px;
  color: var(--el-text-color-regular);
  line-height: 1.55;
}

.repository-lifecycle-status__popover-action {
  height: auto;
  padding: 0;
}
</style>
