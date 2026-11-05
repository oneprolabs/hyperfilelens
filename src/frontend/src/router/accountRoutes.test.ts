// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'

import { router } from './index'

describe('Account Center routes', () => {
  it('owns the canonical personal notifications page', () => {
    const paths = router.getRoutes().map((route) => route.path)

    expect(paths).toContain('/account/notifications')
    expect(paths).not.toContain('/notifications')
  })

  it('keeps Admin Console personal settings outside the tenant layout and under platform authorization', () => {
    const target = router.resolve('/platform-ops/account/profile')
    expect(target.matched.map(record => record.path)).toEqual([
      '/platform-ops', '/platform-ops/account/profile',
    ])
    expect(target.meta.requiresPlatformOps).toBe(true)
    expect(router.resolve('/account/profile').matched.some(record => record.path === '/platform-ops')).toBe(false)
  })
})
