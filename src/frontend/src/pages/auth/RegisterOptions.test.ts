// @vitest-environment jsdom

import { ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../../locales/en'
import Register from './Register.vue'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  replace: vi.fn(),
  push: vi.fn(),
  fetchDeployProfile: vi.fn(),
  loadTurnstileConfig: vi.fn(),
}))

vi.mock('../../lib/api', () => ({ api: mocks.api }))
vi.mock('../../lib/analytics', () => ({ trackAppEvent: vi.fn() }))
vi.mock('../../lib/appConfig', () => ({ appConfig: { showEula: false } }))
vi.mock('../../components/LanguageSwitcher.vue', () => ({
  default: { template: '<span />' },
}))
vi.mock('../../composables/useDeployProfile', () => ({
  fetchDeployProfile: mocks.fetchDeployProfile,
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: mocks.replace, push: mocks.push }),
}))
vi.mock('../../composables/useTurnstileConfig', () => ({
  useTurnstileConfig: () => ({
    turnstileSiteKey: ref(''),
    isTurnstilePending: ref(false),
    isTurnstileReady: ref(false),
    isTurnstileBlocked: ref(false),
    authTurnstileMountGeneration: ref(0),
    loadTurnstileConfig: mocks.loadTurnstileConfig,
    retryTurnstileConfig: vi.fn(),
    buildTurnstilePayload: () => ({}),
    blockTurnstile: vi.fn(),
  }),
}))

function mountRegister() {
  return mount(Register, {
    global: {
      plugins: [
        ElementPlus,
        createI18n({
          legacy: false,
          locale: 'en',
          messages: { en },
        }),
      ],
      stubs: {
        LanguageSwitcher: true,
        AuthBackdrop: true,
        AuthBrandPanel: true,
        AuthTurnstileField: true,
      },
    },
  })
}

