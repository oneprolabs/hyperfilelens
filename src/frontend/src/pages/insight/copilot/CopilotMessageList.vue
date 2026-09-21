<script setup lang="ts">
import { ArrowDown, ChevronDown, ChevronUp, Copy, Download, RefreshCw, Share2, ThumbsDown, ThumbsUp } from 'lucide-vue-next'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { copyTextToClipboard } from '../../../lib/clipboard'
import CopilotMarkdown from '../../../components/copilot/CopilotMarkdown.vue'
import CopilotStreamingMarkdown from '../../../components/copilot/CopilotStreamingMarkdown.vue'
import CopilotAttachmentList from './CopilotAttachmentList.vue'
import CopilotOutputFileList from './CopilotOutputFileList.vue'
import CopilotThinkingTimeline from './CopilotThinkingTimeline.vue'
import CopilotMessageCitations from './CopilotMessageCitations.vue'
import { messagesForLiveHandoff } from './copilotLiveHandoff'
import {
  formatAgentActivityDuration,
  withAgentActivityDuration,
} from './copilotAgentActivityText'
import { hasStructuredRuntimeContent, thinkingActivityCount } from './copilotThinkingActivities'
import { selectCopilotLiveInterim } from './copilotLiveStatus'
import type { CopilotDisplayMessage, CopilotFeedbackUpdate, CopilotRetryDraft } from './types'
import type { ThinkingStep } from '../../../composables/useLensRunStream'
import {
  fetchCopilotRunPdf,
  updateCopilotRunFeedback,
  type LensRunFeedback,
} from '../../../lib/lensApi'

const props = defineProps<{
  sessionId: number
  messages: CopilotDisplayMessage[]
  streamingContent?: string
  streamingThinking?: ThinkingStep[]
  streaming?: boolean
  streamingElapsedSeconds?: number
  streamingRunStatus?: string | null
  streamingQueuePosition?: number | null
  streamingResumeBy?: string | null
  streamError?: string
  bubbleTag?: string
  starterDisabled?: boolean
  clarificationResetToken?: number
  sharedRunId?: string | null
}>()

const emit = defineEmits<{
  retryQuestion: [draft: CopilotRetryDraft]
  feedbackUpdated: [update: CopilotFeedbackUpdate]
  clarificationSubmitted: [runUuid: string, requestId: string, answer: string]
  shareAnswer: [message: CopilotDisplayMessage]
}>()

const { t } = useI18n()
const expandedThinking = ref<Set<string>>(new Set())
const feedbackUpdating = ref<Set<string>>(new Set())
const pdfDownloading = ref<Set<string>>(new Set())
const clarificationAnswers = ref<Record<string, string>>({})
const clarificationSubmitting = ref<Set<string>>(new Set())
const chatScrollRef = ref<HTMLElement | null>(null)
const copilotThreadRef = ref<HTMLElement | null>(null)
const followsLatest = ref(true)
let contentResizeObserver: ResizeObserver | null = null

watch(
  () => props.clarificationResetToken,
  () => {
    clarificationSubmitting.value = new Set()
  },
)

const BOTTOM_FOLLOW_THRESHOLD = 48

function isNearBottom(el: HTMLElement) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_FOLLOW_THRESHOLD
}

function syncFollowState() {
  const el = chatScrollRef.value
  if (el) followsLatest.value = isNearBottom(el)
}

function alignToLatestIfFollowing() {
  if (!followsLatest.value) return
  const el = chatScrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}

function scrollToBottom() {
  followsLatest.value = true
  nextTick(alignToLatestIfFollowing)
}

watch(
  () => [
    props.messages.length,
    props.streamingContent,
    props.streamingThinking?.length,
    props.streaming,
    props.streamError,
  ],
  () => {
    nextTick(alignToLatestIfFollowing)
  },
  { flush: 'post' },
)

onMounted(() => {
  scrollToBottom()
  const thread = copilotThreadRef.value
  if (thread && typeof ResizeObserver !== 'undefined') {
    contentResizeObserver = new ResizeObserver(alignToLatestIfFollowing)
    contentResizeObserver.observe(thread)
  }
})

