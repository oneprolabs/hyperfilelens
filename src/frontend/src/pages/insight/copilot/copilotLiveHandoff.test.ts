import { describe, expect, it } from 'vitest'

import { messagesForLiveHandoff } from './copilotLiveHandoff'
import type { CopilotDisplayMessage } from './types'

function msg(
  partial: Partial<CopilotDisplayMessage> & Pick<CopilotDisplayMessage, 'id' | 'role'>,
): CopilotDisplayMessage {
  return partial
}

describe('messagesForLiveHandoff', () => {
  it('leaves history unchanged when not streaming', () => {
    const messages = [
      msg({ id: 'u1', role: 'user', text: 'Q' }),
      msg({
        id: 'a1',
        role: 'assistant',
        text: '',
        thinking: { steps: [{ message: 'searching' }] },
      }),
    ]
    expect(messagesForLiveHandoff(messages, false)).toBe(messages)
  })

  it('hands the trailing in-progress assistant to the live row while streaming', () => {
    const messages = [
      msg({ id: 'u1', role: 'user', text: 'Q' }),
      msg({
        id: 'a1',
        role: 'assistant',
        runId: 'run-1',
        text: '',
        thinking: {
          steps: [
            { message: 'tool.find_files.start' },
            { message: 'tool.find_files.done' },
          ],
        },
      }),
    ]
    expect(messagesForLiveHandoff(messages, true)).toEqual([messages[0]])
  })

  it('also hides a partial unfinished assistant answer while streaming', () => {
    const messages = [
      msg({ id: 'u1', role: 'user', text: 'Q' }),
      msg({
        id: 'a1',
        role: 'assistant',
        runId: 'run-1',
        text: 'Partial…',
        thinking: { steps: [{ message: 'answering' }] },
      }),
    ]
    expect(messagesForLiveHandoff(messages, true)).toEqual([messages[0]])
  })

  it('keeps a completed trailing answer if the live row is still visible', () => {
    const messages = [
      msg({ id: 'u1', role: 'user', text: 'Q' }),
      msg({
        id: 'a1',
        role: 'assistant',
        runId: 'run-1',
        completedAt: '2026-08-20T02:00:00Z',
        text: 'Done',
        thinking: { duration_seconds: 4, steps: [{ message: 'done' }] },
      }),
    ]
    expect(messagesForLiveHandoff(messages, true)).toEqual(messages)
  })

  it('keeps clarification prompts on the history message', () => {
    const messages = [
      msg({ id: 'u1', role: 'user', text: 'Q' }),
      msg({
        id: 'a1',
        role: 'assistant',
        runId: 'run-1',
        text: '',
        clarificationRequest: { requestId: 'r1', question: 'Which backup?' },
      }),
    ]
    expect(messagesForLiveHandoff(messages, true)).toEqual(messages)
  })

  it('does not strip when the trailing message is the user turn', () => {
    const messages = [
      msg({
        id: 'a0',
        role: 'assistant',
        completedAt: '2026-08-20T01:00:00Z',
        text: 'Earlier',
      }),
      msg({ id: 'u1', role: 'user', text: 'Follow-up' }),
    ]
    expect(messagesForLiveHandoff(messages, true)).toEqual(messages)
  })
})
