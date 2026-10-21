import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { router } from '../router'
import {
  clearDeployProfileCache,
  fetchDeployProfile,
  shouldForceDeployProfileRefresh,
} from './useDeployProfile'
import { getCorrelationHeaders } from '../lib/requestContext'
import { refreshAuthToken } from '../lib/authRefresh'
import { i18n, setAuthenticatedLocalePreference } from '../i18n'
import {
  clearSharedSessionNotice,
  isSessionInvalidReason,
  sessionNoticeMessageKey,
  storeSessionNotice,
  subscribeSessionNotice,
} from '../lib/sessionNotice'
import { clearLogoutBrowserStorage } from '../lib/logoutStorage'
import { resolveAuthenticatedLoginTarget } from '../lib/loginNavigation'
import { readStoredOrganizationKey } from '../lib/organizationStorage'
import { insightPermissionHints, insightRouteRedirect } from '../lib/insightAccess'
import { protectionRouteRedirect } from '../lib/protectionAccess'
import { operationsRouteRedirect } from '../lib/operationsAccess'
import { hasTenantHostMonitor } from '../ops/resolveRoutes'

export interface User {
  id: number
  email: string
  username: string
  first_name?: string
  last_name?: string
  is_staff?: boolean
  language?: string
  timezone?: string
  requires_password_change?: boolean
  access_profile?: {
    org_key: string
    role: string
    authority_source?: 'installation_admin' | 'owner' | 'role' | 'support_readonly' | ''
    is_organization_member?: boolean
    permissions?: string[]
    group_permissions?: string[]
    visible_features: string[]
    available_platforms: Array<{ key: string; default_path: string }>
    landing_path: string
  }
  registered_at?: string | null
  registeredAt?: string | null
}

export interface LoginError {
  errorCode?: string
  message: string
  fields?: Record<string, string[]>
}

// Auth state
const isLoggedIn = ref(false)
export const currentUser = ref<User | null>(null)
const isLoading = ref(false)

const ORG_KEY_STORAGE = 'hfl_org_key'

export function getStoredOrgKey(): string {
  return readStoredOrganizationKey()
}

export function setStoredOrgKey(orgKey: string): void {
  try {
    if (orgKey) {
      localStorage.setItem(ORG_KEY_STORAGE, orgKey)
      sessionStorage.setItem(ORG_KEY_STORAGE, orgKey)
    } else {
      localStorage.removeItem(ORG_KEY_STORAGE)
      sessionStorage.removeItem(ORG_KEY_STORAGE)
    }
  } catch {
    // ignore
  }
}

/** Tab context first, then stored default and the server-verified access profile. */
export function getEffectiveOrgKey(): string {
  const stored = getStoredOrgKey()
  if (stored) return stored
  const fromProfile = currentUser.value?.access_profile?.org_key?.trim() || ''
  if (fromProfile) {
    setStoredOrgKey(fromProfile)
  }
  return fromProfile
}

// Module-level setter for use by setupAuthGuard
function setUser(userData: User | null) {
  currentUser.value = userData
  isLoggedIn.value = userData !== null
  setAuthenticatedLocalePreference(userData?.language)
  const orgKey = userData?.access_profile?.org_key?.trim()
  if (orgKey) {
    setStoredOrgKey(orgKey)
  }
  if (userData) clearSharedSessionNotice()
}

function parseUserPayload(data: unknown): User | null {
  if (!data || typeof data !== 'object') return null
  const o = data as Record<string, unknown>
  const payload = o.data && typeof o.data === 'object' && (o.data as Record<string, unknown>).id
    ? (o.data as User)
    : o.id
      ? (o as User)
      : null
  return payload?.id ? payload : null
}

type CurrentUserFetchResult = {
  user: User | null
  status?: number
  errorCode?: string
  refreshFailed?: boolean
  refreshAvailable?: boolean
  refreshStatus?: number
  refreshNetworkError?: boolean
}

export type CurrentSessionConfirmation =
  | { state: 'authenticated'; user: User }
  | { state: 'unauthenticated' }
  | { state: 'unknown' }

let sessionConfirmationPromise: Promise<CurrentSessionConfirmation> | null = null

function extractSessionErrorCode(data: unknown): string | undefined {
  if (!data || typeof data !== 'object') return undefined
  const d = data as Record<string, unknown>
  if (typeof d.code === 'string') return d.code
  if (typeof d.error_code === 'string') return d.error_code
  if (d.data && typeof d.data === 'object') {
    const inner = d.data as Record<string, unknown>
    if (typeof inner.code === 'string') return inner.code
    if (typeof inner.error_code === 'string') return inner.error_code
    if (inner.error && typeof inner.error === 'object') {
      const err = inner.error as Record<string, unknown>
      if (typeof err.code === 'string') return err.code
      if (typeof err.error_code === 'string') return err.error_code
    }
  }
  if (d.error && typeof d.error === 'object') {
    const err = d.error as Record<string, unknown>
    if (typeof err.code === 'string') return err.code
    if (typeof err.error_code === 'string') return err.error_code
  }
  return undefined
}

