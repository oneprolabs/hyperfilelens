export type StatusTagTone = 'success' | 'warning' | 'danger' | 'info' | 'primary' | 'firing' | 'neutral'

export type StatusTagAttrs = {
  type?: Exclude<StatusTagTone, 'neutral' | 'firing'>
  class?: string
}

/**
 * Shared visual semantics for status tags:
 * success = healthy/complete, info = active/in progress, warning = degraded,
 * danger = failed/unavailable, neutral = disabled/pending/cancelled/unknown.
 * Primary is reserved for selection or category emphasis, not runtime status.
 */
const STATUS_TAG_ATTRS: Record<StatusTagTone, StatusTagAttrs> = {
  success: { type: 'success' },
  warning: { type: 'warning' },
  danger: { type: 'danger' },
  info: { type: 'info' },
  primary: { type: 'primary' },
  firing: { class: 'hfl-tag--firing' },
  neutral: { class: 'hfl-tag--neutral' },
}

export function statusTagAttrs(tone: StatusTagTone): StatusTagAttrs {
  return STATUS_TAG_ATTRS[tone]
}

export function lifecycleStatusTone(status?: string | null): StatusTagTone {
  const normalized = String(status || '').trim().toLowerCase()
  if (['online', 'available', 'success', 'completed', 'active', 'healthy', 'ready', 'created'].includes(normalized)) {
    return 'success'
  }
  if (['offline', 'unavailable', 'failed', 'error', 'timeout', 'create_failed', 'delete_failed', 'remove_failed'].includes(normalized)) {
    return 'danger'
  }
  if (['partial', 'degraded', 'retry', 'retrying', 'warning'].includes(normalized)) {
    return 'warning'
  }
  if (['running', 'in_progress', 'mounting', 'creating', 'deleting', 'dispatching', 'reconnecting', 'removing', 'pending_install', 'installing', 'syncing', 'learning', 'provisioning', 'upgrading', 'restarting', 'verifying', 'cleaning_up'].includes(normalized)) {
    return 'info'
  }
  return 'neutral'
}

export function lifecycleStatusTagAttrs(status?: string | null): StatusTagAttrs {
  return statusTagAttrs(lifecycleStatusTone(status))
}

export function booleanStatusTag(enabled: boolean): StatusTagAttrs {
  const attrs = statusTagAttrs(enabled ? 'success' : 'neutral')
  return {
    ...attrs,
    class: attrs.class ? `${attrs.class} hfl-tag--boolean` : 'hfl-tag--boolean',
  }
}

export function severityStatusTagAttrs(severity?: string | null): StatusTagAttrs {
  const normalized = String(severity || '').trim().toLowerCase()
  if (normalized === 'critical' || normalized === 'high') return statusTagAttrs('danger')
  if (normalized === 'warning' || normalized === 'medium') return statusTagAttrs('warning')
  if (normalized === 'info' || normalized === 'informational' || normalized === 'low') {
    return statusTagAttrs('info')
  }
  return statusTagAttrs('neutral')
}

export function alertStatusTagAttrs(status?: string | null): StatusTagAttrs {
  const normalized = String(status || '').trim().toLowerCase()
  if (normalized === 'firing') return statusTagAttrs('firing')
  if (normalized === 'acknowledged') return statusTagAttrs('info')
  if (normalized === 'resolved') return statusTagAttrs('success')
  return statusTagAttrs('neutral')
}
