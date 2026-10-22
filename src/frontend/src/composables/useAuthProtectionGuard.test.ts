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

describe('authenticated Protection route access', () => {
  beforeEach(() => { clearAuth(); setupAuthGuard() })
  afterEach(() => { clearAuth(); vi.unstubAllGlobals() })

  it.each(['/protection/backups', '/Protection/Backups/', '/protection/backup-sources', '/node/repositories', '/protection/backups/create'])(
    'rejects a Configuration-only direct visit to %s before mounting it', async path => {
      const fetch = session(['configuration.roles.view'])
      expect(await navigate(path)).toHaveBeenCalledExactlyOnceWith('/')
      expect(fetch).toHaveBeenCalledTimes(1)
    },
  )

  it('honors Group-only Source access and picks its permitted tab', async () => {
    session([], ['protection.source_nas.view'])
    expect(await navigate('/protection/backup-sources')).toHaveBeenCalledExactlyOnceWith('/protection/backup-sources?tab=nas')
    expect(await navigate('/protection/backup-sources', { tab: 'nas' })).toHaveBeenCalledExactlyOnceWith()
  })

  it('rechecks the complete cached profile after revocation', async () => {
    const fetch = session(['protection.source_hosts.view'])
    await navigate('/protection/backup-sources')
    currentUser.value!.access_profile!.permissions = []
    expect(await navigate('/protection/backup-sources')).toHaveBeenCalledExactlyOnceWith('/')
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('checks the refreshed profile on the incomplete-session path', async () => {
    const fetch = session(['protection.backup_configurations.view'])
    await navigate('/protection/backups')
    currentUser.value!.access_profile!.role = ''
    expect(await navigate('/protection/backups/create')).toHaveBeenCalledExactlyOnceWith('/protection/backups')
    expect(fetch).toHaveBeenCalledTimes(2)
  })
})
