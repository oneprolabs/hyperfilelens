import { hasNumericRecovery, inverseOperator, recoveryKind } from './alertPolicyRecovery'

type Rule = Record<string, unknown>
type Translate = (key: string, values?: Record<string, string | number>) => string
type LabelFn = (value?: string | null) => string

export function notificationTimingRows(recovery: Rule, t: Translate) {
  const key = (name: string) => `ops.alertsCenter.editor.${name}`
  return [
    {
      label: t(key('fieldNotifyOnFiring')),
      value: t(key(recovery.notify_on_firing === false ? 'no' : 'yes')),
    },
    {
      label: t(key('fieldNotifyOnResolved')),
      value: t(key(recovery.notify_on_resolved === false ? 'no' : 'yes')),
    },
  ]
}

export function resolutionRows(
  type: string,
  trigger: Rule,
  recovery: Rule,
  t: Translate,
  labels?: { metricLabel?: LabelFn; includeNotify?: boolean },
) {
  const key = (name: string) => `ops.alertsCenter.editor.${name}`
  const kind = recoveryKind(type, trigger)
  const enabled = recovery.enabled !== false
  const legacyTask = type === 'task' && enabled
  const automatic = enabled && kind !== 'manual'
  const rows = [{
    label: t(key('previewRecovery')),
    value: t(legacyTask ? key('recoveryLegacyLabel') : automatic
      ? 'ops.alertsCenter.common.automaticResolution' : key('recoveryManual')),
  }]
  if (legacyTask) {
    rows.push({ label: t(key('recoveryLegacyLabel')), value: t(key('recoveryLegacyTask')) })
  } else if (automatic && kind === 'heartbeat') {
    rows.push({ label: t(key('fieldRecoveryCondition')), value: t(key('recoveryHeartbeat')) })
  } else if (automatic && kind === 'numeric') {
    if (!hasNumericRecovery(recovery)) {
      rows.push({ label: t(key('fieldRecoveryCondition')), value: t(key('recoveryTriggerCleared')) })
    } else {
      const operator = String(recovery.operator || inverseOperator(trigger.operator))
      const threshold = recovery.threshold ?? trigger.threshold ?? 0
      const metricKey = String(trigger.metric_key || '')
      const metric = labels?.metricLabel?.(metricKey) || metricKey || '—'
      rows.push({
        label: t(key('fieldRecoveryCondition')),
        value: t(key('resolutionConditionValue'), {
          metric,
          operator, threshold: String(threshold), unit: String(trigger.unit || ''),
        }),
      }, {
        label: t(key('fieldRecoveryDuration')),
        value: Number(recovery.duration_seconds || 0) === 0
          ? t(key('recoveryImmediate'))
          : t(key('resolutionSeconds'), { seconds: String(recovery.duration_seconds) }),
      })
    }
  }
  if (labels?.includeNotify !== false) {
    rows.push(...notificationTimingRows(recovery, t))
  }
  return rows
}
