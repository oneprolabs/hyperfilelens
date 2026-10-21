// @vitest-environment jsdom
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { isNavigationFailure, NavigationFailureType } from 'vue-router'
import TopNav from '../app/layout/TopNav.vue'
import { api } from '../lib/api'
import { getRouteRequestSignal } from '../lib/routeRequestAbort'
import { en } from '../locales/en'
import { router } from './index'

const mocks = vi.hoisted(() => ({
  permissions: [] as string[],
  beginTransition: vi.fn(),
  warning: vi.fn(),
}))

// Keep the production router and all of its global request-scope guards. Only
// page components are replaced; they are unrelated to the navigation lifecycle.
vi.mock('./lazyRoute', () => ({ lazyRoute: () => ({ render: () => null }) }))
vi.mock('../app/layout/AppShell.vue', () => ({ default: { render: () => null } }))
vi.mock('../lib/requestContext', () => ({ getCorrelationHeaders: () => ({}) }))
vi.mock('../lib/routeTransition', () => ({
  beginRouteTransition: mocks.beginTransition,
  finishRouteTransition: vi.fn(),
}))
vi.mock('element-plus', () => ({ ElMessage: { warning: mocks.warning } }))
vi.mock('../composables/useTenantPermissions', () => ({
  useTenantPermissions: () => ({ has: (key: string) => mocks.permissions.includes(key) }),
}))

enableAutoUnmount(afterEach)

function pendingRequest() {
  let respond!: (response: Response) => void
  const fetch = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((resolve, reject) => {
    respond = resolve
    init?.signal?.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')), { once: true })
  }))
  vi.stubGlobal('fetch', fetch)
  // Attach the rejection handler immediately, including for cancellation tests.
  const result = api('/api/v1/subscription/licenses/current/').then(
    data => ({ data, error: null }),
    (error: unknown) => ({ data: null, error }),
  )
  return {
    signal: getRouteRequestSignal(),
    fetch,
    result,
    complete: () => respond(new Response(JSON.stringify({ usage: { users: 3 } }), {
      headers: { 'Content-Type': 'application/json' },
    })),
  }
}

function mountTopNav() {
  return mount(TopNav, {
    global: {
      plugins: [router, createI18n({ legacy: false, locale: 'en', messages: { en } })],
      stubs: { NavNotificationPopover: true, NavUserMenu: true, LanguageSwitcher: true },
    },
  })
}

async function clickOperations(wrapper: ReturnType<typeof mountTopNav>) {
  const completed = new Promise<void>(resolve => {
    const remove = router.afterEach((_to, _from, failure) => {
      if (failure && isNavigationFailure(failure, NavigationFailureType.cancelled)) return
      remove()
      resolve()
    })
  })
  wrapper.get<HTMLAnchorElement>('a[href="/ops"]').element.click()
  await completed
  await flushPromises()
}

