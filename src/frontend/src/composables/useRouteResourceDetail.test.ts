// @vitest-environment jsdom
import { defineComponent, nextTick, h, ref, Suspense } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useRouteResourceDetail } from './useRouteResourceDetail'

vi.mock('../lib/notify', () => ({ notifyError: vi.fn() }))
import { notifyError } from '../lib/notify'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

async function setup(path = '/resources?focus=42', queryKey = 'focus') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: defineComponent({ render: () => null }) }],
  })
  await router.push(path)
  const pending = deferred<{ id: number }>()
  const load = vi.fn((_id: number, _signal: AbortSignal) => pending.promise)
  const open = vi.fn()
  const wrapper = mount(defineComponent({
    setup(_, { expose }) {
      expose(useRouteResourceDetail({
        queryKey,
        enabled: () => router.currentRoute.value.path === '/resources',
        load,
        open,
        queryForResource: () => ({ tab: 'nas' }),
      }))
      return () => null
    },
  }), { global: { plugins: [router] } })
  return { router, pending, load, open, wrapper }
}

afterEach(() => vi.clearAllMocks())

describe('useRouteResourceDetail', () => {
  it.each([
    ['/protection/backup-sources', 'openSource'],
    ['/node/repositories', 'focus'],
  ])('preserves the %s drawer when consuming its deep-link query', async (path, queryKey) => {
    const shell = readFileSync(resolve(process.cwd(), 'src/app/layout/AppShell.vue'), 'utf8')
    const keyExpression = shell.match(/<Suspense\s+:key="([\s\S]*?)"/)?.[1]
    expect(keyExpression).toBeDefined()
    // Use the real application-shell key policy, rather than a router-only harness.
    const getKey = new Function('matchedRoute', `return (${keyExpression})`) as
      (route: { path: string; fullPath: string }) => string
    const pending = deferred<{ id: number }>()
    let mountCount = 0
    const page = defineComponent({
      setup() {
        mountCount++
        const drawer = ref(false)
        useRouteResourceDetail({
          queryKey, enabled: () => true, load: () => pending.promise,
          open: () => { drawer.value = true },
        })
        return () => h('div', { 'data-drawer': drawer.value }, drawer.value ? 'Details open' : 'List')
      },
    })
    const router = createRouter({
      history: createMemoryHistory(), routes: [{ path, component: page }],
    })
    await router.push(`${path}?tab=nas&${queryKey}=42`)
    const wrapper = mount(defineComponent({
      render: () => h(RouterView, {}, {
        default: ({ Component, route }: { Component: object; route: { path: string; fullPath: string } }) =>
          h(Suspense, { key: getKey(route) }, { default: () => h(Component) }),
      }),
    }), { global: { plugins: [router] } })
    pending.resolve({ id: 42 })
    await flushPromises()
    expect(router.currentRoute.value.query[queryKey]).toBeUndefined()
    expect(wrapper.text()).toBe('Details open')
    expect(mountCount).toBe(1)
    wrapper.unmount()
  })

  it.each(['focus', 'openSource'])('loads %s independently of list rows and keeps loading until resolved', async (key) => {
    const ctx = await setup(`/resources?${key}=42&tab=nas#details`, key)
    expect(ctx.load).toHaveBeenCalledWith(42, expect.any(AbortSignal))
    expect((ctx.wrapper.vm as unknown as { loading: boolean }).loading).toBe(true)
    expect(ctx.open).not.toHaveBeenCalled()
    ctx.pending.resolve({ id: 42 })
    await flushPromises()
    expect(ctx.open).toHaveBeenCalledWith({ id: 42 })
    expect((ctx.wrapper.vm as unknown as { loading: boolean }).loading).toBe(false)
    expect(ctx.router.currentRoute.value.query).toEqual({ tab: 'nas' })
    expect(ctx.router.currentRoute.value.hash).toBe('#details')
    ctx.wrapper.unmount()
  })

  it('does not open a stale result after navigating away', async () => {
    const ctx = await setup()
    const signal = ctx.load.mock.calls[0]![1]
    await ctx.router.push('/other')
    await nextTick()
    expect(signal.aborted).toBe(true)
    ctx.pending.resolve({ id: 42 })
    await flushPromises()
    expect(ctx.open).not.toHaveBeenCalled()
    expect(ctx.router.currentRoute.value.path).toBe('/other')
    ctx.wrapper.unmount()
  })

  it('only opens the newest focus when navigating within the same page', async () => {
    const ctx = await setup()
    const second = deferred<{ id: number }>()
    ctx.load.mockReturnValueOnce(second.promise)
    await ctx.router.push('/resources?focus=43')
    await nextTick()
    ctx.pending.resolve({ id: 42 })
    await flushPromises()
    expect(ctx.open).not.toHaveBeenCalled()
    expect((ctx.wrapper.vm as unknown as { loading: boolean }).loading).toBe(true)
    second.resolve({ id: 43 })
    await flushPromises()
    expect(ctx.open).toHaveBeenCalledExactlyOnceWith({ id: 43 })
    ctx.wrapper.unmount()
  })

  it('does not fabricate a drawer for a deleted resource', async () => {
    const ctx = await setup()
    ctx.pending.reject({ status: 404 })
    await flushPromises()
    expect(ctx.open).not.toHaveBeenCalled()
    expect(notifyError).not.toHaveBeenCalled()
    expect(ctx.router.currentRoute.value.query.focus).toBeUndefined()
    expect((ctx.wrapper.vm as unknown as { loading: boolean }).loading).toBe(false)
    ctx.wrapper.unmount()
  })

  it('reports other failures and releases loading', async () => {
    const ctx = await setup()
    ctx.pending.reject(new Error('Load failed'))
    await flushPromises()
    expect(notifyError).toHaveBeenCalledOnce()
    expect(ctx.open).not.toHaveBeenCalled()
    expect((ctx.wrapper.vm as unknown as { loading: boolean }).loading).toBe(false)
    ctx.wrapper.unmount()
  })

  it.each(['0', '-1', 'abc', '1.5', '9007199254740992', '1&focus=2'])('ignores invalid focus %s', async (focus) => {
    const ctx = await setup(`/resources?focus=${focus}`)
    expect(ctx.load).not.toHaveBeenCalled()
    ctx.wrapper.unmount()
  })

  it('aborts an outstanding load when unmounted', async () => {
    const ctx = await setup()
    const signal = ctx.load.mock.calls[0]![1]
    ctx.wrapper.unmount()
    expect(signal.aborted).toBe(true)
    ctx.pending.resolve({ id: 42 })
    await flushPromises()
    expect(ctx.open).not.toHaveBeenCalled()
  })
})
