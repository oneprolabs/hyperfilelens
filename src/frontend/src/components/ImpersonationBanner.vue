<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { UserRound } from 'lucide-vue-next'
import { impersonationContext, endImpersonation, markImpersonationEnded } from '../lib/impersonation'
import { formatLocalDateTime } from '../lib/dateTime'
const emit = defineEmits<{ height: [value: number] }>()
const element = ref<HTMLElement>()
let observer: ResizeObserver | undefined
const { t } = useI18n()
const busy = ref(false)
const error = ref('')
const info = computed(() => impersonationContext.value)
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  const measure = () => emit('height', Math.ceil(element.value?.getBoundingClientRect().height || 0))
  if (typeof ResizeObserver !== 'undefined' && element.value) {
    observer = new ResizeObserver(measure)
    observer.observe(element.value)
  }
  measure()
  timer = setInterval(() => {
    if (info.value?.expires_at && Date.parse(info.value.expires_at) <= Date.now()) {
      markImpersonationEnded()
      window.location.replace('/impersonation/ended')
    }
  }, 1000)
})
onBeforeUnmount(() => { clearInterval(timer); observer?.disconnect(); emit('height', 0) })
async function end() {
  busy.value = true
  error.value = ''
  try { await endImpersonation() } catch { error.value = t('impersonation.endFailed') } finally { busy.value = false }
}
</script>
<template>
  <div v-if="info" ref="element" class="impersonation-banner" role="status">
    <UserRound :size="16" aria-hidden="true" />
    <div class="impersonation-banner__text">
      <span>{{ t('impersonation.actingAs', { user: info.user_email, organization: info.org_name }) }}</span>
      <span v-if="info.expires_at" class="impersonation-banner__expiry">{{ t('impersonation.expiresAt', { time: formatLocalDateTime(info.expires_at) }) }}</span>
      <span v-if="error" role="alert">{{ error }}</span>
    </div>
    <el-button size="small" :loading="busy" @click="end">{{ t('impersonation.endSession') }}</el-button>
  </div>
</template>
<style scoped>
.impersonation-banner { display:flex; align-items:center; gap:12px; padding:10px 24px; color:var(--el-color-warning-dark-2); background:var(--el-color-warning-light-9); border-bottom:1px solid var(--el-color-warning-light-7); }
.impersonation-banner__text { display:flex; flex:1; flex-wrap:wrap; gap:4px 16px; font-size:13px; overflow-wrap:anywhere; }
.impersonation-banner__expiry { color:var(--text-secondary); }
@media(max-width:640px) { .impersonation-banner { padding:10px 12px; gap:8px; } }
</style>
