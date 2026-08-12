import type { ComposerTranslation } from 'vue-i18n'

import { unregisterReasonLabel } from './backupSourceUnregisterDialog'
import type { ErrorDetailsPayload } from './errors/details'
import { openErrorDetails } from './errors/details'
import { notifyError, notifyWarning } from './notify'
import {
  parseBackupSourceDeleteError,
  type BackupSourceDeleteReason,
} from './sourceApi'
import {
  sourceUnregisterTaskOutcome,
  type SourceUnregisterTaskOutcome,
} from './sourceUnregisterMonitor'
import type { TaskRow } from './taskApi'
import {
  taskCleanupFailures,
  taskCleanupWarnings,
  taskFailedCleanupChildren,
  taskRetainedResources,
} from './taskOutcomeDisplay'

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function reasonObjects(value: unknown): BackupSourceDeleteReason[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (typeof item === 'string') {
      const detail = item.trim()
      return detail ? [{ code: 'unregister_failed', detail }] : []
    }
    if (!item || typeof item !== 'object') return []
    const row = item as Record<string, unknown>
    const code = String(row.code || 'unregister_failed').trim()
    const detail = String(row.detail || row.message || '').trim()
    if (!code && !detail) return []
    return [{
      code: code || 'unregister_failed',
      detail: detail || code,
      source_id: String(row.source_id || '').trim() || undefined,
      source_name: String(row.source_name || '').trim() || undefined,
      repository_id: Number(row.repository_id || 0) || undefined,
      repository_name: String(row.repository_name || '').trim() || undefined,
    }]
  })
}

function uniqueStrings(values: Array<string | undefined | null>): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const value of values) {
    const text = String(value || '').trim()
    if (!text || seen.has(text)) continue
    seen.add(text)
    out.push(text)
  }
  return out
}

function sourceLabel(sourceId?: string, sourceName?: string) {
  return String(sourceName || sourceId || '').trim()
}

/** Keep reasons that belong to one source; share unscoped reasons when nothing is tagged. */
export function reasonsForUnregisterSource(
  reasons: BackupSourceDeleteReason[],
  sourceId?: string,
): BackupSourceDeleteReason[] {
  if (!sourceId) return reasons
  const matched = reasons.filter((reason) => reason.source_id === sourceId)
  if (matched.length) return matched
  if (!reasons.some((reason) => reason.source_id)) return reasons
  return []
}

/** Keep warnings that belong to one source; share unscoped warnings when nothing is tagged. */
export function warningsForUnregisterSource(warnings: unknown, sourceId?: string): BackupSourceDeleteReason[] {
  const all = reasonObjects(warnings)
  if (!sourceId) return all
  return reasonsForUnregisterSource(all, sourceId)
}

function warningDetailLines(
  warnings: BackupSourceDeleteReason[],
  t: ComposerTranslation,
): string[] {
  return warnings.map((reason) => {
    const labeled = unregisterReasonLabel(reason, t)
    if (labeled && labeled !== reason.code) return labeled
    return reason.detail || reason.code
  })
}

