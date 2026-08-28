// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'

import { router } from './index'

describe('Configuration system settings removal', () => {
  it('does not register the removed system settings page route', () => {
    const paths = router.getRoutes().map((route) => route.path)

    expect(paths).not.toContain('/node/system')
  })
})
