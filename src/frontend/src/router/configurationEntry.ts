import { useTenantPermissions } from '../composables/useTenantPermissions'
import { configurationEntryTarget, resolveConfigurationEntry, type ConfigurationRoute } from '../lib/configurationAccess'
export { resolveConfigurationEntry } from '../lib/configurationAccess'

/** Called only after authentication, via the /node route's beforeEnter. */
export function enterConfiguration(
  routes: readonly ConfigurationRoute[], location?: { query?: Record<string, unknown> },
) {
  const { has } = useTenantPermissions()
  const path = resolveConfigurationEntry(routes, has)
  // Personal pages are available without organization permissions or membership.
  // Do not carry an organization override into an account-scoped page.
  if (path === '/') {
    return { path: '/account/profile', replace: true }
  }
  const target = configurationEntryTarget(path, location)
  const [targetPath, search] = target.split('?')
  const query: Record<string, string> = {}
  new URLSearchParams(search).forEach((value, key) => { query[key] = value })
  return search ? { path: targetPath, query, replace: true } : { path, replace: true }
}
