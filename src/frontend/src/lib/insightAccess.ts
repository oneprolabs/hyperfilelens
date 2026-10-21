/** Navigation hints only. Backend permissions still authorize every operation. */
export type InsightPermissionCheck = (permission: string, organizationOnly?: boolean) => boolean

export const insightPages = [
  { path: '/insight/copilot', permission: 'insights.chats.view', organizationOnly: false },
  { path: '/insight/usage', permission: 'insights.usage.view', organizationOnly: true },
  { path: '/insight/gateways', permission: 'insights.private_data_gateways.view', organizationOnly: false },
] as const

export function resolveInsightEntry(has: InsightPermissionCheck): string {
  return insightPages.find(page => has(page.permission, page.organizationOnly))?.path || '/'
}

export function insightPermissionHints(
  profile: { permissions?: readonly string[]; group_permissions?: readonly string[] } | undefined,
  enterprise = __HFL_EXTENSIONS_FRONTEND__,
): InsightPermissionCheck {
  return (permission, organizationOnly = false) => !enterprise
    || Boolean(profile?.permissions?.includes(permission))
    || (!organizationOnly && Boolean(profile?.group_permissions?.includes(permission)))
}

/** Return a safe destination before a protected page is mounted. */
export function insightRouteRedirect(path: string, has: InsightPermissionCheck): string | undefined {
  // Vue Router matches static routes case-insensitively by default.
  const normalized = path.replace(/\/+$/, '').toLowerCase()
  // The entry route chooses its destination after session initialization.
  if (normalized === '/insight') return
  // Share Q&A has its own backend-validated share token and membership contract.
  if (normalized === '/insight/copilot/shared') return
  const page = insightPages.find(page =>
    normalized === page.path || normalized.startsWith(`${page.path}/`),
  )
  if (!page) return
  const permission = normalized === '/insight/copilot/new' ? 'insights.chats.use' : page.permission
  if (!has(permission, page.organizationOnly)) return resolveInsightEntry(has)
}