describe('Configuration intermediate navigation with real request cancellation', () => {
  beforeEach(async () => {
    mocks.permissions = ['configuration.subscriptions.view']
    await router.replace('/node/subscription')
    mocks.beginTransition.mockClear()
    mocks.warning.mockClear()
  })
  afterEach(() => vi.unstubAllGlobals())

  it.each(['/node', '/node/', '/NODE', '/node?entry=configuration'])(
    'preserves an in-flight request when %s resolves to the current page',
    async (entry) => {
      const request = pendingRequest()
      const navigation = await router.push(entry)
      expect(isNavigationFailure(navigation, NavigationFailureType.duplicated)).toBe(true)
      expect(router.currentRoute.value.path).toBe('/node/subscription')
      expect(request.signal.aborted).toBe(false)
      expect(mocks.beginTransition).not.toHaveBeenCalled()
      request.complete()
      expect(await request.result).toEqual({ data: { usage: { users: 3 } }, error: null })
      expect(request.fetch).toHaveBeenCalledTimes(1)
    },
  )

  it('also preserves the request through the desktop navigation click handler', async () => {
    const wrapper = mountTopNav()
    const request = pendingRequest()
    await wrapper.get('a[href="/node"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/node/subscription')
    expect(request.signal.aborted).toBe(false)
    expect(mocks.beginTransition).not.toHaveBeenCalled()
    request.complete()
    expect((await request.result).error).toBeNull()
  })

  it.each(['router', 'desktop'])('still cancels requests for a different final page through %s navigation', async (method) => {
    await router.replace('/ops/events')
    mocks.beginTransition.mockClear()
    const request = pendingRequest()
    if (method === 'desktop') {
      const wrapper = mountTopNav()
      await wrapper.get('a[href="/node"]').trigger('click')
      await flushPromises()
    } else {
      await router.push('/node')
    }
    expect(router.currentRoute.value.path).toBe('/node/subscription')
    expect(request.signal.aborted).toBe(true)
    expect(await request.result).toMatchObject({ error: { name: 'AbortError' } })
    expect(mocks.beginTransition).toHaveBeenCalledTimes(1)
  })

  it('leaves requests intact while authentication is pending, then resolves using loaded permissions', async () => {
    await router.replace('/ops/events')
    mocks.permissions = []
    mocks.beginTransition.mockClear()
    const request = pendingRequest()
    let releaseAuth!: () => void
    let authStarted!: () => void
    const started = new Promise<void>(resolve => { authStarted = resolve })
    const auth = new Promise<void>(resolve => { releaseAuth = resolve })
    const removeGuard = router.beforeEach(async (to) => {
      if (to.meta.configurationEntry) {
        authStarted()
        await auth
      }
    })
    try {
      const navigation = router.push('/node')
      await started
      expect(request.signal.aborted).toBe(false)
      expect(mocks.warning).not.toHaveBeenCalled()
      expect(mocks.beginTransition).not.toHaveBeenCalled()
      mocks.permissions = ['configuration.subscriptions.view']
      releaseAuth()
      await navigation
      expect(router.currentRoute.value.path).toBe('/node/subscription')
      expect(request.signal.aborted).toBe(true)
      expect(await request.result).toMatchObject({ error: { name: 'AbortError' } })
      expect(mocks.beginTransition).toHaveBeenCalledTimes(1)
    } finally {
      releaseAuth()
      removeGuard()
    }
  })

  it('does not cancel Dashboard requests when no Configuration page is permitted', async () => {
    await router.replace('/')
    mocks.permissions = []
    mocks.beginTransition.mockClear()
    const request = pendingRequest()
    await router.push('/node')
    expect(router.currentRoute.value.path).toBe('/')
    expect(request.signal.aborted).toBe(false)
    expect(mocks.beginTransition).not.toHaveBeenCalled()
    expect(mocks.warning).toHaveBeenCalledTimes(1)
    request.complete()
    expect((await request.result).error).toBeNull()
  })

  it('still cancels desktop requests when Operations resolves to a different page', async () => {
    mocks.permissions.push('operations.events.view')
    const wrapper = mountTopNav()
    const request = pendingRequest()
    await clickOperations(wrapper)
    expect(router.currentRoute.value.path).toBe('/ops/events')
    expect(request.signal.aborted).toBe(true)
    expect(await request.result).toMatchObject({ error: { name: 'AbortError' } })
    expect(mocks.beginTransition).toHaveBeenCalled()
  })

  it.each(['router', 'desktop'])('preserves current Operations requests when its %s entry resolves to the same page', async method => {
    mocks.permissions = ['operations.tasks.view']
    await router.replace('/ops/tasks')
    mocks.beginTransition.mockClear()
    const request = pendingRequest()
    if (method === 'desktop') {
      const wrapper = mountTopNav()
      await clickOperations(wrapper)
    } else {
      const navigation = await router.push('/ops')
      expect(isNavigationFailure(navigation, NavigationFailureType.duplicated)).toBe(true)
    }
    expect(router.currentRoute.value.path).toBe('/ops/tasks')
    expect(request.signal.aborted).toBe(false)
    expect(mocks.beginTransition).not.toHaveBeenCalled()
    request.complete()
    expect((await request.result).error).toBeNull()
  })

  it('does not cancel Dashboard requests when no Operations page is permitted', async () => {
    await router.replace('/')
    mocks.permissions = []
    mocks.beginTransition.mockClear()
    const request = pendingRequest()
    await router.push('/ops')
    expect(router.currentRoute.value.path).toBe('/')
    expect(request.signal.aborted).toBe(false)
    expect(mocks.beginTransition).not.toHaveBeenCalled()
    request.complete()
    expect((await request.result).error).toBeNull()
  })

  it('waits for Operations grants before cancelling a request for a different final page', async () => {
    mocks.permissions = []
    const request = pendingRequest()
    let release!: () => void
    let started!: () => void
    const authStarted = new Promise<void>(resolve => { started = resolve })
    const auth = new Promise<void>(resolve => { release = resolve })
    const remove = router.beforeEach(async to => {
      if (to.meta.permissionEntry) { started(); await auth }
    })
    try {
      const navigation = router.push('/ops')
      await authStarted
      expect(request.signal.aborted).toBe(false)
      mocks.permissions = ['operations.tasks.view']
      release()
      await navigation
      expect(router.currentRoute.value.path).toBe('/ops/tasks')
      expect(request.signal.aborted).toBe(true)
      expect(await request.result).toMatchObject({ error: { name: 'AbortError' } })
    } finally {
      release()
      remove()
    }
  })
})
