import { describe, expect, it } from 'vitest'
import { appendRunOutcomeMessages } from './runOutcomes'

describe('appendRunOutcomeMessages', () => {
  it('adds a durable failure after the matching user message', () => {
    const merged = appendRunOutcomeMessages(
      [{ id: 'question', role: 'user', text: 'List files', runId: 'run-1' }],
      [{
        run_uuid: 'run-1',
        status: 'failed',
        error_code: 'MODEL_PROVIDER_ERROR',
        message: 'Check model quota.',
      }],
    )

    expect(merged).toHaveLength(2)
    expect(merged[1]).toMatchObject({
      id: 'run-outcome-run-1',
      role: 'assistant',
      isError: true,
      text: 'Check model quota.',
    })
  })

  it('does not duplicate an outcome when an assistant response exists', () => {
    const merged = appendRunOutcomeMessages(
      [
        { id: 'question', role: 'user', text: 'List files', runId: 'run-1' },
        { id: 'answer', role: 'assistant', text: 'Done', runId: 'run-1' },
      ],
      [{
        run_uuid: 'run-1',
        status: 'failed',
        error_code: 'MODEL_PROVIDER_ERROR',
        message: 'Check model quota.',
      }],
    )

    expect(merged).toHaveLength(2)
  })

  it('still surfaces the failure when the assistant placeholder has no answer text', () => {
    const merged = appendRunOutcomeMessages(
      [
        { id: 'question', role: 'user', text: 'List files', runId: 'run-1' },
        {
          id: 'empty-answer',
          role: 'assistant',
          text: '',
          runId: 'run-1',
          thinking: { outcome: 'blocked', steps: [] },
        },
      ],
      [{
        run_uuid: 'run-1',
        status: 'failed',
        error_code: 'AI_RUN_FAILED',
        message: 'No answer was generated for this question. Please try again.',
      }],
    )

    expect(merged).toHaveLength(2)
    expect(merged[1]).toMatchObject({
      id: 'empty-answer',
      role: 'assistant',
      isError: true,
      text: 'No answer was generated for this question. Please try again.',
      thinking: { outcome: 'blocked', steps: [] },
    })
  })

  it('renders one failure for duplicate empty assistant placeholders of one run', () => {
    const merged = appendRunOutcomeMessages(
      [
        {
          id: 'user-1',
          role: 'user',
          text: 'Question',
          createdAt: '2026-09-30T05:00:00Z',
          runId: 'run-1',
        },
        {
          id: 'assistant-empty-1',
          role: 'assistant',
          text: '',
          createdAt: '2026-09-30T05:00:01Z',
          runId: 'run-1',
        },
        {
          id: 'assistant-empty-2',
          role: 'assistant',
          text: '',
          createdAt: '2026-09-30T05:00:02Z',
          runId: 'run-1',
        },
      ],
      [
        {
          run_uuid: 'run-1',
          status: 'failed',
          error_code: 'AI_RUN_FAILED',
          message: 'No answer was generated for this question. Please try again.',
          finished_at: '2026-09-30T05:00:03Z',
        },
      ],
    )

    expect(merged.filter((message) => message.isError)).toHaveLength(1)
    expect(merged).toHaveLength(2)
  })

  it('attaches a failed Run to its empty output message when SourceLens omits Message.run', () => {
    const merged = appendRunOutcomeMessages(
      [
        { id: 'question', role: 'user', text: 'List files', runId: 'run-1' },
        {
          id: 'empty-output',
          role: 'assistant',
          text: '',
          createdAt: '2026-09-30T05:33:47Z',
          thinking: { steps: [{ type: 'retrieval', status: 'failed' }] },
        },
      ],
      [{
        run_uuid: 'run-1',
        status: 'failed',
        error_code: 'AI_RUN_FAILED',
        message: 'No answer was generated for this question. Please try again.',
        finished_at: '2026-09-30T05:34:41Z',
      }],
    )

    expect(merged).toHaveLength(2)
    expect(merged[1]).toMatchObject({
      id: 'empty-output',
      runId: 'run-1',
      isError: true,
      createdAt: '2026-09-30T05:34:41Z',
      thinking: { steps: [{ type: 'retrieval', status: 'failed' }] },
    })
  })
})