onBeforeUnmount(() => contentResizeObserver?.disconnect())

defineExpose({ scrollToBottom })

function toggleThinking(id: string) {
  const next = new Set(expandedThinking.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedThinking.value = next
  if (followsLatest.value) scrollToBottom()
}

function thinkingStepsFor(message: CopilotDisplayMessage) {
  return message.thinking?.steps ?? []
}

function thinkingCountFor(message: CopilotDisplayMessage) {
  return thinkingActivityCount(thinkingStepsFor(message))
}

function thinkingStatusFor(message: CopilotDisplayMessage) {
  const count = thinkingCountFor(message)
  if (!count) return t('insight.copilot.runtimeCardHint')
  const base = t('insight.copilot.agentActivitiesDone', { count })
  return withAgentActivityDuration(base, thinkingDuration(message))
}

function hasThinkingContent(message: CopilotDisplayMessage) {
  const steps = thinkingStepsFor(message)
  return thinkingActivityCount(steps) > 0 || hasStructuredRuntimeContent(steps)
}

function showAssistantActions(message: CopilotDisplayMessage) {
  return message.role === 'assistant' && Boolean(message.text) && !message.isWelcome && !message.isError
}

function isLatestShareableAnswer(message: CopilotDisplayMessage) {
  const latest = [...props.messages]
    .reverse()
    .find((row) => (
      row.role === 'assistant'
      && !row.isWelcome
      && !row.isError
      && Boolean(row.runId)
      && Boolean(row.completedAt)
      && Boolean(row.text?.trim())
    ))
  return latest?.id === message.id
}

function thinkingOutcomeFor(message: CopilotDisplayMessage) {
  const outcome = String(message.thinking?.outcome || '').trim()
  if (!outcome || /^(completed|complete|done|success|succeeded)$/i.test(outcome)) return ''
  return outcome
}

/** SourceLens puts Recorded/Completed + duration in the card header summary. */
const liveThinkingStatus = computed(() => {
  const seconds = props.streamingElapsedSeconds ?? 0
  const count = thinkingActivityCount(props.streamingThinking ?? [])
  if (count > 0) {
    return withAgentActivityDuration(
      t('insight.copilot.agentActivitiesLiveProgress', { count }),
      seconds,
    )
  }
  return seconds > 0 ? formatAgentActivityDuration(seconds) : ''
})

const showLiveActivityCard = computed(() => (
  thinkingActivityCount(props.streamingThinking ?? []) > 0
  || hasStructuredRuntimeContent(props.streamingThinking ?? [])
))

const liveInterimStatusText = computed(() => {
  const interim = selectCopilotLiveInterim({
    steps: props.streamingThinking ?? [],
    runStatus: props.streamingRunStatus,
    queuePosition: props.streamingQueuePosition,
    resumeBy: props.streamingResumeBy,
  })
  return t(interim.key, interim.params ?? {})
})

const liveElapsedText = computed(() => {
  const seconds = props.streamingElapsedSeconds ?? 0
  return seconds > 0 ? formatAgentActivityDuration(seconds) : ''
})

const showRetrievalHint = computed(
  () =>
    Boolean(props.streaming) &&
    thinkingActivityCount(props.streamingThinking ?? []) > 0 &&
    !(props.streamingContent || '').trim() &&
    !props.streamError,
)

function thinkingDuration(message: CopilotDisplayMessage) {
  return message.thinking?.duration_seconds ?? null
}

function formatMessageTime(iso?: string) {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const now = new Date()
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const startMsgDay = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const isToday = startMsgDay.getTime() === startToday.getTime()
  const yesterday = new Date(startToday)
  yesterday.setDate(yesterday.getDate() - 1)
  const isYesterday = startMsgDay.getTime() === yesterday.getTime()

  const timePart = date.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })

  if (isToday) return timePart
  if (isYesterday) return `${t('insight.copilot.messageTimeYesterday')} ${timePart}`
  const datePart = date.toLocaleDateString(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
  return `${datePart} ${timePart}`
}

