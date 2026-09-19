<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RefreshCw } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import {
  fetchCurrentLicense,
  fetchEffectiveQuotaUsage,
  type EffectiveQuotaUsage,
  type LicenseRecord,
  type LicenseUsage,
} from '../../lib/subscriptionApi'
import {
  formatQuotaBytes,
  quotaDefsForSubscription,
  quotaDisplayValue,
  quotaUsagePercent,
  SUBSCRIPTION_QUOTA_FALLBACK_LIMITS,
} from '../../lib/licenseQuotaDisplay'
import { isAbortError } from '../../lib/api'

const FALLBACK_LIMITS = SUBSCRIPTION_QUOTA_FALLBACK_LIMITS

const { t } = useI18n()

const loading = ref(false)
const quotaAvailable = ref(false)
const currentLicense = ref<LicenseRecord | null>(null)
const instanceShared = ref(false)
const usage = ref<LicenseUsage>({})
const limits = ref<Record<string, number>>({ ...FALLBACK_LIMITS })
const effectiveQuotaByKey = ref<Record<string, EffectiveQuotaUsage>>({})

const limitItems = computed(() =>
  quotaDefsForSubscription().map((def) => {
    const effective = effectiveQuotaByKey.value[def.limitKey]
    const used = quotaDisplayValue(
      effective?.used ?? getUsage(def.usageKey),
      def.divisor,
    )
    const limit = quotaDisplayValue(
      effective?.limit ?? getLimit(def.limitKey),
      def.divisor,
    )
    const overLimit = effective?.usage_status === 'exceeded' || (limit >= 0 && used > limit)
    const unlimitedLabel = t('settings.subscription.unlimited')
    return {
      key: def.key,
      label: t(def.labelKey),
      used,
      limit,
      displayUsed: def.formatBytes
        ? formatQuotaBytes(used, unlimitedLabel)
        : `${used}${def.suffix ? ` ${def.suffix}` : ''}`,
      displayLimit: def.formatBytes
        ? formatQuotaBytes(limit, unlimitedLabel)
        : `${formatLimit(limit)}${def.suffix && limit >= 0 ? ` ${def.suffix}` : ''}`,
      usagePercent: effective?.usage_percent,
      usageStatus: effective?.usage_status,
      overLimit,
    }
  }),
)

function formatLimit(value?: number) {
  if (value === undefined || value === null) return '—'
  if (value < 0) return t('settings.subscription.unlimited')
  return String(value)
}

function getLimit(key: string): number {
  // A tenant covered by an instance license must use its effective organization
  // limits rather than the raw instance-license fields.
  if (!instanceShared.value) {
    const fromLicense = currentLicense.value as Record<string, unknown> | null
    if (fromLicense && key in fromLicense) return Number(fromLicense[key]) || 0
  }
  return limits.value[key] ?? FALLBACK_LIMITS[key] ?? 0
}

function getUsage(key: string): number {
  const value = usage.value[key]
  return typeof value === 'number' ? value : 0
}

function progressToneClass(item: (typeof limitItems.value)[number]) {
  if (item.overLimit) return 'is-over-limit'
  if (item.usageStatus === 'warning' || item.usageStatus === 'exhausted' || item.usageStatus === 'unknown') return 'is-warning'
  if (item.limit >= 0 && quotaUsagePercent(item.used, item.limit) >= 80) return 'is-warning'
  return ''
}

function effectiveProgressPercent(item: (typeof limitItems.value)[number]) {
  if (item.usageStatus === 'unknown') return 0
  if (item.usagePercent == null) return quotaUsagePercent(item.used, item.limit)
  return Math.min(100, Math.max(0, Math.round(item.usagePercent)))
}

function progressLabel(item: (typeof limitItems.value)[number], percentage: number) {
  return item.overLimit ? t('settings.subscription.overLimit') : `${percentage}%`
}