export function unregisterFailureToErrorDetails(input: {
  t: ComposerTranslation
  sourceId?: string
  sourceName?: string
  task?: TaskRow | null
  outcome?: SourceUnregisterTaskOutcome | null
  apiError?: unknown
  fallbackMessage?: string
  /** Sync Force Cleanup / partial_success residue lines. */
  warnings?: unknown
  retainedResources?: string[]
  partialSuccess?: boolean
}): ErrorDetailsPayload {
  const { t } = input
  const source = sourceLabel(input.sourceId, input.sourceName)
  const fallback = input.fallbackMessage || t('protection.backupsPage.msgDeleteSourceFailed')
  const syncWarnings = reasonObjects(input.warnings)
  const syncRetained = (input.retainedResources || [])
    .map((item) => String(item || '').trim())
    .filter(Boolean)

  if (input.apiError != null) {
    const parsed = parseBackupSourceDeleteError(input.apiError)
    const scopedReasons = reasonsForUnregisterSource(parsed.reasons, input.sourceId)
    const reasons = uniqueStrings([
      ...scopedReasons.map((reason) => unregisterReasonLabel(reason, t)),
      // Prefer scoped reason text; keep API message only when it adds signal.
      scopedReasons.length ? '' : parsed.message,
    ])
    const resolutions = uniqueStrings([
      parsed.hint,
      t('protection.backupsPage.unregisterFailureRetryHint'),
    ])
    const summary = source
      ? t('protection.backupsPage.unregisterFailureSummaryNamed', { name: source })
      : (reasons[0] || parsed.message || fallback)
    return {
      title: t('protection.backupsPage.unregisterFailureTitle'),
      summary,
      issue: summary,
      errorCode: undefined,
      reasons: reasons.length ? reasons : [parsed.message || fallback],
      resolutions,
      rawDetail: {
        source_id: input.sourceId,
        source_name: input.sourceName,
        hint: parsed.hint,
        reasons: scopedReasons,
        message: parsed.message,
      },
    }
  }

  const task = input.task || null
  const outcome = input.outcome || (task ? sourceUnregisterTaskOutcome(task) : null)
  const payload = record(task?.result_payload)
  const structuredReasons = reasonObjects(payload.reasons)
  const eventHints = (task?.recent_events || []).flatMap((event) => {
    const meta = record(event.metadata)
    const hints = [
      ...reasonObjects(meta.reasons).map((reason) => unregisterReasonLabel(reason, t)),
      typeof meta.hint === 'string' ? meta.hint : '',
      event.level === 'error' ? String(event.message || '').trim() : '',
    ]
    return hints
  })
  const cleanupFailures = outcome?.cleanupFailures ?? (task ? taskCleanupFailures(task) : [])
  const cleanupWarnings = outcome?.cleanupWarnings ?? (task ? taskCleanupWarnings(task) : [])
  const retained = uniqueStrings([
    ...(outcome?.retainedResources ?? (task ? taskRetainedResources(task) : [])),
    ...syncRetained,
  ])
  const failedChildren = outcome?.failedChildren ?? (task ? taskFailedCleanupChildren(task) : [])
  const taskSucceeded = String(task?.status || '').trim().toLowerCase() === 'success'
  const failedStep = String(
    outcome?.failedStep
    || payload.failed_step
    || (taskSucceeded ? '' : task?.current_step)
    || '',
  ).trim()
  const hint = String(outcome?.hint || payload.hint || '').trim()
  const errorMessage = String(
    outcome?.errorMessage || task?.error_message || task?.error_code || '',
  ).trim()
  const errorCode = String(outcome?.errorCode || task?.error_code || '').trim() || undefined
  const taskUuid = String(outcome?.taskUuid || task?.task_uuid || '').trim()

  const isResidue = Boolean(
    input.partialSuccess
    || outcome?.partialSuccess
    || (outcome?.success && !outcome.cleanupComplete)
    || syncWarnings.length
    || syncRetained.length,
  )

  const reasons = uniqueStrings([
    !isResidue && failedStep
      ? t('protection.backupsPage.unregisterFailureFailedStep', { step: failedStep })
      : '',
    ...structuredReasons.map((reason) => unregisterReasonLabel(reason, t)),
    ...cleanupFailures.map((item) => {
      const target = item.sourceName || item.sourceId || ''
      return target
        ? `${item.detail} (${target})`
        : item.detail
    }),
    ...(!isResidue
      ? failedChildren.map((item) =>
          t('protection.backupsPage.unregisterFailureChildTask', {
            uuid: item.taskUuid,
            error: item.error,
          }))
      : []),
    ...eventHints,
    isResidue ? '' : errorMessage,
  ])

  const residueLines = uniqueStrings([
    ...warningDetailLines(syncWarnings, t),
    ...cleanupWarnings.map((item) => item.detail),
    ...retained.map((item) =>
      t('protection.backupsPage.unregisterFailureRetainedResource', { resource: item }),
    ),
  ])

  const resolutions = uniqueStrings([
    hint,
    residueLines.length || isResidue
      ? t('protection.backupsPage.unregisterFailureResidueHint')
      : '',
    isResidue
      ? t('protection.backupsPage.unregisterFailureForceCleanupHint')
      : t('protection.backupsPage.unregisterFailureStrictRetryHint'),
    isResidue ? '' : t('protection.backupsPage.unregisterFailureRetryHint'),
  ])

  const summary = isResidue
    ? (source
      ? t('protection.backupsPage.unregisterCleanupWarningSummaryNamed', { name: source })
      : t('protection.backupsPage.msgDeleteSourcePartialSuccess'))
    : (source
      ? t('protection.backupsPage.unregisterFailureSummaryNamed', { name: source })
      : (reasons[0] || fallback))

  const combinedReasons = uniqueStrings([...reasons, ...residueLines])
  return {
    title: isResidue
      ? t('protection.backupsPage.unregisterCleanupWarningTitle')
      : t('protection.backupsPage.unregisterFailureTitle'),
    summary,
    issue: summary,
    errorCode,
    reasons: combinedReasons.length
      ? combinedReasons
      : [isResidue ? t('protection.backupsPage.msgDeleteSourcePartialSuccess') : fallback],
    resolutions,
    rawDetail: {
      source_id: input.sourceId,
      source_name: input.sourceName,
      task_uuid: taskUuid || undefined,
      failed_step: failedStep || undefined,
      hint: hint || undefined,
      error_code: errorCode,
      error_message: errorMessage || undefined,
      cleanup_complete: outcome?.cleanupComplete,
      cleanup_failures: cleanupFailures,
      cleanup_warnings: cleanupWarnings,
      retained_resources: retained,
      failed_children: failedChildren,
      warnings: syncWarnings,
      reasons: structuredReasons,
      partial_success: isResidue || undefined,
    },
  }
}

