import { describe, expect, it } from 'vitest'
import { summarizeNodeAvailability } from './nodeAvailability'

describe('Dashboard node availability summary', () => {
  it('counts connectivity availability independently from lifecycle status', () => {
    expect(
      summarizeNodeAvailability([
        { availability: 'online' },
        { availability: 'offline' },
        { availability: 'online' },
        {},
      ]),
    ).toEqual({ online: 2, offline: 1 })
  })
})
