import { describe, expect, it } from 'vitest'
import { selectCopilotLiveInterim } from './copilotLiveStatus'

describe('selectCopilotLiveInterim', () => {
  it('uses the run status until SourceLens reports a phase or activity', () => {
    expect(selectCopilotLiveInterim({ runStatus: 'queued' }).key).toBe('insight.copilot.liveStatusQueued')
    expect(selectCopilotLiveInterim({ runStatus: 'queued', queuePosition: 0 }).key).toBe('insight.copilot.liveStatusQueuedNext')
    expect(selectCopilotLiveInterim({ runStatus: 'queued', queuePosition: 2 })).toEqual({
      key: 'insight.copilot.liveStatusQueuedPosition',
      params: { position: 2 },
    })
    expect(selectCopilotLiveInterim({ runStatus: 'running' }).key).toBe('insight.copilot.liveStatusRunning')
    expect(selectCopilotLiveInterim({ runStatus: 'streaming' }).key).toBe('insight.copilot.liveStatusGenerating')
    expect(selectCopilotLiveInterim({ runStatus: null }).key).toBe('insight.copilot.liveStatusWaiting')
  })

  it('prefers the runtime phase, then document progress, then a disconnected node', () => {
    const steps = [
      { message: 'phase.changed', eventType: 'phase.changed', payload: { phase: 'analyzing' } },
      { message: 'phase.changed', eventType: 'phase.changed', payload: { phase: 'answering' } },
    ]
    expect(selectCopilotLiveInterim({ runStatus: 'running', steps }).key).toBe('insight.copilot.livePhase.answering')
    // Phase wins even while the run status is still queued (SourceLens status can lag).
    expect(selectCopilotLiveInterim({ runStatus: 'queued', steps }).key).toBe('insight.copilot.livePhase.answering')
    expect(selectCopilotLiveInterim({
      runStatus: 'running',
      steps: [
        ...steps,
        {
          message: 'document.progress',
          eventType: 'document.progress',
          payload: { revision: 1, stage: 'extracting_text', document_index: 1, document_total: 3 },
        },
      ],
    })).toEqual({
      key: 'insight.copilot.liveDocumentExtracting',
      params: { current: 1, total: 3 },
    })
    expect(selectCopilotLiveInterim({
      runStatus: 'streaming',
      resumeBy: '2026-09-21T00:00:00Z',
      steps,
    }).key).toBe('insight.copilot.liveStatusAwaitingResume')
  })

  it('shows Analyzing instead of Queued once runtime progress events exist', () => {
    expect(selectCopilotLiveInterim({
      runStatus: 'queued',
      steps: [{ message: 'deepagents.runtime.start', agentEvent: 'deepagents.runtime.start' }],
    }).key).toBe('insight.copilot.liveStatusRunning')
  })
})
