// @vitest-environment jsdom

import { Layers3, UserRound, UsersRound } from 'lucide-vue-next'
import { describe, expect, it, vi } from 'vitest'

import { isGovernanceMenuItemVisible, resolveGovernanceMenu } from './resolveMenu'

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

  it('uses distinct icons for Members, Groups, and Resources', () => {
    const menu = resolveGovernanceMenu((key) => key)
    expect(menu.find((item) => item.to === '/node/members')?.icon).toBe(UserRound)
    expect(menu.find((item) => item.to === '/node/resource-groups')?.icon).toBe(UsersRound)
    expect(menu.find((item) => item.to === '/node/resources')?.icon).toBe(Layers3)
  })
})
