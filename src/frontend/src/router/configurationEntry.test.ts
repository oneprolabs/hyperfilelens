// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { enterConfiguration, resolveConfigurationEntry } from './configurationEntry'
import { router as appRouter } from './index'

const mocks = vi.hoisted(() => ({
  organization: [] as string[],
  group: [] as string[],
  warning: vi.fn(),
}))
vi.mock('element-plus', () => ({ ElMessage: { warning: mocks.warning } }))
vi.mock('../composables/useTenantPermissions', () => ({
  useTenantPermissions: () => ({
    has: (key: string, organizationOnly = false) =>
      mocks.organization.includes(key) || (!organizationOnly && mocks.group.includes(key)),
  }),
}))

const routes = [
  'node/organization', 'node/members', 'node/resource-groups',
  'node/roles', 'node/resources', 'node/subscription',
].map(path => ({ path }))
const has = (key: string, organizationOnly = false) =>
  mocks.organization.includes(key) || (!organizationOnly && mocks.group.includes(key))

describe('Configuration entry', () => {
  beforeEach(() => {
    mocks.organization = []
    mocks.group = []
    mocks.warning.mockClear()
  })

  it.each([
    ['configuration.organizations.view', '/node/organization'],
    ['configuration.members.view', '/node/members'],
    ['configuration.groups.view', '/node/resource-groups'],
    ['configuration.roles.view', '/node/roles'],
    ['configuration.resources.view', '/node/resources'],
    ['configuration.subscriptions.view', '/node/subscription'],
  ])('lands on the first permitted page for %s', (permission, expected) => {
    mocks.organization = [permission]
    expect(resolveConfigurationEntry(routes, has)).toBe(expected)
  })

  it('uses sidebar order, not the order permissions were granted', () => {
    mocks.organization = [
      'configuration.subscriptions.view', 'configuration.roles.view',
      'configuration.groups.view', 'configuration.members.view',
    ]
    expect(resolveConfigurationEntry(routes, has)).toBe('/node/members')
    mocks.organization.push('configuration.organizations.view')
    expect(resolveConfigurationEntry(routes, has)).toBe('/node/organization')
  })

  it('accepts scoped Groups and Resources grants but not organization-only grants', () => {
    mocks.group = [
      'configuration.organizations.view', 'configuration.members.view',
      'configuration.roles.view', 'configuration.subscriptions.view',
    ]
    expect(resolveConfigurationEntry(routes, has)).toBe('/')
    mocks.group.push('configuration.resources.view')
    expect(resolveConfigurationEntry(routes, has)).toBe('/node/resources')
    mocks.group.push('configuration.groups.view')
    expect(resolveConfigurationEntry(routes, has)).toBe('/node/resource-groups')
  })

  it('only selects pages registered in the current build (Community Subscription)', () => {
    mocks.organization = ['configuration.organizations.view', 'configuration.subscriptions.view']
    expect(resolveConfigurationEntry([{ path: 'node/subscription' }], has)).toBe('/node/subscription')
  })

  it('returns to the dashboard with a permission notice if no page is authorized', () => {
    expect(enterConfiguration(routes)).toEqual({ path: '/', replace: true })
    expect(mocks.warning).toHaveBeenCalledExactlyOnceWith(
      'You do not have permission to view any Configuration pages.',
    )
  })

  it('waits for the global auth guard on a fresh /node entry', async () => {
    const node = appRouter.options.routes.find(route => route.path === '/')!
      .children!.find(route => route.path === 'node')!
    // Real entry guard, with lightweight shells to avoid mounting the app.
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { ...node, path: '/node' },
        ...routes.map(route => ({ path: `/${route.path}`, component: { render: () => null } })),
        { path: '/', component: { render: () => null } },
      ],
    })
    let finishAuth!: () => void
    const auth = new Promise<void>(resolve => { finishAuth = resolve })
    router.beforeEach(async () => { await auth })
    const navigation = router.push('/node')
    await Promise.resolve()
    expect(mocks.warning).not.toHaveBeenCalled()
    mocks.organization = ['configuration.subscriptions.view']
    finishAuth()
    await navigation
    expect(router.currentRoute.value.path).toBe('/node/subscription')
    expect(mocks.warning).not.toHaveBeenCalled()
  })

  it('does not loop through Organization when an unauthorized route returns to /node', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/node', component: { render: () => null }, beforeEnter: () => enterConfiguration(routes) },
        { path: '/node/organization', component: { render: () => null } },
        { path: '/', component: { render: () => null } },
      ],
    })
    const guard = vi.fn(to => to.path === '/node/organization' ? '/node' : true)
    router.beforeEach(guard)
    await router.push('/node/organization')
    expect(router.currentRoute.value.path).toBe('/')
    expect(guard).toHaveBeenCalledTimes(3)
    expect(mocks.warning).toHaveBeenCalledTimes(1)
  })
})
