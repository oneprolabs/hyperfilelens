/** Navigation hints only. The backend authorizes resource scope and actions. */
export type OperationsPermissionCheck = (permission: string, organizationOnly?: boolean) => boolean
type OperationsLocation = { path: string; query?: Record<string, unknown> }

export const operationsPages = [
  { path: '/ops/host-monitor', permission: 'operations.host_monitoring.view' },
  { path: '/ops/events', permission: 'operations.events.view' },
  { path: '/ops/alerts', permission: 'operations.alerts.view' },
  { path: '/ops/alerts/rules', permission: 'operations.alert_rules.view' },
  { path: '/ops/channels', permission: 'operations.notification_channels.view' },
  { path: '/ops/delivery-history', permission: 'operations.notification_records.view' },
  { path: '/ops/tasks', permission: 'operations.tasks.view' },
  { path: '/ops/audit-logs', permission: 'operations.audit_logs.view' },
] as const

export function resolveOperationsEntry(has: OperationsPermissionCheck, monitorAvailable = false): string {
  return operationsPages.find(page => (monitorAvailable || page.path !== '/ops/host-monitor')
    && has(page.permission))?.path || '/'
}

function withOrganization(target: string, location: OperationsLocation): string {
  const org = location.query?.org
  return typeof org === 'string' && org ? `${target}?org=${encodeURIComponent(org)}` : target
}

/** Return a permitted destination before mounting an inaccessible page. */
export function operationsRouteRedirect(
  location: OperationsLocation, has: OperationsPermissionCheck, monitorAvailable = false,
): string | undefined {
  const path = location.path.replace(/\/+$/, '').toLowerCase()
  if (path === '/ops') return
  let permission: string | undefined
  if (/^\/ops\/alerts\/rules\/(?:create|[^/]+\/edit)$/.test(path)) {
    permission = 'operations.alert_rules.manage'
  } else if (/^\/ops\/channels\/(?:create|[^/]+\/edit)$/.test(path)) {
    permission = 'operations.notification_channels.manage'
  } else {
    // Match Alert Rules before its broader Alerts prefix.
    permission = [...operationsPages].reverse().find(page =>
      path === page.path || path.startsWith(`${page.path}/`),
    )?.permission
  }
  if ((path === '/ops/host-monitor' && !monitorAvailable) || (permission && !has(permission))) {
    return withOrganization(resolveOperationsEntry(has, monitorAvailable), location)
  }
}
