// @vitest-environment jsdom

import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../../locales/en'
import CopilotMessageList from './CopilotMessageList.vue'

let resizeCallback: ResizeObserverCallback | null = null

class ResizeObserverMock {
  constructor(callback: ResizeObserverCallback) {
    resizeCallback = callback
  }

  observe() {}
  unobserve() {}
  disconnect() {}
}

function notifyContentResize() {
  resizeCallback?.([], {} as ResizeObserver)
}

function mountList(props: Record<string, unknown>) {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  return mount(CopilotMessageList, {
    props: {
      sessionId: 1,
      messages: [],
      ...props,
    },
    global: { plugins: [i18n] },
  })
}

describe('CopilotMessageList starter questions and live feedback', () => {
  beforeEach(() => {
    resizeCallback = null
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('emits a starter question directly and exposes its selected state', async () => {
    const wrapper = mountList({
      messages: [{
        id: 'welcome-1',
        role: 'assistant',
        text: en.insight.copilot.welcome,
        starterChips: true,
      }],
      selectedStarterKey: '',
      starterDisabled: false,
    })
    const firstChip = wrapper.get('.copilot-chip-box')

    expect(firstChip.attributes('aria-pressed')).toBe('false')
    await firstChip.trigger('click')

    expect(wrapper.emitted('starterChip')?.[0]).toEqual([
      'chipQuerySops',
      en.insight.copilot.chipQuerySopsPrompt,
    ])

    await wrapper.setProps({ selectedStarterKey: 'chipQuerySops' })
    expect(firstChip.classes()).toContain('is-selected')
    expect(firstChip.attributes('aria-pressed')).toBe('true')
    wrapper.unmount()
  })

  it('disables starter questions while another submission is active', () => {
    const wrapper = mountList({
      messages: [{
        id: 'welcome-1',
        role: 'assistant',
        text: en.insight.copilot.welcome,
        starterChips: true,
      }],
      starterDisabled: true,
    })

    expect(wrapper.get('.copilot-chip-box').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('shows analysis feedback before SourceLens emits the first progress step', () => {
    const wrapper = mountList({
      messages: [{ id: 'user-1', role: 'user', text: 'What is backed up?' }],
      streaming: true,
      streamingThinking: [],
      streamingElapsedSeconds: 2,
    })

    const livePanel = wrapper.get('.thinking-panel-live')
    const status = livePanel.get('[role="status"]')
    expect(status.attributes('aria-live')).toBe('polite')
    expect(status.text()).toContain('Thinking… 2s')
    expect(livePanel.find('.thinking-panel-body').exists()).toBe(false)
    expect(wrapper.find('.message-card--typing').exists()).toBe(false)
    wrapper.unmount()
  })

  it('follows the rendered height after a streamed reply flushes', async () => {
    vi.useFakeTimers()
    const wrapper = mountList({
      messages: [{ id: 'user-1', role: 'user', text: 'Question' }],
      streaming: true,
      streamingContent: '',
    })
    await nextTick()
    const scroll = wrapper.get('.chat-scroll').element as HTMLElement
    let scrollHeight = 800
    Object.defineProperties(scroll, {
      clientHeight: { configurable: true, get: () => 400 },
      scrollHeight: { configurable: true, get: () => scrollHeight },
      scrollTop: { configurable: true, writable: true, value: 400 },
    })

    await wrapper.setProps({ streamingContent: 'A new streamed reply' })
    await nextTick()
    expect(scroll.scrollTop).toBe(800)

    await vi.advanceTimersByTimeAsync(64)
    scrollHeight = 960
    notifyContentResize()

    expect(scroll.scrollTop).toBe(960)
    wrapper.unmount()
  })

  it('pauses following while the user reads history and resumes on request', async () => {
    const wrapper = mountList({
      messages: [{ id: 'user-1', role: 'user', text: 'Question' }],
      streaming: true,
      streamingContent: '',
    })
    await nextTick()
    const scroll = wrapper.get('.chat-scroll').element as HTMLElement
    let scrollHeight = 1000
    Object.defineProperties(scroll, {
      clientHeight: { configurable: true, get: () => 400 },
      scrollHeight: { configurable: true, get: () => scrollHeight },
      scrollTop: { configurable: true, writable: true, value: 200 },
    })

    await wrapper.get('.chat-scroll').trigger('scroll')
    expect(wrapper.get('.scroll-to-latest').text()).toContain('Back to latest')

    scrollHeight = 1200
    await wrapper.setProps({ streamingContent: 'Do not interrupt history reading' })
    await nextTick()
    notifyContentResize()
    expect(scroll.scrollTop).toBe(200)

    await wrapper.get('.scroll-to-latest').trigger('click')
    await nextTick()
    expect(scroll.scrollTop).toBe(1200)
    expect(wrapper.find('.scroll-to-latest').exists()).toBe(false)
    wrapper.unmount()
  })

  it('resumes following when the user scrolls back near the bottom', async () => {
    const wrapper = mountList({
      messages: [{ id: 'user-1', role: 'user', text: 'Question' }],
      streaming: true,
      streamingContent: '',
    })
    await nextTick()
    const scroll = wrapper.get('.chat-scroll').element as HTMLElement
    let scrollHeight = 1000
    Object.defineProperties(scroll, {
      clientHeight: { configurable: true, get: () => 400 },
      scrollHeight: { configurable: true, get: () => scrollHeight },
      scrollTop: { configurable: true, writable: true, value: 200 },
    })

    await wrapper.get('.chat-scroll').trigger('scroll')
    scroll.scrollTop = 560
    await wrapper.get('.chat-scroll').trigger('scroll')
    expect(wrapper.find('.scroll-to-latest').exists()).toBe(false)

    scrollHeight = 1200
    await wrapper.setProps({ streamingContent: 'Continue following the reply' })
    await nextTick()
    expect(scroll.scrollTop).toBe(1200)
    wrapper.unmount()
  })
})
