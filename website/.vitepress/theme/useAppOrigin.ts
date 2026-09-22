import { computed, onMounted, ref } from 'vue'
import { trackWebsiteOpenApp, type WebsiteOpenAppPlacement } from './analytics'

function validOrigin(value: string): string {
  try {
    const parsed = new URL(value)
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) return ''
    if (parsed.pathname !== '/' || parsed.search || parsed.hash) return ''
    return parsed.origin
  } catch {
    return ''
  }
}

function directAppOrigin(): string {
  const hostname = window.location.hostname || '127.0.0.1'
  const host = hostname.includes(':') ? `[${hostname}]` : hostname
  return `https://${host}:11443`
}

/**
 * Resolves the console origin injected by website-runtime-config.js, falling
 * back to the current host on port 11443. Shared by every surface that links
 * into the app so the landing and the blog stay in sync.
 */
export function useAppOrigin() {
  const appOrigin = ref('')

  onMounted(() => {
    appOrigin.value = validOrigin(window.__HFL_WEBSITE_CONFIG__?.appUrl || '') || directAppOrigin()
  })

  const loginUrl = computed(() => `${appOrigin.value || '#'}${appOrigin.value ? '/login' : ''}`)

  function openApp(event: MouseEvent, placement: WebsiteOpenAppPlacement) {
    const target = loginUrl.value
    if (!target || target === '#') {
      event.preventDefault()
      return
    }
    trackWebsiteOpenApp(placement)
  }

  return { loginUrl, openApp }
}

export const githubUrl = 'https://github.com/oneprolabs/hyperfilelens'
export const sourceLensUrl = 'https://github.com/oneprolabs/sourcelens'
