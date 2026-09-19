// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'

import { isGovernanceMenuItemVisible } from './resolveMenu'

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
})
