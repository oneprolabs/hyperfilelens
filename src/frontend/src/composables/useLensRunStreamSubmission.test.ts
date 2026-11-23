// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  streamCopilotRun: vi.fn(),
}))

vi.mock('../lib/lensApi', () => ({
  streamCopilotRun: mocks.streamCopilotRun,
}))

import {
  applySessionActiveRun,
  applySessionRunSubmission,
  beginSessionRunSubmission,
  clearSessionRunSubmission,
  consumeSessionStream,
  getSessionRunStream,
  resetSessionRunStream,
} from './useLensRunStream'

describe('Copilot local submission feedback', () => {
  const sessionId = 44_445

  afterEach(() => {
    resetSessionRunStream(sessionId)
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it('becomes active before SourceLens returns a run', () => {
    beginSessionRunSubmission(sessionId)

    const state = getSessionRunStream(sessionId)
    expect(state.isSubmitting).toBe(true)
    expect(state.runUuid).toBeNull()
    expect(state.thinkingSteps).toEqual([])
  })

  it('hands authority to the SourceLens run once it is created', () => {
    beginSessionRunSubmission(sessionId)
    applySessionActiveRun(sessionId, 'run-1', 'queued', '', [])

    const state = getSessionRunStream(sessionId)
    expect(state.isSubmitting).toBe(false)
    expect(state.runUuid).toBe('run-1')
    expect(state.runStatus).toBe('queued')
  })

  it('restores structured runtime events from an active-run snapshot', () => {
    applySessionActiveRun(sessionId, 'run-structured', 'running', '', [
      {
        event_type: 'stage.updated',
        payload: { summary: 'Indexing documents' },
      },
    ])

    const state = getSessionRunStream(sessionId)
    expect(state.thinkingSteps).toHaveLength(1)
    expect(state.thinkingSteps[0].message).toBe('Indexing documents')
    expect(state.thinkingSteps[0].eventType).toBe('stage.updated')
  })

  it('retains a clarification snapshot without treating it as streaming', () => {
    applySessionActiveRun(sessionId, 'run-clarification', 'awaiting_user_input', '', [])

    const state = getSessionRunStream(sessionId)
    expect(state.runUuid).toBe('run-clarification')
    expect(state.runStatus).toBe('awaiting_user_input')
    expect(state.isStreaming).toBe(false)
  })

  it('shows a readable upstream model-stream failure without retrying in HFL', async () => {
    mocks.streamCopilotRun.mockImplementation(async (_sessionId, _runUuid, emit) => {
      emit({ type: 'error', error: 'MODEL_STREAM_ERROR' })
    })
    await consumeSessionStream(sessionId, 'run-stream-failure')
    expect(getSessionRunStream(sessionId).streamError).toBe(
      'The AI model response was interrupted. Try again or choose a different model.',
    )
    expect(mocks.streamCopilotRun).toHaveBeenCalledTimes(1)
  })

  it('keeps upstream live step timestamps without timing a reconnect snapshot', async () => {
    mocks.streamCopilotRun.mockImplementation(async (_sessionId, _runUuid, emit) => {
      const event = { event_type: 'plan.updated', payload: { steps: [{ id: 'one', title: 'Search', status: 'in_progress' }] } }
      emit({ type: 'sync', ts: '2026-11-23T00:00:10Z', steps: [
        { sequence: 1, detail: { events: [event] } },
      ] })
      emit({ type: 'step', ts: '2026-11-23T00:00:15Z', sequence: 2, detail: {
        events: [{ ...event, payload: { ...event.payload, revision: 2 } }],
      } })
    })
    await consumeSessionStream(sessionId, 'run-timing')
    const steps = getSessionRunStream(sessionId).thinkingSteps
    expect(steps).toHaveLength(2)
    expect(steps[0].observedAt).toBeUndefined()
    expect(steps[1].observedAt).toBe('2026-11-23T00:00:15Z')
    const snapshot = steps.map((step) => ({
      message: step.message, event_type: step.eventType, payload: step.payload,
    }))
    applySessionActiveRun(sessionId, 'run-timing', 'running', '', snapshot)
    expect(getSessionRunStream(sessionId).thinkingSteps[1].observedAt).toBe('2026-11-23T00:00:15Z')
    applySessionActiveRun(sessionId, 'other-run', 'running', '', snapshot)
    expect(getSessionRunStream(sessionId).thinkingSteps.every((step) => step.observedAt == null)).toBe(true)
  })

  it('isolates plan event deduplication, sequence counters and snapshot observations by delegated Run', async () => {
    const plan = (status: string, revision: number) => ({
      event_type: 'plan.updated',
      payload: { revision, steps: [{ id: 'step-1', title: 'Search', status }] },
    })
    const source = (run?: string) => run ? {
      delegated_run_uuid: run, assistant_name: 'Reader', delegated_task: 'Same task',
    } : {}
    mocks.streamCopilotRun.mockImplementation(async (_sessionId, _runUuid, emit) => {
      for (const [run, second] of [[undefined, '00'], ['child-one', '05'], ['child-two', '06']]) {
        emit({
          type: 'step', sequence: 1, ...source(run), ts: `2026-11-23T00:00:${second}Z`,
          detail: { events: [plan('in_progress', 1)] },
        })
      }
      const finished = []
      for (const [run, second] of [['child-one', '08'], ['child-two', '09'], [undefined, '20']]) {
        const step = {
          sequence: 1, ...source(run), ts: `2026-11-23T00:00:${second}Z`,
          detail: { events: [plan('in_progress', 1), plan('completed', 2)] },
        }
        emit({ ...step, type: 'step' })
        finished.push(step)
      }
      emit({ type: 'sync', steps: finished })
    })
    await consumeSessionStream(sessionId, 'run-scopes')
    const steps = getSessionRunStream(sessionId).thinkingSteps
    expect(steps.map((step) => step.delegatedRunUuid)).toEqual([
      undefined, 'child-one', 'child-two', 'child-one', 'child-two', undefined,
    ])
    expect(steps.map((step) => step.observedAt)).toEqual(
      ['00', '05', '06', '08', '09', '20'].map((second) => `2026-11-23T00:00:${second}Z`),
    )
    const snapshot = steps.map((step) => ({
      message: step.message, event_type: step.eventType, payload: step.payload,
      assistant_name: step.assistantName, delegated_task: step.delegatedTask,
      delegated_run_uuid: step.delegatedRunUuid,
    }))
    applySessionActiveRun(sessionId, 'run-scopes', 'running', '', snapshot)
    expect(getSessionRunStream(sessionId).thinkingSteps.map((step) => step.observedAt))
      .toEqual(steps.map((step) => step.observedAt))
    const flattenedHistory = snapshot.map((step) => ({ ...step, delegated_run_uuid: undefined }))
    applySessionActiveRun(sessionId, 'run-scopes', 'running', '', flattenedHistory)
    const restored = getSessionRunStream(sessionId).thinkingSteps
    expect(restored[0].observedAt).toBe('2026-11-23T00:00:00Z')
    expect(restored.slice(1, 5).every((step) => step.observedAt == null && step.delegatedRunUuid == null)).toBe(true)
  })

  it('restores delegated identity from flattened history only when the assistant/task match is unambiguous', async () => {
    mocks.streamCopilotRun.mockImplementation(async (_sessionId, _runUuid, emit) => {
      for (const [run, task, second] of [['child-one', 'First task', '00'], ['child-two', 'Second task', '05']]) {
        emit({
          type: 'step', sequence: 1, delegated_run_uuid: run,
          assistant_name: 'Reader', delegated_task: task, ts: `2026-11-23T00:00:${second}Z`,
          detail: { events: [{
            event_type: 'plan.updated',
            payload: { revision: 1, steps: [{ id: 'step-1', title: 'Search', status: 'in_progress' }] },
          }] },
        })
      }
    })
    await consumeSessionStream(sessionId, 'run-history')
    const steps = getSessionRunStream(sessionId).thinkingSteps
    const snapshot = steps.map((step) => ({
      message: step.message, event_type: step.eventType, payload: step.payload,
      assistant_name: step.assistantName, delegated_task: step.delegatedTask,
    }))
    applySessionActiveRun(sessionId, 'run-history', 'running', '', snapshot)
    expect(getSessionRunStream(sessionId).thinkingSteps.map((step) => [step.delegatedRunUuid, step.observedAt]))
      .toEqual(steps.map((step) => [step.delegatedRunUuid, step.observedAt]))
    applySessionActiveRun(sessionId, 'run-history', 'running', '', [
      ...snapshot,
      { ...snapshot[0], payload: { revision: 2, steps: [{ id: 'step-1', title: 'Search', status: 'completed' }] } },
    ])
    const completed = getSessionRunStream(sessionId).thinkingSteps[2]
    expect(completed.delegatedRunUuid).toBe('child-one')
    expect(completed.observedAt).toBeUndefined()
    // The incoming history can contain another indistinguishable Run which
    // this browser never observed. One known candidate must not identify both.
    applySessionActiveRun(sessionId, 'run-history', 'running', '', [
      { ...snapshot[0], delegated_run_uuid: 'child-one' },
      snapshot[0], snapshot[1],
    ])
    const restored = getSessionRunStream(sessionId).thinkingSteps
    expect(restored[0].delegatedRunUuid).toBe('child-one')
    expect(restored[1].delegatedRunUuid).toBeUndefined()
    expect(restored[1].observedAt).toBeUndefined()
    expect(restored[2].delegatedRunUuid).toBe('child-two')
  })

  it('clears provisional feedback when run creation fails', () => {
    beginSessionRunSubmission(sessionId)
    clearSessionRunSubmission(sessionId)

    expect(getSessionRunStream(sessionId).isSubmitting).toBe(false)
  })

  it('keeps elapsed thinking time when the SourceLens stream takes over', async () => {
    vi.useFakeTimers()
    let finishStream!: () => void
    mocks.streamCopilotRun.mockImplementation(
      () => new Promise<void>((resolve) => {
        finishStream = resolve
      }),
    )

    beginSessionRunSubmission(sessionId)
    await vi.advanceTimersByTimeAsync(2_000)
    expect(getSessionRunStream(sessionId).thinkingElapsedSeconds).toBe(2)

    const stream = consumeSessionStream(sessionId, 'run-1')
    await vi.advanceTimersByTimeAsync(1_000)
    expect(getSessionRunStream(sessionId).thinkingElapsedSeconds).toBe(3)

    finishStream()
    await stream
  })

  it('restores elapsed thinking time from the SourceLens creation anchor', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-11T02:00:10Z'))
    let finishStream!: () => void
    mocks.streamCopilotRun.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finishStream = resolve
        }),
    )

    applySessionActiveRun(
      sessionId,
      'run-reconnected',
      'running',
      '',
      [],
      '2026-08-11T02:00:04Z',
    )
    const stream = consumeSessionStream(sessionId, 'run-reconnected')

    expect(getSessionRunStream(sessionId).thinkingElapsedSeconds).toBe(6)
    await vi.advanceTimersByTimeAsync(2_000)
    expect(getSessionRunStream(sessionId).thinkingElapsedSeconds).toBe(8)

    finishStream()
    await stream
  })

  it('restores a durable submission before a SourceLens Run exists', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-11T02:00:10Z'))

    applySessionRunSubmission(sessionId, '2026-08-11T02:00:04Z')

    const state = getSessionRunStream(sessionId)
    expect(state.isSubmitting).toBe(true)
    expect(state.runUuid).toBeNull()
    expect(state.thinkingElapsedSeconds).toBe(6)
  })

  it('does not let a replaced stream clear the active stream state', async () => {
    vi.useFakeTimers()
    let finishFirst!: () => void
    let finishSecond!: () => void
    mocks.streamCopilotRun
      .mockImplementationOnce(
        () =>
          new Promise<void>((resolve) => {
            finishFirst = resolve
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise<void>((resolve) => {
            finishSecond = resolve
          }),
      )
    const firstFinished = vi.fn()
    const secondFinished = vi.fn()

    const firstStream = consumeSessionStream(sessionId, 'run-old', firstFinished)
    await vi.advanceTimersByTimeAsync(1_000)
    const secondStream = consumeSessionStream(sessionId, 'run-current', secondFinished)
    const elapsedAtReplacement = getSessionRunStream(sessionId).thinkingElapsedSeconds

    finishFirst()
    await firstStream
    expect(getSessionRunStream(sessionId).runUuid).toBe('run-current')
    expect(getSessionRunStream(sessionId).streamAttached).toBe(true)
    expect(getSessionRunStream(sessionId).isStreaming).toBe(true)
    expect(firstFinished).not.toHaveBeenCalled()
    expect(secondFinished).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1_000)
    expect(getSessionRunStream(sessionId).thinkingElapsedSeconds).toBeGreaterThan(
      elapsedAtReplacement,
    )

    finishSecond()
    await secondStream
    expect(getSessionRunStream(sessionId).streamAttached).toBe(false)
    expect(secondFinished).toHaveBeenCalledTimes(1)
  })

  it('keeps distinct phase.changed steps and promotes queued → running / streaming', async () => {
    let onData: ((payload: unknown) => void) | undefined
    let finishStream!: () => void
    mocks.streamCopilotRun.mockImplementation(
      (_sessionId: number, _runUuid: string, handler: (payload: unknown) => void) => {
        onData = handler
        return new Promise<void>((resolve) => {
          finishStream = resolve
        })
      },
    )

    applySessionActiveRun(sessionId, 'run-phases', 'queued', '', [])
    const stream = consumeSessionStream(sessionId, 'run-phases')
    await Promise.resolve()

    onData?.({
      type: 'step',
      sequence: 1,
      detail: {
        events: [
          {
            event_type: 'phase.changed',
            agent_event: 'workflow.phase.changed',
            visibility: 'user',
            payload: { phase: 'analyzing' },
          },
          {
            event_type: 'phase.changed',
            agent_event: 'workflow.phase.changed',
            visibility: 'user',
            payload: { phase: 'answering' },
          },
        ],
      },
    })

    const afterPhases = getSessionRunStream(sessionId)
    expect(afterPhases.runStatus).toBe('running')
    expect(afterPhases.thinkingSteps.map((step) => step.payload?.phase)).toEqual([
      'analyzing',
      'answering',
    ])

    onData?.({ type: 'token', content: 'Hello' })
    expect(getSessionRunStream(sessionId).runStatus).toBe('streaming')
    expect(getSessionRunStream(sessionId).partialAnswer).toBe('Hello')

    // A stale queued snapshot must not wipe the advanced live status.
    onData?.({ type: 'sync', status: 'queued', steps: [] })
    expect(getSessionRunStream(sessionId).runStatus).toBe('streaming')

    finishStream()
    await stream
  })
})
