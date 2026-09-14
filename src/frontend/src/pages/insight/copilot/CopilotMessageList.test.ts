// @vitest-environment jsdom

import { nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../../locales/en'
import type { LensRunFeedbackResponse } from '../../../lib/lensApi'
import CopilotMessageList from './CopilotMessageList.vue'

const mocks = vi.hoisted(() => ({
  fetchCopilotRunPdf: vi.fn(),
  updateCopilotRunFeedback: vi.fn(),
}))

vi.mock('../../../lib/lensApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../lib/lensApi')>(),
  fetchCopilotRunPdf: mocks.fetchCopilotRunPdf,
  updateCopilotRunFeedback: mocks.updateCopilotRunFeedback,
}))

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

describe('CopilotMessageList welcome message and live feedback', () => {
  beforeEach(() => {
    resizeCallback = null
    mocks.fetchCopilotRunPdf.mockReset()
    mocks.updateCopilotRunFeedback.mockReset()
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('shows the welcome message without starter question cards', () => {
    const wrapper = mountList({
      messages: [{
        id: 'welcome-1',
        role: 'assistant',
        text: en.insight.copilot.welcome,
        isWelcome: true,
      }],
    })

    expect(wrapper.get('.message-card--welcome').text()).toBe(en.insight.copilot.welcome)
    expect(wrapper.find('.copilot-chip-grid').exists()).toBe(false)
    expect(wrapper.find('.copilot-chip-box').exists()).toBe(false)
    wrapper.unmount()
  })

  it('persists answer feedback and emits the SourceLens result', async () => {
    let resolveFeedback: (value: LensRunFeedbackResponse) => void = () => undefined
    mocks.updateCopilotRunFeedback.mockImplementation(
      () => new Promise<LensRunFeedbackResponse>((resolve) => {
        resolveFeedback = resolve
      }),
    )
    const wrapper = mountList({
      sessionId: 17,
      messages: [{
        id: 'assistant-1',
        role: 'assistant',
        runId: 'c42dfb76-3afd-4ad7-b896-472f71f38586',
        completedAt: '2026-08-20T01:59:00Z',
        text: 'Answer',
        feedback: null,
      }],
    })
    const likeButton = wrapper.get('button[aria-label="Like"]')

    expect(likeButton.attributes('aria-pressed')).toBe('false')
    await likeButton.trigger('click')
    expect(likeButton.attributes('disabled')).toBeDefined()
    resolveFeedback({
      feedback: 'positive',
      feedback_updated_at: '2026-08-20T02:00:00Z',
    })
    await flushPromises()

    expect(mocks.updateCopilotRunFeedback).toHaveBeenCalledOnce()
    expect(mocks.updateCopilotRunFeedback).toHaveBeenCalledWith(
      17,
      'c42dfb76-3afd-4ad7-b896-472f71f38586',
      'positive',
    )
    expect(wrapper.emitted('feedbackUpdated')?.[0]).toEqual([{
      sessionId: 17,
      messageId: 'assistant-1',
      runId: 'c42dfb76-3afd-4ad7-b896-472f71f38586',
      feedback: 'positive',
    }])
    expect(likeButton.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('clears existing feedback through SourceLens', async () => {
    mocks.updateCopilotRunFeedback.mockResolvedValue({
      feedback: '',
      feedback_updated_at: '2026-08-20T02:05:00Z',
    })
    const wrapper = mountList({
      sessionId: 17,
      messages: [{
        id: 'assistant-1',
        role: 'assistant',
        runId: 'c42dfb76-3afd-4ad7-b896-472f71f38586',
        completedAt: '2026-08-20T01:59:00Z',
        text: 'Answer',
        feedback: 'positive',
      }],
    })
    const likeButton = wrapper.get('button[aria-label="Like"]')

    expect(likeButton.attributes('aria-pressed')).toBe('true')
    await likeButton.trigger('click')
    await flushPromises()

    expect(mocks.updateCopilotRunFeedback).toHaveBeenCalledWith(
      17,
      'c42dfb76-3afd-4ad7-b896-472f71f38586',
      '',
    )
    expect(wrapper.emitted('feedbackUpdated')?.[0]?.[0]).toMatchObject({
      feedback: null,
    })
    wrapper.unmount()
  })

  it('keeps the persisted state when feedback saving fails', async () => {
    mocks.updateCopilotRunFeedback.mockRejectedValue(new Error('unavailable'))
    const wrapper = mountList({
      sessionId: 17,
      messages: [{
        id: 'assistant-1',
        role: 'assistant',
        runId: 'c42dfb76-3afd-4ad7-b896-472f71f38586',
        completedAt: '2026-08-20T01:59:00Z',
        text: 'Answer',
        feedback: 'negative',
      }],
    })
    const dislikeButton = wrapper.get('button[aria-label="Dislike"]')

    await dislikeButton.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('feedbackUpdated')).toBeUndefined()
    expect(dislikeButton.attributes('aria-pressed')).toBe('true')
    expect(dislikeButton.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('does not offer feedback before an answer is complete', () => {
    const wrapper = mountList({
      messages: [{
        id: 'assistant-1',
        role: 'assistant',
        runId: 'c42dfb76-3afd-4ad7-b896-472f71f38586',
        text: 'Partial answer',
        completedAt: null,
      }],
    })

    expect(wrapper.find('button[aria-label="Like"]').exists()).toBe(false)
    expect(wrapper.find('button[aria-label="Dislike"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('downloads the SourceLens PDF through the HFL session proxy', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:answer-pdf'),
      revokeObjectURL,
    })
    mocks.fetchCopilotRunPdf.mockResolvedValue({
      blob: new Blob(['pdf'], { type: 'application/pdf' }),
      filename: 'answer.pdf',
    })
    const runId = 'c42dfb76-3afd-4ad7-b896-472f71f38586'
    const wrapper = mountList({
      sessionId: 17,
      messages: [
        { id: 'user-1', role: 'user', text: 'Question' },
        {
          id: 'assistant-1',
          role: 'assistant',
          runId,
          completedAt: '2026-08-20T01:59:00Z',
          text: 'Answer',
        },
      ],
    })

    await wrapper.get('button[aria-label="Download PDF"]').trigger('click')
    await flushPromises()

    expect(mocks.fetchCopilotRunPdf).toHaveBeenCalledWith(17, runId)
    expect(click).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:answer-pdf')
    wrapper.unmount()
  })

  it('emits the SourceLens Run reference when regenerating an answer', async () => {
    const runId = 'c42dfb76-3afd-4ad7-b896-472f71f38586'
    const wrapper = mountList({
      messages: [
        { id: 'user-1', role: 'user', text: 'Original question' },
        {
          id: 'assistant-1',
          role: 'assistant',
          runId,
          completedAt: '2026-08-20T01:59:00Z',
          text: 'Original answer',
        },
      ],
      starterDisabled: false,
    })

    await wrapper.get('button[aria-label="Regenerate answer"]').trigger('click')

    expect(wrapper.emitted('retryQuestion')?.[0]).toEqual([{
      sessionId: 1,
      question: 'Original question',
      runId,
    }])
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
    expect(status.text()).toContain('Agent activity · 2s')
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
    expect(wrapper.get('.scroll-to-latest').text()).toContain('Back to Latest')

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
