import { describe, expect, it } from 'vitest'
import { insightPermissionHints } from './insightAccess'
import { canViewProtectionSources, protectionPages, protectionRouteRedirect, resolveProtectionEntry } from './protectionAccess'

const grants = (...permissions: string[]) => (permission: string) => permissions.includes(permission)
function parseQuery(search?: string) {
  const query: Record<string, string> = {}
  new URLSearchParams(search).forEach((value, key) => { query[key] = value })
  return query
}

describe('Protection navigation hints (not resource authorization)', () => {
  it('does not expose Protection to a Configuration-only user', () => {
    const has = grants('configuration.roles.manage')
    expect(resolveProtectionEntry(has)).toBe('/')
    for (const page of protectionPages) {
      const [path, query] = page.to.split('?')
      expect(protectionRouteRedirect({ path, query: parseQuery(query) }, has)).toBe('/')
    }
  })

  it.each(protectionPages)('chooses the permitted entry $to, including Group-only grants', page => {
    const permissions: string[] = [page.permission]
    if (page.to === '/protection/backups') permissions.push('protection.source_hosts.view')
    const has = insightPermissionHints({ group_permissions: permissions }, true)
    expect(resolveProtectionEntry(has)).toBe(page.to)
    const [path, query] = page.to.split('?')
    expect(protectionRouteRedirect({ path, query: parseQuery(query) }, has)).toBeUndefined()
  })

  it('keeps configuration-only members in the existing Wizard', () => {
    const has = grants('protection.backup_configurations.view', 'protection.backup_configurations.manage')
    expect(canViewProtectionSources(has)).toBe(false)
    expect(resolveProtectionEntry(has)).toBe('/protection/backups')
    expect(protectionRouteRedirect({ path: '/protection/backups' }, has)).toBeUndefined()
    expect(protectionRouteRedirect({ path: '/protection/backups/12' }, has)).toBeUndefined()
    expect(protectionRouteRedirect({ path: '/protection/backups/create' }, has)).toBe('/protection/backups')
  })

  it.each([
    ['/protection/backup-sources', 'nas', 'protection.source_nas.view'],
    ['/node/repositories', 'nas', 'protection.target_nas_repositories.view'],
    ['/node/repositories', 'proxy_fs', 'protection.local_disk_repositories.view'],
    ['/protection/policies', 'filter', 'protection.file_filters.view'],
  ])('moves an inaccessible default tab to the nearest permitted tab at %s (%s)', (path, tab, permission) => {
    const has = grants(permission)
    expect(protectionRouteRedirect({ path, query: { org: 'tenant a', id: 'stale' } }, has))
      .toBe(`${path}?tab=${tab}&org=tenant%20a`)
    expect(protectionRouteRedirect({ path, query: { tab } }, has)).toBeUndefined()
  })

  it('matches the page default for unknown and multi-valued tabs, without string coercion', () => {
    const has = grants('protection.source_nas.view')
    for (const tab of ['unknown', ['nas'], null]) {
      expect(protectionRouteRedirect({ path: '/protection/backup-sources', query: { tab } }, has))
        .toBe('/protection/backup-sources?tab=nas')
    }
    expect(protectionRouteRedirect({ path: '/Protection/Backup-Sources/', query: { tab: 'nas' } }, has)).toBeUndefined()
  })

  it.each([
    ['/protection/policies/create', 'protection.backup_policies.manage', {}],
    ['/protection/policies/7/edit', 'protection.file_filters.manage', { kind: 'filter' }],
    ['/node/repositories/s3/add', 'protection.object_storage_repositories.manage', {}],
    ['/node/repositories/nas/7/repair', 'protection.target_nas_repositories.manage', {}],
    ['/node/repositories/proxy-fs/7/edit', 'protection.local_disk_repositories.manage', {}],
    ['/node/nodes/deploy', 'protection.source_hosts.manage', {}],
    ['/node/nodes/deploy', 'protection.proxy_hosts.manage', { role: 'proxy' }],
    ['/protection/backups/browse/agent/7', 'protection.backup_data.browse', {}],
  ])('checks the action grant before mounting %s', (path, permission, query) => {
    expect(protectionRouteRedirect({ path, query }, grants())).toBe('/')
    expect(protectionRouteRedirect({ path, query }, grants(permission))).toBeUndefined()
  })

  it('requires browse and restore grants for standalone restore, not Source inventory', () => {
    const location = { path: '/protection/restore/snapshots/7' }
    expect(protectionRouteRedirect(location, grants('protection.restore_plans.run'))).toBe('/')
    expect(protectionRouteRedirect(location, grants('protection.restore_plans.run', 'protection.backup_data.browse'))).toBeUndefined()
  })

  it('leaves Gateway deployment and unrelated routes to their own permission contracts', () => {
    expect(protectionRouteRedirect({ path: '/node/nodes/deploy', query: { role: 'gateway' } }, grants())).toBeUndefined()
    expect(protectionRouteRedirect({ path: '/node/members' }, grants())).toBeUndefined()
  })

  it('retains Community access without an Enterprise permission profile', () => {
    const has = insightPermissionHints(undefined, false)
    expect(resolveProtectionEntry(has)).toBe('/protection/backups')
    expect(canViewProtectionSources(has)).toBe(true)
    for (const page of protectionPages) {
      const [path, query] = page.to.split('?')
      expect(protectionRouteRedirect({ path, query: parseQuery(query) }, has)).toBeUndefined()
    }
  })
})
