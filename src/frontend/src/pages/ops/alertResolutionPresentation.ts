import { hasNumericRecovery, inverseOperator, recoveryKind } from './alertPolicyRecovery'

type Rule = Record<string, unknown>
type Translate = (key: string, values?: Record<string, string | number>) => string

export function resolutionRows(type: string, trigger: Rule, recovery: Rule, t: Translate) {
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
      // Match the system evaluator's disk-space defaults, including its operator override.
      const disk = type === 'system' && trigger.check_type === 'disk_space_low'
      const operator = String(recovery.operator || inverseOperator(disk ? '>=' : trigger.operator))
      const threshold = recovery.threshold ?? trigger.threshold ?? (disk ? 90 : 0)
      rows.push({
        label: t(key('fieldRecoveryCondition')),
        value: t(key('resolutionConditionValue'), {
          metric: String(disk ? 'disk_usage' : trigger.metric_key || '—'),
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
  for (const [label, field] of [
    ['fieldNotifyOnFiring', 'notify_on_firing'],
    ['fieldNotifyOnResolved', 'notify_on_resolved'],
  ]) {
    rows.push({ label: t(key(label!)), value: t(key(recovery[field!] === false ? 'no' : 'yes')) })
  }
  return rows
}
