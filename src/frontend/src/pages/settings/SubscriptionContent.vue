<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
import { currentUser, getEffectiveOrgKey } from '../../composables/useAuth'
import { useTenantPermissions } from '../../composables/useTenantPermissions'

const FALLBACK_LIMITS = SUBSCRIPTION_QUOTA_FALLBACK_LIMITS

const { t, locale } = useI18n()
const { has } = useTenantPermissions()
const canViewSubscription = computed(() => has('configuration.subscriptions.view', true))
const forbidden = ref(false)
const accessDenied = computed(() => !canViewSubscription.value || forbidden.value)

const loading = ref(false)
const quotaAvailable = ref(false)
const currentLicense = ref<LicenseRecord | null>(null)
const instanceShared = ref(false)
const usage = ref<LicenseUsage>({})
const limits = ref<Record<string, number>>({ ...FALLBACK_LIMITS })
const effectiveQuotaByKey = ref<Record<string, EffectiveQuotaUsage>>({})
const quotaWarning = ref('')
const quotaGrid = ref<HTMLElement | null>(null)
const statusWidth = ref(0)
let statusResizeObserver: ResizeObserver | null = null
let loadGeneration = 0

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
    const formatValue = def.formatBytes
      ? (value: number) => formatQuotaBytes(value, unlimitedLabel)
      : def.limitKey === 'ai_tokens' ? formatTokenCount : formatNumber
    return {
      key: def.key,
      label: t(def.labelKey),
      used,
      limit,
      displayUsed: formatValue(used),
      displayLimit: limit < 0 ? unlimitedLabel : formatValue(limit),
      suffix: def.suffix,
      usagePercent: effective?.usage_percent,
      usageStatus: effective?.usage_status,
      overLimit,
    }
  }),
)

function measureStatusWidth() {
  const labels = quotaGrid.value?.querySelectorAll<HTMLElement>('.subscription-quota-item__status-text')
  statusWidth.value = Math.ceil(Math.max(
    0,
    ...Array.from(labels ?? [], (label) => label.getBoundingClientRect().width),
  ))
}

watch([quotaGrid, limitItems, locale], () => {
  statusResizeObserver?.disconnect()
  measureStatusWidth()
  if (typeof ResizeObserver === 'undefined' || !quotaGrid.value) return
  // Observe intrinsic text widths, not their shared column, to avoid a resize
  // feedback loop. This also responds to font loading and translated labels.
  statusResizeObserver ??= new ResizeObserver(measureStatusWidth)
  quotaGrid.value.querySelectorAll('.subscription-quota-item__status-text')
    .forEach((label) => statusResizeObserver!.observe(label))
}, { flush: 'post' })

onBeforeUnmount(() => {
  loadGeneration += 1
  statusResizeObserver?.disconnect()
})

function formatNumber(value: number) {
  return new Intl.NumberFormat(locale.value, { maximumFractionDigits: 2 }).format(value)
}

function formatTokenCount(value: number) {
  // Keep decimal K/M/B notation consistent across locales; this is display-only.
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 2,
  }).format(value)
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
  if (item.usageStatus === 'unknown' || item.limit < 0) return 0
  // Preserve small nonzero usage rather than rounding the fill down to zero.
  const percentage = item.limit > 0
    ? (item.used / item.limit) * 100
    : item.usagePercent ?? quotaUsagePercent(item.used, item.limit)
  return Math.min(100, Math.max(0, percentage))
}

function progressLabel(item: (typeof limitItems.value)[number]) {
  if (item.usageStatus === 'unknown') return t('platformOps.quotaUsage.usageStatus.unknown')
  if (item.limit < 0) return t('settings.subscription.unlimited')
  if (item.overLimit) return t('settings.subscription.overLimit')
  const percentage = effectiveProgressPercent(item)
  if (percentage > 0 && percentage < 0.1) return `<${formatNumber(0.1)}%`
  return `${formatNumber(percentage < 1 ? Math.round(percentage * 10) / 10 : Math.round(percentage))}%`
}

function clearQuota() {
  quotaAvailable.value = false
  currentLicense.value = null
  instanceShared.value = false
  usage.value = {}
  limits.value = { ...FALLBACK_LIMITS }
  effectiveQuotaByKey.value = {}
  quotaWarning.value = ''
}

function errorStatus(error: unknown): number {
  return error && typeof error === 'object' && 'status' in error ? Number(error.status) : 0
}

