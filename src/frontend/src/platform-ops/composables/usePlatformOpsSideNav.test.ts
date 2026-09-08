import { describe, expect, it, vi } from 'vitest'
import { usePlatformOpsSideNav } from './usePlatformOpsSideNav'

const mocks = vi.hoisted(() => ({
  t: vi.fn((key: string) => key),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: mocks.t }),
}))

describe('usePlatformOpsSideNav (community)', () => {
  it('exposes AI Models and essential platform settings only', () => {
    const menus = usePlatformOpsSideNav().value
    const items = menus.flatMap((item) => item.children || [item])
    const paths = items.map((item) => item.to).filter(Boolean)

    expect(paths).toEqual([
      '/platform-ops/engine/ai-settings',
      '/platform-ops/platform/external-access',
      '/platform-ops/platform/runtime-environment',
    ])
    expect(paths.some((path) => path?.includes('/gateways'))).toBe(false)
    expect(paths.some((path) => path?.includes('/email'))).toBe(false)
    expect(paths.some((path) => path?.includes('/authentication'))).toBe(false)
    expect(paths.some((path) => path?.includes('/overview'))).toBe(false)
    expect(paths.some((path) => path?.includes('/users'))).toBe(false)
  })
})
