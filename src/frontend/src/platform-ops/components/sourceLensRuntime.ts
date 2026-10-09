import { formatLocalDateTime } from '../../lib/dateTime'
import type { SourceLensRuntimeProbe } from '../lib/platformOpsApi'
import type { RuntimeStatusCell, RuntimeStatusNotice } from './runtimeStatus'

type Translate = (key: string, params: Record<string, string | number>) => string

const noticeKeys: Record<string, string> = {
  index_corruption: 'indexCorruption',
  index_corruption_history: 'indexCorruptionHistory',
  index_corruption_undated: 'indexCorruptionUndated',
  index_corruption_unconfirmed: 'indexCorruptionUnconfirmed',
  index_scan_incomplete: 'indexScanIncomplete',
  index_logs_unavailable: 'indexLogsUnavailable',
  queue_backlog: 'queueBacklog',
  queue_not_configured: 'queueNotConfigured',
  queue_config_invalid: 'queueConfigInvalid',
  queue_probe_failed: 'queueProbeFailed',
  queue_metrics_unavailable: 'queueMetricsUnavailable',
  queue_auto_unavailable: 'queueAutoUnavailable',
}

export function sourceLensQueueDetails(probe: SourceLensRuntimeProbe, t: Translate): string[] {
  const warned = new Set((probe.notices || [])
    .filter(notice => notice.code === 'queue_backlog')
    .map(notice => String(notice.params.queue || '')))
  const details = Object.entries(probe.queue_lengths || {})
    .filter(([queue]) => !warned.has(queue))
    .map(([queue, count]) => t(
    'platformOps.settings.environment.sourceLensMonitor.queueSummary', { queue, count },
  ))
  if (probe.checked_at) {
    details.push(`${t('platformOps.settings.environment.checkedAt', {})}: ${formatLocalDateTime(probe.checked_at, '—')}`)
  }
  return details
}

/** Reuse the table's existing notice styling; never expose raw upstream logs. */
export function sourceLensNotices(
  probes: SourceLensRuntimeProbe[],
  t: Translate,
  alertsOnly = false,
): RuntimeStatusNotice[] {
  return probes.flatMap(probe => (probe.notices || []).flatMap(notice => {
    const key = noticeKeys[notice.code]
    if (!key || (alertsOnly && notice.level === 'info')) return []
    const params = { ...notice.params }
    if (params.last_seen_at) {
      params.last_seen_at = formatLocalDateTime(String(params.last_seen_at), '—')
    }
    return [{
      level: notice.level,
      message: t(`platformOps.settings.environment.sourceLensMonitor.${key}`, params),
    }]
  }))
}

/** Match Runtime Environment's current labels and semantic status tones. */
export function sourceLensStatusCell(
  status: string,
  column: 'health' | 'availability',
  t: Translate,
): RuntimeStatusCell {
  const prefix = 'platformOps.settings.environment.'
  if (status === 'ok') {
    return {
      label: t(column === 'health' ? 'platformOps.integrations.healthy' : `${prefix}statusOperational`, {}),
      type: 'success',
    }
  }
  if (status === 'error') {
    return {
      label: t(`${prefix}${column === 'health' ? 'healthUnhealthy' : 'statusUnavailable'}`, {}),
      type: 'danger',
    }
  }
  if (status === 'degraded') {
    return {
      label: t(`${prefix}${column === 'health' ? 'healthUnhealthy' : 'statusDegraded'}`, {}),
      type: 'warning',
    }
  }
  return {
    label: column === 'health' ? t(`${prefix}statusNotMonitored`, {}) : '—',
    type: 'info',
  }
}