async function readResponseJson(res: Response): Promise<unknown> {
  try {
    return await res.json()
  } catch {
    return undefined
  }
}

async function inspectCurrentSession(): Promise<CurrentUserFetchResult> {
  const res = await fetch('/api/v1/auth/token/refresh', {
    credentials: 'include',
    headers: getCorrelationHeaders(),
  })
  const data = await readResponseJson(res)

  if (!res.ok) {
    return {
      user: null,
      status: res.status,
      errorCode: extractSessionErrorCode(data),
    }
  }

  const body = data && typeof data === 'object'
    ? data as Record<string, unknown>
    : {}
  const session = body.data && typeof body.data === 'object'
    ? body.data as Record<string, unknown>
    : body
  const payload = parseUserPayload(session.user)
  if (payload?.id) {
    setUser(payload)
    return {
      user: payload,
      status: res.status,
      refreshAvailable: session.refresh_available === true,
    }
  }
  return {
    user: null,
    status: res.status,
    refreshAvailable: session.refresh_available === true,
  }
}

async function fetchCurrentUserWithRefresh(): Promise<CurrentUserFetchResult> {
  const first = await inspectCurrentSession()
  if (first.user || !first.refreshAvailable) return first

  const refreshed = await refreshAuthToken()
  if (!refreshed.ok) {
    return {
      ...first,
      errorCode: refreshed.errorCode || first.errorCode,
      refreshFailed: true,
      refreshStatus: refreshed.status,
      refreshNetworkError: refreshed.networkError,
    }
  }

  return inspectCurrentSession()
}

/** Load the signed-in user and sync org context from the backend access profile. */
export async function fetchCurrentUser(): Promise<User | null> {
  try {
    const result = await fetchCurrentUserWithRefresh()
    return result.user
  } catch {
    return null
  }
}

/** Distinguish an absent session from a session that could not be inspected. */
async function inspectAndConfirmCurrentSession(): Promise<CurrentSessionConfirmation> {
  try {
    const result = await fetchCurrentUserWithRefresh()
    if (result.user) return { state: 'authenticated', user: result.user }
    if (
      result.status === 401
      || result.status === 403
      || result.refreshStatus === 401
      || result.refreshStatus === 403
      || isSessionInvalidReason(result.errorCode)
    ) {
      return { state: 'unauthenticated' }
    }
    if (
      result.refreshNetworkError
      || (result.status !== undefined && result.status >= 500)
      || (result.refreshStatus !== undefined && result.refreshStatus >= 500)
      || result.refreshFailed
    ) {
      return { state: 'unknown' }
    }
    if (result.status !== undefined && result.status >= 200 && result.status < 300) {
      return { state: 'unauthenticated' }
    }
    return { state: 'unknown' }
  } catch {
    return { state: 'unknown' }
  }
}

export function confirmCurrentSession(): Promise<CurrentSessionConfirmation> {
  if (!sessionConfirmationPromise) {
    sessionConfirmationPromise = inspectAndConfirmCurrentSession().finally(() => {
      sessionConfirmationPromise = null
    })
  }
  return sessionConfirmationPromise
}

function hasCompleteAccessProfile(user: User | null): boolean {
  const profile = user?.access_profile
  return !!(profile?.org_key?.trim() && (
    profile?.role?.trim() || profile?.authority_source === 'installation_admin'
    || profile?.authority_source === 'support_readonly'
  ))
}

// Module-level clearAuth for use by setupAuthGuard
export function clearAuth() {
  clearLogoutBrowserStorage()
  currentUser.value = null
  isLoggedIn.value = false
  isLoading.value = false
  setStoredOrgKey('')
  setAuthenticatedLocalePreference(null)
  clearDeployProfileCache()
}

const WATCHDOG_INTERVAL_MS = 15_000
let sessionWatchdogTimer: number | null = null
let sessionWatchdogChecking = false
let sessionExpiredHandling = false
let sessionNoticeSubscriptionStarted = false

function isPublicSessionPath(path: string): boolean {
  return ['/login', '/register', '/auth/oauth/callback', '/auth/oauth/error'].some(publicPath => path.startsWith(publicPath))
}

