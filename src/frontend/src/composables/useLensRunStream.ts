import { reactive, type Reactive } from 'vue'
import { apiErrorMessage, isAbortError } from '../lib/api'
import { streamCopilotRun } from '../lib/lensApi'
import { formatThinkingStepLabel } from '../lib/copilotStreamLabels'

export type ThinkingStep = {
  message: string
  agentEvent?: string
  activity?: string
  displayMessage?: string
  summary?: string
  path?: string
  query?: string
  plan?: string
  tokens?: number
  inputTokens?: number
  outputTokens?: number
  durationMs?: number
  toolName?: string
  error?: string
}

export type SessionRunStreamState = {
  partialAnswer: string
  streamError: string
  isSubmitting: boolean
  isStreaming: boolean
  streamAttached: boolean
  thinkingSteps: ThinkingStep[]
  thinkingPanelOpen: boolean
  runStatus: string | null
  runUuid: string | null
  thinkingElapsedSeconds: number
}

type StreamPayload = Record<string, unknown>

export const ACTIVE_RUN_STATUSES = new Set(['queued', 'running', 'streaming'])

export function isActiveRunStatus(status: string | null | undefined): boolean {
  return Boolean(status && ACTIVE_RUN_STATUSES.has(status))
}

function createEmptyState(): SessionRunStreamState {
  return reactive({
    partialAnswer: '',
    streamError: '',
    isSubmitting: false,
    isStreaming: false,
    streamAttached: false,
    thinkingSteps: [],
    thinkingPanelOpen: true,
    runStatus: null,
    runUuid: null,
    thinkingElapsedSeconds: 0,
  })
}

class SessionRunStreamController {
  readonly state: SessionRunStreamState
  private controller: AbortController | null = null
  private userAborted = false
  private receivedStreamTokens = false
  private streamFinished = false
  private elapsedTimer: ReturnType<typeof setInterval> | null = null
  private streamStartedAt = 0
  private seenActivityKeys = new Set<string>()
  private seenStepEventCounts = new Map<string, number>()

  constructor() {
    this.state = createEmptyState()
  }

  private clearElapsedTimer() {
    if (this.elapsedTimer) {
      clearInterval(this.elapsedTimer)
      this.elapsedTimer = null
    }
  }

  private updateElapsedSeconds() {
    this.state.thinkingElapsedSeconds = Math.max(
      0,
      Math.floor((Date.now() - this.streamStartedAt) / 1000),
    )
  }

  private parseElapsedAnchor(elapsedAnchorAt?: string | null): number | null {
    if (!elapsedAnchorAt) return null
    const parsed = Date.parse(elapsedAnchorAt)
    if (!Number.isFinite(parsed)) return null
    return Math.min(parsed, Date.now())
  }

  private setElapsedAnchor(elapsedAnchorAt?: string | null) {
    const parsed = this.parseElapsedAnchor(elapsedAnchorAt)
    if (parsed != null && (this.streamStartedAt === 0 || parsed < this.streamStartedAt)) {
      this.streamStartedAt = parsed
      this.updateElapsedSeconds()
    }
  }

  private startElapsedTimer(elapsedAnchorAt?: string | null) {
    this.clearElapsedTimer()
    const parsed = this.parseElapsedAnchor(elapsedAnchorAt)
    this.streamStartedAt = parsed ?? (this.streamStartedAt || Date.now())
    this.updateElapsedSeconds()
    this.elapsedTimer = setInterval(() => {
      this.updateElapsedSeconds()
    }, 1000)
  }

  private ensureElapsedTimer(elapsedAnchorAt?: string | null) {
    this.setElapsedAnchor(elapsedAnchorAt)
    if (!this.elapsedTimer) {
      this.startElapsedTimer(elapsedAnchorAt)
    }
  }

  resetStreamState() {
    this.userAborted = false
    this.receivedStreamTokens = false
    this.streamFinished = false
    this.controller?.abort()
    this.controller = null
    this.state.partialAnswer = ''
    this.state.streamError = ''
    this.state.isSubmitting = false
    this.state.runStatus = null
    this.state.runUuid = null
    this.state.thinkingSteps = []
    this.state.thinkingPanelOpen = true
    this.state.thinkingElapsedSeconds = 0
    this.streamStartedAt = 0
    this.seenActivityKeys.clear()
    this.seenStepEventCounts.clear()
    this.clearElapsedTimer()
    this.state.isStreaming = false
    this.state.streamAttached = false
  }

  applyActiveRunSnapshot(
    runUuid: string,
    status: string,
    partialContent: string,
    thinking: Array<Record<string, unknown>>,
    elapsedAnchorAt?: string | null,
  ) {
    this.state.isSubmitting = false
    this.state.runUuid = runUuid
    this.state.runStatus = status
    if (partialContent) {
      this.state.partialAnswer = partialContent
    }
    if (thinking.length) {
      this.state.thinkingSteps = thinking
        .map((item) => {
          const message =
            (item.message as string) ||
            (item.agent_event as string) ||
            (item.activity as string)
          if (!message) return null
          const step: ThinkingStep = {
            message,
            agentEvent: item.agent_event as string,
            activity: item.activity as string,
            summary: item.summary as string,
            path: item.path as string,
            query: item.query as string,
            plan: item.plan as string,
            tokens: item.tokens as number,
            inputTokens: item.input_tokens as number,
            outputTokens: item.output_tokens as number,
            durationMs: item.duration_ms as number,
            toolName: item.tool_name as string,
            error: item.error as string,
          }
          step.displayMessage = formatThinkingStepLabel(step)
          return step
        })
        .filter(Boolean) as ThinkingStep[]
    }
    this.state.isStreaming = isActiveRunStatus(status)
    this.setElapsedAnchor(elapsedAnchorAt)
  }

