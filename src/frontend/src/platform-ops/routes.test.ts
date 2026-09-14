import { describe, expect, it } from 'vitest'
import { platformOpsRoutes } from './routes'
import { resolvePlatformOpsRoutes } from './resolveRoutes'

describe('platformOpsRoutes (community)', () => {
  it('defaults to AI Models and keeps settings routes for extension merge', () => {
    expect(platformOpsRoutes[0]).toEqual({
      path: '',
      redirect: '/platform-ops/engine/ai-settings',
    })
    const engine = platformOpsRoutes.find((route) => route.path === 'engine')
    expect(engine?.children?.some((route) => route.path === 'ai-settings')).toBe(true)
    expect(engine?.children?.some((route) => route.path === 'gateways' && 'redirect' in route)).toBe(true)
    expect(engine?.children?.some((route) => route.path === 'usage')).toBe(false)
    expect(platformOpsRoutes.some((route) => route.path === 'platform/email')).toBe(true)
    expect(platformOpsRoutes.some((route) => route.path === 'platform/external-access')).toBe(true)
    expect(platformOpsRoutes.some((route) => route.path === 'platform/runtime-environment')).toBe(true)
  })
})

describe('resolvePlatformOpsRoutes', () => {
  it('returns community routes when extension stub is empty', () => {
    const resolved = resolvePlatformOpsRoutes()
    expect(resolved[0]).toEqual({
      path: '',
      redirect: '/platform-ops/engine/ai-settings',
    })
    expect(resolved.some((route) => route.path === 'platform/email')).toBe(true)
  })
})
