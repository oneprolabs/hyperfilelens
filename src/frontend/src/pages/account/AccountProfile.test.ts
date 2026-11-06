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
function render(showOrganizationRole = true) {
  return mount(AccountProfile, { props: { showOrganizationRole }, global: {
    plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
    stubs: {
      ResetPasswordCard: {
        name: 'ResetPasswordCard',
        props: { initialEmail: String, lockEmail: Boolean, embedded: Boolean, allowCancel: Boolean, setPassword: Boolean },
        emits: ['cancel', 'password-reset'],
        template: '<div data-test="recovery" />',
      },
      ElButton: { template: '<button><slot /></button>' },
      ElForm: { template: '<form><slot /></form>' }, ElInput: true,
    },
  } })
}
beforeEach(() => {
  vi.clearAllMocks()
  mocks.userTimezone = undefined
})

describe('personal settings layout', () => {
  it('can hide organization-role identity in the platform account view', async () => {
    mocks.api.mockResolvedValue({ has_usable_password: true })
    const wrapper = render(false)
    try {
      await flushPromises()
      expect(wrapper.find('.account-role-badge').exists()).toBe(false)
      expect(wrapper.findAll('h2').map(title => title.text())).toEqual([
        'Account Info', 'Change Password', 'Last Sign-In',
      ])
      expect(wrapper.find('.account-overview__name').text()).toBe('account@example.com')
    } finally {
      wrapper.unmount()
    }
  })
  it('keeps three stacked sections with matching read-only field grids', async () => {
    mocks.api.mockResolvedValue({ has_usable_password: true })
    const wrapper = render()
    try {
      await flushPromises()
      expect(wrapper.findAll('h2').map(title => title.text())).toEqual([
        'Account Info', 'Change Password', 'Last Sign-In',
      ])
      const grids = wrapper.findAll('.account-details-grid')
      expect(grids).toHaveLength(2)
      expect(grids[0].findAll('dt').map(label => label.text())).toEqual([
        'Username', 'Email', 'Created At',
      ])
      expect(grids[1].findAll('dt').map(label => label.text())).toEqual([
        'Time', 'IP Address', 'Location',
      ])
      expect(wrapper.find('.account-overview__email').exists()).toBe(false)
      expect(grids[0].text()).toContain('account@example.com')
      expect(wrapper.findAll('form label').map(label => label.attributes('for'))).toEqual([
        'account-current-password', 'account-new-password', 'account-confirm-password',
      ])
    } finally {
      wrapper.unmount()
    }
  })

  it('places recovery and password update together without changing the recovery flow', async () => {
    mocks.api.mockResolvedValue({ has_usable_password: true, password_reset_available: true })
    const wrapper = render()
    try {
      await flushPromises()
      const actions = wrapper.get('.account-password-actions')
      expect(actions.findAll('button').map(button => button.text())).toEqual([
        'Reset by Email', 'Change Password',
      ])
      const recoveryButton = actions.get('.account-password-recovery-button')
      expect(recoveryButton.attributes('link')).toBeUndefined()
      await recoveryButton.trigger('click')
      expect(wrapper.find('[data-test="recovery"]').exists()).toBe(true)
      expect(wrapper.find('form').exists()).toBe(false)
      const recovery = wrapper.findComponent({ name: 'ResetPasswordCard' })
      expect(recovery.props('allowCancel')).toBe(true)
      expect(recovery.props('setPassword')).toBe(false)
      expect(wrapper.text()).not.toContain(en.account.passwordRecoveryBack)
      recovery.vm.$emit('cancel')
      await flushPromises()
      expect(wrapper.find('form').exists()).toBe(true)
    } finally {
      wrapper.unmount()
    }
  })

  it('keeps the password update action when email recovery is unavailable', async () => {
    mocks.api.mockResolvedValue({ has_usable_password: true, password_reset_available: false })
    const wrapper = render()
    try {
      await flushPromises()
      expect(wrapper.get('.account-password-actions').findAll('button').map(button => button.text()))
        .toEqual([en.account.btnUpdatePassword])
    } finally {
      wrapper.unmount()
    }
  })
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
    expect(card.props('allowCancel')).toBe(false)
    expect(card.props('setPassword')).toBe(true)
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
