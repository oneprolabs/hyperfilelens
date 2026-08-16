<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RefreshCw } from 'lucide-vue-next'
import { ElAlert, ElMessage } from 'element-plus'
import HflTablePanel from '../../components/HflTablePanel.vue'
import { copyTextToClipboard } from '../../lib/clipboard'
import {
  activateLicense,
  fetchCurrentLicense,
  fetchEffectiveQuotaUsage,
  fetchLicenseHistory,
  fetchMachineCode,
  type EffectiveQuotaUsage,
  type LicenseHistoryRow,
  type LicenseRecord,
  type LicenseUsage,
} from '../../lib/subscriptionApi'
import {
  quotaDefsForSubscription,
  quotaUsagePercent,
  SUBSCRIPTION_QUOTA_FALLBACK_LIMITS,
} from '../../lib/licenseQuotaDisplay'
import { formatAppDate, formatAppDateTime } from '../../lib/dateTime'
import { statusTagAttrs } from '../../lib/statusTag'

const FALLBACK_LIMITS = SUBSCRIPTION_QUOTA_FALLBACK_LIMITS

const { t, te } = useI18n()

const loading = ref(false)
const currentLicense = ref<LicenseRecord | null>(null)
const instanceShared = ref(false)
const enforcementEnabled = ref(false)
const usage = ref<LicenseUsage>({})
const limits = ref<Record<string, number>>({ ...FALLBACK_LIMITS })
const effectiveQuotaByKey = ref<Record<string, EffectiveQuotaUsage>>({})
const machineCode = ref('')
const canManageInstanceLicense = ref(false)
const licenseHistory = ref<LicenseHistoryRow[]>([])
const activationCode = ref('')
const activating = ref(false)

const hasActiveLicense = computed(() => Boolean(currentLicense.value?.is_valid))
const canActivateHere = computed(
  () => canManageInstanceLicense.value && !instanceShared.value,
)
const pageSubtitle = computed(() =>
  enforcementEnabled.value
    ? t('settings.subscription.subtitleEnforced')
    : t('settings.subscription.subtitleInformational'),
)

const limitItems = computed(() =>
  quotaDefsForSubscription().map((def) => {
    const effective = effectiveQuotaByKey.value[def.limitKey]
    const used = effective?.used ?? getUsage(def.usageKey)
    const limit = effective?.limit ?? getLimit(def.limitKey)
    return {
      key: def.key,
      label: t(def.labelKey),
      suffix: def.suffix,
      used,
      limit,
      limitSource: effective?.limit_source,
      usagePercent: effective?.usage_percent,
      usageStatus: effective?.usage_status,
    }
  }),
)

const editionLabel = computed(() => {
  if (hasActiveLicense.value || enforcementEnabled.value) {
    return t('settings.subscription.editionEnterprise')
  }
  return t('settings.subscription.editionNone')
})

const licenseStatusLabel = computed(() => {
  if (currentLicense.value?.status === 'expired') return t('settings.subscription.statusExpired')
  if (!currentLicense.value?.is_valid) return t('settings.subscription.statusStopped')
  return t('settings.subscription.statusActive')
})

const expiryDateText = computed(() => {
  if (!currentLicense.value) return '—'
  if (currentLicense.value.is_perpetual) return t('settings.subscription.unlimited')
  return formatDateOnly(currentLicense.value.expires_at)
})

const daysRemainingText = computed(() => {
  const days = currentLicense.value?.days_until_expiry
  if (days == null || days < 0) return '—'
  return t('settings.subscription.daysRemaining', { n: days })
})

function formatDateOnly(iso?: string | null) {
  return formatAppDate(iso, '—')
}

function formatDateTime(iso?: string | null) {
  return formatAppDateTime(iso, '—')
}

function formatLimit(val?: number) {
  if (val === undefined || val === null) return '—'
  if (val < 0) return t('settings.subscription.unlimited')
  return String(val)
}

function getLimit(key: string): number {
  // Instance-shared: prefer EffectiveQuota / payload limits, not host License columns.
  if (!instanceShared.value) {
    const fromLic = currentLicense.value as Record<string, unknown> | null
    if (fromLic && key in fromLic) return Number(fromLic[key]) || 0
  }
  return limits.value[key] ?? FALLBACK_LIMITS[key] ?? 0
}

function getUsage(key: string): number {
  const v = usage.value[key]
  return typeof v === 'number' ? v : 0
}

function usagePercent(current: number, max: number) {
  return quotaUsagePercent(current, max)
}

function progressStatus(current: number, max: number): '' | 'success' | 'warning' | 'exception' {
  if (max < 0) return 'success'
  const p = usagePercent(current, max)
  if (p >= 95) return 'exception'
  if (p >= 80) return 'warning'
  return 'success'
}

