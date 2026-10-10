// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import ElementPlus from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../locales/en'
import EmailCodeLoginForm from './EmailCodeLoginForm.vue'
import AuthTurnstileField from './AuthTurnstileField.vue'

const mocks = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
  notifyWarning: vi.fn(),
  send: vi.fn(),
  verify: vi.fn(),
  turnstileState: 'disabled',
  loadConfig: vi.fn(),
  retryConfig: vi.fn(),
  blockTurnstile: vi.fn(),
  resetWidget: vi.fn(),
}))

vi.mock('../../composables/useTurnstileConfig', () => ({
  useTurnstileConfig: () => ({
    isTurnstilePending: ref(mocks.turnstileState === 'pending'),
    isTurnstileReady: ref(mocks.turnstileState === 'ready'),
    isTurnstileBlocked: ref(mocks.turnstileState === 'blocked'),
    isTurnstileConfigLoaded: ref(false),
    turnstileSiteKey: ref('test-site-key'),
    authTurnstileMountGeneration: ref(0),
    loadTurnstileConfig: mocks.loadConfig,
    retryTurnstileConfig: mocks.retryConfig,
    blockTurnstile: mocks.blockTurnstile,
    buildTurnstilePayload: (token: string) => (
      mocks.turnstileState === 'ready' ? { turnstile_token: token } : {}
    ),
  }),
}))

const TurnstileStub = defineComponent({
  name: 'AuthTurnstileField',
  props: ['ready', 'blocked', 'verified', 'action', 'errorCodeLabel'],
  emits: ['success', 'expire', 'invalidate', 'error', 'retry', 'load-failed'],
  setup(_, { expose }) {
    expose({ reset: mocks.resetWidget })
    return () => null
  },
})

vi.mock('../../lib/emailCodeLoginApi', () => ({
  sendEmailLoginCode: mocks.send,
  verifyEmailLoginCode: mocks.verify,
}))

vi.mock('../../lib/notify', () => ({
  notifyError: mocks.notifyError,
  notifySuccess: mocks.notifySuccess,
  notifyWarning: mocks.notifyWarning,
}))

function mountForm(initialEmail = '') {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
  })
  return mount(EmailCodeLoginForm, {
    props: { initialEmail },
    global: { plugins: [i18n, ElementPlus], stubs: { AuthTurnstileField: TurnstileStub } },
  })
}

