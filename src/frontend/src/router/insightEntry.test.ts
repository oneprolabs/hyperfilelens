// @vitest-environment jsdom
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { enterInsights } from './insightEntry'
import { router as appRouter } from './index'

const mocks = vi.hoisted(() => ({ permissions: [] as string[] }))
vi.mock('../composables/useTenantPermissions', () => ({
  useTenantPermissions: () => ({ has: (key: string) => mocks.permissions.includes(key) }),
}))

describe('Insights module entry', () => {
  beforeEach(() => { mocks.permissions = [] })

  it.each([
    [[], '/'],
    [['insights.usage.view'], '/insight/usage'],
    [['insights.private_data_gateways.view'], '/insight/gateways'],
    [['insights.chats.view'], '/insight/copilot'],
  ])('uses authenticated permissions rather than a fixed Chat redirect for %j', (permissions, expected) => {
    mocks.permissions = permissions
    expect(enterInsights()).toEqual({ path: expected })
  })

  it('selects the destination only after the global session guard finishes', async () => {
    const entry = appRouter.getRoutes().find(route => route.path === '/insight')!
    expect(entry.redirect).toBeUndefined()
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { render: () => null } },
        { path: '/insight', component: { render: () => null }, beforeEnter: entry.beforeEnter },
        { path: '/insight/usage', component: { render: () => null } },
      ],
    })
    router.beforeEach(async () => {
      await Promise.resolve()
      mocks.permissions = ['insights.usage.view']
    })
    await router.push('/insight')
    expect(router.currentRoute.value.path).toBe('/insight/usage')
  })

  it.each([
    ['insights.chats.view', '/insight/copilot'],
    ['insights.usage.view', '/insight/usage'],
    ['insights.private_data_gateways.view', '/insight/gateways'],
  ])('preserves Back and Forward history for %s', async (permission, path) => {
    mocks.permissions = [permission]
    const entry = appRouter.getRoutes().find(route => route.path === '/insight')!
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { render: () => null } },
        { path: '/node/roles', component: { render: () => null } },
        { path: '/insight', component: { render: () => null }, beforeEnter: entry.beforeEnter },
        { path, component: { render: () => null } },
      ],
    })
    async function move(direction: 'back' | 'forward') {
      const navigation = new Promise<void>((resolve) => {
        const remove = router.afterEach(() => { remove(); resolve() })
      })
      router[direction]()
      await navigation
    }
    await router.push('/')
    await router.push('/node/roles')
    await router.push('/insight')
    expect(router.currentRoute.value.path).toBe(path)
    await move('back')
    expect(router.currentRoute.value.path).toBe('/node/roles')
    await move('forward')
    expect(router.currentRoute.value.path).toBe(path)
  })
})
