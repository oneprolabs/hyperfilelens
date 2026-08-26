<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'

import { fetchCurrentUser, setStoredOrgKey } from '../../composables/useAuth'
import { useLocaleSwitch } from '../../composables/useLocaleSwitch'
import { resolvePostLoginPath } from '../../composables/useDeployProfile'
import { trackAppEvent } from '../../lib/analytics'
import {
  clearLoginLocaleSelection,
  clearPendingLoginLocale,
  getLoginLocaleSelection,
  getPendingLoginLocale,
} from '../../i18n'
import { selectLocale } from '../../i18n'
import { installedLangPacks } from '../../lib/langPacks'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { syncAuthenticatedLocale } = useLocaleSwitch()
const loading = ref(true)
let stopLocaleWatch: (() => void) | null = null

function syncPendingLoginLocale() {
  const selectedLocale = getLoginLocaleSelection()
  if (!selectedLocale) return true
  if (!syncAuthenticatedLocale(selectedLocale)) return false
  clearLoginLocaleSelection()
  stopLocaleWatch?.()
  stopLocaleWatch = null
  return true
}

onMounted(async () => {
  const orgKey = route.query.org_key
  if (typeof orgKey === 'string' && orgKey.trim()) {
    setStoredOrgKey(orgKey.trim())
  }

  const user = await fetchCurrentUser()
  loading.value = false

  if (user) {
    const pendingLocale = getPendingLoginLocale()
    if (pendingLocale) selectLocale(pendingLocale)
    if (!syncPendingLoginLocale()) {
      stopLocaleWatch = watch(installedLangPacks, () => {
        syncPendingLoginLocale()
      })
    }
    clearPendingLoginLocale()
    trackAppEvent('login', { method: 'google' })
    router.replace(await resolvePostLoginPath())
    return
  }

  clearLoginLocaleSelection()
  clearPendingLoginLocale()
  ElMessage.error({ message: t('login.googleLoginFailed'), grouping: true })
  router.replace('/login')
})

onUnmounted(() => {
  stopLocaleWatch?.()
})
</script>

<template>
  <div class="oauth-callback">
    <p>{{ loading ? t('login.googleCompleting') : t('login.googleRedirecting') }}</p>
  </div>
</template>

<style scoped>
.oauth-callback {
  min-height: var(--app-viewport-height);
  display: flex;
  align-items: center;
  justify-content: center;
  background: #08090c;
  color: #fff;
}
</style>
