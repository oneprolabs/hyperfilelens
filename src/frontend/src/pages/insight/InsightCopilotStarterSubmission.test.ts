// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { ElMessage } from 'element-plus'
import { createI18n } from 'vue-i18n'
import { defineComponent, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../locales/en'
import InsightCopilot from './InsightCopilot.vue'
import CopilotComposer from './copilot/CopilotComposer.vue'

const mocks = vi.hoisted(() => ({
  createCopilotRun: vi.fn(),
  deleteCopilotAttachment: vi.fn(),
  listCopilotAssistants: vi.fn(),
  listCopilotSessions: vi.fn(),
  syncCopilotSession: vi.fn(),
  streamCopilotRun: vi.fn(),
  markCopilotSessionViewed: vi.fn(),
  uploadCopilotAttachment: vi.fn(),
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
  deleteCopilotAttachment: mocks.deleteCopilotAttachment,
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
  listCopilotAssistants: mocks.listCopilotAssistants,
  listCopilotGatewayOptions: vi.fn().mockResolvedValue([]),
  listCopilotSessions: mocks.listCopilotSessions,
  listKnowledgeSources: vi.fn().mockResolvedValue([]),
  listLensGateways: vi.fn().mockResolvedValue([]),
  markCopilotSessionViewed: mocks.markCopilotSessionViewed,
  pinCopilotSession: vi.fn(),
  renameCopilotSession: vi.fn(),
  retryCopilotSession: vi.fn(),
  streamCopilotRun: mocks.streamCopilotRun,
  syncCopilotSession: mocks.syncCopilotSession,
  unpinCopilotSession: vi.fn(),
  uploadCopilotAttachment: mocks.uploadCopilotAttachment,
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, reject, resolve }
}

const SimpleStub = defineComponent({ template: '<div />' })

function sessionRow(
  activeRun: { uuid: string; status: string } | null = null,
  lifecycleStatus = 'ready',
  id = 444,
  createdAt = '2026-08-11T08:00:00Z',
) {
  return {
    id,
    title: 'Chat',
    lifecycle_status: lifecycleStatus,
    status: 'active',
    sl_session_uuid: `session-${id}`,
    sl_assistant_uuid: 'assistant-1',
    last_message_at: null,
    last_assistant_message_at: null,
    last_viewed_at: null,
    has_unread: false,
    active_run_uuid: activeRun?.uuid ?? null,
    active_run_status: activeRun?.status ?? '',
    created_at: createdAt,
    updated_at: createdAt,
  }
}

function mountCopilot(
  i18n: ReturnType<typeof createI18n>,
  sessionSidebar = SimpleStub,
) {
  return mount(InsightCopilot, {
    global: {
      plugins: [i18n],
      stubs: {
        CopilotSessionSidebar: sessionSidebar,
        CopilotContextBar: SimpleStub,
        CopilotLifecycleState: SimpleStub,
        CopilotEmptyState: SimpleStub,
        CopilotShareDialog: SimpleStub,
        DangerConfirmDialog: SimpleStub,
        ElDrawer: SimpleStub,
        ElImage: SimpleStub,
      },
    },
  })
}

describe('InsightCopilot starter question submission', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.listCopilotSessions.mockResolvedValue([sessionRow()])
    mocks.listCopilotAssistants.mockResolvedValue([{
      uuid: 'assistant-1',
      name: 'Backup Assistant',
      slug: 'backup-assistant',
      status: 'active',
      selected_task: 'backup_qa',
      agent_model_ref: 'agent-model',
      multimodal_model_ref: null,
      supports_document_attachments: true,
    }])
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
    mocks.deleteCopilotAttachment.mockResolvedValue(undefined)
    mocks.uploadCopilotAttachment.mockResolvedValue({
      uuid: '00000000-0000-4000-8000-000000000001',
      url: '/api/v1/lens/copilot/sessions/444/attachments/document/?token=signed',
      kind: 'document',
      mime_type: 'application/pdf',
      original_name: 'report.pdf',
      byte_size: 3,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('reserves the measured composer height below the conversation', async () => {
    const i18n = createI18n({
      legacy: false,
      locale: 'en',
      messages: { en },
      missingWarn: false,
      fallbackWarn: false,
    })
    const wrapper = mountCopilot(i18n)
    await flushPromises()

    wrapper.findComponent(CopilotComposer).vm.$emit('resize', 248)
    await nextTick()

    expect(wrapper.get('.copilot-chat-stage').attributes('style')).toContain(
      '--copilot-composer-height: 248px',
    )
    wrapper.unmount()
  })

  it('keeps long lifecycle polling active and stops it after unmount', async () => {
    vi.useFakeTimers()
    mocks.listCopilotSessions.mockResolvedValue([
      sessionRow(null, 'provisioning'),
    ])
    const i18n = createI18n({
      legacy: false,
      locale: 'en',
      messages: { en },
      missingWarn: false,
      fallbackWarn: false,
    })
    const wrapper = mountCopilot(i18n)
    await flushPromises()

    await vi.advanceTimersByTimeAsync(2_400_000)
    await flushPromises()

    expect(mocks.listCopilotSessions.mock.calls.length).toBeGreaterThan(121)
    wrapper.unmount()
    const callsAfterUnmount = mocks.listCopilotSessions.mock.calls.length

    await vi.advanceTimersByTimeAsync(60_000)
    await flushPromises()

    expect(mocks.listCopilotSessions).toHaveBeenCalledTimes(callsAfterUnmount)
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
      [],
      undefined,
    )
    expect(wrapper.get('.copilot-input-field').element).toHaveProperty('value', '')
    expect(firstChip.attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('.thinking-panel-live').exists()).toBe(true)
    expect(wrapper.get('.copilot-input-field').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('.copilot-send-btn--stop').attributes('disabled')).toBeDefined()

    createRequest.resolve({ uuid: 'run-444', status: 'queued' })
    await flushPromises()
    wrapper.unmount()
  })

  it('keeps the composer unavailable while the initial synchronization is pending', async () => {
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
    expect(wrapper.find('.copilot-send-btn--stop').exists()).toBe(true)
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
    expect(wrapper.get('.copilot-input-field').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.copilot-send-btn--stop').exists()).toBe(true)
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

  it('forwards the original SourceLens Run when regenerating an unchanged question', async () => {
    const originalRunUuid = 'c42dfb76-3afd-4ad7-b896-472f71f38586'
    mocks.syncCopilotSession.mockResolvedValue({
      session_id: 444,
      messages: [
        {
          uuid: '00000000-0000-4000-8000-000000000001',
          role: 'user',
          content: 'Original question',
        },
        {
          uuid: '00000000-0000-4000-8000-000000000002',
          role: 'assistant',
          content: 'Original answer',
          run: originalRunUuid,
          completed_at: '2026-08-20T02:00:00Z',
        },
      ],
      active_run: null,
      run_outcomes: [],
    })
    mocks.createCopilotRun.mockResolvedValue({ uuid: 'retry-run', status: 'queued' })
    const i18n = createI18n({
      legacy: false,
      locale: 'en',
      messages: { en },
      missingWarn: false,
      fallbackWarn: false,
    })
    const wrapper = mountCopilot(i18n)
    await flushPromises()

    await wrapper.get('button[aria-label="Regenerate answer"]').trigger('click')
    expect(wrapper.get('.copilot-input-field').element).toHaveProperty(
      'value',
      'Original question',
    )
    await wrapper.get('.copilot-send-btn').trigger('click')
    await flushPromises()

    expect(mocks.createCopilotRun).toHaveBeenCalledWith(
      444,
      'Original question',
      expect.stringMatching(/^copilot-444-/),
      [],
      originalRunUuid,
    )
    wrapper.unmount()
  })

  it('does not reuse a retry Run after switching to another Chat', async () => {
    const originalRunUuid = 'c42dfb76-3afd-4ad7-b896-472f71f38586'
    mocks.listCopilotSessions.mockResolvedValue([
      sessionRow(null, 'ready', 444, '2026-08-11T08:00:00Z'),
      sessionRow(null, 'ready', 445, '2026-08-11T07:00:00Z'),
    ])
    mocks.syncCopilotSession.mockImplementation(async (sessionId: number) => ({
      session_id: sessionId,
      messages: sessionId === 444
        ? [
            {
              uuid: '00000000-0000-4000-8000-000000000001',
              role: 'user',
              content: 'Original question',
            },
            {
              uuid: '00000000-0000-4000-8000-000000000002',
              role: 'assistant',
              content: 'Original answer',
              run: originalRunUuid,
              completed_at: '2026-08-20T02:00:00Z',
            },
          ]
        : [],
      active_run: null,
      run_outcomes: [],
    }))
    mocks.createCopilotRun.mockResolvedValue({ uuid: 'new-run', status: 'queued' })
    const SessionSwitchStub = defineComponent({
      emits: ['select'],
      template: '<button class="switch-session" @click="$emit(\'select\', 445)">Switch</button>',
    })
    const i18n = createI18n({
      legacy: false,
      locale: 'en',
      messages: { en },
      missingWarn: false,
      fallbackWarn: false,
    })
    const wrapper = mountCopilot(i18n, SessionSwitchStub)
    await flushPromises()

    await wrapper.get('button[aria-label="Regenerate answer"]').trigger('click')
    await wrapper.find('.switch-session').trigger('click')
    await flushPromises()

    expect(wrapper.get('.copilot-input-field').element).toHaveProperty(
      'value',
      'Original question',
    )
    await wrapper.get('.copilot-send-btn').trigger('click')
    await flushPromises()

    expect(mocks.createCopilotRun).toHaveBeenCalledWith(
      445,
      'Original question',
      expect.stringMatching(/^copilot-445-/),
      [],
      undefined,
    )
    wrapper.unmount()
  })

  it('starts a new Run when a regenerated question adds a new attachment', async () => {
    const originalRunUuid = 'c42dfb76-3afd-4ad7-b896-472f71f38586'
    mocks.syncCopilotSession.mockResolvedValue({
      session_id: 444,
      messages: [
        {
          uuid: '00000000-0000-4000-8000-000000000001',
          role: 'user',
          content: 'Original question',
        },
        {
          uuid: '00000000-0000-4000-8000-000000000002',
          role: 'assistant',
          content: 'Original answer',
          run: originalRunUuid,
          completed_at: '2026-08-20T02:00:00Z',
        },
      ],
      active_run: null,
      run_outcomes: [],
    })
    mocks.createCopilotRun.mockResolvedValue({ uuid: 'new-run', status: 'queued' })
    const i18n = createI18n({
      legacy: false,
      locale: 'en',
      messages: { en },
      missingWarn: false,
      fallbackWarn: false,
    })
    const wrapper = mountCopilot(i18n)
    await flushPromises()

    await wrapper.get('button[aria-label="Regenerate answer"]').trigger('click')
    const fileInput = wrapper.get('input[type="file"]')
    Object.defineProperty(fileInput.element, 'files', {
      configurable: true,
      value: [new File(['pdf'], 'report.pdf', { type: 'application/pdf' })],
    })
    await fileInput.trigger('change')
    await flushPromises()
    await wrapper.get('.copilot-send-btn').trigger('click')
    await flushPromises()

    expect(mocks.createCopilotRun).toHaveBeenCalledWith(
      444,
      'Original question',
      expect.stringMatching(/^copilot-444-/),
      ['00000000-0000-4000-8000-000000000001'],
      undefined,
    )
    wrapper.unmount()
  })

  it('does not poll an in-flight submission before its request resolves', async () => {
    vi.useFakeTimers()
    const runRequest = deferred<{ uuid: string; status: string }>()
    mocks.createCopilotRun.mockReturnValue(runRequest.promise)
    const i18n = createI18n({
      legacy: false,
      locale: 'en',
      messages: { en },
      missingWarn: false,
      fallbackWarn: false,
    })
    const wrapper = mountCopilot(i18n)
    await flushPromises()
    await wrapper.get('.copilot-input-field').setValue('Wait for acceptance')

    await wrapper.get('.copilot-send-btn').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(12_000)
    await flushPromises()

    expect(mocks.createCopilotRun).toHaveBeenCalledTimes(1)
    expect(mocks.syncCopilotSession).toHaveBeenCalledTimes(1)

    runRequest.reject({ status: 400, message: 'Request rejected.' })
    await flushPromises()
    wrapper.unmount()
  })

  it('restores text and reusable attachments after a definitive rejection', async () => {
    mocks.createCopilotRun.mockRejectedValue({
      status: 400,
      message: 'The selected model cannot run this request.',
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
    const fileInput = wrapper.get('input[type="file"]')
    Object.defineProperty(fileInput.element, 'files', {
      configurable: true,
      value: [new File(['pdf'], 'report.pdf', { type: 'application/pdf' })],
    })
    await fileInput.trigger('change')
    await flushPromises()
    const composer = wrapper.get('.copilot-input-field')
    await composer.setValue('Summarize this report')

    await wrapper.get('.copilot-send-btn').trigger('click')
    await flushPromises()

    expect(mocks.createCopilotRun).toHaveBeenCalledWith(
      444,
      'Summarize this report',
      expect.stringMatching(/^copilot-444-/),
      ['00000000-0000-4000-8000-000000000001'],
      undefined,
    )
    expect(wrapper.get('.copilot-input-field').element).toHaveProperty(
      'value',
      'Summarize this report',
    )
    expect(wrapper.get('.copilot-attachment-card__name').text()).toBe('report.pdf')
    wrapper.unmount()
    expect(mocks.deleteCopilotAttachment).toHaveBeenCalledWith(
      444,
      '00000000-0000-4000-8000-000000000001',
      '/api/v1/lens/copilot/sessions/444/attachments/document/?token=signed',
    )
  })

  it('localizes a SourceLens document attachment diagnostic', async () => {
    const errorMessage = vi.spyOn(ElMessage, 'error')
    mocks.uploadCopilotAttachment.mockRejectedValue({
      status: 400,
      message: 'Validation failed.',
      code: 'VALIDATION.FAILED',
      errorCode: 'VALIDATION.FAILED',
      meta: {
        diagnostic: 'DOCUMENT_ATTACHMENTS_UNSUPPORTED_BY_LENSNODE',
      },
    })
    const i18n = createI18n({
      legacy: false,
      locale: 'en',
      messages: { en },
      missingWarn: false,
      fallbackWarn: false,
    })
    const wrapper = mountCopilot(i18n)
    try {
      await flushPromises()
      const fileInput = wrapper.get('input[type="file"]')
      Object.defineProperty(fileInput.element, 'files', {
        configurable: true,
        value: [new File(['pdf'], 'report.pdf', { type: 'application/pdf' })],
      })

      await fileInput.trigger('change')
      await flushPromises()

      expect(errorMessage).toHaveBeenCalledWith(expect.objectContaining({
        message: en.insight.copilot.attachmentUnsupported,
      }))
    } finally {
      errorMessage.mockRestore()
      wrapper.unmount()
    }
  })

  it('removes the optimistic message when rejection reconciliation also fails', async () => {
    mocks.createCopilotRun.mockRejectedValue({
      status: 400,
      message: 'The selected model cannot run this request.',
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
    mocks.syncCopilotSession.mockRejectedValueOnce(
      new Error('Reconciliation unavailable'),
    )
    const composer = wrapper.get('.copilot-input-field')
    await composer.setValue('Question rejected by the API')

    await wrapper.get('.copilot-send-btn').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.message-row-user')).toHaveLength(0)
    expect(composer.element).toHaveProperty(
      'value',
      'Question rejected by the API',
    )
    expect(composer.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('cleans up a rejected document after the component is unmounted', async () => {
    const runRequest = deferred<{ uuid: string; status: string }>()
    mocks.createCopilotRun.mockReturnValue(runRequest.promise)
    const i18n = createI18n({
      legacy: false,
      locale: 'en',
      messages: { en },
      missingWarn: false,
      fallbackWarn: false,
    })
    const wrapper = mountCopilot(i18n)
    await flushPromises()
    const fileInput = wrapper.get('input[type="file"]')
    Object.defineProperty(fileInput.element, 'files', {
      configurable: true,
      value: [new File(['pdf'], 'report.pdf', { type: 'application/pdf' })],
    })
    await fileInput.trigger('change')
    await flushPromises()
    await wrapper.get('.copilot-input-field').setValue('Summarize this report')
    await wrapper.get('.copilot-send-btn').trigger('click')
    await flushPromises()

    wrapper.unmount()
    runRequest.reject({
      status: 400,
      message: 'The selected model cannot run this request.',
    })
    await flushPromises()

    expect(mocks.deleteCopilotAttachment).toHaveBeenCalledWith(
      444,
      '00000000-0000-4000-8000-000000000001',
      '/api/v1/lens/copilot/sessions/444/attachments/document/?token=signed',
    )
  })

  it('preserves an optimistic attachment submission after an uncertain failure', async () => {
    mocks.createCopilotRun.mockRejectedValue({
      status: 503,
      message: 'The submission result is temporarily unavailable.',
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
    mocks.syncCopilotSession.mockRejectedValueOnce(
      new Error('Reconciliation unavailable'),
    )
    const fileInput = wrapper.get('input[type="file"]')
    Object.defineProperty(fileInput.element, 'files', {
      configurable: true,
      value: [new File(['pdf'], 'report.pdf', { type: 'application/pdf' })],
    })
    await fileInput.trigger('change')
    await flushPromises()
    await wrapper.get('.copilot-input-field').setValue('Summarize this report')

    await wrapper.get('.copilot-send-btn').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.message-row-user')).toHaveLength(1)
    expect(wrapper.text()).toContain('Summarize this report')
    expect(wrapper.get('.copilot-input-field').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.copilot-send-btn--stop').exists()).toBe(true)
    expect(mocks.deleteCopilotAttachment).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
