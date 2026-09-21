import type { CopilotDisplayMessage } from './types'

/**
 * SourceLens Chat.vue hands the trailing in-progress assistant placeholder to
 * the live progress row while a run is active, so history does not render a
 * second Agent-activity card beside the live one.
 */
export function messagesForLiveHandoff(
  messages: CopilotDisplayMessage[],
  streaming: boolean,
): CopilotDisplayMessage[] {
  if (!streaming || messages.length === 0) return messages

  const last = messages[messages.length - 1]
  if (!last || last.role !== 'assistant' || last.isWelcome || last.isError) {
    return messages
  }
  // Clarification UI lives on the history message while awaiting user input.
  if (last.clarificationRequest) return messages
  // A finished answer must stay visible if a live row is briefly still up.
  if (last.completedAt && (last.text || '').trim()) return messages

  return messages.slice(0, -1)
}
