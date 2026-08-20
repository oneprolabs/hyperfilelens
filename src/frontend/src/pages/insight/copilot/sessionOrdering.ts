import type { LensSessionLink } from '../../../lib/lensApi'

export type SessionGroupKey = 'pinned' | 'today' | 'yesterday' | 'earlier'
export type SessionRow = LensSessionLink & { group: SessionGroupKey }

function timestamp(value: string | null | undefined) {
  const parsed = Date.parse(value || '')
  return Number.isFinite(parsed) ? parsed : 0
}

export function groupForCreatedAt(
  createdAt: string | null | undefined,
  now = new Date(),
): Exclude<SessionGroupKey, 'pinned'> {
  const created = new Date(createdAt || '')
  if (Number.isNaN(created.getTime())) return 'earlier'
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const startYesterday = new Date(startToday)
  startYesterday.setDate(startYesterday.getDate() - 1)
  if (created >= startToday) return 'today'
  if (created >= startYesterday) return 'yesterday'
  return 'earlier'
}

export function toSessionRows(
  rows: LensSessionLink[],
  now = new Date(),
): SessionRow[] {
  return rows.map((row) => ({
    ...row,
    group: row.pinned_at ? 'pinned' : groupForCreatedAt(row.created_at, now),
  })).sort((left, right) => {
    const leftPinned = Boolean(left.pinned_at)
    const rightPinned = Boolean(right.pinned_at)
    if (leftPinned !== rightPinned) return rightPinned ? 1 : -1
    if (leftPinned && rightPinned) {
      const pinDelta = timestamp(right.pinned_at) - timestamp(left.pinned_at)
      if (pinDelta) return pinDelta
    }
    const createdDelta = timestamp(right.created_at) - timestamp(left.created_at)
    if (createdDelta) return createdDelta
    return right.id - left.id
  })
}
