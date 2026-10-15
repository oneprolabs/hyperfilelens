import type { ComposerTranslation } from 'vue-i18n'
import { unregisterReasonLabel } from './backupSourceUnregisterDialog'
import { snapshotUsageFailureText, type SnapshotUsageReason } from './snapshotUsageFailureDisplay'
import { unregisterCleanupSuggestion, unregisterCleanupText } from './unregisterCleanupDisplay'

export type UnregisterTaskReason = SnapshotUsageReason & {
  source_id?: string
  source_name?: string
}

/** Keep aggregate task diagnostics out of business causes when specific causes exist. */
export function unregisterBusinessReasons<T extends UnregisterTaskReason>(
  reasons: T[],
  errorCode?: string | null,
): T[] {
  const isAggregate = (reason: UnregisterTaskReason) =>
    Boolean(reason.code && (
      reason.code === errorCode
      || reason.code === 'TASK_FAILED'
      || reason.code.startsWith('SOURCE_UNREGISTER_')
    ))
  if (!reasons.some(reason => reason.code && !isAggregate(reason))) return reasons
  return reasons.filter(reason => !isAggregate(reason))
}

export function unregisterTaskReasonText(item: UnregisterTaskReason, t: ComposerTranslation): string {
  const knownText = unregisterCleanupText(item.detail, t)
  if (knownText !== item.detail) return knownText
  if (item.code === 'agent_offline' && !item.source_name && !item.source_id) {
    return t('ops.task.failureDetails.reason.agent_offline')
  }
  const label = snapshotUsageFailureText(item, t)
    || unregisterReasonLabel({ ...item, code: item.code || '' }, t)
  if (label !== item.detail && label !== item.code) return label
  const key = `ops.task.failureDetails.reason.${item.code || ''}`
  const translated = t(key, {}, { missingWarn: false, fallbackWarn: false })
  return translated !== key ? translated : label
}

export function unregisterTaskSuggestionText(
  item: { code?: string; detail: string },
  reasons: UnregisterTaskReason[],
  t: ComposerTranslation,
): string {
  if (['review_task', 'review_source_unregister'].includes(item.code || '')) {
    if (reasons.some(reason => reason.code === 'snapshot_in_use')) {
      return t('ops.task.failureDetails.suggestion.resolve_snapshot_usage')
    }
    if (reasons.some(reason => reason.code === 'agent_offline')) {
      return t('ops.task.failureDetails.suggestion.reconnect_unregister_agent')
    }
  }
  return unregisterCleanupSuggestion(item, t)
}

/** Historical events may carry only the generic error; use durable task reasons then. */
export function unregisterEventFailure(
  metadata: Record<string, unknown>,
  taskType: string | undefined,
  fallbackReasons: UnregisterTaskReason[],
  t: ComposerTranslation,
  fallbackResolutions: string[] = [],
) {
  const code = String(metadata.error_code || '')
  if (taskType !== 'source_unregister' && !code.startsWith('SOURCE_UNREGISTER_')) return null
  const eventReasons = Array.isArray(metadata.reasons) ? metadata.reasons : []
  if (!code && !metadata.error_message && !eventReasons.length) return null
  const reasons: UnregisterTaskReason[] = (eventReasons.length ? eventReasons : fallbackReasons)
    .flatMap((item) => {
      if (typeof item === 'string') return [{ detail: item }]
      if (!item || typeof item !== 'object') return []
      const row = item as UnregisterTaskReason
      return [{ ...row, detail: String(row.detail || row.code || '') }]
    })
  const specific = unregisterBusinessReasons(reasons, code).filter(reason =>
    !String(reason.code || '').startsWith('SOURCE_UNREGISTER_')
    && reason.detail !== 'Backup source was not deleted.',
  )
  const hasSpecificGuidance = reasons.some(reason => ['snapshot_in_use', 'agent_offline'].includes(reason.code || ''))
  const resolutions = fallbackResolutions.length
    ? fallbackResolutions
    : metadata.hint ? [unregisterCleanupText(String(metadata.hint), t)] : []
  return {
    summary: [...new Set(specific.map(reason => unregisterTaskReasonText(reason, t)))].filter(Boolean).join(' ')
      || unregisterCleanupText(String(metadata.error_message || 'Backup source was not deleted.'), t),
    resolutions: !hasSpecificGuidance && resolutions.length ? resolutions : [unregisterTaskSuggestionText(
      { code: 'review_source_unregister', detail: '' }, reasons, t,
    )],
  }
}