function questionForMessage(message: CopilotDisplayMessage) {
  const idx = props.messages.findIndex((row) => row.id === message.id)
  if (idx < 0) return ''
  for (let i = idx - 1; i >= 0; i -= 1) {
    const row = props.messages[i]
    if (row?.role === 'user' && row.text?.trim()) {
      return row.text.trim()
    }
  }
  return ''
}

async function copyText(text: string) {
  try {
    await copyTextToClipboard(text)
    ElMessage.success({ message: t('common.copied'), grouping: true })
  } catch {
    ElMessage.error({ message: t('errors.generic.requestFailed'), grouping: true })
  }
}

function retryForMessage(message: CopilotDisplayMessage) {
  const question = questionForMessage(message)
  if (!question || !message.runId) return
  emit('retryQuestion', {
    sessionId: props.sessionId,
    question,
    runId: message.runId,
  })
}

async function downloadMessagePdf(message: CopilotDisplayMessage) {
  const runId = message.runId
  if (!runId || pdfDownloading.value.has(runId)) return
  pdfDownloading.value = new Set(pdfDownloading.value).add(runId)
  try {
    const { blob, filename } = await fetchCopilotRunPdf(props.sessionId, runId)
    const href = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = href
    link.download = filename || 'answer.pdf'
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(href)
  } catch {
    ElMessage.error({
      message: t('insight.copilot.pdfDownloadFailed'),
      grouping: true,
    })
  } finally {
    const pending = new Set(pdfDownloading.value)
    pending.delete(runId)
    pdfDownloading.value = pending
  }
}

function feedbackForMessage(message: CopilotDisplayMessage): LensRunFeedback | null {
  return message.feedback ?? null
}

function feedbackIsUpdating(message: CopilotDisplayMessage) {
  return Boolean(message.runId && feedbackUpdating.value.has(message.runId))
}

function submitClarification(message: CopilotDisplayMessage) {
  const request = message.clarificationRequest
  const runId = message.runId
  const answer = runId ? (clarificationAnswers.value[runId] || '').trim() : ''
  if (!request || !runId || !answer || clarificationSubmitting.value.has(runId)) return
  clarificationSubmitting.value = new Set(clarificationSubmitting.value).add(runId)
  emit('clarificationSubmitted', runId, request.requestId, answer)
}

async function setMessageFeedback(
  message: CopilotDisplayMessage,
  requested: LensRunFeedback,
) {
  const runId = message.runId
  if (!runId || feedbackUpdating.value.has(runId)) return
  const nextFeedback = feedbackForMessage(message) === requested ? '' : requested
  feedbackUpdating.value = new Set(feedbackUpdating.value).add(runId)
  try {
    const result = await updateCopilotRunFeedback(
      props.sessionId,
      runId,
      nextFeedback,
    )
    emit('feedbackUpdated', {
      sessionId: props.sessionId,
      messageId: message.id,
      runId,
      feedback: result.feedback || null,
    })
    ElMessage.success({
      message: nextFeedback
        ? t('insight.copilot.feedbackThanks')
        : t('insight.copilot.feedbackCleared'),
      grouping: true,
    })
  } catch {
    ElMessage.error({
      message: t('insight.copilot.feedbackFailed'),
      grouping: true,
    })
  } finally {
    const pending = new Set(feedbackUpdating.value)
    pending.delete(runId)
    feedbackUpdating.value = pending
  }
}

const showLiveRow = computed(() => props.streaming)

// Mirror SourceLens: while the live row owns progress + partial answer, do not
// also render the trailing in-progress assistant placeholder from sync.
const displayMessages = computed(() =>
  messagesForLiveHandoff(props.messages, Boolean(props.streaming)),
)
</script>

