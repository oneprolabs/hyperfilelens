<script setup lang="ts">
import { ArrowDown, ChevronDown, ChevronUp, Copy, RotateCcw, Share2, Sparkles } from 'lucide-vue-next'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { copyTextToClipboard } from '../../../lib/clipboard'
import { currentUser } from '../../../composables/useAuth'
import CopilotMarkdown from '../../../components/copilot/CopilotMarkdown.vue'
import CopilotStreamingMarkdown from '../../../components/copilot/CopilotStreamingMarkdown.vue'
import CopilotAttachmentList from './CopilotAttachmentList.vue'
import type { CopilotDisplayMessage } from './types'
import type { ThinkingStep } from '../../../composables/useLensRunStream'
import { formatThinkingStepLabel } from '../../../lib/copilotStreamLabels'
import type { LensChatThinkingStep } from '../../../lib/lensApi'

const props = defineProps<{
  sessionId: number
  messages: CopilotDisplayMessage[]
  streamingContent?: string
  streamingThinking?: ThinkingStep[]
  streaming?: boolean
  streamingElapsedSeconds?: number
  streamError?: string
  bubbleTag?: string
  selectedStarterKey?: string
  starterDisabled?: boolean
}>()

const emit = defineEmits<{
  starterChip: [key: string, text: string]
  retryQuestion: [text: string]
}>()

const { t } = useI18n()
const expandedThinking = ref<Set<string>>(new Set())
const liveThinkingOpen = ref(true)
const chatScrollRef = ref<HTMLElement | null>(null)
const copilotThreadRef = ref<HTMLElement | null>(null)
const followsLatest = ref(true)
let contentResizeObserver: ResizeObserver | null = null

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

const starterChips = [
  { key: 'chipQuerySops', icon: '📖' },
  { key: 'chipTrackDecisions', icon: '📋' },
  { key: 'chipRetrieveTemplates', icon: '📂' },
  { key: 'chipReviewContracts', icon: '📜' },
  { key: 'chipAnalyzeExpenses', icon: '💵' },
] as const

type StarterChipKey = (typeof starterChips)[number]['key']

function starterChipTitle(key: StarterChipKey) {
  return t(`insight.copilot.${key}Title`)
}

function starterChipPrompt(key: StarterChipKey) {
  return t(`insight.copilot.${key}Prompt`)
}

const userInitial = computed(() => {
  const user = currentUser.value
  const source =
    [user?.first_name, user?.last_name].filter(Boolean).join(' ').trim() ||
    user?.username?.trim() ||
    user?.email?.trim() ||
    ''
  const first = source.charAt(0)
  return first ? first.toUpperCase() : 'U'
})

