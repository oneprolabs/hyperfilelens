import { describe, expect, it } from 'vitest'
import { planDuration, recordPlanTiming } from './copilotPlanTimings'

describe('plan step timing', () => {
  const start = '2026-11-23T00:00:00Z'
  const end = '2026-11-23T00:01:05Z'

  it('uses upstream start/end observations and freezes completed durations', () => {
    const running = recordPlanTiming(undefined, 'in_progress', start)
    expect(planDuration(running, true, Date.parse(end))).toBe('1m 5s')
    const finished = recordPlanTiming(running, 'completed', end)
    expect(planDuration(finished, false, Date.parse(end) + 60_000)).toBe('1m 5s')
    expect(recordPlanTiming(finished, 'completed', '2026-11-23T00:03:00Z')).toEqual(finished)
  })

  it('does not invent timings for history, invalid dates or terminal-only observations', () => {
    expect(recordPlanTiming(undefined, 'in_progress', undefined)).toBeUndefined()
    expect(recordPlanTiming(undefined, 'in_progress', 'invalid')).toBeUndefined()
    expect(recordPlanTiming(undefined, 'completed', end)).toBeUndefined()
    expect(planDuration(undefined, true, Date.now())).toBe('')
  })

  it('does not keep counting when the run is no longer live', () => {
    const running = recordPlanTiming(undefined, 'in_progress', start)
    expect(planDuration(running, false, Date.parse(end))).toBe('')
  })

  it.each(['completed', 'failed', 'skipped'])('preserves a known start without inventing a missing %s timestamp', (status) => {
    const running = recordPlanTiming(undefined, 'in_progress', start)
    const unknownEnd = recordPlanTiming(running, status, undefined)
    expect(unknownEnd).toEqual(running)
    expect(planDuration(unknownEnd, false, Date.parse(end))).toBe('')
    const restoredEnd = recordPlanTiming(unknownEnd, status, end)
    expect(planDuration(restoredEnd, false, Date.parse(end) + 60_000)).toBe('1m 5s')
  })

  it.each(['failed', 'skipped'])('freezes %s steps and guards against negative durations', (status) => {
    const running = recordPlanTiming(undefined, 'in_progress', end)
    const stopped = recordPlanTiming(running, status, start)
    expect(planDuration(stopped, false, Date.now())).toBe('0s')
  })
})
