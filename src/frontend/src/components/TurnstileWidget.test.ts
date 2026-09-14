// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  preloadTurnstileScript,
  resetTurnstileScriptLoad,
} from '../lib/turnstileLoader'
import TurnstileWidget from './TurnstileWidget.vue'

vi.mock('../lib/turnstileLoader', () => ({
  preloadTurnstileScript: vi.fn(),
  resetTurnstileScriptLoad: vi.fn(),
  TURNSTILE_LOAD_TIMEOUT_MS: 1_000,
}))

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: {
    en: {
      login: { captchaLoading: 'Loading Cloudflare human verification...' },
    },
    fr: {
      login: { captchaLoading: 'Chargement de la vérification humaine...' },
    },
    'zh-hans': {
      login: { captchaLoading: 'Localized human verification message' },
    },
  },
})

type RenderOptions = Parameters<NonNullable<typeof window.turnstile>['render']>[1]

function appendTurnstileResponse(container: HTMLElement, value = '') {
  const wrapper = document.createElement('div')
  const input = document.createElement('input')
  input.type = 'hidden'
  input.name = 'cf-turnstile-response'
  input.value = value
  wrapper.append(input)
  container.append(wrapper)
  return input
}

function mountWidget(overrides: Partial<{
  siteKey: string
  action: string
  loadTimeoutMs: number
  slowLoadDelayMs: number
}> = {}) {
  return mount(TurnstileWidget, {
    props: { siteKey: 'test-site-key', action: 'login', ...overrides },
    global: { plugins: [i18n] },
  })
}

