<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

defineOptions({ name: 'InsightCopilot' })
import { apiErrorMessage, type ApiError } from '../../lib/api'
import { normalizeThrownError } from '../../lib/errors'
import {
  beginSessionRunSubmission,
  clearSessionRunSubmission,
  getSessionRunStream,
  isActiveRunStatus,
} from '../../composables/useLensRunStream'
import { useCopilotRunStore } from '../../stores/copilotRunStore'
import {
  createCopilotRun,
  deleteCopilotAttachment,
  deleteCopilotSession,
  fetchCopilotReadiness,
  fetchLensHealth,
  listCopilotAssistants,
  listCopilotGatewayOptions,
  listCopilotSessions,
  listKnowledgeSources,
  listLensGateways,
  markCopilotSessionViewed,
  pinCopilotSession,
  renameCopilotSession,
  retryCopilotSession,
  unpinCopilotSession,
  uploadCopilotAttachment,
  type LensChatAttachment,
  type LensChatMessage,
  type LensCopilotActiveRun,
  type LensCopilotReadiness,
  type LensCopilotRunOutcome,
  type LensCopilotResponseState,
  type LensCopilotAssistant,
  type LensGatewayInsight,
  type LensCopilotGatewayOption,
  type LensKnowledgeSource,
  type LensSessionLink,
} from '../../lib/lensApi'
import CopilotComposer from './copilot/CopilotComposer.vue'
import CopilotContextBar from './copilot/CopilotContextBar.vue'
import CopilotEmptyState, { type CopilotEmptyPhase, type CopilotReadiness } from './copilot/CopilotEmptyState.vue'
import CopilotLifecycleState from './copilot/CopilotLifecycleState.vue'
import CopilotMessageList from './copilot/CopilotMessageList.vue'
import CopilotSessionSidebar, { type SessionGroupKey, type SessionRow } from './copilot/CopilotSessionSidebar.vue'
import DangerConfirmDialog from '../../components/DangerConfirmDialog.vue'
import type { CopilotComposerAttachment, CopilotDisplayMessage } from './copilot/types'
import { appendRunOutcomeMessages } from './copilot/runOutcomes'
import { Menu } from 'lucide-vue-next'
import { useResponsiveLayout } from '../../composables/useResponsiveLayout'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const copilotStore = useCopilotRunStore()
const { isPhone } = useResponsiveLayout()
const mobileSessionsOpen = ref(false)

const bridgeReady = ref(false)
const loading = ref(false)
const bootstrapError = ref<'network' | null>(null)
const assistants = ref<LensCopilotAssistant[]>([])
const gateways = ref<LensGatewayInsight[]>([])
const copilotGatewayOptions = ref<LensCopilotGatewayOption[]>([])
const knowledgeSources = ref<LensKnowledgeSource[]>([])
const modelReadiness = ref<LensCopilotReadiness | null>(null)
const sessions = ref<SessionRow[]>([])
const activeSessionId = ref<number | null>(null)
const deleteOpen = ref(false)
const deleteLoading = ref(false)
const deleteTarget = ref<SessionRow | null>(null)
const messagesBySession = ref<Record<number, CopilotDisplayMessage[]>>({})
const selectedStarterBySession = ref<Record<number, string>>({})
const input = ref('')
const composerAttachments = ref<CopilotComposerAttachment[]>([])
const messageListRef = ref<{ scrollToBottom: () => void } | null>(null)
const lifecyclePollingEpochs = new Map<number, number>()
let componentUnmounted = false
let componentActive = false
let lifecyclePollingEpoch = 0
let composerLifecycleGeneration = 0

let idSeq = 0
function uid(prefix: string) {
  idSeq += 1
  return `${prefix}-${Date.now()}-${idSeq}`
}

function isAssistantChatReady(row: LensCopilotAssistant) {
  if (row.status && row.status !== 'active') return false
  const status = row.knowledge_source_status
  if (!status) return true
  return status === 'ready' || status === 'degraded'
}

function groupForDate(iso: string | null | undefined): SessionGroupKey {
  if (!iso) return 'earlier'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'earlier'
  const now = new Date()
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const startYesterday = new Date(startToday)
  startYesterday.setDate(startYesterday.getDate() - 1)
  if (date >= startToday) return 'today'
  if (date >= startYesterday) return 'yesterday'
  return 'earlier'
}

function toSessionRows(rows: LensSessionLink[]): SessionRow[] {
  return rows.map((row) => ({
    ...row,
    group: row.pinned_at
      ? 'pinned'
      : groupForDate(row.last_message_at || row.created_at),
  })).sort((left, right) => {
    const leftPinned = left.pinned_at ? Date.parse(left.pinned_at) : 0
    const rightPinned = right.pinned_at ? Date.parse(right.pinned_at) : 0
    if (leftPinned !== rightPinned) return rightPinned - leftPinned
    const leftRecent = Date.parse(left.last_message_at || left.created_at || '') || 0
    const rightRecent = Date.parse(right.last_message_at || right.created_at || '') || 0
    return rightRecent - leftRecent
  })
}

