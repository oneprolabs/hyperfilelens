// @vitest-environment jsdom

import { defineComponent, h, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../locales/en'
import { storeSessionNotice } from '../../lib/sessionNotice'
import Login from './Login.vue'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  blockTurnstile: vi.fn(),
  buildTurnstilePayload: vi.fn(),
  confirmCurrentSession: vi.fn(),
  fetchCurrentUser: vi.fn(),
  fetchDeployProfile: vi.fn(),
  loadTurnstileConfig: vi.fn(),
  retryTurnstileConfig: vi.fn(),
  resetWidget: vi.fn(),
  routeQuery: {} as Record<string, string>,
  routerPush: vi.fn(),
  setStoredOrgKey: vi.fn(),
  setUser: vi.fn(),
  syncAuthenticatedLocale: vi.fn(),
  turnstileBlocked: false,
}))

vi.mock('../../lib/api', () => ({ api: mocks.api }))

vi.mock('../../composables/useAuth', () => ({
  confirmCurrentSession: mocks.confirmCurrentSession,
  fetchCurrentUser: mocks.fetchCurrentUser,
  setStoredOrgKey: mocks.setStoredOrgKey,
  useAuth: () => ({ setUser: mocks.setUser }),
}))

vi.mock('../../composables/useLocaleSwitch', () => ({
  useLocaleSwitch: () => ({
    canSwitchLocale: ref(false),
    currentLocaleLabel: ref('English'),
    localeOptions: ref([{ code: 'en', label: 'English' }]),
    selectLocale: vi.fn(),
    syncAuthenticatedLocale: mocks.syncAuthenticatedLocale,
  }),
}))

vi.mock('../../composables/useTurnstileConfig', () => ({
  useTurnstileConfig: () => ({
    turnstileSiteKey: ref('test-site-key'),
    isTurnstilePending: ref(false),
    isTurnstileReady: ref(true),
    isTurnstileBlocked: ref(mocks.turnstileBlocked),
    authTurnstileMountGeneration: ref(0),
    loadTurnstileConfig: mocks.loadTurnstileConfig,
    retryTurnstileConfig: mocks.retryTurnstileConfig,
    buildTurnstilePayload: mocks.buildTurnstilePayload,
    blockTurnstile: mocks.blockTurnstile,
  }),
}))

vi.mock('../../composables/useDeployProfile', () => ({
  fetchDeployProfile: mocks.fetchDeployProfile,
  resolvePostLoginPath: vi.fn().mockResolvedValue('/'),
}))

vi.mock('../../lib/appConfig', () => ({
  appConfig: { showEula: false },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: mocks.routeQuery }),
  useRouter: () => ({ push: mocks.routerPush }),
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

const successfulLoginResponse = {
  code: '0000',
  data: {
    user: { id: 1, email: 'person@example.com', username: 'person' },
    available_orgs: [],
  },
}

function installDefaultApiMock() {
  mocks.api.mockImplementation(async (path: string) => {
    if (path === '/api/v1/auth/google/config') {
      return { code: '0000', data: { enabled: false } }
    }
    if (path === '/api/v1/auth/email-login') {
      return successfulLoginResponse
    }
    throw new Error(`Unexpected API path: ${path}`)
  })
}

async function mountLogin(viewportWidth: number) {
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    value: viewportWidth,
  })
  window.dispatchEvent(new Event('resize'))

  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  const wrapper = mount(Login, {
    attachTo: document.body,
    global: {
      plugins: [i18n, ElementPlus],
      stubs: {
        AuthTurnstileField: AuthTurnstileFieldStub,
        ResetPasswordCard: true,
        Globe: true,
        Mail: true,
        Lock: true,
        Eye: true,
        EyeOff: true,
      },
    },
  })
  await flushPromises()
  return wrapper
}

async function fillCredentials(wrapper: Awaited<ReturnType<typeof mountLogin>>) {
  const inputs = wrapper.findAll('input')
  await inputs[0].setValue('person@example.com')
  await inputs[1].setValue('ValidPass123')
  return {
    email: inputs[0].element as HTMLInputElement,
    password: inputs[1].element as HTMLInputElement,
  }
}

