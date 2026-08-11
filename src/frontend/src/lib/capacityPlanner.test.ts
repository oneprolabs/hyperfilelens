import { describe, expect, it } from 'vitest'
import { planRepositoryCapacity } from './capacityPlanner'

describe('planRepositoryCapacity', () => {
  it('calculates a warning only from the selected finite repository', () => {
    const plan = planRepositoryCapacity({
      usedBytes: 70,
      capacityBytes: 100,
      capacityMode: 'known',
    }, 15)

    expect(plan).toEqual({
      projectedBytes: 85,
      projectedPct: 85,
      remainingBytes: 15,
      status: 'warning',
    })
  })

  it.each(['unlimited', 'pending', 'unavailable'] as const)(
    'does not evaluate capacity for a %s repository',
    (capacityMode) => {
      const plan = planRepositoryCapacity({
        usedBytes: 5,
        capacityBytes: 0,
        capacityMode,
      }, 1024)

      expect(plan).toEqual({
        projectedBytes: 1029,
        projectedPct: null,
        remainingBytes: null,
        status: 'neutral',
      })
    },
  )
})
