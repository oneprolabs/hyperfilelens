<script setup lang="ts">
import { computed } from 'vue'
import type { Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { CheckCircle, Circle, Clock, Loader2, Search, Sparkles, Wrench, XCircle } from 'lucide-vue-next'
import { formatThinkingStepLabel } from '../../../lib/copilotStreamLabels'
import type { ThinkingStep } from '../../../composables/useLensRunStream'
import type { LensChatThinkingStep } from '../../../lib/lensApi'

export type TimelineStep = ThinkingStep | LensChatThinkingStep

const props = defineProps<{
  steps: TimelineStep[]
}>()

const { t } = useI18n()

function stepLabel(step: TimelineStep): string {
  if ('displayMessage' in step && step.displayMessage) {
    return step.displayMessage
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
  const event = ('agent_event' in step ? step.agent_event : step.agentEvent) || step.activity || ''
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
  if (step.tokens || step.inputTokens || step.outputTokens) {
    const parts: string[] = []
    if (step.inputTokens != null) parts.push(`${step.inputTokens} in`)
    if (step.outputTokens != null) parts.push(`${step.outputTokens} out`)
    if (step.tokens != null && parts.length === 0) parts.push(`${step.tokens}`)
    lines.push(t('insight.copilot.agentActivityTokens', { tokens: parts.join(' / ') }))
  }
  if (step.durationMs != null && step.durationMs > 0) {
    lines.push(t('insight.copilot.agentActivityDuration', { ms: step.durationMs }))
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
</script>

<template>
  <div class="copilot-timeline">
    <div
      v-for="(item, idx) in items"
      :key="idx"
      class="copilot-timeline-item"
    >
      <div class="copilot-timeline-track">
        <div
          class="copilot-timeline-dot"
          :class="item.style.dotClass"
        >
          <component
            :is="item.style.icon"
            :size="12"
            :stroke-width="2.5"
          />
        </div>
        <div
          v-if="idx < items.length - 1"
          class="copilot-timeline-line"
        />
      </div>
      <div class="copilot-timeline-content">
        <div class="copilot-timeline-header">
          <span class="copilot-timeline-title">{{ item.title }}</span>
          <span
            v-if="item.time"
            class="copilot-timeline-time"
          >{{ item.time }}</span>
        </div>
        <div
          v-if="item.details.length"
          class="copilot-timeline-details"
        >
          <div
            v-for="(line, dIdx) in item.details"
            :key="dIdx"
            class="copilot-timeline-detail-line"
          >
            {{ line }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.copilot-timeline {
  padding: 8px 0;
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
