// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'

import { router } from './index'

describe('Account Center routes', () => {
  it('owns the canonical personal notifications page', () => {
    const paths = router.getRoutes().map((route) => route.path)

    expect(paths).toContain('/account/notifications')
    expect(paths).not.toContain('/notifications')
  })
})
