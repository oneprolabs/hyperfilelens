<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { currentUser, getEffectiveOrgKey } from '../../../composables/useAuth'
import { organizationCapabilityAvailable } from '../../../lib/organizationCapabilities'
import { api, isAbortError } from '../../../lib/api'
import { unwrapApiPayload } from '../../../lib/parse'

const props = defineProps<{ hasAvailableTarget: boolean; targetsLoading?: boolean }>()
const emit = defineEmits<{ ready: [] }>()
const { t } = useI18n()
type State = 'preparing' | 'failed' | 'ready' | 'missing' | 'unavailable'
const state = ref<State>()
const failed = ref(false)
const loading = ref(false)
const enabled = computed(() => {
  const profile = currentUser.value?.access_profile
  return profile?.organization_mode === 'personal' && organizationCapabilityAvailable(profile, 'managed_storage')
})
let abort: AbortController | undefined
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0
let disposed = false
const shown = computed(() => enabled.value && !props.hasAvailableTarget && !props.targetsLoading
  && (failed.value || state.value !== undefined))
const message = computed(() => t(`managedStorage.readiness.${failed.value ? 'loadFailed' : state.value}`))
const severity = computed(() => failed.value || state.value === 'failed' ? 'warning' : 'info')

async function refresh() {
  abort?.abort()
  clearTimeout(timer)
  const org = getEffectiveOrgKey()
  const token = ++generation
  if (!enabled.value || !org || disposed) return
  const controller = new AbortController()
  abort = controller
  loading.value = true
  const previous = state.value
  try {
    const response = unwrapApiPayload<{ state: State }>(await api<unknown>(
      '/api/v1/subscription/my-storage/', {
        headers: { 'X-Org-Key': org }, signal: controller.signal,
      },
    ))
    if (!['preparing', 'failed', 'ready', 'missing', 'unavailable'].includes(response.state)) {
      throw new Error('Invalid storage readiness response.')
    }
    if (disposed || controller.signal.aborted || token !== generation || org !== getEffectiveOrgKey()) return
    state.value = response.state
    failed.value = false
    if (response.state === 'ready' && previous !== 'ready') emit('ready')
    if (response.state === 'preparing' && !props.hasAvailableTarget) {
      timer = setTimeout(() => void refresh(), 5000)
    }
  } catch (error) {
    if (!disposed && !isAbortError(error) && token === generation && org === getEffectiveOrgKey()) {
      failed.value = true
    }
  } finally {
    if (token === generation) loading.value = false
  }
}

watch([enabled, () => getEffectiveOrgKey()], () => {
  abort?.abort()
  clearTimeout(timer)
  generation++
  state.value = undefined
  failed.value = false
  loading.value = false
  if (enabled.value) void refresh()
}, { immediate: true })
watch(() => props.hasAvailableTarget, available => {
  if (available) clearTimeout(timer)
  else if (enabled.value) void refresh()
})
onBeforeUnmount(() => {
  disposed = true
  generation++
  abort?.abort()
  clearTimeout(timer)
})
</script>

<template>
  <el-alert
    v-if="shown"
    :type="severity"
    :closable="false"
    show-icon
    class="personal-storage-readiness"
    role="status"
  >
    <div class="personal-storage-readiness__content">
      <span>{{ message }}</span>
      <el-button
        link
        type="primary"
        :loading="loading"
        :disabled="loading"
        @click="refresh"
      >
        {{ t('common.refresh') }}
      </el-button>
    </div>
  </el-alert>
</template>

<style scoped>
.personal-storage-readiness { margin-bottom: 16px; }
.personal-storage-readiness__content { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
</style>