<template>
  <div class="chat-scroll-shell min-h-0 flex-1">
    <div
      ref="chatScrollRef"
      class="chat-scroll h-full overflow-y-auto"
      @scroll="syncFollowState"
    >
      <div
        ref="copilotThreadRef"
        class="copilot-thread"
      >
        <div
          v-for="msg in displayMessages"
          :key="msg.id"
          class="message-row"
          :class="msg.role === 'user' ? 'message-row-user' : 'message-row-assistant'"
        >
          <div class="message-body">
            <div
              v-if="msg.role === 'assistant' && hasThinkingContent(msg)"
              class="thinking-panel thinking-panel-done"
            >
              <button
                type="button"
                class="thinking-panel-header"
                @click="toggleThinking(msg.id)"
              >
                <span class="thinking-panel-title">{{ t('insight.copilot.agentActivitiesLive') }}</span>
                <span class="thinking-panel-status">
                  {{ thinkingStatusFor(msg) }}
                </span>
                <ChevronUp
                  v-if="expandedThinking.has(msg.id)"
                  :size="13"
                  class="thinking-panel-chevron"
                />
                <ChevronDown
                  v-else
                  :size="13"
                  class="thinking-panel-chevron"
                />
              </button>
              <div
                v-if="expandedThinking.has(msg.id)"
                class="thinking-panel-body"
              >
                <CopilotThinkingTimeline :steps="thinkingStepsFor(msg)" />
                <div
                  v-if="thinkingOutcomeFor(msg)"
                  class="thinking-outcome"
                >
                  {{ thinkingOutcomeFor(msg) }}
                </div>
              </div>
            </div>

            <div
              class="message-card"
              :class="[
                msg.role,
                msg.isError ? 'message-card--error' : '',
                msg.isWelcome ? 'message-card--welcome' : '',
              ]"
            >
              <CopilotAttachmentList
                v-if="msg.attachments?.length"
                :session-id="sessionId"
                :attachments="msg.attachments"
              />
              <div
                v-if="msg.isWelcome || msg.isError"
                class="message-text"
              >
                {{ msg.text }}
              </div>
              <div
                v-else-if="msg.role === 'assistant' && msg.text"
                class="message-markdown"
              >
                <CopilotMarkdown :content="msg.text" />
              </div>
              <div
                v-else-if="msg.text"
                class="message-text"
              >
                {{ msg.text }}
              </div>

              <div
                v-if="msg.role === 'assistant' && msg.plannedEvidence?.sufficient === false"
                class="evidence-warning"
                role="status"
              >
                {{ msg.plannedEvidence.planner_rejection_reason || t('insight.copilot.evidenceMayBeInsufficient') }}
              </div>

              <CopilotOutputFileList
                v-if="msg.role === 'assistant' && msg.outputFiles?.length"
                :session-id="sessionId"
                :files="msg.outputFiles"
              />
              <CopilotMessageCitations
                v-if="msg.role === 'assistant' && msg.citations?.length"
                :session-id="sessionId"
                :run-id="msg.runId"
                :citations="msg.citations"
              />
              <div
                v-if="msg.role === 'assistant' && msg.clarificationRequest && msg.runId"
                class="clarification-card"
              >
                <strong>{{ t('insight.copilot.clarificationTitle') }}</strong>
                <p>{{ msg.clarificationRequest.question }}</p>
                <textarea
                  v-model="clarificationAnswers[msg.runId]"
                  class="clarification-input"
                  :disabled="clarificationSubmitting.has(msg.runId)"
                  :placeholder="t('insight.copilot.clarificationPlaceholder')"
                  rows="3"
                />
                <button
                  type="button"
                  class="clarification-submit"
                  :disabled="clarificationSubmitting.has(msg.runId) || !(clarificationAnswers[msg.runId] || '').trim()"
                  @click="submitClarification(msg)"
                >
                  {{ clarificationSubmitting.has(msg.runId) ? t('insight.copilot.clarificationSubmitting') : t('insight.copilot.clarificationSubmit') }}
                </button>
              </div>
            </div>

            <div
              v-if="msg.createdAt && !showAssistantActions(msg)"
              class="message-time"
              :class="msg.role"
            >
              {{ formatMessageTime(msg.createdAt) }}
            </div>

            <div
              v-if="showAssistantActions(msg)"
              class="message-actions"
            >
              <div class="message-actions-group">
                <button
                  type="button"
                  class="message-action-btn"
                  :title="t('common.copy')"
                  :aria-label="t('common.copy')"
                  @click="copyText(msg.text || '')"
                >
                  <Copy :size="16" />
                </button>
                <button
                  v-if="msg.runId && msg.completedAt"
                  type="button"
                  class="message-action-btn"
                  :title="t('insight.copilot.downloadPdf')"
                  :aria-label="t('insight.copilot.downloadPdf')"
                  :disabled="pdfDownloading.has(msg.runId)"
                  @click="downloadMessagePdf(msg)"
                >
                  <Download :size="16" />
                </button>
                <button
                  v-if="msg.runId && msg.completedAt"
                  type="button"
                  class="message-action-btn"
                  :class="{ 'is-positive': feedbackForMessage(msg) === 'positive' }"
                  :title="t('insight.copilot.likeAnswer')"
                  :aria-label="t('insight.copilot.likeAnswer')"
                  :aria-pressed="feedbackForMessage(msg) === 'positive'"
                  :disabled="feedbackIsUpdating(msg)"
                  @click="setMessageFeedback(msg, 'positive')"
                >
                  <ThumbsUp :size="16" />
                </button>
                <button
                  v-if="msg.runId && msg.completedAt"
                  type="button"
                  class="message-action-btn"
                  :class="{ 'is-negative': feedbackForMessage(msg) === 'negative' }"
                  :title="t('insight.copilot.dislikeAnswer')"
                  :aria-label="t('insight.copilot.dislikeAnswer')"
                  :aria-pressed="feedbackForMessage(msg) === 'negative'"
                  :disabled="feedbackIsUpdating(msg)"
                  @click="setMessageFeedback(msg, 'negative')"
                >
                  <ThumbsDown :size="16" />
                </button>
                <button
                  v-if="msg.runId && msg.completedAt && isLatestShareableAnswer(msg)"
                  type="button"
                  class="message-action-btn"
                  :class="{ 'is-shared': Boolean(msg.runId && sharedRunId && msg.runId === sharedRunId) }"
                  :title="t('insight.copilot.share')"
                  :aria-label="t('insight.copilot.share')"
                  @click="emit('shareAnswer', msg)"
                >
                  <Share2 :size="16" />
                </button>
                <button
                  v-if="msg.runId && msg.completedAt"
                  type="button"
                  class="message-action-btn"
                  :title="t('insight.copilot.regenerateAnswer')"
                  :aria-label="t('insight.copilot.regenerateAnswer')"
                  :disabled="!questionForMessage(msg) || starterDisabled"
                  @click="retryForMessage(msg)"
                >
                  <RefreshCw :size="16" />
                </button>
              </div>
              <span
                v-if="msg.createdAt"
                class="message-time message-time-inline"
              >
                {{ formatMessageTime(msg.createdAt) }}
              </span>
            </div>
          </div>
        </div>

        <div
          v-if="showLiveRow"
          class="message-row message-row-assistant live-progress-row"
        >
          <div class="message-body">
            <div
              v-if="!streamError && showLiveActivityCard"
              class="thinking-panel thinking-panel-live"
            >
              <div
                class="thinking-panel-header thinking-panel-header--static"
                role="status"
                aria-live="polite"
              >
                <span class="live-progress-dot" />
                <span class="thinking-panel-title">{{ t('insight.copilot.agentActivitiesLive') }}</span>
                <span
                  v-if="liveThinkingStatus"
                  class="thinking-panel-status"
                >
                  {{ liveThinkingStatus }}
                </span>
              </div>
              <div
                v-if="thinkingActivityCount(streamingThinking ?? []) || hasStructuredRuntimeContent(streamingThinking ?? [])"
                class="thinking-panel-body"
              >
                <CopilotThinkingTimeline
                  :steps="streamingThinking"
                  live
                />
              </div>
            </div>

            <div
              v-else-if="!streamError"
              class="live-status-card"
              role="status"
              aria-live="polite"
            >
              <span class="live-progress-dot" />
              <span class="live-status-text">{{ liveInterimStatusText }}</span>
              <span
                v-if="liveElapsedText"
                class="thinking-elapsed"
              >
                {{ liveElapsedText }}
              </span>
            </div>

            <p
              v-if="showRetrievalHint"
              class="thinking-retrieval-hint"
            >
              {{ t('insight.copilot.thinkingRetrievalHint') }}
            </p>

            <div
              v-if="streamingContent || streamError"
              class="message-card assistant"
              :class="{ 'message-card--error': streamError }"
            >
              <div
                v-if="streamError"
                class="message-text message-text--error"
              >
                {{ streamError }}
              </div>
              <div
                v-else
                class="message-markdown live-markdown"
                :class="{ 'is-streaming': streaming }"
              >
                <CopilotStreamingMarkdown
                  :content="streamingContent || ''"
                  :streaming="streaming"
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <button
      v-if="!followsLatest"
      type="button"
      class="scroll-to-latest"
      :aria-label="t('insight.copilot.scrollToLatest')"
      :title="t('insight.copilot.scrollToLatest')"
      @click="scrollToBottom"
    >
      <ArrowDown
        :size="16"
        aria-hidden="true"
      />
      <span>{{ t('insight.copilot.scrollToLatest') }}</span>
    </button>
  </div>
