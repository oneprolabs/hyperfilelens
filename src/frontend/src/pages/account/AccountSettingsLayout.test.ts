// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAppPrimaryNav } from '../../composables/useAppPrimaryNav'
import { en } from '../../locales/en'
import AccountSettingsLayout from './AccountSettingsLayout.vue'

const mocks = vi.hoisted(() => ({ allowed: false }))
vi.mock('../../composables/useTenantPermissions', () => ({
  useTenantPermissions: () => ({ has: () => mocks.allowed }),
}))

async function render(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account', component: AccountSettingsLayout, children: [
        { path: 'profile', component: { template: '<div>Profile content</div>' } },
        { path: 'notifications', component: { template: '<div>Notification content</div>' } },
      ] },
      { path: '/node/subscription', component: { template: '<div>Usage content</div>' } },
      { path: '/', component: { template: '<div>Dashboard</div>' } },
    ],
  })
  await router.push(path)
  let nav!: ReturnType<typeof useAppPrimaryNav>
  const wrapper = mount(defineComponent({
    setup() {
      nav = useAppPrimaryNav()
      return () => h(RouterView)
    },
  }), {
    global: { plugins: [router, createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
  await flushPromises()
  return { wrapper, router, nav }
}

describe('Account pages within Configuration', () => {
  afterEach(() => { mocks.allowed = false })

  it.each([
    ['/account/profile', 'Personal Settings', 'Profile content'],
    ['/account/notifications', 'Notifications', 'Notification content'],
  ])('uses the Account sidebar and Configuration highlight on %s without tenant grants', async (path, title, content) => {
    const { wrapper, nav } = await render(path)
    expect(wrapper.find('h1').text()).toBe(title)
    expect(wrapper.text()).toContain(content)
    expect(wrapper.text()).toContain('Account')
    expect(wrapper.text()).not.toContain('Organization Management')
    expect(wrapper.find(`a[href="${path}"]`).classes()).toContain('active')
    expect(nav.itemsWithActiveState.value.find(item => item.to === '/node')?.active).toBe(true)
    expect(nav.itemsWithActiveState.value.filter(item => item.active)).toHaveLength(1)
    wrapper.unmount()
  })

  it('includes authorized organization pages alongside Account and switches between personal pages', async () => {
    mocks.allowed = true
    const { wrapper, router } = await render('/account/profile')
    expect(wrapper.text()).toContain('Organization Management')
    expect(wrapper.find('a[href="/node/subscription"]').exists()).toBe(true)
    await wrapper.get('a[href="/account/notifications"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/account/notifications')
    expect(wrapper.find('h1').text()).toBe('Notifications')
    expect(wrapper.text()).toContain('Notification content')
    wrapper.unmount()
  })
})
