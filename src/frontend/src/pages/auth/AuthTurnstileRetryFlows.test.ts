// @vitest-environment jsdom

import { defineComponent, h, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ResetPasswordCard from '../../components/auth/ResetPasswordCard.vue'
import { en } from '../../locales/en'
import Register from './Register.vue'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  blockTurnstile: vi.fn(),
  buildTurnstilePayload: vi.fn(),
  loadTurnstileConfig: vi.fn(),
  retryTurnstileConfig: vi.fn(),
  resetWidget: vi.fn(),
  routerPush: vi.fn(),
  routerReplace: vi.fn(),
  fetchDeployProfile: vi.fn(),
}))

vi.mock('../../lib/api', () => ({
  api: mocks.api,
  apiErrorMessageI18n: (error: unknown, _t: unknown, fallback: string) => {
    return (error as { message?: string })?.message || fallback
  },
}))

vi.mock('../../composables/useDeployProfile', () => ({
  fetchDeployProfile: mocks.fetchDeployProfile,
}))

vi.mock('../../composables/useLocaleSwitch', () => ({
  useLocaleSwitch: () => ({
    canSwitchLocale: ref(false),
    currentLocaleLabel: ref('English'),
    localeOptions: ref([{ code: 'en', label: 'English' }]),
    selectLocale: vi.fn(),
  }),
}))

vi.mock('../../composables/useTurnstileConfig', () => ({
  useTurnstileConfig: () => ({
    turnstileSiteKey: ref('test-site-key'),
    isTurnstilePending: ref(false),
    isTurnstileReady: ref(true),
    isTurnstileBlocked: ref(false),
    authTurnstileMountGeneration: ref(0),
    loadTurnstileConfig: mocks.loadTurnstileConfig,
    retryTurnstileConfig: mocks.retryTurnstileConfig,
    buildTurnstilePayload: mocks.buildTurnstilePayload,
    blockTurnstile: mocks.blockTurnstile,
  }),
}))

vi.mock('../../lib/appConfig', () => ({
  appConfig: { showEula: false },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({
    push: mocks.routerPush,
    replace: mocks.routerReplace,
  }),
}))

const AuthTurnstileFieldStub = defineComponent({
  name: 'AuthTurnstileField',
  props: {
    errorMessage: { type: String, default: '' },
    errorCodeLabel: { type: String, default: '' },
    verified: { type: Boolean, default: false },
    manualRetryLabel: { type: String, default: '' },
  },
  emits: ['retry', 'success', 'expire', 'invalidate', 'error', 'load-failed'],
  setup(props, { expose }) {
    expose({ reset: mocks.resetWidget })
    return () => h('div', {
      class: 'turnstile-field-stub',
      role: props.errorMessage ? 'alert' : undefined,
    }, props.errorMessage)
  },
})

function createI18nPlugin() {
  return createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
}

function mountRegister() {
  return mount(Register, {
    global: {
      plugins: [createI18nPlugin(), ElementPlus],
      stubs: {
        AuthTurnstileField: AuthTurnstileFieldStub,
        CheckCircle2: true,
        Eye: true,
        EyeOff: true,
        Globe: true,
        Key: true,
        Lock: true,
        Mail: true,
      },
    },
  })
}

function mountResetPasswordCard(embedded = false, options: { allowCancel?: boolean; setPassword?: boolean } = {}) {
  return mount(ResetPasswordCard, {
    props: { initialEmail: 'person@example.com', embedded, lockEmail: embedded, ...options },
    global: {
      plugins: [createI18nPlugin(), ElementPlus],
      stubs: {
        AuthTurnstileField: AuthTurnstileFieldStub,
        CheckCircle2: true,
        Eye: true,
        EyeOff: true,
        Key: true,
        Lock: true,
        Mail: true,
      },
    },
  })
}

function submittedBody(call: unknown[]) {
  const init = call[1] as RequestInit
  return JSON.parse(String(init.body)) as Record<string, string>
}

