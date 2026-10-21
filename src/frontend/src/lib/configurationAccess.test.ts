import { describe, expect, it } from 'vitest'
import { insightPermissionHints } from './insightAccess'
import {
  canViewConfigurationPage, configurationEntryTarget, configurationPages,
  configurationRouteRedirect, resolveConfigurationEntry,
} from './configurationAccess'

const routes = configurationPages.map(page => ({ path: page.path.slice(1) }))
const grants = (...permissions: string[]) => insightPermissionHints({ permissions }, true)

describe('Configuration permission hints', () => {
  it('hides Configuration for members with only other-module permissions', () => {
    const has = grants('protection.source_hosts.view', 'insights.chats.view', 'operations.tasks.view')
    expect(resolveConfigurationEntry(routes, has)).toBe('/')
    for (const page of configurationPages) {
      expect(canViewConfigurationPage(page.path, routes, has)).toBe(false)
      expect(configurationRouteRedirect({ path: page.path }, routes, has)).toBe('/')
    }
  })

  it.each(configurationPages)('selects only the permitted page $path', page => {
    const has = grants(page.permission)
    expect(resolveConfigurationEntry(routes, has)).toBe(page.path)
    expect(configurationRouteRedirect({ path: page.path }, routes, has)).toBeUndefined()
  })

  it.each(configurationPages)('honors the assignment context of $path', page => {
    const has = insightPermissionHints({ group_permissions: [page.permission] }, true)
    expect(resolveConfigurationEntry(routes, has)).toBe(page.organizationOnly ? '/' : page.path)
    expect(configurationRouteRedirect({ path: page.path }, routes, has))
      .toBe(page.organizationOnly ? '/' : undefined)
  })

  it('chooses sidebar order and requires an actual route in the build', () => {
    const has = grants(...configurationPages.map(page => page.permission))
    expect(resolveConfigurationEntry(routes.toReversed(), has)).toBe('/node/organization')
    expect(resolveConfigurationEntry([{ path: '/node/subscription/' }], has)).toBe('/node/subscription')
    expect(resolveConfigurationEntry([], has)).toBe('/')
    expect(configurationRouteRedirect({ path: '/node/members' }, [{ path: 'node/subscription' }], has))
      .toBe('/node/subscription')
  })

  it('retains Community Subscription without an Enterprise permission profile', () => {
    const has = insightPermissionHints(undefined, false)
    const community = [{ path: 'node/subscription' }]
    expect(resolveConfigurationEntry(community, has)).toBe('/node/subscription')
    expect(configurationRouteRedirect({ path: '/node/subscription' }, community, has)).toBeUndefined()
    expect(canViewConfigurationPage('/node/organization', community, has)).toBe(false)
  })

  it('preserves only a valid organization when selecting another page', () => {
    expect(configurationRouteRedirect({
      path: '/NODE/MEMBERS/', query: { org: 'tenant a', id: 'stale', tab: 'old' },
    }, routes, grants('configuration.resources.view'))).toBe('/node/resources?org=tenant%20a')
    expect(configurationEntryTarget('/node/resources', { query: { org: 'tenant?&' } }))
      .toBe('/node/resources?org=tenant%3F%26')
    for (const org of [undefined, '', ['tenant-a'], null]) {
      expect(configurationEntryTarget('/node/resources', { query: { org } })).toBe('/node/resources')
    }
  })

  it.each([
    ['/node/members/new', 'configuration.members.manage', true, '/node/members'],
    ['/node/members/7/edit', 'configuration.members.assign_role', true, '/node/members'],
    ['/node/roles/new', 'configuration.roles.manage', true, '/node/roles'],
    ['/node/roles/operator/edit', 'configuration.roles.manage', true, '/node/roles'],
    ['/node/resource-groups/new', 'configuration.groups.manage', true, '/node/resource-groups'],
    ['/node/resource-groups/7/edit', 'configuration.groups.manage', true, '/node/resource-groups'],
    ['/node/resource-groups/7/members', 'configuration.groups.manage_members', false, '/node/resource-groups'],
    ['/node/resource-groups/7/resources', 'configuration.groups.manage_resources', false, '/node/resource-groups'],
  ] as const)('checks the action and assignment context before opening %s', (path, permission, orgOnly, pagePath) => {
    const page = configurationPages.find(page => page.path === pagePath)!
    expect(configurationRouteRedirect({ path }, routes, grants(page.permission))).toBe(pagePath)
    expect(configurationRouteRedirect({ path }, routes, grants(permission))).toBeUndefined()
    const group = insightPermissionHints({ group_permissions: [permission] }, true)
    expect(configurationRouteRedirect({ path }, routes, group)).toBe(orgOnly ? '/' : undefined)
    expect(configurationRouteRedirect({ path }, [], grants(permission))).toBe('/')
  })

  it.each([
    '/node', '/node/agents', '/node/repositories', '/node/nodes/deploy',
    '/node/snapshots', '/node/gateways', '/platform-ops/organizations', '/account/organizations/new',
  ])('leaves unrelated routes and the intermediate entry to their own guards: %s', path => {
    expect(configurationRouteRedirect({ path }, routes, grants())).toBeUndefined()
  })
})
