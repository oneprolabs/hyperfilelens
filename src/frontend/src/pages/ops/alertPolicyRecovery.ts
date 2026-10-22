export type RecoveryKind = 'numeric' | 'heartbeat' | 'manual'

export function recoveryKind(type: string, trigger: Record<string, unknown>): RecoveryKind {
  if (type === 'metric' || (type === 'system'
    && (trigger.check_type === 'disk_space_low' || !!trigger.metric_key))) return 'numeric'
  if (type === 'availability' && trigger.check_type === 'heartbeat') return 'heartbeat'
  return 'manual'
}

export function hasNumericRecovery(rule: Record<string, unknown>): boolean {
  return ['operator', 'threshold', 'duration_seconds'].some((key) => rule[key] != null)
}

export function inverseOperator(value: unknown): string {
  const inverse: Record<string, string> = {
    '>': '<=', '>=': '<', '<': '>=', '<=': '>', '==': '!=', '!=': '==',
  }
  return inverse[String(value || '>')] || '<='
}

// Keep shared and managed settings; remove only fields owned by the old trigger type.
const typeFields = new Set([
  'metric_key', 'operator', 'threshold', 'unit', 'duration_seconds',
  'evaluation_interval_seconds', 'task_type', 'event_type', 'operation_type',
  'check_type', 'timeout_seconds', 'event_category', 'event_types',
])

export function switchTrigger(
  previous: Record<string, unknown>,
  defaults: Record<string, unknown>,
): Record<string, unknown> {
  return {
    ...Object.fromEntries(Object.entries(previous).filter(([key]) => !typeFields.has(key))),
    ...defaults,
  }
}

export function switchRecovery(
  previous: Record<string, unknown>,
  kind: RecoveryKind,
): Record<string, unknown> {
  const shared = Object.fromEntries(Object.entries(previous).filter(
    ([key]) => !['enabled', 'condition', 'operator', 'threshold', 'duration_seconds'].includes(key),
  ))
  return kind === 'numeric'
    ? { ...shared, enabled: true, operator: '<', threshold: 70, duration_seconds: 180 }
    : { ...shared, enabled: kind === 'heartbeat' }
}
