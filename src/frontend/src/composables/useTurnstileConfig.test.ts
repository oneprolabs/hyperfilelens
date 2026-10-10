// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  preloadTurnstileScript: vi.fn(),
  resetTurnstileScriptLoad: vi.fn(),
}))

vi.mock('../lib/api', () => ({ api: mocks.api }))
vi.mock('../lib/turnstileLoader', () => ({
  preloadTurnstileScript: mocks.preloadTurnstileScript,
  resetTurnstileScriptLoad: mocks.resetTurnstileScriptLoad,
}))

describe('Turnstile configuration visibility', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    mocks.preloadTurnstileScript.mockResolvedValue(undefined)
  })

  it('shows the challenge when Turnstile is enabled and configured', async () => {
    mocks.api.mockResolvedValue({
      code: '0000',
      data: {
        enabled: true,
        configured: true,
        site_key: 'test-site-key',
      },
    })

    const { useTurnstileConfig } = await import('./useTurnstileConfig')
    const turnstile = useTurnstileConfig()

    await turnstile.loadTurnstileConfig()
    expect(turnstile.isTurnstileReady.value).toBe(true)
    expect(mocks.api).toHaveBeenCalledTimes(1)
    expect(turnstile.turnstileSiteKey.value).toBe('test-site-key')
    expect(mocks.preloadTurnstileScript).toHaveBeenCalledTimes(1)
  })

  it('keeps the optional field hidden when configuration cannot be loaded', async () => {
    mocks.api.mockRejectedValue(new Error('network unavailable'))

    const { useTurnstileConfig } = await import('./useTurnstileConfig')
    const turnstile = useTurnstileConfig()

    await turnstile.loadTurnstileConfig()

    expect(mocks.api).toHaveBeenCalledTimes(1)
    expect(turnstile.isTurnstileDisabled.value).toBe(true)
    expect(turnstile.turnstileSiteKey.value).toBe('')
    expect(mocks.preloadTurnstileScript).not.toHaveBeenCalled()
  })

  it('shows an unavailable state when Turnstile is enabled without complete keys', async () => {
    mocks.api.mockResolvedValue({
      code: '0000',
      data: { enabled: true, configured: false },
    })

    const { useTurnstileConfig } = await import('./useTurnstileConfig')
    const turnstile = useTurnstileConfig()

    await turnstile.loadTurnstileConfig()

    expect(turnstile.isTurnstileBlocked.value).toBe(true)
    expect(mocks.api).toHaveBeenCalledTimes(1)
    expect(mocks.preloadTurnstileScript).not.toHaveBeenCalled()
  })

  it('does not load the Cloudflare script when Turnstile is disabled', async () => {
    mocks.api.mockResolvedValue({
      code: '0000',
      data: { enabled: false, configured: false },
    })

    const { useTurnstileConfig } = await import('./useTurnstileConfig')
    const turnstile = useTurnstileConfig()

    await turnstile.loadTurnstileConfig()

    expect(turnstile.isTurnstileDisabled.value).toBe(true)
    expect(mocks.preloadTurnstileScript).not.toHaveBeenCalled()
  })

  it('hides verification and omits tokens only for an exempt password login', async () => {
    mocks.api.mockResolvedValue({
      code: '0000',
      data: {
        enabled: true, configured: true, site_key: 'test-site-key', login_exempt: true,
      },
    })
    const { useTurnstileConfig } = await import('./useTurnstileConfig')
    const login = useTurnstileConfig('login')
    await login.loadTurnstileConfig()
    expect(login.isTurnstileDisabled.value).toBe(true)
    expect(login.isTurnstileReady.value).toBe(false)
    expect(login.turnstileSiteKey.value).toBe('')
    expect(login.buildTurnstilePayload('')).toEqual({})
    expect(mocks.preloadTurnstileScript).not.toHaveBeenCalled()
    const registration = useTurnstileConfig()
    expect(registration.isTurnstileReady.value).toBe(true)
    expect(registration.turnstileSiteKey.value).toBe('test-site-key')
    expect(registration.buildTurnstilePayload('register-token')).toEqual({
      turnstile_token: 'register-token',
    })
  })

  it('keeps verification for a non-exempt password login', async () => {
    mocks.api.mockResolvedValue({
      code: '0000',
      data: { enabled: true, configured: true, site_key: 'test-site-key', login_exempt: false },
    })
    const { useTurnstileConfig } = await import('./useTurnstileConfig')
    const login = useTurnstileConfig('login')
    await login.loadTurnstileConfig()
    expect(login.isTurnstileReady.value).toBe(true)
    expect(login.buildTurnstilePayload('login-token')).toEqual({ turnstile_token: 'login-token' })
  })

  it('re-evaluates an exemption when navigating back to authentication', async () => {
    mocks.api
      .mockResolvedValueOnce({
        code: '0000',
        data: { enabled: true, configured: true, site_key: 'key', login_exempt: true },
      })
      .mockResolvedValueOnce({
        code: '0000',
        data: { enabled: true, configured: true, site_key: 'key', login_exempt: false },
      })
    const { useTurnstileConfig, prefetchAuthTurnstile } = await import('./useTurnstileConfig')
    const login = useTurnstileConfig('login')
    await login.loadTurnstileConfig()
    expect(login.isTurnstileDisabled.value).toBe(true)
    prefetchAuthTurnstile()
    await login.loadTurnstileConfig()
    expect(mocks.api).toHaveBeenCalledTimes(2)
    expect(login.isTurnstileReady.value).toBe(true)
  })

  it('retries a failed request only when configuration is requested again', async () => {
    mocks.api
      .mockRejectedValueOnce(new Error('network unavailable'))
      .mockResolvedValueOnce({
        code: '0000',
        data: { enabled: true, configured: true, site_key: 'test-site-key' },
      })

    const { useTurnstileConfig } = await import('./useTurnstileConfig')
    const turnstile = useTurnstileConfig()
    await turnstile.loadTurnstileConfig()
    await turnstile.loadTurnstileConfig()

    expect(turnstile.isTurnstileReady.value).toBe(true)
    expect(mocks.api).toHaveBeenCalledTimes(2)
  })
})
