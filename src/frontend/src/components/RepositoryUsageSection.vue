<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertTriangle, RefreshCw } from 'lucide-vue-next'
import { safeErrorDetailText } from '../lib/errors/details'

const props = defineProps<{
  status?: string
  refreshing: boolean
  error?: string
  lastSuccess: string
}>()
defineEmits<{ refresh: [] }>()
const { t } = useI18n()
const statusLabel = computed(() => props.refreshing
  ? t('repositoriesPage.usageRefreshPending')
  : props.status === 'success'
    ? t('repositoriesPage.usageMeasurementSuccess')
    : props.status === 'failed'
      ? t('repositoriesPage.usageMeasurementFailed')
      : t('repositoriesPage.usageMeasurementPending'))
const statusType = computed(() => props.refreshing ? 'info'
  : props.status === 'success' ? 'success' : props.status === 'failed' ? 'danger' : 'info')
const failureLocaleKeys: Record<string, string> = {
  'Repository owner is unavailable.': 'repositoriesPage.usageOwnerUnavailable',
  'Unable to read repository usage.': 'repositoriesPage.usageReadFailed',
  'Repository is not ready for metrics collection.': 'repositoriesPage.usageRepositoryNotReady',
  'repo.status failed': 'repositoriesPage.usageProbeFailed',
}
const failureReason = computed(() => {
  const error = props.error?.trim() || ''
  const key = failureLocaleKeys[error]
  return key ? t(key) : safeErrorDetailText(error) || t('repositoriesPage.usageRefreshFailed')
})
</script>

<template>
  <section class="hfl-detail-section">
    <h4 class="hfl-detail-section__title repository-usage-section__title">
      {{ t('repositoriesPage.detailFieldRepositoryUsage') }}
      <ElButton
        type="primary"
        plain
        size="small"
        :loading="refreshing"
        :disabled="refreshing"
        @click="$emit('refresh')"
      >
        <RefreshCw :size="14" />
        {{ refreshing ? t('repositoriesPage.usageRefreshPending') : t('repositoriesPage.refreshUsage') }}
      </ElButton>
    </h4>
    <div
      v-if="!refreshing && (status === 'failed' || error)"
      class="repository-usage-section__notice"
      role="status"
    >
      <AlertTriangle
        :size="16"
        class="repository-usage-section__notice-icon"
        aria-hidden="true"
      />
      <div class="repository-usage-section__notice-content">
        <p class="repository-usage-section__notice-reason">
          {{ failureReason }}
        </p>
        <p class="repository-usage-section__notice-help">
          {{ t('repositoriesPage.usageRefreshFailedHelp') }}
        </p>
      </div>
    </div>
    <div class="hfl-detail-grid">
      <slot />
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('repositoriesPage.usageLastUpdated') }}</span>
        <span class="hfl-detail-row__value">{{ lastSuccess }}</span>
      </div>
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('repositoriesPage.usageMeasurementStatus') }}</span>
        <span class="hfl-detail-row__value">
          <ElTag
            :type="statusType"
            size="small"
            effect="light"
          >{{ statusLabel }}</ElTag>
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.repository-usage-section__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.repository-usage-section__title .el-button {
  margin-left: auto;
}
.repository-usage-section__notice {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  margin: 12px 16px;
  background: var(--color-warning-light);
  border: 1px solid var(--color-warning-border);
  border-radius: 8px;
}
.repository-usage-section__notice-icon {
  flex-shrink: 0;
  margin-top: 2px;
  color: var(--color-warning-text);
}
.repository-usage-section__notice-content {
  min-width: 0;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.repository-usage-section__notice-reason {
  margin: 0;
  color: var(--color-text-primary);
  font-size: 13px;
  font-weight: 500;
}
.repository-usage-section__notice-help {
  margin: 4px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
}
</style>