function welcomeMessage(sessionId: number, createdAt?: string | null): CopilotDisplayMessage {
  return {
    id: `welcome-${sessionId}`,
    role: 'assistant',
    text: t('insight.copilot.welcome'),
    starterChips: true,
    createdAt: createdAt || new Date().toISOString(),
  }
}

function withWelcomeMessage(
  sessionId: number,
  mapped: CopilotDisplayMessage[],
  createdAt?: string | null,
): CopilotDisplayMessage[] {
  const withoutWelcome = mapped.filter(
    (row) => !row.starterChips && row.id !== `welcome-${sessionId}`,
  )
  const existingWelcome = mapped.find(
    (row) => row.starterChips || row.id === `welcome-${sessionId}`,
  )
  const welcome = existingWelcome ?? welcomeMessage(sessionId, createdAt)
  return [welcome, ...withoutWelcome]
}

function mapApiMessage(row: LensChatMessage): CopilotDisplayMessage | null {
  if (row.role === 'system') return null
  const text = row.content || ''
  if (row.role !== 'user' && !text.trim()) return null
  return {
    id: row.uuid || uid('m'),
    role: row.role === 'user' ? 'user' : 'assistant',
    text,
    createdAt: row.created_at,
    runId: row.run,
    thinking: row.thinking,
    attachments: row.attachments,
  }
}

function applyMessagesFromSync(
  sessionId: number,
  rows: LensChatMessage[],
  runOutcomes: LensCopilotRunOutcome[],
  responseState?: LensCopilotResponseState,
) {
  const list = Array.isArray(rows) ? rows : []
  const mapped = appendRunOutcomeMessages(
    list.map(mapApiMessage).filter(Boolean) as CopilotDisplayMessage[],
    runOutcomes,
  )
  const pendingQuestion = responseState?.status === 'submitting'
    ? responseState.question?.trim()
    : ''
  const latestMessage = mapped.at(-1)
  if (
    pendingQuestion
    && !(
      latestMessage?.role === 'user'
      && latestMessage.text?.trim() === pendingQuestion
    )
  ) {
    mapped.push({
      id: `pending-${sessionId}`,
      role: 'user',
      text: pendingQuestion,
      createdAt: responseState?.started_at || new Date().toISOString(),
    })
  }
  const session = sessions.value.find((row) => row.id === sessionId)
  messagesBySession.value = {
    ...messagesBySession.value,
    [sessionId]: withWelcomeMessage(sessionId, mapped, session?.created_at),
  }
  if (
    sessionId === activeSessionId.value
    && route.path === '/insight/copilot'
    && document.visibilityState === 'visible'
  ) {
    void markSessionViewed(sessionId)
  }
}

function applySessionActiveMeta(sessionId: number, activeRun: LensCopilotActiveRun | null) {
  sessions.value = sessions.value.map((row) =>
    row.id === sessionId
      ? {
          ...row,
          active_run_uuid: activeRun?.uuid ?? null,
          active_run_status: activeRun?.status ?? '',
        }
      : row,
  )
  refreshPollerSessions()
}

function applySessionSyncMeta(sessionId: number, payload: { last_assistant_message_at?: string | null; has_unread?: boolean }) {
  sessions.value = sessions.value.map((row) => row.id === sessionId
    ? {
        ...row,
        last_assistant_message_at: payload.last_assistant_message_at ?? row.last_assistant_message_at,
        has_unread: payload.has_unread ?? row.has_unread,
      }
    : row)
  refreshPollerSessions()
}

const syncHandlers = {
  onMessages: applyMessagesFromSync,
  onSessionMeta: applySessionActiveMeta,
  onSessionSync: applySessionSyncMeta,
}

function refreshPollerSessions() {
  const ids = sessions.value
    .filter(
      (row) => isActiveRunStatus(row.active_run_status)
        || getSessionRunStream(row.id).isSubmitting,
    )
    .map((row) => row.id)
  copilotStore.updatePollerSessions(ids)
}

const assistantByUuid = computed(() => new Map(assistants.value.map((row) => [row.uuid, row])))

const chatReadyAssistants = computed(() => assistants.value.filter(isAssistantChatReady))

const activeSession = computed(() =>
  sessions.value.find((row) => row.id === activeSessionId.value) ?? null,
)

const activeAssistant = computed((): LensCopilotAssistant | null => {
  const session = activeSession.value
  if (!session?.sl_assistant_uuid) return null
  const fromList = assistantByUuid.value.get(session.sl_assistant_uuid)
  if (fromList) return fromList
  if (session.assistant_name) {
    return {
      uuid: session.sl_assistant_uuid,
      name: session.assistant_name,
      slug: '',
      status: 'active',
      selected_task: session.selected_task || undefined,
      agent_model_ref: session.agent_model_ref,
      multimodal_model_ref: session.multimodal_model_ref,
    }
  }
  return null
})

