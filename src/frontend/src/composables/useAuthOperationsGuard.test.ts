// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type Location = { path: string; query: Record<string, unknown>; meta: Record<string, unknown> }
type Guard = (to: Location, from: { path: string }, next: ReturnType<typeof vi.fn>) => Promise<void>
const mocks = vi.hoisted(() => ({ guard: null as Guard | null }))
vi.mock('../router', () => ({
  router: {
    currentRoute: { value: { path: '/login', fullPath: '/login' } },
    beforeEach: (guard: Guard) => { mocks.guard = guard },
    replace: vi.fn(),
  },
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('../lib/authRefresh', () => ({ refreshAuthToken: vi.fn() }))
vi.mock('./useDeployProfile', () => ({
  clearDeployProfileCache: vi.fn(), fetchDeployProfile: vi.fn(),
  resolvePostLoginPath: vi.fn(), shouldForceDeployProfileRefresh: vi.fn(),
}))
vi.mock('../lib/insightAccess', async (load) => {
  const actual = await load<typeof import('../lib/insightAccess')>()
  return {
    ...actual,
    insightPermissionHints: (profile: Parameters<typeof actual.insightPermissionHints>[0]) =>
      actual.insightPermissionHints(profile, true),
  }
})
vi.mock('../ops/resolveRoutes', () => ({ hasTenantHostMonitor: () => true }))
import { clearAuth, currentUser, setupAuthGuard } from './useAuth'

function session(permissions: string[] = [], group_permissions: string[] = []) {
  const user = {
    id: 7, username: 'reader', email: 'reader@example.test',
    access_profile: {
      org_key: 'tenant-a', role: 'reader', permissions, group_permissions,
      visible_features: [], available_platforms: [], landing_path: '/',
    },
  }
  const fetch = vi.fn().mockImplementation(async () => new Response(
    JSON.stringify({ user }), { headers: { 'Content-Type': 'application/json' } },
  ))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

async function navigate(path: string, query: Record<string, unknown> = {}) {
  const next = vi.fn()
  await mocks.guard?.({ path, query, meta: {} }, { path: '/' }, next)
  return next
}

describe('authenticated Operations route access', () => {
  beforeEach(() => { clearAuth(); setupAuthGuard() })
  afterEach(() => { clearAuth(); vi.unstubAllGlobals() })

  it.each(['/ops/host-monitor', '/OPS/Host-Monitor/', '/ops/events', '/ops/tasks', '/ops/alerts/rules/create', '/ops/channels/7/edit'])(
    'rejects a Configuration-only direct visit to %s before mounting it', async path => {
      const fetch = session(['configuration.roles.view'])
      expect(await navigate(path)).toHaveBeenCalledExactlyOnceWith('/')
      expect(fetch).toHaveBeenCalledTimes(1)
    },
  )

  it('honors Group-only task access without entering the monitoring page', async () => {
    session([], ['operations.tasks.view'])
    expect(await navigate('/ops/host-monitor')).toHaveBeenCalledExactlyOnceWith('/ops/tasks')
    expect(await navigate('/ops/tasks')).toHaveBeenCalledExactlyOnceWith()
  })

  it('rechecks the complete cached profile after revocation', async () => {
    const fetch = session(['operations.events.view'])
    await navigate('/ops/events')
    currentUser.value!.access_profile!.permissions = []
    expect(await navigate('/ops/events')).toHaveBeenCalledExactlyOnceWith('/')
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('checks the refreshed profile on the incomplete-session path', async () => {
    const fetch = session(['operations.alert_rules.view'])
    await navigate('/ops/alerts/rules')
    currentUser.value!.access_profile!.role = ''
    expect(await navigate('/ops/alerts/rules/create')).toHaveBeenCalledExactlyOnceWith('/ops/alerts/rules')
    expect(fetch).toHaveBeenCalledTimes(2)
  })
})
