// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearSharedSessionNotice, consumeSessionNotice } from './sessionNotice'

const mocks = vi.hoisted(() => ({
  clearAuth: vi.fn(),
  getRouteRequestSignal: vi.fn(),
  refreshAuthToken: vi.fn(),
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

vi.mock('./authRefresh', () => ({
  refreshAuthToken: mocks.refreshAuthToken,
}))

import { api } from './api'

afterEach(() => {
  window.sessionStorage.clear()
  clearSharedSessionNotice()
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

  it('keeps the session when refresh fails without a terminal response', async () => {
    mocks.refreshAuthToken.mockResolvedValue({ ok: false, networkError: true })
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: '1001',
      error: { message: 'Authentication required' },
    }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api('/api/v1/ops/alerts')).rejects.toMatchObject({
      errorCode: 'NETWORK.UNAVAILABLE',
    })

    expect(mocks.refreshAuthToken).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(mocks.clearAuth).not.toHaveBeenCalled()
    expect(mocks.routerReplace).not.toHaveBeenCalled()
  })

  it('does not refresh again or log out after the retried request returns 401', async () => {
    mocks.refreshAuthToken.mockResolvedValue({ ok: true, status: 200 })
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({
      code: '1001',
      error: { message: 'Not authorized for this resource' },
    }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api('/api/v1/ops/alerts')).rejects.toMatchObject({ status: 401 })

    expect(mocks.refreshAuthToken).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({
      headers: expect.objectContaining({ 'X-Retry': 'true' }),
    })
    expect(mocks.clearAuth).not.toHaveBeenCalled()
    expect(mocks.routerReplace).not.toHaveBeenCalled()
  })
})
