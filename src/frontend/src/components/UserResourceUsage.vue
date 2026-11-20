<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RefreshCw } from 'lucide-vue-next'
import { currentUser, getEffectiveOrgKey } from '../composables/useAuth'
import { isAbortError } from '../lib/api'
import { formatQuotaBytes, quotaMeterLabelKey } from '../lib/licenseQuotaDisplay'
import { fetchUserUsage, validUserUsage, type UserUsageItem } from '../lib/userUsageApi'

const props = withDefaults(defineProps<{ userId?: number; active?: boolean; showTitle?: boolean; showRefresh?: boolean }>(), {
  active: true,
  userId: undefined,
  showTitle: true,
  showRefresh: true,
})
const { t, locale } = useI18n()
const items = ref<UserUsageItem[]>([])
const loading = ref(false)
const failed = ref(false)
const denied = ref(false)
let controller: AbortController | undefined
let generation = 0
const errorText = computed(() => t(denied.value ? 'accountUsage.denied' : 'accountUsage.loadFailed'))

function display(item: UserUsageItem, value: number | null = item.used): string {
  if (value === null) return '—'
  return item.unit === 'bytes' ? formatQuotaBytes(value)
    : new Intl.NumberFormat(locale.value).format(value)
}
function percentage(item: UserUsageItem): number {
  if (item.limit < 0 || item.used === null) return 0
  if (item.limit === 0) return item.used > 0 ? 100 : 0
  return Math.min(100, Math.max(0, item.used / item.limit * 100))
}
function measurementHint(item: UserUsageItem): string {
  if (item.status === 'complete') return ''
  if (item.reason === 'storage_attribution') return ''
  const key = ['storage_attribution', 'workspace_attribution', 'unattributed_history'].includes(item.reason)
    ? item.reason : 'unavailable'
  return `${t(`accountUsage.reasons.${key}`)}${item.status === 'partial'
    ? ` ${t('accountUsage.knownUsage', { value: display(item, item.known_used) })}` : ''}`
}
async function load() {
  const request = ++generation
  controller?.abort()
  controller = new AbortController()
  const signal = controller.signal
  items.value = []
  failed.value = false
  denied.value = false
  loading.value = false
  const accountId = currentUser.value?.id
  const orgKey = getEffectiveOrgKey()
  const target = props.userId ?? accountId
  if (!props.active || !orgKey || !accountId || !target) return
  loading.value = true
  try {
    const data = await fetchUserUsage(props.userId, signal)
    if (signal.aborted || request !== generation || orgKey !== getEffectiveOrgKey()
      || accountId !== currentUser.value?.id) return
    if (!validUserUsage(data, orgKey, target)) {
      throw new Error('Invalid organization-user usage response.')
    }
    items.value = data.items
  } catch (error) {
    if (signal.aborted || request !== generation || isAbortError(error)
      || orgKey !== getEffectiveOrgKey() || accountId !== currentUser.value?.id) return
    failed.value = true
    denied.value = (error as { status?: number }).status === 403
  } finally {
    if (request === generation) loading.value = false
  }
}
watch([
  () => props.userId, () => props.active, () => currentUser.value?.id,
  () => currentUser.value?.access_profile?.org_key,
], () => { void load() }, { immediate: true })
onBeforeUnmount(() => { ++generation; controller?.abort() })
</script>

