// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../../locales/en'
import AccountProfile from './AccountProfile.vue'
const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  replace: vi.fn(),
  clearAuth: vi.fn(),
  userTimezone: undefined as string | undefined,
}))
vi.mock('../../lib/api', () => ({ api: mocks.api, apiErrorMessage: () => 'Error' }))
vi.mock('vue-router', () => ({ useRouter: () => ({ replace: mocks.replace }) }))
vi.mock('../../composables/useAuth', () => ({
  useAuth: () => ({
    user: ref({ id: 1, email: 'account@example.com', timezone: mocks.userTimezone }),
    clearAuth: mocks.clearAuth,
  }),
}))
function render() {
  return mount(AccountProfile, { global: {
    plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
    stubs: {
      ResetPasswordCard: { name: 'ResetPasswordCard', props: { initialEmail: String, lockEmail: Boolean, embedded: Boolean }, template: '<div data-test="recovery" />' },
      ElButton: { template: '<button><slot /></button>' },
      ElForm: { template: '<form><slot /></form>' }, ElInput: true,
    },
  } })
}
beforeEach(() => {
  vi.clearAllMocks()
  mocks.userTimezone = undefined
})
describe('account timestamp display', () => {
  it.each([
    { profileTimezone: 'UTC', authTimezone: 'UTC' },
    { profileTimezone: 'Pacific/Honolulu', authTimezone: 'UTC' },
    { profileTimezone: undefined, authTimezone: 'Pacific/Honolulu' },
    { profileTimezone: undefined, authTimezone: undefined },
  ])('uses browser time instead of hidden timezone $profileTimezone / $authTimezone', async ({
    profileTimezone,
    authTimezone,
  }) => {
    mocks.userTimezone = authTimezone
    // Start with browser-local dates, then emulate the API's UTC timestamps.
    // Run this suite in separate TZ processes, including winter/summer in New York.
    mocks.api.mockResolvedValue({
      has_usable_password: true,
      timezone: profileTimezone,
      registered_at: new Date(2026, 0, 15, 15, 54, 43).toISOString(),
      security_audit: {
        last_login_at: new Date(2026, 6, 15, 15, 57, 32).toISOString(),
      },
    })
    const wrapper = render()
    try {
      await flushPromises()
      expect(wrapper.text()).toContain('2026-01-15 15:54:43')
      expect(wrapper.text()).toContain('2026-07-15 15:57:32')
      expect(wrapper.text()).not.toContain(en.account.passwordDetailsFailed)
    } finally {
      wrapper.unmount()
    }
  })

  it('accepts explicit offsets and camel-case account fields', async () => {
    const registeredAt = new Date(2026, 9, 16, 15, 54, 43).toISOString().replace('Z', '+00:00')
    const lastLoginAt = new Date(2026, 9, 27, 13, 57, 32).toISOString().replace('Z', '+00:00')
    mocks.api.mockResolvedValue({
      has_usable_password: true,
      timezone: 'UTC',
      registeredAt,
      securityAudit: { lastLoginAt },
    })
    const wrapper = render()
    try {
      await flushPromises()
      expect(wrapper.text()).toContain('2026-10-16 15:54:43')
      expect(wrapper.text()).toContain('2026-10-27 13:57:32')
    } finally {
      wrapper.unmount()
    }
  })

  it('keeps empty timestamp placeholders without failing the account request', async () => {
    mocks.api.mockResolvedValue({
      has_usable_password: true,
      registered_at: null,
      security_audit: { last_login_at: null },
    })
    const wrapper = render()
    try {
      await flushPromises()
      expect(wrapper.text()).toContain(en.account.emptyValue)
      expect(wrapper.find('form').exists()).toBe(true)
      expect(wrapper.text()).not.toContain('Invalid Date')
      expect(wrapper.text()).not.toContain(en.account.passwordDetailsFailed)
    } finally {
      wrapper.unmount()
    }
  })
})

describe('account password settings', () => {
  it('shows loading until password state is known', async () => {
    let resolve!: (value: unknown) => void
    mocks.api.mockReturnValue(new Promise(r => { resolve = r }))
    const wrapper = render()
    expect(wrapper.text()).toContain(en.account.passwordDetailsLoading)
    expect(wrapper.find('form').exists()).toBe(false)
    resolve({ has_usable_password: false, password_reset_available: true })
    await flushPromises()
    expect(wrapper.text()).not.toContain(en.account.passwordDetailsLoading)
    wrapper.unmount()
  })
  it('offers email verification instead of current password for passwordless users', async () => {
    mocks.api.mockResolvedValue({ has_usable_password: false, password_reset_available: true })
    const wrapper = render()
    await flushPromises()
    expect(wrapper.text()).toContain(en.account.setPassword)
    expect(wrapper.find('form').exists()).toBe(false)
    const card = wrapper.findComponent({ name: 'ResetPasswordCard' })
    expect(card.props('initialEmail')).toBe('account@example.com')
    expect(card.props('lockEmail')).toBe(true)
    card.vm.$emit('password-reset')
    await flushPromises()
    expect(mocks.clearAuth).toHaveBeenCalledOnce()
    expect(mocks.replace).toHaveBeenCalledWith('/login')
    wrapper.unmount()
  })
  it('keeps current password validation and offers recovery for existing passwords', async () => {
    mocks.api.mockResolvedValue({ has_usable_password: true, password_reset_available: true })
    const wrapper = render()
    await flushPromises()
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.text()).toContain(en.account.fieldCurrentPassword)
    const button = wrapper.findAll('button').find(b => b.text() === en.account.passwordRecoveryAction)!
    await button.trigger('click')
    expect(wrapper.find('form').exists()).toBe(false)
    expect(wrapper.find('[data-test="recovery"]').exists()).toBe(true)
    wrapper.unmount()
  })
  it('fails closed when email recovery is unavailable', async () => {
    mocks.api.mockResolvedValue({ has_usable_password: false, password_reset_available: false })
    const wrapper = render()
    await flushPromises()
    expect(wrapper.text()).toContain(en.account.passwordRecoveryUnavailable)
    expect(wrapper.find('[data-test="recovery"]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('shows a retryable error instead of assuming password state', async () => {
    mocks.api.mockRejectedValue(new Error('offline'))
    const wrapper = render()
    await flushPromises()
    expect(wrapper.text()).toContain(en.account.passwordDetailsFailed)
    mocks.api.mockResolvedValue({ has_usable_password: true, password_reset_available: false })
    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(wrapper.find('form').exists()).toBe(true)
    wrapper.unmount()
  })
})
