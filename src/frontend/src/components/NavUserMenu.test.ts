// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { defineComponent } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../locales/en'
import NavUserMenu from './NavUserMenu.vue'

const mocks = vi.hoisted(() => ({
  fetchDeployProfile: vi.fn(),
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.push }),
}))

vi.mock('../composables/useAuth', async () => {
  const { ref } = await import('vue')
  return {
    useAuth: () => ({
      user: ref({
        username: 'owner',
        email: 'owner@example.test',
        access_profile: { role: 'owner' },
      }),
    }),
  }
})

vi.mock('../composables/useTheme', async () => {
  const { ref } = await import('vue')
  return { useTheme: () => ({ theme: ref('light') }) }
})

vi.mock('../composables/useDeployProfile', () => ({
  fetchDeployProfile: mocks.fetchDeployProfile,
}))

const HflPopoverStub = defineComponent({
  methods: { hide() {} },
  template: `
    <div>
      <slot name="reference" />
      <slot />
    </div>
  `,
})

function mountMenu() {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  return mount(NavUserMenu, {
    global: {
      plugins: [i18n],
      stubs: { HflPopover: HflPopoverStub },
    },
  })
}

describe('NavUserMenu product identity', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the public Release version and Enterprise edition', async () => {
    mocks.fetchDeployProfile.mockResolvedValue({
      product_version: '0.2.1',
      edition: 'enterprise',
    })

    const wrapper = mountMenu()
    await flushPromises()

    expect(wrapper.get('.nav-user-product__name').text()).toBe('HyperFileLens v0.2.1')
    expect(wrapper.get('.nav-user-product__meta').text()).toBe('Enterprise')
  })

  it('uses a localized development label when the valid profile has no version', async () => {
    mocks.fetchDeployProfile.mockResolvedValue({
      product_version: null,
      edition: 'community',
    })

    const wrapper = mountMenu()
    await flushPromises()

    expect(wrapper.get('.nav-user-product__name').text()).toBe('HyperFileLens')
    expect(wrapper.get('.nav-user-product__meta').text()).toBe(
      'Development build · Community',
    )
  })

  it('does not mislabel a production instance when the profile request fails', async () => {
    mocks.fetchDeployProfile.mockResolvedValue(null)

    const wrapper = mountMenu()
    await flushPromises()

    expect(wrapper.find('.nav-user-product').exists()).toBe(false)
  })

  it('keeps the footer hidden for a legacy profile without identity fields', async () => {
    mocks.fetchDeployProfile.mockResolvedValue({ site_role: 'tenant' })

    const wrapper = mountMenu()
    await flushPromises()

    expect(wrapper.find('.nav-user-product').exists()).toBe(false)
  })
})