</template>

<style scoped>
.chat-scroll-shell {
  position: relative;
}

.chat-scroll {
  background: var(--color-card-bg);
  overscroll-behavior: contain;
}

.scroll-to-latest {
  position: absolute;
  z-index: 2;
  bottom: 16px;
  left: 50%;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  padding: 7px 12px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-card-bg);
  box-shadow: var(--shadow-md);
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transform: translateX(-50%);
  transition: border-color 0.16s ease, color 0.16s ease, box-shadow 0.16s ease;
}

.scroll-to-latest:hover {
  border-color: color-mix(in srgb, var(--color-primary) 42%, var(--color-border));
  color: var(--color-primary);
  box-shadow: var(--shadow-lg);
}

.scroll-to-latest:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--color-primary) 55%, transparent);
  outline-offset: 2px;
}

.copilot-thread {
  width: 100%;
  max-width: none;
  margin: 0;
  /* Keep right padding; nudge content away from the session-list divider. */
  padding: 20px 28px 32px 48px;
  font-family: var(--font-sans);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.message-row {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  width: 100%;
  margin-bottom: 36px;
}

.message-row-user {
  flex-direction: row-reverse;
  justify-content: flex-start;
}

.message-row-assistant {
  flex-direction: row;
  justify-content: flex-start;
}

.message-body {
  min-width: 0;
}

.clarification-card {
  margin-top: 12px;
  padding: 14px;
  border: 1px solid color-mix(in srgb, var(--color-primary) 35%, var(--color-border));
  border-radius: 10px;
  background: color-mix(in srgb, var(--color-primary) 5%, var(--color-card-bg));
}
.clarification-card strong { color: var(--color-text-title); font-size: 13px; }
.clarification-card p { margin: 6px 0 10px; color: var(--color-text-secondary); font-size: 13px; line-height: 1.5; }
.clarification-input { width: 100%; min-height: 72px; resize: vertical; padding: 9px 10px; border: 1px solid var(--color-border); border-radius: 7px; background: var(--color-card-bg); color: var(--color-text-primary); font: inherit; font-size: 13px; }
.clarification-submit { margin-top: 9px; min-height: 32px; padding: 6px 12px; border: 0; border-radius: 6px; background: var(--color-primary); color: #fff; font-size: 13px; font-weight: 600; cursor: pointer; }
.clarification-submit:disabled { cursor: not-allowed; opacity: 0.55; }
.evidence-warning { margin-top: 12px; padding: 9px 11px; border-left: 3px solid var(--color-warning, #d97706); background: color-mix(in srgb, var(--color-warning, #d97706) 8%, transparent); color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
.thinking-outcome { margin-top: 8px; padding: 8px 10px; border-top: 1px solid var(--color-border); color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }

.message-row-assistant .message-body {
  flex: 1;
  max-width: min(920px, 88%);
}

.message-row-user .message-body {
  flex: 0 1 auto;
  width: fit-content;
  max-width: min(820px, 78%);
  text-align: right;
}

.message-card {
  min-width: 0;
}

.message-card.assistant {
  width: 100%;
}

.message-card.assistant:not(.message-card--welcome):not(.message-card--error) {
  padding: 0;
  border: none;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}

.message-card--error .message-text {
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--color-danger) 35%, transparent);
  background: color-mix(in srgb, var(--color-danger) 6%, transparent);
  color: var(--color-danger);
  text-align: left;
}

.message-card.assistant.message-card--welcome {
  box-sizing: border-box;
  width: fit-content;
  max-width: 100%;
  padding: 9px 13px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #ffffff;
  box-shadow: 0 1px 2px rgb(17 24 39 / 0.06);
  text-align: left;
  font-size: 15px;
  line-height: 24px;
}

.message-card--typing {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: #f3f4f6;
  border-color: #e5e7eb;
  color: #6b7280;
  font-size: 14px;
  border-radius: 16px;
  box-shadow: none;
}

.message-text {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 15px;
  line-height: 24px;
  font-weight: 400;
  letter-spacing: normal;
  color: var(--color-text-primary);
}

.message-row-user .message-card {
  box-sizing: border-box;
  display: inline-block;
  padding: 10px 14px;
  border: none;
  border-radius: 12px;
  background: #f3f4f6;
  box-shadow: none;
  text-align: left;
  font-size: 15px;
  line-height: 24px;
}

.message-row-user .message-text {
  text-align: left;
  color: #111827;
  font-size: 15px;
  line-height: 24px;
}

.message-row-user .message-markdown {
  text-align: left;
}

.message-row-user :deep(.copilot-message-attachments) {
  justify-content: flex-end;
}

.message-text--error {
  font-size: 14px;
  line-height: 1.6;
  text-align: left;
}

.message-markdown {
  min-width: 0;
  width: 100%;
  text-align: left;
}

.message-card :deep(.copilot-markdown) {
  font-family: inherit;
  font-size: 15px;
  line-height: 24px;
  font-weight: 400;
  letter-spacing: normal;
  color: #171512;
}

.message-card :deep(.copilot-markdown p) {
  margin-bottom: 10px;
}

.message-card :deep(.copilot-markdown p:last-child) {
  margin-bottom: 0;
}

.message-time {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.4;
  color: var(--color-text-tertiary);
}

.message-time.user {
  text-align: right;
}

.message-time.assistant {
  text-align: left;
}

.message-time-inline {
  margin-top: 0;
  margin-left: 4px;
}

.message-actions {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  margin-top: 10px;
}

.message-row-user .message-actions {
  justify-content: flex-end;
}

.message-actions-group {
  display: flex;
  align-items: center;
  gap: 4px;
}

.message-action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
  transition:
    background 0.15s ease,
    color 0.15s ease;
}

.message-action-btn:hover:not(:disabled) {
  background: #f3f4f6;
  color: #374151;
}

.message-action-btn.is-positive {
  background: #dcfce7;
  color: #15803d;
}

.message-action-btn.is-positive:hover:not(:disabled) {
  background: #bbf7d0;
  color: #166534;
}

.message-action-btn.is-negative {
  background: #fee2e2;
  color: #b91c1c;
}

.message-action-btn.is-negative:hover:not(:disabled) {
  background: #fecaca;
  color: #991b1b;
}

.message-action-btn.is-shared {
  background: rgb(34 197 94 / 10%);
  color: #15803d;
}

.message-action-btn.is-shared:hover:not(:disabled) {
  background: rgb(34 197 94 / 16%);
  color: #166534;
}

.message-action-btn:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--color-primary) 55%, transparent);
  outline-offset: 2px;
}

