import type { LocationQuery, LocationQueryRaw } from 'vue-router'

export const LOGIN_ROUTE_NAME = 'login'

type LoginSiteProfile = {
  site_role: 'tenant' | 'ops'
  platform_ops_access_allowed: boolean
  tenant_public_url: string
  landing_path: string
  admin_console_landing_path: string
}

export type AuthenticatedLoginTarget =
  | { kind: 'internal'; path: string }
  | { kind: 'external'; url: string }
  | { kind: 'unavailable' }

const ENCODED_PATH_DELIMITER_PATTERN = /%(?:25)*(?:2e|2f|5c)/i

function containsControlCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const code = character.charCodeAt(0)
    return code <= 31 || code === 127
  })
}

function currentOrigin(): string {
  return typeof window !== 'undefined' ? window.location.origin : 'http://localhost'
}

export function resolveSafeLoginRedirect(
  redirect: unknown,
  origin = currentOrigin(),
): string | null {
  if (typeof redirect !== 'string') return null
  if (!redirect.startsWith('/') || redirect.startsWith('//')) return null
  if (redirect.includes('\\') || containsControlCharacter(redirect)) return null

  try {
    const expectedOrigin = new URL(origin).origin
    const target = new URL(redirect, expectedOrigin)
    if (target.origin !== expectedOrigin) return null
    if (ENCODED_PATH_DELIMITER_PATTERN.test(target.pathname)) return null
    const normalizedPath = target.pathname.toLowerCase()
    if (normalizedPath === '/login' || normalizedPath.startsWith('/login/')) return null
    return `${target.pathname}${target.search}${target.hash}`
  } catch {
    return null
  }
}

export function resolveAuthenticatedLoginTarget(
  profile: LoginSiteProfile,
  redirect: unknown,
  origin = currentOrigin(),
): AuthenticatedLoginTarget {
  const safeRedirect = resolveSafeLoginRedirect(redirect, origin)
  if (profile.site_role === 'ops') {
    if (!profile.platform_ops_access_allowed) {
      return profile.tenant_public_url
        ? { kind: 'external', url: profile.tenant_public_url }
        : { kind: 'unavailable' }
    }
    if (safeRedirect?.startsWith('/platform-ops')) {
      return { kind: 'internal', path: safeRedirect }
    }
    const landingPath = profile.landing_path.startsWith('/platform-ops')
      ? profile.landing_path
      : profile.admin_console_landing_path
    return landingPath
      ? { kind: 'internal', path: landingPath }
      : { kind: 'unavailable' }
  }

  if (
    safeRedirect
    && !safeRedirect.startsWith('/platform-ops')
    && !safeRedirect.startsWith('/admin')
  ) {
    return { kind: 'internal', path: safeRedirect }
  }
  return { kind: 'internal', path: profile.landing_path || '/' }
}

export function withoutLegacySessionReason(query: LocationQuery): LocationQueryRaw | null {
  if (!Object.prototype.hasOwnProperty.call(query, 'reason')) return null
  const sanitized: LocationQueryRaw = { ...query }
  delete sanitized.reason
  return sanitized
}