function toggleThinking(id: string) {
  const next = new Set(expandedThinking.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedThinking.value = next
  if (followsLatest.value) scrollToBottom()
}

function toggleLiveThinking() {
  liveThinkingOpen.value = !liveThinkingOpen.value
  if (followsLatest.value) scrollToBottom()
}

function thinkingStepsFor(message: CopilotDisplayMessage) {
  return message.thinking?.steps ?? []
}

function stepLabel(step: ThinkingStep | LensChatThinkingStep) {
  if ('displayMessage' in step && step.displayMessage) {
    return step.displayMessage
  }
  return formatThinkingStepLabel({
    message: step.message || '',
    agentEvent: 'agent_event' in step ? step.agent_event : step.agentEvent,
    activity: step.activity,
  })
}

function liveThinkingStatus() {
  const seconds = props.streamingElapsedSeconds ?? 0
  const count = props.streamingThinking?.length ?? 0
  if (seconds > 0 && count > 0) {
    return t('insight.copilot.thinkingLiveProgress', { seconds, count })
  }
  if (seconds > 0) {
    return t('insight.copilot.thinkingLiveElapsed', { seconds })
  }
  return t('insight.copilot.thinkingLive')
}

const showRetrievalHint = computed(
  () =>
    Boolean(props.streaming) &&
    (props.streamingThinking?.length ?? 0) > 0 &&
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
  if (!question) return
  emit('retryQuestion', question)
}

function shareMessage() {
  ElMessage.info({ message: t('insight.copilot.shareComingSoon'), grouping: true })
}

const showLiveRow = computed(() => props.streaming)
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
          v-for="msg in messages"
          :key="msg.id"
          class="message-row"
          :class="msg.role === 'user' ? 'message-row-user' : 'message-row-assistant'"
        >
          <div
            class="message-avatar message-avatar-icon"
            :class="
              msg.role === 'user'
                ? 'message-avatar-icon--user'
                : 'message-avatar-icon--assistant'
            "
            :aria-label="
              msg.role === 'user' ? t('insight.copilot.roleUser') : t('insight.copilot.roleAi')
            "
          >
            <span
              v-if="msg.role === 'user'"
              class="message-avatar-initial"
            >{{ userInitial }}</span>
            <Sparkles
              v-else
              :size="16"
              :stroke-width="2"
            />
          </div>

          <div class="message-body">
            <div
              v-if="msg.role === 'assistant' && thinkingStepsFor(msg).length"
              class="thinking-panel thinking-panel-done"
            >
              <button
                type="button"
                class="thinking-panel-header"
                @click="toggleThinking(msg.id)"
              >
                <span class="thinking-panel-status">
                  {{
                    thinkingDuration(msg) != null
                      ? t('insight.copilot.thinkingDone', {
                        seconds: thinkingDuration(msg),
                        count: thinkingStepsFor(msg).length,
                      })
                      : t('insight.copilot.thinkingDoneSteps', {
                        count: thinkingStepsFor(msg).length,
                      })
                  }}
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
                <div
                  v-for="(step, idx) in thinkingStepsFor(msg)"
                  :key="idx"
                  class="thinking-step-item"
                >
                  <span class="thinking-step-bullet">▸</span>
                  <span class="thinking-step-text">{{ stepLabel(step) }}</span>
                </div>
              </div>
            </div>

            <div
              class="message-card"
              :class="[
                msg.role,
                msg.isError ? 'message-card--error' : '',
                msg.starterChips ? 'message-card--welcome' : '',
              ]"
            >
              <CopilotAttachmentList
                v-if="msg.attachments?.length"
                :session-id="sessionId"
                :attachments="msg.attachments"
              />
              <div
                v-if="msg.starterChips || msg.isError"
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
                v-if="msg.starterChips"
                class="copilot-chip-grid"
              >
                <button
                  v-for="chip in starterChips"
                  :key="chip.key"
                  type="button"
                  class="copilot-chip-box"
                  :class="{ 'is-selected': selectedStarterKey === chip.key }"
                  :aria-pressed="selectedStarterKey === chip.key"
                  :disabled="starterDisabled"
                  @click="emit('starterChip', chip.key, starterChipPrompt(chip.key))"
                >
                  <span class="copilot-chip-inner">
                    <span
                      class="copilot-chip-icon"
                      aria-hidden="true"
                    >{{ chip.icon }}</span>
                    <span class="copilot-chip-label">{{ starterChipTitle(chip.key) }}</span>
                  </span>
                </button>
              </div>
            </div>

            <div
              v-if="msg.createdAt"
              class="message-time"
              :class="msg.role"
            >
              {{ formatMessageTime(msg.createdAt) }}
            </div>

            <div
              v-if="msg.role === 'assistant' && msg.text && !msg.starterChips && !msg.isError"
              class="message-actions"
            >
              <button
                type="button"
                class="message-action-btn"
                :title="t('common.copy')"
                @click="copyText(msg.text || '')"
              >
                <Copy :size="16" />
              </button>
              <button
                type="button"
                class="message-action-btn"
                :title="t('insight.copilot.retryQuestion')"
                :disabled="!questionForMessage(msg)"
                @click="retryForMessage(msg)"
              >
                <RotateCcw :size="16" />
              </button>
              <button
                type="button"
                class="message-action-btn"
                :title="t('insight.copilot.shareAnswer')"
                @click="shareMessage()"
              >
                <Share2 :size="16" />
              </button>
            </div>
          </div>
        </div>

        <div
          v-if="showLiveRow"
          class="message-row message-row-assistant live-progress-row"
        >
          <div
            class="message-avatar message-avatar-icon message-avatar-icon--assistant"
            :aria-label="t('insight.copilot.roleAi')"
          >
            <Sparkles
              :size="16"
              :stroke-width="2"
            />
          </div>
          <div class="message-body">
            <div
              v-if="!streamError"
              class="thinking-panel thinking-panel-live"
            >
              <button
                v-if="streamingThinking?.length"
                type="button"
                class="thinking-panel-header"
                :aria-expanded="liveThinkingOpen"
                @click="toggleLiveThinking"
              >
                <span class="live-progress-dot" />
                <span class="thinking-panel-status">
                  <span class="thinking-panel-status-text">{{ liveThinkingStatus() }}</span>
                </span>
                <span
                  v-if="streamingThinking.length"
                  class="thinking-step-count"
                >
                  {{ streamingThinking.length }}
                </span>
                <ChevronUp
                  v-if="liveThinkingOpen"
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
                v-else
                class="thinking-panel-header thinking-panel-header--static"
                role="status"
                aria-live="polite"
              >
                <span class="live-progress-dot" />
                <span class="thinking-panel-status">
                  <span class="thinking-panel-status-text">{{ liveThinkingStatus() }}</span>
                </span>
              </div>
              <div
                v-if="liveThinkingOpen && streamingThinking?.length"
                class="thinking-panel-body"
              >
                <div
                  v-for="(step, idx) in streamingThinking"
                  :key="idx"
                  class="thinking-step-item"
                >
                  <span class="thinking-step-bullet">▸</span>
                  <span class="thinking-step-text">{{ step.displayMessage || stepLabel(step) }}</span>
                </div>
              </div>
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
  padding: 20px 28px 32px;
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

