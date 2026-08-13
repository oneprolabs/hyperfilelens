<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertTriangle } from 'lucide-vue-next'
import HflPopover from './HflPopover.vue'

const props = withDefaults(defineProps<{
  usedBytes: number
  limitBytes: number
  probeStatus?: string
  formatBytes: (value: number) => string
  warning?: boolean
  storageAvailableBytes?: number
  variant?: 'compact' | 'detail'
  showSupporting?: boolean
  showBar?: boolean
}>(), {
  probeStatus: 'pending',
  warning: false,
  storageAvailableBytes: 0,
  variant: 'compact',
  showSupporting: true,
  showBar: false,
})

const { t } = useI18n()

const status = computed(() => String(props.probeStatus || 'pending').toLowerCase())
const used = computed(() => Math.max(0, Number(props.usedBytes || 0)))
const limit = computed(() => Math.max(0, Number(props.limitBytes || 0)))
const hasLimit = computed(() => limit.value > 0)
const remainingLimit = computed(() => Math.max(0, limit.value - used.value))
const percent = computed(() => (
  hasLimit.value ? Math.min(100, Math.round((used.value / limit.value) * 100)) : 0
))
const stateLabel = computed(() => (
  status.value === 'failed'
    ? t('repositoriesPage.usageUnavailable')
    : t('repositoriesPage.metricsPending')
))
const supportingLabel = computed(() => (
  hasLimit.value
    ? t('repositoriesPage.estimatedConfiguredLimit')
    : t('repositoriesPage.estimatedNoConfiguredLimit')
))
const unavailableSupportingLabel = computed(() => (
  hasLimit.value
    ? t('repositoriesPage.configuredLimitValue', { value: props.formatBytes(limit.value) })
    : ''
))
</script>

<template>
  <div class="repository-estimated-usage" :class="`repository-estimated-usage--${variant}`">
    <template v-if="status === 'success'">
      <div class="repository-estimated-usage__numbers">
        <span class="repository-estimated-usage__used">≈ {{ formatBytes(used) }}</span>
        <span v-if="hasLimit" class="repository-estimated-usage__limit">/ {{ formatBytes(limit) }}</span>
        <span v-if="hasLimit" class="repo-usage-cell__percent hfl-table-no-tooltip">{{ percent }}%</span>
        <HflPopover
          v-if="warning"
          trigger="hover"
          placement="bottom-start"
          :width="440"
          :fallback-placements="['top-start', 'bottom-end']"
          popper-class="repository-info-popper repository-warning-popper"
        >
          <template #reference>
            <AlertTriangle class="repository-estimated-usage__warning" :size="15" aria-hidden="true" />
          </template>
          <div class="repository-info-popover repository-capacity-popover">
            <div class="repository-info-popover__head repository-capacity-popover__head">
              <span class="repository-capacity-popover__icon" aria-hidden="true">
                <AlertTriangle :size="17" />
              </span>
              <div class="repository-info-popover__title">
                {{ t('repositoriesPage.capacityConflictTitle') }}
              </div>
            </div>
            <dl class="repository-info-popover__rows repository-capacity-popover__metrics">
              <div class="repository-info-popover__row">
                <dt>{{ t('repositoriesPage.remainingLimit') }}</dt>
                <dd>≈ {{ formatBytes(remainingLimit) }}</dd>
              </div>
              <div class="repository-info-popover__row">
                <dt>{{ t('repositoriesPage.available') }}</dt>
                <dd>{{ formatBytes(storageAvailableBytes) }}</dd>
              </div>
            </dl>
            <div class="repository-info-popover__note repository-capacity-popover__risk">
              <span>
                <span class="repository-capacity-popover__formula">{{ t('repositoriesPage.remainingLimitFormula') }}</span>
                <span>{{ t('repositoriesPage.capacityConflictRisk') }}</span>
              </span>
            </div>
          </div>
        </HflPopover>
      </div>
      <div v-if="hasLimit && showBar" class="repo-usage-bar">
        <span class="repo-usage-bar__fill" :style="{ width: `${percent}%` }" />
      </div>
      <span v-if="showSupporting" class="repository-estimated-usage__supporting">{{ supportingLabel }}</span>
    </template>
    <template v-else>
      <span class="repository-estimated-usage__state">{{ stateLabel }}</span>
      <span v-if="unavailableSupportingLabel" class="repository-estimated-usage__supporting">
        {{ unavailableSupportingLabel }}
      </span>
    </template>
  </div>
</template>
