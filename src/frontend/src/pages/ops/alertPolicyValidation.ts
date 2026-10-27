export type TriggerValidationKind = 'string' | 'number' | 'array'

export type TriggerValidationSpec = {
  path: `triggerRule.${string}`
  field: string
  labelKey: string
  kind: TriggerValidationKind
}

export const triggerValidationSpecs: Record<string, TriggerValidationSpec[]> = {
  metric: [
    { path: 'triggerRule.metric_key', field: 'metric_key', labelKey: 'ops.alertsCenter.editor.fieldMetric', kind: 'string' },
    { path: 'triggerRule.operator', field: 'operator', labelKey: 'ops.alertsCenter.editor.fieldOperator', kind: 'string' },
    { path: 'triggerRule.threshold', field: 'threshold', labelKey: 'ops.alertsCenter.editor.fieldThreshold', kind: 'number' },
    { path: 'triggerRule.duration_seconds', field: 'duration_seconds', labelKey: 'ops.alertsCenter.editor.fieldDuration', kind: 'number' },
    { path: 'triggerRule.evaluation_interval_seconds', field: 'evaluation_interval_seconds', labelKey: 'ops.alertsCenter.editor.fieldEvaluationInterval', kind: 'number' },
  ],
  health: [
    { path: 'triggerRule.check_type', field: 'check_type', labelKey: 'ops.alertsCenter.editor.checkType', kind: 'string' },
    { path: 'triggerRule.timeout_seconds', field: 'timeout_seconds', labelKey: 'ops.alertsCenter.editor.fieldAvailabilityTimeout', kind: 'number' },
    { path: 'triggerRule.duration_seconds', field: 'duration_seconds', labelKey: 'ops.alertsCenter.editor.fieldDuration', kind: 'number' },
  ],
  task: [
    { path: 'triggerRule.task_type', field: 'task_type', labelKey: 'ops.alertsCenter.editor.taskType', kind: 'string' },
    { path: 'triggerRule.event_type', field: 'event_type', labelKey: 'ops.alertsCenter.editor.eventType', kind: 'string' },
  ],
  event: [
    { path: 'triggerRule.event_category', field: 'event_category', labelKey: 'ops.alertsCenter.editor.eventCategory', kind: 'string' },
    { path: 'triggerRule.event_types', field: 'event_types', labelKey: 'ops.alertsCenter.editor.eventTypes', kind: 'array' },
  ],
}

export function triggerSpecsForType(alertType: string) {
  return triggerValidationSpecs[alertType] || []
}

export function isValidTriggerValue(value: unknown, kind: TriggerValidationKind) {
  if (kind === 'array') return Array.isArray(value) && value.length > 0
  if (kind === 'number') return typeof value === 'number' && Number.isFinite(value)
  return typeof value === 'string' && value.trim().length > 0
}

function fieldsNamedInMessage(message: string) {
  const match = message.match(/missing required fields?:\s*(.+)$/i)
  if (!match) return []
  return match[1]
    .split(',')
    .map((field) => field.trim())
    .filter(Boolean)
}

export function backendTriggerErrorSpecs(
  fields: Record<string, string[]> | undefined,
  alertType: string,
) {
  if (!fields) return []
  const specs = triggerSpecsForType(alertType)
  const requestedFields = new Set<string>()

  for (const [field, messages] of Object.entries(fields)) {
    const normalizedField = field
      .replace(/^trigger_rule(?:[.]|\[)?/, '')
      .replace(/^triggerRule(?:[.]|\[)?/, '')
      .replace(/\]$/, '')

    if (normalizedField && normalizedField !== field) requestedFields.add(normalizedField)
    if (specs.some((spec) => spec.field === field || spec.path === field)) {
      requestedFields.add(field.replace(/^triggerRule\./, ''))
    }
    if (field === 'trigger_rule' || field === 'triggerRule') {
      for (const message of messages) {
        for (const missingField of fieldsNamedInMessage(message)) {
          requestedFields.add(missingField)
        }
      }
    }
  }

  return specs.filter((spec) => requestedFields.has(spec.field))
}
