<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { unwrapApiPayload } from '../../lib/parse'
import { fetchDeployProfile } from '../../composables/useDeployProfile'
import { impersonationHeaders, saveImpersonation, scopedImpersonationUrl, type ImpersonationContext } from '../../lib/impersonation'

const { t } = useI18n()
const failed = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let dispose = () => {}

onMounted(async () => {
  try {
    // Verify storage before exchanging any one-time handoff.
    sessionStorage.setItem('hfl_impersonation_probe', '1')
    sessionStorage.removeItem('hfl_impersonation_probe')
    if (!window.opener) throw new Error('Missing initiator')
    const opener = window.opener
    const profile = await fetchDeployProfile(true)
    const adminOrigin = new URL(profile?.admin_console_url || '').origin
    const verifier = Array.from(crypto.getRandomValues(new Uint8Array(32)), b => b.toString(16).padStart(2, '0')).join('')
    const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
    const challenge = Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('')
    let consumed = false
    const receive = async (event: MessageEvent) => {
      if (event.source !== opener || event.origin !== adminOrigin || event.data?.type !== 'hfl-impersonation-start' || consumed) return
      consumed = true
      clearTimeout(timer)
      try {
        const response = await fetch('/api/v1/impersonation/exchange', {
          method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: event.data.id, handoff_code: event.data.handoff_code, verifier }),
        })
        if (!response.ok) throw new Error('Handoff failed')
        const info = unwrapApiPayload<ImpersonationContext & { landing_path: string }>(await response.json())
        saveImpersonation(info)
        const userResponse = await fetch('/api/v1/auth/token/refresh', { credentials: 'include', headers: impersonationHeaders() })
        if (!userResponse.ok) throw new Error('Session confirmation failed')
        const session = unwrapApiPayload<{ user: { id: number; access_profile: { org_key: string } } }>(await userResponse.json())
        if (session.user?.id !== info.user_id || session.user?.access_profile?.org_key !== info.org_key) throw new Error('Context mismatch')
        opener.postMessage({ type: 'hfl-impersonation-entered', id: info.id }, adminOrigin)
        window.opener = null
        const landing = info.landing_path.startsWith('/') && !info.landing_path.startsWith('//') && !info.landing_path.startsWith('/platform-ops') ? info.landing_path : '/'
        window.location.replace(scopedImpersonationUrl(landing))
      } catch { failed.value = true }
    }
    window.addEventListener('message', receive)
    dispose = () => window.removeEventListener('message', receive)
    opener.postMessage({ type: 'hfl-impersonation-ready', challenge }, adminOrigin)
    timer = setTimeout(() => { failed.value = true; dispose() }, 60_000)
  } catch { failed.value = true }
})
onBeforeUnmount(() => { clearTimeout(timer); dispose() })
</script>
<template>
  <main class="impersonation-state" role="status">
    <h1>{{ t(failed ? 'impersonation.entryFailed' : 'impersonation.starting') }}</h1>
    <p v-if="failed">{{ t('impersonation.entryFailedDescription') }}</p>
  </main>
</template>
<style scoped>
.impersonation-state { max-width: 36rem; margin: 18vh auto; padding: 24px; text-align: center; color: var(--text-primary); }
h1 { font-size: 20px; font-weight: 600; } p { color: var(--text-secondary); margin-top: 12px; }
</style>
