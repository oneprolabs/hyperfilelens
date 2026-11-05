// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Sidebar from './Sidebar.vue'

const profilePath = '/platform-ops/account/profile'
const menus = [
  { label: 'Monitoring', children: [{ label: 'Tasks', to: '/platform-ops/monitoring/tasks' }] },
  { label: 'Platform', children: [{ label: 'Personal Settings', to: profilePath }] },
]
const mounted: ReturnType<typeof mount>[] = []

function rect(top: number, height: number): DOMRect {
  return { top, bottom: top + height, left: 0, right: 200, width: 200, height, x: 0, y: top, toJSON: () => ({}) }
}

async function render(path = profilePath, initialMenus = menus, collapsed = false) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
  })
  await router.push(path)
  const wrapper = mount(Sidebar, {
    props: { menus: initialMenus, collapsed },
    global: { plugins: [router] },
  })
  mounted.push(wrapper)
  await flushPromises()
  return { wrapper, router, nav: () => wrapper.get('.sidebar-nav').element as HTMLElement }
}

describe('Admin Console active sidebar visibility', () => {
  beforeEach(() => {
    sessionStorage.clear()
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains('sidebar-nav')) return rect(100, 400)
      if (this.matches('a.active')) {
        const position = this.getAttribute('href') === profilePath ? 900 : 0
        const nav = this.closest('.sidebar-nav') as HTMLElement
        return rect(100 + position - nav.scrollTop, 36)
      }
      return rect(0, 0)
    })
  })

  afterEach(() => {
    mounted.splice(0).forEach(wrapper => wrapper.unmount())
    vi.restoreAllMocks()
    sessionStorage.clear()
  })

  it('reveals Personal Settings below the fold on a direct visit', async () => {
    const { wrapper, nav } = await render()
    expect(nav().scrollTop).toBe(536)
    expect(wrapper.get(`a[href="${profilePath}"]`).classes()).toContain('active')
  })

  it('preserves restored sidebar scroll when the selected item is already visible', async () => {
    sessionStorage.setItem('hfl-sidebar-scroll:platform-ops', '650')
    const { nav } = await render()
    expect(nav().scrollTop).toBe(650)
  })

  it('expands the selected group and reveals its item when navigating from another page', async () => {
    const { wrapper, router, nav } = await render('/platform-ops/monitoring/tasks')
    await wrapper.findAll('.menu-parent')[1].trigger('click')
    expect(wrapper.find(`a[href="${profilePath}"]`).exists()).toBe(false)
    await router.push(profilePath)
    await flushPromises()
    expect(wrapper.get(`a[href="${profilePath}"]`).classes()).toContain('active')
    expect(nav().scrollTop).toBe(536)
    await router.push('/platform-ops/monitoring/tasks')
    await flushPromises()
    expect(nav().scrollTop).toBe(0)
  })

  it('reveals the active item after capability-filtered menus load asynchronously', async () => {
    const { wrapper, nav } = await render(profilePath, [])
    await wrapper.setProps({ menus })
    await flushPromises()
    expect(wrapper.find(`a[href="${profilePath}"]`).exists()).toBe(true)
    expect(nav().scrollTop).toBe(536)
  })

  it('keeps the active icon visible in collapsed mode and after expanding', async () => {
    const { wrapper, nav } = await render(profilePath, menus, true)
    expect(nav().scrollTop).toBe(536)
    await wrapper.setProps({ collapsed: false })
    await flushPromises()
    expect(nav().scrollTop).toBe(536)
  })

  it('does not change tenant sidebar scrolling', async () => {
    const { nav } = await render('/account/profile', [
      { label: 'Account', children: [{ label: 'Personal Settings', to: '/account/profile' }] },
    ])
    expect(nav().scrollTop).toBe(0)
  })
})