const activeMessages = computed(() => {
  const id = activeSessionId.value
  if (id == null) return []
  return messagesBySession.value[id] ?? []
})

const activeStream = computed(() => {
  const id = activeSessionId.value
  if (id == null) return null
  return getSessionRunStream(id)
})

const runInProgress = computed(() => {
  const stream = activeStream.value
  return Boolean(
    isActiveRunStatus(activeSession.value?.active_run_status)
    || stream?.isSubmitting
    || stream?.streamAttached
    || isActiveRunStatus(stream?.runStatus),
  )
})

const showLiveStream = computed(
  () => runInProgress.value || Boolean(activeStream.value?.streamError),
)

const runCanStop = computed(
  () => runInProgress.value && Boolean(activeStream.value?.runUuid),
)

const composerDisabled = computed(() => {
  if (loading.value || !bridgeReady.value || activeSessionId.value == null) return true
  const session = sessions.value.find((row) => row.id === activeSessionId.value)
  if (session && session.lifecycle_status && session.lifecycle_status !== 'ready') return true
  const stream = activeStream.value
  if (!stream) return false
  return runInProgress.value
})

const bubbleTag = computed(() => activeAssistant.value?.name ?? '')

const supportsImageAttachments = computed(() =>
  Boolean(activeAssistant.value?.multimodal_model_ref),
)

const supportsDocumentAttachments = computed(() =>
  activeAssistant.value?.supports_document_attachments === true
  && activeAssistant.value?.selected_task !== 'general_chat',
)

const selectedStarterKey = computed(() => {
  const sessionId = activeSessionId.value
  return sessionId == null ? '' : selectedStarterBySession.value[sessionId] || ''
})

const emptyPhase = computed((): CopilotEmptyPhase => {
  if (loading.value) return 'loading'
  if (bootstrapError.value === 'network') return 'network_error'
  if (!bridgeReady.value) return 'bridge_not_ready'
  return 'onboarding'
})

const copilotReadiness = computed((): CopilotReadiness => {
  const hasDefaultAgent = Boolean(modelReadiness.value?.default_agent_model_ref)
  const readyCopilotGateways = copilotGatewayOptions.value.filter((row) => row.copilot_eligible)
  const hasAssistants = chatReadyAssistants.value.length > 0
  return {
    hasModels: hasDefaultAgent,
    hasGateways: readyCopilotGateways.length > 0,
    hasKnowledgeSources: knowledgeSources.value.length > 0,
    hasAssistants,
    canStartChat: hasDefaultAgent && readyCopilotGateways.length > 0,
  }
})

const showActiveChat = computed(() => activeSession.value != null)

function sessionCleanupInProgress(session: LensSessionLink | SessionRow) {
  return session.lifecycle_status === 'failed'
    && session.cleanup_intent === 'reset_for_retry'
    && ['pending', 'running', 'blocked'].includes(session.cleanup_status || '')
}

function sessionNeedsLifecyclePolling(session: LensSessionLink | SessionRow) {
  return session.lifecycle_status === 'provisioning'
    || session.lifecycle_status === 'deleting'
    || sessionCleanupInProgress(session)
}

async function markSessionViewed(sessionId: number) {
  copilotStore.markSessionViewed(sessionId)
  try {
    const updated = await markCopilotSessionViewed(sessionId)
    sessions.value = sessions.value.map((row) => row.id === sessionId
      ? { ...row, last_viewed_at: updated.last_viewed_at, has_unread: false }
      : row)
  } catch {
    sessions.value = sessions.value.map((row) => row.id === sessionId ? { ...row, has_unread: false } : row)
  }
}

