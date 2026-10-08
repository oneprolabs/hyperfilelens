import { formatLocalDateTime } from './dateTime'
import { formatBytes } from './kopiaProgress'
import type { BackupSourceSnapshot } from './protectionBackupConfigApi'

type PickerSnapshot = Pick<BackupSourceSnapshot,
  'id' | 'snapshot_uid' | 'finished_at' | 'started_at' | 'created_at' | 'total_size_bytes'>

function snapshotTime(row: PickerSnapshot) {
  return row.finished_at || row.started_at || row.created_at
}

export function snapshotOptionLabel(row: PickerSnapshot): string {
  const time = snapshotTime(row)
  return `${row.snapshot_uid} · ${time ? formatLocalDateTime(time) : '—'} · ${formatBytes(row.total_size_bytes)}`
}

/** Match the backend's picker_latest ordering, including its ID tie-breaker. */
export function isNewerSnapshot(candidate: PickerSnapshot, applied: PickerSnapshot): boolean {
  const candidateTime = Date.parse(snapshotTime(candidate))
  const appliedTime = Date.parse(snapshotTime(applied))
  return Number.isFinite(candidateTime) && Number.isFinite(appliedTime)
    && (candidateTime > appliedTime || (candidateTime === appliedTime && candidate.id > applied.id))
}

export function compareSnapshotsNewestFirst(left: PickerSnapshot, right: PickerSnapshot): number {
  const leftTime = Date.parse(snapshotTime(left))
  const rightTime = Date.parse(snapshotTime(right))
  return (Number.isFinite(rightTime) ? rightTime : 0) - (Number.isFinite(leftTime) ? leftTime : 0)
    || right.id - left.id
}
