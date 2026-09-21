import { describe, expect, it } from 'vitest'

import {
  formatAgentActivityDuration,
  withAgentActivityDuration,
} from './copilotAgentActivityText'

describe('formatAgentActivityDuration', () => {
  it('matches SourceLens formatDuration under one minute', () => {
    expect(formatAgentActivityDuration(0)).toBe('0s')
    expect(formatAgentActivityDuration(14)).toBe('14s')
    expect(formatAgentActivityDuration(14.4)).toBe('14s')
    expect(formatAgentActivityDuration(14.6)).toBe('15s')
  })

  it('matches SourceLens formatDuration at and above one minute', () => {
    expect(formatAgentActivityDuration(60)).toBe('1m 0s')
    expect(formatAgentActivityDuration(65)).toBe('1m 5s')
    expect(formatAgentActivityDuration(125)).toBe('2m 5s')
  })

  it('returns empty for missing values', () => {
    expect(formatAgentActivityDuration(null)).toBe('')
    expect(formatAgentActivityDuration(undefined)).toBe('')
  })
})

describe('withAgentActivityDuration', () => {
  it('appends the SourceLens separator and duration', () => {
    expect(withAgentActivityDuration('Completed 1 activities', 14)).toBe(
      'Completed 1 activities · 14s',
    )
    expect(withAgentActivityDuration('Recorded 1 activities', 65)).toBe(
      'Recorded 1 activities · 1m 5s',
    )
  })

  it('leaves the base text alone when duration is missing', () => {
    expect(withAgentActivityDuration('Completed 1 activities', null)).toBe(
      'Completed 1 activities',
    )
  })
})
