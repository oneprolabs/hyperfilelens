// @vitest-environment jsdom
import { mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { provideShellSidebar, useShellSidebar } from './useShellSidebar'

const observers: Array<{ notify: ResizeObserverCallback; observe: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> }> = []
let wrapper: VueWrapper | undefined

const Page = defineComponent({
  props: { width: { type: Number, default: 311 }, active: { type: Boolean, default: true } },
  setup(props) {
    const sidebar = ref<HTMLElement | null>(null)
    useShellSidebar(sidebar, () => props.active)
    return () => h('aside', { ref: sidebar, 'data-width': props.width, class: 'page-sidebar' })
  },
})

async function render() {
  const enabled = ref(true)
  const visible = ref(true)
  const active = ref(true)
  const width = ref(311)
  const fallbackVisible = ref(false)
  const Harness = defineComponent({
    setup() {
      const fallback = ref<HTMLElement | null>(null)
      const inset = provideShellSidebar(enabled, fallback)
      return () => h('div', [
        h('output', String(inset.value)),
        fallbackVisible.value ? h('aside', { ref: fallback, 'data-width': 203 }) : null,
        visible.value ? h(Page, { width: width.value, active: active.value }) : null,
      ])
    },
  })
  wrapper = mount(Harness)
  await nextTick()
  await nextTick()
  const readInset = () => Number(wrapper!.find('output').text())
  return { enabled, visible, active, width, fallbackVisible, readInset }
}

beforeEach(() => {
  observers.length = 0
  vi.stubGlobal('ResizeObserver', class {
    observe = vi.fn()
    disconnect = vi.fn()
    constructor(notify: ResizeObserverCallback) {
      observers.push({ notify, observe: this.observe, disconnect: this.disconnect })
    }
  })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const width = Number(this.dataset.width || 0)
    return { width, right: width, left: 0 } as DOMRect
  })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('shell context bar follows the rendered sidebar', () => {
  it('measures the actual sidebar rather than assuming an expanded width', async () => {
    const state = await render()
    expect(state.readInset()).toBe(311)
    expect(observers.at(-1)?.observe).toHaveBeenCalledWith(wrapper!.find('.page-sidebar').element)
  })

  it('follows collapse and restores full width when the sidebar is hidden', async () => {
    const state = await render()
    state.width.value = 56
    await nextTick()
    observers.at(-1)!.notify([], {} as ResizeObserver)
    await nextTick()
    expect(state.readInset()).toBe(56)
    state.width.value = 0
    await nextTick()
    observers.at(-1)!.notify([], {} as ResizeObserver)
    await nextTick()
    expect(state.readInset()).toBe(0)
  })

  it('responds to viewport changes even without ResizeObserver support', async () => {
    vi.stubGlobal('ResizeObserver', undefined)
    const state = await render()
    state.width.value = 0
    await nextTick()
    window.dispatchEvent(new Event('resize'))
    await nextTick()
    expect(state.readInset()).toBe(0)
  })

  it('disconnects tracking outside impersonation without changing the page sidebar', async () => {
    const state = await render()
    const observer = observers.at(-1)!
    state.enabled.value = false
    await nextTick()
    expect(state.readInset()).toBe(0)
    expect(observer.disconnect).toHaveBeenCalled()
    expect(wrapper!.find('.page-sidebar').exists()).toBe(true)
    state.enabled.value = true
    await nextTick()
    expect(state.readInset()).toBe(311)
  })

  it('uses the loading fallback and clears the inset on pages without a sidebar', async () => {
    const state = await render()
    state.fallbackVisible.value = true
    await nextTick()
    expect(state.readInset()).toBe(311)
    state.visible.value = false
    await nextTick()
    await nextTick()
    expect(state.readInset()).toBe(203)
    state.fallbackVisible.value = false
    await nextTick()
    expect(state.readInset()).toBe(0)
  })

  it('does not reserve sidebar space when a module has no menu items', async () => {
    const state = await render()
    state.active.value = false
    await nextTick()
    expect(state.readInset()).toBe(0)
    state.active.value = true
    await nextTick()
    expect(state.readInset()).toBe(311)
  })

  it('does not let a departing page unregister its replacement', async () => {
    const oldVisible = ref(true)
    const newVisible = ref(false)
    const Harness = defineComponent({
      setup() {
        const inset = provideShellSidebar(true, ref(null))
        return () => h('div', [
          h('output', String(inset.value)),
          oldVisible.value ? h(Page, { key: 'old', width: 311 }) : null,
          newVisible.value ? h(Page, { key: 'new', width: 287 }) : null,
        ])
      },
    })
    wrapper = mount(Harness)
    await nextTick()
    newVisible.value = true
    await nextTick()
    await nextTick()
    expect(wrapper.find('output').text()).toBe('287')
    oldVisible.value = false
    await nextTick()
    expect(wrapper.find('output').text()).toBe('287')
  })

  it('removes observers and its resize listener on shell teardown', async () => {
    const removeListener = vi.spyOn(window, 'removeEventListener')
    await render()
    const observer = observers.at(-1)!
    wrapper!.unmount()
    wrapper = undefined
    expect(observer.disconnect).toHaveBeenCalled()
    expect(removeListener).toHaveBeenCalledWith('resize', expect.any(Function))
  })

  it('leaves standalone and Admin Console module pages independent of the tenant shell', async () => {
    wrapper = mount(Page)
    await nextTick()
    expect(wrapper.find('.page-sidebar').exists()).toBe(true)
    expect(observers).toHaveLength(0)
  })
})
