import type { LensCopilotRunOutcome } from '../../../lib/lensApi'
import type { CopilotDisplayMessage } from './types'

export function appendRunOutcomeMessages(
  messages: CopilotDisplayMessage[],
  outcomes: LensCopilotRunOutcome[],
): CopilotDisplayMessage[] {
  const outcomesByRun = new Map(outcomes.map((outcome) => [outcome.run_uuid, outcome]))
  // SourceLens may link a failed Run to its empty output message through
  // Run.output_message while leaving Message.run unset. Keep that message's
  // activity, but attach the adjacent question's terminal Run for rendering.
  const linkedMessages = messages.map((message, index) => {
    const question = messages[index - 1]
    if (
      message.role === 'assistant'
      && !message.runId
      && !message.text?.trim()
      && question?.role === 'user'
      && question.runId
      && outcomesByRun.has(question.runId)
    ) {
      return { ...message, runId: question.runId }
    }
    return message
  })
  // Only a non-empty assistant answer counts as a real response. An empty
  // blocked/failed placeholder must still surface the durable error.
  const answeredRuns = new Set(
    linkedMessages
      .filter((message) => (
        message.role === 'assistant'
        && message.runId
        && Boolean(message.text?.trim())
      ))
      .map((message) => message.runId as string),
  )
  const merged: CopilotDisplayMessage[] = []
  const emittedOutcomes = new Set<string>()

  for (const message of linkedMessages) {
    if (
      message.role === 'assistant'
      && message.runId
      && !message.text?.trim()
    ) {
      if (emittedOutcomes.has(message.runId)) {
        continue
      }
      const outcome = outcomesByRun.get(message.runId)
      if (outcome) {
        emittedOutcomes.add(outcome.run_uuid)
        merged.push({
          ...message,
          text: outcome.message,
          isError: true,
          createdAt: outcome.finished_at || message.createdAt,
        })
        continue
      }
    }

    merged.push(message)

    if (
      message.role !== 'user'
      || !message.runId
      || answeredRuns.has(message.runId)
      || emittedOutcomes.has(message.runId)
    ) {
      continue
    }
    const outcome = outcomesByRun.get(message.runId)
    if (!outcome) continue
    // Prefer attaching to an empty assistant placeholder when one exists later
    // in the list; otherwise insert the durable error after the question.
    const hasEmptyAssistant = linkedMessages.some((row) => (
      row.role === 'assistant'
      && row.runId === message.runId
      && !row.text?.trim()
    ))
    if (hasEmptyAssistant) continue
    emittedOutcomes.add(outcome.run_uuid)
    merged.push({
      id: `run-outcome-${outcome.run_uuid}`,
      role: 'assistant',
      text: outcome.message,
      isError: true,
      createdAt: outcome.finished_at || message.createdAt,
      runId: outcome.run_uuid,
    })
  }
  return merged
}
