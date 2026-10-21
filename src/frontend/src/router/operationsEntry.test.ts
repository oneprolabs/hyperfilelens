// @vitest-environment jsdom
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { enterOperations } from './operationsEntry'
import { router as appRouter } from './index'

const mocks = vi.hoisted(() => ({ permissions: [] as string[], monitor: true }))
vi.mock('../composables/useTenantPermissions', () => ({
  useTenantPermissions: () => ({ has: (key: string) => mocks.permissions.includes(key) }),
}))
vi.mock('../ops/resolveRoutes', () => ({
  hasTenantHostMonitor: () => mocks.monitor,
  resolveTenantOpsRoutes: () => [],
}))

describe('Operations entry', () => {
  beforeEach(() => { mocks.permissions = []; mocks.monitor = true })

  it.each([
    [[], '/'],
    [['operations.host_monitoring.view'], '/ops/host-monitor'],
    [['operations.events.view'], '/ops/events'],
    [['operations.tasks.view'], '/ops/tasks'],
    [['operations.audit_logs.view'], '/ops/audit-logs'],
  ])('selects the permitted entry for %j', (permissions, path) => {
    mocks.permissions = permissions
    expect(enterOperations()).toEqual({ path })
  })

  it('preserves organization context but not stale page-specific parameters', () => {
    mocks.permissions = ['operations.tasks.view']
    expect(enterOperations({ query: { org: 'tenant-a', id: 'stale' } }))
      .toEqual({ path: '/ops/tasks', query: { org: 'tenant-a' } })
    expect(enterOperations({ query: { org: ['tenant-a'] } })).toEqual({ path: '/ops/tasks' })
    mocks.monitor = false
    mocks.permissions = ['operations.host_monitoring.view', 'operations.events.view']
    expect(enterOperations()).toEqual({ path: '/ops/events' })
  })

  function testRouter() {
    const entry = appRouter.getRoutes().find(route => route.path === '/ops')!
    expect(entry.redirect).toBeUndefined()
    return createRouter({
      history: createMemoryHistory(),
      routes: [
        ...['/', '/node/roles', '/ops/tasks'].map(path => ({ path, component: { render: () => null } })),
        { path: '/ops', component: { render: () => null }, beforeEnter: entry.beforeEnter },
      ],
    })
  }

  it('resolves only after the session guard has loaded grants', async () => {
    const router = testRouter()
    router.beforeEach(async () => {
      await Promise.resolve()
      mocks.permissions = ['operations.tasks.view']
    })
    await router.push('/ops')
    expect(router.currentRoute.value.path).toBe('/ops/tasks')
  })

  it('preserves Back and Forward history after entry resolution', async () => {
    const router = testRouter()
    mocks.permissions = ['operations.tasks.view']
    await router.push('/')
    await router.push('/node/roles')
    await router.push('/ops')
    async function move(direction: 'back' | 'forward') {
      const navigation = new Promise<void>(resolve => {
        const remove = router.afterEach(() => { remove(); resolve() })
      })
      router[direction]()
      await navigation
    }
    await move('back')
    expect(router.currentRoute.value.path).toBe('/node/roles')
    await move('forward')
    expect(router.currentRoute.value.path).toBe('/ops/tasks')
  })
})
