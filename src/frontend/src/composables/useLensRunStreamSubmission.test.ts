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
})
