<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  configuredLimitBytes: number
  estimatedUsageBytes: number
  storageAvailableBytes: number
  formatBytes: (value: number) => string
}>()

const { t } = useI18n()

const limit = computed(() => Math.max(0, Number(props.configuredLimitBytes || 0)))
const usage = computed(() => Math.max(0, Number(props.estimatedUsageBytes || 0)))
const remaining = computed(() => Math.max(0, limit.value - usage.value))
const available = computed(() => Math.max(0, Number(props.storageAvailableBytes || 0)))
const visible = computed(() => limit.value > 0 && remaining.value > available.value)
</script>

<template>
  <ElAlert
    v-if="visible"
    type="warning"
    :closable="false"
    show-icon
    :title="t('repositoriesPage.capacityConflictTitle')"
    class="repository-capacity-alert"
  >
    <div class="repository-capacity-alert__content">
      <dl class="repository-capacity-alert__metrics">
        <div>
          <dt>{{ t('repositoriesPage.remainingLimit') }}</dt>
          <dd>≈ {{ formatBytes(remaining) }}</dd>
        </div>
        <div>
          <dt>{{ t('repositoriesPage.available') }}</dt>
          <dd>{{ formatBytes(available) }}</dd>
        </div>
      </dl>
      <div class="repository-capacity-alert__note">
        {{ t('repositoriesPage.remainingLimitFormula') }}<br>
        {{ t('repositoriesPage.capacityConflictRisk') }}
      </div>
    </div>
  </ElAlert>
</template>
