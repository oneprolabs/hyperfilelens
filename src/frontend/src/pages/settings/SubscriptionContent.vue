<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  cancelQuotaIncreaseRequest,
  fetchCurrentLicense,
  fetchEffectiveQuotaUsage,
  fetchQuotaIncreaseRequests,
  submitQuotaIncreaseRequest,
  type EffectiveQuotaUsage,
  type LicenseRecord,
  type LicenseUsage,
  type QuotaIncreaseRequest,
  type QuotaIncreaseRequestPage,
} from '../../lib/subscriptionApi'
import {
  formatQuotaBytes,
  isQuotaByteMeter,
  quotaDefsForSubscription,
  quotaDisplayValue,
  quotaUsagePercent,
  SUBSCRIPTION_QUOTA_FALLBACK_LIMITS,
} from '../../lib/licenseQuotaDisplay'
import { apiErrorMessageI18n, isAbortError } from '../../lib/api'
import { formatLocalDateTime } from '../../lib/dateTime'
import { currentUser, getEffectiveOrgKey } from '../../composables/useAuth'
import { useTenantPermissions } from '../../composables/useTenantPermissions'
import { fetchDeployProfile } from '../../composables/useDeployProfile'
import HflPagination from '../../components/HflPagination.vue'
import HflStatusTag from '../../components/HflStatusTag.vue'
import HflTablePanel from '../../components/HflTablePanel.vue'
import '../../components/backupSourceFlowActionDialog.css'

const FALLBACK_LIMITS = SUBSCRIPTION_QUOTA_FALLBACK_LIMITS
const quotaRequestsEnabled = __HFL_EXTENSIONS_FRONTEND__
const MIB = 1024 ** 2
const GIB = 1024 ** 3
const TIB = 1024 ** 4
type CapacityUnit = 'mb' | 'gb' | 'tb'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const { has } = useTenantPermissions()
const canViewSubscription = computed(() => has('configuration.subscriptions.view', true))
const canManageQuotaRequests = computed(() => has('configuration.subscriptions.manage', true))
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
const quotaGovernanceEnabled = ref(false)
const showQuotaRequestSurface = computed(() => (
  quotaRequestsEnabled && quotaGovernanceEnabled.value && canViewSubscription.value
))
const canRequestQuota = computed(() => (
  showQuotaRequestSurface.value && canManageQuotaRequests.value
))
const activeTab = ref<'usage' | 'requests'>('usage')
const quotaRequests = ref<QuotaIncreaseRequest[]>([])
const quotaRequestPage = ref(1)
const quotaRequestPageSize = ref(20)
const quotaRequestTotal = ref(0)
const requestHistoryFailed = ref(false)
const requestHistoryBusy = ref(false)
const requestDialogOpen = ref(false)
const requestSubmitting = ref(false)
const requestMeterKey = ref('')
const requestLimitInput = ref<number | null>(null)
const requestCapacityUnit = ref<CapacityUnit>('gb')
const requestJustification = ref('')
let statusResizeObserver: ResizeObserver | null = null
let loadGeneration = 0
let requestHistoryGeneration = 0
const requestUsesBytes = computed(() => isQuotaByteMeter(requestMeterKey.value))
const requestCurrentLimitLabel = computed(() => {
  const item = limitItems.value.find((row) => row.meterKey === requestMeterKey.value)
  if (!item) return ''
  return t('settings.subscription.requestCurrentLimitLabel', {
    limit: item.limit < 0 ? t('settings.subscription.unlimited') : `${item.displayLimit}${item.suffix ? ` ${item.suffix}` : ''}`,
  })
})

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
    const pending = quotaRequests.value.some(
      (row) => row.meter_key === def.limitKey && row.status === 'pending',
    )
    return {
      key: def.key,
      meterKey: def.limitKey,
      label: t(def.labelKey),
      used,
      limit,
      rawLimit: effective?.limit ?? getLimit(def.limitKey),
      displayUsed: formatValue(used),
      displayLimit: limit < 0 ? unlimitedLabel : formatValue(limit),
      suffix: def.suffix,
      usagePercent: effective?.usage_percent,
      usageStatus: effective?.usage_status,
      overLimit,
      pending,
      canRequest: canRequestQuota.value && limit >= 0 && !pending,
    }
  }),
)