function effectiveProgressStatus(item: (typeof limitItems.value)[number]) {
  if (item.usageStatus === 'exhausted' || item.usageStatus === 'exceeded') return 'exception'
  if (item.usageStatus === 'warning' || item.usageStatus === 'unknown') return 'warning'
  return progressStatus(item.used, item.limit)
}

function effectiveProgressPercent(item: (typeof limitItems.value)[number]) {
  if (item.usageStatus === 'unknown') return 0
  if (item.usagePercent == null) return usagePercent(item.used, item.limit)
  return Math.min(100, Math.max(0, Math.round(item.usagePercent)))
}

function changeTypeLabel(type?: string | null) {
  const normalizedType = type?.trim()
  if (!normalizedType) return '—'
  const key = `settings.subscription.changeType.${normalizedType}`
  return te(key) ? t(key) : normalizedType
}

function historyChangeLabel(row: LicenseHistoryRow) {
  if (row.change_reason?.trim()) return row.change_reason
  return changeTypeLabel(row.change_type)
}

function historyStatusLabel(row: LicenseHistoryRow) {
  if (row.change_type === 'machine_code' || row.status === 'completed') {
    return t('settings.subscription.historyStatusCompleted')
  }
  if (row.status === 'expired' || row.status === 'revoked') {
    return t('settings.subscription.historyStatusExpired')
  }
  return t('settings.subscription.historyStatusActive')
}

function historyStatusTagAttrs(row: LicenseHistoryRow) {
  if (row.status === 'expired' || row.status === 'revoked') return statusTagAttrs('neutral')
  return statusTagAttrs('success')
}

async function loadAll() {
  loading.value = true
  try {
    const [current, history] = await Promise.all([fetchCurrentLicense(), fetchLicenseHistory()])
    usage.value = current.usage || {}
    machineCode.value = current.machine_code || ''
    limits.value = { ...FALLBACK_LIMITS, ...(current.limits || {}) }
    instanceShared.value = Boolean(current.instance_shared)
    canManageInstanceLicense.value = Boolean(current.can_manage_instance_license)
    enforcementEnabled.value = Boolean(current.enforcement_enabled)
    effectiveQuotaByKey.value = {}
    if (current.enforcement_enabled) {
      try {
        const effective = await fetchEffectiveQuotaUsage()
        effectiveQuotaByKey.value = Object.fromEntries(
          (effective.quota_usage || []).map((row) => [row.key, row]),
        )
      } catch {
        // Keep the compatible limits/usage from the current-license payload.
      }
    }
    if (current.license) {
      currentLicense.value = current.license
      if (current.license.machine_code && !current.instance_shared) {
        machineCode.value = current.license.machine_code
      }
    } else if (current.is_valid) {
      // Secondary tenant covered by instance grant without a nested license object.
      currentLicense.value = {
        id: 'instance-shared',
        license_key: '',
        is_valid: true,
        status: 'active',
        days_until_expiry: current.days_until_expiry,
        is_perpetual: current.days_until_expiry == null,
        organization_name: current.organization_name,
        ...(current.limits || {}),
      }
    } else {
      currentLicense.value = null
    }
    licenseHistory.value = history
    if (canManageInstanceLicense.value && !machineCode.value) {
      const mc = await fetchMachineCode()
      machineCode.value = mc.machine_code
    }
  } catch (e: unknown) {
    const msg = e && typeof e === 'object' && 'message' in e ? String((e as { message: string }).message) : ''
    ElMessage.error({ message: msg || t('settings.subscription.activateFailed'), grouping: true })
    currentLicense.value = null
    instanceShared.value = false
    canManageInstanceLicense.value = false
    enforcementEnabled.value = false
    licenseHistory.value = []
    limits.value = { ...FALLBACK_LIMITS }
    effectiveQuotaByKey.value = {}
  } finally {
    loading.value = false
  }
}

async function copyIdentification() {
  if (!machineCode.value) return
  try {
    await copyTextToClipboard(machineCode.value)
    ElMessage.success({ message: t('common.copied'), grouping: true })
  } catch {
    ElMessage.error({ message: t('settings.subscription.copyFailed'), grouping: true })
  }
}