describe('registration options response', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    localStorage.clear()
    mocks.fetchDeployProfile.mockResolvedValue({ email_signup_enabled: true })
    mocks.loadTurnstileConfig.mockResolvedValue(undefined)
  })

  afterEach(() => {
    localStorage.clear()
  })

  it.each([true, false])(
    'opens Enterprise registration without Personal registration (envelope: %s)',
    async (enveloped) => {
      const options = {
        enabled: true,
        personal: { enabled: false, available: false },
        enterprise: { enabled: true },
      }
      mocks.api.mockResolvedValue(enveloped
        ? { code: 0, message: 'success', data: options }
        : options)
      const wrapper = mountRegister()
      try {
        await flushPromises()
        expect(mocks.replace).not.toHaveBeenCalled()
        expect(mocks.loadTurnstileConfig).toHaveBeenCalledOnce()
        const choices = wrapper.findAll('[role="radio"]')
        expect(choices).toHaveLength(0)
        expect(wrapper.find('.registration-single-label').exists()).toBe(false)
        expect(wrapper.find('.registration-description').exists()).toBe(false)
        expect(wrapper.find('input[autocomplete="organization"]').exists()).toBe(true)
        expect(wrapper.get('button.submit-btn').text()).toBe(en.register.createOrganization)
      } finally {
        wrapper.unmount()
      }
    },
  )

  it('selects Personal when only Personal registration is available', async () => {
    mocks.api.mockResolvedValue({
      code: 0,
      message: 'success',
      data: {
        enabled: true,
        personal: { enabled: true, available: true },
        enterprise: { enabled: false },
      },
    })
    const wrapper = mountRegister()
    try {
      await flushPromises()
      expect(mocks.replace).not.toHaveBeenCalled()
      const choices = wrapper.findAll('[role="radio"]')
      expect(choices).toHaveLength(0)
      expect(wrapper.find('.registration-single-label').exists()).toBe(false)
      expect(wrapper.find('.registration-description').exists()).toBe(false)
      expect(wrapper.find('input[autocomplete="organization"]').exists()).toBe(false)
      expect(wrapper.get('button.submit-btn').text()).toBe(en.register.createPersonalAccount)
    } finally {
      wrapper.unmount()
    }
  })

  it('keeps descriptions outside the mode labels and switches the matching form', async () => {
    mocks.api.mockResolvedValue({
      code: 0,
      data: {
        personal: { enabled: true, available: true },
        enterprise: { enabled: true },
      },
    })
    const wrapper = mountRegister()
    try {
      await flushPromises()
      const choices = wrapper.findAll('[role="radio"]')
      expect(choices.map(choice => choice.text())).toEqual([
        en.register.personal, en.register.enterprise,
      ])
      expect(wrapper.get('.registration-description').text()).toBe(en.register.personalDescription)
      expect(choices[0].attributes('tabindex')).toBe('0')
      expect(choices[1].attributes('tabindex')).toBe('-1')
      await choices[1].trigger('click')
      expect(choices[1].attributes('aria-checked')).toBe('true')
      expect(wrapper.get('.registration-description').text()).toBe(en.register.enterpriseDescription)
      expect(wrapper.find('input[autocomplete="organization"]').exists()).toBe(true)
      await choices[1].trigger('keydown', { key: 'Home' })
      expect(choices[0].attributes('aria-checked')).toBe('true')
      expect(wrapper.get('.registration-description').text()).toBe(en.register.personalDescription)
      expect(wrapper.find('input[autocomplete="organization"]').exists()).toBe(false)
      await choices[0].trigger('keydown', { key: 'ArrowRight' })
      expect(choices[1].attributes('aria-checked')).toBe('true')
      await choices[1].trigger('keydown', { key: 'ArrowLeft' })
      expect(choices[0].attributes('aria-checked')).toBe('true')
      await choices[0].trigger('keydown', { key: 'End' })
      expect(choices[1].attributes('aria-checked')).toBe('true')
    } finally {
      wrapper.unmount()
    }
  })

  it('omits Personal when enabled but unavailable in this installation', async () => {
    mocks.api.mockResolvedValue({
      code: 0,
      data: {
        personal: { enabled: true, available: false },
        enterprise: { enabled: true },
      },
    })
    const wrapper = mountRegister()
    try {
      await flushPromises()
      expect(mocks.replace).not.toHaveBeenCalled()
      expect(mocks.loadTurnstileConfig).toHaveBeenCalledOnce()
      expect(wrapper.find('[role="radio"]').exists()).toBe(false)
      expect(wrapper.find('.registration-single-label').exists()).toBe(false)
      expect(wrapper.find('.registration-description').exists()).toBe(false)
      expect(wrapper.find('input[autocomplete="organization"]').exists()).toBe(true)
    } finally {
      wrapper.unmount()
    }
  })

  it('does not show a registration form while availability is loading', async () => {
    mocks.api.mockImplementation(() => new Promise(() => {}))
    const wrapper = mountRegister()
    try {
      await flushPromises()
      expect(wrapper.get('[role="status"]').text()).toBe(en.common.loading)
      expect(wrapper.find('input').exists()).toBe(false)
      expect(wrapper.find('button.submit-btn').exists()).toBe(false)
      expect(mocks.loadTurnstileConfig).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })

  it('shows a safe unavailable state instead of bouncing Personal-only users to Sign In', async () => {
    mocks.api.mockResolvedValue({
      personal: { enabled: true, available: false, reason: 'managed_storage_unavailable' },
      enterprise: { enabled: false },
    })
    const wrapper = mountRegister()
    try {
      await flushPromises()
      expect(mocks.replace).not.toHaveBeenCalled()
      expect(wrapper.get('[role="status"]').text()).toContain(en.register.unavailableTitle)
      expect(wrapper.text()).toContain(en.register.unavailableDescription)
      expect(wrapper.text()).not.toContain('managed_storage')
      expect(wrapper.find('input').exists()).toBe(false)
      expect(wrapper.find('.send-code-btn').exists()).toBe(false)
      expect(wrapper.get('button.submit-btn').text()).toBe(en.common.retry)
      expect(mocks.loadTurnstileConfig).not.toHaveBeenCalled()
      await wrapper.get('a[href="/login"]').trigger('click')
      expect(mocks.push).toHaveBeenCalledWith('/login')
    } finally {
      wrapper.unmount()
    }
  })

  it('rechecks readiness on Retry and opens Personal registration once storage is configured', async () => {
    mocks.api.mockResolvedValueOnce({
      personal: { enabled: true, available: false }, enterprise: { enabled: false },
    }).mockResolvedValueOnce({
      personal: { enabled: true, available: true }, enterprise: { enabled: false },
    })
    const wrapper = mountRegister()
    try {
      await flushPromises()
      await wrapper.get('button.submit-btn').trigger('click')
      await flushPromises()
      expect(mocks.replace).not.toHaveBeenCalled()
      expect(mocks.api).toHaveBeenCalledTimes(2)
      expect(mocks.loadTurnstileConfig).toHaveBeenCalledOnce()
      expect(wrapper.find('.registration-unavailable').exists()).toBe(false)
      expect(wrapper.get('button.submit-btn').text()).toBe(en.register.createPersonalAccount)
      expect(wrapper.find('input[autocomplete="email"]').exists()).toBe(true)
      expect(wrapper.find('[role="radio"]').exists()).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })

  it('handles availability API failures without exposing errors or enabling the form', async () => {
    mocks.api.mockRejectedValue(new Error('internal storage configuration detail'))
    const wrapper = mountRegister()
    try {
      await flushPromises()
      expect(mocks.replace).not.toHaveBeenCalled()
      expect(wrapper.get('[role="status"]').text()).toContain(en.register.unavailableTitle)
      expect(wrapper.text()).not.toContain('internal storage')
      expect(wrapper.find('input').exists()).toBe(false)
      expect(mocks.loadTurnstileConfig).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })

  it('uses sentence-case placeholders and natural keyboard order', async () => {
    mocks.api.mockResolvedValue({
      code: 0,
      data: {
        personal: { enabled: false, available: false },
        enterprise: { enabled: true },
      },
    })
    const wrapper = mountRegister()
    try {
      await flushPromises()
      expect(wrapper.get('.register-box-title__copy').text()).toBe('Create Your Account')
      expect(wrapper.findAll('input').map(input => input.attributes('aria-label'))).toEqual([
        'Organization name', 'Email address', '6-digit verification code', 'Create password',
      ])
      expect(wrapper.findAll('input').every(input => input.attributes('tabindex') === undefined)).toBe(true)
      expect(wrapper.get('input[type="email"]').attributes('autocomplete')).toBe('email')
      expect(wrapper.get('input[autocomplete="one-time-code"]').attributes('inputmode')).toBe('numeric')
      expect(wrapper.get('.send-code-btn').text()).toBe('Send Code')
      expect(wrapper.get('button.submit-btn').text()).toBe('Create Organization')
    } finally {
      wrapper.unmount()
    }
  })

  it('keeps registration closed when neither mode is enabled', async () => {
    mocks.api.mockResolvedValue({
      code: 0,
      message: 'success',
      data: {
        enabled: false,
        personal: { enabled: false, available: false },
        enterprise: { enabled: false },
      },
    })
    const wrapper = mountRegister()
    try {
      await flushPromises()
      expect(mocks.replace).toHaveBeenCalledOnce()
      expect(mocks.loadTurnstileConfig).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })
})