const requestableItems = computed(() => limitItems.value.filter((item) => item.canRequest))

function requestStatusLabel(status: QuotaIncreaseRequest['status']) {
  if (status === 'pending') return t('settings.subscription.requestStatusPending')
  if (status === 'approved_applied') return t('settings.subscription.requestStatusApprovedApplied')
  if (status === 'approved_failed') return t('settings.subscription.requestStatusApprovedFailed')
  if (status === 'cancelled') return t('settings.subscription.requestStatusCancelled')
  return t('settings.subscription.requestStatusRejected')
}

function requestStatusTone(status: QuotaIncreaseRequest['status']) {
  if (status === 'pending') return 'warning'
  if (status === 'approved_applied') return 'success'
  if (status === 'approved_failed') return 'danger'
  return 'info'
}

function meterLabel(meterKey: string) {
  const def = quotaDefsForSubscription().find((item) => item.limitKey === meterKey)
  return def ? t(def.labelKey) : meterKey
}

function preferredCapacityUnit(bytes: number): CapacityUnit {
  if (bytes >= TIB && bytes % TIB === 0) return 'tb'
  if (bytes >= 0 && bytes % GIB !== 0 && bytes % MIB === 0) return 'mb'
  return 'gb'
}

function capacityValue(bytes: number, unit: CapacityUnit) {
  if (unit === 'tb') return bytes / TIB
  if (unit === 'mb') return bytes / MIB
  return bytes / GIB
}

function capacityMultiplier(unit: CapacityUnit) {
  if (unit === 'tb') return TIB
  if (unit === 'mb') return MIB
  return GIB
}

function formatRequestLimit(meterKey: string, limit: number) {
  if (isQuotaByteMeter(meterKey)) {
    return formatQuotaBytes(limit, t('settings.subscription.unlimited'))
  }
  return String(limit)
}

function formatRequestCreated(row: QuotaIncreaseRequest) {
  return formatLocalDateTime(row.created_at, '—')
}

const requestHistoryEmptyDescription = computed(() => {
  if (requestHistoryFailed.value) return t('settings.subscription.requestHistoryLoadFailed')
  if (canRequestQuota.value) return t('settings.subscription.requestHistoryEmptyHint')
  return t('settings.subscription.requestHistoryEmpty')
})

function applyRequestMeterDefaults(meterKey: string) {
  const item = limitItems.value.find((row) => row.meterKey === meterKey)
  if (!item) return
  requestMeterKey.value = item.meterKey
  if (isQuotaByteMeter(item.meterKey) && item.rawLimit >= 0) {
    const unit = preferredCapacityUnit(item.rawLimit)
    requestCapacityUnit.value = unit
    requestLimitInput.value = capacityValue(item.rawLimit, unit) + (unit === 'mb' ? 1024 : 1)
  } else {
    requestCapacityUnit.value = 'gb'
    requestLimitInput.value = item.rawLimit >= 0 ? Math.trunc(item.rawLimit) + 1 : 1
  }
}

function openRequestDialog(item: (typeof limitItems.value)[number]) {
  requestJustification.value = ''
  applyRequestMeterDefaults(item.meterKey)
  requestDialogOpen.value = true
}

function openNewQuotaRequest() {
  const first = requestableItems.value[0]
  if (!first) return
  openRequestDialog(first)
}

function onRequestMeterChange(meterKey: string) {
  applyRequestMeterDefaults(meterKey)
}

function maybeOpenRequestFromQuery() {
  if (!canRequestQuota.value || loading.value || accessDenied.value) return
  const meter = String(route.query.request || '').trim()
  if (!meter) return
  const item = limitItems.value.find((row) => row.meterKey === meter && row.canRequest)
  if (!item) return
  openRequestDialog(item)
  const query = { ...route.query }
  delete query.request
  void router.replace({ query })
}