.message-action-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.thinking-panel {
  width: 100%;
  margin-bottom: 20px;
  overflow: hidden;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #ffffff;
  box-shadow: 0 1px 2px rgb(17 24 39 / 0.06);
  text-align: left;
  font-size: 12.5px;
  line-height: 1.45;
}

.thinking-panel-header {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
  font-size: 12.5px;
  line-height: 1.45;
}

.thinking-panel-header:hover {
  background: #f3f4f6;
}

.thinking-panel-header--static {
  cursor: default;
}

.thinking-panel-header--static:hover {
  background: transparent;
}

.thinking-panel-status {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  line-height: 1.45;
  color: var(--color-text-secondary);
}

.thinking-panel-title {
  flex: 0 0 auto;
  color: #4b5563;
  font-size: 12.5px;
  line-height: 1.45;
  font-weight: 600;
}

.thinking-panel-status-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.thinking-step-count {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--color-grey-3);
  font-size: 11px;
  color: var(--color-text-tertiary);
}

.thinking-panel-chevron {
  flex-shrink: 0;
  color: var(--color-text-tertiary);
}

.thinking-panel-body {
  max-height: 144px;
  overflow-y: auto;
  padding: 4px 12px 10px;
  border-top: 1px solid var(--color-border-light);
}

.thinking-step-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 2px 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-tertiary);
}

