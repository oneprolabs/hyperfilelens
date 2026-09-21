<script setup lang="ts">
import { computed } from 'vue'
import type { Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { CheckCircle, Circle, Clock, Loader2, Search, Sparkles, Wrench, XCircle } from 'lucide-vue-next'
import { formatThinkingStepLabel } from '../../../lib/copilotStreamLabels'
import type { ThinkingStep } from '../../../composables/useLensRunStream'
import type { LensChatThinkingStep } from '../../../lib/lensApi'
import { summarizeThinkingSteps } from './copilotThinkingActivities'

export type TimelineStep = ThinkingStep | LensChatThinkingStep

const props = defineProps<{
  steps: TimelineStep[]
  live?: boolean
}>()

const { t } = useI18n()

function stepLabel(step: TimelineStep): string {
  if ('displayMessage' in step && step.displayMessage) {
    return step.displayMessage
  }
  const eventType = 'event_type' in step ? step.event_type : ('eventType' in step ? step.eventType : '')
  if (eventType) {
    const event = eventType.replace(/[._-]+/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
    return step.message || step.summary || event
  }
  return formatThinkingStepLabel({
    message: step.message || '',
    agentEvent: 'agent_event' in step ? step.agent_event : step.agentEvent,
    activity: step.activity,
  })
}

function extractTimestamp(message?: string): { date?: string; time?: string } {
  if (!message) return {}
  const match = message.match(/^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]/)
  if (!match) return {}
  const full = match[1]
  const time = full.split(' ')[1]
  return { date: full.split(' ')[0], time }
}

function eventCategory(step: TimelineStep): string {
  const event = ('event_type' in step ? step.event_type : ('eventType' in step ? step.eventType : ''))
    || ('agent_event' in step ? step.agent_event : step.agentEvent)
    || step.activity || ''
  return String(event).toLowerCase()
}

type TimelineStyle = {
  icon: Component
  dotClass: string
}

const STYLES: Record<string, TimelineStyle> = {
  invoke: { icon: Sparkles, dotClass: 'copilot-timeline-dot--primary' },
  plan: { icon: Loader2, dotClass: 'copilot-timeline-dot--info' },
  observe: { icon: CheckCircle, dotClass: 'copilot-timeline-dot--success' },
  retry: { icon: Clock, dotClass: 'copilot-timeline-dot--warning' },
  retrieval: { icon: Search, dotClass: 'copilot-timeline-dot--accent' },
  answer: { icon: CheckCircle, dotClass: 'copilot-timeline-dot--success' },
  tool: { icon: Wrench, dotClass: 'copilot-timeline-dot--muted' },
}

function timelineStyle(step: TimelineStep): TimelineStyle {
  if (step.error) {
    return { icon: XCircle, dotClass: 'copilot-timeline-dot--danger' }
  }
  const category = eventCategory(step)
  for (const key of Object.keys(STYLES)) {
    if (category.includes(key)) return STYLES[key]
  }
  return { icon: Circle, dotClass: 'copilot-timeline-dot--muted' }
}

function detailLines(step: TimelineStep): string[] {
  const lines: string[] = []
  if (step.error) {
    lines.push(step.error)
    return lines
  }
  const summary = step.summary || step.plan
  if (summary) lines.push(summary)
  if (step.query) lines.push(t('insight.copilot.agentActivityQuery', { query: step.query }))
  if (step.path) lines.push(t('insight.copilot.agentActivityPath', { path: step.path }))
  if (step.toolName) lines.push(t('insight.copilot.agentActivityTool', { tool: step.toolName }))
  const inputTokens = 'input_tokens' in step ? step.input_tokens : step.inputTokens
  const outputTokens = 'output_tokens' in step ? step.output_tokens : step.outputTokens
  const tokens = 'tokens' in step ? step.tokens : undefined
  if (tokens || inputTokens || outputTokens) {
    const parts: string[] = []
    if (inputTokens != null) parts.push(`${inputTokens} in`)
    if (outputTokens != null) parts.push(`${outputTokens} out`)
    if (tokens != null && parts.length === 0) parts.push(`${tokens}`)
    lines.push(t('insight.copilot.agentActivityTokens', { tokens: parts.join(' / ') }))
  }
  const durationMs = 'duration_ms' in step ? step.duration_ms : step.durationMs
  if (durationMs != null && durationMs > 0) {
    lines.push(t('insight.copilot.agentActivityDuration', { ms: durationMs }))
  }
  const assistantName = 'assistant_name' in step ? step.assistant_name : ('assistantName' in step ? step.assistantName : '')
  const delegatedTask = 'delegated_task' in step ? step.delegated_task : ('delegatedTask' in step ? step.delegatedTask : '')
  if (assistantName) {
    lines.push(`${assistantName}${delegatedTask ? `: ${delegatedTask}` : ''}`)
  }
  const payload = 'payload' in step ? step.payload : undefined
  if (payload) {
    const summary = payload.summary || payload.message || payload.description
    if (typeof summary === 'string' && summary.trim()) lines.push(summary)
  }
  return lines
}

const items = computed(() =>
  props.steps.map((step) => {
    const ts = extractTimestamp(step.message)
    return {
      step,
      title: stepLabel(step),
      time: ts.time,
      date: ts.date,
      style: timelineStyle(step),
      details: detailLines(step),
    }
  }),
)

type RuntimeStatus = 'pending' | 'in_progress' | 'completed' | 'failed' | 'skipped'
type RuntimeCardItem = { id: string; title: string; detail: string; status: RuntimeStatus; parentId?: string }
type RuntimeCardRow = RuntimeCardItem & { depth: number }

function payloadId(payload: Record<string, unknown>, fallback: string) {
  return String(payload.id || payload.uuid || payload.step_id || payload.task_id || fallback)
}

function payloadParentId(payload: Record<string, unknown>) {
  const parent = payload.parent_id ?? payload.parentId ?? payload.parent_uuid ?? payload.parentUuid
  return parent == null || parent === '' ? undefined : String(parent)
}

function runtimeRows(items: RuntimeCardItem[]): RuntimeCardRow[] {
  const byParent = new Map<string | undefined, RuntimeCardItem[]>()
  for (const item of items) {
    const parent = item.parentId && items.some((candidate) => candidate.id === item.parentId)
      ? item.parentId
      : undefined
    const bucket = byParent.get(parent) || []
    bucket.push(item)
    byParent.set(parent, bucket)
  }
  const rows: RuntimeCardRow[] = []
  const visit = (item: RuntimeCardItem, depth: number, path: Set<string>) => {
    if (path.has(item.id)) return
    rows.push({ ...item, depth })
    const nextPath = new Set(path).add(item.id)
    for (const child of byParent.get(item.id) || []) visit(child, depth + 1, nextPath)
  }
  for (const item of byParent.get(undefined) || []) visit(item, 0, new Set())
  return rows
}

const runtimeCard = computed(() => {
  const plan: RuntimeCardItem[] = []
  const stages: RuntimeCardItem[] = []
  const activities: RuntimeCardItem[] = []
  const delegated: RuntimeCardItem[] = []
  const outcomes: string[] = []
  const upsert = (target: RuntimeCardItem[], item: RuntimeCardItem) => {
    const index = target.findIndex((existing) => existing.id === item.id)
    if (index < 0) target.push(item)
    else target[index] = { ...target[index], ...item }
  }
  for (const item of items.value) {
    const category = eventCategory(item.step)
    const detail = item.details[0] || ''
    const status = normalizeRuntimeStatus(item.step.status) || (item.step.error
      ? 'failed'
      : /start|running|progress|plan/i.test(category) ? 'in_progress' : 'completed')
    const payload = item.step.payload || {}
    const stepParentId = 'parent_id' in item.step
      ? item.step.parent_id
      : ('parentId' in item.step ? item.step.parentId : undefined)
    const isEvent = (name: string) => category === name || category.endsWith(`.${name}`)
    const planItems = payload.steps || payload.items || payload.tasks
    if (isEvent('plan.updated') && Array.isArray(planItems)) {
      for (const [index, raw] of planItems.entries()) {
        if (!raw || typeof raw !== 'object') continue
        const step = raw as Record<string, unknown>
        const title = String(step.title || '').trim()
        if (!title) continue
        upsert(plan, {
          id: payloadId(step, `plan-${index + 1}`),
          parentId: payloadParentId(step),
          title,
          detail: String(step.summary || step.description || ''),
          status: normalizeRuntimeStatus(step.status) || 'pending',
        })
      }
    } else if (isEvent('stage.updated') || isEvent('stage.started') || isEvent('stage.completed')) {
      const title = String(payload.title || payload.name || item.title).trim()
      if (title) upsert(stages, {
        id: payloadId(payload, title),
        parentId: payloadParentId(payload),
        title,
        detail: String(payload.summary || payload.message || payload.description || ''),
        status: normalizeRuntimeStatus(payload.status) || status,
      })
    } else if (isEvent('activity.recorded') || isEvent('activity.started') || isEvent('activity.completed')) {
      const title = String(payload.kind || payload.title || payload.name || item.title).trim()
      if (title) upsert(activities, {
        id: payloadId(payload, `${title}-${item.time || items.value.indexOf(item)}`),
        parentId: payloadParentId(payload),
        title,
        detail: String(payload.summary || payload.message || payload.description || detail),
        status: normalizeRuntimeStatus(payload.status) || status,
      })
    } else if (item.step.plan || /plan|workflow|task/i.test(category)) {
      upsert(plan, {
        id: `${item.title}-${item.time || items.value.indexOf(item)}`,
        parentId: stepParentId || payloadParentId(payload),
        title: item.title,
        detail,
        status,
      })
    }
    const assistantName = 'assistant_name' in item.step
      ? item.step.assistant_name
      : item.step.assistantName
    const delegatedTask = 'delegated_task' in item.step
      ? item.step.delegated_task
      : item.step.delegatedTask || String(payload.task || payload.description || '')
    if (assistantName || delegatedTask) {
      upsert(delegated, {
        id: String(item.step.id || payload.id || `${assistantName}-${delegatedTask}`),
        parentId: stepParentId || payloadParentId(payload),
        title: assistantName || t('insight.copilot.agentActivitiesLive'),
        detail: delegatedTask || detail,
        status,
      })
    }
    if (item.step.error || item.step.outcome || payload.outcome || /outcome|answer|complete|failed/i.test(category)) {
      const outcome = item.step.error || item.step.outcome || String(payload.outcome || '') || detail || item.title
      if (outcome && !outcomes.includes(outcome)) outcomes.push(outcome)
    }
  }
  return {
    plan,
    stages,
    activities,
    delegated,
    outcomes,
    rows: {
      plan: runtimeRows(plan),
      stages: runtimeRows(stages),
      activities: runtimeRows(activities),
      delegated: runtimeRows(delegated),
    },
    visible: Boolean(plan.length || stages.length || activities.length || delegated.length || outcomes.length),
  }
})

const activityItems = computed(() =>
  summarizeThinkingSteps(props.steps),
)

function normalizeRuntimeStatus(value: unknown): RuntimeStatus | null {
  const status = String(value || '').toLowerCase().replace(/[-\s]+/g, '_')
  if (['pending', 'queued', 'waiting'].includes(status)) return 'pending'
  if (['in_progress', 'inprogress', 'running', 'started', 'active'].includes(status)) return 'in_progress'
  if (['completed', 'complete', 'success', 'succeeded', 'done'].includes(status)) return 'completed'
  if (['failed', 'failure', 'error'].includes(status)) return 'failed'
  if (['skipped', 'cancelled', 'canceled'].includes(status)) return 'skipped'
  return null
}

function runtimeStatusGlyph(status: RuntimeStatus): string {
  if (status === 'failed') return '!'
  if (status === 'in_progress') return '…'
  if (status === 'pending') return '·'
  if (status === 'skipped') return '–'
  return '✓'
}
</script>

<template>
  <div class="copilot-timeline">
    <section
      v-if="runtimeCard.visible"
      class="copilot-runtime-card"
      :aria-label="t('insight.copilot.runtimeCard')"
    >
      <div class="copilot-runtime-card__header">
        <span class="copilot-runtime-card__title">{{ t('insight.copilot.runtimeCard') }}</span>
        <span class="copilot-runtime-card__hint">{{ t('insight.copilot.runtimeCardHint') }}</span>
      </div>
      <div
        v-if="runtimeCard.plan.length"
        class="copilot-runtime-card__section"
      >
        <div class="copilot-runtime-card__section-title">
          {{ t('insight.copilot.runtimePlan') }}
        </div>
        <div
          v-for="item in runtimeCard.rows.plan"
          :key="`plan-${item.id}`"
          class="copilot-runtime-card__row"
          :style="{ '--runtime-depth': item.depth }"
        >
          <span
            class="copilot-runtime-card__status"
            :class="`is-${item.status}`"
            aria-hidden="true"
          >{{ runtimeStatusGlyph(item.status) }}</span>
          <span class="copilot-runtime-card__content"><strong>{{ item.title }}</strong><small v-if="item.detail">{{ item.detail }}</small></span>
        </div>
      </div>
      <div
        v-if="runtimeCard.stages.length"
        class="copilot-runtime-card__section"
      >
        <div class="copilot-runtime-card__section-title">
          {{ t('insight.copilot.runtimeStages') }}
        </div>
        <div
          v-for="item in runtimeCard.rows.stages"
          :key="`stage-${item.id}`"
          class="copilot-runtime-card__row"
          :style="{ '--runtime-depth': item.depth }"
        >
          <span
            class="copilot-runtime-card__status"
            :class="`is-${item.status}`"
            aria-hidden="true"
          >{{ runtimeStatusGlyph(item.status) }}</span>
          <span class="copilot-runtime-card__content"><strong>{{ item.title }}</strong><small v-if="item.detail">{{ item.detail }}</small></span>
        </div>
      </div>
      <div
        v-if="runtimeCard.activities.length"
        class="copilot-runtime-card__section"
      >
        <div class="copilot-runtime-card__section-title">
          {{ t('insight.copilot.runtimeActivities') }}
        </div>
        <div
          v-for="item in runtimeCard.rows.activities"
          :key="`activity-${item.id}`"
          class="copilot-runtime-card__row"
          :style="{ '--runtime-depth': item.depth }"
        >
          <span
            class="copilot-runtime-card__status"
            :class="`is-${item.status}`"
            aria-hidden="true"
          >{{ runtimeStatusGlyph(item.status) }}</span>
          <span class="copilot-runtime-card__content"><strong>{{ item.title }}</strong><small v-if="item.detail">{{ item.detail }}</small></span>
        </div>
      </div>
      <div
        v-if="runtimeCard.delegated.length"
        class="copilot-runtime-card__section"
      >
        <div class="copilot-runtime-card__section-title">
          {{ t('insight.copilot.runtimeDelegatedTasks') }}
        </div>
        <div
          v-for="item in runtimeCard.rows.delegated"
          :key="`task-${item.id}`"
          class="copilot-runtime-card__row"
          :style="{ '--runtime-depth': item.depth }"
        >
          <span
            class="copilot-runtime-card__status"
            :class="`is-${item.status}`"
            aria-hidden="true"
          >{{ runtimeStatusGlyph(item.status) }}</span>
          <span class="copilot-runtime-card__content"><strong>{{ item.title }}</strong><small v-if="item.detail">{{ item.detail }}</small></span>
        </div>
      </div>
      <div
        v-if="runtimeCard.outcomes.length"
        class="copilot-runtime-card__section copilot-runtime-card__outcome"
      >
        <div class="copilot-runtime-card__section-title">
          {{ t('insight.copilot.runtimeOutcome') }}
        </div>
        <p
          v-for="outcome in runtimeCard.outcomes"
          :key="outcome"
        >
          {{ outcome }}
        </p>
      </div>
    </section>
    <details
      v-if="activityItems.length"
      :open="props.live"
      class="copilot-activity-group"
    >
      <summary class="copilot-activity-group-header">
        <span
          class="copilot-activity-group-status"
          :class="{ 'is-live': props.live }"
          aria-hidden="true"
        >
          <Loader2
            v-if="props.live"
            :size="12"
            class="copilot-activity-spinner"
          />
          <span v-else>✓</span>
        </span>
        <span>{{ props.live ? t('insight.copilot.agentActivityStatusRunning') : t('insight.copilot.agentActivityStatusCompleted') }}</span>
        <span
          class="copilot-activity-group-chevron"
          aria-hidden="true"
        >⌄</span>
      </summary>
      <div class="copilot-activity-list">
        <div
          v-for="activity in activityItems"
          :key="activity.id"
          class="copilot-activity-item"
        >
          <span
            class="copilot-activity-status"
            :class="`is-${activity.status}`"
            aria-hidden="true"
          >{{ activity.status === 'failed' ? '!' : activity.status === 'in_progress' ? '…' : '✓' }}</span>
          <div class="copilot-activity-content">
            <div class="copilot-activity-title">
              <span>{{ activity.titleKey ? t(activity.titleKey) : activity.title }}</span>
              <span
                v-if="activity.count > 1"
                class="copilot-activity-count"
              >×{{ activity.count }}</span>
            </div>
            <div
              v-if="activity.details.length"
              class="copilot-activity-details"
            >
              {{ activity.details[0] }}
            </div>
          </div>
        </div>
      </div>
    </details>
  </div>
</template>

<style scoped>
.copilot-timeline {
  padding: 8px 0;
}

.copilot-runtime-card { margin-bottom: 12px; overflow: hidden; border: 1px solid var(--el-border-color-lighter); border-radius: 10px; background: var(--el-bg-color); }
.copilot-runtime-card__header { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; padding: 10px 12px; border-bottom: 1px solid var(--el-border-color-lighter); }
.copilot-runtime-card__title { color: var(--el-text-color-primary); font-size: 13px; font-weight: 600; }
.copilot-runtime-card__hint { color: var(--el-text-color-secondary); font-size: 11px; }
.copilot-runtime-card__section { padding: 10px 12px; }
.copilot-runtime-card__section + .copilot-runtime-card__section { border-top: 1px solid var(--el-border-color-lighter); }
.copilot-runtime-card__section-title { margin-bottom: 7px; color: var(--el-text-color-secondary); font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
.copilot-runtime-card__row { display: flex; align-items: flex-start; gap: 8px; padding: 4px 0; padding-left: calc(var(--runtime-depth, 0) * 18px); }
.copilot-runtime-card__status { display: grid; width: 18px; height: 18px; flex: 0 0 auto; place-items: center; border-radius: 50%; background: var(--el-color-success); color: #fff; font-size: 11px; font-weight: 700; }
.copilot-runtime-card__status.is-pending, .copilot-runtime-card__status.is-skipped { background: var(--el-text-color-placeholder); }
.copilot-runtime-card__status.is-in_progress { background: var(--el-color-primary); }
.copilot-runtime-card__status.is-failed { background: var(--el-color-danger); }
.copilot-runtime-card__content { min-width: 0; display: flex; flex-direction: column; gap: 2px; color: var(--el-text-color-primary); font-size: 12px; }
.copilot-runtime-card__content small { color: var(--el-text-color-secondary); font-size: 11px; overflow-wrap: anywhere; }
.copilot-runtime-card__outcome p { margin: 0; color: var(--el-text-color-regular); font-size: 12px; line-height: 1.5; }
.copilot-runtime-card__outcome p + p { margin-top: 4px; }

.copilot-activity-group { overflow: hidden; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; background: var(--el-bg-color); }
.copilot-activity-group-header { cursor: pointer; list-style: none; }
.copilot-activity-group-header::-webkit-details-marker { display: none; }
.copilot-activity-group-header { display: flex; align-items: center; gap: 8px; min-height: 34px; padding: 7px 9px; color: var(--el-text-color-secondary); font-size: 12px; }
.copilot-activity-group-status { display: grid; width: 16px; height: 16px; flex: 0 0 auto; place-items: center; color: var(--el-color-success); font-size: 12px; font-weight: 700; }
.copilot-activity-group-status.is-live { color: var(--el-color-primary); }
.copilot-activity-spinner { animation: copilot-activity-spin 1s linear infinite; }
.copilot-activity-group-chevron { margin-left: auto; color: var(--el-text-color-placeholder); font-size: 14px; transition: transform 0.16s ease; }
.copilot-activity-group[open] > .copilot-activity-group-header .copilot-activity-group-chevron { transform: rotate(180deg); }
.copilot-activity-list { display: grid; gap: 2px; padding: 0 9px 8px 33px; }
.copilot-activity-item { display: flex; align-items: flex-start; gap: 6px; padding: 1px 0; }
.copilot-activity-status { display: grid; width: 14px; height: 14px; flex: 0 0 auto; place-items: center; color: var(--el-text-color-secondary); font-size: 11px; font-weight: 700; }
.copilot-activity-status.is-in_progress { color: var(--el-color-primary); }
.copilot-activity-status.is-failed { color: var(--el-color-danger); }
.copilot-activity-content { min-width: 0; color: var(--el-text-color-secondary); font-size: 11px; line-height: 1.35; }
.copilot-activity-title { display: flex; align-items: baseline; gap: 6px; }
.copilot-activity-count { color: var(--el-text-color-secondary); font-size: 11px; }
.copilot-activity-details { margin-top: 2px; color: var(--el-text-color-secondary); font-size: 11px; overflow-wrap: anywhere; }

@keyframes copilot-activity-spin {
  to { transform: rotate(360deg); }
}

.copilot-timeline-item {
  display: flex;
  gap: 10px;
  padding: 4px 0;
}

.copilot-timeline-track {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 20px;
  flex-shrink: 0;
}

.copilot-timeline-dot {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  flex-shrink: 0;
}

.copilot-timeline-dot--primary {
  background-color: var(--el-color-primary);
}

.copilot-timeline-dot--info {
  background-color: var(--el-color-info);
}

.copilot-timeline-dot--success {
  background-color: var(--el-color-success);
}

.copilot-timeline-dot--warning {
  background-color: var(--el-color-warning);
}

.copilot-timeline-dot--danger {
  background-color: var(--el-color-danger);
}

.copilot-timeline-dot--accent {
  background-color: #0ea5e9;
}

.copilot-timeline-dot--muted {
  background-color: var(--el-text-color-placeholder);
}

.copilot-timeline-line {
  width: 2px;
  flex: 1;
  min-height: 12px;
  background-color: var(--el-border-color-lighter);
  margin: 2px 0;
}

.copilot-timeline-content {
  flex: 1;
  min-width: 0;
  padding-bottom: 8px;
}

.copilot-timeline-header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.copilot-timeline-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  flex: 1;
}

.copilot-timeline-time {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.copilot-timeline-details {
  margin-top: 4px;
  padding: 6px 8px;
  border-radius: 6px;
  background-color: var(--el-fill-color-lighter);
}

.copilot-timeline-detail-line {
  font-size: 12px;
  color: var(--el-text-color-regular);
  line-height: 1.5;
  word-break: break-word;
}

.copilot-timeline-detail-line + .copilot-timeline-detail-line {
  margin-top: 4px;
}
</style>
