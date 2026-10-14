<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import { apiErrorMessage } from '../../lib/api'
import { formatLocalDateTime } from '../../lib/dateTime'
import {
  fetchCopilotUsageDetail,
  type LensCopilotUsageDetail,
  type LensCopilotUsageItem,
} from '../../lib/lensApi'

const props = defineProps<{ modelValue: boolean; row: LensCopilotUsageItem | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const { t } = useI18n()
// Same level-one width and shared presentation as HostSourceDetailDrawer.
const { drawerSize } = useResponsiveDrawerWidth()
const open = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})
const detail = ref<LensCopilotUsageDetail | null>(null)
const loading = ref(false)
const loadError = ref('')
let requestId = 0

watch(() => [props.modelValue, props.row?.run_uuid] as const, async ([visible, runUuid]) => {
  const id = ++requestId
  detail.value = null
  loadError.value = ''
  loading.value = false
  if (!visible || !runUuid) return
  loading.value = true
  try {
    const result = await fetchCopilotUsageDetail(runUuid, true)
    if (id === requestId) detail.value = result
  } catch (error) {
    if (id === requestId) loadError.value = apiErrorMessage(error, t('insight.usage.detail.loadFailed'))
  } finally {
    if (id === requestId) loading.value = false
  }
}, { immediate: true })

onBeforeUnmount(() => { requestId++ })

function statusLabel(status: string) {
  if (status === 'done') return t('insight.usage.statusCompleted')
  if (status === 'failed') return t('insight.usage.statusFailed')
  if (status === 'cancelled') return t('insight.usage.statusCancelled')
  if (status === 'queued') return t('insight.usage.statusQueued')
  if (['running', 'streaming', 'awaiting_user_input'].includes(status)) return t('insight.usage.statusInProgress')
  return status || t('insight.usage.statusUnknown')
}

const overview = computed(() => {
  const row = detail.value
  if (!row) return []
  const gateway = row.gateway_name || (row.gateway_mode === 'auto'
    ? t('insight.usage.detail.automaticGateway') : row.gateway_mode)
  return [
    { label: t('insight.usage.chat'), value: row.chat_title },
    { label: t('insight.usage.backupSource'), value: row.backup_source_name },
    { label: t('insight.usage.status'), value: statusLabel(row.status) },
    { label: t('insight.usage.time'), value: formatLocalDateTime(row.time) },
    { label: t('insight.usage.detail.started'), value: formatLocalDateTime(row.started_at) },
    { label: t('insight.usage.detail.finished'), value: formatLocalDateTime(row.finished_at) },
    { label: t('insight.usage.detail.gateway'), value: gateway },
    { label: t('insight.usage.detail.models'), value: row.models_used?.join(', ') },
  ]
})
</script>

<template>
  <ElDrawer
    v-model="open"
    direction="rtl"
    destroy-on-close
    :modal="true"
    :size="drawerSize"
    class="hfl-detail-drawer insight-detail-drawer usage-request-drawer"
  >
    <template #header>
      <span class="hfl-detail-drawer__title">{{ t('insight.usage.detail.title') }}</span>
    </template>
    <div
      v-loading="loading"
      class="hfl-detail-drawer__body"
    >
      <ElAlert
        v-if="loadError"
        :title="loadError"
        type="error"
        show-icon
        :closable="false"
      />
      <div
        v-else-if="detail"
        class="hfl-detail-sections"
      >
        <section class="hfl-detail-section">
          <h4 class="hfl-detail-section__title">
            {{ t('insight.usage.detail.overview') }}
          </h4>
          <div class="hfl-detail-grid">
            <div
              v-for="item in overview"
              :key="item.label"
              class="hfl-detail-row"
            >
              <span class="hfl-detail-row__label">{{ item.label }}</span>
              <span
                class="hfl-detail-row__value hfl-detail-row__value--break"
                :class="{ 'hfl-empty-mark': !item.value || item.value === '—' }"
              >{{ item.value || '—' }}</span>
            </div>
            <div
              v-if="detail.source_scopes?.length"
              class="hfl-detail-row hfl-detail-row--full"
            >
              <span class="hfl-detail-row__label">{{ t('insight.usage.detail.scopes') }}</span>
              <span class="hfl-detail-row__value hfl-detail-row__value--break">{{ detail.source_scopes.map(scope => scope.source_path).join('\n') }}</span>
            </div>
          </div>
        </section>
        <section class="hfl-detail-section">
          <h4 class="hfl-detail-section__title">
            {{ t('insight.usage.question') }}
          </h4>
          <p class="usage-request-content">
            {{ detail.question || t('insight.usage.questionUnavailable') }}
          </p>
        </section>
        <section class="hfl-detail-section">
          <h4 class="hfl-detail-section__title">
            {{ t('insight.usage.detail.answer') }}
          </h4>
          <p
            class="usage-request-content"
            :class="{ 'hfl-empty-mark': !detail.answer }"
          >
            {{ detail.answer || t('insight.usage.detail.answerUnavailable') }}
          </p>
        </section>
        <section
          v-if="detail.error"
          class="hfl-detail-section"
        >
          <h4 class="hfl-detail-section__title">
            {{ t('insight.usage.detail.error') }}
          </h4>
          <p class="usage-request-content">
            {{ detail.error }}
          </p>
        </section>
      </div>
    </div>
  </ElDrawer>
</template>

<style scoped>
.usage-request-content {
  margin: 0;
  padding: 14px 16px;
  color: var(--el-text-color-primary);
  font-size: 13px;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
