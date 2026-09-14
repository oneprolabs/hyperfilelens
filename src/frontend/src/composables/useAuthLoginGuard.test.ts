// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type Guard = (
  to: { path: string; query: Record<string, unknown>; meta: Record<string, unknown> },
  from: { path: string },
  next: ReturnType<typeof vi.fn>,
) => Promise<void>

const mocks = vi.hoisted(() => ({
  beforeEach: vi.fn(),
  fetchDeployProfile: vi.fn(),
  guard: null as Guard | null,
  refreshAuthToken: vi.fn(),
}))

vi.mock('../router', () => ({
  router: {
    currentRoute: { value: { path: '/login', fullPath: '/login' } },
    beforeEach: mocks.beforeEach.mockImplementation((guard: Guard) => {
      mocks.guard = guard
    }),
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
  fetchDeployProfile: mocks.fetchDeployProfile,
  resolvePostLoginPath: vi.fn().mockResolvedValue('/'),
  shouldForceDeployProfileRefresh: vi.fn().mockReturnValue(false),
}))

import { clearAuth, setupAuthGuard } from './useAuth'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const tenantProfile = {
  site_role: 'tenant',
  email_signup_enabled: false,
  email_code_login_available: false,
  platform_ops_enabled: true,
  password_reset_available: true,
  tenant_public_url: 'https://tenant.example.test',
  admin_console_url: 'https://ops.example.test',
  landing_path: '/',
  admin_console_landing_path: '/platform-ops/overview',
  admin_console_entry_visible: true,
  platform_ops_access_allowed: false,
}

describe('login route authentication guard', () => {
  beforeEach(() => {
    clearAuth()
    mocks.beforeEach.mockClear()
    mocks.fetchDeployProfile.mockReset()
    mocks.guard = null
    setupAuthGuard()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    clearAuth()
  })

  it('holds the login route until session inspection redirects an authenticated user', async () => {
    let resolveSession!: (response: Response) => void
    const sessionResponse = new Promise<Response>((resolve) => {
      resolveSession = resolve
    })
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(sessionResponse))
    mocks.fetchDeployProfile.mockResolvedValue(tenantProfile)
    const next = vi.fn()

    const navigation = mocks.guard?.(
      { path: '/login', query: {}, meta: {} },
      { path: '/' },
      next,
    )

    await Promise.resolve()
    expect(next).not.toHaveBeenCalled()

    resolveSession(jsonResponse({
      data: {
        user: { id: 7, email: 'person@example.com', username: 'person' },
        refresh_available: false,
      },
    }))
    await navigation

    expect(next).toHaveBeenCalledOnce()
    expect(next).toHaveBeenCalledWith('/')
  })

  it('allows the login page after the backend confirms there is no session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      data: { user: null, refresh_available: false },
    })))
    const next = vi.fn()

    await mocks.guard?.(
      { path: '/login', query: {}, meta: {} },
      { path: '/' },
      next,
    )

    expect(next).toHaveBeenCalledOnce()
    expect(next).toHaveBeenCalledWith()
    expect(mocks.fetchDeployProfile).not.toHaveBeenCalled()
  })
})
