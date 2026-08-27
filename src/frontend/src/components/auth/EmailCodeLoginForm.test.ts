// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../locales/en'
import EmailCodeLoginForm from './EmailCodeLoginForm.vue'

const mocks = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
  notifyWarning: vi.fn(),
  send: vi.fn(),
  verify: vi.fn(),
}))

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
    global: { plugins: [i18n, ElementPlus] },
  })
}

describe('EmailCodeLoginForm', () => {
  beforeEach(() => {
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
    await vi.waitFor(() => {
      expect(wrapper.get('.email-code-login-form__send').text()).toContain('60s')
    })
    const codeInput = wrapper.get<HTMLInputElement>('#email-code-login-code')
    await codeInput.setValue('123456')
    expect(wrapper.get('button.submit-btn').attributes('disabled')).toBeUndefined()

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
