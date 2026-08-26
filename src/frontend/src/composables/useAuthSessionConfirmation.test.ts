// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  refreshAuthToken: vi.fn(),
}))

vi.mock('../router', () => ({
  router: {
    currentRoute: { value: { path: '/login', fullPath: '/login' } },
    beforeEach: vi.fn(),
    replace: vi.fn(),
  },
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
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

import { confirmCurrentSession, currentUser } from './useAuth'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('confirmCurrentSession', () => {
  beforeEach(() => {
    mocks.refreshAuthToken.mockReset()
    currentUser.value = null
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    currentUser.value = null
  })

  it('returns the authenticated user when the session can be inspected', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      data: {
        user: { id: 7, email: 'person@example.com', username: 'person' },
        refresh_available: false,
      },
    })))

    await expect(confirmCurrentSession()).resolves.toMatchObject({
      state: 'authenticated',
      user: { id: 7 },
    })
  })

  it('distinguishes an explicitly absent session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({}, 401)))

    await expect(confirmCurrentSession()).resolves.toEqual({ state: 'unauthenticated' })
  })

  it('keeps the result unknown when session inspection cannot connect', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network unavailable')))

    await expect(confirmCurrentSession()).resolves.toEqual({ state: 'unknown' })
  })

  it('keeps the result unknown when token refresh loses its response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      data: { user: null, refresh_available: true },
    })))
    mocks.refreshAuthToken.mockResolvedValue({ ok: false, networkError: true })

    await expect(confirmCurrentSession()).resolves.toEqual({ state: 'unknown' })
  })
})