async function bootstrap() {
  loading.value = true
  bootstrapError.value = null
  try {
    let health
    try {
      health = await fetchLensHealth()
    } catch {
      bootstrapError.value = 'network'
      bridgeReady.value = false
      assistants.value = []
      gateways.value = []
      copilotGatewayOptions.value = []
      knowledgeSources.value = []
      modelReadiness.value = null
      sessions.value = []
      clearComposerAttachments({ deleteDocuments: true })
      activeSessionId.value = null
      return
    }

    bridgeReady.value = Boolean(health.lens?.configured && health.lens?.authenticated)
    if (!bridgeReady.value) {
      assistants.value = []
      gateways.value = []
      copilotGatewayOptions.value = []
      knowledgeSources.value = []
      modelReadiness.value = null
      sessions.value = []
      clearComposerAttachments({ deleteDocuments: true })
      activeSessionId.value = null
      return
    }

    const [assistantRows, sessionRows, readiness, gatewayRows, copilotGatewayRows, ksRows] = await Promise.all([
      listCopilotAssistants().catch(() => [] as LensCopilotAssistant[]),
      listCopilotSessions().catch(() => [] as LensSessionLink[]),
      fetchCopilotReadiness().catch(() => null),
      listLensGateways().catch(() => [] as LensGatewayInsight[]),
      listCopilotGatewayOptions().catch(() => [] as LensCopilotGatewayOption[]),
      listKnowledgeSources().catch(() => [] as LensKnowledgeSource[]),
    ])
    assistants.value = assistantRows
    gateways.value = gatewayRows
    copilotGatewayOptions.value = copilotGatewayRows
    knowledgeSources.value = ksRows
    modelReadiness.value = readiness
    sessions.value = toSessionRows(sessionRows)
    refreshPollerSessions()
    for (const session of sessions.value) {
      if (sessionNeedsLifecyclePolling(session)) {
        void pollSessionLifecycle(session.id)
      }
    }

    if (sessions.value.length) {
      const remembered = sessions.value.find((row) => row.id === activeSessionId.value)
      const routeSessionId = Number(route.query.session)
      const requested = Number.isFinite(routeSessionId)
        ? sessions.value.find((row) => row.id === routeSessionId)
        : null
      const nextId = requested?.id ?? remembered?.id ?? sessions.value[0]!.id
      if (activeSessionId.value !== nextId) {
        clearComposerAttachments({ deleteDocuments: true })
      }
      activeSessionId.value = nextId
      const next = sessions.value.find((row) => row.id === nextId)
      if (next && sessionNeedsLifecyclePolling(next)) {
        void pollSessionLifecycle(nextId)
      } else if (next?.lifecycle_status !== 'failed') {
        await copilotStore.syncSession(nextId, syncHandlers, nextId, { attachStream: true })
      }
    } else {
      clearComposerAttachments({ deleteDocuments: true })
      activeSessionId.value = null
    }

    copilotStore.startBackgroundPoller(syncHandlers, () => activeSessionId.value)
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

async function selectSession(id: number) {
  const previousId = activeSessionId.value
  if (previousId !== id) clearComposerAttachments({ deleteDocuments: true })
  activeSessionId.value = id
  try {
    await copilotStore.selectSession(previousId, id, syncHandlers, id)
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.loadFailed')), grouping: true })
  }
  scrollToBottom()
}

function openNewChatFlow() {
  void router.push('/insight/copilot/new')
}

async function pollSessionLifecycle(sessionId: number) {
  if (componentUnmounted || !componentActive) return
  const pollingEpoch = lifecyclePollingEpoch
  if (lifecyclePollingEpochs.get(sessionId) === pollingEpoch) return
  lifecyclePollingEpochs.set(sessionId, pollingEpoch)
  try {
    let pollDelayMilliseconds = 3000
    let unchangedPolls = 0
    let previousFingerprint = ''
    while (
      !componentUnmounted
      && componentActive
      && lifecyclePollingEpoch === pollingEpoch
    ) {
      await new Promise((resolve) => setTimeout(resolve, pollDelayMilliseconds))
      if (
        componentUnmounted
        || !componentActive
        || lifecyclePollingEpoch !== pollingEpoch
      ) return
      try {
        const rows = await listCopilotSessions()
        if (
          componentUnmounted
          || !componentActive
          || lifecyclePollingEpoch !== pollingEpoch
        ) return
        sessions.value = toSessionRows(rows)
        refreshPollerSessions()
        const current = sessions.value.find((row) => row.id === sessionId)
        if (!current) return
        if (current.lifecycle_status === 'ready') {
          if (activeSessionId.value === sessionId) {
            await copilotStore.syncSession(sessionId, syncHandlers, sessionId, { attachStream: true })
          } else {
            copilotStore.notifySessionComplete(sessionId, activeSessionId.value)
          }
          return
        }
        if (current.lifecycle_status === 'deleted') return
        if (current.lifecycle_status === 'failed' && !sessionCleanupInProgress(current)) return
        const fingerprint = [
          current.lifecycle_status,
          current.provision_phase,
          current.provision_detail,
          current.cleanup_status,
        ].join(':')
        unchangedPolls = fingerprint === previousFingerprint ? unchangedPolls + 1 : 0
        previousFingerprint = fingerprint
        if (current.cleanup_status === 'blocked' || unchangedPolls >= 20) {
          pollDelayMilliseconds = 15000
        } else if (unchangedPolls >= 5) {
          pollDelayMilliseconds = 10000
        } else {
          pollDelayMilliseconds = 3000
        }
      } catch {
        if (componentUnmounted) return
        pollDelayMilliseconds = Math.min(30000, Math.max(5000, pollDelayMilliseconds * 2))
      }
    }
  } finally {
    if (lifecyclePollingEpochs.get(sessionId) === pollingEpoch) {
      lifecyclePollingEpochs.delete(sessionId)
    }
  }
}

async function retryProvision(row: SessionRow) {
  try {
    const updated = await retryCopilotSession(row.id)
    sessions.value = sessions.value.map((item) => item.id === row.id ? { ...updated, group: item.group } : item)
    if (activeSessionId.value !== row.id) {
      clearComposerAttachments({ deleteDocuments: true })
    }
    activeSessionId.value = row.id
    if (updated.lifecycle_status === 'ready') {
      await copilotStore.syncSession(row.id, syncHandlers, row.id, { attachStream: true })
      return
    }
    if (updated.lifecycle_status === 'provisioning') {
      void pollSessionLifecycle(row.id)
    }
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
  }
}

async function renameSession(row: SessionRow, title: string) {
  try {
    const updated = await renameCopilotSession(row.id, title)
    sessions.value = sessions.value.map((item) =>
      item.id === row.id ? { ...item, title: updated.title } : item,
    )
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('insight.copilot.renameFailed')), grouping: true })
  }
}

