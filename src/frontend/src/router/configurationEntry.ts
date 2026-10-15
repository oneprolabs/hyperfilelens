import { ElMessage } from 'element-plus'
import { useTenantPermissions } from '../composables/useTenantPermissions'
import { i18n } from '../i18n'

type HasPermission = (key: string, organizationOnly?: boolean) => boolean

// Sidebar order, with actual read capabilities rather than Role names or
// unconditional menu visibility. Groups and Resources support scoped grants.
const configurationPages = [
  ['node/organization', 'configuration.organizations.view', true],
  ['node/members', 'configuration.members.view', true],
  ['node/resource-groups', 'configuration.groups.view', false],
  ['node/roles', 'configuration.roles.view', true],
  ['node/resources', 'configuration.resources.view', false],
  ['node/subscription', 'configuration.subscriptions.view', true],
] as const

export function resolveConfigurationEntry(
  routes: readonly { path?: string }[],
  has: HasPermission,
): string {
  const available = new Set(routes.map(route => route.path))
  const page = configurationPages.find(([path, permission, organizationOnly]) =>
    available.has(path) && has(permission, organizationOnly),
  )
  return page ? `/${page[0]}` : '/'
}

/** Called only after authentication, via the /node route's beforeEnter. */
export function enterConfiguration(routes: readonly { path?: string }[]) {
  const { has } = useTenantPermissions()
  const path = resolveConfigurationEntry(routes, has)
  if (path === '/') {
    ElMessage.warning(i18n.global.t('settings.nav.accessDenied'))
  }
  return { path, replace: true }
}