async function loadAll() {
  const generation = ++loadGeneration
  const orgKey = getEffectiveOrgKey()
  const isCurrent = () => generation === loadGeneration
    && canViewSubscription.value && orgKey === getEffectiveOrgKey()
  forbidden.value = false
  clearQuota()
  loading.value = canViewSubscription.value
  if (!canViewSubscription.value) return
  try {
    const current = await fetchCurrentLicense()
    if (!isCurrent()) return
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
        if (!isCurrent()) return
        effectiveQuotaByKey.value = Object.fromEntries(
          (effective.quota_usage || []).map((row) => [row.key, row]),
        )
      } catch (error: unknown) {
        if (!isCurrent()) return
        if (isAbortError(error) || errorStatus(error) === 403) throw error
        const status = errorStatus(error)
        if (status === 404 || status === 405) {
          // Only an absent older endpoint is a compatibility fallback.
          quotaWarning.value = t('settings.subscription.compatibleQuotaHint')
        } else {
          quotaAvailable.value = false
          quotaWarning.value = t('settings.subscription.effectiveQuotaFailed')
        }
      }
    }
  } catch (error: unknown) {
    if (!isCurrent() || isAbortError(error)) return
    clearQuota()
    if (errorStatus(error) === 403) {
      forbidden.value = true
      return
    }
    const message = error && typeof error === 'object' && 'message' in error
      ? String((error as { message?: unknown }).message || '').trim()
      : ''
    ElMessage.error({
      message: message || t('settings.subscription.loadFailed'),
      grouping: true,
    })
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}

onMounted(loadAll)
watch([canViewSubscription, () => currentUser.value?.access_profile?.org_key], () => { void loadAll() })
</script>

<template>
  <div
    v-loading="loading"
    class="subscription-page"
  >
    <el-alert
      v-if="accessDenied"
      :title="t('settings.subscription.accessDenied')"
      type="warning"
      :closable="false"
      show-icon
    />
    <section
      v-else
      class="subscription-section"
    >
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
      <el-alert
        v-if="quotaWarning"
        :title="quotaWarning"
        type="warning"
        :closable="false"
        show-icon
      />

      <div
        v-if="quotaAvailable"
        ref="quotaGrid"
        class="subscription-quota-grid"
        :style="{ '--quota-status-width': statusWidth > 0 ? `${statusWidth}px` : 'max-content' }"
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
            {{ item.displayUsed }}<template v-if="item.limit >= 0"> / {{ item.displayLimit }}</template><template v-if="item.suffix">{{ ` ${item.suffix}` }}</template>
          </span>
          <div
            class="subscription-quota-item__progress"
            :class="progressToneClass(item)"
          >
            <ElProgress
              class="subscription-quota-item__track"
              :percentage="effectiveProgressPercent(item)"
              :show-text="false"
              :aria-label="item.label"
              :aria-hidden="item.limit < 0 || item.usageStatus === 'unknown' ? true : undefined"
            />
            <span
              class="subscription-quota-item__status"
              :title="progressLabel(item)"
            >
              <span class="subscription-quota-item__status-text">{{ progressLabel(item) }}</span>
            </span>
          </div>
        </div>
      </div>
      <div
        v-else-if="!loading"
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
  min-width: 0;
  color: var(--color-text-secondary, #606266);
}

.subscription-quota-item__numbers {
  grid-column: 2;
  grid-row: 1;
  font-size: 12px;
  color: var(--color-text-primary, #303133);
  white-space: nowrap;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.subscription-quota-item__progress {
  grid-column: 1 / -1;
  grid-row: 2;
  display: grid;
  /* Independent of the header's variable-width usage values. */
  grid-template-columns: minmax(0, 1fr) var(--quota-status-width, max-content);
  column-gap: 12px;
  align-items: center;
}

.subscription-quota-item__track {
  min-width: 0;
}

.subscription-quota-item__progress :deep(.el-progress-bar) {
  min-width: 0;
  margin-right: 0;
  padding-right: 0;
}

.subscription-quota-item__status {
  min-width: 0;
  text-align: right;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  line-height: 1;
  color: var(--color-text-secondary, #606266);
}

.subscription-quota-item__status-text {
  display: inline-block;
  width: max-content;
}

.subscription-quota-item__progress.is-over-limit :deep(.el-progress-bar__inner) {
  background-color: var(--el-color-warning);
}

.subscription-quota-item__progress.is-warning :deep(.el-progress-bar__inner) {
  background-color: var(--el-color-warning);
}

.subscription-quota-item__progress.is-over-limit .subscription-quota-item__status {
  color: var(--el-color-warning);
  font-weight: 600;
}

.subscription-quota-item__progress.is-warning .subscription-quota-item__status {
  color: var(--el-color-warning);
  font-weight: 600;
}

</style>
