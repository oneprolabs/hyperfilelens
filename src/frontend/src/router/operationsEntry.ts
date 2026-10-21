import { useTenantPermissions } from '../composables/useTenantPermissions'
import { resolveOperationsEntry } from '../lib/operationsAccess'
import { hasTenantHostMonitor } from '../ops/resolveRoutes'

/** Resolve the module entry only after the authentication guard loads grants. */
export function enterOperations(location?: { query: Record<string, unknown> }) {
  const { has } = useTenantPermissions()
  const path = resolveOperationsEntry(has, hasTenantHostMonitor())
  const org = location?.query.org
  return typeof org === 'string' && org ? { path, query: { org } } : { path }
}
