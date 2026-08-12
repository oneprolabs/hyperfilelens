// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { consumeSessionNotice } from './sessionNotice'

const mocks = vi.hoisted(() => ({
  clearAuth: vi.fn(),
  getRouteRequestSignal: vi.fn(),
  routerReplace: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('../composables/useAuth', () => ({
  clearAuth: mocks.clearAuth,
  currentUser: { value: null },
  getEffectiveOrgKey: () => '',
}))

vi.mock('../router', () => ({
  router: {
    currentRoute: {
      value: {
        path: '/ops/alerts',
        fullPath: '/ops/alerts?status=open',
      },
    },
    replace: mocks.routerReplace,
  },
}))

vi.mock('./routeRequestAbort', () => ({
  getRouteRequestSignal: mocks.getRouteRequestSignal,
}))

import { api } from './api'

afterEach(() => {
  window.sessionStorage.clear()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe('api session expiry handoff', () => {
  it('stores a backend security reason without exposing it in the login URL', async () => {
    const fetchMock = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).includes('/api/v1/auth/logout')) {
        return new Response('{}', { status: 200 })
      }
      return new Response(JSON.stringify({
        code: '1001',
        error: {
          error_code: 'TOKEN_REUSED',
          message: 'Suspicious login activity detected',
        },
      }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(api('/api/v1/ops/alerts')).rejects.toMatchObject({
      status: 401,
      errorCode: 'TOKEN_REUSED',
    })

    await vi.waitFor(() => {
      expect(mocks.routerReplace).toHaveBeenCalledWith({
        path: '/login',
        query: {
          redirect: '/ops/alerts?status=open',
        },
      })
    })
    expect(mocks.clearAuth).toHaveBeenCalledOnce()
    expect(consumeSessionNotice()).toBe('TOKEN_REUSED')
  })
})