.message-avatar {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  font-size: 12px;
  font-weight: 600;
}

.message-avatar-icon {
  border-radius: 9px;
  border: 1px solid color-mix(in srgb, var(--color-primary) 16%, var(--color-border));
  background: var(--color-primary-light);
}

.message-avatar-icon--user,
.message-avatar-icon--assistant {
  color: var(--color-primary);
}

.message-avatar-initial {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  font-size: 13px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: 0;
}

.message-body {
  min-width: 0;
}

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

.message-card--error .message-text {
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--color-danger) 35%, transparent);
  background: color-mix(in srgb, var(--color-danger) 6%, transparent);
  color: var(--color-danger);
  text-align: left;
}

.message-card--welcome {
  text-align: left;
}

.message-card--typing {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 2px 0;
}

.message-text {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 15px;
  line-height: 1.65;
  font-weight: 400;
  letter-spacing: normal;
  color: var(--color-text-primary);
}

.message-row-user .message-text {
  text-align: right;
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
  text-align: left;
}

.message-card :deep(.copilot-markdown) {
  font-family: inherit;
  font-size: 15px;
  line-height: 1.65;
  font-weight: 400;
  letter-spacing: normal;
  color: var(--color-text-primary);
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

.message-actions {
  display: flex;
  gap: 4px;
  margin-top: 12px;
}

.message-row-user .message-actions {
  justify-content: flex-end;
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
  background: var(--color-grey-2);
  color: var(--color-text-secondary);
}

.message-action-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.copilot-chip-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
  margin-top: 14px;
  width: 100%;
}

.copilot-chip-box {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 44px;
  margin: 0;
  padding: 0 10px;
  border-radius: var(--radius-card);
  border: 1px solid var(--color-border);
  background: var(--color-card-bg);
  font-family: inherit;
  cursor: pointer;
  box-sizing: border-box;
}

.copilot-chip-inner {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  max-width: 100%;
  min-width: 0;
}

.copilot-chip-icon {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  font-size: 14px;
  line-height: 1;
}

.copilot-chip-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  color: var(--color-text-title);
}

.copilot-chip-box:hover {
  border-color: var(--color-primary);
  box-shadow: 0 1px 4px rgb(69 122 176 / 0.15);
}

.copilot-chip-box.is-selected {
  border-color: var(--color-primary);
  background: color-mix(in srgb, var(--color-primary) 9%, var(--color-card-bg));
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-primary) 12%, transparent);
}

.copilot-chip-box:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
}

.copilot-chip-box:disabled {
  cursor: not-allowed;
  opacity: 0.62;
  box-shadow: none;
}

.thinking-panel {
  width: 100%;
  margin-bottom: 8px;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-grey-1);
  text-align: left;
}

.thinking-panel-header {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.thinking-panel-header:hover {
  background: var(--color-grey-2);
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
  font-size: 13px;
  color: var(--color-text-secondary);
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

@media (max-width: 768px) {
  .scroll-to-latest {
    min-height: 44px;
  }

  .copilot-thread {
    padding: 16px 16px 28px;
  }

  .message-row {
    gap: 12px;
    margin-bottom: 28px;
  }

  .copilot-chip-grid {
    display: flex;
    gap: 8px;
    overflow-x: auto;
    padding-bottom: 2px;
  }

  .copilot-chip-box {
    flex: 0 0 148px;
  }
}
</style>
