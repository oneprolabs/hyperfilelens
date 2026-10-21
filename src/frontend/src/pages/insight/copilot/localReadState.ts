type ChatReadMetadata = {
  has_unread?: boolean
  last_assistant_message_at?: string | null
}

/** Compare server reply versions without relying on the browser's clock. */
export function compareReplyVersions(left: string, right: string): number | null {
  const leftTime = Date.parse(left)
  const rightTime = Date.parse(right)
  if (!Number.isFinite(leftTime) || !Number.isFinite(rightTime)) return null
  if (leftTime !== rightTime) return leftTime < rightTime ? -1 : 1
  // Date.parse truncates Django's microseconds. Keep the remaining precision
  // so a newer reply in the same millisecond is not silently marked read.
  const remainder = (value: string) =>
    (value.match(/\.(\d+)(?:Z|[+-]\d{2}:?\d{2})$/i)?.[1] || '')
      .slice(3).padEnd(6, '0').slice(0, 6)
  const leftRemainder = remainder(left)
  const rightRemainder = remainder(right)
  return leftRemainder === rightRemainder ? 0 : leftRemainder < rightRemainder ? -1 : 1
}

export function advanceLocalReadVersion(
  current: string | undefined,
  observed: string | null | undefined,
): string | undefined {
  if (!observed || compareReplyVersions(observed, observed) === null) return current
  if (!current) return observed
  const order = compareReplyVersions(observed, current)
  return order === null || order >= 0 ? observed : current
}

/** Overlay only replies already displayed in this component's lifetime. */
export function withLocalReadState<T extends ChatReadMetadata>(row: T, readThrough?: string): T {
  if (!row.has_unread || !row.last_assistant_message_at || !readThrough) return row
  const order = compareReplyVersions(row.last_assistant_message_at, readThrough)
  return order !== null && order <= 0 ? { ...row, has_unread: false } : row
}