describe('TurnstileWidget lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(preloadTurnstileScript).mockReset().mockResolvedValue(undefined)
    vi.mocked(resetTurnstileScriptLoad).mockReset()
    i18n.global.locale.value = 'en'
    delete window.turnstile
  })

  afterEach(() => {
    vi.useRealTimers()
    delete window.turnstile
  })

  it('rerenders expired challenges and accepts repeated test tokens safely', async () => {
    const renderOptions: RenderOptions[] = []
    const remove = vi.fn()
    window.turnstile = {
      ready: callback => callback(),
      render: (container, options) => {
        renderOptions.push(options)
        appendTurnstileResponse(container)
        return `widget-${renderOptions.length}`
      },
      reset: vi.fn(),
      remove,
    }

    const wrapper = mountWidget()
    await flushPromises()
    expect(wrapper.find('.turnstile-widget__loading').exists()).toBe(true)

    await vi.advanceTimersByTimeAsync(100)
    expect(wrapper.find('.turnstile-widget__loading').exists()).toBe(false)
    expect(wrapper.find('input[name="cf-turnstile-response"]').exists()).toBe(true)
    expect(wrapper.emitted('rendered')).toHaveLength(1)
    expect(wrapper.emitted('slow-load')).toBeUndefined()
    expect(wrapper.emitted('success')).toBeUndefined()
    expect(renderOptions[0]).toMatchObject({
      'response-field': true,
      'response-field-name': 'cf-turnstile-response',
      retry: 'never',
      'refresh-expired': 'never',
      'refresh-timeout': 'auto',
    })

    renderOptions[0].callback('fixed-test-token')
    renderOptions[0].callback('duplicate-token')
    expect(wrapper.emitted('success')).toEqual([['fixed-test-token']])

    renderOptions[0]['expired-callback']?.()
    await flushPromises()
    expect(wrapper.emitted('success')).toEqual([['fixed-test-token']])
    expect(wrapper.emitted('expire')).toHaveLength(1)
    expect(remove).toHaveBeenCalledWith('widget-1')
    expect(renderOptions).toHaveLength(2)

    renderOptions[0].callback('stale-token')
    renderOptions[1].callback('fixed-test-token')
    expect(wrapper.emitted('success')).toEqual([
      ['fixed-test-token'],
      ['fixed-test-token'],
    ])

    wrapper.vm.reset()
    await flushPromises()
    expect(remove).toHaveBeenCalledWith('widget-2')
    expect(renderOptions).toHaveLength(3)

    renderOptions[1].callback('stale-token')
    renderOptions[2].callback('fixed-test-token')
    expect(wrapper.emitted('success')).toEqual([
      ['fixed-test-token'],
      ['fixed-test-token'],
      ['fixed-test-token'],
    ])
    wrapper.unmount()
    expect(remove).toHaveBeenCalledWith('widget-3')
  })

  it('emits an error when Cloudflare fails before rendering a challenge', async () => {
    let renderOptions: RenderOptions | undefined
    window.turnstile = {
      ready: callback => callback(),
      render: (_container, options) => {
        renderOptions = options
        return 'widget-2'
      },
      reset: vi.fn(),
      remove: vi.fn(),
    }

    const wrapper = mountWidget()
    await flushPromises()
    renderOptions!['error-callback']?.()
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('error')).toHaveLength(1)
    expect(wrapper.find('.turnstile-widget__loading').exists()).toBe(false)
    wrapper.unmount()
  })

  it('reports challenge errors after the response field is mounted', async () => {
    let renderOptions: RenderOptions | undefined
    window.turnstile = {
      ready: callback => callback(),
      render: (container, options) => {
        renderOptions = options
        appendTurnstileResponse(container)
        return 'widget-error'
      },
      reset: vi.fn(),
      remove: vi.fn(),
    }

    const wrapper = mountWidget()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(100)
    renderOptions!['error-callback']?.('300030')
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('error')).toEqual([['300030']])
    wrapper.unmount()
  })

  it('drops malformed challenge error codes', async () => {
    let renderOptions: RenderOptions | undefined
    window.turnstile = {
      ready: callback => callback(),
      render: (_container, options) => {
        renderOptions = options
        return 'widget-invalid-error-code'
      },
      reset: vi.fn(),
      remove: vi.fn(),
    }

    const wrapper = mountWidget()
    await flushPromises()
    renderOptions!['error-callback']?.('token=must-not-leak')
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('error')).toEqual([[undefined]])
    wrapper.unmount()
  })

  it('reports an error when no widget DOM appears before timeout', async () => {
    window.turnstile = {
      ready: callback => callback(),
      render: () => 'widget-timeout',
      reset: vi.fn(),
      remove: vi.fn(),
    }

    const wrapper = mountWidget({ loadTimeoutMs: 1_000, slowLoadDelayMs: 500 })
    await flushPromises()

    await vi.advanceTimersByTimeAsync(499)
    expect(wrapper.emitted('slow-load')).toBeUndefined()

    await vi.advanceTimersByTimeAsync(1)
    expect(wrapper.emitted('slow-load')).toHaveLength(1)

    await vi.advanceTimersByTimeAsync(500)
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('error')).toHaveLength(1)
    expect(wrapper.find('.turnstile-widget__loading').exists()).toBe(false)
    wrapper.unmount()
  })

  it('invalidates the token without reporting expiration when language changes', async () => {
    const renderOptions: RenderOptions[] = []
    const remove = vi.fn()
    window.turnstile = {
      ready: callback => callback(),
      render: (container, options) => {
        renderOptions.push(options)
        appendTurnstileResponse(container)
        return `widget-${renderOptions.length}`
      },
      reset: vi.fn(),
      remove,
    }

    const wrapper = mountWidget()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(100)

    i18n.global.locale.value = 'zh-hans'
    await flushPromises()

    expect(wrapper.emitted('invalidate')).toHaveLength(1)
    expect(wrapper.emitted('expire')).toBeUndefined()
    expect(remove).toHaveBeenCalledWith('widget-1')
    expect(renderOptions.at(-1)?.language).toBe('zh-cn')

    renderOptions[0].callback('stale-language-token')
    expect(wrapper.emitted('success')).toBeUndefined()
    renderOptions[1].callback('current-language-token')
    expect(wrapper.emitted('success')).toEqual([['current-language-token']])
    wrapper.unmount()
  })

  it('reports a load failure when rendering a replacement challenge throws', async () => {
    let renderOptions: RenderOptions | undefined
    let renderCount = 0
    const remove = vi.fn()
    window.turnstile = {
      ready: callback => callback(),
      render: (container, options) => {
        renderCount += 1
        if (renderCount > 1) throw new Error('replacement render failed')
        renderOptions = options
        appendTurnstileResponse(container)
        return 'widget-reset-error'
      },
      reset: vi.fn(),
      remove,
    }

    const wrapper = mountWidget()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(100)
    renderOptions!.callback('token-that-must-be-invalidated')
    wrapper.vm.reset()
    await flushPromises()

    expect(remove).toHaveBeenCalledWith('widget-reset-error')
    expect(wrapper.emitted('load-failed')).toHaveLength(1)
    expect(wrapper.emitted('success')).toEqual([['token-that-must-be-invalidated']])

    renderOptions!.callback('token-that-must-be-invalidated')
    expect(wrapper.emitted('success')).toEqual([['token-that-must-be-invalidated']])
    wrapper.unmount()
  })

  it('retries a failed script load once before reporting load failure', async () => {
    vi.mocked(preloadTurnstileScript).mockRejectedValue(new Error('network unavailable'))

    const wrapper = mountWidget()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()

    expect(preloadTurnstileScript).toHaveBeenCalledTimes(2)
    expect(resetTurnstileScriptLoad).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('load-failed')).toHaveLength(1)
    expect(wrapper.find('.turnstile-widget__loading').exists()).toBe(false)
    wrapper.unmount()
  })
})
