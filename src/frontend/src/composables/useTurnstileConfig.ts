import { computed, nextTick, ref } from 'vue'
import { api } from '../lib/api'
import {
  preloadTurnstileScript,
  resetTurnstileScriptLoad,
} from '../lib/turnstileLoader'

export type TurnstileState = 'pending' | 'disabled' | 'ready' | 'blocked'

interface TurnstileConfigResponse {
  code: string
  data: {
    enabled: boolean
    configured: boolean
    site_key?: string
    login_exempt?: boolean
  }
}

const state = ref<TurnstileState>('pending')
const siteKey = ref('')
const configLoaded = ref(false)
const loginExempt = ref(false)
let configLoadPromise: Promise<void> | null = null
let configRetryPromise: Promise<void> | null = null
const authTurnstileMountGeneration = ref(0)
async function loadTurnstileConfig(force = false): Promise<void> {
  if (configLoadPromise) return configLoadPromise
  if (configLoaded.value && !force) return

  state.value = 'pending'
  configLoadPromise = (async () => {
    try {
      const res = await api<TurnstileConfigResponse>('/api/v1/auth/turnstile/config')
      if (res.code !== '0000' || !res.data) {
        throw new Error('Invalid Turnstile configuration response')
      }
      loginExempt.value = res.data.login_exempt === true
      if (!res.data.enabled) {
        state.value = 'disabled'
        siteKey.value = ''
        configLoaded.value = true
        return
      }
      if (!res.data.configured || !res.data.site_key) {
        state.value = 'blocked'
        siteKey.value = ''
        configLoaded.value = true
        return
      }

      siteKey.value = res.data.site_key
      state.value = 'ready'
      // The mounted widget owns user-visible load failure handling. Prefetch
      // failures are intentionally swallowed here to avoid an unhandled
      // rejection before the lazy authentication page finishes mounting.
      if (!loginExempt.value) {
        void preloadTurnstileScript().catch(() => undefined)
      }
      configLoaded.value = true
    } catch {
      // A failed request does not establish that Turnstile is enabled. Keep
      // the optional field hidden; auth endpoints remain the security boundary.
      state.value = 'disabled'
      loginExempt.value = false
      siteKey.value = ''
      configLoaded.value = false
    } finally {
      configLoadPromise = null
    }
  })()
  return configLoadPromise
}

async function retryTurnstileConfig(): Promise<void> {
  if (configRetryPromise) return configRetryPromise
  if (configLoadPromise) return configLoadPromise

  configRetryPromise = (async () => {
    // Leave the ready state first so any mounted widget is removed before the
    // shared Turnstile API and script tag are discarded.
    state.value = 'pending'
    configLoaded.value = false
    await nextTick()
    resetTurnstileScriptLoad()
    authTurnstileMountGeneration.value += 1
    await loadTurnstileConfig(true)
  })()

  try {
    await configRetryPromise
  } finally {
    configRetryPromise = null
  }
}

export function resetAuthTurnstileSession(): void {
  if (state.value === 'blocked' && siteKey.value) state.value = 'ready'
  authTurnstileMountGeneration.value += 1
}

export function prefetchAuthTurnstile(): void {
  resetAuthTurnstileSession()
  // Re-evaluate IP-specific exemptions on each new authentication navigation.
  void loadTurnstileConfig(true)
}

export function useTurnstileConfig(action = '') {
  // Registration/reset share the configuration but never the login exemption.
  const effectiveState = computed<TurnstileState>(() =>
    action === 'login' && loginExempt.value && state.value !== 'pending'
      ? 'disabled'
      : state.value,
  )
  const isTurnstilePending = computed(() => effectiveState.value === 'pending')
  const isTurnstileDisabled = computed(() => effectiveState.value === 'disabled')
  const isTurnstileReady = computed(() => effectiveState.value === 'ready')
  const isTurnstileBlocked = computed(() => effectiveState.value === 'blocked')

  function blockTurnstile(): void {
    if (state.value !== 'disabled') state.value = 'blocked'
  }

  function buildTurnstilePayload(token: string): Record<string, string> {
    return isTurnstileReady.value ? { turnstile_token: token } : {}
  }

  return {
    isTurnstileConfigLoaded: computed(() => configLoaded.value),
    turnstileState: effectiveState,
    turnstileSiteKey: computed(() => isTurnstileDisabled.value ? '' : siteKey.value),
    authTurnstileMountGeneration,
    isTurnstilePending,
    isTurnstileDisabled,
    isTurnstileReady,
    isTurnstileBlocked,
    loadTurnstileConfig,
    retryTurnstileConfig,
    buildTurnstilePayload,
    blockTurnstile,
  }
}