.thinking-step-bullet {
  flex-shrink: 0;
  color: var(--color-text-disabled);
}

.thinking-step-text {
  min-width: 0;
  flex: 1;
  word-break: break-word;
}

.thinking-retrieval-hint {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.45;
  color: var(--color-text-secondary);
}

.live-progress-row {
  margin-bottom: 8px;
}

.live-status-card {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding: 8px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #f9fafb;
  color: #4b5563;
  font-size: 14px;
  line-height: 1.45;
}

.live-status-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.thinking-elapsed {
  flex-shrink: 0;
  color: #9ca3af;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.live-progress-dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: var(--color-primary);
  animation: copilot-cursor-blink 1s steps(2, start) infinite;
}

.typing-label {
  font-size: 14px;
  color: var(--color-text-tertiary);
}

.typing-dots {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.typing-dots span {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: var(--color-text-disabled);
  animation: copilot-typing-dot 1.2s ease-in-out infinite;
}

.typing-dots span:nth-child(2) {
  animation-delay: 0.15s;
}

.typing-dots span:nth-child(3) {
  animation-delay: 0.3s;
}

.live-markdown.is-streaming :deep(.copilot-markdown > *:last-child)::after {
  content: '';
  display: inline-block;
  width: 2px;
  height: 16px;
  margin-left: 2px;
  vertical-align: middle;
  background: var(--color-primary);
  animation: copilot-cursor-blink 1s steps(2, start) infinite;
}

@keyframes copilot-cursor-blink {
  0%,
  45% {
    opacity: 1;
  }
  46%,
  100% {
    opacity: 0;
  }
}

@keyframes copilot-typing-dot {
  0%,
  80%,
  100% {
    opacity: 0.35;
    transform: translateY(0);
  }
  40% {
    opacity: 1;
    transform: translateY(-2px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .scroll-to-latest,
  .message-action-btn {
    transition: none;
  }

  .live-progress-dot,
  .typing-dots span,
  .live-markdown.is-streaming :deep(.copilot-markdown > *:last-child)::after {
    animation: none;
  }
}

@media (max-width: 768px) {
  .message-action-btn {
    width: 44px;
    height: 44px;
  }

  .scroll-to-latest {
    min-height: 44px;
  }

  .copilot-thread {
    padding: 16px 16px 28px 24px;
  }

  .message-row {
    gap: 12px;
    margin-bottom: 28px;
  }

  .message-row-assistant .message-body {
    max-width: calc(100% - 42px);
  }

  .message-row-user .message-body {
    max-width: 86%;
  }

  .message-text {
    font-size: 16px;
  }

  .message-card :deep(.copilot-markdown) {
    font-size: 16px;
  }

}
</style>