async function submitActivate() {
  const code = activationCode.value.trim()
  if (!code) return
  activating.value = true
  try {
    await activateLicense(code)
    ElMessage.success({ message: t('settings.subscription.activateSuccess'), grouping: true })
    activationCode.value = ''
    await loadAll()
  } catch (e: unknown) {
    const msg = e && typeof e === 'object' && 'message' in e ? String((e as { message: string }).message) : ''
    ElMessage.error({ message: msg || t('settings.subscription.activateFailed'), grouping: true })
  } finally {
    activating.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <div
    v-loading="loading"
    class="subscription-page"
  >
    <p class="subscription-page__lead">
      {{ pageSubtitle }}
    </p>
    <ElAlert
      class="subscription-page__enforcement"
      :type="enforcementEnabled ? 'success' : 'info'"
      show-icon
      :closable="false"
      :title="enforcementEnabled
        ? t('settings.subscription.enforcementActiveHint')
        : t('settings.subscription.devNoEnforcement')"
    />
    <section class="subscription-section">
      <header class="subscription-section__header">
        <h2 class="subscription-section__title">
          {{ t('settings.subscription.overviewTitle') }}
        </h2>
        <ElButton
          text
          class="subscription-section__refresh"
          :title="t('common.refresh')"
          :disabled="loading"
          @click="loadAll"
        >
          <RefreshCw
            :size="16"
            :class="{ 'is-spinning': loading }"
          />
        </ElButton>
      </header>

      <div class="subscription-overview-grid">
        <div class="subscription-overview-item">
          <span class="subscription-overview-item__label">{{ t('settings.subscription.currentEdition') }}</span>
          <span class="subscription-overview-item__value">{{ editionLabel }}</span>
        </div>
        <div class="subscription-overview-item">
          <span class="subscription-overview-item__label">{{ t('settings.subscription.licenseStatus') }}</span>
          <span class="subscription-overview-item__value">{{ licenseStatusLabel }}</span>
        </div>
        <div class="subscription-overview-item">
          <span class="subscription-overview-item__label">{{ t('settings.subscription.daysRemainingLabel') }}</span>
          <span class="subscription-overview-item__value">{{ daysRemainingText }}</span>
        </div>
        <div class="subscription-overview-item">
          <span class="subscription-overview-item__label">{{ t('settings.subscription.expiresAtLabel') }}</span>
          <span class="subscription-overview-item__value">{{ expiryDateText }}</span>
        </div>
      </div>
    </section>

    <section class="subscription-section">
      <header class="subscription-section__header">
        <h2 class="subscription-section__title">
          {{ t('settings.subscription.limitsAndUsage') }}
        </h2>
      </header>
      <p class="subscription-quota-lifetime-hint">
        {{ t('settings.subscription.aiTokensLifetimeHint') }}
      </p>
      <div class="subscription-quota-grid">
        <div
          v-for="item in limitItems"
          :key="item.key"
          class="subscription-quota-item"
        >
          <span class="subscription-quota-item__label">
            {{ item.label }}
            <small v-if="item.limitSource" class="subscription-quota-item__scope">
              {{ item.limitSource === 'override'
                ? t('settings.subscription.manualOverride')
                : t('settings.subscription.planLimit') }}
            </small>
          </span>
          <span class="subscription-quota-item__numbers">
            {{ item.used }}
            <template v-if="item.suffix"> {{ item.suffix }}</template>
            / {{ formatLimit(item.limit) }}
            <template v-if="item.suffix && item.limit >= 0"> {{ item.suffix }}</template>
          </span>
          <ElProgress
            class="subscription-quota-item__progress"
            :percentage="effectiveProgressPercent(item)"
            :status="effectiveProgressStatus(item)"
            :show-text="item.limit >= 0 && item.usageStatus !== 'unknown'"
          />
        </div>
      </div>
    </section>

    <section class="subscription-section">
      <header class="subscription-section__header">
        <h2 class="subscription-section__title">
          {{ t('settings.subscription.activationTitle') }}
        </h2>
      </header>

      <div class="subscription-activation">
        <template v-if="canActivateHere">
          <div class="subscription-activation__step">
            <h3 class="subscription-activation__step-title">
              {{ t('settings.subscription.activationStep1Title') }}
            </h3>
            <div class="subscription-activation__code-row">
              <code class="subscription-activation__code">{{ machineCode || '—' }}</code>
              <ElButton
                type="primary"
                :disabled="!machineCode"
                @click="copyIdentification"
              >
                {{ t('settings.subscription.copyIdentification') }}
              </ElButton>
            </div>
            <p class="subscription-activation__hint">
              {{ t('settings.subscription.activationStep1Hint') }}
            </p>
          </div>

          <div class="subscription-activation__step">
            <h3 class="subscription-activation__step-title">
              {{ t('settings.subscription.activationStep2Title') }}
            </h3>
            <ElInput
              v-model="activationCode"
              type="textarea"
              :rows="4"
              :placeholder="t('settings.subscription.activationPlaceholder')"
              class="subscription-activation__input font-mono"
            />
            <div class="subscription-activation__actions">
              <ElButton
                type="primary"
                size="large"
                :loading="activating"
                :disabled="!activationCode.trim()"
                @click="submitActivate"
              >
                {{ t('settings.subscription.activateNow') }}
              </ElButton>
            </div>
          </div>
        </template>
        <p
          v-else
          class="subscription-activation__hint"
        >
          {{ t('settings.subscription.instanceSharedHint') }}
        </p>
      </div>
    </section>

    <section class="subscription-section subscription-section--last">
      <header class="subscription-section__header">
        <h2 class="subscription-section__title">
          {{ t('settings.subscription.historyTitle') }}
        </h2>
      </header>
      <HflTablePanel>
        <el-table
          v-table-column-resize="'settings.subscriptionHistory'"
          :data="licenseHistory"
          stripe
          row-key="id"
          class="hfl-list-table"
        >
          <template #empty>
            <el-empty :description="t('settings.subscription.historyEmpty')" />
          </template>

          <el-table-column
            :label="t('settings.subscription.historyLicenseKey')"
            min-width="220"
          >
            <template #default="{ row }">
              <span :class="{ 'hfl-empty-mark': !row.license_key }">{{ row.license_key || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('settings.subscription.historyChangeType')"
            min-width="180"
          >
            <template #default="{ row }">
              {{ historyChangeLabel(row) }}
            </template>
          </el-table-column>
          <el-table-column
            :label="t('settings.subscription.historyActivatedAt')"
            width="170"
          >
            <template #default="{ row }">
              <span
                class="hfl-table-cell-time"
                :class="{ 'hfl-empty-mark': !(row.activated_at || row.archived_at) }"
              >{{ formatDateTime(row.activated_at || row.archived_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('settings.subscription.historyValidUntil')"
            width="140"
          >
            <template #default="{ row }">
              <span :class="{ 'hfl-empty-mark': !row.is_perpetual && !row.expires_at }">{{ row.is_perpetual ? t('settings.subscription.unlimited') : formatDateOnly(row.expires_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('settings.subscription.historyStatus')"
            width="100"
          >
            <template #default="{ row }">
              <el-tag
                v-bind="historyStatusTagAttrs(row)"
                size="small"
              >
                {{ historyStatusLabel(row) }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </HflTablePanel>
    </section>
  </div>
</template>

<style scoped>
.subscription-page {
  display: flex;
  flex-direction: column;
  width: 100%;
  min-width: 0;
  font-weight: 400;
}

.subscription-page__lead {
  margin: 0 0 12px;
  color: var(--color-text-secondary, #6b6b78);
  font-size: 13px;
  line-height: 1.45;
}

.subscription-page__enforcement {
  margin-bottom: 20px;
}

.subscription-section {
  padding-bottom: 24px;
}

.subscription-section:not(.subscription-section--last) {
  margin-bottom: 24px;
  border-bottom: 1px solid var(--color-border-light, #e4e7ed);
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

.subscription-overview-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px 24px;
}

@media (max-width: 960px) {
  .subscription-overview-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .subscription-overview-grid {
    grid-template-columns: 1fr;
  }
}

.subscription-overview-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.subscription-overview-item__label {
  font-size: 12px;
  color: var(--color-text-tertiary, #909399);
}

.subscription-overview-item__value {
  font-size: 14px;
  color: var(--color-text-primary, #303133);
  word-break: break-word;
}

.subscription-quota-lifetime-hint {
  margin: 0 0 12px;
  color: var(--color-text-secondary, #64748b);
  font-size: 13px;
  line-height: 1.45;
}

.subscription-quota-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
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

.subscription-quota-item__scope {
  margin-left: 4px;
  color: var(--color-text-tertiary, #909399);
  font-size: 10px;
  font-weight: 400;
  white-space: nowrap;
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

.subscription-quota-item__progress :deep(.el-progress--without-text .el-progress-bar) {
  grid-column: 1 / -1;
}

.subscription-activation__step + .subscription-activation__step {
  margin-top: 24px;
}

.subscription-activation__step-title {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 400;
  color: var(--color-text-primary, #303133);
}

.subscription-activation__code-row {
  display: flex;
  flex-wrap: wrap;
  align-items: stretch;
  gap: 12px;
}

.subscription-activation__code {
  flex: 1 1 320px;
  min-width: 0;
  display: flex;
  align-items: center;
  margin: 0;
  padding: 7px 14px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  line-height: 1.5;
  color: var(--color-text-primary, #303133);
  word-break: break-all;
  background: rgb(248 250 252);
  border: 1px solid var(--color-border-light, #e4e7ed);
  border-radius: 8px;
}

.subscription-activation__hint {
  margin: 10px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--color-text-tertiary, #909399);
}

.subscription-activation__input :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.subscription-activation__actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
