// @vitest-environment jsdom
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import ImpersonationEntry from './ImpersonationEntry.vue'
import { en } from '../../locales/en'
import { fetchDeployProfile } from '../../composables/useDeployProfile'
import { impersonationContext, returnToAdmin } from '../../lib/impersonation'

vi.mock('../../composables/useDeployProfile', () => ({ fetchDeployProfile: vi.fn() }))
vi.mock('../../lib/impersonation', async importOriginal => {
  const original = await importOriginal<typeof import('../../lib/impersonation')>()
  return { ...original, returnToAdmin: vi.fn() }
})

const id = '9ad09efb-cf40-4d00-8343-56a60eeeb329'
const adminUrl = 'https://admin.example.test/platform-ops/users'
const info = { id, user_id: 20, org_key: 'personal', landing_path: '/overview' }
let wrapper: VueWrapper | undefined
let opener: { postMessage: ReturnType<typeof vi.fn> }
let replace: ReturnType<typeof vi.fn>
let fetchMock: ReturnType<typeof vi.fn>

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify({ code: 0, data }), { status, headers: { 'Content-Type': 'application/json' } })
}

function render() {
  wrapper = mount(ImpersonationEntry, {
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      stubs: { ElButton: { template: '<button><slot /></button>' } },
    },
  })
  return wrapper
}