async function loadQuotaRequests() {
  if (!showQuotaRequestSurface.value) {
    quotaRequests.value = []
    quotaRequestTotal.value = 0
    requestHistoryFailed.value = false
    return
  }
  const generation = ++requestHistoryGeneration
  const orgKey = getEffectiveOrgKey()
  const isCurrent = () => (
    generation === requestHistoryGeneration
    && orgKey === getEffectiveOrgKey()
    && showQuotaRequestSurface.value
  )
  requestHistoryBusy.value = true
  requestHistoryFailed.value = false
  try {
    const result: QuotaIncreaseRequestPage = await fetchQuotaIncreaseRequests({
      page: quotaRequestPage.value,
      page_size: quotaRequestPageSize.value,
    })
    if (!isCurrent()) return
    quotaRequests.value = result.results
    quotaRequestTotal.value = result.count
  } catch (error: unknown) {
    if (!isCurrent() || isAbortError(error)) return
    if (errorStatus(error) === 403 || errorStatus(error) === 404) {
      quotaRequests.value = []
      quotaRequestTotal.value = 0
      return
    }
    quotaRequests.value = []
    quotaRequestTotal.value = 0
    requestHistoryFailed.value = true
    ElMessage.error({
      message: apiErrorMessageI18n(error, t, t('settings.subscription.requestHistoryLoadFailed')),
      grouping: true,
    })
  } finally {
    if (generation === requestHistoryGeneration) requestHistoryBusy.value = false
  }
}

async function submitRequest() {
  if (!canRequestQuota.value || !requestMeterKey.value || requestLimitInput.value == null) return
  const entered = Number(requestLimitInput.value)
  if (!Number.isFinite(entered) || entered < 0) return
  const requestedLimit = requestUsesBytes.value
    ? Math.round(entered * capacityMultiplier(requestCapacityUnit.value))
    : Math.trunc(entered)
  requestSubmitting.value = true
  try {
    await submitQuotaIncreaseRequest({
      meter_key: requestMeterKey.value,
      requested_limit: requestedLimit,
      justification: requestJustification.value.trim(),
    })
    ElMessage.success({ message: t('settings.subscription.requestSubmitted'), grouping: true })
    requestDialogOpen.value = false
    activeTab.value = 'requests'
    await loadQuotaRequests()
  } catch (error: unknown) {
    ElMessage.error({
      message: apiErrorMessageI18n(error, t, t('settings.subscription.requestSubmitFailed')),
      grouping: true,
    })
  } finally {
    requestSubmitting.value = false
  }
}

const cancellingRequestId = ref<number | null>(null)

async function cancelRequest(row: QuotaIncreaseRequest) {
  if (!canRequestQuota.value || row.status !== 'pending' || cancellingRequestId.value != null) return
  let cancellationNote = ''
  try {
    const prompt = await ElMessageBox.prompt(
      t('settings.subscription.requestCancelConfirm', { item: meterLabel(row.meter_key) }),
      t('settings.subscription.requestCancelTitle'),
      {
        confirmButtonText: t('settings.subscription.requestCancelConfirmAction'),
        cancelButtonText: t('settings.subscription.requestCancelKeep'),
        inputPlaceholder: t('settings.subscription.requestCancelNotePlaceholder'),
        inputType: 'textarea',
        inputValidator: (value) => value.length <= 2000
          || t('settings.subscription.requestCancelNoteTooLong'),
        type: 'warning',
        distinguishCancelAndClose: true,
      },
    )
    cancellationNote = String(prompt.value || '').trim()
  } catch {
    // Element Plus rejects the promise for both Cancel and Close.
    return
  }
  cancellingRequestId.value = row.id
  try {
    await cancelQuotaIncreaseRequest(row.id, cancellationNote)
    ElMessage.success({ message: t('settings.subscription.requestCancelled'), grouping: true })
    await loadQuotaRequests()
  } catch (error: unknown) {
    ElMessage.error({
      message: apiErrorMessageI18n(error, t, t('settings.subscription.requestCancelFailed')),
      grouping: true,
    })
  } finally {
    cancellingRequestId.value = null
  }
}

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
  quotaGovernanceEnabled.value = false
  requestHistoryGeneration += 1
  quotaRequests.value = []
  quotaRequestTotal.value = 0
  quotaRequestPage.value = 1
  requestHistoryFailed.value = false
  requestHistoryBusy.value = false
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
        if (quotaRequestsEnabled) {
          const profile = await fetchDeployProfile()
          quotaGovernanceEnabled.value = Boolean(
            profile?.enterprise_features?.includes('quota_governance'),
          )
          if (showQuotaRequestSurface.value) await loadQuotaRequests()
        }
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
    if (generation === loadGeneration) {
      loading.value = false
      maybeOpenRequestFromQuery()
    }
  }
}

