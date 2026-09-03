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

vi.mock('../../lib/api', () => ({ api: mocks.api }))

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

function mountResetPasswordCard() {
  return mount(ResetPasswordCard, {
    props: { initialEmail: 'person@example.com' },
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