async function redirectToLoginForSession(
  reason = 'REFRESH_EXPIRED',
  notifyBackend = true,
  publish = true,
): Promise<void> {
  if (sessionExpiredHandling) return
  sessionExpiredHandling = true
  try {
    const currentRoute = router.currentRoute.value
    const redirect = currentRoute.fullPath || '/'

    if (notifyBackend) {
      void fetch('/api/v1/auth/logout', {
        method: 'POST',
        credentials: 'include',
        headers: getCorrelationHeaders(),
      }).catch(() => undefined)
    }

    clearAuth()

    if (!isPublicSessionPath(currentRoute.path)) {
      storeSessionNotice(reason, undefined, Date.now(), publish)
      await router.replace({
        path: '/login',
        query: {
          redirect,
        },
      })
    }
  } finally {
    window.setTimeout(() => {
      sessionExpiredHandling = false
    }, 1000)
  }
}

async function checkSessionForWatchdog(): Promise<void> {
  if (sessionWatchdogChecking || !isLoggedIn.value) return
  if (isPublicSessionPath(router.currentRoute.value.path)) return
  sessionWatchdogChecking = true
  try {
    const result = await fetchCurrentUserWithRefresh()
    if (result.user) return

    if (isSessionInvalidReason(result.errorCode)) {
      await redirectToLoginForSession(result.errorCode)
      return
    }
    if (
      result.status !== undefined
      && result.status >= 200
      && result.status < 300
      && result.refreshAvailable === false
    ) {
      await redirectToLoginForSession('REFRESH_EXPIRED', false)
      return
    }
    if (
      result.refreshFailed
      && (
        result.status === 401
        || result.status === 403
        || result.refreshStatus === 401
        || result.refreshStatus === 403
      )
    ) {
      await redirectToLoginForSession(result.errorCode || 'REFRESH_EXPIRED')
    }
  } catch {
    // Ignore transient network failures; regular API calls still handle auth errors.
  } finally {
    sessionWatchdogChecking = false
  }
}

export function useAuth() {
  const router = useRouter()

  const user = computed(() => currentUser.value)
  const loggedIn = computed(() => isLoggedIn.value)
  const loading = computed(() => isLoading.value)

  function setLoading(loading: boolean) {
    isLoading.value = loading
  }

  function handleAuthError(error: LoginError) {
    if (isSessionInvalidReason(error.errorCode)) {
      logout()
    }
  }

  async function logout() {
    try {
      // Call logout endpoint to clear cookies
      await fetch('/api/v1/auth/logout', {
        method: 'POST',
        credentials: 'include',
        headers: getCorrelationHeaders({ 'Content-Type': 'application/json' }),
      })
    } catch {
      // Ignore errors, clear local state anyway
    } finally {
      clearAuth()
      router.push('/login')
    }
  }

  function isSessionInvalidError(errorCode?: string): boolean {
    return isSessionInvalidReason(errorCode)
  }

  function getErrorMessage(error: LoginError): string {
    const messageKey = sessionNoticeMessageKey(error.errorCode)
    if (messageKey) return i18n.global.t(messageKey)
    return error.message
  }

  return {
    user,
    loggedIn,
    loading,
    setUser,
    setLoading,
    handleAuthError,
    logout,
    clearAuth,
    isSessionInvalidError,
    getErrorMessage,
  }
}

function hasRequiredRouteRole(to: { meta: Record<string, unknown> }): boolean {
  const requiredPermissions = to.meta.requiredPermissions
  if (Array.isArray(requiredPermissions) && requiredPermissions.length) {
    const profile = currentUser.value?.access_profile
    const grants = [...(profile?.permissions || []), ...(profile?.group_permissions || [])]
    return requiredPermissions.every((key) => grants.includes(key))
  }
  const requiredRoles = to.meta.requiredRoles
  if (!Array.isArray(requiredRoles) || requiredRoles.length === 0) return true
  if (currentUser.value?.access_profile?.authority_source === 'installation_admin') return true
  const role = currentUser.value?.access_profile?.role?.trim() || ''
  return requiredRoles.includes(role)
}

function authenticatedRouteRedirect(to: { path: string; meta: Record<string, unknown>; query?: Record<string, unknown> }): string | undefined {
  const has = insightPermissionHints(currentUser.value?.access_profile)
  const insightRedirect = insightRouteRedirect(
    to.path, has,
  )
  if (insightRedirect) return insightRedirect
  const protectionRedirect = protectionRouteRedirect(to, has)
  if (protectionRedirect) return protectionRedirect
  const operationsRedirect = operationsRouteRedirect(to, has, hasTenantHostMonitor())
  if (operationsRedirect) return operationsRedirect
  if (!hasRequiredRouteRole(to)) return '/node'
}

