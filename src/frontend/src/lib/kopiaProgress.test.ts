import { describe, expect, it } from 'vitest'

import { formatSpeedBps, transferCapacityText, transferMetricParts, transferSpeedParts } from './kopiaProgress'

import { createI18n } from 'vue-i18n'
import { enProtectionPages } from '../locales/enProtectionPages'
const t = createI18n({ legacy: false, locale: 'en', messages: { en: { protection: enProtectionPages } } }).global.t

describe('transferCapacityText', () => {
  it('uses backup progress for the reference total', () => {
    expect(transferCapacityText(t, {
      bytes_done: 5_000_000,
      bytes_total: 2_000_000_000,
      bytes_total_known: true,
      bytes_total_reference: true,
    })).toBe('Backup progress: 4.77 MB / 1.86 GB')
  })

  it('uses backup progress for an estimated total', () => {
    expect(transferCapacityText(t, {
      bytes_done: 5_000_000,
      bytes_total: 12_500_000,
      bytes_total_known: true,
      estimated_bytes: 12_500_000,
    })).toBe('Backup progress: 4.77 MB / 11.9 MB')
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
    })).toBe('Backup progress: 3.24 GB / 3.85 GB')
  })

  it('uses restore-specific wording for restore capacity', () => {
    expect(transferCapacityText(t, {
      label_key: 'protection.taskProgress.restore.transferring',
      progress_schema_version: 2,
      bytes_done: 1_210_000_000,
      bytes_total: 24_500_000_000,
      bytes_total_known: true,
    })).toBe('Data restored: 1.13 GB / 22.8 GB')
  })

  it('shows processed bytes without inventing a total', () => {
    expect(transferCapacityText(t, {
      progress_schema_version: 2,
      processed_bytes: 3_157_346_250,
      bytes_done: 3_157_346_250,
      bytes_total: null,
      bytes_total_known: false,
      uploaded_bytes: 192,
    })).toBe('Backup progress: 2.94 GB')
  })
})

describe('transferSpeedParts', () => {
  it('hides legacy hash throughput for backups', () => {
    expect(transferSpeedParts(t, {
      phase: 'transferring',
      speed_bps: 393_000_000,
      hash_speed_bps: 393_000_000,
    })).toEqual([])
  })

  it('hides processed-byte throughput for backups', () => {
    expect(transferSpeedParts(t, {
      phase: 'transferring',
      progress_schema_version: 2,
      processing_speed_bps: 19_293_000,
      upload_speed_bps: 5_740_000,
    })).toEqual([])
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

  it('labels restore speed explicitly', () => {
    expect(transferSpeedParts(t, {
      phase: 'transferring',
      label_key: 'protection.taskProgress.restore.transferring',
      upload_speed_bps: 5_340_000,
    })).toEqual(['Restore speed: 5.09 MB/s'])
  })

  it('does not expose physical upload speed for backup progress', () => {
    expect(transferSpeedParts(t, {
      phase: 'transferring',
      progress_schema_version: 2,
      upload_speed_bps: 0,
    })).toEqual([])
    expect(formatSpeedBps(null)).toBeNull()
  })

  it('hides processing throughput even when labels are requested', () => {
    expect(transferSpeedParts(t, {
      phase: 'transferring',
      progress_schema_version: 2,
      processing_speed_bps: 393_000_000,
      hash_speed_bps: 393_000_000,
    }, { labelProcessingSpeed: true })).toEqual([])
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
    })).not.toContain('About 30s remaining')
  })
})


describe('backup display and restore isolation', () => {
  it.each([
    [45, 'About 45s remaining'], [120, 'About 2 min remaining'],
    [3600, 'About 1h remaining'], [3660, 'About 1h 1m remaining'],
  ])('formats backup ETA %s without speed', (seconds, expected) => {
    const transfer = { bytes_done: 1024, bytes_total: 2048, bytes_total_known: true,
      phase: 'transferring', eta_seconds: Number(seconds), processing_speed_bps: 123456 }
    const parts = transferMetricParts(t, transfer)
    expect(parts.at(-1)).toBe(expected)
    expect(parts).toHaveLength(2)
    expect(parts.join(' ')).not.toContain('/s')
  })
  it('preserves restore ETA and unknown-total wording', () => {
    expect(transferMetricParts(t, {
      label_key: 'protection.taskProgress.restore.transferring', bytes_done: 1024,
      bytes_total: 2048, bytes_total_known: true, phase: 'transferring',
      eta_seconds: 120, upload_speed_bps: 1024,
    })).toEqual(['Data restored: 1.00 KB / 2.00 KB', 'Restore speed: 1.00 KB/s', '2 min left'])
    expect(transferCapacityText(t, {
      label_key: 'protection.taskProgress.restore.transferring', progress_schema_version: 2,
      processed_bytes: 1024, bytes_total_known: false,
    })).toBe('Processed: 1.00 KB')
  })
})
