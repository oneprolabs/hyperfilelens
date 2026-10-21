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

// These authentication contracts exercise an installed Roles page. Community
// builds otherwise register only Subscription and correctly redirect elsewhere.
vi.mock('../governance/resolveRoutes', () => ({
  resolveGovernanceRoutes: () => [{ path: 'node/roles' }, { path: 'node/subscription' }],
}))

import { clearAuth, getEffectiveOrgKey, setStoredOrgKey, setupAuthGuard } from './useAuth'

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
  it('accepts a verified installation context without an invented tenant role', async () => {
    const fetch = vi.fn().mockResolvedValue(jsonResponse({
      user: {
        id: 1, email: 'root@test.local', is_staff: true,
        access_profile: {
          org_key: 'target-org', role: '', authority_source: 'installation_admin',
          permissions: ['configuration.roles.manage', 'configuration.roles.view'],
          visible_features: ['settings'], landing_path: '/',
        },
      },
    }))
    vi.stubGlobal('fetch', fetch)
    const next = vi.fn()
    await mocks.guard?.({
      path: '/node/roles', query: { org: 'target-org' }, meta: {},
    }, { path: '/' }, next)
    expect(next).toHaveBeenCalledExactlyOnceWith()
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(getEffectiveOrgKey()).toBe('target-org')
    // Selecting another organization in another tab must not retarget this tab.
    localStorage.setItem('hfl_org_key', 'another-tab-org')
    expect(getEffectiveOrgKey()).toBe('target-org')
  })

  it('does not enter an organization that the server did not verify', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      user: {
        id: 8, email: 'member@test.local',
        access_profile: {
          org_key: 'actual-org', role: 'member', permissions: [],
          visible_features: [], landing_path: '/',
        },
      },
    })))
    const next = vi.fn()
    await mocks.guard?.({
      path: '/', query: { org: 'unavailable-org' }, meta: {},
    }, { path: '/' }, next)
    expect(next).toHaveBeenCalledWith('/')
    expect(getEffectiveOrgKey()).toBe('actual-org')
  })

  it('clears both the tab context and stored default on logout', () => {
    setStoredOrgKey('tenant-context')
    expect(sessionStorage.getItem('hfl_org_key')).toBe('tenant-context')
    clearAuth()
    expect(sessionStorage.getItem('hfl_org_key')).toBeNull()
    expect(localStorage.getItem('hfl_org_key')).toBeNull()
  })

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

  it('redirects a pending account to password setup before any business route', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      code: '0000', data: { user: {
        id: 91, email: 'pending@test.local', requires_password_change: true,
        access_profile: { org_key: 'tenant-a', role: 'member', visible_features: [], landing_path: '/' },
      } },
    })))
    const next = vi.fn()
    await mocks.guard?.({ path: '/protection/backups', query: {}, meta: {} }, { path: '/login' }, next)
    expect(next).toHaveBeenCalledWith('/account/setup')
    next.mockClear()
    await mocks.guard?.({ path: '/account/setup', query: {}, meta: {} }, { path: '/' }, next)
    expect(next).toHaveBeenCalledExactlyOnceWith()
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

  it('redirects an authenticated role away from restricted governance routes', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      data: {
        user: {
          id: 9,
          email: 'operator@example.com',
          username: 'operator',
          access_profile: {
            org_key: 'acme',
            role: 'operator',
            visible_features: ['dashboard'],
            available_platforms: [],
            landing_path: '/',
          },
        },
        refresh_available: false,
      },
    })))
    const next = vi.fn()

    await mocks.guard?.(
      {
        path: '/node/roles',
        query: {},
        meta: { requiredRoles: ['owner', 'admin', 'manager', 'auditor'] },
      },
      { path: '/' },
      next,
    )

    expect(next).toHaveBeenCalledWith('/node')
  })
})
