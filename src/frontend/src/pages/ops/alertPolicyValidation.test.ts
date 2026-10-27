import { describe, expect, it } from 'vitest'
import {
  backendTriggerErrorSpecs,
  isValidTriggerValue,
  triggerSpecsForType,
} from './alertPolicyValidation'

const requiredFieldsByType = {
  metric: [
    'metric_key',
    'operator',
    'threshold',
    'duration_seconds',
    'evaluation_interval_seconds',
  ],
  health: ['check_type', 'timeout_seconds', 'duration_seconds'],
  task: ['task_type', 'event_type'],
  event: ['event_category', 'event_types'],
}

describe('alert policy trigger validation', () => {
  it.each(Object.entries(requiredFieldsByType))(
    'matches the backend required fields for %s alerts',
    (alertType, expectedFields) => {
      expect(triggerSpecsForType(alertType).map((spec) => spec.field)).toEqual(expectedFields)
    },
  )

  it.each([
    ['string', '', false],
    ['string', '   ', false],
    ['string', 'heartbeat', true],
    ['number', undefined, false],
    ['number', Number.NaN, false],
    ['number', 0, true],
    ['array', [], false],
    ['array', ['event_type'], true],
  ] as const)('validates %s trigger values', (kind, value, expected) => {
    expect(isValidTriggerValue(value, kind)).toBe(expected)
  })

  it('maps an aggregate backend event error to both event controls', () => {
    const specs = backendTriggerErrorSpecs({
      trigger_rule: ['Missing required fields: event_category, event_types'],
    }, 'event')

    expect(specs.map((spec) => spec.path)).toEqual([
      'triggerRule.event_category',
      'triggerRule.event_types',
    ])
  })

  it('maps a nested backend field error to the matching current control', () => {
    const specs = backendTriggerErrorSpecs({
      'trigger_rule.timeout_seconds': ['This field is required.'],
    }, 'health')

    expect(specs.map((spec) => spec.path)).toEqual(['triggerRule.timeout_seconds'])
  })

  it('does not map fields belonging to another alert type', () => {
    const specs = backendTriggerErrorSpecs({
      trigger_rule: ['Missing required fields: event_types'],
    }, 'metric')

    expect(specs).toEqual([])
  })
})
