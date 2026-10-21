import { describe, expect, it } from 'vitest'
import { insightPermissionHints } from './insightAccess'
import { operationsPages, operationsRouteRedirect, resolveOperationsEntry } from './operationsAccess'

const grants = (...permissions: string[]) => insightPermissionHints({ permissions }, true)

describe('Operations navigation hints', () => {
  it('does not expose Operations to a Configuration-only member', () => {
    const has = grants('configuration.roles.manage')
    expect(resolveOperationsEntry(has, true)).toBe('/')
    for (const page of operationsPages) {
      expect(operationsRouteRedirect({ path: page.path }, has, true)).toBe('/')
    }
  })

  it.each(operationsPages)('accepts only the permitted page $path for organization and Group grants', page => {
    for (const has of [
      grants(page.permission),
      insightPermissionHints({ group_permissions: [page.permission] }, true),
    ]) {
      expect(resolveOperationsEntry(has, true)).toBe(page.path)
      expect(operationsRouteRedirect({ path: page.path }, has, true)).toBeUndefined()
    }
  })

  it('does not select Enterprise monitoring when that route is unavailable', () => {
    expect(resolveOperationsEntry(grants('operations.host_monitoring.view'))).toBe('/')
    expect(resolveOperationsEntry(grants('operations.host_monitoring.view', 'operations.tasks.view')))
      .toBe('/ops/tasks')
    expect(operationsRouteRedirect({ path: '/ops/host-monitor' }, grants('operations.host_monitoring.view')))
      .toBe('/')
  })

  it('redirects a monitoring visit to Tasks, retaining only the organization context', () => {
    expect(operationsRouteRedirect({
      path: '/OPS/Host-Monitor/', query: { org: 'tenant a', id: 'stale', tab: 'old' },
    }, grants('operations.tasks.view'), true)).toBe('/ops/tasks?org=tenant%20a')
    expect(operationsRouteRedirect({
      path: '/ops/events', query: { org: ['tenant-a'] },
    }, grants())).toBe('/')
  })

  it.each([
    ['/ops/alerts/rules/create', 'operations.alert_rules.manage', 'operations.alert_rules.view'],
    ['/ops/alerts/rules/7/edit', 'operations.alert_rules.manage', 'operations.alert_rules.view'],
    ['/ops/channels/create', 'operations.notification_channels.manage', 'operations.notification_channels.view'],
    ['/ops/channels/7/edit', 'operations.notification_channels.manage', 'operations.notification_channels.view'],
  ])('requires the action permission for %s', (path, manage, view) => {
    expect(operationsRouteRedirect({ path }, grants(view), true)).toBe(resolveOperationsEntry(grants(view), true))
    expect(operationsRouteRedirect({ path }, grants(manage), true)).toBeUndefined()
  })

  it('does not mistake Alert Rules for the broader Alerts page', () => {
    expect(operationsRouteRedirect({ path: '/ops/alerts/rules' }, grants('operations.alerts.view')))
      .toBe('/ops/alerts')
  })

  it('leaves module entry resolution until after authentication and ignores unrelated routes', () => {
    expect(operationsRouteRedirect({ path: '/ops' }, grants())).toBeUndefined()
    expect(operationsRouteRedirect({ path: '/platform-ops/overview' }, grants())).toBeUndefined()
    expect(operationsRouteRedirect({ path: '/node/members' }, grants())).toBeUndefined()
  })

  it('retains Community navigation without an Enterprise profile', () => {
    const has = insightPermissionHints(undefined, false)
    expect(resolveOperationsEntry(has)).toBe('/ops/events')
    expect(resolveOperationsEntry(has, true)).toBe('/ops/host-monitor')
    for (const page of operationsPages.filter(page => page.path !== '/ops/host-monitor')) {
      expect(operationsRouteRedirect({ path: page.path }, has)).toBeUndefined()
    }
  })
})
