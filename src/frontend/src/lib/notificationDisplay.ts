import type { UserNotification } from './notificationApi'
import { formatQuotaBytes, isQuotaByteMeter, quotaMeterLabelKey } from './licenseQuotaDisplay'
import { taskAlertNotificationDisplay } from './operationalEventDisplay'

type Translate = (key: string, params?: Record<string, unknown>) => string
type Outcome = 'approved' | 'rejected' | 'failed' | 'cancelled'

const quotaOutcomes: Record<string, Outcome> = {
  'Quota increase approved': 'approved',
  'Quota increase rejected': 'rejected',
  'Quota increase apply failed': 'failed',
  'Quota request cancelled': 'cancelled',
}

/** Format known generated text; preserve unfamiliar messages and administrator notes. */
export function notificationDisplay(notification: UserNotification, t: Translate) {
  const fallback = taskAlertNotificationDisplay(notification, t)
  if (notification.kind !== 'quota_increase_request') return fallback
  const outcome = quotaOutcomes[notification.title]
  if (!outcome) return fallback
  const title = t(`notificationsPage.quota.${outcome}`)
  const summary = notification.summary || ''
  const meter = /^([a-z_]+): /.exec(summary)
  const labelKey = meter && quotaMeterLabelKey(meter[1]!)
  if (!meter || !labelKey) return { title, summary }
  const label = t(labelKey)
  const body = summary.slice(meter[0].length)
  const pattern = outcome === 'approved'
    ? /^limit is now (-?\d+)\.([\s\S]*)$/
    : outcome === 'rejected'
      ? /^request for (-?\d+) was rejected\.([\s\S]*)$/
      : null
  const match = pattern?.exec(body)
  if (match) {
    const value = Number(match[1])
    if (!Number.isSafeInteger(value) || value < -1) return { title, summary }
    const limit = value === -1
      ? t('dashboard.unlimited')
      : isQuotaByteMeter(meter[1]!) ? formatQuotaBytes(value) : value.toLocaleString()
    return {
      title,
      summary: t(`notificationsPage.quota.${outcome}Summary`, { meter: label, limit }) + match[2],
    }
  }
  const failedPrefix = 'approval was recorded, but applying the new limit failed. Please contact your platform administrator.'
  if (outcome === 'failed' && body.startsWith(failedPrefix)) {
    return { title, summary: t('notificationsPage.quota.failedSummary', { meter: label }) + body.slice(failedPrefix.length) }
  }
  // Preserve names and free-form notes, including punctuation, in cancellation messages.
  if (outcome === 'cancelled' && body.startsWith('your quota increase request was cancelled by ')) {
    return {
      title,
      summary: t('notificationsPage.quota.cancelledSummary', {
        meter: label, actor: body.slice('your quota increase request was cancelled by '.length),
      }),
    }
  }
  return { title, summary }
}

export function notificationOutcome(notification: UserNotification): Outcome | 'info' | 'warning' | 'error' {
  if (notification.kind === 'quota_increase_request' && quotaOutcomes[notification.title]) {
    return quotaOutcomes[notification.title]!
  }
  if (notification.severity === 'critical' || notification.severity === 'error') return 'error'
  if (notification.severity === 'warning') return 'warning'
  return 'info'
}
