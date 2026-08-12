// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { defineComponent, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../locales/en'
import InsightCopilot from './InsightCopilot.vue'

const mocks = vi.hoisted(() => ({
  createCopilotRun: vi.fn(),
  listCopilotSessions: vi.fn(),
  syncCopilotSession: vi.fn(),
  streamCopilotRun: vi.fn(),
  markCopilotSessionViewed: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ path: '/insight/copilot', query: {} }),
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('../../composables/useResponsiveLayout', () => ({
  useResponsiveLayout: () => ({ isPhone: ref(false) }),
}))

vi.mock('../../composables/useAuth', () => ({
  currentUser: ref({ id: 7, username: 'copilot-user', email: 'copilot@example.com' }),
}))

vi.mock('../../lib/lensApi', () => ({
  cancelCopilotRun: vi.fn(),
  createCopilotRun: mocks.createCopilotRun,
  deleteCopilotSession: vi.fn(),
  fetchCopilotReadiness: vi.fn().mockResolvedValue({
    default_agent_model_ref: 'agent-model',
    default_multimodal_model_ref: null,
    active_models: [],
  }),
  fetchLensHealth: vi.fn().mockResolvedValue({
    app: 'lens-bridge',
    status: 'ok',
    lens: { configured: true, reachable: true, authenticated: true },
  }),
  listCopilotAssistants: vi.fn().mockResolvedValue([]),
  listCopilotGatewayOptions: vi.fn().mockResolvedValue([]),
  listCopilotSessions: mocks.listCopilotSessions,
  listKnowledgeSources: vi.fn().mockResolvedValue([]),
  listLensGateways: vi.fn().mockResolvedValue([]),
  markCopilotSessionViewed: mocks.markCopilotSessionViewed,
  renameCopilotSession: vi.fn(),
  retryCopilotSession: vi.fn(),
  streamCopilotRun: mocks.streamCopilotRun,
  syncCopilotSession: mocks.syncCopilotSession,
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

const SimpleStub = defineComponent({ template: '<div />' })

function sessionRow(activeRun: { uuid: string; status: string } | null = null) {
  return {
    id: 444,
    title: 'Chat',
    lifecycle_status: 'ready',
    status: 'active',
    sl_session_uuid: 'session-1',
    sl_assistant_uuid: 'assistant-1',
    last_message_at: null,
    last_assistant_message_at: null,
    last_viewed_at: null,
    has_unread: false,
    active_run_uuid: activeRun?.uuid ?? null,
    active_run_status: activeRun?.status ?? '',
    created_at: '2026-08-11T08:00:00Z',
    updated_at: '2026-08-11T08:00:00Z',
  }
}

function mountCopilot(i18n: ReturnType<typeof createI18n>) {
  return mount(InsightCopilot, {
    global: {
      plugins: [i18n],
      stubs: {
        CopilotSessionSidebar: SimpleStub,
        CopilotContextBar: SimpleStub,
        CopilotLifecycleState: SimpleStub,
        CopilotEmptyState: SimpleStub,
        DangerConfirmDialog: SimpleStub,
        ElDrawer: SimpleStub,
      },
    },
  })
}

describe('InsightCopilot starter question submission', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.listCopilotSessions.mockResolvedValue([sessionRow()])
    mocks.syncCopilotSession.mockResolvedValue({
      session_id: 444,
      messages: [],
      active_run: null,
      run_outcomes: [],
    })
    mocks.markCopilotSessionViewed.mockResolvedValue({
      id: 444,
      last_viewed_at: '2026-08-11T08:01:00Z',
    })
    mocks.streamCopilotRun.mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('submits the starter question without copying it into the composer', async () => {
    const createRequest = deferred<{ uuid: string; status: string }>()
    mocks.createCopilotRun.mockReturnValue(createRequest.promise)
    const i18n = createI18n({
      legacy: false,
      locale: 'en',
      messages: { en },
      missingWarn: false,
      fallbackWarn: false,
    })
    const wrapper = mountCopilot(i18n)
    await flushPromises()

    const firstChip = wrapper.get('.copilot-chip-box')
    await firstChip.trigger('click')
    await nextTick()

    expect(mocks.createCopilotRun).toHaveBeenCalledTimes(1)
    expect(mocks.createCopilotRun).toHaveBeenCalledWith(
      444,
      en.insight.copilot.chipQuerySopsPrompt,
      expect.stringMatching(/^copilot-444-/),
    )
    expect(wrapper.get('.copilot-input-field').element).toHaveProperty('value', '')
    expect(firstChip.attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('.thinking-panel-live').exists()).toBe(true)
    expect(wrapper.get('.copilot-input-field').attributes('disabled')).toBeDefined()

    createRequest.resolve({ uuid: 'run-444', status: 'queued' })
    await flushPromises()
    wrapper.unmount()
  })

  it('keeps the composer disabled while an active run is being synchronized', async () => {
    const syncRequest = deferred<{
      session_id: number
      messages: never[]
      active_run: null
      run_outcomes: never[]
    }>()
    mocks.listCopilotSessions.mockResolvedValue([
      sessionRow({ uuid: 'run-active', status: 'running' }),
    ])
    mocks.syncCopilotSession.mockReturnValue(syncRequest.promise)
    const i18n = createI18n({
      legacy: false,
      locale: 'en',
      messages: { en },
      missingWarn: false,
      fallbackWarn: false,
    })

    const wrapper = mountCopilot(i18n)
    await flushPromises()

    expect(wrapper.get('.copilot-input-field').attributes('disabled')).toBeDefined()
    expect(mocks.createCopilotRun).not.toHaveBeenCalled()

    syncRequest.resolve({
      session_id: 444,
      messages: [],
      active_run: null,
      run_outcomes: [],
    })
    await flushPromises()
    wrapper.unmount()
  })

  it('restores a pending question and thinking feedback after synchronization', async () => {
    mocks.syncCopilotSession.mockResolvedValue({
      session_id: 444,
      messages: [],
      active_run: null,
      response_state: {
        status: 'submitting',
        started_at: '2026-08-11T08:00:01Z',
        question: 'Recover my pending question',
      },
      run_outcomes: [],
    })
    const i18n = createI18n({
      legacy: false,
      locale: 'en',
      messages: { en },
      missingWarn: false,
      fallbackWarn: false,
    })

    const wrapper = mountCopilot(i18n)
    await flushPromises()

    expect(wrapper.text()).toContain('Recover my pending question')
    expect(wrapper.find('.thinking-panel-live').exists()).toBe(true)
    expect(wrapper.get('.copilot-input-field').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.copilot-send-btn--stop').exists()).toBe(false)
    wrapper.unmount()
  })

  it('synchronizes a terminal idempotent run without opening a stream', async () => {
    mocks.createCopilotRun.mockResolvedValue({ uuid: 'run-complete', status: 'done' })
    const i18n = createI18n({
      legacy: false,
      locale: 'en',
      messages: { en },
      missingWarn: false,
      fallbackWarn: false,
    })
    const wrapper = mountCopilot(i18n)
    await flushPromises()

    await wrapper.get('.copilot-chip-box').trigger('click')
    await flushPromises()

    expect(mocks.createCopilotRun).toHaveBeenCalledTimes(1)
    expect(mocks.streamCopilotRun).not.toHaveBeenCalled()
    expect(mocks.syncCopilotSession).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })
})
