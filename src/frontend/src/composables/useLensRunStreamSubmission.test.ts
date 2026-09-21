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
