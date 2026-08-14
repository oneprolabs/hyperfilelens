<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

defineOptions({ name: 'InsightCopilot' })
import { apiErrorMessage } from '../../lib/api'
import {
  beginSessionRunSubmission,
  clearSessionRunSubmission,
  getSessionRunStream,
  isActiveRunStatus,
} from '../../composables/useLensRunStream'
import { useCopilotRunStore } from '../../stores/copilotRunStore'
import {
  createCopilotRun,
  deleteCopilotSession,
  fetchCopilotReadiness,
  fetchLensHealth,
  listCopilotAssistants,
  listCopilotGatewayOptions,
  listCopilotSessions,
  listKnowledgeSources,
  listLensGateways,
  markCopilotSessionViewed,
  renameCopilotSession,
  retryCopilotSession,
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
import type { CopilotDisplayMessage } from './copilot/types'
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
const messageListRef = ref<{ scrollToBottom: () => void } | null>(null)
const lifecyclePollingIds = new Set<number>()
let componentUnmounted = false

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
    group: groupForDate(row.last_message_at || row.created_at),
  }))
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
      if (session.lifecycle_status === 'provisioning' || session.lifecycle_status === 'deleting') {
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
      activeSessionId.value = nextId
      const next = sessions.value.find((row) => row.id === nextId)
      if (next?.lifecycle_status === 'provisioning') {
        void pollSessionLifecycle(nextId)
      } else if (next?.lifecycle_status !== 'failed') {
        await copilotStore.syncSession(nextId, syncHandlers, nextId, { attachStream: true })
      }
    } else {
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
  if (lifecyclePollingIds.has(sessionId)) return
  lifecyclePollingIds.add(sessionId)
  try {
    for (let i = 0; i < 120 && !componentUnmounted; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 3000))
      try {
        const rows = await listCopilotSessions()
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
        if (current.lifecycle_status === 'failed' || current.lifecycle_status === 'deleted') return
      } catch {
        if (componentUnmounted) return
      }
    }
  } finally {
    lifecyclePollingIds.delete(sessionId)
  }
}

async function retryProvision(row: SessionRow) {
  try {
    const updated = await retryCopilotSession(row.id)
    sessions.value = sessions.value.map((item) => item.id === row.id ? { ...updated, group: item.group } : item)
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

function onAttach() {
  ElMessage.info({ message: t('insight.copilot.attachComingSoon'), grouping: true })
}

async function submitQuestion(question: string, { clearComposer = false } = {}) {
  const text = question.trim()
  const sessionId = activeSessionId.value
  if (!text || sessionId == null || composerDisabled.value) return

  beginSessionRunSubmission(sessionId)
  const list = [...(messagesBySession.value[sessionId] ?? [])]
  list.push({ id: uid('m'), role: 'user', text, createdAt: new Date().toISOString() })
  messagesBySession.value = { ...messagesBySession.value, [sessionId]: list }
  if (clearComposer) input.value = ''
  scrollToBottom()

  try {
    const run = await createCopilotRun(sessionId, text, uid(`copilot-${sessionId}`))
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
    clearSessionRunSubmission(sessionId)
    const message = apiErrorMessage(err, t('errors.generic.requestFailed'))
    ElMessage.error({ message, grouping: true })
    await copilotStore.syncSession(sessionId, syncHandlers, activeSessionId.value).catch(() => undefined)
  }
}

async function sendMessage() {
  await submitQuestion(input.value, { clearComposer: true })
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
  if (bridgeReady.value) {
    copilotStore.startBackgroundPoller(syncHandlers, () => activeSessionId.value)
    const id = activeSessionId.value
    if (id != null) {
      void copilotStore.syncSession(id, syncHandlers, id, { attachStream: true })
    }
    for (const session of sessions.value) {
      if (session.lifecycle_status === 'provisioning' || session.lifecycle_status === 'deleting') {
        void pollSessionLifecycle(session.id)
      }
    }
  }
})

onDeactivated(() => {
  copilotStore.detachSessionStream(activeSessionId.value ?? -1)
})

onUnmounted(() => {
  componentUnmounted = true
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
        @new-chat="mobileSessionsOpen = false; openNewChatFlow()"
      />
    </ElDrawer>

    <div class="copilot-main flex min-h-0 min-w-0 flex-1 flex-col bg-[var(--color-card-bg)]">
      <div class="copilot-mobile-navigation">
        <button type="button" :aria-label="t('insight.copilot.sessions')" @click="mobileSessionsOpen = true">
          <Menu :size="20" aria-hidden="true" />
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

        <div v-if="activeSession?.lifecycle_status === 'ready'" class="flex min-h-0 flex-1 flex-col overflow-hidden">
          <CopilotMessageList
            :key="activeSessionId"
            ref="messageListRef"
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
          :sending="runCanStop"
          :disabled="composerDisabled"
          @send="sendMessage"
          @stop="stopStreaming"
          @attach="onAttach"
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