<template>
  <section
    class="user-resource-usage"
    :aria-label="t('accountUsage.resources')"
    :aria-busy="loading"
  >
    <header
      v-if="props.showTitle || props.showRefresh"
      class="user-resource-usage__header"
      :class="{ 'user-resource-usage__header--without-title': !props.showTitle }"
    >
      <h2 v-if="props.showTitle">
        {{ t('accountUsage.resources') }}
      </h2>
      <button
        v-if="props.showRefresh"
        type="button"
        class="user-resource-usage__refresh"
        :aria-label="t('common.refresh')"
        :title="t('common.refresh')"
        :disabled="loading"
        @click="load"
      >
        <RefreshCw
          :size="16"
          :class="{ 'is-loading': loading }"
          aria-hidden="true"
        />
      </button>
    </header>
    <div
      v-if="loading"
      class="user-resource-usage__message"
      role="status"
    >
      {{ t('common.loading') }}
    </div>
    <div
      v-else-if="failed"
      class="user-resource-usage__message"
      role="status"
    >
      <span>{{ errorText }}</span>
      <button
        type="button"
        class="user-resource-usage__retry"
        @click="load"
      >
        {{ t('common.retry') }}
      </button>
    </div>
    <div
      v-else
      class="user-resource-usage__grid"
    >
      <div
        v-for="item in items"
        :key="item.key"
        class="user-resource-usage__item"
        :data-meter="item.key"
      >
        <span class="user-resource-usage__label">{{ t(quotaMeterLabelKey(item.key)) }}</span>
        <span
          class="user-resource-usage__value"
          :title="measurementHint(item) || undefined"
        >{{ display(item) }}</span>
        <div class="user-resource-usage__progress">
          <span
            class="user-resource-usage__track"
            :class="{ 'is-exceeded': item.limit >= 0 && item.used !== null && item.used > item.limit }"
            :role="item.limit >= 0 && item.used !== null ? 'progressbar' : undefined"
            :aria-hidden="item.limit < 0 || item.used === null ? true : undefined"
            :aria-label="item.limit >= 0 && item.used !== null ? t(quotaMeterLabelKey(item.key)) : undefined"
            :aria-valuemin="item.limit >= 0 && item.used !== null ? 0 : undefined"
            :aria-valuemax="item.limit >= 0 && item.used !== null ? item.limit : undefined"
            :aria-valuenow="item.limit >= 0 && item.used !== null ? Math.min(item.used, item.limit) : undefined"
          >
            <span
              v-if="item.limit >= 0 && item.used !== null"
              class="user-resource-usage__fill"
              :style="{ width: `${percentage(item)}%` }"
            />
          </span>
          <span class="user-resource-usage__limit">{{ item.limit < 0 ? t('settings.subscription.unlimited') : display(item, item.limit) }}</span>
        </div>
        <p
          v-if="item.status !== 'complete' && measurementHint(item)"
          class="user-resource-usage__hint"
        >
          {{ measurementHint(item) }}
        </p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.user-resource-usage { container-type: inline-size; min-width: 0; }
.user-resource-usage__header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.user-resource-usage__header--without-title { justify-content: flex-end; margin-bottom: 12px; }
.user-resource-usage__header h2 { margin: 0; color: var(--el-text-color-primary); font-size: 15px; font-weight: 600; line-height: 1.4; }
.user-resource-usage__refresh { display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border: 1px solid var(--el-border-color); border-radius: 4px; color: var(--el-text-color-regular); background: var(--el-bg-color); cursor: pointer; }
.user-resource-usage__refresh:hover { color: var(--el-color-primary); border-color: var(--el-color-primary); }
.user-resource-usage__refresh:focus-visible, .user-resource-usage__retry:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 3px; }
.user-resource-usage__refresh:disabled { cursor: wait; opacity: .6; }
.user-resource-usage__grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.user-resource-usage__item { display: grid; min-width: 0; grid-template-columns: minmax(0, 1fr) auto; column-gap: 8px; row-gap: 8px; align-items: center; padding: 12px; border: 1px solid var(--color-border-light, #e4e7ed); border-radius: 12px; background: rgb(248 250 252 / 0.5); }
.user-resource-usage__label { min-width: 0; overflow: hidden; color: var(--color-text-secondary, #606266); font-size: 12px; line-height: normal; text-overflow: ellipsis; white-space: nowrap; }
.user-resource-usage__value { color: var(--color-text-primary, #303133); font-size: 12px; line-height: normal; font-variant-numeric: tabular-nums; white-space: nowrap; }
.user-resource-usage__progress { display: grid; grid-column: 1 / -1; grid-template-columns: minmax(0, 1fr) max-content; column-gap: 12px; align-items: center; min-width: 0; }
.user-resource-usage__track { display: block; overflow: hidden; min-width: 0; height: 5px; border-radius: 3px; background: var(--el-fill-color); }
.user-resource-usage__fill { display: block; height: 100%; border-radius: inherit; background: var(--el-color-primary); }
.user-resource-usage__track.is-exceeded .user-resource-usage__fill { background: var(--el-color-danger); }
.user-resource-usage__limit { color: var(--color-text-secondary, #606266); font-size: 12px; line-height: 1; white-space: nowrap; }
.user-resource-usage__hint { grid-column: 1 / -1; margin: 0; color: var(--el-text-color-secondary); font-size: 12px; line-height: 20px; }
.user-resource-usage__message { display: flex; align-items: center; gap: 12px; padding: 24px 0; color: var(--el-text-color-secondary); font-size: 14px; }
.user-resource-usage__retry { border: 0; padding: 4px 8px; color: var(--el-color-primary); background: transparent; cursor: pointer; font: inherit; }
.is-loading { animation: usage-spin 1s linear infinite; }
@keyframes usage-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .is-loading { animation: none; } }
@container (min-width: 601px) and (max-width: 960px) { .user-resource-usage__grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@container (max-width: 600px) { .user-resource-usage__grid { grid-template-columns: minmax(0, 1fr); gap: 12px; } }
</style>
