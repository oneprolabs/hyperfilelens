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
  fetchCopilotCitation: vi.fn(),
  updateCopilotRunFeedback: vi.fn(),
}))

vi.mock('../../../lib/lensApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../lib/lensApi')>(),
  fetchCopilotRunPdf: mocks.fetchCopilotRunPdf,
  fetchCopilotCitation: mocks.fetchCopilotCitation,
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
    mocks.fetchCopilotCitation.mockReset()
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

  it('keeps the thinking panel visible for structured runtime-only events', async () => {
    const wrapper = mountList({
      messages: [{
        id: 'assistant-runtime',
        role: 'assistant',
        text: 'Answer',
        thinking: {
          steps: [{
            eventType: 'plan.updated',
            message: 'plan.updated',
            payload: { steps: [{ id: 'step-1', title: 'Inspect sources' }] },
          }],
        },
      }],
    })

    expect(wrapper.find('.thinking-panel-done').exists()).toBe(true)
    await wrapper.get('.thinking-panel-header').trigger('click')
    expect(wrapper.find('.copilot-runtime-card').exists()).toBe(true)
    wrapper.unmount()
  })

  it('opens SourceLens citations in a right-side drawer and closes with Escape', async () => {
    mocks.fetchCopilotCitation.mockResolvedValue({
      id: 'citation-1',
      path: 'src/example.ts',
      start_line: 4,
      end_line: 5,
      lines: [
        { number: 4, content: 'const answer = true' },
        { number: 5, content: 'export default answer' },
      ],
    })
    const wrapper = mountList({
      sessionId: 17,
      messages: [{
        id: 'assistant-1',
        role: 'assistant',
        runId: 'run-1',
        completedAt: '2026-08-20T01:59:00Z',
        text: 'Answer',
        citations: [{ id: 'citation-1', path: 'src/example.ts' }],
      }],
    })

    await wrapper.get('.message-citation').trigger('click')
    await flushPromises()

    expect(wrapper.find('.citation-drawer').exists()).toBe(true)
    expect(wrapper.find('.citation-code-line--highlighted').exists()).toBe(true)
    expect(mocks.fetchCopilotCitation).toHaveBeenCalledWith(17, 'run-1', 'citation-1')

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(wrapper.find('.citation-drawer').exists()).toBe(false)
    wrapper.unmount()
  })

  it('re-enables clarification after the parent reports a failed submission', async () => {
    const wrapper = mountList({
      messages: [{
        id: 'clarification-1',
        role: 'assistant',
        runId: 'run-1',
        text: '',
        thinking: {
          termination_detail: {
            request: {
              request_id: 'request-1',
              question: 'Which service should be inspected?',
            },
          },
        },
        clarificationRequest: {
          requestId: 'request-1',
          question: 'Which service should be inspected?',
        },
      }],
      clarificationResetToken: 0,
    })

    const input = wrapper.get('.clarification-input')
    await input.setValue('API service')
    const submit = wrapper.get('.clarification-submit')
    await submit.trigger('click')
    await nextTick()
    expect(submit.attributes('disabled')).toBeDefined()

    await wrapper.setProps({ clarificationResetToken: 1 })
    expect(wrapper.get('.clarification-submit').attributes('disabled')).toBeUndefined()
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

  it('offers Chat sharing for the latest completed answer', async () => {
    const wrapper = mountList({
      messages: [
        {
          id: 'assistant-1',
          role: 'assistant',
          runId: 'run-1',
          completedAt: '2026-08-20T01:59:00Z',
          text: 'Earlier answer',
        },
        {
          id: 'assistant-2',
          role: 'assistant',
          runId: 'run-2',
          completedAt: '2026-08-20T02:00:00Z',
          text: 'Latest answer',
        },
      ],
    })

    expect(wrapper.findAll('button[aria-label="Share"]')).toHaveLength(1)
    await wrapper.get('button[aria-label="Share"]').trigger('click')

    expect(wrapper.emitted('shareAnswer')?.[0]).toEqual([expect.objectContaining({
      id: 'assistant-2',
      runId: 'run-2',
    })])
    wrapper.unmount()
  })

  it('colors like, dislike, and shared actions like SourceLens', () => {
    const wrapper = mountList({
      sharedRunId: 'run-2',
      messages: [
        {
          id: 'assistant-2',
          role: 'assistant',
          runId: 'run-2',
          completedAt: '2026-08-20T02:00:00Z',
          text: 'Latest answer',
          feedback: 'negative',
        },
      ],
    })

    expect(wrapper.get('button[aria-label="Like"]').classes()).not.toContain('is-positive')
    expect(wrapper.get('button[aria-label="Dislike"]').classes()).toContain('is-negative')
    expect(wrapper.get('button[aria-label="Share"]').classes()).toContain('is-shared')
    wrapper.unmount()
  })

  it('keeps sharing on the latest completed answer while a newer answer is incomplete', () => {
    const wrapper = mountList({
      messages: [
        {
          id: 'assistant-1',
          role: 'assistant',
          runId: 'run-1',
          completedAt: '2026-08-20T01:59:00Z',
          text: 'Earlier answer',
        },
        {
          id: 'assistant-2',
          role: 'assistant',
          runId: 'run-2',
          completedAt: null,
          text: 'Streaming answer',
        },
      ],
    })

    const shareButton = wrapper.get('button[aria-label="Share"]')
    expect(wrapper.findAll('button[aria-label="Share"]')).toHaveLength(1)
    expect(shareButton.element.closest('.message-body')?.textContent).toContain('Earlier answer')
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
      streamingRunStatus: 'running',
      streamingThinking: [],
      streamingElapsedSeconds: 2,
    })

    const status = wrapper.get('.live-status-card')
    expect(status.attributes('aria-live')).toBe('polite')
    expect(status.text()).toContain('Analyzing')
    expect(status.text()).toContain('2s')
    expect(status.text()).not.toContain('Agent activity')
    expect(status.text()).not.toContain('Running')
    expect(wrapper.find('.thinking-panel-live').exists()).toBe(false)
    expect(wrapper.find('.message-card--typing').exists()).toBe(false)
    wrapper.unmount()
  })

  it('switches the live line to Generating, then to the Agent activity card', () => {
    const generating = mountList({
      messages: [{ id: 'user-1', role: 'user', text: 'What is backed up?' }],
      streaming: true,
      streamingRunStatus: 'streaming',
      streamingThinking: [],
      streamingElapsedSeconds: 4,
    })
    expect(generating.get('.live-status-card').text()).toContain('Generating')
    expect(generating.get('.live-status-card').text()).toContain('4s')
    generating.unmount()

    const answering = mountList({
      messages: [{ id: 'user-1', role: 'user', text: 'What is backed up?' }],
      streaming: true,
      streamingRunStatus: 'queued',
      streamingThinking: [
        { message: 'phase.changed', eventType: 'phase.changed', payload: { phase: 'answering' } },
      ],
    })
    expect(answering.get('.live-status-card').text()).toContain('Preparing the answer')
    expect(answering.find('.thinking-panel-live').exists()).toBe(false)
    answering.unmount()
  })

  it('keeps Queued / Analyzing until a real tool activity arrives', () => {
    const queued = mountList({
      messages: [{ id: 'user-1', role: 'user', text: 'What is backed up?' }],
      streaming: true,
      streamingRunStatus: 'queued',
      streamingThinking: [
        {
          message: 'workflow.phase.changed',
          eventType: 'phase.changed',
          agentEvent: 'workflow.phase.changed',
          payload: { phase: 'analyzing' },
        },
      ],
      streamingElapsedSeconds: 3,
    })
    expect(queued.get('.live-status-card').text()).toContain('Analyzing the question')
    expect(queued.find('.thinking-panel-live').exists()).toBe(false)
    queued.unmount()

    const activityOnly = mountList({
      messages: [{ id: 'user-1', role: 'user', text: 'What is backed up?' }],
      streaming: true,
      streamingRunStatus: 'running',
      streamingThinking: [
        { message: 'activity.recorded', eventType: 'activity.recorded', payload: { id: 'a1' } },
      ],
    })
    expect(activityOnly.get('.live-status-card').text()).toContain('Analyzing')
    expect(activityOnly.find('.thinking-panel-live').exists()).toBe(false)
    activityOnly.unmount()
  })

  it('matches SourceLens activity grouping while a run is live', () => {
    const wrapper = mountList({
      messages: [{ id: 'user-1', role: 'user', text: 'What is backed up?' }],
      streaming: true,
      streamingThinking: [
        { message: 'tool.find_files.start', agentEvent: 'tool.find_files.start' },
        { message: 'tool.find_files.done', agentEvent: 'tool.find_files.done' },
      ],
      streamingElapsedSeconds: 13,
    })

    expect(wrapper.get('.copilot-activity-group').attributes('open')).toBeDefined()
    expect(wrapper.get('.copilot-activity-group').text()).toContain('Running')
    expect(wrapper.get('.copilot-activity-group').text()).toContain('Searching relevant sources')
    expect(wrapper.get('.thinking-panel-live .thinking-panel-status').text()).toContain(
      'Recorded 1 activities · 13s',
    )
    wrapper.unmount()
  })

  it('hands the sync in-progress assistant to the live row like SourceLens', () => {
    const wrapper = mountList({
      messages: [
        { id: 'user-1', role: 'user', text: 'What is backed up?' },
        {
          id: 'assistant-live',
          role: 'assistant',
          runId: 'run-1',
          text: '',
          thinking: {
            steps: [
              { message: 'tool.find_files.start', agentEvent: 'tool.find_files.start' },
              { message: 'tool.find_files.done', agentEvent: 'tool.find_files.done' },
            ],
          },
        },
      ],
      streaming: true,
      streamingThinking: [
        { message: 'tool.find_files.start', agentEvent: 'tool.find_files.start' },
        { message: 'tool.find_files.done', agentEvent: 'tool.find_files.done' },
      ],
      streamingElapsedSeconds: 8,
    })

    expect(wrapper.findAll('.thinking-panel-done')).toHaveLength(0)
    expect(wrapper.findAll('.thinking-panel-live')).toHaveLength(1)
    expect(wrapper.get('.thinking-panel-live').text()).toContain('Searching relevant sources')
    wrapper.unmount()
  })

  it('formats completed activity duration like SourceLens (including minutes)', () => {
    const wrapper = mountList({
      messages: [{
        id: 'assistant-1',
        role: 'assistant',
        runId: 'run-1',
        completedAt: '2026-08-20T02:00:00Z',
        text: 'Answer',
        thinking: {
          duration_seconds: 65,
          steps: [
            { message: 'tool.find_files.start', agentEvent: 'tool.find_files.start' },
            { message: 'tool.find_files.done', agentEvent: 'tool.find_files.done' },
          ],
        },
      }],
    })

    expect(wrapper.get('.thinking-panel-done .thinking-panel-status').text()).toContain(
      'Completed 1 activities · 1m 5s',
    )
    wrapper.unmount()
  })

  it('matches SourceLens activity grouping after a run completes', async () => {
    const wrapper = mountList({
      messages: [{
        id: 'assistant-1',
        role: 'assistant',
        runId: 'run-1',
        completedAt: '2026-08-20T02:00:00Z',
        text: 'Answer',
        thinking: {
          duration_seconds: 16,
          outcome: 'Completed',
          steps: [
            { message: 'tool.find_files.start', agentEvent: 'tool.find_files.start' },
            { message: 'tool.find_files.done', agentEvent: 'tool.find_files.done' },
          ],
        },
      }],
    })

    const panel = wrapper.get('.thinking-panel-done')
    expect(panel.text()).toContain('Agent activity')
    expect(panel.text()).toContain('Completed 1 activities · 16s')
    await panel.get('.thinking-panel-header').trigger('click')
    expect(panel.get('.copilot-activity-group').attributes('open')).toBeUndefined()
    expect(panel.get('.copilot-activity-group').text()).toContain('Completed')
    expect(panel.get('.copilot-activity-group').text()).toContain('Searching relevant sources')
    expect(panel.find('.thinking-outcome').exists()).toBe(false)
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
