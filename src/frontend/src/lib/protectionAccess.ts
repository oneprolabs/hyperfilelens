/** UI hints only; the backend still authorizes every resource and operation. */
export type ProtectionPermissionCheck = (permission: string, organizationOnly?: boolean) => boolean
type ProtectionLocation = { path: string; query?: Record<string, unknown> }

export const backupConfigurationDirectoryPath = '/protection/backup-configurations'

/** Accept only a known list destination, never an arbitrary return URL. */
export function resolveBackupDetailReturnPath(returnTo: unknown): string {
  return returnTo === backupConfigurationDirectoryPath ? backupConfigurationDirectoryPath : '/protection/backups'
}

export const protectionPages = [
  { to: '/protection/backups', permission: 'protection.backup_configurations.view' },
  { to: backupConfigurationDirectoryPath, permission: 'protection.backup_configurations.view' },
  { to: '/protection/backup-sources?tab=host', permission: 'protection.source_hosts.view' },
  { to: '/protection/backup-sources?tab=nas', permission: 'protection.source_nas.view' },
  { to: '/node/agents', permission: 'protection.proxy_hosts.view' },
  { to: '/protection/source-tags', permission: 'protection.tags.view' },
  { to: '/node/repositories?tab=s3', permission: 'protection.object_storage_repositories.view' },
  { to: '/node/repositories?tab=nas', permission: 'protection.target_nas_repositories.view' },
  { to: '/node/repositories?tab=proxy_fs', permission: 'protection.local_disk_repositories.view' },
  { to: '/protection/policies?tab=backup', permission: 'protection.backup_policies.view' },
  { to: '/protection/policies?tab=filter', permission: 'protection.file_filters.view' },
] as const

export function canViewProtectionSources(has: ProtectionPermissionCheck): boolean {
  return has('protection.source_hosts.view') || has('protection.source_nas.view')
}

export function resolveProtectionEntry(has: ProtectionPermissionCheck): string {
  return protectionPages.find(page => has(page.permission)
    && (page.to !== '/protection/backups' || canViewProtectionSources(has)))?.to || '/'
}

function withOrganization(target: string, location: ProtectionLocation): string {
  const org = location.query?.org
  return typeof org === 'string' && org
    ? `${target}${target.includes('?') ? '&' : '?'}org=${encodeURIComponent(org)}`
    : target
}

export function protectionRouteRedirect(
  location: ProtectionLocation, has: ProtectionPermissionCheck,
): string | undefined {
  const path = location.path.replace(/\/+$/, '').toLowerCase()
  if (path === '/protection') return
  const query = location.query || {}
  const denied = () => withOrganization(resolveProtectionEntry(has), location)
  const tabs = protectionPages.filter(page => page.to.split('?')[0] === path)
  if (tabs.some(page => page.to.includes('?tab='))) {
    const defaultTab = path === '/protection/backup-sources' ? 'host'
      : path === '/node/repositories' ? 's3' : 'backup'
    const tab = typeof query.tab === 'string' ? query.tab : undefined
    const requestedTab = tab === 'hostFileSystem' ? 'host' : tab
    const selected = tabs.find(page => page.to.endsWith(`?tab=${requestedTab}`))
      || tabs.find(page => page.to.endsWith(`?tab=${defaultTab}`))!
    if (has(selected.permission)) return
    const allowed = tabs.find(page => has(page.permission))
    // Discard stale tab-specific detail/filter IDs when choosing another page.
    return allowed ? withOrganization(allowed.to, location) : denied()
  }

  let permission: string | undefined
  if (path === '/protection/backups/create') {
    if (!canViewProtectionSources(has)) return denied()
    permission = 'protection.backup_configurations.manage'
  } else if (/^\/protection\/backups\/browse\//.test(path)) {
    permission = 'protection.backup_data.browse'
  } else if (/^\/protection\/restore\/snapshots\//.test(path)) {
    if (!has('protection.backup_data.browse')) return denied()
    permission = 'protection.restore_plans.run'
  } else if (path === '/protection/backups') {
    permission = 'protection.backup_configurations.view'
    if (has(permission) && !canViewProtectionSources(has)) {
      return withOrganization(backupConfigurationDirectoryPath, location)
    }
  } else if (path === backupConfigurationDirectoryPath || /^\/protection\/backups\/[^/]+(?:\/snapshots\/[^/]+)?$/.test(path)) {
    permission = 'protection.backup_configurations.view'
  } else if (/^\/protection\/policies\/(?:create|[^/]+\/edit)$/.test(path)) {
    permission = query.kind === 'filter' ? 'protection.file_filters.manage' : 'protection.backup_policies.manage'
  } else if (path === '/protection/file-filter-rules/help') {
    permission = 'protection.file_filters.view'
  } else if (path === '/protection/source-tags') {
    permission = 'protection.tags.view'
  } else if (path === '/node/agents') {
    permission = 'protection.proxy_hosts.view'
  } else if (path === '/node/snapshots') {
    permission = 'protection.backup_configurations.view'
  } else if (path === '/node/nodes/deploy' && query.role !== 'gateway') {
    permission = query.role === 'proxy' ? 'protection.proxy_hosts.manage' : 'protection.source_hosts.manage'
  } else {
    const repository = path.match(/^\/node\/repositories\/(s3|nas|proxy-fs)\/(?:add|[^/]+\/(?:edit|repair))$/)
    if (repository) {
      const resource = { s3: 'object_storage_repositories', nas: 'target_nas_repositories', 'proxy-fs': 'local_disk_repositories' }[repository[1] as 's3' | 'nas' | 'proxy-fs']
      permission = `protection.${resource}.manage`
    }
  }
  if (permission && !has(permission)) return denied()
}
