import type { ThinkingStep } from '../../../composables/useLensRunStream'
import type { LensChatThinkingStep } from '../../../lib/lensApi'

export type TimelineStep = ThinkingStep | LensChatThinkingStep

export type CopilotThinkingActivity = {
  id: string
  title: string
  titleKey?: string
  count: number
  details: string[]
  status: 'completed' | 'in_progress' | 'failed'
}

const SOURCE_ACTIVITY_LABELS: Record<string, string> = {
  analyzingResults: 'Analyzing collected results',
  findingCapability: 'Finding the required capability',
  preparingOutput: 'Preparing the output',
  queryingData: 'Querying business data',
  readingContext: 'Reading task context',
  readingSources: 'Reading relevant sources',
  searchingSources: 'Searching relevant sources',
  usingCapability: 'Running the required operation',
}

const SOURCE_ACTIVITY_KEYS: Record<string, string> = {
  analyzingResults: 'insight.copilot.agentActivityAnalyzingResults',
  findingCapability: 'insight.copilot.agentActivityFindingCapability',
  preparingOutput: 'insight.copilot.agentActivityPreparingOutput',
  queryingData: 'insight.copilot.agentActivityQueryingData',
  readingContext: 'insight.copilot.agentActivityReadingContext',
  readingSources: 'insight.copilot.agentActivityReadingSources',
  searchingSources: 'insight.copilot.agentActivitySearchingSources',
  usingCapability: 'insight.copilot.agentActivityUsingCapability',
}

function sourceActivityKind(tool: string): string {
  if (['analyze_structured_output', 'inspect_saved_output', 'run_skill_transform'].includes(tool)) {
    return 'analyzingResults'
  }
  if (['run_skill_script', 'call_skill_api'].includes(tool) || tool.startsWith('mcp__')) {
    return 'queryingData'
  }
  if (['read_file', 'ls'].includes(tool)) return 'readingContext'
  if (['search_workspace', 'find_files', 'glob_files'].includes(tool)) return 'searchingSources'
  if (['read_workspace_file', 'git_diff', 'git_log', 'summarize_recent_changes'].includes(tool)) {
    return 'readingSources'
  }
  if (['write_file', 'save_deliverable'].includes(tool)) return 'preparingOutput'
  if (tool === 'tool_search') return 'findingCapability'
  return 'usingCapability'
}

function eventCategory(step: TimelineStep): string {
  return String(
    ('event_type' in step ? step.event_type : ('eventType' in step ? step.eventType : ''))
      || ('agent_event' in step ? step.agent_event : step.agentEvent)
      || step.activity
      || '',
  ).toLowerCase()
}

function toolName(step: TimelineStep, category: string): string {
  const explicit = 'toolName' in step ? step.toolName : undefined
  if (explicit) return String(explicit).toLowerCase()
  return category
    .replace(/^tool\./, '')
    .replace(/\.(start|done|invoke|completed|failed)$/, '')
}

function activityTitle(step: TimelineStep, category: string): string | null {
  // SourceLens keeps runtime/bootstrap events out of the user activity list.
  if (
    category.startsWith('mcp.')
    || category.startsWith('phase.')
    || category.startsWith('workflow.')
    || category.startsWith('deepagents.')
    || category.startsWith('resources.')
    || category.startsWith('llm.')
    || category.startsWith('plan.')
    || category.startsWith('stage.')
    || category.startsWith('activity.')
    || category === 'queue'
    || category.startsWith('document.')
  ) return null
  if (step.error) return 'Activity failed'
  const assistantName = 'assistant_name' in step ? step.assistant_name : step.assistantName
  const delegatedTask = 'delegated_task' in step ? step.delegated_task : step.delegatedTask
  if (assistantName || delegatedTask) return String(assistantName || delegatedTask)
  if (category.startsWith('tool.')) {
    const tool = toolName(step, category)
    return SOURCE_ACTIVITY_LABELS[sourceActivityKind(tool)]
  }
  const label = step.displayMessage || step.message || step.summary
  return label && !/^deepagents\.|^resources\.|^llm\./i.test(label) ? label : null
}