function emailLoginCalls() {
  return mocks.api.mock.calls.filter(([path]) => path === '/api/v1/auth/email-login')
}

function submittedBody(call: unknown[]) {
  const init = call[1] as RequestInit
  return JSON.parse(String(init.body)) as Record<string, string>
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

describe('Login Turnstile lifecycle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
    for (const key of Object.keys(mocks.routeQuery)) {
      delete mocks.routeQuery[key]
    }
    mocks.fetchDeployProfile.mockResolvedValue({
      email_signup_enabled: false,
      email_code_login_available: false,
      password_reset_available: false,
    })
    mocks.turnstileBlocked = false
    mocks.loadTurnstileConfig.mockResolvedValue(undefined)
    mocks.retryTurnstileConfig.mockResolvedValue(undefined)
    mocks.buildTurnstilePayload.mockImplementation((token: string) => (
      token ? { turnstile_token: token } : {}
    ))
    mocks.confirmCurrentSession.mockResolvedValue({ state: 'unknown' })
    installDefaultApiMock()
  })

  it('ignores a forged session reason from the public URL', async () => {
    mocks.routeQuery.reason = 'TOKEN_REUSED'
    mocks.routeQuery.redirect = '/ops/alerts'

    const wrapper = await mountLogin(1440)

    expect(wrapper.find('.session-alert').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows a backend-produced session notice only once', async () => {
    expect(storeSessionNotice('TOKEN_REUSED')).toBe(true)

    const firstMount = await mountLogin(1440)
    const securityNotice = firstMount.get('.session-alert')
    expect(securityNotice.text()).toContain(
      'Unusual sign-in activity. Sign in again.',
    )
    expect(securityNotice.classes()).toContain('session-alert--warning')
    expect(firstMount.getComponent({ name: 'ElAlert' }).props('type')).toBe('warning')
    firstMount.unmount()

    const replayMount = await mountLogin(1440)
    expect(replayMount.find('.session-alert').exists()).toBe(false)
    replayMount.unmount()
  })

  it('uses the brand information tone for routine session expiry', async () => {
    expect(storeSessionNotice('TOKEN_EXPIRED')).toBe(true)

    const wrapper = await mountLogin(1440)
    const expiryNotice = wrapper.get('.session-alert')

    expect(expiryNotice.text()).toContain('Sign-in expired. Sign in again.')
    expect(expiryNotice.classes()).toContain('session-alert--info')
    expect(wrapper.getComponent({ name: 'ElAlert' }).props('type')).toBe('info')
    wrapper.unmount()
  })

  it('exposes an accessible password form when no method tabs are available', async () => {
    const wrapper = await mountLogin(1440)
    const panel = wrapper.get('#login-method-panel')
    const form = wrapper.get('form.login-password-form')
    const email = wrapper.get('#login-email')
    const password = wrapper.get('#login-password')

    expect(panel.attributes('aria-labelledby')).toBe('login-card-title')
    expect(form.exists()).toBe(true)
    expect(wrapper.get('label[for="login-email"]').text()).toBe('Email address')
    expect(wrapper.get('label[for="login-password"]').text()).toBe('Password')
    expect(email.attributes('tabindex')).toBeUndefined()
    expect(password.attributes('tabindex')).toBeUndefined()

    await email.setValue('invalid-email')
    expect(email.attributes('aria-invalid')).toBe('true')
    expect(email.attributes('aria-describedby')).toBe('login-email-error')
    expect(wrapper.get('#login-email-error').attributes('role')).toBe('alert')
    wrapper.unmount()
  })

  it('shows account lockout as a form notice without inventing a countdown', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/auth/google/config') {
        return { code: '0000', data: { enabled: false } }
      }
      if (path === '/api/v1/auth/email-login') {
        return {
          code: '1001',
          data: {},
          error: {
            error_code: 'ACCOUNT_LOCKED',
            fields: { email: ['Backend lockout detail'] },
          },
        }
      }
      throw new Error(`Unexpected API path: ${path}`)
    })

    const wrapper = await mountLogin(1440)
    await fillCredentials(wrapper)
    wrapper.getComponent(AuthTurnstileFieldStub).vm.$emit('success', 'verified-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('button.submit-btn').trigger('click')
    await flushPromises()

    const notice = wrapper.get('.login-form-alert')
    expect(notice.text()).toContain('Account temporarily locked')
    expect(notice.text()).toContain('Try again in a few minutes')
    expect(notice.text()).not.toContain('attempts left')
    expect(notice.text()).not.toMatch(/\d{1,2}:\d{2}/)
    wrapper.unmount()
  })

  it('keeps the password tab selected when Turnstile is blocked', async () => {
    mocks.turnstileBlocked = true
    mocks.fetchDeployProfile.mockResolvedValue({
      email_signup_enabled: false,
      email_code_login_available: true,
      password_reset_available: false,
    })

    const wrapper = await mountLogin(1440)
    const tabs = wrapper.findAll('.login-method-tabs__tab')

    expect(tabs).toHaveLength(2)
    expect(tabs[0].classes()).toContain('is-active')
    expect(tabs[1].classes()).not.toContain('is-active')
    expect(tabs[0].attributes('aria-selected')).toBe('true')
    expect(tabs[0].attributes('aria-controls')).toBe('login-method-panel')
    expect(wrapper.get('#login-method-panel').attributes('aria-labelledby')).toBe('login-method-tab-password')
    expect(wrapper.findComponent({ name: 'EmailCodeLoginForm' }).exists()).toBe(false)

    await tabs[1].trigger('click')
    expect(tabs[1].classes()).toContain('is-active')
    expect(tabs[1].attributes('aria-selected')).toBe('true')
    expect(wrapper.get('#login-method-panel').attributes('aria-labelledby')).toBe('login-method-tab-email-code')
    expect(wrapper.findComponent({ name: 'EmailCodeLoginForm' }).exists()).toBe(true)
    wrapper.unmount()
  })

  it('switches login methods with tab-list keyboard navigation', async () => {
    mocks.fetchDeployProfile.mockResolvedValue({
      email_signup_enabled: false,
      email_code_login_available: true,
      password_reset_available: false,
    })

    const wrapper = await mountLogin(1440)
    const tabs = wrapper.findAll('.login-method-tabs__tab')

    await tabs[0].trigger('keydown', { key: 'ArrowRight' })
    await wrapper.vm.$nextTick()
    expect(tabs[1].attributes('aria-selected')).toBe('true')
    expect((tabs[1].element as HTMLButtonElement).ownerDocument.activeElement).toBe(tabs[1].element)
    wrapper.unmount()
  })

  it('keeps the forgot-password action visible in email-code mode', async () => {
    mocks.fetchDeployProfile.mockResolvedValue({
      email_signup_enabled: false,
      email_code_login_available: true,
      password_reset_available: true,
    })

    const wrapper = await mountLogin(1440)
    const tabs = wrapper.findAll('.login-method-tabs__tab')

    expect(wrapper.get('.forgot-link').text()).toBe('Forgot Password?')
    await tabs[1].trigger('click')
    expect(wrapper.get('.forgot-link').text()).toBe('Forgot Password?')
    wrapper.unmount()
  })

  it('keeps sign-in clickable so empty credentials receive validation feedback', async () => {
    const wrapper = await mountLogin(1440)
    const inputs = wrapper.findAll('input')
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)
    const submit = wrapper.get('button.submit-btn')

    turnstile.vm.$emit('success', 'verified-token')
    await wrapper.vm.$nextTick()
    expect(submit.attributes('disabled')).toBeUndefined()
    await submit.trigger('click')
    await flushPromises()
    expect(emailLoginCalls()).toHaveLength(0)
    expect(wrapper.findAll('.input-wrapper.has-error')).toHaveLength(2)
    expect(wrapper.get('form.login-password-form').classes()).toContain('is-validation-shaking')

    await inputs[0].setValue('person@example.com')
    expect(submit.attributes('disabled')).toBeUndefined()

    await inputs[1].setValue('invalid')
    expect(submit.attributes('disabled')).toBeUndefined()
    expect(emailLoginCalls()).toHaveLength(0)

    wrapper.unmount()
  })

  it('keeps credential submission locked until route navigation completes', async () => {
    const navigation = deferred<void>()
    mocks.routerPush.mockReturnValueOnce(navigation.promise)
    const wrapper = await mountLogin(1440)
    await fillCredentials(wrapper)
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)
    const submit = wrapper.get('button.submit-btn')

    turnstile.vm.$emit('success', 'verified-token')
    await wrapper.vm.$nextTick()
    await submit.trigger('click')
    await vi.waitFor(() => expect(mocks.routerPush).toHaveBeenCalledTimes(1))

    expect(submit.attributes('disabled')).toBeDefined()
    expect(submit.text()).toContain('Entering HyperFileLens')
    await submit.trigger('click')
    expect(emailLoginCalls()).toHaveLength(1)

    navigation.resolve(undefined)
    await flushPromises()
    wrapper.unmount()
  })

  it('shows navigation recovery without reopening credential submission', async () => {
    mocks.routerPush
      .mockResolvedValueOnce(new Error('lazy route failed'))
      .mockResolvedValueOnce(undefined)
    mocks.confirmCurrentSession.mockResolvedValue({
      state: 'authenticated',
      user: successfulLoginResponse.data.user,
    })
    const wrapper = await mountLogin(1440)
    await fillCredentials(wrapper)
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)
    turnstile.vm.$emit('success', 'verified-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('button.submit-btn').trigger('click')
    await flushPromises()

    expect(wrapper.get('.login-recovery__title').text()).toBe("You're signed in")
    expect(wrapper.find('#login-method-panel').exists()).toBe(false)
    expect(emailLoginCalls()).toHaveLength(1)

    await wrapper.get('.login-recovery button').trigger('click')
    await flushPromises()

    expect(mocks.confirmCurrentSession).toHaveBeenCalledTimes(1)
    expect(mocks.routerPush).toHaveBeenCalledTimes(2)
    expect(emailLoginCalls()).toHaveLength(1)
    wrapper.unmount()
  })

  it('keeps an unknown session locked until it can be confirmed', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/auth/google/config') {
        return { code: '0000', data: { enabled: false } }
      }
      if (path === '/api/v1/auth/email-login') {
        throw { status: 0, errorCode: 'NETWORK.UNAVAILABLE' }
      }
      throw new Error(`Unexpected API path: ${path}`)
    })
    const wrapper = await mountLogin(1440)
    await fillCredentials(wrapper)
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)

    turnstile.vm.$emit('success', 'verified-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('button.submit-btn').trigger('click')
    await flushPromises()

    expect(wrapper.get('.login-recovery__title').text()).toBe('Sign-in status unavailable')
    expect(wrapper.find('#login-method-panel').exists()).toBe(false)
    expect(mocks.confirmCurrentSession).toHaveBeenCalledTimes(1)
    expect(emailLoginCalls()).toHaveLength(1)

    await wrapper.get('.login-recovery button').trigger('click')
    await flushPromises()
    expect(mocks.confirmCurrentSession).toHaveBeenCalledTimes(2)
    expect(emailLoginCalls()).toHaveLength(1)
    wrapper.unmount()
  })

  it('restores credential submission only after confirming no active session', async () => {
    mocks.routerPush.mockResolvedValueOnce(new Error('navigation cancelled'))
    mocks.confirmCurrentSession.mockResolvedValue({ state: 'unauthenticated' })
    const wrapper = await mountLogin(1440)
    await fillCredentials(wrapper)
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)

    turnstile.vm.$emit('success', 'verified-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('button.submit-btn').trigger('click')
    await flushPromises()
    await wrapper.get('.login-recovery button').trigger('click')
    await flushPromises()

    expect(wrapper.find('.login-recovery').exists()).toBe(false)
    expect(wrapper.get('#login-method-panel').exists()).toBe(true)
    expect(wrapper.get('button.submit-btn').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('syncs a language explicitly selected during password sign-in', async () => {
    const wrapper = await mountLogin(1440)
    const languageSwitcher = wrapper.getComponent({ name: 'LanguageSwitcher' })
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)

    languageSwitcher.vm.$emit('change', 'zh-hans')
    await fillCredentials(wrapper)
    turnstile.vm.$emit('success', 'verified-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('button.submit-btn').trigger('click')
    await flushPromises()

    expect(mocks.setUser).toHaveBeenCalledWith(successfulLoginResponse.data.user)
    expect(mocks.syncAuthenticatedLocale).toHaveBeenCalledTimes(1)
    expect(mocks.syncAuthenticatedLocale).toHaveBeenCalledWith('zh-hans')
    wrapper.unmount()
  })

  it('does not override the profile language without an explicit login-page selection', async () => {
    const wrapper = await mountLogin(1440)
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)

    await fillCredentials(wrapper)
    turnstile.vm.$emit('success', 'verified-token')
    await wrapper.vm.$nextTick()
    await wrapper.get('button.submit-btn').trigger('click')
    await flushPromises()

    expect(mocks.syncAuthenticatedLocale).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('syncs the explicit language after email-code authentication loads the user', async () => {
    mocks.fetchDeployProfile.mockResolvedValue({
      email_signup_enabled: false,
      email_code_login_available: true,
      password_reset_available: false,
    })
    mocks.fetchCurrentUser.mockResolvedValue({
      id: 1,
      email: 'person@example.com',
      username: 'person',
      language: 'en',
    })
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/auth/google/config') {
        return { code: '0000', data: { enabled: false } }
      }
      if (path === '/api/v1/auth/org-select') {
        return { code: '0000', data: {} }
      }
      throw new Error(`Unexpected API path: ${path}`)
    })

    const wrapper = await mountLogin(1440)
    wrapper.getComponent({ name: 'LanguageSwitcher' }).vm.$emit('change', 'zh-hans')
    await wrapper.findAll('.login-method-tabs__tab')[1].trigger('click')
    const emailCodeForm = wrapper.getComponent({ name: 'EmailCodeLoginForm' })

    emailCodeForm.vm.$emit('verified', {
      available_orgs: [{ org_key: 'org-1', org_name: 'Organization', role: 'member' }],
    })
    await flushPromises()

    expect(mocks.fetchCurrentUser).toHaveBeenCalledTimes(1)
    expect(mocks.syncAuthenticatedLocale).toHaveBeenCalledWith('zh-hans')
    expect(mocks.fetchCurrentUser.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.syncAuthenticatedLocale.mock.invocationCallOrder[0],
    )
    wrapper.unmount()
  })

  it('rejects an invalid email format before password login', async () => {
    const wrapper = await mountLogin(1440)
    const inputs = wrapper.findAll('input')
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)
    const submit = wrapper.get('button.submit-btn')

    turnstile.vm.$emit('success', 'verified-token')
    await inputs[0].setValue('invalid-email')
    await inputs[1].setValue('ValidPass123')

    expect(wrapper.get('.input-wrapper.has-error .error-msg').text()).toBe('Invalid email format')
    expect(submit.attributes('disabled')).toBeUndefined()

    await inputs[1].trigger('keyup.enter')
    await flushPromises()
    expect(emailLoginCalls()).toHaveLength(0)

    await inputs[0].setValue('person@example.com')
    expect(wrapper.find('.input-wrapper.has-error').exists()).toBe(false)
    expect(submit.attributes('disabled')).toBeUndefined()

    wrapper.unmount()
  })

  it.each([
    ['short credential', 'short'],
    ['credential longer than the creation limit', 'This-credential-is-longer-than-twenty-characters'],
    ['credential containing spaces and symbols', 'Credential value ! forwarded'],
  ])('forwards a %s unchanged for server-side authentication', async (_name, password) => {
    const wrapper = await mountLogin(1440)
    const inputs = wrapper.findAll('input')
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)

    mocks.api.mockResolvedValue({
      code: '1001',
      data: {},
      error: {
        error_code: 'INVALID_PASSWORD',
        fields: { password: ['Incorrect password'] },
      },
    })

    await inputs[0].setValue('person@example.com')
    await inputs[1].setValue(password)
    expect(wrapper.find('.strength-bar-wrapper').exists()).toBe(false)

    turnstile.vm.$emit('success', 'verified-token')
    await wrapper.vm.$nextTick()
    await inputs[1].trigger('keyup.enter')
    await flushPromises()

    expect(emailLoginCalls()).toHaveLength(1)
    expect(submittedBody(emailLoginCalls()[0])).toMatchObject({
      email: 'person@example.com',
      password,
      turnstile_token: 'verified-token',
    })
    expect(wrapper.get('.input-wrapper.has-error .error-msg').text()).toBe('Incorrect password')
    wrapper.unmount()
  })

  it.each([
    ['mobile', 390],
    ['tablet', 820],
    ['desktop', 1440],
  ])('recovers from invalidation and repeated expiration on %s', async (_name, width) => {
    const wrapper = await mountLogin(width)
    const credentials = await fillCredentials(wrapper)
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)
    const submit = wrapper.get('button.submit-btn')

    expect(submit.attributes('disabled')).toBeUndefined()

    turnstile.vm.$emit('success', 'initial-token')
    await wrapper.vm.$nextTick()
    expect(submit.attributes('disabled')).toBeUndefined()

    turnstile.vm.$emit('invalidate')
    await wrapper.vm.$nextTick()
    expect(submit.attributes('disabled')).toBeUndefined()
    expect(turnstile.props('errorMessage')).toBe('')
    expect(credentials.email.value).toBe('person@example.com')
    expect(credentials.password.value).toBe('ValidPass123')

    turnstile.vm.$emit('success', 'language-refresh-token')
    await wrapper.vm.$nextTick()
    turnstile.vm.$emit('expire')
    await wrapper.vm.$nextTick()
    expect(submit.attributes('disabled')).toBeUndefined()
    expect(turnstile.props('errorMessage')).toBe(
      'Human verification expired. Please complete the new challenge.',
    )

    turnstile.vm.$emit('success', 'replacement-token')
    await wrapper.vm.$nextTick()
    turnstile.vm.$emit('expire')
    await wrapper.vm.$nextTick()
    turnstile.vm.$emit('success', 'final-token')
    await wrapper.vm.$nextTick()
    expect(submit.attributes('disabled')).toBeUndefined()

    await submit.trigger('click')
    await flushPromises()

    expect(emailLoginCalls()).toHaveLength(1)
    expect(submittedBody(emailLoginCalls()[0])).toMatchObject({
      email: 'person@example.com',
      password: 'ValidPass123',
      turnstile_token: 'final-token',
    })
    expect(credentials.email.value).toBe('person@example.com')
    expect(credentials.password.value).toBe('ValidPass123')
    wrapper.unmount()
  })

  it('accepts a new token after the backend rejects an expired token', async () => {
    let loginAttempt = 0
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/auth/google/config') {
        return { code: '0000', data: { enabled: false } }
      }
      if (path === '/api/v1/auth/email-login') {
        loginAttempt += 1
        if (loginAttempt === 1) {
          throw {
            status: 400,
            message: 'Invalid or expired human verification',
            fields: {
              turnstile_token: ['Invalid or expired human verification'],
            },
          }
        }
        return successfulLoginResponse
      }
      throw new Error(`Unexpected API path: ${path}`)
    })

    const wrapper = await mountLogin(1440)
    await fillCredentials(wrapper)
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)
    const submit = wrapper.get('button.submit-btn')

    turnstile.vm.$emit('success', 'rejected-token')
    await wrapper.vm.$nextTick()
    await submit.trigger('click')
    await flushPromises()

    expect(mocks.resetWidget).toHaveBeenCalledTimes(1)
    expect(submit.attributes('disabled')).toBeUndefined()
    expect(turnstile.props('errorMessage')).toBe('Human verification failed or expired')

    turnstile.vm.$emit('success', 'accepted-token')
    await wrapper.vm.$nextTick()
    expect(submit.attributes('disabled')).toBeUndefined()

    await submit.trigger('click')
    await flushPromises()

    expect(emailLoginCalls()).toHaveLength(2)
    expect(submittedBody(emailLoginCalls()[1]).turnstile_token).toBe('accepted-token')
    wrapper.unmount()
  })

  it('clears a token and fully reloads Turnstile on manual retry', async () => {
    const wrapper = await mountLogin(1440)
    await fillCredentials(wrapper)
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)
    const submit = wrapper.get('button.submit-btn')

    turnstile.vm.$emit('success', 'verified-token')
    await wrapper.vm.$nextTick()
    expect(turnstile.props('verified')).toBe(true)
    expect(submit.attributes('disabled')).toBeUndefined()

    turnstile.vm.$emit('retry')
    await flushPromises()

    expect(mocks.retryTurnstileConfig).toHaveBeenCalledTimes(1)
    expect(turnstile.props('verified')).toBe(false)
    expect(submit.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('shows and clears a safe Turnstile reference code', async () => {
    const wrapper = await mountLogin(1440)
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)

    turnstile.vm.$emit('error', '300030')
    await wrapper.vm.$nextTick()

    expect(turnstile.props('errorCodeLabel')).toBe('Reference code: 300030')
    expect(mocks.blockTurnstile).toHaveBeenCalledTimes(1)

    turnstile.vm.$emit('retry')
    await flushPromises()

    expect(turnstile.props('errorCodeLabel')).toBe('')
    wrapper.unmount()
  })

  it('accepts corrected credentials after a password error resets Turnstile', async () => {
    let loginAttempt = 0
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/auth/google/config') {
        return { code: '0000', data: { enabled: false } }
      }
      if (path === '/api/v1/auth/email-login') {
        loginAttempt += 1
        if (loginAttempt === 1) {
          return {
            code: '1001',
            data: {},
            error: {
              fields: {
                password: ['Incorrect password'],
              },
            },
          }
        }
        return successfulLoginResponse
      }
      throw new Error(`Unexpected API path: ${path}`)
    })

    const wrapper = await mountLogin(1440)
    const credentials = await fillCredentials(wrapper)
    const turnstile = wrapper.getComponent(AuthTurnstileFieldStub)
    const submit = wrapper.get('button.submit-btn')

    turnstile.vm.$emit('success', 'initial-token')
    await wrapper.vm.$nextTick()
    await submit.trigger('click')
    await flushPromises()

    expect(mocks.resetWidget).toHaveBeenCalledTimes(1)
    expect(submit.attributes('disabled')).toBeUndefined()
    expect(wrapper.get('.input-wrapper.has-error .error-msg').text()).toBe('Incorrect password')

    await wrapper.findAll('input')[1].setValue('CorrectPass123')
    turnstile.vm.$emit('success', 'replacement-token')
    await wrapper.vm.$nextTick()

    expect(submit.attributes('disabled')).toBeUndefined()
    await submit.trigger('click')
    await flushPromises()

    expect(emailLoginCalls()).toHaveLength(2)
    expect(submittedBody(emailLoginCalls()[0])).toMatchObject({
      password: 'ValidPass123',
      turnstile_token: 'initial-token',
    })
    expect(submittedBody(emailLoginCalls()[1])).toMatchObject({
      password: 'CorrectPass123',
      turnstile_token: 'replacement-token',
    })
    expect(credentials.password.value).toBe('CorrectPass123')
    wrapper.unmount()
  })
})
