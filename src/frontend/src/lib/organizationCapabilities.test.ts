import { describe, expect, it } from 'vitest'
import { organizationCapabilityAvailable } from './organizationCapabilities'
import { insightPermissionHints } from './insightAccess'
import { protectionRouteRedirect, resolveProtectionEntry } from './protectionAccess'

const capabilities = {
  managed_storage: true, object_storage: false, target_nas: false, local_disks: false,
  source_nas: false, proxy_hosts: false, source_tags: false,
}

describe('organization product boundaries', () => {
  it('keeps Community storage unchanged and never adds Managed Storage', () => {
    expect(organizationCapabilityAvailable(undefined, 'object_storage', false)).toBe(true)
    expect(organizationCapabilityAvailable(undefined, 'managed_storage', false)).toBe(false)
  })

  it('trusts the explicit capability map instead of the name or legacy mode', () => {
    expect(organizationCapabilityAvailable({
      organization_mode: 'team', organization_capabilities: capabilities,
    }, 'managed_storage', true)).toBe(true)
    expect(organizationCapabilityAvailable({
      organization_mode: 'personal', organization_capabilities: { managed_storage: false },
    }, 'managed_storage', true)).toBe(false)
    expect(organizationCapabilityAvailable({
      organization_capabilities: {},
    }, 'target_nas', true)).toBe(false)
  })

  it('intersects product availability with Role and Group grants', () => {
    const profile = {
      organization_capabilities: capabilities,
      permissions: [
        'protection.object_storage_repositories.view',
        'protection.target_nas_repositories.view',
        'protection.target_nas_repositories.manage',
      ],
    }
    const has = insightPermissionHints(profile, true)
    expect(has('protection.object_storage_repositories.view', false, 'managed_storage')).toBe(true)
    expect(has('protection.object_storage_repositories.view', false, 'object_storage')).toBe(false)
    expect(has('configuration.managed_storage.view', true, 'managed_storage')).toBe(false)
    expect(has('protection.target_nas_repositories.view', false, 'target_nas')).toBe(false)
    expect(protectionRouteRedirect({ path: '/node/repositories', query: { tab: 'nas' } }, has))
      .not.toBeUndefined()
    expect(protectionRouteRedirect({ path: '/node/repositories/nas/add' }, has))
      .not.toBeUndefined()
  })

  it('keeps an organization-only connection grant out of Group delegation', () => {
    const has = insightPermissionHints({
      organization_capabilities: capabilities,
      group_permissions: ['configuration.managed_storage.view'],
    }, true)
    expect(has('configuration.managed_storage.view', true, 'managed_storage')).toBe(false)
    expect(resolveProtectionEntry(has)).toBe('/')
  })

  it('accepts old personal/team profiles only when the capability map is absent', () => {
    expect(organizationCapabilityAvailable({ organization_mode: 'personal' }, 'object_storage', true)).toBe(false)
    expect(organizationCapabilityAvailable({ organization_mode: 'personal' }, 'managed_storage', true)).toBe(true)
    expect(organizationCapabilityAvailable({ organization_mode: 'team' }, 'target_nas', true)).toBe(true)
  })

  it('does not let Role or Group grants reenable unsupported source operations', () => {
    for (const grant of ['permissions', 'group_permissions']) {
      const has = insightPermissionHints({
        organization_capabilities: capabilities,
        [grant]: [
          'protection.source_nas.view', 'protection.proxy_hosts.manage', 'protection.tags.view',
        ],
      }, true)
      expect(has('protection.source_nas.view')).toBe(false)
      expect(has('protection.proxy_hosts.manage')).toBe(false)
      expect(has('protection.tags.view')).toBe(false)
    }
  })
})
