// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'
import { router } from './index'

describe('route shell loading', () => {
  it('keeps tenant and platform shells as native async route components', () => {
    const tenantRoute = router.options.routes.find(route => route.path === '/' && 'children' in route)
    const platformRoute = router.options.routes.find(route => route.path === '/platform-ops')

    expect(tenantRoute?.component).toEqual(expect.any(Function))
    expect(platformRoute?.component).toEqual(expect.any(Function))
  })

  it('uses System Monitoring as the Operations entry when the extension contributes it', () => {
    const opsRoute = router.getRoutes().find(route => route.path === '/ops')
    const hasSystemMonitoring = router.getRoutes().some(route => route.path === '/ops/host-monitor')

    expect(opsRoute?.redirect).toBe(hasSystemMonitoring ? '/ops/host-monitor' : '/ops/events')
  })
})
