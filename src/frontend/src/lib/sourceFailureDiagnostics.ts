import type { ComposerTranslation } from 'vue-i18n'
import { getNode } from './nodeApi'
import { getSourceResource } from './sourceApi'
import { getTask, listTasks, type TaskRow } from './taskApi'
import type { ErrorDetailsPayload } from './errors/details'
import { unregisterFailureToErrorDetails } from './unregisterFailureDetails'
import { humanizeLegacyErrorMessage } from './errors'
import { ERROR_CODE_I18N_KEYS } from './errors/registry'
import { snapshotUsageFailureText, type SnapshotUsageReason } from './snapshotUsageFailureDisplay'
import { taskStepTranslationKey } from './taskStepDisplay'

function translated(t: ComposerTranslation, key?: string, params: Record<string, unknown> = {}) {
  if (!key) return undefined
  const value = t(key, params, { missingWarn: false, fallbackWarn: false })
  return value === key ? undefined : value
}

function diagnosticText(t: ComposerTranslation, item: SnapshotUsageReason, kind: 'reason' | 'suggestion') {
  if (kind === 'reason') {
    const usage = snapshotUsageFailureText(item, t)
    if (usage) return usage
    if (['AGENT_PACKAGE_DOWNLOAD_INTERRUPTED', 'AGENT_PACKAGE_DOWNLOAD_PROGRESS_TIMEOUT'].includes(item.code || '')) {
      return t('nodeUpgradeProgress.packageDownloadInterrupted')
    }
  }
  return translated(t, `ops.task.failureDetails.${kind}.${item.code || ''}`, { count: (item as { count?: number }).count || 0 })
    || (kind === 'reason' ? translated(t, ERROR_CODE_I18N_KEYS[item.code || '']) : undefined)
    || translated(t, `ops.task.failureDetails.${kind}_message.${item.detail.replace(/\.+$/, '')}`)
    || humanizeLegacyErrorMessage(item.detail, t)
}

function failureSummary(t: ComposerTranslation, sourceName: string, status: string) {
  return t('protection.backupsPage.sourceFailureSummary', { name: sourceName, status })
}

export function isSourceFailure(status?: string): boolean {
  return Boolean(status && (
    ['error', 'failed', 'remove_failed', 'deregistration_failed'].includes(status)
    || status.endsWith('_failed')
  ))
}

export function isUnregisterFailure(status?: string): boolean {
  return status === 'remove_failed' || status === 'deregistration_failed'
}

function sourceIdentity(sourceId: string) {
  const match = /^(agent|nas):([1-9]\d*)$/.exec(sourceId)
  if (!match) throw new Error('Invalid source identity')
  return { type: match[1]!, id: Number(match[2]) }
}

/** Query the latest operation, NOT the latest failure (which may be obsolete). */
export async function latestUnregisterFailure(
  sourceId: string,
  init?: RequestInit,
): Promise<TaskRow | null> {
  return latestSourceTaskFailure(sourceId, 'source_unregister', init)
}

async function latestSourceTaskFailure(
  sourceId: string,
  taskType: string,
  init?: RequestInit,
): Promise<TaskRow | null> {
  const source = sourceIdentity(sourceId)
  const page = await listTasks({
    task_type: taskType,
    resource_type: 'backup_source',
    resource_subtype: source.type,
    resource_id: source.id,
    page: 1,
    page_size: 1,
  }, init)
  const latest = page.results[0]
  if (!latest || !['failed', 'timeout'].includes(latest.status)) return null
  const task = await getTask(latest.task_uuid, init)
  return task.task_type === taskType
    && ['failed', 'timeout'].includes(task.status)
    && task.resources?.some(resource =>
      resource.resource_type === 'backup_source'
      && resource.resource_subtype === source.type
      && resource.resource_id === source.id)
    ? task : null
}

function taskDetails(t: ComposerTranslation, sourceId: string, sourceName: string, task: TaskRow): ErrorDetailsPayload {
  const contract = task.error_details
  const operation = (task.request_payload as { operation?: string } | null)?.operation
  const status = task.task_type === 'backup_config_provision'
    ? t('ops.task.failureDetails.provisionFailedTitle')
    : task.task_type === 'backup_config_reset'
      ? t('protection.backupsPage.resetStatusFailed')
      : task.task_type === 'node_lifecycle' && operation === 'upgrade'
        ? t('nodeLifecycle.state.upgrade_failed')
        : t('ops.task.failureDetails.failureTitle')
  const step = contract?.failed_step || task.current_step
  return {
    title: t('protection.backupsPage.sourceFailureDetailsTitle'),
    summary: failureSummary(t, sourceName, status),
    severity: contract?.severity || 'error',
    taskUuid: task.task_uuid,
    taskType: task.task_type,
    taskAttempt: task.retry_count,
    failedStep: translated(t, taskStepTranslationKey(step, task.task_type) || undefined) || step || undefined,
    errorCode: contract?.error_code || task.error_code || undefined,
    traceId: contract?.correlation_id || undefined,
    reasons: contract?.reasons?.length
      ? contract.reasons.map(reason => diagnosticText(t, reason, 'reason'))
      : [diagnosticText(t, {
        code: contract?.error_code || task.error_code || undefined,
        detail: task.error_message || t('protection.backupsPage.sourceFailureDetailsUnavailable'),
      }, 'reason')],
    resolutions: contract?.suggestions?.map(suggestion => diagnosticText(t, suggestion, 'suggestion')),
    entities: [{ id: sourceId, name: sourceName, type: 'source' }],
    rawDetail: { error_details: contract, error_message: task.error_message, failed_step: step },
  }
}