describe('authentication Turnstile retry flows', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mocks.fetchDeployProfile.mockResolvedValue({ email_signup_enabled: true })
    mocks.loadTurnstileConfig.mockResolvedValue(undefined)
    mocks.retryTurnstileConfig.mockResolvedValue(undefined)
    mocks.buildTurnstilePayload.mockImplementation((token: string) => (
      token ? { turnstile_token: token } : {}
    ))
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('shows the account email as read-only information instead of an input', async () => {
    mocks.api.mockResolvedValue({ code: '0000', data: {} })
    const wrapper = mountResetPasswordCard(true)
    await flushPromises()
    expect(wrapper.classes()).toContain('reset-password-card--embedded')
    expect(wrapper.get('.embedded-reset-label').text()).toBe(en.account.fieldEmail)
    expect(wrapper.get('.embedded-reset-email').text()).toContain('person@example.com')
    expect(wrapper.find('#reset-request-email').exists()).toBe(false)
    expect(wrapper.find('#reset-new-password').exists()).toBe(true)
    expect(wrapper.text()).not.toContain(en.findPwd.requestSubtitle)
    expect(wrapper.get('button.embedded-send-code').text()).toBe(en.account.sendCode)
    expect(wrapper.find('.reset-footer').exists()).toBe(false)
    wrapper.getComponent(AuthTurnstileFieldStub).vm.$emit('success', 'embedded-reset-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('button.embedded-send-code').trigger('click')
    await flushPromises()
    expect(submittedBody(mocks.api.mock.calls[0]).email).toBe('person@example.com')
    expect(wrapper.findAll('label.embedded-reset-label').map(label => label.attributes('for'))).toEqual([
      'reset-verification-code', 'reset-new-password', 'reset-confirm-password',
    ])
    wrapper.unmount()
  })

  it('keeps the standalone login form layout unchanged', async () => {
    const wrapper = mountResetPasswordCard()
    await flushPromises()
    expect(wrapper.classes()).not.toContain('reset-password-card--embedded')
    expect(wrapper.get('label[for="reset-request-email"]').classes()).toContain('sr-only')
    expect(wrapper.get('#reset-request-email').attributes('readonly')).toBeUndefined()
    expect(wrapper.find('.reset-footer').exists()).toBe(true)
    wrapper.unmount()
  })

  it('shows all recovery fields before sending a code and requires a successful send before confirmation', async () => {
    const wrapper = mountResetPasswordCard(true, { allowCancel: true })
    await flushPromises()
    expect(wrapper.findAll('form')).toHaveLength(1)
    expect(wrapper.findAll('label.embedded-reset-label').map(label => label.text())).toEqual([
      'Verification Code', 'New Password', 'Confirm Password',
    ])
    await wrapper.get('#reset-verification-code').setValue('123456')
    await wrapper.get('#reset-new-password').setValue('NewPass123')
    await wrapper.get('#reset-confirm-password').setValue('NewPass123')
    expect(wrapper.get('.embedded-confirm-password').attributes('disabled')).toBeDefined()
    await wrapper.get('form').trigger('submit')
    expect(mocks.api).not.toHaveBeenCalled()
    const cancel = wrapper.findAll('button').find(button => button.text() === en.common.cancel)!
    await cancel.trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    wrapper.unmount()
  })

  it('preserves entered passwords when sending and resending without replacing the form', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    const wrapper = mountResetPasswordCard(true)
    try {
      mocks.api.mockResolvedValue({ code: '0000', data: {} })
      await flushPromises()
      const form = wrapper.get('form').element
      await wrapper.get('#reset-new-password').setValue('NewPass123')
      await wrapper.get('#reset-confirm-password').setValue('NewPass123')
      const captcha = wrapper.getComponent(AuthTurnstileFieldStub)
      captcha.vm.$emit('success', 'first-send-token')
      await wrapper.vm.$nextTick()
      await wrapper.get('.embedded-send-code').trigger('click')
      await flushPromises()
      expect(wrapper.get('form').element).toBe(form)
      expect(wrapper.get('.embedded-send-code').text()).toBe('Resend in 60s')
      expect(wrapper.text()).toContain(en.account.codeSent)
      expect(wrapper.get<HTMLInputElement>('#reset-new-password').element.value).toBe('NewPass123')
      captcha.vm.$emit('success', 'resend-token')
      await wrapper.vm.$nextTick()
      await wrapper.get('.embedded-send-code').trigger('click')
      expect(mocks.api).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(60_000)
      expect(wrapper.get('.embedded-send-code').text()).toBe('Resend Code')
      await wrapper.get('.embedded-send-code').trigger('click')
      await flushPromises()
      expect(mocks.api).toHaveBeenCalledTimes(2)
      expect(submittedBody(mocks.api.mock.calls[1]).turnstile_token).toBe('resend-token')
      expect(wrapper.get('form').element).toBe(form)
      expect(wrapper.get<HTMLInputElement>('#reset-confirm-password').element.value).toBe('NewPass123')
      expect(wrapper.emitted('update:step')).toEqual([['request']])
      expect(mocks.resetWidget).toHaveBeenCalledTimes(2)
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('shows send failures inline and retries without clearing the passwords', async () => {
    mocks.api.mockRejectedValueOnce({ errorCode: 'EMAIL_SERVICE_UNAVAILABLE' })
      .mockResolvedValueOnce({ code: '0000', data: {} })
    const wrapper = mountResetPasswordCard(true)
    await flushPromises()
    await wrapper.get('#reset-new-password').setValue('NewPass123')
    const captcha = wrapper.getComponent(AuthTurnstileFieldStub)
    captcha.vm.$emit('success', 'send-error-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('.embedded-send-code').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe(en.findPwd.emailServiceUnavailable)
    expect(wrapper.get('.embedded-confirm-password').attributes('disabled')).toBeDefined()
    expect(wrapper.get<HTMLInputElement>('#reset-new-password').element.value).toBe('NewPass123')
    captcha.vm.$emit('success', 'retry-send-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('.embedded-send-code').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).toContain(en.account.codeSent)
    wrapper.unmount()
  })

  it('keeps send and confirm mutually exclusive and disables cancellation during requests', async () => {
    let resolveSend!: (value: unknown) => void
    mocks.api.mockReturnValue(new Promise(resolve => { resolveSend = resolve }))
    const wrapper = mountResetPasswordCard(true, { allowCancel: true })
    await flushPromises()
    wrapper.getComponent(AuthTurnstileFieldStub).vm.$emit('success', 'pending-send-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('.embedded-send-code').trigger('click')
    await wrapper.get('.embedded-send-code').trigger('click')
    await wrapper.get('form').trigger('submit')
    expect(mocks.api).toHaveBeenCalledTimes(1)
    const cancel = wrapper.findAll('button').find(button => button.text() === en.common.cancel)!
    expect(cancel.attributes('disabled')).toBeDefined()
    resolveSend({ code: '0000', data: {} })
    await flushPromises()
    expect(cancel.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('uses Set Password for passwordless accounts without a cancel action', async () => {
    const wrapper = mountResetPasswordCard(true, { setPassword: true })
    await flushPromises()
    expect(wrapper.get('.embedded-confirm-password').text()).toBe('Set Password')
    expect(wrapper.findAll('button').some(button => button.text() === en.common.cancel)).toBe(false)
    wrapper.unmount()
  })

  it('does not redirect an embedded recovery form to registration', async () => {
    mocks.api.mockResolvedValue({ code: '0000', data: { pending_registration: true } })
    const wrapper = mountResetPasswordCard(true)
    await flushPromises()
    wrapper.getComponent(AuthTurnstileFieldStub).vm.$emit('success', 'pending-account-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('.embedded-send-code').trigger('click')
    await flushPromises()
    expect(mocks.routerPush).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toBe(en.findPwd.pendingRegistration)
    expect(wrapper.get('.embedded-confirm-password').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('ignores a late successful confirmation after leaving the embedded form', async () => {
    let resolveConfirmation!: (value: unknown) => void
    mocks.api.mockImplementation((path: string) => {
      if (path === '/api/v1/auth/forgot-password') return Promise.resolve({ code: '0000', data: {} })
      return new Promise(resolve => { resolveConfirmation = resolve })
    })
    const wrapper = mountResetPasswordCard(true)
    await flushPromises()
    wrapper.getComponent(AuthTurnstileFieldStub).vm.$emit('success', 'late-confirm-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('.embedded-send-code').trigger('click')
    await flushPromises()
    await wrapper.get('#reset-verification-code').setValue('123456')
    await wrapper.get('#reset-new-password').setValue('NewPass123')
    await wrapper.get('#reset-confirm-password').setValue('NewPass123')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('.embedded-send-code').attributes('disabled')).toBeDefined()
    await wrapper.get('form').trigger('submit')
    expect(mocks.api).toHaveBeenCalledTimes(2)
    wrapper.unmount()
    resolveConfirmation({ code: '0000' })
    await flushPromises()
    expect(wrapper.emitted('password-reset')).toBeUndefined()
    expect(wrapper.emitted('back-to-login')).toBeUndefined()
  })

  it('retains the standalone two-step password reset flow', async () => {
    mocks.api.mockResolvedValue({ code: '0000', data: {} })
    const wrapper = mountResetPasswordCard()
    await flushPromises()
    expect(wrapper.find('#reset-new-password').exists()).toBe(false)
    wrapper.getComponent(AuthTurnstileFieldStub).vm.$emit('success', 'standalone-send-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('button.submit-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('#reset-request-email').exists()).toBe(false)
    expect(wrapper.find('#reset-verification-code').exists()).toBe(true)
    expect(wrapper.find('#reset-new-password').exists()).toBe(true)
    expect(wrapper.emitted('update:step')).toEqual([['request'], ['reset']])
    wrapper.unmount()
  })

  it.each([
    [
      { errorCode: 'INVALID_CODE', detail: { error: { reason: 'EXPIRED' } } },
      en.account.passwordCodeExpired,
    ],
    [
      { errorCode: 'INVALID_CODE', detail: { data: { error: { reason: 'TOO_MANY_ATTEMPTS' } } } },
      en.account.passwordCodeAttemptsExceeded,
    ],
    [{ errorCode: 'INVALID_CODE' }, en.account.passwordCodeInvalid],
    [{ status: 429 }, en.account.passwordResetRateLimited],
    [{ status: 503, message: 'Private provider failure' }, en.account.passwordResetServiceUnavailable],
    [{ fields: { password: ['Password must contain uppercase letters'] } }, 'Password must contain uppercase letters'],
    [{ message: 'Network unavailable. Please retry.' }, 'Network unavailable. Please retry.'],
    [new Error(''), en.findPwd.resetFailed],
  ])('displays the reason for a rejected password reset', async (failure, expected) => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/auth/forgot-password') return { code: '0000', data: {} }
      throw failure
    })
    const wrapper = mountResetPasswordCard(true)
    await flushPromises()
    wrapper.getComponent(AuthTurnstileFieldStub).vm.$emit('success', 'reset-error-test-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('button.embedded-send-code').trigger('click')
    await flushPromises()
    await wrapper.get('#reset-verification-code').setValue('123456')
    await wrapper.get('#reset-new-password').setValue('NewPass123')
    await wrapper.get('#reset-confirm-password').setValue('NewPass123')
    await wrapper.get('button.embedded-confirm-password').trigger('click')
    await flushPromises()
    expect(wrapper.get('#reset-form-error').text()).toBe(expected)
    expect(wrapper.get('button.embedded-confirm-password').attributes('disabled')).toBeUndefined()
    expect(wrapper.emitted('password-reset')).toBeUndefined()
    wrapper.unmount()
  })

  it('allows retrying after correcting a rejected verification code', async () => {
    let confirmations = 0
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/auth/forgot-password') return { code: '0000', data: {} }
      confirmations += 1
      if (confirmations === 1) throw { errorCode: 'INVALID_CODE' }
      return { code: '0000' }
    })
    const wrapper = mountResetPasswordCard(true)
    await flushPromises()
    wrapper.getComponent(AuthTurnstileFieldStub).vm.$emit('success', 'retry-test-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('button.embedded-send-code').trigger('click')
    await flushPromises()
    await wrapper.get('#reset-verification-code').setValue('123456')
    await wrapper.get('#reset-new-password').setValue('NewPass123')
    await wrapper.get('#reset-confirm-password').setValue('NewPass123')
    await wrapper.get('button.embedded-confirm-password').trigger('click')
    await flushPromises()
    expect(wrapper.get('#reset-form-error').text()).toBe(en.account.passwordCodeInvalid)
    await wrapper.get('#reset-verification-code').setValue('654321')
    expect(wrapper.find('#reset-form-error').exists()).toBe(false)
    expect(wrapper.get('button.embedded-confirm-password').attributes('disabled')).toBeUndefined()
    await wrapper.get('button.embedded-confirm-password').trigger('click')
    await flushPromises()
    expect(confirmations).toBe(2)
    expect(submittedBody(mocks.api.mock.calls[2]).code).toBe('654321')
    expect(wrapper.emitted('password-reset')).toHaveLength(1)
    expect(wrapper.get('button.embedded-confirm-password').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('releases the submission lock after catch without bypassing form validation', async () => {
    let rejectRequest!: (reason: unknown) => void
    mocks.api.mockImplementation((path: string) => {
      if (path === '/api/v1/auth/forgot-password') return Promise.resolve({ code: '0000', data: {} })
      return new Promise((_resolve, reject) => { rejectRequest = reject })
    })
    const wrapper = mountResetPasswordCard(true)
    await flushPromises()
    wrapper.getComponent(AuthTurnstileFieldStub).vm.$emit('success', 'request-lock-test-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('button.embedded-send-code').trigger('click')
    await flushPromises()
    await wrapper.get('#reset-verification-code').setValue('123456')
    await wrapper.get('#reset-new-password').setValue('NewPass123')
    await wrapper.get('#reset-confirm-password').setValue('NewPass123')
    const button = wrapper.get('button.embedded-confirm-password')
    expect(button.attributes('disabled')).toBeUndefined()
    await button.trigger('click')
    expect(button.attributes('disabled')).toBeDefined()
    rejectRequest({ errorCode: 'INVALID_CODE' })
    await flushPromises()
    expect(wrapper.get('#reset-form-error').text()).toBe(en.account.passwordCodeInvalid)
    expect(button.attributes('disabled')).toBeUndefined()
    await wrapper.get('#reset-verification-code').setValue('')
    expect(button.attributes('disabled')).toBeDefined()
    await wrapper.get('#reset-verification-code').setValue('654321')
    expect(button.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('disables submission until all fields are complete and valid', async () => {
    mocks.api.mockResolvedValue({ code: '0000', data: {} })
    const wrapper = mountResetPasswordCard(true)
    await flushPromises()
    wrapper.getComponent(AuthTurnstileFieldStub).vm.$emit('success', 'validation-test-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('button.embedded-send-code').trigger('click')
    await flushPromises()
    const button = wrapper.get('button.embedded-confirm-password')
    expect(button.attributes('disabled')).toBeDefined()
    await wrapper.get('#reset-verification-code').setValue('abcdef')
    expect(button.attributes('disabled')).toBeDefined()
    await wrapper.get('#reset-verification-code').setValue('123456')
    await wrapper.get('#reset-new-password').setValue('lowercase123')
    await wrapper.get('#reset-confirm-password').setValue('lowercase123')
    expect(button.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain(en.account.securityPwdHint)
    await wrapper.get('#reset-new-password').setValue('NewPass123')
    expect(button.attributes('disabled')).toBeDefined()
    expect(wrapper.get('#reset-confirm-password-error').text()).toBe(en.findPwd.passwordNotMatch)
    await wrapper.get('#reset-confirm-password').setValue('NewPass123')
    expect(button.attributes('disabled')).toBeUndefined()
    expect(wrapper.find('#reset-confirm-password-error').exists()).toBe(false)
    await wrapper.get('#reset-verification-code').setValue('')
    expect(button.attributes('disabled')).toBeDefined()
    await wrapper.get('#reset-verification-code').setValue('654321')
    expect(button.attributes('disabled')).toBeUndefined()
    await wrapper.get('#reset-confirm-password').setValue('')
    expect(button.attributes('disabled')).toBeDefined()
    expect(mocks.api).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it.each([
    ['registration', mountRegister],
    ['password reset', mountResetPasswordCard],
  ])('fully reloads Turnstile from the manual recovery action on %s', async (_name, mountView) => {
    const wrapper = mountView()
    await flushPromises()
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)

    turnstile.vm.$emit('success', 'verified-token')
    await wrapper.vm.$nextTick()
    expect(turnstile.props('verified')).toBe(true)

    turnstile.vm.$emit('retry')
    await flushPromises()

    expect(mocks.retryTurnstileConfig).toHaveBeenCalledTimes(1)
    expect(turnstile.props('verified')).toBe(false)
    wrapper.unmount()
  })

  it('uses a replacement token when registration code sending is retried', async () => {
    let sendAttempt = 0
    mocks.api.mockImplementation(async (path: string) => {
      if (path !== '/api/v1/auth/email-register/send-code') {
        throw new Error(`Unexpected API path: ${path}`)
      }
      sendAttempt += 1
      if (sendAttempt === 1) {
        return {
          code: '1001',
          error: {
            fields: {
              turnstile_token: ['Invalid or expired human verification'],
            },
          },
        }
      }
      return { code: '0000' }
    })

    const wrapper = mountRegister()
    await flushPromises()
    await wrapper.find('input[type="text"]').setValue('person@example.com')
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)
    const sendCode = wrapper.get('button.send-code-btn')

    turnstile.vm.$emit('success', 'registration-token-1')
    await wrapper.vm.$nextTick()
    await sendCode.trigger('click')
    await flushPromises()

    expect(mocks.resetWidget).toHaveBeenCalledTimes(1)
    expect(mocks.api).toHaveBeenCalledTimes(1)

    await sendCode.trigger('click')
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(1)

    turnstile.vm.$emit('success', 'registration-token-2')
    await wrapper.vm.$nextTick()
    await sendCode.trigger('click')
    await flushPromises()

    expect(mocks.api).toHaveBeenCalledTimes(2)
    expect(submittedBody(mocks.api.mock.calls[0]).turnstile_token).toBe('registration-token-1')
    expect(submittedBody(mocks.api.mock.calls[1]).turnstile_token).toBe('registration-token-2')
    wrapper.unmount()
  })

  it('reenables password reset requests after receiving a replacement token', async () => {
    let resetAttempt = 0
    mocks.api.mockImplementation(async (path: string) => {
      if (path !== '/api/v1/auth/forgot-password') {
        throw new Error(`Unexpected API path: ${path}`)
      }
      resetAttempt += 1
      if (resetAttempt === 1) {
        return {
          code: '1001',
          error: {
            fields: {
              turnstile_token: ['Invalid or expired human verification'],
            },
          },
        }
      }
      return { code: '0000', data: {} }
    })

    const wrapper = mountResetPasswordCard()
    await flushPromises()
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)
    const sendResetCode = wrapper.get('button.submit-btn')

    expect(sendResetCode.attributes('disabled')).toBeDefined()
    turnstile.vm.$emit('success', 'reset-token-1')
    await wrapper.vm.$nextTick()
    expect(sendResetCode.attributes('disabled')).toBeUndefined()

    await sendResetCode.trigger('click')
    await flushPromises()

    expect(mocks.resetWidget).toHaveBeenCalledTimes(1)
    expect(sendResetCode.attributes('disabled')).toBeDefined()
    expect(mocks.api).toHaveBeenCalledTimes(1)

    turnstile.vm.$emit('success', 'reset-token-2')
    await wrapper.vm.$nextTick()
    expect(sendResetCode.attributes('disabled')).toBeUndefined()

    await sendResetCode.trigger('click')
    await flushPromises()

    expect(mocks.api).toHaveBeenCalledTimes(2)
    expect(submittedBody(mocks.api.mock.calls[0]).turnstile_token).toBe('reset-token-1')
    expect(submittedBody(mocks.api.mock.calls[1]).turnstile_token).toBe('reset-token-2')
    wrapper.unmount()
  })
})