async function setSessionPinned(row: SessionRow, pinned: boolean) {
  try {
    const updated = pinned
      ? await pinCopilotSession(row.id)
      : await unpinCopilotSession(row.id)
    sessions.value = toSessionRows(
      sessions.value.map((item) =>
        item.id === row.id
          ? { ...item, pinned_at: updated.pinned_at ?? null }
          : item,
      ),
    )
    ElMessage.success({
      message: t(
        pinned
          ? 'insight.copilot.sessionPinned'
          : 'insight.copilot.sessionUnpinned',
      ),
      grouping: true,
    })
  } catch (err) {
    ElMessage.error({
      message: apiErrorMessage(
        err,
        t('insight.copilot.sessionActionFailed'),
      ),
      grouping: true,
    })
  }
}

function deleteActiveSession() {
  if (activeSession.value) void deleteSession(activeSession.value)
}

function retryActiveSession() {
  if (activeSession.value) void retryProvision(activeSession.value)
}

function deleteSession(row: SessionRow) {
  deleteTarget.value = row
  deleteOpen.value = true
}

async function confirmDeleteSession() {
  const row = deleteTarget.value
  if (!row) return
  deleteLoading.value = true
  try {
    await deleteCopilotSession(row.id)
    copilotStore.detachSessionStream(row.id)
    sessions.value = sessions.value.filter((item) => item.id !== row.id)
    const copy = { ...messagesBySession.value }
    delete copy[row.id]
    messagesBySession.value = copy
    const starterCopy = { ...selectedStarterBySession.value }
    delete starterCopy[row.id]
    selectedStarterBySession.value = starterCopy
    if (activeSessionId.value === row.id) {
      clearComposerAttachments({ deleteDocuments: false })
      activeSessionId.value = sessions.value[0]?.id ?? null
      if (activeSessionId.value != null) {
        await copilotStore.syncSession(
          activeSessionId.value,
          syncHandlers,
          activeSessionId.value,
          { attachStream: true },
        )
      }
    }
    refreshPollerSessions()
    deleteOpen.value = false
    deleteTarget.value = null
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
  } finally {
    deleteLoading.value = false
  }
}

function scrollToBottom() {
  messageListRef.value?.scrollToBottom()
}

async function applyStarterChip(key: string, text: string) {
  const sessionId = activeSessionId.value
  if (sessionId == null || composerDisabled.value) return
  selectedStarterBySession.value = {
    ...selectedStarterBySession.value,
    [sessionId]: key,
  }
  await submitQuestion(text)
}

function retryQuestion(text: string) {
  input.value = text
}

function attachmentErrorMessage(error: unknown) {
  const fallbackMessage = apiErrorMessage(error, '')
  const diagnostic = String(normalizeThrownError(error).meta?.diagnostic || '')
  // HFL's problem-details handler keeps upstream validation reasons in
  // meta.diagnostic while exposing a generic VALIDATION.FAILED title. Prefer
  // the stable SourceLens reason here so the existing localized copy can be
  // selected without duplicating SourceLens validation rules in HFL.
  const message = [fallbackMessage, diagnostic].find((candidate) =>
    candidate.includes('ATTACHMENT_')
      || candidate.includes('DOCUMENT_ATTACHMENTS_UNSUPPORTED')
      || candidate.includes('does not accept images')
      || candidate.includes('does not accept document'),
  ) || fallbackMessage
  if (message.includes('ATTACHMENT_TOO_LARGE')) {
    return t('insight.copilot.attachmentTooLarge')
  }
  if (message.includes('ATTACHMENT_DIMENSIONS_TOO_LARGE')) {
    return t('insight.copilot.attachmentImageDimensionsTooLarge')
  }
  if (message.includes('ATTACHMENT_ASPECT_UNSUPPORTED')) {
    return t('insight.copilot.attachmentImageAspectUnsupported')
  }
  if (
    message.includes('ATTACHMENT_UNSUPPORTED_TYPE')
    || message.includes('DOCUMENT_ATTACHMENTS_UNSUPPORTED')
  ) {
    return t('insight.copilot.attachmentUnsupported')
  }
  if (message.includes('ATTACHMENT_TOO_MANY')) {
    return t('insight.copilot.attachmentTooMany')
  }
  if (message.includes('does not accept images')) {
    return t('insight.copilot.attachmentModelUnsupported')
  }
  if (message.includes('does not accept document')) {
    return t('insight.copilot.attachmentDocumentUnsupported')
  }
  return message || t('insight.copilot.attachmentUploadFailed')
}

