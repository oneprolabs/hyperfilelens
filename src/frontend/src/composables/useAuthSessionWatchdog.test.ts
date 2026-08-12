// @vitest-environment jsdom

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { consumeSessionNotice } from '../lib/sessionNotice'

const mocks = vi.hoisted(() => ({
  currentRoute: {
    value: {
      path: '/ops/alerts',
      fullPath: '/ops/alerts?status=open',
    },
  },
  refreshAuthToken: vi.fn().mockResolvedValue({
    ok: false,
    errorCode: 'TOKEN_REUSED',
  }),
  routerReplace: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('../router', () => ({
  router: {
    currentRoute: mocks.currentRoute,
    beforeEach: vi.fn(),
    replace: mocks.routerReplace,
  },
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}))

vi.mock('../lib/authRefresh', () => ({
  refreshAuthToken: mocks.refreshAuthToken,
}))

vi.mock('../lib/requestContext', () => ({
  getCorrelationHeaders: () => ({}),
}))

vi.mock('./useDeployProfile', () => ({
  clearDeployProfileCache: vi.fn(),
  fetchDeployProfile: vi.fn(),
  resolvePostLoginPath: vi.fn().mockResolvedValue('/'),
  shouldForceDeployProfileRefresh: vi.fn().mockReturnValue(false),
}))

import { setupSessionWatchdog, useAuth } from './useAuth'

describe('session watchdog notice handoff', () => {
  beforeAll(() => {
    vi.useFakeTimers()
    window.sessionStorage.clear()
  })

  afterAll(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
    window.sessionStorage.clear()
  })

  it('stores a verified refresh failure without exposing the reason in the URL', async () => {
    const fetchMock = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).includes('/api/v1/auth/logout')) {
        return new Response('{}', { status: 200 })
      }
      return new Response(JSON.stringify({
        authenticated: false,
        refresh_available: true,
        user: null,
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    useAuth().setUser({
      id: 1,
      email: 'person@example.com',
      username: 'person',
    })
    setupSessionWatchdog()
    window.dispatchEvent(new Event('focus'))

    await vi.waitFor(() => {
      expect(mocks.routerReplace).toHaveBeenCalledWith({
        path: '/login',
        query: {
          redirect: '/ops/alerts?status=open',
        },
      })
    })
    expect(mocks.refreshAuthToken).toHaveBeenCalledOnce()
    expect(consumeSessionNotice()).toBe('TOKEN_REUSED')
    expect(consumeSessionNotice()).toBeNull()
  })
})
