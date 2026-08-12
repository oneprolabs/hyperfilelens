// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../composables/useTurnstileConfig', () => ({
  prefetchAuthTurnstile: vi.fn(),
}))

import { router } from './index'

describe('login route security state', () => {
  beforeEach(async () => {
    await router.replace('/')
  })

  it.each([
    '/login',
    '/login/',
    '/LOGIN',
    '/Login',
  ])('removes a forged legacy reason from %s without losing a valid redirect', async (path) => {
    await router.push({
      path,
      query: {
        reason: 'TOKEN_REUSED',
        redirect: '/ops/alerts',
      },
    })

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query).toEqual({
      redirect: '/ops/alerts',
    })
    expect(window.location.search).toBe('?redirect=/ops/alerts')
  })
})
