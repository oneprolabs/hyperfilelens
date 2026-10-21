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
  clearDeployProfileCache: vi.fn(),
  fetchDeployProfile: vi.fn(),
  resolvePostLoginPath: vi.fn(),
  shouldForceDeployProfileRefresh: vi.fn(),
}))
// Exercise Enterprise grants even when this regression runs in Community CI.
vi.mock('../lib/insightAccess', async (load) => {
  const actual = await load<typeof import('../lib/insightAccess')>()
  return {
    ...actual,
    insightPermissionHints: (profile: Parameters<typeof actual.insightPermissionHints>[0]) =>
      actual.insightPermissionHints(profile, true),
  }
})

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
  return { user, fetch }
}

async function navigate(path: string) {
  const next = vi.fn()
  await mocks.guard?.({ path, query: {}, meta: {} }, { path: '/' }, next)
  return next
}

describe('authenticated Insights route access', () => {
  beforeEach(() => { clearAuth(); setupAuthGuard() })
  afterEach(() => { clearAuth(); vi.unstubAllGlobals() })

  it.each(['/insight/copilot', '/insight/copilot/new', '/insight/usage', '/insight/gateways', '/Insight/CoPilot/New/'])(
    'rejects a Configuration-only direct visit to %s after loading the session', async (path) => {
      const { fetch } = session(['configuration.roles.view'])
      const next = await navigate(path)
      expect(next).toHaveBeenCalledExactlyOnceWith('/')
      expect(fetch).toHaveBeenCalledTimes(1)
    },
  )

  it('allows viewing Chats but not entering the creation form', async () => {
    session(['insights.chats.view'])
    expect(await navigate('/insight/copilot')).toHaveBeenCalledExactlyOnceWith()
    expect(await navigate('/insight/copilot/new')).toHaveBeenCalledExactlyOnceWith('/insight/copilot')
  })

  it('recognizes Group-only Chat viewing and use grants', async () => {
    session([], ['insights.chats.view', 'insights.chats.use'])
    expect(await navigate('/insight/copilot/new')).toHaveBeenCalledExactlyOnceWith()
  })

  it('redirects a Gateway-only reader to their permitted page, not Chat', async () => {
    session(['insights.private_data_gateways.view'])
    expect(await navigate('/insight/copilot')).toHaveBeenCalledExactlyOnceWith('/insight/gateways')
  })

  it('also checks an already initialized session after grants change', async () => {
    const { fetch } = session(['insights.chats.view', 'insights.chats.use'])
    await navigate('/insight/copilot')
    currentUser.value!.access_profile!.permissions = ['configuration.roles.view']
    expect(await navigate('/insight/copilot/new')).toHaveBeenCalledExactlyOnceWith('/')
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('checks the refreshed profile on the incomplete-session path', async () => {
    const { fetch } = session(['insights.chats.view'])
    await navigate('/insight/copilot')
    currentUser.value!.access_profile!.role = ''
    expect(await navigate('/insight/copilot/new')).toHaveBeenCalledExactlyOnceWith('/insight/copilot')
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('leaves shared Q&A token access to its independent backend contract', async () => {
    session(['configuration.roles.view'])
    expect(await navigate('/insight/copilot/shared')).toHaveBeenCalledExactlyOnceWith()
  })
})
