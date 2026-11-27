<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { CircleAlert, LoaderCircle } from 'lucide-vue-next'
import { unwrapApiPayload } from '../../lib/parse'
import { fetchDeployProfile } from '../../composables/useDeployProfile'
import { impersonationHeaders, markImpersonationEnded, returnToAdmin, saveImpersonation, scopedImpersonationUrl, type ImpersonationContext } from '../../lib/impersonation'

const { t } = useI18n()
const failed = ref(false)
const adminUrl = ref('')
const controller = new AbortController()
let timer: ReturnType<typeof setTimeout> | undefined
let dispose = () => {}
let active = true
let savedContext = false
let notifyFailure = () => {}
let sessionId: string | null = null

function stop() {
  active = false
  clearTimeout(timer)
  dispose()
  controller.abort()
}

function fail() {
  if (!active) return
  stop()
  // Keep a partially established session fenced until an explicit full navigation.
  if (savedContext) markImpersonationEnded()
  failed.value = true
  // Release the initiator's timer and window ownership before offering a return.
  // No handoff material or error details cross the window boundary.
  try { notifyFailure() } catch { /* The initiating window may already be gone. */ }
}

onMounted(async () => {
  // One deadline covers setup, handoff, exchange, and identity confirmation.
  timer = setTimeout(fail, 60_000)
  try {
    // Verify storage before exchanging any one-time handoff.
    sessionStorage.setItem('hfl_impersonation_probe', '1')
    sessionStorage.removeItem('hfl_impersonation_probe')
    if (!window.opener) throw new Error('Missing initiator')
    const opener = window.opener
    const profile = await fetchDeployProfile(true)
    if (!active) return
    const admin = new URL(profile?.admin_console_url || '')
    if (!['https:', 'http:'].includes(admin.protocol) || admin.username || admin.password) throw new Error('Invalid administrator URL')
    adminUrl.value = admin.href
    const adminOrigin = admin.origin
    notifyFailure = () => opener.postMessage({ type: 'hfl-impersonation-failed', id: sessionId }, adminOrigin)
    const verifier = Array.from(crypto.getRandomValues(new Uint8Array(32)), b => b.toString(16).padStart(2, '0')).join('')
    const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
    if (!active) return
    const challenge = Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('')
    let consumed = false
    const receive = async (event: MessageEvent) => {
      if (!active || event.source !== opener || event.origin !== adminOrigin || event.data?.type !== 'hfl-impersonation-start' || consumed) return
      consumed = true
      sessionId = event.data.id
      try {
        const response = await fetch('/api/v1/impersonation/exchange', {
          method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: event.data.id, handoff_code: event.data.handoff_code, verifier }),
          signal: controller.signal,
        })
        if (!active) return
        if (!response.ok) throw new Error('Handoff failed')
        const info = unwrapApiPayload<ImpersonationContext & { landing_path: string }>(await response.json())
        if (!active) return
        saveImpersonation(info)
        savedContext = true
        const userResponse = await fetch('/api/v1/auth/token/refresh', { credentials: 'include', headers: impersonationHeaders(), signal: controller.signal })
        if (!active) return
        if (!userResponse.ok) throw new Error('Session confirmation failed')
        const session = unwrapApiPayload<{ user: { id: number; access_profile: { org_key: string } } }>(await userResponse.json())
        if (!active) return
        if (session.user?.id !== info.user_id || session.user?.access_profile?.org_key !== info.org_key) throw new Error('Context mismatch')
        opener.postMessage({ type: 'hfl-impersonation-entered', id: info.id }, adminOrigin)
        window.opener = null
        const landing = info.landing_path.startsWith('/') && !info.landing_path.startsWith('//') && !info.landing_path.startsWith('/platform-ops') ? info.landing_path : '/'
        window.location.replace(scopedImpersonationUrl(landing))
        stop()
      } catch { fail() }
    }
    window.addEventListener('message', receive)
    dispose = () => window.removeEventListener('message', receive)
    opener.postMessage({ type: 'hfl-impersonation-ready', challenge }, adminOrigin)
  } catch { fail() }
})
onBeforeUnmount(() => {
  if (active && savedContext) markImpersonationEnded()
  stop()
})
</script>

<template>
  <main
    class="impersonation-entry"
    aria-labelledby="impersonation-entry-title"
    aria-describedby="impersonation-entry-description"
  >
    <div
      class="impersonation-entry__content"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <component
        :is="failed ? CircleAlert : LoaderCircle"
        class="impersonation-entry__icon"
        :class="{ 'impersonation-entry__icon--loading': !failed }"
        :size="32"
        :stroke-width="1.75"
        aria-hidden="true"
        focusable="false"
      />
      <h1 id="impersonation-entry-title">
        {{ t(failed ? 'impersonation.entryFailed' : 'impersonation.starting') }}
      </h1>
      <p id="impersonation-entry-description">
        {{ t(failed ? 'impersonation.entryFailedDescription' : 'impersonation.startingDescription') }}
      </p>
    </div>
    <el-button
      v-if="failed && adminUrl"
      class="impersonation-entry__return"
      type="primary"
      @click="returnToAdmin(adminUrl)"
    >
      {{ t('impersonation.returnToAdmin') }}
    </el-button>
  </main>
</template>

<style scoped>
.impersonation-entry {
  display: flex;
  min-height: var(--app-viewport-height, 100vh);
  box-sizing: border-box;
  padding: 48px 24px calc(48px + var(--app-viewport-height, 100vh) / 10);
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: var(--color-content-bg);
  color: var(--color-text-primary);
  text-align: center;
}

.impersonation-entry__content {
  display: flex;
  width: 100%;
  max-width: 480px;
  min-width: 0;
  flex-direction: column;
  align-items: center;
}

.impersonation-entry__icon {
  flex-shrink: 0;
  margin-bottom: 16px;
  color: var(--color-text-secondary);
}

.impersonation-entry__icon--loading {
  color: var(--color-primary);
  animation: impersonation-entry-spin 1s linear infinite;
}

h1 {
  margin: 0;
  color: var(--color-text-title);
  font-size: 20px;
  font-weight: 600;
  line-height: 28px;
  text-wrap: balance;
  overflow-wrap: anywhere;
}

p {
  max-width: 352px;
  margin: 12px 0 0;
  color: var(--color-text-secondary);
  font-size: 14px;
  font-weight: 400;
  line-height: 22px;
  text-wrap: balance;
  overflow-wrap: anywhere;
}

.impersonation-entry .el-button.impersonation-entry__return:not(.is-link):not(.is-text) {
  max-width: 100%;
  height: auto;
  min-height: var(--impersonation-entry-button-height, 36px);
  margin: 24px 0 0;
  padding: 7px 16px;
  font-size: 14px;
  font-weight: 400;
  line-height: 20px;
  white-space: normal;
  overflow-wrap: anywhere;
  box-shadow: none !important;
}

@keyframes impersonation-entry-spin {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .impersonation-entry__icon--loading {
    animation: none;
  }
}

@media (max-width: 640px) {
  .impersonation-entry {
    --impersonation-entry-button-height: 44px;
  }
}
</style>