/** Build per-source details for a sync unregister API failure. */
export function unregisterSyncFailuresBySource(input: {
  t: ComposerTranslation
  sourceIds: string[]
  sourceName?: (sourceId: string) => string
  apiError: unknown
}): Map<string, ErrorDetailsPayload> {
  const out = new Map<string, ErrorDetailsPayload>()
  for (const sourceId of input.sourceIds) {
    out.set(sourceId, unregisterFailureToErrorDetails({
      t: input.t,
      sourceId,
      sourceName: input.sourceName?.(sourceId) || sourceId,
      apiError: input.apiError,
    }))
  }
  return out
}

export function notifyUnregisterFailure(input: {
  t: ComposerTranslation
  sourceId?: string
  sourceName?: string
  task?: TaskRow | null
  outcome?: SourceUnregisterTaskOutcome | null
  apiError?: unknown
  fallbackMessage?: string
  details?: ErrorDetailsPayload
  dedupeKey?: string
}) {
  const details = input.details || unregisterFailureToErrorDetails(input)
  return notifyError({
    title: details.title,
    message: details.summary,
    duration: 12000,
    dedupeKey: input.dedupeKey || `unregister-failure:${input.sourceId || details.summary}`,
    showDetails: true,
    details,
  })
}

export function notifyUnregisterCleanupWarning(input: {
  t: ComposerTranslation
  sourceId?: string
  sourceName?: string
  task?: TaskRow | null
  outcome?: SourceUnregisterTaskOutcome | null
  warnings?: unknown
  retainedResources?: string[]
  dedupeKey?: string
}) {
  const details = unregisterFailureToErrorDetails({
    ...input,
    partialSuccess: true,
  })
  return notifyWarning({
    title: details.title,
    message: details.summary,
    duration: 12000,
    dedupeKey: input.dedupeKey || `unregister-cleanup-warning:${input.sourceId || details.summary}`,
    showDetails: true,
    details,
  })
}

export function openUnregisterFailureDetails(details: ErrorDetailsPayload) {
  openErrorDetails(details)
}

export function unregisterFailureSummaryLine(details?: ErrorDetailsPayload | null): string {
  if (!details) return ''
  if (details.reasons?.length) return details.reasons[0] || details.summary
  return details.summary || details.issue || ''
}

/** Prefer the composed summary for banners / toasts over the first reason line. */
export function unregisterFailureBannerText(details?: ErrorDetailsPayload | null): string {
  if (!details) return ''
  return details.summary || details.issue || unregisterFailureSummaryLine(details)
}

function uniqueDetailList(values: Array<string | undefined>): string[] {
  return uniqueStrings(values)
}

