import { useTenantPermissions } from '../composables/useTenantPermissions'
import { resolveInsightEntry } from '../lib/insightAccess'

/** Session initialization runs in the global guard before this entry hook. */
export function enterInsights() {
  const { has } = useTenantPermissions()
  return { path: resolveInsightEntry(has) }
}
