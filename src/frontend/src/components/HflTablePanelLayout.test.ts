// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { nextTick, defineComponent, h, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import HflTablePanel from './HflTablePanel.vue'
import { useListTableLayout } from '../composables/useListTableLayout'

let resizeCallback: ResizeObserverCallback
const observe = vi.fn()
const unobserve = vi.fn()
const disconnect = vi.fn()

function setup() {
  observe.mockClear()
  unobserve.mockClear()
  disconnect.mockClear()
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { resizeCallback = callback }
    observe = observe
    unobserve = unobserve
    disconnect = disconnect
  })
  vi.stubGlobal('requestAnimationFrame', () => 1)
  const page = document.createElement('div')
  page.className = 'page-body'
  page.style.paddingBottom = '16px'
  page.style.borderBottom = '1px solid'
  document.body.append(page)
  let footerHeight = 48
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (this.classList.contains('page-body')) return { top: 100, bottom: 700, height: 600 } as DOMRect
    if (this.classList.contains('hfl-list-footer')) return { top: 600, bottom: 600 + footerHeight, height: footerHeight } as DOMRect
    return { top: 250, bottom: 600, height: 350 } as DOMRect
  })
  return { page, wrapFooter: () => { footerHeight = 92 } }
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('shared list layouts track footer size', () => {
  it('observes pagination and updates the panel slot height when it wraps', async () => {
    const { page, wrapFooter } = setup()
    const wrapper = mount(HflTablePanel, {
      attachTo: page,
      props: { fill: true },
      slots: {
        table: ({ tableMaxHeight }: { tableMaxHeight: number }) => h('span', { class: 'height-value' }, String(tableMaxHeight)),
        footer: () => h('span', 'Pagination'),
      },
    })
    await nextTick()
    await nextTick()
    expect(wrapper.find('.height-value').text()).toBe('385')
    expect(observe).toHaveBeenCalledWith(wrapper.find('.hfl-list-footer').element)
    wrapFooter()
    resizeCallback([], {} as ResizeObserver)
    await nextTick()
    expect(wrapper.find('.height-value').text()).toBe('341')
    wrapper.unmount()
    expect(disconnect).toHaveBeenCalledOnce()
  })

  it('updates the composable height without changing individual page logic', async () => {
    const { page, wrapFooter } = setup()
    const Harness = defineComponent({
      setup() {
        const block = ref<HTMLElement | null>(null)
        const { tableMaxHeight, updateTableMaxHeight } = useListTableLayout(ref(null), block)
        return { block, tableMaxHeight, updateTableMaxHeight }
      },
      template: '<div class="hfl-list-panel"><div ref="block">{{ tableMaxHeight }}</div><div class="hfl-list-footer">Pagination</div></div>',
    })
    const wrapper = mount(Harness, { attachTo: page })
    wrapper.vm.updateTableMaxHeight()
    await nextTick()
    expect(wrapper.text()).toContain('385')
    expect(observe).toHaveBeenCalledWith(wrapper.find('.hfl-list-footer').element)
    wrapFooter()
    resizeCallback([], {} as ResizeObserver)
    await nextTick()
    expect(wrapper.text()).toContain('341')
    wrapper.unmount()
    expect(disconnect).toHaveBeenCalledOnce()
  })
})
