// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import { en } from '../../../locales/en'
import PersonalSettings from './PersonalSettings.vue'

vi.mock('../../composables/useResolvedPlatformOpsSideNav', () => ({
  useResolvedPlatformOpsSideNav: () => [{
    label: 'Platform',
    children: [{
      label: 'Personal Settings',
      pageTitle: 'Personal Settings',
      to: '/platform-ops/account/profile',
    }],
  }],
}))

describe('Admin Console Personal Settings', () => {
  it('reuses the personal profile content with platform navigation and a single page heading', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/platform-ops/account/profile', component: PersonalSettings }],
    })
    await router.push('/platform-ops/account/profile')
    const wrapper = mount(PersonalSettings, {
      global: {
        plugins: [router, createI18n({ legacy: false, locale: 'en', messages: { en } })],
        stubs: { AccountProfile: { name: 'AccountProfile', props: ['showOrganizationRole'], template: '<div data-testid="shared-profile" />' } },
      },
    })
    await flushPromises()
    expect(wrapper.findAll('h1').map(heading => heading.text())).toEqual(['Personal Settings'])
    expect(wrapper.find('[data-testid="shared-profile"]').exists()).toBe(true)
    expect(wrapper.getComponent({ name: 'AccountProfile' }).props('showOrganizationRole')).toBe(false)
    expect(wrapper.find('a[href="/platform-ops/account/profile"]').classes()).toContain('active')
    expect(wrapper.find('a[href="/account/profile"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Organization Management')
    wrapper.unmount()
  })
})
