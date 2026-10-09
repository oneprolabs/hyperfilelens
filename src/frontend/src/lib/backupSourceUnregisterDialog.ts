import type { ComposerTranslation } from 'vue-i18n'
import type { BackupSourceDeletePreflight, BackupSourceDeleteReason } from './sourceApi'
import { humanizeLegacyErrorMessage } from './errors'
import { snapshotUsageFailureText } from './snapshotUsageFailureDisplay'

export function mergeUnregisterSubmitRisks(
  preflight: BackupSourceDeletePreflight | null,
  submitErrorReasons: BackupSourceDeleteReason[],
): BackupSourceDeleteReason[] {
  const seen = new Set<string>()
  const merged: BackupSourceDeleteReason[] = []
  for (const row of [...(preflight?.risks || []), ...submitErrorReasons]) {
    const key = `${row.code}:${row.source_id || ''}:${row.detail || ''}`
    if (seen.has(key)) continue
    seen.add(key)
    merged.push(row)
  }
  return merged
}

export type BackupSourceUnregisterDisplayRow = {
  id: string
  name: string
  type?: string
  sourceType?: 'host' | 'nas'
  platform?: string
  protocol?: 'nfs' | 'smb'
  statusLabel?: string
  statusTag?: 'success' | 'warning' | 'danger' | 'info' | 'neutral'
  registeredAt?: string
  snapshotCount?: string | number
}

export function buildUnregisterImpactItems(
  t: ComposerTranslation,
  opts: { hasAgent: boolean; hasNas: boolean; isStep3: boolean },
): string[] {
  const items = [
    t('protection.backupsPage.deleteImpactItemConfigs'),
    t('protection.backupsPage.deleteImpactItemRecords'),
  ]
  if (opts.hasAgent) {
    items.push(t('protection.backupsPage.deleteImpactItemAgentOnline'))
  }
  if (opts.hasNas) {
    items.push(t('protection.backupsPage.deleteImpactItemNas'))
  }
  if (opts.isStep3) {
    items.push(t('protection.backupsPage.deleteImpactItemStep3Task'))
  }
  items.push(t('protection.backupsPage.deleteImpactItemIrreversible'))
  items.push(t('protection.backupsPage.deleteImpactItemRetained'))
  return items
}

export function unregisterReasonLabel(
  reason: BackupSourceDeleteReason,
  t: ComposerTranslation,
): string {
  const name = reason.source_name || reason.source_id || ''
  switch (reason.code) {
    case 'snapshot_in_use':
      return snapshotUsageFailureText(reason, t)
        || t('ops.task.failureDetails.reason.snapshot_in_use')
    case 'agent_offline':
      return t('protection.backupsPage.deleteReasonAgentOffline', { name })
    case 'proxy_offline':
      return t('protection.backupsPage.deleteReasonProxyOffline', { name })
    case 'proxy_unbound':
      return t('protection.backupsPage.deleteReasonProxyUnbound', { name })
    case 'repository_snapshot_delete_failed':
      return reason.detail
        ? humanizeLegacyErrorMessage(reason.detail, t)
        : t('protection.backupsPage.deleteReasonRepository', {
            repo: reason.repository_name || reason.repository_id || '',
          })
    case 'repository_unreachable':
      return t('protection.backupsPage.deleteReasonRepositoryUnreachable', {
        repo: reason.repository_name || String(reason.repository_id || ''),
      })
    case 'running_tasks':
      return t('protection.backupsPage.deleteReasonRunningTasks', { name })
    case 'lifecycle_in_progress':
      return t('protection.backupsPage.deleteReasonLifecycleInProgress', { name })
    case 'node_workload_active':
      return reason.detail || t('protection.backupsPage.deleteReasonNodeWorkload', { name })
    case 'nas_umount_failed':
      return t('protection.backupsPage.deleteReasonNasUmountFailed', {
        name,
        detail: humanizeLegacyErrorMessage(reason.detail || '', t),
      })
    default:
      return reason.detail
        ? humanizeLegacyErrorMessage(reason.detail, t)
        : reason.code
  }
}
