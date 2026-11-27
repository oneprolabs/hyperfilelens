// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./requestContext', () => ({
  getCorrelationHeaders: () => ({}),
}))

import { refreshAuthToken } from './authRefresh'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('coordinated auth refresh', () => {
  beforeEach(() => {
    window.localStorage.clear()
    Object.defineProperty(window.navigator, 'locks', {
      configurable: true,
      value: undefined,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    window.localStorage.clear()
  })

  it('coalesces refresh callers in the same tab', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        authenticated: false,
        refresh_available: true,
      }))
      .mockResolvedValueOnce(jsonResponse({ code: '0000' }))
    vi.stubGlobal('fetch', fetchMock)

    const [first, second] = await Promise.all([
      refreshAuthToken(),
      refreshAuthToken(),
    ])

    expect(first).toEqual({ ok: true, status: 200 })
    expect(second).toEqual(first)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })

  it('serializes refreshes from separate tabs with the browser lock', async () => {
    let lockTail = Promise.resolve<unknown>(undefined)
    const lockRequest = vi.fn((
      _name: string,
      _options: LockOptions,
      callback: () => Promise<unknown>,
    ) => {
      const result = lockTail.then(callback)
      lockTail = result.catch(() => undefined)
      return result
    })
    Object.defineProperty(window.navigator, 'locks', {
      configurable: true,
      value: { request: lockRequest },
    })

    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        authenticated: false,
        refresh_available: true,
      }))
      .mockResolvedValueOnce(jsonResponse({ code: '0000' }))
      .mockResolvedValueOnce(jsonResponse({
        authenticated: true,
        refresh_available: true,
      }))
    vi.stubGlobal('fetch', fetchMock)

    vi.resetModules()
    const firstTab = await import('./authRefresh')
    vi.resetModules()
    const secondTab = await import('./authRefresh')

    const [first, second] = await Promise.all([
      firstTab.refreshAuthToken(),
      secondTab.refreshAuthToken(),
    ])

    expect(first).toEqual({ ok: true, status: 200 })
    expect(second).toEqual({ ok: true, status: 200 })
    expect(lockRequest).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })

  it('settles all waiters on a stalled refresh and allows a later attempt', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ authenticated: false, refresh_available: true }))
      .mockReturnValueOnce(new Promise(() => {}))
      .mockResolvedValueOnce(jsonResponse({ authenticated: true, refresh_available: true }))
    vi.stubGlobal('fetch', fetchMock)

    const first = refreshAuthToken()
    expect(refreshAuthToken()).toBe(first)
    await vi.advanceTimersByTimeAsync(12_000)
    await expect(first).resolves.toEqual({ ok: false, networkError: true })
    expect(fetchMock.mock.calls[1]?.[1].signal.aborted).toBe(true)
    await expect(refreshAuthToken()).resolves.toMatchObject({ ok: true })
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })

  it('waits for the winning refresh when the backend reports concurrency', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        authenticated: false,
        refresh_available: true,
      }))
      .mockResolvedValueOnce(jsonResponse({
        code: '1001',
        error: { error_code: 'REFRESH_CONCURRENT' },
      }, 409))
      .mockResolvedValueOnce(jsonResponse({
        authenticated: true,
        refresh_available: true,
      }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(refreshAuthToken()).resolves.toEqual({ ok: true, status: 200 })
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('keeps an ambiguous network failure local until the backend confirms replay', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        authenticated: false,
        refresh_available: true,
      }))
      .mockRejectedValueOnce(new TypeError('network unavailable'))
      .mockResolvedValueOnce(jsonResponse({
        authenticated: false,
        refresh_available: true,
      }))
      .mockResolvedValueOnce(jsonResponse({
        code: '1001',
        error: { error_code: 'TOKEN_REUSED' },
      }, 401))
    vi.stubGlobal('fetch', fetchMock)

    await expect(refreshAuthToken()).resolves.toEqual({
      ok: false,
      networkError: true,
    })
    await expect(refreshAuthToken()).resolves.toEqual({
      ok: false,
      errorCode: 'TOKEN_REUSED',
      status: 401,
    })
  })
})
