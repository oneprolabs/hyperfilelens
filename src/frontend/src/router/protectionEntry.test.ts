// @vitest-environment jsdom
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { enterProtection } from './protectionEntry'
import { router as appRouter } from './index'

const mocks = vi.hoisted(() => ({ permissions: [] as string[] }))
vi.mock('../composables/useTenantPermissions', () => ({
  useTenantPermissions: () => ({ has: (key: string) => mocks.permissions.includes(key) }),
}))

describe('Protection module entry', () => {
  beforeEach(() => { mocks.permissions = [] })

  it.each([
    [[], '/'],
    [['protection.backup_configurations.view'], '/protection/backups'],
    [['protection.backup_configurations.view', 'protection.source_hosts.view'], '/protection/backups'],
    [['protection.source_nas.view'], '/protection/backup-sources?tab=nas'],
    [['protection.object_storage_repositories.view'], '/node/repositories?tab=s3'],
    [['protection.file_filters.view'], '/protection/policies?tab=filter'],
  ])('selects a permitted page for %j', (permissions, expected) => {
    mocks.permissions = permissions
    const [path, search] = expected.split('?')
    const query: Record<string, string> = {}
    new URLSearchParams(search).forEach((value, key) => { query[key] = value })
    expect(enterProtection()).toEqual(search
      ? { path, query } : { path })
  })

  it('keeps the explicit organization context, not stale tab-specific detail parameters', () => {
    mocks.permissions = ['protection.source_nas.view']
    expect(enterProtection({ query: { org: 'tenant-a', id: 'stale' } })).toEqual({
      path: '/protection/backup-sources', query: { tab: 'nas', org: 'tenant-a' },
    })
  })

  function testRouter() {
    const entry = appRouter.getRoutes().find(route => route.path === '/protection')!
    expect(entry.redirect).toBeUndefined()
    return createRouter({
      history: createMemoryHistory(),
      routes: [
        ...['/', '/node/roles', '/protection/backup-sources', '/protection/backups', '/protection/backup-configurations', '/node/repositories']
          .map(path => ({ path, component: { render: () => null } })),
        { path: '/protection', component: { render: () => null }, beforeEnter: entry.beforeEnter },
      ],
    })
  }

  it('resolves the target only after the session guard has loaded the grants', async () => {
    const router = testRouter()
    router.beforeEach(async () => {
      await Promise.resolve()
      mocks.permissions = ['protection.source_nas.view']
    })
    await router.push('/protection')
    expect(router.currentRoute.value.fullPath).toBe('/protection/backup-sources?tab=nas')
  })

  it('preserves Back and Forward history when selecting the permitted entry', async () => {
    const router = testRouter()
    mocks.permissions = ['protection.object_storage_repositories.view']
    await router.push('/')
    await router.push('/node/roles')
    await router.push('/protection')
    async function move(direction: 'back' | 'forward') {
      const navigation = new Promise<void>((resolve) => {
        const remove = router.afterEach(() => { remove(); resolve() })
      })
      router[direction]()
      await navigation
    }
    expect(router.currentRoute.value.fullPath).toBe('/node/repositories?tab=s3')
    await move('back')
    expect(router.currentRoute.value.path).toBe('/node/roles')
    await move('forward')
    expect(router.currentRoute.value.fullPath).toBe('/node/repositories?tab=s3')
  })
})