  applySubmissionSnapshot(elapsedAnchorAt?: string | null) {
    if (this.state.runUuid || this.state.streamAttached) {
      this.resetStreamState()
    }
    this.state.isSubmitting = true
    this.state.runUuid = null
    this.state.runStatus = null
    this.state.streamError = ''
    this.state.isStreaming = false
    this.ensureElapsedTimer(elapsedAnchorAt)
  }

  private pushThinkingStep(item: Record<string, unknown>) {
    const message =
      (item.message as string) ||
      (item.summary as string) ||
      (item.agent_event as string) ||
      (item.activity as string)
    if (!message) return
    const key = `${(item.agent_event as string) || ''}|${message}`
    if (this.seenActivityKeys.has(key)) return
    this.seenActivityKeys.add(key)
    const step: ThinkingStep = {
      message,
      agentEvent: item.agent_event as string,
      activity: item.activity as string,
      summary: item.summary as string,
      path: item.path as string,
      query: item.query as string,
      plan: item.plan as string,
      tokens: item.tokens as number,
      inputTokens: item.input_tokens as number,
      outputTokens: item.output_tokens as number,
      durationMs: item.duration_ms as number,
      toolName: item.tool_name as string,
      error: item.error as string,
    }
    step.displayMessage = formatThinkingStepLabel(step)
    this.state.thinkingSteps = [...this.state.thinkingSteps, step]
  }

  private handleStepEvent(event: StreamPayload) {
    if (event.status === 'failed') {
      const detail = event.detail as
        | { events?: Array<{ error?: string; message?: string }> }
        | undefined
      const failed = detail?.events?.find((item) => item.error || item.message)
      if (failed) {
        this.state.streamError = failed.message || failed.error || 'Run failed'
      }
    }
    const events =
      (event.detail as { events?: Array<Record<string, unknown>> } | undefined)?.events || []
    const stepKey = String(event.sequence ?? event.step ?? 'step')
    const seenCount = this.seenStepEventCounts.get(stepKey) || 0
    const newEvents = events.slice(seenCount)
    this.seenStepEventCounts.set(stepKey, events.length)
    for (const item of newEvents) {
      this.pushThinkingStep(item)
    }
  }

  private formatStreamError(raw: string) {
    const code = raw.trim()
    if (code === 'LENSNODE_BUSY') {
      return 'LensNode is busy processing another request. Wait a moment and try again.'
    }
    if (code === 'LENSNODE_RUN_ACTIVE') {
      return 'LensNode already has an active run. Stop the other run or wait for it to finish.'
    }
    if (code === 'MODEL_TIMEOUT') {
      return 'The AI model timed out. Try again or choose a different model.'
    }
    return code || 'Run failed'
  }

  private markStreamFinished() {
    this.streamFinished = true
    this.controller?.abort()
  }

  private applySyncContent(content: string) {
    if (!this.receivedStreamTokens) {
      this.state.partialAnswer = content
      return
    }
    if (content.length >= this.state.partialAnswer.length) {
      this.state.partialAnswer = content
    }
  }

  private handleEvent(event: StreamPayload) {
    const type = String(event.type || '')
    if (type === 'sync' || type === 'status') {
      if (typeof event.status === 'string') this.state.runStatus = event.status
      if (type === 'sync' && Array.isArray(event.steps)) {
        for (const step of event.steps as StreamPayload[]) this.handleStepEvent(step)
      }
      if (type === 'sync' && typeof event.content === 'string') {
        this.applySyncContent(event.content)
      }
      if (type === 'status' && event.status === 'done') {
        this.markStreamFinished()
      }
    }
    if (type === 'queue_position') {
      const position = Number(event.position ?? 0)
      this.pushThinkingStep({
        message: position > 0 ? `Queued (position ${position + 1})` : 'Queued',
        activity: 'queue',
      })
    }
    if (type === 'step') this.handleStepEvent(event)
    if (type === 'token' && typeof event.content === 'string') {
      this.receivedStreamTokens = true
      this.state.partialAnswer += event.content
    }
    if (type === 'token_reset') {
      if (this.state.partialAnswer) this.state.partialAnswer += '\n'
    }
    if (type === 'done') {
      this.state.runStatus = 'done'
      this.markStreamFinished()
    }
    if (type === 'error') {
      const err = event.error
      const raw =
        (typeof err === 'object' && err && 'message' in err
          ? String((err as { message?: string }).message)
          : typeof err === 'string'
            ? err
            : '') || 'Run failed'
      this.state.streamError = this.formatStreamError(raw)
      this.markStreamFinished()
    }
  }

