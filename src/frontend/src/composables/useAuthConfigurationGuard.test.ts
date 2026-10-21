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
vi.mock('../governance/resolveRoutes', () => ({
  resolveGovernanceRoutes: () => ['organization', 'members', 'resource-groups', 'roles', 'resources', 'subscription']
    .map(page => ({ path: 'node/' + page })),
}))
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

describe('authenticated Configuration route access', () => {
  beforeEach(() => { clearAuth(); setupAuthGuard() })
  afterEach(() => { clearAuth(); vi.unstubAllGlobals() })

  it.each(['/node/organization', '/NODE/MEMBERS/', '/node/resource-groups', '/node/roles', '/node/resources', '/node/subscription', '/node/members/new', '/node/roles/operator/edit'])(
    'rejects a member with only Protection permissions before mounting %s', async path => {
      const fetch = session(['protection.source_hosts.view'])
      expect(await navigate(path)).toHaveBeenCalledExactlyOnceWith('/')
      expect(fetch).toHaveBeenCalledTimes(1)
    },
  )

  it('honors Group-only Resources without opening organization-only pages', async () => {
    session([], ['configuration.resources.view'])
    expect(await navigate('/node/organization')).toHaveBeenCalledExactlyOnceWith('/node/resources')
    expect(await navigate('/node/subscription')).toHaveBeenCalledExactlyOnceWith('/node/resources')
    expect(await navigate('/node/resources')).toHaveBeenCalledExactlyOnceWith()
  })

  it('keeps valid Group association management but not organization-level Group editing', async () => {
    session([], ['configuration.groups.view', 'configuration.groups.manage_members', 'configuration.groups.manage_resources'])
    expect(await navigate('/node/resource-groups/7/members')).toHaveBeenCalledExactlyOnceWith()
    expect(await navigate('/node/resource-groups/7/resources')).toHaveBeenCalledExactlyOnceWith()
    expect(await navigate('/node/resource-groups/7/edit')).toHaveBeenCalledExactlyOnceWith('/node/resource-groups')
  })

  it('never treats Group grants as organization-level Members, Roles or Subscription access', async () => {
    session([], ['configuration.members.view', 'configuration.roles.view', 'configuration.subscriptions.view'])
    for (const path of ['/node/members', '/node/roles', '/node/subscription']) {
      expect(await navigate(path)).toHaveBeenCalledExactlyOnceWith('/')
    }
  })

  it('preserves verified organization context, discarding stale page parameters on redirect', async () => {
    session(['configuration.resources.view'])
    expect(await navigate('/node/members', { org: 'tenant-a', id: 'old', tab: 'members' }))
      .toHaveBeenCalledExactlyOnceWith('/node/resources?org=tenant-a')
  })

  it('rechecks the complete cached profile after revocation', async () => {
    const fetch = session(['configuration.resources.view'])
    await navigate('/node/resources')
    currentUser.value!.access_profile!.permissions = []
    expect(await navigate('/node/resources')).toHaveBeenCalledExactlyOnceWith('/')
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('checks the refreshed profile on the incomplete-session path', async () => {
    const fetch = session(['configuration.roles.view'])
    await navigate('/node/roles')
    currentUser.value!.access_profile!.role = ''
    expect(await navigate('/node/roles/new')).toHaveBeenCalledExactlyOnceWith('/node/roles')
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('does not require Configuration permission to create an independent organization', async () => {
    session(['protection.source_hosts.view'])
    expect(await navigate('/account/organizations/new')).toHaveBeenCalledExactlyOnceWith()
  })
})
