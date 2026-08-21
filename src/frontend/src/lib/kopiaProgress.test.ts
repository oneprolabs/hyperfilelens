import { describe, expect, it } from 'vitest'

import { formatSpeedBps, transferCapacityText, transferMetricParts, transferSpeedParts } from './kopiaProgress'

const t = (key: string, args?: Record<string, unknown>) => {
  if (key.endsWith('bytesCapacityRef')) return `Transferred: ${args?.done} / source data: ${args?.total}`
  if (key.endsWith('bytesCapacityEst')) return `Incremental transfer: ${args?.done} / est. ${args?.total}`
  if (key.endsWith('bytesCapacity')) return `${args?.done} / ${args?.total}`
  if (key.endsWith('bytesProcessedCapacity')) return `Processed: ${args?.done} / ${args?.total}`
  if (key.endsWith('bytesProcessed')) return `Processed: ${args?.size}`
  if (key.endsWith('hashSpeed')) return `Scanning: ${args?.speed}`
  if (key.endsWith('uploadSpeed')) return `Upload: ${args?.speed}`
  if (key.endsWith('etaSeconds')) return `${args?.n}s left`
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

  it('uses logical processed bytes for schema v2 capacity', () => {
    expect(transferCapacityText(t, {
      progress_schema_version: 2,
      processed_bytes: 3_478_373_863,
      bytes_done: 3_478_373_863,
      uploaded_bytes: 270_077_614,
      bytes_total: 4_130_621_356,
      bytes_total_known: true,
      estimated_bytes: 4_130_621_356,
    })).toBe('Processed: 3.48 GB / 4.13 GB')
  })

  it('shows processed bytes without inventing a total', () => {
    expect(transferCapacityText(t, {
      progress_schema_version: 2,
      processed_bytes: 3_157_346_250,
      bytes_done: 3_157_346_250,
      bytes_total: null,
      bytes_total_known: false,
      uploaded_bytes: 192,
    })).toBe('Processed: 3.16 GB')
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

  it('labels physical upload speed and preserves a fresh zero sample', () => {
    expect(transferSpeedParts(t, {
      phase: 'transferring',
      progress_schema_version: 2,
      upload_speed_bps: 0,
    })).toEqual(['Upload: 0 B/s'])
    expect(formatSpeedBps(null)).toBeNull()
  })

  it('does not present processing throughput as upload speed for schema v2', () => {
    expect(transferSpeedParts(t, {
      phase: 'transferring',
      progress_schema_version: 2,
      processing_speed_bps: 393_000_000,
      hash_speed_bps: 393_000_000,
    })).toEqual([])
  })

  it('hides ETA while finalizing', () => {
    expect(transferMetricParts(t, {
      phase: 'finalizing',
      progress_schema_version: 2,
      processed_bytes: 1_000,
      bytes_done: 1_000,
      bytes_total: 1_000,
      bytes_total_known: true,
      eta_seconds: 30,
    })).not.toContain('30s left')
  })
})
