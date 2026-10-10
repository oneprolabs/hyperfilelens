// @vitest-environment jsdom

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent } from 'vue'
import ElementPlus from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../../locales/en'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  send: vi.fn(),
  verify: vi.fn(),
  preload: vi.fn(),
  resetScript: vi.fn(),
}))

vi.mock('../../lib/api', () => ({
  api: mocks.api,
  isAbortError: (error: unknown) => error instanceof DOMException && error.name === 'AbortError',
}))
vi.mock('../../lib/emailCodeLoginApi', () => ({
  sendEmailLoginCode: mocks.send,
  verifyEmailLoginCode: mocks.verify,
}))
vi.mock('../../lib/turnstileLoader', () => ({
  TURNSTILE_LOAD_TIMEOUT_MS: 15000,
  preloadTurnstileScript: mocks.preload,
  resetTurnstileScriptLoad: mocks.resetScript,
}))
vi.mock('../../lib/notify', () => ({
  notifySuccess: vi.fn(),
  notifyError: vi.fn(),
  notifyWarning: vi.fn(),
}))

const WidgetStub = defineComponent({
  name: 'TurnstileWidget',
  emits: ['success'],
  setup(_, { expose }) {
    expose({ reset: vi.fn() })
    return () => null
  },
})

let wrapper: VueWrapper | undefined

async function mountForm() {
  // Use the real shared configuration composable and real AuthTurnstileField.
  const { default: Form } = await import('./EmailCodeLoginForm.vue')
  wrapper = mount(Form, {
    props: { initialEmail: 'person@example.com' },
    global: {
      plugins: [
        ElementPlus,
        createI18n({ legacy: false, locale: 'en', messages: { en } }),
      ],
      stubs: { TurnstileWidget: WidgetStub },
    },
  })
  await flushPromises()
  return wrapper
}

describe('email login Turnstile configuration recovery', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.resetAllMocks()
    sessionStorage.clear()
    mocks.preload.mockResolvedValue(undefined)
    mocks.send.mockRejectedValue({ errorCode: 'TURNSTILE_MISCONFIGURED' })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
  })

  it('still displays verification when the IP is exempt only from password login', async () => {
    mocks.api.mockResolvedValue({
      code: '0000',
      data: { enabled: true, configured: true, site_key: 'test-key', login_exempt: true },
    })
    const form = await mountForm()
    expect(form.find('.auth-turnstile-field').exists()).toBe(true)
    expect(form.get('.email-code-login-form__send').attributes('disabled')).toBeDefined()
    expect(mocks.send).not.toHaveBeenCalled()
  })

  it('shows an actionable error even if both initial and recovery config requests fail', async () => {
    mocks.api.mockRejectedValue(new Error('offline'))
    const form = await mountForm()
    expect(form.find('.auth-turnstile-field').exists()).toBe(false)
    await form.get('.email-code-login-form__send').trigger('click')
    await flushPromises()

    expect(form.get('.auth-turnstile-field__blocked').text()).toContain(en.login.captchaUnavailable)
    expect(form.get('.auth-turnstile-field__retry').exists()).toBe(true)
    expect(form.get('.email-code-login-form__send').attributes('disabled')).toBeDefined()
    expect(mocks.api).toHaveBeenCalledTimes(2)

    mocks.api.mockResolvedValue({
      code: '0000',
      data: { enabled: true, configured: true, site_key: 'test-site-key' },
    })
    await form.get('.auth-turnstile-field__retry').trigger('click')
    await flushPromises()
    expect(form.find('.auth-turnstile-field__blocked').exists()).toBe(false)
    expect(form.get('.email-code-login-form__send').attributes('disabled')).toBeDefined()

    form.getComponent(WidgetStub).vm.$emit('success', 'fresh-token')
    await form.vm.$nextTick()
    expect(form.get('.email-code-login-form__send').attributes('disabled')).toBeUndefined()
    mocks.send.mockResolvedValue({
      code: '0000',
      data: { retry_after: 60, expires_in: 600 },
    })
    await form.get('.email-code-login-form__send').trigger('click')
    await flushPromises()
    expect(mocks.send).toHaveBeenLastCalledWith(
      'person@example.com', expect.any(AbortSignal), { turnstile_token: 'fresh-token' },
    )
  })

  it('recovers a stale disabled configuration into a visible misconfigured state', async () => {
    mocks.api
      .mockResolvedValueOnce({ code: '0000', data: { enabled: false, configured: false } })
      .mockResolvedValue({ code: '0000', data: { enabled: true, configured: false } })
    const form = await mountForm()
    await form.get('.email-code-login-form__send').trigger('click')
    await flushPromises()
    expect(form.get('.auth-turnstile-field__retry').exists()).toBe(true)
    expect(form.get('.email-code-login-form__send').attributes('disabled')).toBeDefined()
  })

  it('allows an explicit disabled configuration to clear the server rejection', async () => {
    mocks.api.mockRejectedValue(new Error('offline'))
    const form = await mountForm()
    await form.get('.email-code-login-form__send').trigger('click')
    await flushPromises()
    mocks.api.mockResolvedValue({ code: '0000', data: { enabled: false, configured: false } })
    await form.get('.auth-turnstile-field__retry').trigger('click')
    await flushPromises()
    expect(form.find('.auth-turnstile-field').exists()).toBe(false)
    expect(form.get('.email-code-login-form__send').attributes('disabled')).toBeUndefined()
  })

  it('reloads verification configuration after a missing-token rejection', async () => {
    mocks.api
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue({
        code: '0000',
        data: { enabled: true, configured: true, site_key: 'test-site-key' },
      })
    mocks.send.mockRejectedValue({
      errorCode: 'VALIDATION_ERROR', fields: { turnstile_token: ['Required'] },
    })
    const form = await mountForm()
    await form.get('.email-code-login-form__send').trigger('click')
    await flushPromises()
    expect(form.findComponent(WidgetStub).exists()).toBe(true)
    expect(form.get('.email-code-login-form__send').attributes('disabled')).toBeDefined()
    form.getComponent(WidgetStub).vm.$emit('success', 'token')
    await form.vm.$nextTick()
    expect(form.get('.email-code-login-form__send').attributes('disabled')).toBeUndefined()
  })
})