// Watch for router changes to check auth state
export function setupAuthGuard() {
  router.beforeEach(async (to, from, next) => {
    const publicPaths = ['/login', '/register', '/auth/oauth/callback', '/auth/oauth/error']
    const isPublicPath = publicPaths.some(path => to.path.startsWith(path))
    const isPlatformOpsPath = to.path.startsWith('/platform-ops')
    // Admin Console deep links select a target, but only the backend decides
    // whether the authenticated actor may enter it. Never infer membership.
    const requestedOrg = typeof to.query.org === 'string' ? to.query.org.trim() : ''
    if (!isPublicPath && !isPlatformOpsPath && requestedOrg) {
      const previousOrg = getStoredOrgKey()
      setStoredOrgKey(requestedOrg)
      const verified = await fetchCurrentUser()
      if (!verified || verified.access_profile?.org_key !== requestedOrg) {
        setStoredOrgKey(previousOrg)
        await fetchCurrentUser()
        next('/')
        return
      }
    }

    if (to.path === '/login') {
      const confirmation = isLoggedIn.value
        ? { state: 'authenticated' as const, user: currentUser.value }
        : await confirmCurrentSession()

      if (confirmation.state === 'authenticated') {
        if (confirmation.user?.requires_password_change) {
          next('/account/setup')
          return
        }
        const profile = await fetchDeployProfile(true)
        if (!profile) {
          next()
          return
        }

        const target = resolveAuthenticatedLoginTarget(profile, to.query.redirect)
        if (target.kind === 'external') {
          window.location.replace(target.url)
          return
        }
        if (target.kind === 'internal') {
          next(target.path)
          return
        }
      }

      next()
      return
    }

    if (to.path === '/register') {
      const profile = await fetchDeployProfile()
      if (profile && !profile.email_signup_enabled) {
        next('/login')
        return
      }
    }

    // Check auth state for non-public paths
    if (!isPublicPath) {
      const setupUser = currentUser.value || await fetchCurrentUser()
      if (setupUser?.requires_password_change) {
        if (to.path === '/account/setup') next()
        else next('/account/setup')
        return
      }
      if (to.path === '/account/setup') {
        next(setupUser ? '/' : '/login')
        return
      }
      if (to.path === '/account/organizations/new' && setupUser) {
        next()
        return
      }
      // Refresh incomplete session state (e.g. missing role after login)
      if (isLoggedIn.value && !isPlatformOpsPath && !hasCompleteAccessProfile(currentUser.value)) {
        const refreshed = await fetchCurrentUser()
        if (refreshed) {
          const redirect = authenticatedRouteRedirect(to)
          if (redirect) {
            next(redirect)
            return
          }
          next()
          return
        }
        clearAuth()
        next('/login')
        return
      }

      // If already logged in with a complete profile, allow access
      if (isLoggedIn.value) {
        if (isPlatformOpsPath || to.meta.requiresPlatformOps) {
          const profile = await fetchDeployProfile(shouldForceDeployProfileRefresh(
            to.path,
            from.path,
            Boolean(to.meta.requiresPlatformOps),
          ))
          const staff = currentUser.value?.is_staff === true
          if (!staff || !profile?.platform_ops_access_allowed) {
            if (profile?.tenant_public_url) {
              window.location.replace(profile.tenant_public_url)
              return
            }
            next('/login')
            return
          }
        }
        const redirect = authenticatedRouteRedirect(to)
        if (redirect) {
          next(redirect)
          return
        }
        next()
        return
      }

      // Try to validate session via API
      try {
        const payload = await fetchCurrentUser()
        if (payload?.id) {
          if (isPlatformOpsPath || to.meta.requiresPlatformOps) {
            const profile = await fetchDeployProfile(shouldForceDeployProfileRefresh(
              to.path,
              from.path,
              Boolean(to.meta.requiresPlatformOps),
            ))
            const staff = payload.is_staff === true
            if (!staff || !profile?.platform_ops_access_allowed) {
              if (profile?.tenant_public_url) {
                window.location.replace(profile.tenant_public_url)
                return
              }
              next('/login')
              return
            }
          }
          const redirect = authenticatedRouteRedirect(to)
          if (redirect) {
            next(redirect)
            return
          }
          next()
          return
        }

        // Invalid or no session - redirect to login
        next('/login')
        return
      } catch {
        // Network error - redirect to login
        next('/login')
        return
      }
    }

    next()
  })
}

export function setupSessionWatchdog() {
  if (typeof window === 'undefined' || sessionWatchdogTimer !== null) return
  if (!sessionNoticeSubscriptionStarted) {
    sessionNoticeSubscriptionStarted = true
    subscribeSessionNotice((reason) => {
      if (!isLoggedIn.value || isPublicSessionPath(router.currentRoute.value.path)) return
      void redirectToLoginForSession(reason, false, false)
    })
  }
  sessionWatchdogTimer = window.setInterval(() => {
    void checkSessionForWatchdog()
  }, WATCHDOG_INTERVAL_MS)

  window.addEventListener('focus', () => {
    void checkSessionForWatchdog()
  })
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) void checkSessionForWatchdog()
  })
}