async function loadAll() {
  loading.value = true
  try {
    const current = await fetchCurrentLicense()
    quotaAvailable.value = true
    usage.value = current.usage || {}
    limits.value = { ...FALLBACK_LIMITS, ...(current.limits || {}) }
    instanceShared.value = Boolean(current.instance_shared)
    currentLicense.value = current.license || null
    effectiveQuotaByKey.value = {}

    // Community hard-enforces its built-in limits in Host, but the effective
    // quota endpoint is an EE governance endpoint and is not mounted there.
    if (current.enforcement_enabled && current.entitlement_source !== 'builtin_community') {
      try {
        const effective = await fetchEffectiveQuotaUsage()
        effectiveQuotaByKey.value = Object.fromEntries(
          (effective.quota_usage || []).map((row) => [row.key, row]),
        )
      } catch (error: unknown) {
        if (isAbortError(error)) throw error
        // Keep the compatible limits and usage returned by the base endpoint.
      }
    }
  } catch (error: unknown) {
    if (isAbortError(error)) return
    const message = error && typeof error === 'object' && 'message' in error
      ? String((error as { message?: unknown }).message || '').trim()
      : ''
    ElMessage.error({
      message: message || t('settings.subscription.loadFailed'),
      grouping: true,
    })
    currentLicense.value = null
    instanceShared.value = false
    quotaAvailable.value = false
    usage.value = {}
    limits.value = { ...FALLBACK_LIMITS }
    effectiveQuotaByKey.value = {}
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <div
    v-loading="loading"
    class="subscription-page"
  >
    <section class="subscription-section">
      <header class="subscription-section__header">
        <h2 class="subscription-section__title">
          {{ t('settings.subscription.limitsAndUsage') }}
        </h2>
        <ElButton
          text
          class="subscription-section__refresh"
          :title="t('common.refresh')"
          :aria-label="t('common.refresh')"
          :disabled="loading"
          @click="loadAll"
        >
          <RefreshCw
            :size="16"
            :class="{ 'is-spinning': loading }"
          />
        </ElButton>
      </header>

      <div
        v-if="quotaAvailable"
        class="subscription-quota-grid"
      >
        <div
          v-for="item in limitItems"
          :key="item.key"
          class="subscription-quota-item"
        >
          <span class="subscription-quota-item__label">
            {{ item.label }}
          </span>
          <span class="subscription-quota-item__numbers">
            {{ item.displayUsed }} / {{ item.displayLimit }}
          </span>
          <ElProgress
            class="subscription-quota-item__progress"
            :class="progressToneClass(item)"
            :percentage="effectiveProgressPercent(item)"
            :format="(percentage) => progressLabel(item, percentage)"
            :show-text="item.limit >= 0 && item.usageStatus !== 'unknown'"
          />
        </div>
      </div>
      <div
        v-else
        class="subscription-quota-empty"
        role="status"
        aria-live="polite"
      >
        <span>{{ t('dashboard.quotaUnavailable') }}</span>
        <ElButton
          text
          size="small"
          :disabled="loading"
          @click="loadAll"
        >
          {{ t('common.retry') }}
        </ElButton>
      </div>
    </section>
  </div>
</template>

<style scoped>
.subscription-page {
  width: 100%;
  min-width: 0;
  font-weight: 400;
}

.subscription-section {
  padding-bottom: 24px;
}

.subscription-section__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.subscription-section__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.subscription-section__refresh {
  padding: 4px;
}

.subscription-quota-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.subscription-quota-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 96px;
  padding: 16px;
  border: 1px solid var(--color-border-light, #e4e7ed);
  border-radius: 12px;
  background: rgb(248 250 252 / 0.5);
  color: var(--color-text-secondary, #606266);
  font-size: 13px;
}

@media (max-width: 960px) {
  .subscription-quota-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .subscription-quota-grid {
    grid-template-columns: 1fr;
  }
}

.subscription-quota-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  column-gap: 8px;
  row-gap: 8px;
  align-items: center;
  padding: 12px;
  border: 1px solid var(--color-border-light, #e4e7ed);
  border-radius: 12px;
  background: rgb(248 250 252 / 0.5);
}

.subscription-quota-item__label {
  grid-column: 1;
  grid-row: 1;
  font-size: 12px;
  color: var(--color-text-secondary, #606266);
}

.subscription-quota-item__numbers {
  grid-column: 2;
  grid-row: 1;
  font-size: 12px;
  color: var(--color-text-primary, #303133);
  white-space: nowrap;
  text-align: right;
}

.subscription-quota-item__progress {
  grid-column: 1 / -1;
  grid-row: 2;
  display: grid;
  grid-template-columns: subgrid;
  align-items: center;
}

.subscription-quota-item__progress :deep(.el-progress-bar) {
  grid-column: 1;
  min-width: 0;
  flex-grow: unset;
  margin-right: 0;
  padding-right: 0;
}

.subscription-quota-item__progress :deep(.el-progress__text) {
  grid-column: 2;
  justify-self: end;
  min-width: 0;
  width: auto;
  margin-left: 0;
  font-size: 12px;
  line-height: 1;
  text-align: right;
}

.subscription-quota-item__progress.is-over-limit :deep(.el-progress-bar__inner) {
  background-color: var(--el-color-warning);
}

.subscription-quota-item__progress.is-warning :deep(.el-progress-bar__inner) {
  background-color: var(--el-color-warning);
}

.subscription-quota-item__progress.is-over-limit :deep(.el-progress__text) {
  color: var(--el-color-warning);
  font-weight: 600;
}

.subscription-quota-item__progress.is-warning :deep(.el-progress__text) {
  color: var(--el-color-warning);
  font-weight: 600;
}

.subscription-quota-item__progress :deep(.el-progress--without-text .el-progress-bar) {
  grid-column: 1 / -1;
}
</style>
