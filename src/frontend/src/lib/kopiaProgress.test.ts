import { describe, expect, it } from 'vitest'

import { formatSpeedBps, transferCapacityText, transferMetricParts, transferSpeedParts } from './kopiaProgress'

const t = (key: string, args?: Record<string, unknown>) => {
  if (key.endsWith('bytesCapacityRef')) return `Transferred: ${args?.done} / source data: ${args?.total}`
  if (key.endsWith('bytesCapacityEst')) return `Incremental transfer: ${args?.done} / est. ${args?.total}`
  if (key.endsWith('bytesCapacity')) return `${args?.done} / ${args?.total}`
  if (key.endsWith('bytesProcessedCapacity')) return `Processed: ${args?.done} / ${args?.total}`
  if (key.endsWith('bytesProcessed')) return `Processed: ${args?.size}`
  if (key.endsWith('hashSpeed')) return `Scanning: ${args?.speed}`
  if (key.endsWith('processingSpeed')) return `Processing speed: ${args?.speed}`
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
    })).toBe('Transferred: 4.77 MB / source data: 1.86 GB')
  })

  it('labels a Kopia estimate as incremental transfer volume', () => {
    expect(transferCapacityText(t, {
      bytes_done: 5_000_000,
      bytes_total: 12_500_000,
      bytes_total_known: true,
      estimated_bytes: 12_500_000,
    })).toBe('Incremental transfer: 4.77 MB / est. 11.9 MB')
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
    })).toBe('Processed: 3.24 GB / 3.85 GB')
  })

  it('shows processed bytes without inventing a total', () => {
    expect(transferCapacityText(t, {
      progress_schema_version: 2,
      processed_bytes: 3_157_346_250,
      bytes_done: 3_157_346_250,
      bytes_total: null,
      bytes_total_known: false,
      uploaded_bytes: 192,
    })).toBe('Processed: 2.94 GB')
  })
})

describe('transferSpeedParts', () => {
  it('labels hash throughput instead of presenting it as upload throughput', () => {
    expect(transferSpeedParts(t, {
      phase: 'transferring',
      speed_bps: 393_000_000,
      hash_speed_bps: 393_000_000,
    })).toEqual(['Scanning: 375 MB/s'])
  })

  it('uses processed-byte throughput for backup progress', () => {
    expect(transferSpeedParts(t, {
      phase: 'transferring',
      progress_schema_version: 2,
      processing_speed_bps: 19_293_000,
      upload_speed_bps: 5_740_000,
    })).toEqual(['18.4 MB/s'])
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
    }, { allowUnclassifiedSpeed: true })).toEqual(['488 KB/s'])
  })

  it('does not expose physical upload speed for backup progress', () => {
    expect(transferSpeedParts(t, {
      phase: 'transferring',
      progress_schema_version: 2,
      upload_speed_bps: 0,
    })).toEqual([])
    expect(formatSpeedBps(null)).toBeNull()
  })

  it('presents schema-v2 processing throughput with its own label', () => {
    expect(transferSpeedParts(t, {
      phase: 'transferring',
      progress_schema_version: 2,
      processing_speed_bps: 393_000_000,
      hash_speed_bps: 393_000_000,
    }, { labelProcessingSpeed: true })).toEqual(['Processing speed: 375 MB/s'])
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
