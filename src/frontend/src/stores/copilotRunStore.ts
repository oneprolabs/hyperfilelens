import { ref, shallowRef } from 'vue'
import {
  cancelCopilotRun,
  syncCopilotSession,
  type LensCopilotActiveRun,
  type LensCopilotResponseState,
  type LensCopilotSyncResponse,
} from '../lib/lensApi'
import {
  applySessionActiveRun,
  applySessionRunSubmission,
  consumeSessionStream,
  detachAllSessionStreams,
  detachSessionStream,
  getSessionRunStream,
  isActiveRunStatus,
  resetSessionRunStream,
} from '../composables/useLensRunStream'

export type CopilotSyncHandlers = {
  onMessages: (
    sessionId: number,
    messages: LensCopilotSyncResponse['messages'],
    runOutcomes: LensCopilotSyncResponse['run_outcomes'],
    responseState?: LensCopilotResponseState,
  ) => void
  onSessionMeta?: (sessionId: number, activeRun: LensCopilotActiveRun | null) => void
  onSessionSync?: (sessionId: number, payload: LensCopilotSyncResponse) => void
}

const pendingNotifications = ref(new Set<number>())
const pollerTimer = ref<ReturnType<typeof setInterval> | null>(null)
const pollerSessions = shallowRef<number[]>([])

export function useCopilotRunStore() {
  function markSessionViewed(sessionId: number) {
    if (pendingNotifications.value.has(sessionId)) {
      const next = new Set(pendingNotifications.value)
      next.delete(sessionId)
      pendingNotifications.value = next
    }
  }

  function notifySessionComplete(sessionId: number, activeSessionId: number | null) {
    if (sessionId !== activeSessionId) {
      const next = new Set(pendingNotifications.value)
      next.add(sessionId)
      pendingNotifications.value = next
    }
  }

  async function syncSession(
    sessionId: number,
    handlers: CopilotSyncHandlers,
    activeSessionId: number | null,
    opts?: { attachStream?: boolean },
  ) {
    const payload = await syncCopilotSession(sessionId)
    handlers.onMessages(
      sessionId,
      payload.messages,
      payload.run_outcomes || [],
      payload.response_state,
    )
    handlers.onSessionMeta?.(sessionId, payload.active_run)
    if (payload.active_run && isActiveRunStatus(payload.active_run.status)) {
      applySessionActiveRun(
        sessionId,
        payload.active_run.uuid,
        payload.active_run.status,
        payload.active_run.partial_content || '',
        payload.active_run.thinking || [],
        payload.active_run.elapsed_anchor_at,
      )
      if (opts?.attachStream && sessionId === activeSessionId) {
        void attachStream(sessionId, payload.active_run.uuid, handlers, activeSessionId)
      }
    } else if (payload.response_state?.status === 'submitting') {
      applySessionRunSubmission(sessionId, payload.response_state.started_at)
    } else {
      const previousState = getSessionRunStream(sessionId)
      const hadActiveRun = isActiveRunStatus(previousState.runStatus)
      const hadPendingSubmission = previousState.isSubmitting
      resetSessionRunStream(sessionId)
      if (hadActiveRun || hadPendingSubmission) {
        notifySessionComplete(sessionId, activeSessionId)
      }
    }
    handlers.onSessionSync?.(sessionId, payload)
    return payload
  }

  async function attachStream(
    sessionId: number,
    runUuid: string,
    handlers: CopilotSyncHandlers,
    activeSessionId: number | null,
  ) {
    await consumeSessionStream(sessionId, runUuid, async () => {
      await syncSession(sessionId, handlers, activeSessionId, { attachStream: false })
    })
  }

  async function startRunStream(
    sessionId: number,
    runUuid: string,
    status: string,
    handlers: CopilotSyncHandlers,
    activeSessionId: number | null,
  ) {
    if (!isActiveRunStatus(status)) {
      await syncSession(sessionId, handlers, activeSessionId, { attachStream: false })
      return
    }
    applySessionActiveRun(sessionId, runUuid, status, '', [])
    if (sessionId === activeSessionId) {
      await attachStream(sessionId, runUuid, handlers, activeSessionId)
    }
  }

  async function cancelActiveRun(
    sessionId: number,
    runUuid: string,
    handlers: CopilotSyncHandlers,
    activeSessionId: number | null,
  ) {
    detachSessionStream(sessionId)
    await cancelCopilotRun(sessionId, runUuid)
    await syncSession(sessionId, handlers, activeSessionId)
  }

  function selectSession(
    previousId: number | null,
    nextId: number,
    handlers: CopilotSyncHandlers,
    activeSessionId: number | null,
  ) {
    if (previousId != null && previousId !== nextId) {
      detachSessionStream(previousId)
    }
    markSessionViewed(nextId)
    return syncSession(nextId, handlers, activeSessionId, { attachStream: true })
  }

  function updatePollerSessions(sessionIds: number[]) {
    pollerSessions.value = sessionIds
  }

  function startBackgroundPoller(
    handlers: CopilotSyncHandlers,
    getActiveSessionId: () => number | null,
    intervalMs = 12_000,
  ) {
    stopBackgroundPoller()
    pollerTimer.value = setInterval(() => {
      for (const sessionId of pollerSessions.value) {
        const stream = getSessionRunStream(sessionId)
        if (stream.streamAttached) continue
        void syncSession(sessionId, handlers, getActiveSessionId(), {
          attachStream: sessionId === getActiveSessionId(),
        }).catch(() => undefined)
      }
    }, intervalMs)
  }

  function stopBackgroundPoller() {
    if (pollerTimer.value) {
      clearInterval(pollerTimer.value)
      pollerTimer.value = null
    }
  }

  function teardown() {
    stopBackgroundPoller()
    detachAllSessionStreams()
  }

  return {
    pendingNotifications,
    markSessionViewed,
    notifySessionComplete,
    syncSession,
    startRunStream,
    cancelActiveRun,
    selectSession,
    updatePollerSessions,
    startBackgroundPoller,
    stopBackgroundPoller,
    teardown,
    getSessionRunStream,
    detachSessionStream,
  }
}
