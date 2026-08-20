import { describe, expect, it } from 'vitest'

import { transferCapacityText, transferSpeedParts } from './kopiaProgress'

const t = (key: string, args?: Record<string, unknown>) => {
  if (key.endsWith('bytesCapacityRef')) return `Transferred: ${args?.done} / source data: ${args?.total}`
  if (key.endsWith('bytesCapacityEst')) return `Incremental transfer: ${args?.done} / est. ${args?.total}`
  if (key.endsWith('bytesCapacity')) return `${args?.done} / ${args?.total}`
  if (key.endsWith('hashSpeed')) return `Scanning: ${args?.speed}`
  return key
}

describe('transferCapacityText', () => {
  it('labels the logical source-data reference total explicitly', () => {
    expect(transferCapacityText(t, {
      bytes_done: 5_000_000,
      bytes_total: 2_000_000_000,
      bytes_total_known: true,
      bytes_total_reference: true,
    })).toBe('Transferred: 5.00 MB / source data: 2.00 GB')
  })

  it('labels a Kopia estimate as incremental transfer volume', () => {
    expect(transferCapacityText(t, {
      bytes_done: 5_000_000,
      bytes_total: 12_500_000,
      bytes_total_known: true,
      estimated_bytes: 12_500_000,
    })).toBe('Incremental transfer: 5.00 MB / est. 12.5 MB')
  })
})

describe('transferSpeedParts', () => {
  it('labels hash throughput instead of presenting it as upload throughput', () => {
    expect(transferSpeedParts(t, {
      phase: 'transferring',
      speed_bps: 393_000_000,
      hash_speed_bps: 393_000_000,
    })).toEqual(['Scanning: 393 MB/s'])
  })

  it('does not display an unclassified legacy speed', () => {
    expect(transferSpeedParts(t, {
      phase: 'transferring',
      speed_bps: 393_000_000,
    })).toEqual([])
  })

  it('allows restore callers to display their legacy runtime speed', () => {
    expect(transferSpeedParts(t, {
      phase: 'transferring',
      speed_bps: 500_000,
    }, { allowUnclassifiedSpeed: true })).toEqual(['500 KB/s'])
  })
})
