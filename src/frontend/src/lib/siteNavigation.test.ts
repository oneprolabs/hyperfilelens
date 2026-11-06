import { describe, expect, it } from 'vitest'
import type { DeployProfile } from '../composables/useDeployProfile'
import { operationsSiteRedirect, PLATFORM_ACCOUNT_PATH } from './siteNavigation'

const ops = {
  site_role: 'ops', landing_path: '/platform-ops/overview',
  admin_console_landing_path: '/platform-ops/engine/ai-settings',
} as DeployProfile

describe('operations site navigation boundary', () => {
  it.each(['/account', '/account/', '/account/profile', '/ACCOUNT/PROFILE/'])(
    'maps the legacy personal URL %s into the Admin Console', path => {
      expect(operationsSiteRedirect(path, ops)).toBe(PLATFORM_ACCOUNT_PATH)
    },
  )
  it.each(['/', '/protection/backups', '/insight/copilot', '/node/members',
    '/ops/tasks', '/account/notifications', '/account/organizations/new', '/search'])(
    'does not allow a tenant business page on the operations site: %s', path => {
      expect(operationsSiteRedirect(path, ops)).toBe('/platform-ops/overview')
    },
  )
  it.each(['/platform-ops', '/platform-ops/account/profile', '/platform-ops/users', '/account/setup'])(
    'preserves Admin Console and shared password setup routes: %s', path => {
      expect(operationsSiteRedirect(path, ops)).toBeUndefined()
    },
  )
  it('uses a validated accessible landing path without hardcoding any host or port', () => {
    expect(operationsSiteRedirect('/node/roles', { ...ops, landing_path: '/platform-ops/engine/ai-settings' }))
      .toBe('/platform-ops/engine/ai-settings')
    expect(operationsSiteRedirect('/', { ...ops, landing_path: '//evil.test', admin_console_landing_path: '/node' }))
      .toBe(PLATFORM_ACCOUNT_PATH)
  })
  it('does not change tenant navigation', () => {
    for (const path of ['/account/profile', '/node/organization', '/protection/backups']) {
      expect(operationsSiteRedirect(path, { ...ops, site_role: 'tenant' })).toBeUndefined()
      expect(operationsSiteRedirect(path, null)).toBeUndefined()
    }
  })
})
