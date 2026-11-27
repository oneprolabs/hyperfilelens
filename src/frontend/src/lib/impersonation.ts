import { shallowRef } from 'vue'

export type ImpersonationContext = {
  id: string
  user_id?: number
  user_email?: string
  actor_id?: number
  actor_email?: string
  org_key?: string
  org_name?: string
  expires_at?: string
  admin_url?: string
  ended?: boolean
}
const KEY = 'hfl_impersonation_context'
const HEADER = 'X-HFL-Impersonation'
const CONTROL = '/api/v1/impersonation/session'

function restore(): ImpersonationContext | null {
  if (typeof window === 'undefined') return null
  const selected = new URL(window.location.href).searchParams.get('impersonation')
  let saved: string | null = null
  try { saved = sessionStorage.getItem(KEY) } catch {
    return selected ? { id: selected, ended: true } : null
  }
  try {
    const data = saved ? JSON.parse(saved) as ImpersonationContext : null
    if (selected) {
      if (!/^[a-f0-9-]{36}$/.test(selected)) return { id: 'invalid', ended: true }
      return data?.id === selected ? data : { id: selected }
    }
    if (!saved) return null
    return data && typeof data.id === 'string' ? data : { id: 'invalid', ended: true }
  } catch { return { id: selected || 'invalid', ended: true } }
}

export const impersonationContext = shallowRef<ImpersonationContext | null>(restore())
export function isImpersonating(): boolean { return impersonationContext.value !== null }
export function impersonationHeaders(): Record<string, string> {
  const value = impersonationContext.value
  return value ? { [HEADER]: value.id || 'invalid', ...(value.org_key ? { 'X-Org-Key': value.org_key } : {}) } : {}
}
export function saveImpersonation(value: ImpersonationContext): void {
  // Fail closed when a tab cannot retain its scope across a full navigation.
  sessionStorage.setItem(KEY, JSON.stringify(value))
  impersonationContext.value = value
}
export function markImpersonationEnded(): void {
  const value = impersonationContext.value
  if (!value) return
  const ended = { ...value, ended: true }
  impersonationContext.value = ended
  try { sessionStorage.setItem(KEY, JSON.stringify(ended)) } catch { /* Keep the document fenced. */ }
}
export function scopedImpersonationUrl(raw: string): string {
  const value = impersonationContext.value
  if (!value) return raw
  const url = new URL(raw, window.location.href)
  if (!['http:', 'https:'].includes(url.protocol) || url.origin !== window.location.origin) return raw
  if (url.pathname.startsWith('/api/') || !/\.[a-z0-9]+$/i.test(url.pathname)) {
    url.searchParams.set('impersonation', value.id)
  }
  return url.href
}

let installed = false
/** Cover fetch streams/downloads as well as api(); never intercept other origins. */
export function installImpersonationTransport(): void {
  if (installed || typeof window === 'undefined') return
  installed = true
  const originalFetch = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const value = impersonationContext.value
    if (!value) return originalFetch(input, init)
    const url = new URL(input instanceof Request ? input.url : String(input), window.location.href)
    const apiBase = new URL(import.meta.env.VITE_API_BASE?.toString() || window.location.origin, window.location.href)
    if (![window.location.origin, apiBase.origin].includes(url.origin) || !['/api/', '/accounts/', '/_allauth/', '/admin/'].some(prefix => url.pathname.startsWith(prefix))) {
      return originalFetch(input, init)
    }
    if (value.ended && url.pathname !== CONTROL) throw new Error('The impersonation session has ended.')
    const headers = new Headers(input instanceof Request ? input.headers : undefined)
    new Headers(init?.headers).forEach((v, k) => headers.set(k, v))
    headers.set(HEADER, value.id || 'invalid')
    if (value.org_key && !headers.has('X-Org-Key')) headers.set('X-Org-Key', value.org_key)
    const response = await originalFetch(input, { ...init, credentials: 'include', headers })
    if (response.headers.get('X-HFL-Impersonation-Ended') === 'true' || response.status === 401) {
      markImpersonationEnded()
      if (window.location.pathname !== '/impersonation/ended') window.location.replace('/impersonation/ended')
    }
    return response
  }
  const originalOpen = window.open.bind(window)
  window.open = (url, target, features) => originalOpen(
    url ? scopedImpersonationUrl(String(url)) : url, target, features,
  )
  const scopeLink = (event: Event) => {
    const anchor = (event.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null
    if (anchor && impersonationContext.value) anchor.href = scopedImpersonationUrl(anchor.href)
  }
  for (const kind of ['click', 'pointerdown', 'contextmenu', 'auxclick']) document.addEventListener(kind, scopeLink, true)
}

export async function endImpersonation(): Promise<void> {
  // Retain the marker until the document is gone; never fall back to the actor.
  const response = await fetch(CONTROL, { method: 'DELETE', credentials: 'include', headers: impersonationHeaders() })
  if (!response.ok) throw new Error('The session could not be ended. Try again.')
  markImpersonationEnded()
  window.location.replace('/impersonation/ended')
}

/** Only inert state pages may discard the tab selector before full navigation. */
export function returnToAdmin(url: string): void {
  const path = window.location.pathname
  const failedEntry = path === '/impersonation/entry'
    && (!impersonationContext.value || impersonationContext.value.ended)
  if (path !== '/impersonation/ended' && !failedEntry) return
  let target: URL
  try { target = new URL(url) } catch { return }
  if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password) return
  sessionStorage.removeItem(KEY)
  window.location.assign(target.href)
}