function detail(step: TimelineStep): string | null {
  if (step.error) return step.error
  const payload = 'payload' in step ? step.payload : undefined
  if (payload) {
    const value = payload.summary || payload.message || payload.description
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  const value = step.summary || step.query || step.path
  return value ? String(value) : null
}

function isTerminalToolEvent(category: string): boolean {
  return /\.(done|completed|failed)$/.test(category)
}

/**
 * Collapse SourceLens' low-level runtime events into the user-facing
 * activities shown by the native SourceLens chat UI. Setup and LLM events are
 * implementation details; tool start/done pairs become one activity and
 * repeated retrievals are represented with a count.
 */
export function summarizeThinkingSteps(steps: TimelineStep[]): CopilotThinkingActivity[] {
  const result: CopilotThinkingActivity[] = []
  const byKey = new Map<string, CopilotThinkingActivity>()
  const add = (key: string, title: string, step: TimelineStep, count = 1) => {
    const existing = byKey.get(key)
    const status = step.error ? 'failed' : isTerminalToolEvent(eventCategory(step)) || title === 'Completed' ? 'completed' : 'in_progress'
    if (existing) {
      existing.count += count
      existing.status = existing.status === 'failed' || status === 'failed' ? 'failed' : status
      const line = detail(step)
      if (line && !existing.details.includes(line)) existing.details.push(line)
      return
    }
    const activity: CopilotThinkingActivity = {
      id: key,
      title,
      titleKey: eventCategory(step).startsWith('tool.')
        ? SOURCE_ACTIVITY_KEYS[sourceActivityKind(toolName(step, eventCategory(step)))]
        : undefined,
      count,
      details: detail(step) ? [detail(step) as string] : [],
      status,
    }
    byKey.set(key, activity)
    result.push(activity)
  }

  for (const step of steps) {
    const category = eventCategory(step)
    const title = activityTitle(step, category)
    if (!title) continue
    const tool = category.startsWith('tool.')
    const normalizedTool = toolName(step, category)
    const retrievalTool = ['find_files', 'search_workspace', 'glob_files'].includes(normalizedTool)
    const key = tool
      ? `tool:${retrievalTool ? 'retrieval' : normalizedTool}`
      : `activity:${title}`
    // A tool's done event completes the existing activity; it is not another
    // activity. Repeated start/invoke events are counted for the ×N label.
    const terminal = tool && isTerminalToolEvent(category)
    add(key, title, step, terminal && byKey.has(key) ? 0 : 1)
  }
  const hasMeaningfulActivity = result.some((activity) => activity.title !== 'Completed')
  return result.filter((activity) => activity.count > 0 && (hasMeaningfulActivity || activity.title !== 'Completed'))
}

export function thinkingActivityCount(steps: TimelineStep[]): number {
  return summarizeThinkingSteps(steps).reduce(
    (total, activity) => total + activity.count,
    0,
  )
}

/** Return true when SourceLens supplied structured plan/stage/workflow content. */
export function hasStructuredRuntimeContent(steps: TimelineStep[]): boolean {
  return steps.some((step) => {
    const category = eventCategory(step)
    const assistantName = 'assistant_name' in step ? step.assistant_name : step.assistantName
    const delegatedTask = 'delegated_task' in step ? step.delegated_task : step.delegatedTask
    const title = 'title' in step ? step.title : undefined
    const payload = 'payload' in step && step.payload && typeof step.payload === 'object'
      ? step.payload as Record<string, unknown>
      : {}
    // activity.* alone is not enough: knowledge-QA tools become user activities via
    // tool.* events, and bare activity.recorded without a summarized title must not
    // flip the live line from Analyzing/Queued straight to an empty Agent activity card.
    return (
      /^plan\.|^stage\./.test(category)
      || Boolean(step.plan || step.outcome || assistantName)
      || Boolean(delegatedTask)
      || (/plan|workflow|task/i.test(category)
        && Boolean(title || step.summary || payload.steps || payload.tasks))
      || Boolean(payload.steps || payload.tasks)
    )
  })
}
