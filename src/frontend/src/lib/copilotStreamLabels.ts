export type ThinkingStepLike = {
  message?: string
  agentEvent?: string
  activity?: string
  agent_event?: string
}

const INTERNAL_EVENT_PREFIXES = ['deepagents.', 'resources.', 'llm.']

const EVENT_LABELS: Record<string, string> = {
  'deepagents.runtime.start': 'Starting analysis',
  'deepagents.runtime.resume': 'Resuming analysis',
  'deepagents.runtime.done': 'Analysis finished',
  'deepagents.runtime.outcome': 'Finalizing results',
  'deepagents.runtime.error': 'Analysis error',
  'deepagents.agent.create': 'Preparing tools',
  'deepagents.agent.invoke': 'Running agent',
  'deepagents.agent.truncated': 'Reached turn limit',
  'deepagents.agent.wrapup': 'Wrapping up agent',
  'deepagents.agent.soft_deadline': 'Approaching time limit',
  'deepagents.agent.soft_deadline.requested': 'Requesting extra time',
  'deepagents.agent.token_budget': 'Token budget reached',
  'deepagents.plan.required': 'Planning next steps',
  'deepagents.offload.configured': 'Setting up background tasks',
  'deepagents.stream.recovering': 'Recovering stream',
  'deepagents.answer.recovery': 'Recovering answer',
  'deepagents.answer.promise_recovery': 'Recovering answer',
  'deepagents.capability.warning': 'Capability degraded',
  'deepagents.capability.recovered': 'Capability recovered',
  'deepagents.capability.exhausted': 'Capability exhausted',
  'deepagents.summarization.enabled': 'Context summarization enabled',
  'deepagents.summarization.compacted': 'Context compacted',
  'resources.materialized': 'Loading skills and tools',
  'llm.response': 'Model responded',
  queue: 'Queued',
}

const TOOL_PREFIX = 'tool.'

function toolLabel(raw: string): string | null {
  const name = raw.replace(/^tool\./, '').replace(/\.(start|done|invoke)$/, '')
  const labels: Record<string, string> = {
    find_files: 'Searching files',
    ls: 'Listing workspace',
    read_workspace_file: 'Reading file',
    read_file: 'Reading file',
    search_workspace: 'Searching workspace',
    glob_files: 'Finding files by pattern',
  }
  if (labels[name]) return labels[name]
  if (raw.startsWith(TOOL_PREFIX)) {
    return `Running ${name.replace(/_/g, ' ')}`
  }
  return null
}

function isInternalRawMessage(message: string): boolean {
  const trimmed = message.trim()
  if (!trimmed) return true
  if (trimmed.startsWith('→')) return false
  return INTERNAL_EVENT_PREFIXES.some((prefix) => trimmed.startsWith(prefix))
}

/** Map LensNode / SourceLens step payloads to user-facing labels. */
export function formatThinkingStepLabel(step: ThinkingStepLike): string {
  const message = (step.message || '').trim()
  const event = (step.agentEvent || step.activity || step.agent_event || '').trim()

  if (message.startsWith('→')) {
    const action = message.slice(1).trim()
    const tool = toolLabel(`tool.${action.split(/[×\s]/)[0]}`)
    return tool ? `${tool}…` : message
  }

  if (event) {
    if (EVENT_LABELS[event]) return EVENT_LABELS[event]
    const tool = toolLabel(event)
    if (tool) return tool
  }

  if (message && !isInternalRawMessage(message)) return message

  if (event && EVENT_LABELS[event]) return EVENT_LABELS[event]
  if (event) {
    const tool = toolLabel(event)
    if (tool) return tool
  }

  return message || event || 'Working…'
}
