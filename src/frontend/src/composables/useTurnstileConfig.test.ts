// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  isAbortError: vi.fn(() => false),
  preloadTurnstileScript: vi.fn(),
  resetTurnstileScriptLoad: vi.fn(),
}))

vi.mock('../lib/api', () => ({
  api: mocks.api,
  isAbortError: mocks.isAbortError,
}))
vi.mock('../lib/turnstileLoader', () => ({
  preloadTurnstileScript: mocks.preloadTurnstileScript,
  resetTurnstileScriptLoad: mocks.resetTurnstileScriptLoad,
}))

describe('Turnstile configuration retry', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    mocks.isAbortError.mockReturnValue(false)
    mocks.preloadTurnstileScript.mockResolvedValue(undefined)
  })

  it('recovers after one transient configuration failure', async () => {
    mocks.api
      .mockRejectedValueOnce(new Error('network unavailable'))
      .mockResolvedValueOnce({
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
    expect(mocks.api).toHaveBeenCalledTimes(2)
    expect(turnstile.turnstileSiteKey.value).toBe('test-site-key')
    expect(mocks.preloadTurnstileScript).toHaveBeenCalledTimes(1)
  })

  it('keeps retry fail-closed when the configuration request still fails', async () => {
    mocks.api.mockRejectedValue(new Error('network unavailable'))

    const { useTurnstileConfig } = await import('./useTurnstileConfig')
    const turnstile = useTurnstileConfig()

    await turnstile.loadTurnstileConfig()

    expect(mocks.api).toHaveBeenCalledTimes(2)
    expect(turnstile.isTurnstileConfigError.value).toBe(true)
    expect(turnstile.turnstileSiteKey.value).toBe('')
  })

  it('does not surface an error when the request is aborted', async () => {
    const aborted = new Error('route changed')
    aborted.name = 'AbortError'
    mocks.api.mockRejectedValue(aborted)
    mocks.isAbortError.mockReturnValue(true)

    const { useTurnstileConfig } = await import('./useTurnstileConfig')
    const turnstile = useTurnstileConfig()

    await turnstile.loadTurnstileConfig()

    expect(turnstile.isTurnstileConfigError.value).toBe(false)
    expect(turnstile.isTurnstilePending.value).toBe(true)
    expect(mocks.api).toHaveBeenCalledTimes(1)
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

  it('reloads configuration after a forced retry is aborted', async () => {
    mocks.api.mockResolvedValueOnce({
      code: '0000',
      data: { enabled: true, configured: true, site_key: 'test-site-key' },
    })

    const { useTurnstileConfig } = await import('./useTurnstileConfig')
    const turnstile = useTurnstileConfig()
    await turnstile.loadTurnstileConfig()

    const aborted = new Error('route changed')
    aborted.name = 'AbortError'
    mocks.api.mockRejectedValueOnce(aborted)
    mocks.isAbortError.mockReturnValue(true)
    await turnstile.retryTurnstileConfig()

    mocks.isAbortError.mockReturnValue(false)
    mocks.api.mockResolvedValueOnce({
      code: '0000',
      data: { enabled: false, configured: false },
    })
    await turnstile.loadTurnstileConfig()

    expect(turnstile.isTurnstileDisabled.value).toBe(true)
    expect(mocks.api).toHaveBeenCalledTimes(3)
  })
})
