import { describe, expect, it } from 'vitest'
import { normalizeTaskStatus, taskStatusTone } from './taskStatusDisplay'

describe('taskStatusTone', () => {
  it.each([
    ['pending', 'warning'],
    ['queued', 'warning'],
    ['running', 'info'],
    ['in_progress', 'info'],
    ['dispatching', 'info'],
    ['creating', 'info'],
    ['success', 'success'],
    ['completed', 'success'],
    ['available', 'success'],
    ['failed', 'danger'],
    ['timeout', 'danger'],
    ['cancelled', 'neutral'],
    ['canceled', 'neutral'],
    ['retrying', 'warning'],
    ['blocked', 'warning'],
    ['partial', 'warning'],
    ['degraded', 'warning'],
    ['warning', 'warning'],
    ['unknown', 'neutral'],
    ['', 'neutral'],
  ])('maps %s to %s', (status, tone) => {
    expect(taskStatusTone(status)).toBe(tone)
  })

  it('normalizes aliases before display', () => {
    expect(normalizeTaskStatus(' In_Progress ')).toBe('running')
    expect(normalizeTaskStatus('completed')).toBe('success')
    expect(normalizeTaskStatus('canceled')).toBe('cancelled')
  })
})
