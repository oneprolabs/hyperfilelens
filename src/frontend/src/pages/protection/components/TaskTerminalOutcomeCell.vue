<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { formatAppDateTime } from '../../../lib/dateTime'
import { normalizeTaskStatus, taskStatusTone } from '../../../lib/taskStatusDisplay'

type TaskOutcomeSource = {
  status?: string | null
  error_code?: string | null
  error_message?: string | null
  finished_at?: string | null
  started_at?: string | null
  created_at?: string | null
  recent_events?: Array<{ metadata?: unknown }>
}

type TerminalTaskStatus = 'success' | 'failed' | 'timeout' | 'partial' | 'cancelled'

const props = withDefaults(defineProps<{
  task?: TaskOutcomeSource | null
  fallback?: TaskOutcomeSource | null
}>(), {
  task: null,
  fallback: null,
})

const { t, te } = useI18n()
const terminalStatuses = new Set<TerminalTaskStatus>([
  'success',
  'failed',
  'timeout',
  'partial',
  'cancelled',
])
const diagnosticStatuses = new Set<TerminalTaskStatus>(['failed', 'timeout', 'partial'])

function terminalStatus(source?: TaskOutcomeSource | null): TerminalTaskStatus | '' {
  const raw = normalizeTaskStatus(source?.status)
  const normalized = raw === 'available' || raw === 'done' ? 'success' : raw
  return terminalStatuses.has(normalized as TerminalTaskStatus)
    ? normalized as TerminalTaskStatus
    : ''
}

function structuredFailureDetails(source?: TaskOutcomeSource | null) {
  for (const event of source?.recent_events || []) {
    const metadata = event?.metadata
    if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) continue
    const details = (metadata as Record<string, unknown>).failure_details
    if (details && typeof details === 'object' && !Array.isArray(details)) {
      return details as Record<string, unknown>
    }
  }
  return null
}

const outcome = computed(() => {
  const source = terminalStatus(props.task) ? props.task : props.fallback
  const status = terminalStatus(source)
  if (!source || !status) return null

  const diagnosticFallback = source === props.task ? props.fallback : null
  const code = String(source.error_code || diagnosticFallback?.error_code || '').trim()
  const reason = String(source.error_message || diagnosticFallback?.error_message || '').trim()
  const details = structuredFailureDetails(source) || structuredFailureDetails(diagnosticFallback)
  const detailCount = Number(details?.total_count ?? details?.count)
  const detailCategory = String(details?.category || '').trim()
  const detailKey = detailCategory ? `ops.task.failureDetails.summary.${detailCategory}` : ''
  const structuredReason = details && Number.isFinite(detailCount) && detailCount > 0 && te(detailKey)
    ? t(detailKey, { count: detailCount })
    : ''
  const displayReason = structuredReason || reason
  const showDiagnostic = diagnosticStatuses.has(status) && Boolean(code || displayReason)
  const diagnostic = showDiagnostic
    ? [code ? `[${code}]` : '', displayReason].filter(Boolean).join(' ')
    : ''
  const rawTimestamp = source.finished_at
    || source.started_at
    || source.created_at
    || diagnosticFallback?.finished_at
    || diagnosticFallback?.started_at
    || diagnosticFallback?.created_at
    || ''
  const timestamp = rawTimestamp ? formatAppDateTime(rawTimestamp, '').slice(0, 16) : ''

  return {
    status,
    tone: taskStatusTone(status),
    label: t(`ops.task.status.${status}`),
    timestamp,
    code,
    reason: displayReason,
    diagnostic,
    showDiagnostic,
  }
})
</script>

<template>
  <span
    v-if="outcome"
    class="task-terminal-outcome"
    :class="`task-terminal-outcome--${outcome.tone}`"
    :data-status="outcome.status"
  >
    <span class="task-terminal-outcome__summary">
      <span class="task-terminal-outcome__status">{{ outcome.label }}</span>
      <span
        v-if="outcome.timestamp"
        class="task-terminal-outcome__separator"
        aria-hidden="true"
      >·</span>
      <span
        v-if="outcome.timestamp"
        class="task-terminal-outcome__time"
      >{{ outcome.timestamp }}</span>
    </span>
    <span
      v-if="outcome.showDiagnostic"
      class="task-terminal-outcome__diagnostic"
      :aria-label="outcome.diagnostic"
      :data-table-overflow-title="outcome.diagnostic"
      :data-table-overflow-tone="['danger', 'warning'].includes(outcome.tone) ? outcome.tone : undefined"
    >
      <span
        v-if="outcome.code"
        class="task-terminal-outcome__code"
        :class="{ 'task-terminal-outcome__code--only': !outcome.reason }"
      >[{{ outcome.code }}]</span>
      <span
        v-if="outcome.reason"
        class="task-terminal-outcome__reason"
      >{{ outcome.reason }}</span>
    </span>
  </span>
  <span
    v-else
    class="task-terminal-outcome task-terminal-outcome--empty hfl-empty-mark"
  >—</span>
</template>

<style scoped>
.task-terminal-outcome {
  display: grid;
  min-width: 0;
  max-width: 100%;
  gap: 2px;
  color: var(--color-text-primary);
  font-size: 12px;
  font-weight: 400;
  line-height: 18px;
}

.task-terminal-outcome__summary,
.task-terminal-outcome__diagnostic {
  display: flex;
  min-width: 0;
  align-items: baseline;
  overflow: hidden;
  white-space: nowrap;
}

.task-terminal-outcome__summary {
  gap: 5px;
}

.task-terminal-outcome__status {
  flex: 0 0 auto;
  color: var(--task-outcome-tone, var(--color-text-secondary));
  font-weight: 600;
}

.task-terminal-outcome__separator,
.task-terminal-outcome__time {
  color: var(--color-text-secondary);
  font-weight: 400;
}

.task-terminal-outcome__time,
.task-terminal-outcome__reason {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.task-terminal-outcome__time {
  font-variant-numeric: tabular-nums;
}

.task-terminal-outcome__diagnostic {
  gap: 5px;
}

.task-terminal-outcome__code {
  flex: 0 1 auto;
  min-width: 0;
  max-width: 50%;
  overflow: hidden;
  color: var(--task-outcome-tone, var(--color-text-secondary));
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-weight: 500;
  text-overflow: ellipsis;
}

.task-terminal-outcome__code--only {
  max-width: 100%;
}

.task-terminal-outcome__reason {
  flex: 1 1 auto;
  color: var(--color-text-primary);
  font-weight: 400;
}

.task-terminal-outcome--danger .task-terminal-outcome__reason {
  color: var(--color-error-text);
}

.task-terminal-outcome--warning .task-terminal-outcome__reason {
  color: var(--color-warning-text);
}

.task-terminal-outcome--success { --task-outcome-tone: var(--color-success-text); }
.task-terminal-outcome--danger { --task-outcome-tone: var(--color-error-text); }
.task-terminal-outcome--warning { --task-outcome-tone: var(--color-warning-text); }
.task-terminal-outcome--neutral { --task-outcome-tone: var(--color-text-secondary); }

.task-terminal-outcome--empty {
  color: var(--color-text-secondary);
}
</style>
