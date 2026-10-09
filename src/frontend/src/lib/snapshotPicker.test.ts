import { describe, expect, it } from 'vitest'
import { formatLocalDateTime } from './dateTime'
import { compareSnapshotsNewestFirst, isNewerSnapshot, snapshotOptionLabel } from './snapshotPicker'

const row = {
  id: 71,
  snapshot_uid: 'bss-71',
  created_at: '2026-10-06T08:00:00Z',
  started_at: '2026-10-07T08:00:00Z',
  finished_at: '2026-10-08T08:00:00Z',
  total_size_bytes: 1024,
}

describe('snapshot picker presentation', () => {
  it('shows the business ID, effective time, and size consistently', () => {
    expect(snapshotOptionLabel(row)).toBe(`bss-71 · ${formatLocalDateTime(row.finished_at)} · 1.00 KB`)
    expect(snapshotOptionLabel({ ...row, finished_at: null })).toContain(formatLocalDateTime(row.started_at))
    expect(snapshotOptionLabel({ ...row, finished_at: null, started_at: null })).toContain(formatLocalDateTime(row.created_at))
  })

  it('only considers later effective times or the backend ID tie-breaker newer', () => {
    expect(isNewerSnapshot({ ...row, id: 72 }, row)).toBe(true)
    expect(isNewerSnapshot({ ...row, id: 70 }, row)).toBe(false)
    expect(isNewerSnapshot(row, row)).toBe(false)
    expect(isNewerSnapshot({ ...row, id: 80, finished_at: '2026-10-07T08:00:00Z' }, row)).toBe(false)
    expect(isNewerSnapshot({ ...row, finished_at: 'invalid' }, row)).toBe(false)
  })

  it('orders a separately loaded current snapshot by the same time and ID rules', () => {
    const older = { ...row, id: 70, finished_at: '2026-10-07T08:00:00Z' }
    const newer = { ...row, id: 72 }
    expect([older, row, newer].sort(compareSnapshotsNewestFirst).map((item) => item.id)).toEqual([72, 71, 70])
  })
})
