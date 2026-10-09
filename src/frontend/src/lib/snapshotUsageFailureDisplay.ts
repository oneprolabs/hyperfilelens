import type { TranslateFn } from './errors/resolver'

export type SnapshotUsageReason = {
  code?: string
  detail: string
  snapshot_id?: number
  consumers?: Array<{ type: string; status: string }>
}

export function snapshotUsageReasonKey(item: SnapshotUsageReason): string {
  if (item.code === 'snapshot_in_use') {
    return `${item.code}:${item.snapshot_id || item.detail}`
  }
  return item.code || item.detail || ''
}

// Keep snapshot identity and failed-Chat context when translating the generic
// snapshot_in_use code. Historical tasks without metadata use their fallback.
export function snapshotUsageFailureText(
  item: SnapshotUsageReason,
  t: TranslateFn,
): string | undefined {
  if (item.code !== 'snapshot_in_use' || !item.snapshot_id) return undefined
  const key = 'ops.task.failureDetails.reason.snapshot_in_use_detail'
  const translated = t(key, { snapshotId: item.snapshot_id })
  if (!translated || translated === key) return item.detail
  if (!item.consumers?.some(consumer => consumer.type === 'chat' && consumer.status === 'failed')) {
    return translated
  }
  const failedKey = 'ops.task.failureDetails.reason.snapshot_failed_chat'
  const failedText = t(failedKey)
  return failedText && failedText !== failedKey ? `${translated} ${failedText}` : item.detail
}