/** Merge multiple per-source details into one toast / retry banner payload. */
export function mergeUnregisterDetails(
  t: ComposerTranslation,
  items: ErrorDetailsPayload[],
  kind: 'failure' | 'cleanup_warning' = 'failure',
): ErrorDetailsPayload {
  const isWarning = kind === 'cleanup_warning'
  if (!items.length) {
    const summary = isWarning
      ? t('protection.backupsPage.msgDeleteSourcePartialSuccess')
      : t('protection.backupsPage.msgDeleteSourceFailed')
    return {
      title: isWarning
        ? t('protection.backupsPage.unregisterCleanupWarningTitle')
        : t('protection.backupsPage.unregisterFailureTitle'),
      summary,
      issue: summary,
      reasons: [summary],
      resolutions: [],
      rawDetail: { count: 0, sources: [] },
    }
  }
  if (items.length === 1) return items[0]
  const summary = isWarning
    ? t('protection.backupsPage.unregisterCleanupWarningSummaryCount', { n: items.length })
    : t('protection.backupsPage.unregisterFailureSummaryCount', { n: items.length })
  return {
    title: isWarning
      ? t('protection.backupsPage.unregisterCleanupWarningTitle')
      : t('protection.backupsPage.unregisterFailureTitle'),
    summary,
    issue: summary,
    reasons: uniqueDetailList(items.flatMap((item) => item.reasons || [])),
    resolutions: uniqueDetailList(items.flatMap((item) => item.resolutions || [])),
    rawDetail: {
      count: items.length,
      sources: items.map((item) => item.rawDetail),
    },
  }
}

export function notifyUnregisterFailureBatch(input: {
  t: ComposerTranslation
  items: Array<{
    sourceId?: string
    sourceName?: string
    details: ErrorDetailsPayload
  }>
  dedupeKey?: string
}) {
  if (!input.items.length) return { close: () => undefined }
  if (input.items.length === 1) {
    const only = input.items[0]
    return notifyUnregisterFailure({
      t: input.t,
      sourceId: only.sourceId,
      sourceName: only.sourceName,
      details: only.details,
      dedupeKey: input.dedupeKey || `unregister-failure:${only.sourceId || only.details.summary}`,
    })
  }
  const merged = mergeUnregisterDetails(
    input.t,
    input.items.map((item) => item.details),
    'failure',
  )
  return notifyUnregisterFailure({
    t: input.t,
    details: merged,
    dedupeKey: input.dedupeKey
      || `unregister-failure-batch:${input.items.map((item) => item.sourceId || '').join(',')}`,
  })
}

export function notifyUnregisterCleanupWarningBatch(input: {
  t: ComposerTranslation
  items: Array<{
    sourceId?: string
    sourceName?: string
    details: ErrorDetailsPayload
  }>
  dedupeKey?: string
}) {
  if (!input.items.length) return { close: () => undefined }
  if (input.items.length === 1) {
    const only = input.items[0]
    return notifyWarning({
      title: only.details.title,
      message: only.details.summary,
      duration: 12000,
      dedupeKey: input.dedupeKey
        || `unregister-cleanup-warning:${only.sourceId || only.details.summary}`,
      showDetails: true,
      details: only.details,
    })
  }
  const merged = mergeUnregisterDetails(
    input.t,
    input.items.map((item) => item.details),
    'cleanup_warning',
  )
  return notifyWarning({
    title: merged.title,
    message: merged.summary,
    duration: 12000,
    dedupeKey: input.dedupeKey
      || `unregister-cleanup-warning-batch:${input.items.map((item) => item.sourceId || '').join(',')}`,
    showDetails: true,
    details: merged,
  })
}

/** Combine persisted per-source failures for a multi-source retry dialog. */
export function previousUnregisterFailureDetails(
  t: ComposerTranslation,
  detailsList: ErrorDetailsPayload[],
): ErrorDetailsPayload | null {
  if (!detailsList.length) return null
  if (detailsList.length === 1) {
    const only = detailsList[0]
    return {
      ...only,
      title: t('protection.backupsPage.unregisterPreviousFailureTitle'),
      // Keep summary/issue/reasons from the original failure for the banner body + details.
    }
  }
  const merged = mergeUnregisterDetails(t, detailsList, 'failure')
  return {
    ...merged,
    title: t('protection.backupsPage.unregisterPreviousFailureTitleCount', {
      n: detailsList.length,
    }),
  }
}
