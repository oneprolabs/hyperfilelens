import type { DeployProfile } from '../composables/useDeployProfile'
import { resolveSafeLoginRedirect } from './loginNavigation'

export const PLATFORM_ACCOUNT_PATH = '/platform-ops/account/profile'

/** Keep operations-site navigation inside the Admin Console shell. */
export function operationsSiteRedirect(path: string, profile: DeployProfile | null): string | undefined {
  if (profile?.site_role !== 'ops') return
  const normalized = path.replace(/\/+$/, '').toLowerCase()
  // Shared authentication pages are deliberately outside both business shells.
  if (normalized === '/account/setup'
    || normalized === '/platform-ops' || normalized.startsWith('/platform-ops/')) return
  if (normalized === '/account' || normalized === '/account/profile') return PLATFORM_ACCOUNT_PATH
  const landing = resolveSafeLoginRedirect(profile.landing_path)
  if (landing?.startsWith('/platform-ops/')) return landing
  const fallback = resolveSafeLoginRedirect(profile.admin_console_landing_path)
  return fallback?.startsWith('/platform-ops/') ? fallback : PLATFORM_ACCOUNT_PATH
}
