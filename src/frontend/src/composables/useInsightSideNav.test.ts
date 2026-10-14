import { describe, expect, it, vi } from 'vitest'

import { useInsightSideNav } from './useInsightSideNav'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))
const permissions = vi.hoisted(() => ({ has: vi.fn(() => true) }))
vi.mock('./useTenantPermissions', () => ({
  useTenantPermissions: () => permissions,
}))

describe('useInsightSideNav', () => {
  it('shows Usage for users with the usage permission', () => {
    const paths = useInsightSideNav().value
      .flatMap((group) => group.children || [])
      .map((item) => item.to)

    expect(paths).toContain('/insight/copilot')
    expect(paths).toContain('/insight/gateways')
    expect(paths).toContain('/insight/usage')
    const groups = useInsightSideNav().value
    expect(groups.find((group) => group.label === 'insight.side.groupSmartApps')?.children?.map((item) => item.to))
      .not.toContain('/insight/usage')
    expect(groups.find((group) => group.label === 'insight.side.groupDataGateways')?.children?.map((item) => item.to))
      .toContain('/insight/usage')
  })

  it('hides Insights pages without their specific permission', () => {
    permissions.has.mockReturnValue(false)
    expect(useInsightSideNav().value.flatMap((group) => group.children || []))
      .toEqual([])
    permissions.has.mockReturnValue(true)
  })

  it('checks usage permission independently from chats and gateways', () => {
    permissions.has.mockImplementation((permission: string) => permission !== 'insights.usage.view')
    const paths = useInsightSideNav().value
      .flatMap((group) => group.children || [])
      .map((item) => item.to)

    expect(paths).toContain('/insight/copilot')
    expect(paths).toContain('/insight/gateways')
    expect(paths).not.toContain('/insight/usage')
    permissions.has.mockReturnValue(true)
  })
})