function isDefinitiveRunRejection(error: unknown) {
  const status = Number((error as Partial<ApiError> | null)?.status || 0)
  return status >= 400
    && status < 500
    && ![408, 409, 425, 429].includes(status)
}

function isMissingAttachmentError(error: unknown) {
  const apiError = error as Partial<ApiError> | null
  return [apiError?.errorCode, apiError?.code, apiErrorMessage(error, '')]
    .some((value) => String(value || '').includes('ATTACHMENT_NOT_FOUND'))
}

function revokeComposerAttachment(item: CopilotComposerAttachment) {
  if (item.localUrl) URL.revokeObjectURL(item.localUrl)
}

function revokeComposerAttachments(items: CopilotComposerAttachment[]) {
  for (const item of items) revokeComposerAttachment(item)
}

function clearComposerAttachments({ deleteDocuments }: { deleteDocuments: boolean }) {
  composerLifecycleGeneration += 1
  const current = composerAttachments.value
  composerAttachments.value = []
  for (const item of current) {
    revokeComposerAttachment(item)
    if (
      deleteDocuments
      && item.status === 'ready'
      && item.kind === 'document'
      && activeSessionId.value != null
    ) {
      void deleteCopilotAttachment(
        activeSessionId.value,
        item.uuid,
        item.url,
      ).catch(() => undefined)
    }
  }
}

function removeComposerAttachment(item: CopilotComposerAttachment) {
  composerAttachments.value = composerAttachments.value.filter(
    (candidate) => candidate.key !== item.key,
  )
  revokeComposerAttachment(item)
  if (
    item.status === 'ready'
    && item.kind === 'document'
    && activeSessionId.value != null
  ) {
    void deleteCopilotAttachment(
      activeSessionId.value,
      item.uuid,
      item.url,
    ).catch(() => {
      ElMessage.warning({
        message: t('insight.copilot.attachmentDeleteFailed'),
        grouping: true,
      })
    })
  }
}

async function addComposerAttachments(files: File[]) {
  const sessionId = activeSessionId.value
  if (sessionId == null || composerDisabled.value) return
  const available = Math.max(0, 4 - composerAttachments.value.length)
  const selected = files.slice(0, available)
  if (files.length > available) {
    ElMessage.warning({
      message: t('insight.copilot.attachmentMaxCount', { max: 4 }),
      grouping: true,
    })
  }
  const drafts = selected.map((file) => {
    const key = uid('attachment')
    const kind = file.type.startsWith('image/') ? 'image' : 'document'
    const draft: CopilotComposerAttachment = {
      key,
      uuid: '',
      kind,
      mime_type: file.type,
      byte_size: file.size,
      original_name: file.name,
      localUrl: kind === 'image' ? URL.createObjectURL(file) : '',
      status: 'uploading',
    }
    return { file, draft }
  })
  composerAttachments.value = [
    ...composerAttachments.value,
    ...drafts.map(({ draft }) => draft),
  ]
  await Promise.all(drafts.map(async ({ file, draft }) => {
    try {
      const uploaded = await uploadCopilotAttachment(sessionId, file)
      const current = composerAttachments.value.find((item) => item.key === draft.key)
      if (!current || activeSessionId.value !== sessionId) {
        revokeComposerAttachment(draft)
        if (uploaded.kind === 'document') {
          void deleteCopilotAttachment(
            sessionId,
            uploaded.uuid,
            uploaded.url,
          ).catch(() => undefined)
        }
        return
      }
      composerAttachments.value = composerAttachments.value.map((item) =>
        item.key === draft.key
          ? {
              ...uploaded,
              key: draft.key,
              localUrl: draft.localUrl,
              status: 'ready',
            }
          : item,
      )
    } catch (error) {
      composerAttachments.value = composerAttachments.value.filter(
        (item) => item.key !== draft.key,
      )
      revokeComposerAttachment(draft)
      ElMessage.error({
        message: attachmentErrorMessage(error),
        grouping: true,
      })
    }
  }))
}

function apiAttachment(item: CopilotComposerAttachment): LensChatAttachment {
  return {
    uuid: item.uuid,
    url: item.url,
    kind: item.kind,
    mime_type: item.mime_type,
    width: item.width,
    height: item.height,
    byte_size: item.byte_size,
    original_name: item.original_name,
    order: item.order,
    expires_at: item.expires_at,
  }
}

