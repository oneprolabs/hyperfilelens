import { describe, expect, it } from 'vitest'
import {
  booleanStatusTag,
  alertStatusTagAttrs,
  lifecycleStatusTone,
  severityStatusTagAttrs,
  statusTagAttrs,
} from './statusTag'

describe('lifecycleStatusTone', () => {
  it.each([
    ['available', 'success'],
    ['online', 'success'],
    ['failed', 'danger'],
    ['offline', 'danger'],
    ['running', 'info'],
    ['in_progress', 'info'],
    ['mounting', 'info'],
    ['creating', 'info'],
    ['deleting', 'info'],
    ['dispatching', 'info'],
    ['reconnecting', 'info'],
    ['removing', 'info'],
    ['pending_install', 'info'],
    ['syncing', 'info'],
    ['upgrading', 'info'],
    ['created', 'success'],
    ['create_failed', 'danger'],
    ['delete_failed', 'danger'],
    ['remove_failed', 'danger'],
    ['partial', 'warning'],
    ['degraded', 'warning'],
    ['retrying', 'warning'],
    ['pending', 'neutral'],
    ['cancelled', 'neutral'],
    ['deleted', 'neutral'],
    ['unknown', 'neutral'],
  ])('maps %s to %s', (status, tone) => {
    expect(lifecycleStatusTone(status)).toBe(tone)
  })
})

describe('booleanStatusTag', () => {
  it('uses success for enabled values', () => {
    expect(booleanStatusTag(true)).toEqual({ type: 'success', class: 'hfl-tag--boolean' })
  })

  it('uses the neutral global tag for disabled values', () => {
    expect(booleanStatusTag(false)).toEqual({ class: 'hfl-tag--neutral hfl-tag--boolean' })
  })
})

describe('status tag semantic helpers', () => {
  it('keeps neutral separate from the purple info tone', () => {
    expect(statusTagAttrs('neutral')).toEqual({ class: 'hfl-tag--neutral' })
    expect(statusTagAttrs('info')).toEqual({ type: 'info' })
  })

  it.each([
    ['firing', { class: 'hfl-tag--firing' }],
    ['acknowledged', { type: 'info' }],
    ['resolved', { type: 'success' }],
    ['pending', { class: 'hfl-tag--neutral' }],
  ])('maps alert status %s to its lifecycle tone', (status, attrs) => {
    expect(alertStatusTagAttrs(status)).toEqual(attrs)
  })

  it.each([
    ['critical', { type: 'danger' }],
    ['high', { type: 'danger' }],
    ['warning', { type: 'warning' }],
    ['medium', { type: 'warning' }],
    ['info', { type: 'info' }],
    ['low', { type: 'info' }],
    ['unknown', { class: 'hfl-tag--neutral' }],
  ])('maps severity %s to the shared tone', (severity, attrs) => {
    expect(severityStatusTagAttrs(severity)).toEqual(attrs)
  })
})