onMounted(loadAll)
watch([canViewSubscription, () => currentUser.value?.access_profile?.org_key], () => { void loadAll() })
watch(
  [() => route.query.request, canRequestQuota, () => limitItems.value.map((item) => item.meterKey).join(',')],
  () => { maybeOpenRequestFromQuery() },
)
watch([quotaRequestPage, quotaRequestPageSize], () => {
  if (!showQuotaRequestSurface.value) return
  void loadQuotaRequests()
})
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
      <div
        v-if="showQuotaRequestSurface"
        class="subscription-tabs-bar"
      >
        <el-tabs
          v-model="activeTab"
          class="subscription-tabs"
        >
          <el-tab-pane
            :label="t('settings.subscription.limitsAndUsage')"
            name="usage"
          >
            <div class="subscription-tab-panel">
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
            </div>
          </el-tab-pane>
          <el-tab-pane
            :label="t('settings.subscription.requestHistoryTitle')"
            name="requests"
          >
            <div class="subscription-tab-panel">
              <HflTablePanel class="subscription-requests-panel">
                <template #table="{ tableMaxHeight }">
                  <el-table
                    v-loading="requestHistoryBusy"
                    :data="quotaRequests"
                    row-key="id"
                    class="hfl-list-table subscription-requests-table"
                    stripe
                    :max-height="tableMaxHeight"
                  >
                    <el-table-column
                      :label="t('settings.subscription.requestColMeter')"
                      min-width="160"
                    >
                      <template #default="{ row }">
                        {{ meterLabel(row.meter_key) }}
                      </template>
                    </el-table-column>
                    <el-table-column
                      :label="t('settings.subscription.requestColCurrent')"
                      min-width="120"
                    >
                      <template #default="{ row }">
                        {{ formatRequestLimit(row.meter_key, row.current_limit) }}
                      </template>
                    </el-table-column>
                    <el-table-column
                      :label="t('settings.subscription.requestColRequested')"
                      min-width="120"
                    >
                      <template #default="{ row }">
                        {{ formatRequestLimit(row.meter_key, row.requested_limit) }}
                      </template>
                    </el-table-column>
                    <el-table-column
                      :label="t('settings.subscription.requestColStatus')"
                      width="140"
                    >
                      <template #default="{ row }">
                        <HflStatusTag
                          :label="requestStatusLabel(row.status)"
                          :tone="requestStatusTone(row.status)"
                        />
                      </template>
                    </el-table-column>
                    <el-table-column
                      :label="t('settings.subscription.requestColUpdated')"
                      min-width="160"
                    >
                      <template #default="{ row }">
                        {{ formatRequestCreated(row) }}
                      </template>
                    </el-table-column>
                    <el-table-column
                      v-if="canRequestQuota"
                      :label="t('settings.subscription.requestColActions')"
                      class-name="subscription-requests__actions-col hfl-table-no-tooltip"
                      label-class-name="subscription-requests__actions-col hfl-table-no-tooltip"
                      width="112"
                      align="center"
                      header-align="center"
                      :show-overflow-tooltip="false"
                    >
                      <template #default="{ row }">
                        <div
                          class="hfl-table-no-tooltip subscription-requests__actions"
                          data-table-overflow-explicit-only
                        >
                          <button
                            v-if="row.status === 'pending'"
                            type="button"
                            class="subscription-requests__cancel"
                            style="padding: 0 16px"
                            :disabled="cancellingRequestId != null"
                            @click.stop="cancelRequest(row)"
                          >
                            <span
                              v-if="cancellingRequestId === row.id"
                              class="subscription-requests__cancel-spinner"
                              aria-hidden="true"
                            />
                            {{ t('settings.subscription.requestCancel') }}
                          </button>
                          <span
                            v-else
                            class="subscription-requests__action-empty"
                          >—</span>
                        </div>
                      </template>
                    </el-table-column>
                    <template #empty>
                      <el-empty
                        :description="requestHistoryEmptyDescription"
                        :image-size="80"
                      >
                        <el-button
                          v-if="requestHistoryFailed"
                          :disabled="requestHistoryBusy"
                          @click="loadQuotaRequests"
                        >
                          {{ t('common.retry') }}
                        </el-button>
                      </el-empty>
                    </template>
                  </el-table>
                </template>
                <template #footer>
                  <HflPagination
                    v-model:current-page="quotaRequestPage"
                    v-model:page-size="quotaRequestPageSize"
                    class="hfl-list-footer__pagination"
                    :total="quotaRequestTotal"
                    :page-sizes="[10, 20, 30, 50]"
                    :disabled="requestHistoryBusy"
                  />
                </template>
              </HflTablePanel>
            </div>
          </el-tab-pane>
        </el-tabs>
        <el-button
          v-if="canRequestQuota"
          type="primary"
          class="subscription-tabs-bar__action"
          :disabled="!requestableItems.length"
          @click="openNewQuotaRequest"
        >
          {{ t('settings.subscription.requestIncrease') }}
        </el-button>
      </div>

      <template v-else>
        <header class="subscription-section__header">
          <h2 class="subscription-section__title">
            {{ t('settings.subscription.limitsAndUsage') }}
          </h2>
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
      </template>
    </section>

    <ElDialog
      v-model="requestDialogOpen"
      class="hfl-flow-action-dialog hfl-flow-action-dialog--form subscription-request-dialog"
      :title="t('settings.subscription.requestIncreaseTitle')"
      align-center
      append-to-body
      destroy-on-close
      :close-on-click-modal="!requestSubmitting"
      :close-on-press-escape="!requestSubmitting"
      :show-close="!requestSubmitting"
    >
      <ElForm
        label-position="top"
        class="hfl-flow-action-dialog__form subscription-request-dialog__form"
        @submit.prevent="submitRequest"
      >
        <ElFormItem
          :label="t('settings.subscription.requestMeter')"
          required
        >
          <ElSelect
            :model-value="requestMeterKey"
            class="subscription-request-dialog__meter"
            :disabled="requestSubmitting"
            @update:model-value="onRequestMeterChange"
          >
            <ElOption
              v-for="item in requestableItems"
              :key="item.meterKey"
              :label="item.label"
              :value="item.meterKey"
            />
          </ElSelect>
          <p
            v-if="requestCurrentLimitLabel"
            class="subscription-request-dialog__current"
          >
            {{ requestCurrentLimitLabel }}
          </p>
        </ElFormItem>
        <ElFormItem
          :label="t('settings.subscription.requestRequestedLimit')"
          required
        >
          <div class="subscription-request-dialog__limit-row">
            <ElInputNumber
              v-model="requestLimitInput"
              class="subscription-request-dialog__number"
              :min="0"
              :step="1"
              :precision="0"
              controls-position="right"
              :disabled="requestSubmitting"
            />
            <ElSelect
              v-if="requestUsesBytes"
              v-model="requestCapacityUnit"
              class="subscription-request-dialog__unit"
              :disabled="requestSubmitting"
            >
              <ElOption
                :label="t('settings.subscription.requestUnitMb')"
                value="mb"
              />
              <ElOption
                :label="t('settings.subscription.requestUnitGb')"
                value="gb"
              />
              <ElOption
                :label="t('settings.subscription.requestUnitTb')"
                value="tb"
              />
            </ElSelect>
          </div>
        </ElFormItem>
        <ElFormItem
          :label="t('settings.subscription.requestJustification')"
          required
        >
          <ElInput
            v-model="requestJustification"
            type="textarea"
            :rows="4"
            :placeholder="t('settings.subscription.requestJustificationPlaceholder')"
            :disabled="requestSubmitting"
          />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton
          :disabled="requestSubmitting"
          @click="requestDialogOpen = false"
        >
          {{ t('common.cancel') }}
        </ElButton>
        <ElButton
          type="primary"
          :loading="requestSubmitting"
          :disabled="!requestJustification.trim() || requestLimitInput == null"
          @click="submitRequest"
        >
          {{ t('settings.subscription.requestSubmit') }}
        </ElButton>
      </template>
    </ElDialog>
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