async function submitQuestion(
  question: string,
  {
    clearComposer = false,
    attachments = [],
  }: {
    clearComposer?: boolean
    attachments?: CopilotComposerAttachment[]
  } = {},
) {
  const text = question.trim()
  const sessionId = activeSessionId.value
  const readyAttachments = attachments.filter((item) => item.status === 'ready')
  if ((!text && !readyAttachments.length) || sessionId == null || composerDisabled.value) return

  const submissionComposerGeneration = composerLifecycleGeneration
  const optimisticMessageId = uid('m')
  beginSessionRunSubmission(sessionId)
  const list = [...(messagesBySession.value[sessionId] ?? [])]
  list.push({
    id: optimisticMessageId,
    role: 'user',
    text,
    attachments: readyAttachments.map(apiAttachment),
    createdAt: new Date().toISOString(),
  })
  messagesBySession.value = { ...messagesBySession.value, [sessionId]: list }
  if (clearComposer) input.value = ''
  if (readyAttachments.length) {
    const submittedKeys = new Set(readyAttachments.map((item) => item.key))
    composerAttachments.value = composerAttachments.value.filter(
      (item) => !submittedKeys.has(item.key),
    )
  }
  scrollToBottom()

  let runAccepted = false
  let attachmentPreviewsReleased = false
  try {
    const idempotencyKey = uid(`copilot-${sessionId}`)
    const attachmentUuids = readyAttachments.map((item) => item.uuid)
    const run = attachmentUuids.length
      ? await createCopilotRun(
          sessionId,
          text,
          idempotencyKey,
          attachmentUuids,
        )
      : await createCopilotRun(sessionId, text, idempotencyKey)
    runAccepted = true
    revokeComposerAttachments(readyAttachments)
    attachmentPreviewsReleased = true
    applySessionActiveMeta(sessionId, {
      uuid: run.uuid,
      status: run.status || 'queued',
    })
    await copilotStore.startRunStream(
      sessionId,
      run.uuid,
      run.status || 'queued',
      syncHandlers,
      activeSessionId.value,
    )
  } catch (err) {
    const definitiveRejection = !runAccepted && isDefinitiveRunRejection(err)
    const missingAttachment = isMissingAttachmentError(err)
    const restoreRejectedRequest = definitiveRejection
      && activeSessionId.value === sessionId
      && !componentUnmounted
      && composerLifecycleGeneration === submissionComposerGeneration
    if (definitiveRejection) {
      clearSessionRunSubmission(sessionId)
      const currentMessages = messagesBySession.value[sessionId] ?? []
      messagesBySession.value = {
        ...messagesBySession.value,
        [sessionId]: currentMessages.filter(
          (item) => item.id !== optimisticMessageId,
        ),
      }
    }
    refreshPollerSessions()
    if (restoreRejectedRequest && clearComposer) input.value = question
    if (
      restoreRejectedRequest
      && readyAttachments.length
      && !missingAttachment
    ) {
      composerAttachments.value = [
        ...composerAttachments.value,
        ...readyAttachments,
      ]
    } else if (!attachmentPreviewsReleased) {
      revokeComposerAttachments(readyAttachments)
      attachmentPreviewsReleased = true
    }
    if (definitiveRejection && !restoreRejectedRequest && !missingAttachment) {
      for (const item of readyAttachments) {
        if (item.kind !== 'document') continue
        void deleteCopilotAttachment(
          sessionId,
          item.uuid,
          item.url,
        ).catch(() => undefined)
      }
    }
    const message = apiErrorMessage(err, t('errors.generic.requestFailed'))
    ElMessage.error({ message, grouping: true })
    await copilotStore.syncSession(sessionId, syncHandlers, activeSessionId.value).catch(() => undefined)
  }
}

async function sendMessage() {
  await submitQuestion(input.value, {
    clearComposer: true,
    attachments: [...composerAttachments.value],
  })
}

async function stopStreaming() {
  const sessionId = activeSessionId.value
  const stream = activeStream.value
  if (sessionId == null || !stream?.runUuid) {
    copilotStore.detachSessionStream(sessionId ?? -1)
    return
  }
  try {
    await copilotStore.cancelActiveRun(
      sessionId,
      stream.runUuid,
      syncHandlers,
      activeSessionId.value,
    )
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
  }
}

onMounted(() => {
  componentUnmounted = false
  componentActive = true
  void bootstrap()
})

watch(
  () => route.query.session,
  () => {
    if (bridgeReady.value) void bootstrap()
  },
)

onActivated(() => {
  componentUnmounted = false
  componentActive = true
  if (bridgeReady.value) {
    copilotStore.startBackgroundPoller(syncHandlers, () => activeSessionId.value)
    const id = activeSessionId.value
    if (id != null) {
      void copilotStore.syncSession(id, syncHandlers, id, { attachStream: true })
    }
    for (const session of sessions.value) {
      if (sessionNeedsLifecyclePolling(session)) {
        void pollSessionLifecycle(session.id)
      }
    }
  }
})

onDeactivated(() => {
  componentActive = false
  lifecyclePollingEpoch += 1
  clearComposerAttachments({ deleteDocuments: true })
  copilotStore.detachSessionStream(activeSessionId.value ?? -1)
})

onUnmounted(() => {
  componentUnmounted = true
  componentActive = false
  lifecyclePollingEpoch += 1
  clearComposerAttachments({ deleteDocuments: true })
  copilotStore.teardown()
})
</script>

