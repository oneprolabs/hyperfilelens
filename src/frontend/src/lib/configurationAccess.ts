/** UI hints only. Backend authorization still determines scope and actions. */
export type ConfigurationPermissionCheck = (key: string, organizationOnly?: boolean) => boolean
export type ConfigurationRoute = { path?: string }
type ConfigurationLocation = { path: string; query?: Record<string, unknown> }

/** Sidebar order; organization permissions must never be inferred from Groups. */
export const configurationPages = [
  { path: '/node/organization', permission: 'configuration.organizations.view', organizationOnly: true },
  { path: '/node/members', permission: 'configuration.members.view', organizationOnly: true },
  { path: '/node/resource-groups', permission: 'configuration.groups.view', organizationOnly: false },
  { path: '/node/roles', permission: 'configuration.roles.view', organizationOnly: true },
  { path: '/node/resources', permission: 'configuration.resources.view', organizationOnly: false },
  { path: '/node/subscription', permission: 'configuration.subscriptions.view', organizationOnly: true },
] as const

function available(path: string, routes: readonly ConfigurationRoute[]): boolean {
  return routes.some(route => typeof route.path === 'string'
    && `/${route.path.replace(/^\/+|\/+$/g, '').toLowerCase()}` === path)
}

export function canViewConfigurationPage(
  path: string, routes: readonly ConfigurationRoute[], has: ConfigurationPermissionCheck,
): boolean {
  const page = configurationPages.find(page => page.path === path)
  return Boolean(page && available(page.path, routes) && has(page.permission, page.organizationOnly))
}

export function resolveConfigurationEntry(
  routes: readonly ConfigurationRoute[], has: ConfigurationPermissionCheck,
): string {
  return configurationPages.find(page => canViewConfigurationPage(page.path, routes, has))?.path || '/'
}

export function configurationEntryTarget(
  path: string, location?: { query?: Record<string, unknown> },
): string {
  const org = location?.query?.org
  return typeof org === 'string' && org ? `${path}?org=${encodeURIComponent(org)}` : path
}

const configurationActions = [
  { pattern: /^\/node\/members\/new$/, page: '/node/members', permission: 'configuration.members.manage', organizationOnly: true },
  { pattern: /^\/node\/members\/[^/]+\/edit$/, page: '/node/members', permission: 'configuration.members.assign_role', organizationOnly: true },
  { pattern: /^\/node\/roles\/(?:new|[^/]+\/edit)$/, page: '/node/roles', permission: 'configuration.roles.manage', organizationOnly: true },
  { pattern: /^\/node\/resource-groups\/(?:new|[^/]+\/edit)$/, page: '/node/resource-groups', permission: 'configuration.groups.manage', organizationOnly: true },
  { pattern: /^\/node\/resource-groups\/[^/]+\/members$/, page: '/node/resource-groups', permission: 'configuration.groups.manage_members', organizationOnly: false },
  { pattern: /^\/node\/resource-groups\/[^/]+\/resources$/, page: '/node/resource-groups', permission: 'configuration.groups.manage_resources', organizationOnly: false },
] as const

/** Block inaccessible Configuration deep links before the page issues reads. */
export function configurationRouteRedirect(
  location: ConfigurationLocation, routes: readonly ConfigurationRoute[], has: ConfigurationPermissionCheck,
): string | undefined {
  const path = location.path.replace(/\/+$/, '').toLowerCase()
  // The module entry is resolved after the authentication guard loads grants.
  if (path === '/node') return
  const page = configurationPages.find(page => page.path === path)
  const action = configurationActions.find(action => action.pattern.test(path))
  const target = action ? { path: action.page, ...action } : page
  // Other /node paths belong to Protection or Insights, not Configuration.
  if (!target) return
  if (!available(target.path, routes) || !has(target.permission, target.organizationOnly)) {
    return configurationEntryTarget(resolveConfigurationEntry(routes, has), location)
  }
}
