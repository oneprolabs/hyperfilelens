// @vitest-environment jsdom
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import ImpersonationBanner from './ImpersonationBanner.vue'
import { impersonationContext, endImpersonation, markImpersonationEnded } from '../lib/impersonation'
import { en } from '../locales/en'

vi.mock('../lib/impersonation', async () => {
  const { shallowRef } = await import('vue')
  return {
    impersonationContext: shallowRef(null),
    endImpersonation: vi.fn(),
    markImpersonationEnded: vi.fn(),
  }
})

let wrapper: VueWrapper | undefined
const now = Date.parse('2026-11-27T04:00:00Z')

function render(milliseconds = 24 * 60_000) {
  impersonationContext.value = {
    id: 'test-session',
    user_email: 'customer@example.test',
    org_name: 'Personal Edition',
    expires_at: new Date(now + milliseconds).toISOString(),
  }
  wrapper = mount(ImpersonationBanner, {
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      stubs: {
        ElButton: {
          props: ['loading', 'disabled'],
          template: '<button :disabled="disabled" :aria-busy="loading"><span v-if="loading" data-testid="end-loading" /><slot v-else name="icon" /><slot /></button>',
        },
      },
    },
  })
  return wrapper
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(now)
  vi.mocked(endImpersonation).mockReset()
  vi.mocked(markImpersonationEnded).mockReset()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  impersonationContext.value = null
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('impersonation context bar', () => {
  it('separates identity, organization and remaining time without a long timestamp or tooltip', () => {
    const view = render()
    expect(view.attributes('role')).toBe('region')
    expect(view.attributes('aria-label')).toBe('Impersonation Session')
    expect(view.find('.impersonation-banner__label').text()).toBe('Acting as')
    expect(view.find('strong').text()).toBe('customer@example.test')
    expect(view.find('.impersonation-banner__organization').text()).toContain('Personal Edition')
    expect(view.find('.impersonation-banner__remaining').text()).toBe('24 min left')
    expect(view.text()).not.toContain('2026-11-27')
    expect(view.find('[role="status"]').exists()).toBe(false)
    expect(view.find('[title]').exists()).toBe(false)
    expect(view.find('el-tooltip').exists()).toBe(false)
  })

  it('keeps the minute label stable between minute boundaries', async () => {
    const view = render()
    await vi.advanceTimersByTimeAsync(59_000)
    expect(view.find('.impersonation-banner__remaining').text()).toBe('24 min left')
    await vi.advanceTimersByTimeAsync(1_000)
    expect(view.find('.impersonation-banner__remaining').text()).toBe('23 min left')
  })

  it('uses consistent linear identity and exit icons without replacing the button label', () => {
    const view = render()
    const identity = view.find('.impersonation-banner__icon')
    const exit = view.find('.impersonation-banner__end-icon')
    expect(identity.attributes('width')).toBe('16')
    expect(identity.attributes('stroke-width')).toBe('1.75')
    expect(identity.attributes('aria-hidden')).toBe('true')
    expect(exit.attributes('width')).toBe('14')
    expect(exit.attributes('stroke-width')).toBe('1.75')
    expect(exit.attributes('aria-hidden')).toBe('true')
    expect(view.find('button').text()).toBe('End Session')
  })

  it('emphasizes the remaining time only when fewer than five minutes remain', async () => {
    const view = render(5 * 60_000)
    expect(view.find('.impersonation-banner__remaining--soon').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(view.find('.impersonation-banner__remaining--soon').text()).toBe('5 min left')
  })

  it('uses a readable last-minute message instead of ticking seconds', () => {
    const view = render(59_000)
    expect(view.find('.impersonation-banner__remaining--soon').text()).toBe('Less than 1 min left')
  })

  it.each([undefined, 'invalid-date'])('does not display an invalid countdown for %s', async expiresAt => {
    const view = render()
    impersonationContext.value = { ...impersonationContext.value!, expires_at: expiresAt }
    await nextTick()
    expect(view.find('.impersonation-banner__remaining').exists()).toBe(false)
    expect(view.text()).not.toContain('NaN')
    expect(view.find('button').text()).toBe('End Session')
  })

  it('uses stable account and organization identifiers while names are unavailable', async () => {
    const view = render()
    impersonationContext.value = { id: 'test-session', user_id: 12, org_key: 'personal' }
    await nextTick()
    expect(view.find('strong').text()).toBe('12')
    expect(view.find('.impersonation-banner__organization').text()).toContain('personal')
  })

  it('keeps the session visible and reports an explicit end failure', async () => {
    vi.mocked(endImpersonation).mockRejectedValueOnce(new Error('Network unavailable'))
    const view = render()
    await view.find('button').trigger('click')
    await nextTick()
    expect(endImpersonation).toHaveBeenCalledOnce()
    expect(view.find('[role="alert"]').text()).toBe('Unable to end the session. Try again.')
    expect(view.find('button').attributes('disabled')).toBeUndefined()
    expect(markImpersonationEnded).not.toHaveBeenCalled()
    expect(impersonationContext.value).not.toBeNull()
  })

  it('disables End Session while the existing termination request is pending', async () => {
    let resolveEnd!: () => void
    vi.mocked(endImpersonation).mockImplementationOnce(() => new Promise<void>(resolve => { resolveEnd = resolve }))
    const view = render()
    await view.find('button').trigger('click')
    expect(view.find('button').attributes('disabled')).toBeDefined()
    expect(view.find('button').attributes('aria-busy')).toBe('true')
    expect(view.find('[data-testid="end-loading"]').exists()).toBe(true)
    expect(view.find('.impersonation-banner__end-icon').exists()).toBe(false)
    expect(view.find('button').text()).toBe('End Session')
    resolveEnd()
    await flushPromises()
    await nextTick()
    expect(view.find('button').attributes('disabled')).toBeUndefined()
    expect(view.find('.impersonation-banner__end-icon').exists()).toBe(true)
  })

  it('retains the existing expiry transition instead of reverting to administrator context', async () => {
    const realWindow = window
    const replace = vi.fn()
    vi.stubGlobal('window', new Proxy(realWindow, {
      get(target, key) {
        return key === 'location' ? { replace } : Reflect.get(target, key)
      },
    }))
    render(1_000)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(markImpersonationEnded).toHaveBeenCalledOnce()
    expect(replace).toHaveBeenCalledWith('/impersonation/ended')
    expect(endImpersonation).not.toHaveBeenCalled()
  })

  it('reports wrapped height changes and cleans up the observer and timer', () => {
    let resize!: ResizeObserverCallback
    const observe = vi.fn()
    const disconnect = vi.fn()
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: ResizeObserverCallback) { resize = callback }
      observe = observe
      disconnect = disconnect
    })
    const bounds = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect')
    bounds.mockReturnValue({ height: 40 } as DOMRect)
    const view = render()
    expect(observe).toHaveBeenCalledWith(view.element)
    expect(view.emitted('height')?.at(-1)).toEqual([40])
    expect(document.documentElement.style.getPropertyValue('--hfl-impersonation-height')).toBe('40px')
    bounds.mockReturnValue({ height: 108 } as DOMRect)
    resize([], {} as ResizeObserver)
    expect(view.emitted('height')?.at(-1)).toEqual([108])
    expect(document.documentElement.style.getPropertyValue('--hfl-impersonation-height')).toBe('108px')
    view.unmount()
    wrapper = undefined
    expect(view.emitted('height')?.at(-1)).toEqual([0])
    expect(disconnect).toHaveBeenCalledOnce()
    expect(document.documentElement.style.getPropertyValue('--hfl-impersonation-height')).toBe('')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('restores an existing document offset after the session bar unmounts', () => {
    document.documentElement.style.setProperty('--hfl-impersonation-height', '12px')
    const view = render()
    view.unmount()
    wrapper = undefined
    expect(document.documentElement.style.getPropertyValue('--hfl-impersonation-height')).toBe('12px')
    document.documentElement.style.removeProperty('--hfl-impersonation-height')
  })
})
