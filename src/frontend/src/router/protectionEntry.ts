import { useTenantPermissions } from '../composables/useTenantPermissions'
import { resolveProtectionEntry } from '../lib/protectionAccess'

/** The global authentication guard loads the profile before this entry hook. */
export function enterProtection(location?: { query: Record<string, unknown> }) {
  const { has } = useTenantPermissions()
  const [path, search] = resolveProtectionEntry(has).split('?')
  const query: Record<string, string> = {}
  new URLSearchParams(search).forEach((value, key) => { query[key] = value })
  const org = location?.query.org
  if (typeof org === 'string' && org) query.org = org
  return Object.keys(query).length ? { path, query } : { path }
}
