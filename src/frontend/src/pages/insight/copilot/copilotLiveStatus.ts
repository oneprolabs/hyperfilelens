import type { TimelineStep } from './copilotThinkingActivities'

const LIVE_PHASES = new Set(['analyzing', 'planning', 'executing', 'answering', 'completed'])
const DOCUMENT_STAGES = new Set(['downloading', 'extracting_text', 'recognizing_images', 'ready'])

export type CopilotLiveInterim = {
  key: string
  params?: Record<string, number>
}

function eventType(step: TimelineStep): string {
  return String(
    ('event_type' in step ? step.event_type : step.eventType) || '',
  )
}

function payloadOf(step: TimelineStep): Record<string, unknown> {
  const payload = 'payload' in step ? step.payload : undefined
  return payload && typeof payload === 'object' ? payload : {}
}

function latestPhase(steps: TimelineStep[]): string | null {
  let phase: string | null = null
  for (const step of steps) {
    if (eventType(step) !== 'phase.changed') continue
    const value = String(payloadOf(step).phase || '')
    if (LIVE_PHASES.has(value)) phase = value
  }
  return phase
}

function latestDocumentProgress(steps: TimelineStep[]): Record<string, unknown> | null {
  let progress: Record<string, unknown> | null = null
  let revision = -1
  for (const step of steps) {
    if (eventType(step) !== 'document.progress') continue
    const payload = payloadOf(step)
    const nextRevision = Math.max(Number(payload.revision || 0), 0)
    if (!DOCUMENT_STAGES.has(String(payload.stage || '')) || nextRevision < revision) continue
    revision = nextRevision
    progress = payload
  }
  return progress
}

function documentInterim(progress: Record<string, unknown>): CopilotLiveInterim | null {
  const stage = String(progress.stage || '')
  if (stage === 'recognizing_images') {
    return {
      key: 'insight.copilot.liveDocumentRecognizing',
      params: {
        completed: Math.max(Number(progress.image_completed || 0), 0),
        total: Math.max(Number(progress.image_total || 0), 0),
      },
    }
  }
  if (stage === 'downloading' || stage === 'extracting_text') {
    return {
      key: stage === 'downloading'
        ? 'insight.copilot.liveDocumentDownloading'
        : 'insight.copilot.liveDocumentExtracting',
      params: {
        current: Math.max(Number(progress.document_index || 0), 0),
        total: Math.max(Number(progress.document_total || 0), 0),
      },
    }
  }
  return null
}

function statusInterim(runStatus: string | null | undefined, queuePosition: number | null | undefined): CopilotLiveInterim {
  if (runStatus === 'streaming') return { key: 'insight.copilot.liveStatusGenerating' }
  if (runStatus === 'running') return { key: 'insight.copilot.liveStatusRunning' }
  if (runStatus === 'queued') {
    if (queuePosition == null) return { key: 'insight.copilot.liveStatusQueued' }
    if (queuePosition === 0) return { key: 'insight.copilot.liveStatusQueuedNext' }
    return { key: 'insight.copilot.liveStatusQueuedPosition', params: { position: queuePosition } }
  }
  return { key: 'insight.copilot.liveStatusWaiting' }
}

function eventCategory(step: TimelineStep): string {
  return String(
    eventType(step)
    || ('agent_event' in step ? step.agent_event : ('agentEvent' in step ? step.agentEvent : ''))
    || step.activity
    || '',
  ).toLowerCase()
}

/** Runtime already started even if the run row is still labeled queued. */
function stepsIndicateExecution(steps: TimelineStep[]): boolean {
  return steps.some((step) => {
    const category = eventCategory(step)
    const payload = payloadOf(step)
    return (
      category === 'phase.changed'
      || category === 'document.progress'
      || category.startsWith('plan.')
      || category.startsWith('stage.')
      || category === 'activity.recorded'
      || category === 'route.selected'
      || category.startsWith('tool.')
      || category.startsWith('deepagents.agent.')
      || category.startsWith('deepagents.runtime.')
      || category.startsWith('llm.')
      || Boolean(payload.phase || payload.stage || payload.steps)
    )
  })
}

/**
 * SourceLens live line before an Agent activity card exists.
 * A disconnected node wins, then document progress, then the runtime phase,
 * then the run status (Generating / Analyzing / Queued / Waiting).
 */
export function selectCopilotLiveInterim(input: {
  steps?: TimelineStep[]
  runStatus?: string | null
  queuePosition?: number | null
  resumeBy?: string | null
}): CopilotLiveInterim {
  if (input.resumeBy) return { key: 'insight.copilot.liveStatusAwaitingResume' }
  const steps = input.steps ?? []
  const documentProgress = latestDocumentProgress(steps)
  if (documentProgress) {
    const documentStatus = documentInterim(documentProgress)
    if (documentStatus) return documentStatus
  }
  const phase = latestPhase(steps)
  if (phase) return { key: `insight.copilot.livePhase.${phase}` }
  // Prefer Analyzing over a stale Queued label once runtime events have started.
  if (
    stepsIndicateExecution(steps)
    && (input.runStatus === 'queued' || !input.runStatus)
  ) {
    return { key: 'insight.copilot.liveStatusRunning' }
  }
  return statusInterim(input.runStatus, input.queuePosition)
}