function handoff(origin = 'https://admin.example.test', source: unknown = opener) {
  window.dispatchEvent(new MessageEvent('message', {
    origin, source: source as Window,
    data: { type: 'hfl-impersonation-start', id, handoff_code: 'one-use-code' },
  }))
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  sessionStorage.clear()
  impersonationContext.value = null
  opener = { postMessage: vi.fn() }
  replace = vi.fn()
  fetchMock = vi.fn()
  vi.stubGlobal('opener', opener)
  vi.stubGlobal('location', { href: 'http://localhost:3000/impersonation/entry', origin: 'http://localhost:3000', pathname: '/impersonation/entry', replace })
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('crypto', {
    getRandomValues: (bytes: Uint8Array) => bytes.fill(7),
    subtle: { digest: vi.fn().mockResolvedValue(new Uint8Array(32).fill(9).buffer) },
  })
  vi.mocked(fetchDeployProfile).mockReset()
  vi.mocked(fetchDeployProfile).mockResolvedValue({ admin_console_url: adminUrl } as Awaited<ReturnType<typeof fetchDeployProfile>>)
  fetchMock.mockResolvedValueOnce(json(info))
    .mockResolvedValueOnce(json({ user: { id: 20, access_profile: { org_key: 'personal' } } }))
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  impersonationContext.value = null
  sessionStorage.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('impersonation entry state', () => {
  it('shows an accessible loading state without buttons, tooltips, or session details', async () => {
    const page = render()
    await flushPromises()
    expect(page.get('h1').text()).toBe('Starting Impersonation')
    expect(page.get('p').text()).toBe('Please wait while we prepare your session.')
    expect(page.attributes('aria-labelledby')).toBe(page.get('h1').attributes('id'))
    expect(page.attributes('aria-describedby')).toBe(page.get('p').attributes('id'))
    expect(page.get('[role="status"]').attributes()).toMatchObject({ 'aria-live': 'polite', 'aria-atomic': 'true' })
    expect(page.get('svg').attributes()).toMatchObject({ width: '32', 'stroke-width': '1.75', 'aria-hidden': 'true', focusable: 'false' })
    expect(page.find('.impersonation-entry__icon--loading').exists()).toBe(true)
    expect(page.find('button, [title], [role="tooltip"]').exists()).toBe(false)
    expect(opener.postMessage).toHaveBeenCalledExactlyOnceWith({
      type: 'hfl-impersonation-ready', challenge: '09'.repeat(32),
    }, 'https://admin.example.test')
  })

  it('requires the original window and exact administrator origin, and consumes the handoff only once', async () => {
    render()
    await flushPromises()
    handoff('https://other.example.test')
    handoff('https://admin.example.test', {})
    await flushPromises()
    expect(fetchMock).not.toHaveBeenCalled()
    handoff()
    handoff()
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const [url, request] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/v1/impersonation/exchange')
    expect(JSON.parse(request.body)).toEqual({ session_id: id, handoff_code: 'one-use-code', verifier: '07'.repeat(32) })
    expect(request).toMatchObject({ method: 'POST', credentials: 'include' })
    expect(fetchMock.mock.calls[1]![1].headers).toEqual({ 'X-HFL-Impersonation': id, 'X-Org-Key': 'personal' })
    expect(opener.postMessage).toHaveBeenLastCalledWith({ type: 'hfl-impersonation-entered', id }, 'https://admin.example.test')
    expect(replace).toHaveBeenCalledExactlyOnceWith(`http://localhost:3000/overview?impersonation=${id}`)
    expect(window.opener).toBeNull()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(impersonationContext.value?.ended).not.toBe(true)
  })

  it.each(['//outside.test/path', '/platform-ops/users'])('retains the safe landing fallback for %s', async landing => {
    fetchMock.mockReset().mockResolvedValueOnce(json({ ...info, landing_path: landing }))
      .mockResolvedValueOnce(json({ user: { id: 20, access_profile: { org_key: 'personal' } } }))
    render()
    await flushPromises()
    handoff()
    await flushPromises()
    expect(replace).toHaveBeenCalledExactlyOnceWith(`http://localhost:3000/?impersonation=${id}`)
  })

  it('times out waiting for a handoff, removes the listener, and returns only on an explicit click', async () => {
    const page = render()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(page.get('h1').text()).toBe('Unable to Start Impersonation')
    expect(page.get('p').text()).toBe('Return to the Admin Console and start the session again.')
    expect(page.find('.impersonation-entry__icon--loading').exists()).toBe(false)
    expect(opener.postMessage).toHaveBeenLastCalledWith({
      type: 'hfl-impersonation-failed', id: null,
    }, 'https://admin.example.test')
    expect(returnToAdmin).not.toHaveBeenCalled()
    handoff()
    await flushPromises()
    expect(fetchMock).not.toHaveBeenCalled()
    await page.get('button').trigger('click')
    expect(returnToAdmin).toHaveBeenCalledExactlyOnceWith(adminUrl)
  })

  it('includes profile setup in the deadline and ignores a late profile', async () => {
    const profile = deferred<Awaited<ReturnType<typeof fetchDeployProfile>>>()
    vi.mocked(fetchDeployProfile).mockReturnValue(profile.promise)
    const page = render()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(page.get('h1').text()).toBe('Unable to Start Impersonation')
    expect(page.find('button').exists()).toBe(false)
    profile.resolve({ admin_console_url: adminUrl } as Awaited<ReturnType<typeof fetchDeployProfile>>)
    await flushPromises()
    expect(opener.postMessage).not.toHaveBeenCalled()
    expect(page.find('button').exists()).toBe(false)
  })

  it('includes verifier generation in the deadline and ignores a late digest', async () => {
    const digest = deferred<ArrayBuffer>()
    vi.mocked(crypto.subtle.digest).mockReturnValue(digest.promise)
    const page = render()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(60_000)
    digest.resolve(new Uint8Array(32).buffer)
    await flushPromises()
    expect(page.get('h1').text()).toBe('Unable to Start Impersonation')
    expect(opener.postMessage).toHaveBeenCalledExactlyOnceWith({
      type: 'hfl-impersonation-failed', id: null,
    }, 'https://admin.example.test')
  })

  it('keeps the original deadline after the handoff and ignores a late exchange', async () => {
    const exchange = deferred<Response>()
    fetchMock.mockReset().mockReturnValue(exchange.promise)
    const page = render()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(55_000)
    handoff()
    await flushPromises()
    const signal = fetchMock.mock.calls[0]![1].signal as AbortSignal
    await vi.advanceTimersByTimeAsync(5_000)
    expect(signal.aborted).toBe(true)
    exchange.resolve(json(info))
    await flushPromises()
    expect(page.get('h1').text()).toBe('Unable to Start Impersonation')
    expect(impersonationContext.value).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(replace).not.toHaveBeenCalled()
    expect(opener.postMessage).toHaveBeenCalledTimes(2)
    expect(opener.postMessage).toHaveBeenLastCalledWith({
      type: 'hfl-impersonation-failed', id,
    }, 'https://admin.example.test')
  })

  it('includes response parsing in the deadline without persisting a late context', async () => {
    const body = deferred<{ code: number; data: typeof info }>()
    fetchMock.mockReset().mockResolvedValue({ ok: true, json: () => body.promise })
    const page = render()
    await flushPromises()
    handoff()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(60_000)
    body.resolve({ code: 0, data: info })
    await flushPromises()
    expect(page.get('h1').text()).toBe('Unable to Start Impersonation')
    expect(sessionStorage.getItem('hfl_impersonation_context')).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(replace).not.toHaveBeenCalled()
  })

  it('aborts a hanging confirmation and fences its saved scope against late success', async () => {
    const confirmation = deferred<Response>()
    fetchMock.mockReset().mockResolvedValueOnce(json(info)).mockReturnValueOnce(confirmation.promise)
    const page = render()
    await flushPromises()
    handoff()
    await flushPromises()
    expect(impersonationContext.value?.id).toBe(id)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchMock.mock.calls[1]![1].signal.aborted).toBe(true)
    expect(impersonationContext.value).toMatchObject({ id, ended: true })
    expect(JSON.parse(sessionStorage.getItem('hfl_impersonation_context')!)).toMatchObject({ id, ended: true })
    confirmation.resolve(json({ user: { id: 20, access_profile: { org_key: 'personal' } } }))
    await flushPromises()
    expect(page.get('h1').text()).toBe('Unable to Start Impersonation')
    expect(replace).not.toHaveBeenCalled()
    expect(opener.postMessage).toHaveBeenCalledTimes(2)
    expect(opener.postMessage).toHaveBeenLastCalledWith({
      type: 'hfl-impersonation-failed', id,
    }, 'https://admin.example.test')
  })

  it.each([
    { id: 21, access_profile: { org_key: 'personal' } },
    { id: 20, access_profile: { org_key: 'another-org' } },
  ])('fails closed on an identity or organization mismatch: %j', async user => {
    fetchMock.mockReset().mockResolvedValueOnce(json(info)).mockResolvedValueOnce(json({ user }))
    const page = render()
    await flushPromises()
    handoff()
    await flushPromises()
    expect(page.get('h1').text()).toBe('Unable to Start Impersonation')
    expect(impersonationContext.value).toMatchObject({ id, ended: true })
    expect(replace).not.toHaveBeenCalled()
  })

  it('stops on exchange failure without retrying or saving a context', async () => {
    fetchMock.mockReset().mockResolvedValue(json({}, 403))
    const page = render()
    await flushPromises()
    handoff()
    await flushPromises()
    expect(page.get('h1').text()).toBe('Unable to Start Impersonation')
    handoff()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(impersonationContext.value).toBeNull()
    expect(replace).not.toHaveBeenCalled()
    expect(opener.postMessage).toHaveBeenCalledTimes(2)
    expect(opener.postMessage).toHaveBeenLastCalledWith({
      type: 'hfl-impersonation-failed', id,
    }, 'https://admin.example.test')
    await page.get('button').trigger('click')
    expect(returnToAdmin).toHaveBeenCalledExactlyOnceWith(adminUrl)
  })

  it('can still render recovery when notifying a closed initiator throws', async () => {
    const page = render()
    await flushPromises()
    opener.postMessage.mockImplementation(() => { throw new Error('Initiator unavailable') })
    await vi.advanceTimersByTimeAsync(60_000)
    expect(page.get('h1').text()).toBe('Unable to Start Impersonation')
    await page.get('button').trigger('click')
    expect(returnToAdmin).toHaveBeenCalledExactlyOnceWith(adminUrl)
  })

  it.each(['', '/platform-ops', 'bad-url', 'javascript:alert(1)', 'data:text/html,admin', 'https://user:password@admin.example.test'])(
    'does not advertise a return destination or start a handshake for invalid profile URL %s', async url => {
      vi.mocked(fetchDeployProfile).mockResolvedValue({ admin_console_url: url } as Awaited<ReturnType<typeof fetchDeployProfile>>)
      const page = render()
      await flushPromises()
      expect(page.get('h1').text()).toBe('Unable to Start Impersonation')
      expect(page.find('button').exists()).toBe(false)
      expect(opener.postMessage).not.toHaveBeenCalled()
    },
  )

  it('fails without an initiating window rather than using a URL query as a destination', async () => {
    vi.stubGlobal('opener', null)
    const page = render()
    await flushPromises()
    expect(page.get('h1').text()).toBe('Unable to Start Impersonation')
    expect(page.find('button').exists()).toBe(false)
    expect(fetchDeployProfile).not.toHaveBeenCalled()
  })

  it('fails before exchanging when session storage is blocked', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Blocked') })
    const page = render()
    await flushPromises()
    expect(page.get('h1').text()).toBe('Unable to Start Impersonation')
    expect(fetchDeployProfile).not.toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('does not install a listener after unmounting during setup', async () => {
    const profile = deferred<Awaited<ReturnType<typeof fetchDeployProfile>>>()
    vi.mocked(fetchDeployProfile).mockReturnValue(profile.promise)
    render().unmount()
    wrapper = undefined
    profile.resolve({ admin_console_url: adminUrl } as Awaited<ReturnType<typeof fetchDeployProfile>>)
    await flushPromises()
    expect(opener.postMessage).not.toHaveBeenCalled()
  })

  it('aborts and fences an in-progress confirmation on unmount without navigating later', async () => {
    const confirmation = deferred<Response>()
    fetchMock.mockReset().mockResolvedValueOnce(json(info)).mockReturnValueOnce(confirmation.promise)
    render()
    await flushPromises()
    handoff()
    await flushPromises()
    wrapper!.unmount()
    wrapper = undefined
    expect(fetchMock.mock.calls[1]![1].signal.aborted).toBe(true)
    expect(impersonationContext.value).toMatchObject({ id, ended: true })
    confirmation.resolve(json({ user: { id: 20, access_profile: { org_key: 'personal' } } }))
    await flushPromises()
    expect(replace).not.toHaveBeenCalled()
    expect(opener.postMessage).toHaveBeenCalledTimes(1)
  })
})
