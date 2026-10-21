import { tenantOpsRoutes as extTenantOpsRoutes } from '@ext/platform/ops/routes'

/**
 * Tenant Operations routes contributed by the platform extension.
 * Community (empty socket): none.
 */
export function resolveTenantOpsRoutes() {
  return (extTenantOpsRoutes || []) as Array<Record<string, unknown>>
}

export function hasTenantHostMonitor(): boolean {
  return resolveTenantOpsRoutes().some(route => route.path === 'ops/host-monitor')
}
