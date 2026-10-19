// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { defineComponent } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../locales/en'
import NavUserMenu from './NavUserMenu.vue'
import navUserMenuSource from './NavUserMenu.vue?raw'

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
  emits: ['show'],
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

    expect(wrapper.get('.nav-user-product__name').text()).toBe(
      'HyperFileLens v0.2.1 · Enterprise',
    )
    expect(wrapper.get('.nav-user-menu__email').text()).toBe('owner@example.test')
    expect(wrapper.get('.nav-user-menu__role').text()).toBe('Owner')
  })

  it('uses a localized development label when the valid profile has no version', async () => {
    mocks.fetchDeployProfile.mockResolvedValue({
      product_version: null,
      edition: 'community',
    })

    const wrapper = mountMenu()
    await flushPromises()

    expect(wrapper.get('.nav-user-product__name').text()).toBe(
      'HyperFileLens · Development build · Community',
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

describe('NavUserMenu hover behavior', () => {
  it('does not add native title tooltips to the user trigger or dropdown', async () => {
    mocks.fetchDeployProfile.mockResolvedValue({
      product_version: '0.3.0',
      edition: 'enterprise',
    })

    const wrapper = mountMenu()
    await flushPromises()

    expect(wrapper.get('.nav-user-trigger').attributes('title')).toBeUndefined()
    expect(wrapper.get('.nav-user-menu__email').attributes('title')).toBeUndefined()
    expect(wrapper.find('.nav-dropdown-panel [title]').exists()).toBe(false)
  })
})

describe('NavUserMenu Product Website link', () => {
  it('opens the configured website in a new tab without a native tooltip', async () => {
    mocks.fetchDeployProfile.mockResolvedValue({
      product_version: '0.3.8',
      edition: 'enterprise',
      website_public_url: 'https://hyperfilelens.com/',
    })
    const wrapper = mountMenu()
    await flushPromises()
    const link = wrapper.get('a.nav-user-website')
    expect(link.text()).toBe('Product Website')
    expect(link.attributes('href')).toBe('https://hyperfilelens.com/')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toBe('noopener noreferrer')
    expect(link.attributes('title')).toBeUndefined()
    expect(wrapper.find('.nav-dropdown-panel [title]').exists()).toBe(false)
  })

  it('keeps the link independent of product-version metadata', async () => {
    mocks.fetchDeployProfile.mockResolvedValue({
      website_public_url: 'https://192.168.8.69:11442',
    })
    const wrapper = mountMenu()
    await flushPromises()
    expect(wrapper.get('a.nav-user-website').attributes('href')).toBe('https://192.168.8.69:11442/')
    expect(wrapper.find('.nav-user-product').exists()).toBe(false)
  })

  it.each([undefined, '', 'javascript:alert(1)', 'data:text/html,test', '//evil.example', 'https://user:password@example.com/'])(
    'does not render an unsafe or missing website URL: %s',
    async (website) => {
      mocks.fetchDeployProfile.mockResolvedValue({ website_public_url: website })
      const wrapper = mountMenu()
      await flushPromises()
      expect(wrapper.find('a.nav-user-website').exists()).toBe(false)
    },
  )
})

describe('NavUserMenu website refresh', () => {
  beforeEach(() => mocks.fetchDeployProfile.mockReset())

  it('updates a mounted menu after saving or restoring the website setting', async () => {
    mocks.fetchDeployProfile.mockResolvedValue({ website_public_url: 'https://old.example/' })
    const wrapper = mountMenu()
    await flushPromises()
    try {
      expect(wrapper.get('a.nav-user-website').attributes('href')).toBe('https://old.example/')
      mocks.fetchDeployProfile.mockResolvedValue({ website_public_url: 'https://new.example/' })
      wrapper.getComponent(HflPopoverStub).vm.$emit('show')
      await flushPromises()
      expect(mocks.fetchDeployProfile).toHaveBeenLastCalledWith(true)
      expect(wrapper.get('a.nav-user-website').attributes('href')).toBe('https://new.example/')
      mocks.fetchDeployProfile.mockResolvedValue({ website_public_url: 'https://192.168.8.69:11442/' })
      wrapper.getComponent(HflPopoverStub).vm.$emit('show')
      await flushPromises()
      expect(wrapper.get('a.nav-user-website').attributes('href')).toBe('https://192.168.8.69:11442/')
    } finally { wrapper.unmount() }
  })

  it.each([undefined, '', 'javascript:alert(1)'])(
    'removes an old link when the refreshed website is missing or unsafe: %s',
    async website => {
      mocks.fetchDeployProfile.mockResolvedValue({ website_public_url: 'https://old.example/' })
      const wrapper = mountMenu()
      await flushPromises()
      try {
        expect(wrapper.find('a.nav-user-website').exists()).toBe(true)
        mocks.fetchDeployProfile.mockResolvedValue({ website_public_url: website })
        wrapper.getComponent(HflPopoverStub).vm.$emit('show')
        await flushPromises()
        expect(wrapper.find('a.nav-user-website').exists()).toBe(false)
      } finally { wrapper.unmount() }
    },
  )

  it('does not overwrite a fresh website with a slower initial request', async () => {
    let resolveInitial!: (value: { website_public_url: string }) => void
    const initial = new Promise<{ website_public_url: string }>(resolve => { resolveInitial = resolve })
    mocks.fetchDeployProfile.mockReturnValueOnce(initial)
    const wrapper = mountMenu()
    try {
      mocks.fetchDeployProfile.mockResolvedValueOnce({ website_public_url: 'https://new.example/' })
      wrapper.getComponent(HflPopoverStub).vm.$emit('show')
      await flushPromises()
      expect(wrapper.get('a.nav-user-website').attributes('href')).toBe('https://new.example/')
      resolveInitial({ website_public_url: 'https://old.example/' })
      await flushPromises()
      expect(wrapper.get('a.nav-user-website').attributes('href')).toBe('https://new.example/')
    } finally { wrapper.unmount() }
  })
})

describe('NavUserMenu responsive trigger', () => {
  it('uses a 360px dropdown width', async () => {
    mocks.fetchDeployProfile.mockResolvedValue(null)

    const wrapper = mountMenu()
    await flushPromises()

    expect(wrapper.findComponent(HflPopoverStub).attributes('width')).toBe('360')
  })

  it('allows the trigger and label to shrink without clipping the page', () => {
    expect(navUserMenuSource).toMatch(
      /\.nav-user-trigger\s*{[\s\S]*?min-width:\s*0[\s\S]*?max-width:\s*100%[\s\S]*?flex:\s*0 1 auto/,
    )
    expect(navUserMenuSource).toMatch(
      /\.nav-user-trigger__label\s*{[\s\S]*?min-width:\s*0[\s\S]*?max-width:\s*140px[\s\S]*?flex:\s*1 1 auto/,
    )
  })
})