function missingDetails(t: ComposerTranslation, sourceId: string, sourceName: string): ErrorDetailsPayload {
  return {
    title: t('protection.backupsPage.sourceFailureDetailsTitle'),
    summary: t('protection.backupsPage.sourceFailureDetailsUnavailable'),
    severity: 'warning',
    reasons: [t('protection.backupsPage.sourceFailureDetailsUnavailable')],
    entities: [{ id: sourceId, name: sourceName, type: 'source' }],
  }
}

/** Read-only diagnostics. Never writes pending operations or emits task notices. */
export async function resolveSourceFailureDetails(input: {
  t: ComposerTranslation
  sourceId: string
  sourceName: string
  status?: string
  init?: RequestInit
}): Promise<ErrorDetailsPayload> {
  const { t, sourceId, sourceName, init } = input
  const source = sourceIdentity(sourceId)
  if (isUnregisterFailure(input.status)) {
    const task = await latestUnregisterFailure(sourceId, init)
    return task ? unregisterFailureToErrorDetails({ t, sourceId, sourceName, task })
      : missingDetails(t, sourceId, sourceName)
  }
  const configTaskType = input.status === 'provision_failed' ? 'backup_config_provision'
    : input.status === 'reset_failed' ? 'backup_config_reset' : null
  if (configTaskType) {
    const task = await latestSourceTaskFailure(sourceId, configTaskType, init)
    return task ? taskDetails(t, sourceId, sourceName, task) : missingDetails(t, sourceId, sourceName)
  }

  if (source.type === 'nas') {
    const resource = await getSourceResource(source.id, init)
    const message = resource.effective_status_message || resource.status_message
    return message ? {
      ...missingDetails(t, sourceId, sourceName),
      severity: 'error',
      summary: failureSummary(t, sourceName, t('ops.task.failureDetails.failureTitle')),
      reasons: [diagnosticText(t, { detail: message }, 'reason')],
    } : missingDetails(t, sourceId, sourceName)
  }

  // Selectable rows contain only a status. Fetch authoritative lifecycle identity.
  const node = await getNode(source.id, init)
  const lifecycle = node.lifecycle
  if (!lifecycle || lifecycle.state !== 'failed') return missingDetails(t, sourceId, sourceName)
  if (input.status === 'upgrade_failed' && lifecycle.kind !== 'upgrade') {
    return missingDetails(t, sourceId, sourceName)
  }
  if (lifecycle.task_uuid) {
    const task = await getTask(lifecycle.task_uuid, init)
    const belongsToSource = task.resources?.some(resource =>
      resource.resource_id === source.id
      && resource.resource_subtype === 'agent'
      && ['host', 'backup_source'].includes(resource.resource_type))
    const payload = task.request_payload as { operation?: string } | null
    const correctOperation = lifecycle.kind === 'remove'
      ? task.task_type === 'source_unregister'
        || task.task_type === 'node_lifecycle' && payload?.operation === 'remove'
      : task.task_type === 'node_lifecycle' && payload?.operation === 'upgrade'
    if (belongsToSource && correctOperation && ['failed', 'timeout'].includes(task.status)) {
      if (task.task_type === 'source_unregister') {
        return unregisterFailureToErrorDetails({ t, sourceId, sourceName, task })
      }
      return taskDetails(t, sourceId, sourceName, task)
    }
    return missingDetails(t, sourceId, sourceName)
  }
  // Legacy lifecycle records may lack a projected Operations task.
  return lifecycle.error || lifecycle.failure_code ? {
    ...missingDetails(t, sourceId, sourceName),
    severity: 'error',
    summary: failureSummary(t, sourceName, t(lifecycle.kind === 'upgrade'
      ? 'nodeLifecycle.state.upgrade_failed' : 'nodeLifecycle.state.deregistration_failed')),
    errorCode: lifecycle.failure_code || undefined,
    reasons: [diagnosticText(t, {
      code: lifecycle.failure_code || undefined,
      detail: lifecycle.error || lifecycle.failure_code || '',
    }, 'reason')],
    rawDetail: lifecycle,
  } : missingDetails(t, sourceId, sourceName)
}