<template>
  <div class="copilot-root">
    <CopilotSessionSidebar
      v-if="!isPhone"
      :sessions="sessions"
      :active-id="activeSessionId"
      :loading="loading"
      :pending-notifications="copilotStore.pendingNotifications.value"
      @select="selectSession"
      @delete="deleteSession"
      @rename="renameSession"
      @retry="retryProvision"
      @pin="setSessionPinned"
      @new-chat="openNewChatFlow"
    />

    <ElDrawer
      v-else
      v-model="mobileSessionsOpen"
      class="copilot-session-drawer"
      direction="ltr"
      size="min(88vw, 360px)"
      :title="t('insight.copilot.sessions')"
      append-to-body
    >
      <CopilotSessionSidebar
        :sessions="sessions"
        :active-id="activeSessionId"
        :loading="loading"
        :pending-notifications="copilotStore.pendingNotifications.value"
        @select="selectSession($event); mobileSessionsOpen = false"
        @delete="deleteSession"
        @rename="renameSession"
        @retry="retryProvision"
        @pin="setSessionPinned"
        @new-chat="mobileSessionsOpen = false; openNewChatFlow()"
      />
    </ElDrawer>

    <div class="copilot-main flex min-h-0 min-w-0 flex-1 flex-col bg-[var(--color-card-bg)]">
      <div class="copilot-mobile-navigation">
        <button
          type="button"
          :aria-label="t('insight.copilot.sessions')"
          @click="mobileSessionsOpen = true"
        >
          <Menu
            :size="20"
            aria-hidden="true"
          />
          <span>{{ t('insight.copilot.sessions') }}</span>
        </button>
      </div>
      <template v-if="showActiveChat">
        <CopilotContextBar
          v-if="activeSession"
          :session="activeSession"
        />

        <CopilotLifecycleState
          v-if="activeSession && activeSession.lifecycle_status !== 'ready'"
          :session="activeSession"
          @retry="retryActiveSession"
          @delete="deleteActiveSession"
        />

        <div
          v-if="activeSession?.lifecycle_status === 'ready'"
          class="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <CopilotMessageList
            :key="activeSessionId"
            ref="messageListRef"
            :session-id="activeSessionId ?? 0"
            :messages="activeMessages"
            :streaming="showLiveStream"
            :streaming-content="activeStream?.partialAnswer ?? ''"
            :streaming-thinking="activeStream?.thinkingSteps ?? []"
            :streaming-elapsed-seconds="activeStream?.thinkingElapsedSeconds ?? 0"
            :stream-error="activeStream?.streamError ?? ''"
            :bubble-tag="bubbleTag"
            :selected-starter-key="selectedStarterKey"
            :starter-disabled="composerDisabled"
            @starter-chip="applyStarterChip"
            @retry-question="retryQuestion"
          />
        </div>

        <CopilotComposer
          v-if="activeSession?.lifecycle_status === 'ready'"
          v-model="input"
          :attachments="composerAttachments"
          :sending="runCanStop"
          :disabled="composerDisabled"
          :supports-images="supportsImageAttachments"
          :supports-documents="supportsDocumentAttachments"
          @send="sendMessage"
          @stop="stopStreaming"
          @attach="addComposerAttachments"
          @remove-attachment="removeComposerAttachment"
        />
      </template>

      <CopilotEmptyState
        v-else
        :phase="emptyPhase"
        :readiness="copilotReadiness"
        @retry="bootstrap"
        @new-chat="openNewChatFlow"
      />
    </div>
    <DangerConfirmDialog
      v-model="deleteOpen"
      :title="t('insight.copilot.deleteConfirm')"
      :message="t('insight.copilot.deleteConfirm')"
      :items="deleteTarget ? [{ key: deleteTarget.id, name: deleteTarget.title }] : []"
      :cancel-text="t('insight.copilot.btnCancel')"
      :confirm-text="t('insight.copilot.btnConfirm')"
      :loading="deleteLoading"
      @confirm="confirmDeleteSession"
      @cancel="deleteTarget = null"
    />
  </div>
</template>

<style scoped>
.copilot-root {
  display: flex;
  flex-direction: row;
  align-items: stretch;
  width: 100%;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.copilot-main {
  min-height: 0;
}

.copilot-mobile-navigation {
  display: none;
}

@media (max-width: 767.98px) {
  .copilot-mobile-navigation {
    display: flex;
    min-height: 52px;
    flex: 0 0 auto;
    align-items: center;
    padding: 4px 10px;
    border-bottom: 1px solid var(--color-border-light);
    background: var(--color-card-bg);
  }

  .copilot-mobile-navigation button {
    display: inline-flex;
    min-height: 44px;
    align-items: center;
    gap: 8px;
    padding: 0 12px;
    border: 0;
    border-radius: 9px;
    background: var(--el-fill-color-light);
    color: var(--color-text-title);
    font: inherit;
    font-weight: 600;
  }
}

:global(.copilot-session-drawer .el-drawer__body) {
  padding: 0 !important;
}

:global(.copilot-session-drawer .copilot-aside) {
  width: 100%;
  height: 100%;
  border-right: 0;
}
</style>