  async consumeStream(
    sessionId: number,
    runUuid: string,
    onFinished?: () => void,
  ): Promise<void> {
    const finishedCallback = onFinished ?? null
    this.userAborted = false
    this.receivedStreamTokens = false
    this.streamFinished = false
    this.controller?.abort()
    const controller = new AbortController()
    this.controller = controller
    this.state.runUuid = runUuid
    this.state.streamError = ''
    this.state.isSubmitting = false
    this.state.isStreaming = true
    this.state.streamAttached = true
    this.ensureElapsedTimer()
    const timeoutMs = 180_000
    const timeout = window.setTimeout(() => {
      if (this.controller !== controller) return
      if (!this.streamFinished) {
        this.state.streamError = this.state.streamError || 'Response timed out. Try again.'
        this.markStreamFinished()
      }
    }, timeoutMs)
    try {
      await streamCopilotRun(
        sessionId,
        runUuid,
        (payload) => {
          if (this.controller !== controller) return
          if (payload && typeof payload === 'object') {
            this.handleEvent(payload as StreamPayload)
          }
        },
        controller.signal,
      )
    } catch (err) {
      if (this.controller !== controller) return
      if (this.streamFinished || this.userAborted || isAbortError(err)) {
        return
      }
      if (!this.state.streamError) {
        this.state.streamError = apiErrorMessage(err, 'Stream failed')
      }
    } finally {
      window.clearTimeout(timeout)
      if (this.controller === controller) {
        this.clearElapsedTimer()
        if (
          !this.state.streamError &&
          (this.state.runStatus === 'failed' || this.state.runStatus === 'cancelled')
        ) {
          this.state.streamError = this.formatStreamError(
            this.state.runStatus === 'cancelled' ? 'Run cancelled' : 'Run failed',
          )
        }
        this.controller = null
        this.state.streamAttached = false
        this.state.isStreaming = false
        finishedCallback?.()
      }
    }
  }

  detachStream() {
    this.userAborted = true
    this.controller?.abort()
    this.controller = null
    this.clearElapsedTimer()
    this.state.streamAttached = false
    this.state.isStreaming = false
  }

  beginSubmission() {
    this.resetStreamState()
    this.state.isSubmitting = true
    this.startElapsedTimer()
  }

  clearSubmission() {
    this.state.isSubmitting = false
    if (!this.state.isStreaming && !this.state.streamAttached) {
      this.clearElapsedTimer()
    }
  }
}

const controllers = new Map<number, SessionRunStreamController>()

function getController(sessionId: number): SessionRunStreamController {
  let ctrl = controllers.get(sessionId)
  if (!ctrl) {
    ctrl = new SessionRunStreamController()
    controllers.set(sessionId, ctrl)
  }
  return ctrl
}

export function getSessionRunStream(sessionId: number): Reactive<SessionRunStreamState> {
  return getController(sessionId).state
}

export function resetSessionRunStream(sessionId: number) {
  getController(sessionId).resetStreamState()
}

export function beginSessionRunSubmission(sessionId: number) {
  getController(sessionId).beginSubmission()
}

export function clearSessionRunSubmission(sessionId: number) {
  getController(sessionId).clearSubmission()
}

export function applySessionRunSubmission(
  sessionId: number,
  elapsedAnchorAt?: string | null,
) {
  getController(sessionId).applySubmissionSnapshot(elapsedAnchorAt)
}

export function applySessionActiveRun(
  sessionId: number,
  runUuid: string,
  status: string,
  partialContent: string,
  thinking: Array<{ message?: string; agent_event?: string; activity?: string }>,
  elapsedAnchorAt?: string | null,
) {
  getController(sessionId).applyActiveRunSnapshot(
    runUuid,
    status,
    partialContent,
    thinking,
    elapsedAnchorAt,
  )
}

export async function consumeSessionStream(
  sessionId: number,
  runUuid: string,
  onFinished?: () => void,
) {
  await getController(sessionId).consumeStream(sessionId, runUuid, onFinished)
}

export function detachSessionStream(sessionId: number) {
  controllers.get(sessionId)?.detachStream()
}

export function detachAllSessionStreams() {
  for (const ctrl of controllers.values()) {
    ctrl.detachStream()
  }
}

/** @deprecated Use getSessionRunStream(sessionId) for per-session state. */
export function useLensRunStream() {
  const fallback = getController(-1)
  return {
    partialAnswer: fallback.state.partialAnswer,
    streamError: fallback.state.streamError,
    isStreaming: fallback.state.isStreaming,
    thinkingSteps: fallback.state.thinkingSteps,
    thinkingPanelOpen: fallback.state.thinkingPanelOpen,
    runStatus: fallback.state.runStatus,
    thinkingElapsedSeconds: fallback.state.thinkingElapsedSeconds,
    consumeStream: (sessionId: number, runUuid: string) =>
      fallback.consumeStream(sessionId, runUuid),
    abortStream: () => fallback.detachStream(),
    resetStreamState: () => fallback.resetStreamState(),
  }
}
