// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'

import { router } from './index'

describe('Insight Usage visibility', () => {
  it('registers the Usage page route', () => {
    const paths = router.getRoutes().map((route) => route.path)

    expect(paths).toContain('/insight/usage')
  })

  it('resolves direct Usage links to the restored page', () => {
    expect(router.resolve('/insight/usage').path).toBe('/insight/usage')
  })
})
