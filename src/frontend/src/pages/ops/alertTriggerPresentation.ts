type Rule = Record<string, unknown>
type Translate = (key: string, values?: Record<string, string | number>) => string
type LabelFn = (value?: string | null) => string

export type TriggerSummaryParts = {
  primary: string
  secondary?: string
}

const DURATION_KEYS: Record<number, string> = {
  60: 'duration1m',
  180: 'duration3m',
  300: 'duration5m',
  600: 'duration10m',
  900: 'duration15m',
  1800: 'duration30m',
}

function durationLabel(seconds: unknown, t: Translate) {
  const value = Number(seconds)
  const key = DURATION_KEYS[value]
  if (key) return t(`ops.alertsCenter.editor.${key}`)
  if (!Number.isFinite(value) || value <= 0) return ''
  if (value % 3600 === 0) {
    const hours = value / 3600
    return hours === 1
      ? t('ops.alertsCenter.editor.durationHour')
      : t('ops.alertsCenter.editor.durationHours', { n: hours })
  }
  if (value % 60 === 0) {
    const minutes = value / 60
    return minutes === 1
      ? t('ops.alertsCenter.editor.durationMinute')
      : t('ops.alertsCenter.editor.durationMinutes', { n: minutes })
  }
  return t('ops.alertsCenter.editor.durationSecondsCount', { n: value })
}

function eventTypesPrimary(typeLabels: string[]) {
  if (!typeLabels.length) return '—'
  if (typeLabels.length === 1) return typeLabels[0]
  return `${typeLabels[0]} +${typeLabels.length - 1}`
}

export function triggerSummaryParts(
  type: string,
  trigger: Rule,
  t: Translate,
  labels: {
    metricLabel: LabelFn
    healthCheckLabel: LabelFn
    taskTypeLabel: LabelFn
    taskEventLabel: LabelFn
    eventCategoryLabel: LabelFn
    eventTypeLabel: LabelFn
  },
): TriggerSummaryParts {
  const rule = trigger || {}
  if (type === 'metric') {
    const metric = labels.metricLabel(String(rule.metric_key || ''))
    const unit = String(rule.unit || '')
    const duration = durationLabel(rule.duration_seconds, t)
    return {
      primary: [metric, rule.operator || '>=', `${rule.threshold ?? '—'}${unit}`]
        .filter(Boolean)
        .join(' '),
      secondary: duration || undefined,
    }
  }
  if (type === 'task') {
    return {
      primary: labels.taskEventLabel(String(rule.event_type || '')),
      secondary: labels.taskTypeLabel(String(rule.task_type || '')),
    }
  }
  if (type === 'health' || type === 'availability') {
    const duration = durationLabel(rule.duration_seconds, t)
    return {
      primary: labels.healthCheckLabel(String(rule.check_type || 'heartbeat')),
      secondary: duration || undefined,
    }
  }
  if (type === 'event') {
    const typeLabels = Array.isArray(rule.event_types)
      ? rule.event_types.map((item) => labels.eventTypeLabel(String(item)))
      : []
    const category = labels.eventCategoryLabel(String(rule.event_category || ''))
    return {
      primary: eventTypesPrimary(typeLabels),
      secondary: category || undefined,
    }
  }
  return { primary: '—' }
}

export function triggerSummary(
  type: string,
  trigger: Rule,
  t: Translate,
  labels: {
    metricLabel: LabelFn
    healthCheckLabel: LabelFn
    taskTypeLabel: LabelFn
    taskEventLabel: LabelFn
    eventCategoryLabel: LabelFn
    eventTypeLabel: LabelFn
  },
) {
  const parts = triggerSummaryParts(type, trigger, t, labels)
  return parts.secondary ? `${parts.primary} · ${parts.secondary}` : parts.primary
}

export type TriggerDetailRow = {
  label: string
  value: string
}

export function triggerDetailRows(
  type: string,
  trigger: Rule,
  t: Translate,
  labels: {
    metricLabel: LabelFn
    healthCheckLabel: LabelFn
    taskTypeLabel: LabelFn
    taskEventLabel: LabelFn
    eventCategoryLabel: LabelFn
    eventTypeLabel: LabelFn
  },
): TriggerDetailRow[] {
  const rule = trigger || {}
  const field = (name: string) => t(`ops.alertsCenter.editor.${name}`)
  if (type === 'metric') {
    const unit = String(rule.unit || '')
    const duration = durationLabel(rule.duration_seconds, t)
    const rows: TriggerDetailRow[] = [
      { label: field('fieldMetric'), value: labels.metricLabel(String(rule.metric_key || '')) || '—' },
      { label: field('fieldOperator'), value: String(rule.operator || '>=') },
      { label: field('fieldThreshold'), value: `${rule.threshold ?? '—'}${unit}` },
    ]
    if (duration) rows.push({ label: field('fieldDuration'), value: duration })
    return rows
  }
  if (type === 'task') {
    return [
      { label: field('taskType'), value: labels.taskTypeLabel(String(rule.task_type || '')) || '—' },
      { label: field('eventType'), value: labels.taskEventLabel(String(rule.event_type || '')) || '—' },
    ]
  }
  if (type === 'health' || type === 'availability') {
    const duration = durationLabel(rule.duration_seconds, t)
    const rows: TriggerDetailRow[] = [
      {
        label: field('checkType'),
        value: labels.healthCheckLabel(String(rule.check_type || 'heartbeat')) || '—',
      },
    ]
    if (duration) rows.push({ label: field('fieldDuration'), value: duration })
    return rows
  }
  if (type === 'event') {
    const types = Array.isArray(rule.event_types)
      ? rule.event_types.map((item) => labels.eventTypeLabel(String(item))).filter(Boolean).join(', ')
      : ''
    return [
      {
        label: field('eventCategory'),
        value: labels.eventCategoryLabel(String(rule.event_category || '')) || '—',
      },
      { label: field('eventTypes'), value: types || '—' },
    ]
  }
  return []
}
