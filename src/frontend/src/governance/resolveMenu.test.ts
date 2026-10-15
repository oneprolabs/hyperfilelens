// @vitest-environment jsdom

import { Layers3, UserRound, UsersRound } from 'lucide-vue-next'
import { describe, expect, it, vi } from 'vitest'
import { currentUser } from '../composables/useAuth'

import { isGovernanceMenuItemVisible, resolveGovernanceMenu } from './resolveMenu'

vi.mock('../composables/useAuth', async () => {
  const { ref } = await import('vue')
  return { currentUser: ref(null) }
})
vi.mock('@ext/platform/governance/menu', () => ({
  governanceMenuItems: [
    { id: 'members', labelKey: 'settings.nav.members', to: '/node/members', icon: 'members' },
    { id: 'resource-groups', labelKey: 'settings.nav.resourceGroups', to: '/node/resource-groups', icon: 'groups' },
    { id: 'resources', labelKey: 'settings.nav.resources', to: '/node/resources', icon: 'resources' },
  ],
}))

describe('governance menu role filtering', () => {
  const membersItem = {
    id: 'members',
    labelKey: 'settings.nav.members',
    to: '/node/members',
    icon: 'members' as const,
    requiredRoles: ['owner', 'admin', 'manager', 'auditor'],
  }

  it('keeps governance readers visible and hides operator-only access', () => {
    expect(isGovernanceMenuItemVisible(membersItem, 'auditor')).toBe(true)
    expect(isGovernanceMenuItemVisible(membersItem, 'operator')).toBe(false)
  })

  it('keeps items without a role requirement visible', () => {
    expect(isGovernanceMenuItemVisible({ ...membersItem, requiredRoles: undefined }, 'operator')).toBe(true)
  })

  it('does not substitute Group grants or Role names for organization-only permissions', () => {
    const item = {
      ...membersItem, requiredPermissions: ['configuration.organizations.view'], organizationOnly: true,
    }
    currentUser.value = {
      id: 1, email: 'owner@test.local', username: 'owner',
      access_profile: {
        org_key: 'current', role: 'owner', permissions: [],
        group_permissions: ['configuration.organizations.view'],
        visible_features: [], available_platforms: [], landing_path: '/',
      },
    }
    expect(isGovernanceMenuItemVisible(item, 'owner')).toBe(false)
    currentUser.value.access_profile!.permissions = ['configuration.organizations.view']
    expect(isGovernanceMenuItemVisible(item, 'custom-reader')).toBe(true)
    currentUser.value = null
  })

  it('uses distinct icons for Members, Groups, and Resources', () => {
    const menu = resolveGovernanceMenu((key) => key)
    expect(menu.find((item) => item.to === '/node/members')?.icon).toBe(UserRound)
    expect(menu.find((item) => item.to === '/node/resource-groups')?.icon).toBe(UsersRound)
    expect(menu.find((item) => item.to === '/node/resources')?.icon).toBe(Layers3)
  })
})