.subscription-tabs-bar {
  position: relative;
  min-width: 0;
}

.subscription-tabs-bar__action {
  position: absolute;
  top: 4px;
  right: 0;
  z-index: 2;
}

.subscription-tabs {
  min-width: 0;
}

.subscription-tabs :deep(.el-tabs__header) {
  margin-bottom: 12px;
}

.subscription-tabs :deep(.el-tabs__nav-wrap) {
  /* Keep tab labels clear of the page-level Request Quota Increase button. */
  padding-right: 220px;
}

.subscription-tabs :deep(.el-tabs__nav-wrap::after) {
  height: 1px;
}

.subscription-tabs :deep(.el-tabs__item) {
  font-weight: 500;
}

.subscription-tabs :deep(.el-tabs__content) {
  overflow: visible;
}

.subscription-tab-panel {
  min-width: 0;
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

.subscription-requests-panel {
  min-width: 0;
}

.subscription-requests__actions {
  display: inline-flex;
  justify-content: center;
  align-items: center;
  width: 100%;
  max-width: none;
  min-width: auto;
  overflow: visible;
  white-space: nowrap;
}

.subscription-requests__action-empty {
  color: var(--el-text-color-placeholder);
}

.subscription-requests__cancel {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  flex: 0 0 auto;
  box-sizing: border-box;
  min-width: max-content;
  height: 28px;
  margin: 0;
  padding: 0 16px;
  border: 1px solid var(--color-border, var(--el-border-color));
  border-radius: 6px;
  background: var(--el-fill-color-blank, #fff);
  color: var(--color-text-title, var(--el-text-color-primary));
  font: inherit;
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
}

.subscription-requests__cancel:hover:not(:disabled),
.subscription-requests__cancel:focus-visible:not(:disabled) {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
  outline: none;
}

.subscription-requests__cancel:disabled {
  opacity: 0.65;
  cursor: not-allowed;
}

.subscription-requests__cancel-spinner {
  width: 12px;
  height: 12px;
  border: 1.5px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: subscription-requests-spin 0.7s linear infinite;
}

@keyframes subscription-requests-spin {
  to {
    transform: rotate(360deg);
  }
}

.subscription-requests-table.hfl-list-table :deep(
  td.subscription-requests__actions-col.el-table__cell:not(.el-table-column--selection):not(.el-table__expand-column) > .cell
),
.subscription-requests-table.hfl-list-table :deep(
  th.subscription-requests__actions-col.el-table__cell:not(.el-table-column--selection):not(.el-table__expand-column) > .cell
) {
  overflow: visible !important;
  text-overflow: clip;
  white-space: nowrap;
}

.subscription-requests-table.hfl-list-table :deep(
  td.subscription-requests__actions-col.el-table__cell:not(.el-table-column--selection):not(.el-table__expand-column) > .cell > *
) {
  min-width: auto !important;
  max-width: none !important;
}

:global(.subscription-request-dialog.hfl-flow-action-dialog.el-dialog) {
  --el-dialog-width: min(520px, calc(100vw - 32px));
}

.subscription-request-dialog__form :deep(.el-form-item) {
  margin-bottom: 16px;
}

.subscription-request-dialog__form :deep(.el-form-item:last-child) {
  margin-bottom: 0;
}

.subscription-request-dialog__meter {
  width: 100%;
}

.subscription-request-dialog__current {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.4;
  color: var(--el-text-color-secondary);
}

.subscription-request-dialog__limit-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.subscription-request-dialog__number {
  flex: 1 1 auto;
  min-width: 0;
  width: 100%;
}

.subscription-request-dialog__unit {
  flex: 0 0 104px;
  width: 104px;
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
