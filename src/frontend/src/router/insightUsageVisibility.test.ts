// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'

import { router } from './index'

describe('Insight Usage visibility', () => {
  it('does not register the temporarily hidden Usage page route', () => {
    const paths = router.getRoutes().map((route) => route.path)

    expect(paths).not.toContain('/insight/usage')
  })

  it('redirects direct Usage links to the Insight home page', async () => {
    await router.push('/insight/usage')

    expect(router.currentRoute.value.fullPath).toBe('/insight/copilot')
  })
})