describe('EmailCodeLoginForm', () => {
  beforeEach(() => {
    mocks.turnstileState = 'disabled'
    mocks.loadConfig.mockReset()
    mocks.retryConfig.mockReset()
    mocks.blockTurnstile.mockReset()
    mocks.resetWidget.mockReset()
    vi.spyOn(globalThis.crypto.subtle, 'digest').mockImplementation(async (_algorithm, data) => {
      const input = new Uint8Array(data as ArrayBuffer)
      const digest = new Uint8Array(32)
      input.forEach((byte, index) => {
        digest[index % digest.length] ^= byte
      })
      return digest.buffer
    })
    sessionStorage.clear()
    mocks.send.mockReset().mockResolvedValue({
      code: '0000',
      data: {
        message: "Request received. Please check your email for the verification code. If it doesn't arrive, confirm that the email address is correct.",
        retry_after: 60,
        expires_in: 600,
      },
    })
    mocks.verify.mockReset()
    mocks.notifyError.mockReset()
    mocks.notifySuccess.mockReset()
    mocks.notifyWarning.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('keeps the requested email editable, shows a success toast, and starts the cooldown', async () => {
    const wrapper = mountForm('person@example.com')

    await wrapper.get('.email-code-login-form__send').trigger('click')
    await flushPromises()

    await vi.waitFor(() => {
      expect(wrapper.get('.email-code-login-form__send').text()).toContain('60s')
    })

    expect(mocks.send).toHaveBeenCalledWith(
      'person@example.com',
      expect.any(AbortSignal),
      {},
    )
    expect(wrapper.get('#email-code-login-email').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.email-code-login-form__change').exists()).toBe(false)
    expect(wrapper.find('.email-code-login-form__status').exists()).toBe(false)
    expect(mocks.notifySuccess).toHaveBeenCalledWith(expect.objectContaining({
      title: 'Request received',
      message: "Please check your email for the verification code. If it doesn't arrive, confirm that the email address is correct.",
      dedupeKey: 'auth:email-code:send:success',
      duration: 6000,
    }))

    wrapper.unmount()
  })

  it.each(['pending', 'blocked', 'ready'])('blocks sending in the %s state without a token', async (state) => {
    mocks.turnstileState = state
    const wrapper = mountForm('person@example.com')
    expect(wrapper.get('.email-code-login-form__send').attributes('disabled')).toBeDefined()
    await wrapper.get('#email-code-login-email').trigger('keyup.enter')
    expect(mocks.send).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('sends the token with a dedicated action and clears it even after a failed request', async () => {
    mocks.turnstileState = 'ready'
    mocks.send.mockRejectedValueOnce({ errorCode: 'EMAIL_SERVICE_UNAVAILABLE' })
    const wrapper = mountForm('person@example.com')
    const field = wrapper.getComponent(AuthTurnstileField)
    expect(field.props('action')).toBe('email_login_send_code')
    field.vm.$emit('success', 'email-send-token')
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.email-code-login-form__send').attributes('disabled')).toBeUndefined()
    await wrapper.get('.email-code-login-form__send').trigger('click')
    await flushPromises()
    expect(mocks.send).toHaveBeenCalledWith(
      'person@example.com', expect.any(AbortSignal), { turnstile_token: 'email-send-token' },
    )
    expect(field.props('verified')).toBe(false)
    expect(wrapper.get('.email-code-login-form__send').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it.each(['expire', 'invalidate'])('disables sending when the widget emits %s', async (event) => {
    mocks.turnstileState = 'ready'
    const wrapper = mountForm('person@example.com')
    const field = wrapper.getComponent(AuthTurnstileField)
    field.vm.$emit('success', 'token')
    await wrapper.vm.$nextTick()
    field.vm.$emit(event)
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.email-code-login-form__send').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('blocks widget errors and offers a configuration retry', async () => {
    mocks.turnstileState = 'ready'
    const wrapper = mountForm('person@example.com')
    const field = wrapper.getComponent(AuthTurnstileField)
    field.vm.$emit('error', '300030')
    await wrapper.vm.$nextTick()
    expect(mocks.blockTurnstile).toHaveBeenCalled()
    expect(field.props('errorCodeLabel')).toContain('300030')
    field.vm.$emit('retry')
    await flushPromises()
    expect(mocks.retryConfig).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('blocks sending when the server reports incomplete Turnstile configuration', async () => {
    mocks.send.mockRejectedValueOnce({ errorCode: 'TURNSTILE_MISCONFIGURED' })
    const wrapper = mountForm('person@example.com')
    await wrapper.get('.email-code-login-form__send').trigger('click')
    await flushPromises()
    expect(wrapper.getComponent(AuthTurnstileField).props('blocked')).toBe(true)
    expect(mocks.notifySuccess).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does not require another human verification to submit the received email code', async () => {
    mocks.turnstileState = 'ready'
    mocks.verify.mockResolvedValue({ code: '0000', data: { available_orgs: [] } })
    const wrapper = mountForm('person@example.com')
    const field = wrapper.getComponent(AuthTurnstileField)
    field.vm.$emit('success', 'token')
    await wrapper.vm.$nextTick()
    await wrapper.get('.email-code-login-form__send').trigger('click')
    await flushPromises()
    expect(field.props('verified')).toBe(false)
    expect(mocks.resetWidget).toHaveBeenCalled()
    await wrapper.get('#email-code-login-code').setValue('123456')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mocks.verify).toHaveBeenCalledWith(
      'person@example.com', '123456', expect.any(AbortSignal),
    )
    expect(wrapper.emitted('verified')).toHaveLength(1)
    wrapper.unmount()
  })

  it('requires a fresh human verification after remounting the email form', async () => {
    mocks.turnstileState = 'ready'
    const first = mountForm('person@example.com')
    first.getComponent(AuthTurnstileField).vm.$emit('success', 'old-token')
    await first.vm.$nextTick()
    first.unmount()
    const second = mountForm('person@example.com')
    expect(second.getComponent(AuthTurnstileField).props('verified')).toBe(false)
    expect(second.get('.email-code-login-form__send').attributes('disabled')).toBeDefined()
    second.unmount()
  })

  it.each([
    { errorCode: 'TURNSTILE_INVALID' },
    { errorCode: 'VALIDATION_ERROR', fields: { turnstile_token: ['Required'] } },
  ])('reloads configuration after a server verification rejection', async (error) => {
    mocks.send.mockRejectedValueOnce(error)
    const wrapper = mountForm('person@example.com')
    await wrapper.get('.email-code-login-form__send').trigger('click')
    await flushPromises()
    expect(mocks.retryConfig).toHaveBeenCalled()
    expect(mocks.notifySuccess).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('disables sign-in after an incorrect code until six new digits are entered', async () => {
    mocks.verify.mockRejectedValueOnce({
      errorCode: 'INVALID_OR_EXPIRED_CODE',
    })
    const wrapper = mountForm('person@example.com')
    await wrapper.get('.email-code-login-form__send').trigger('click')
    await flushPromises()
    await vi.waitFor(() => {
      expect(wrapper.get('.email-code-login-form__send').text()).toContain('60s')
    })

    const codeInput = wrapper.get<HTMLInputElement>('#email-code-login-code')
    await codeInput.setValue('123456')
    expect(wrapper.get('button.submit-btn').attributes('disabled')).toBeUndefined()

    await wrapper.get('button.submit-btn').trigger('click')
    await flushPromises()

    expect(mocks.verify).toHaveBeenCalledWith(
      'person@example.com',
      '123456',
      expect.any(AbortSignal),
    )
    expect(codeInput.element.value).toBe('')
    expect(wrapper.get('button.submit-btn').attributes('disabled')).toBeDefined()
    expect(wrapper.get('.error-msg').attributes('role')).toBe('alert')

    await codeInput.setValue('654321')
    expect(wrapper.get('button.submit-btn').attributes('disabled')).toBeUndefined()

    wrapper.unmount()
  })

  it('hands an unknown verification result to the parent without enabling another attempt', async () => {
    const networkError = { status: 0, errorCode: 'NETWORK.UNAVAILABLE' }
    mocks.verify.mockRejectedValueOnce(networkError)
    const wrapper = mountForm('person@example.com')
    await wrapper.get('.email-code-login-form__send').trigger('click')
    await flushPromises()
    const codeInput = wrapper.get<HTMLInputElement>('#email-code-login-code')
    await codeInput.setValue('123456')

    await vi.waitFor(() => {
      expect(wrapper.get('button.submit-btn').attributes('disabled')).toBeUndefined()
    })
    await wrapper.get('button.submit-btn').trigger('click')
    await vi.waitFor(() => {
      expect(mocks.verify).toHaveBeenCalledWith(
        'person@example.com',
        '123456',
        expect.any(AbortSignal),
      )
    })
    await flushPromises()

    expect(wrapper.emitted('verification-unknown')).toEqual([[networkError]])
    expect(codeInput.element.value).toBe('123456')

    await wrapper.setProps({ disabled: true })
    expect(wrapper.get('button.submit-btn').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('invalidates the issued code when the normalized email changes', async () => {
    const wrapper = mountForm('person@example.com')
    await wrapper.get('.email-code-login-form__send').trigger('click')
    await flushPromises()
    await vi.waitFor(() => {
      expect(wrapper.get('.email-code-login-form__send').text()).toContain('60s')
    })

    const codeInput = wrapper.get<HTMLInputElement>('#email-code-login-code')
    await codeInput.setValue('123456')
    expect(wrapper.get('button.submit-btn').attributes('disabled')).toBeUndefined()

    await wrapper.get('#email-code-login-email').setValue('another@example.com')
    await flushPromises()

    expect(wrapper.get('#email-code-login-email').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('#email-code-login-code').attributes('disabled')).toBeDefined()
    expect(codeInput.element.value).toBe('')
    expect(wrapper.get('button.submit-btn').attributes('disabled')).toBeDefined()
    await vi.waitFor(() => {
      expect(wrapper.get('.email-code-login-form__send').attributes('disabled')).toBeUndefined()
    })

    wrapper.unmount()
  })

  it('keeps the issued code usable when only casing or surrounding spaces change', async () => {
    const wrapper = mountForm('person@example.com')
    await wrapper.get('.email-code-login-form__send').trigger('click')
    await flushPromises()
    await vi.waitFor(() => {
      expect(wrapper.get('.email-code-login-form__send').text()).toContain('60s')
    })

    const codeInput = wrapper.get<HTMLInputElement>('#email-code-login-code')
    await codeInput.setValue('123456')
    await wrapper.get('#email-code-login-email').setValue('  PERSON@EXAMPLE.COM  ')

    expect(codeInput.element.value).toBe('123456')
    expect(wrapper.get('button.submit-btn').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('restores the issued-code state and countdown after a refresh', async () => {
    const first = mountForm('person@example.com')
    await first.get('.email-code-login-form__send').trigger('click')
    await vi.waitFor(() => {
      expect(first.get('.email-code-login-form__send').text()).toContain('60s')
    })
    first.unmount()

    const refreshed = mountForm('person@example.com')
    await vi.waitFor(() => {
      expect(refreshed.get('#email-code-login-code').attributes('disabled')).toBeUndefined()
    })

    expect(refreshed.get('#email-code-login-email').attributes('disabled')).toBeUndefined()
    expect(refreshed.get('.email-code-login-form__send').text()).toContain('60s')
    expect(mocks.send).toHaveBeenCalledTimes(1)
    refreshed.unmount()
  })
})
