import { describe, expect, it } from 'vitest'
import { hasNumericRecovery, inverseOperator, recoveryKind, switchRecovery, switchTrigger } from './alertPolicyRecovery'

describe('alert recovery capabilities', () => {
  it.each([
    ['metric', {}, 'numeric'],
    ['task', {}, 'manual'],
    ['event', {}, 'manual'],
    ['availability', { check_type: 'heartbeat' }, 'heartbeat'],
    ['availability', { check_type: 'unknown' }, 'manual'],
    ['system', { check_type: 'disk_space_low' }, 'numeric'],
    ['system', { check_type: 'service_health', metric_key: 'cpu_usage' }, 'numeric'],
    ['system', { check_type: 'service_health' }, 'manual'],
  ] as const)('classifies %s %j as %s', (type, trigger, kind) => {
    expect(recoveryKind(type, trigger)).toBe(kind)
  })

  it('removes old trigger fields without dropping shared or managed settings', () => {
    expect(switchTrigger({
      metric_key: 'cpu_usage', threshold: 80, operator: '>=', duration_seconds: 300,
      unit: '%', evaluation_interval_seconds: 60, operation_type: 'maintenance',
      repeat_interval_seconds: 3600, merge_enabled: true, managed: 'repository_quota',
      max_repeats: 5, silenced_until: '2026-10-22',
    }, { task_type: 'backup', event_type: 'task_failed' })).toEqual({
      task_type: 'backup', event_type: 'task_failed', repeat_interval_seconds: 3600,
      merge_enabled: true, managed: 'repository_quota', max_repeats: 5, silenced_until: '2026-10-22',
    })
  })

  it.each(['manual', 'heartbeat'] as const)('removes numeric recovery but keeps notifications for %s', kind => {
    expect(switchRecovery({
      enabled: true, condition: 'below_threshold', operator: '<', threshold: 70,
      duration_seconds: 180, notify_on_firing: false, notify_on_resolved: true, custom: 'keep',
    }, kind)).toEqual({
      enabled: kind === 'heartbeat', notify_on_firing: false, notify_on_resolved: true, custom: 'keep',
    })
  })

  it('recognizes notification-only settings as legacy recovery, not custom numeric recovery', () => {
    expect(hasNumericRecovery({})).toBe(false)
    expect(hasNumericRecovery({ enabled: true, notify_on_firing: false })).toBe(false)
    expect(hasNumericRecovery({ duration_seconds: 0 })).toBe(true)
  })

  it.each([['>=', '<'], ['>', '<='], ['<', '>='], ['<=', '>'], ['==', '!='], ['!=', '==']])(
    'uses the evaluator inverse of %s', (operator, expected) => {
      expect(inverseOperator(operator)).toBe(expected)
    },
  )
})
