import { statusTagAttrs, type StatusTagTone } from './statusTag'

export type ResourceStatus =
  | 'active' | 'inactive' | 'creating' | 'updating'
  | 'verifying' | 'removing' | 'failed' | 'unknown'

const ENABLED: Record<string, ResourceStatus> = { active: 'active', inactive: 'inactive' }
/** Legacy API fallback, matching EE's explicit per-kind status projection. */
const LEGACY_STATUS_MAPS: Record<string, Record<string, ResourceStatus>> = {
  node: {
    active: 'active', upgrading: 'updating', restarting: 'updating',
    verifying: 'verifying', verification_pending: 'verifying',
    removing: 'removing', cleaning_up: 'removing', failed: 'failed',
    upgrade_failed: 'failed', deregistration_failed: 'failed',
  },
  repository: {
    created: 'active', creating: 'creating', removing: 'removing',
    create_failed: 'failed', remove_failed: 'failed',
  },
  source_resource: {
    active: 'active', inactive: 'inactive', probing: 'verifying',
    removing: 'removing', error: 'failed', remove_failed: 'failed',
  },
  backup_config: {
    active: 'active', provisioning: 'creating', resetting: 'updating',
    provision_failed: 'failed', reset_failed: 'failed',
  },
  restore_plan: ENABLED,
  backup_policy: ENABLED,
  file_filter_rule: ENABLED,
  alert_policy: ENABLED,
  notification_channel: ENABLED,
}
const TONES: Record<ResourceStatus, StatusTagTone> = {
  active: 'success', inactive: 'neutral', creating: 'info', updating: 'info',
  verifying: 'info', removing: 'warning', failed: 'danger', unknown: 'neutral',
}
type StatusInput = {
  resource_type?: string
  resource_status?: string | null
  status?: string | null
}
type Translate = (key: string) => string

function normalized(value: unknown): string {
  return typeof value === 'string' ? value.trim().toLowerCase() : ''
}

export function governanceResourceStatus(row: StatusInput): ResourceStatus {
  // A missing field indicates the old API; an invalid new value stays Unknown.
  if (row.resource_status !== undefined) {
    const value = normalized(row.resource_status)
    return Object.hasOwn(TONES, value) ? value as ResourceStatus : 'unknown'
  }
  const kind = normalized(row.resource_type)
  if (!Object.hasOwn(LEGACY_STATUS_MAPS, kind)) return 'unknown'
  const statuses = LEGACY_STATUS_MAPS[kind]
  const raw = normalized(row.status)
  return Object.hasOwn(statuses, raw) ? statuses[raw] : 'unknown'
}

export function governanceResourceStatusLabel(status: ResourceStatus, t: Translate): string {
  return t(`settings.resourceStatus.${status}`)
}

export function governanceResourceStatusTagAttrs(status: ResourceStatus) {
  return statusTagAttrs(TONES[status])
}

export function governanceResourceStatusDetail(row: StatusInput, t: Translate): string {
  const kind = normalized(row.resource_type)
  const raw = normalized(row.status)
  if (!Object.hasOwn(LEGACY_STATUS_MAPS, kind)) return ''
  const statuses = LEGACY_STATUS_MAPS[kind]
  if (!Object.hasOwn(statuses, raw)) return ''
  if (statuses === ENABLED) {
    return t(`settings.resourceStatusDetails.${raw === 'active' ? 'enabled' : 'disabled'}`)
  }
  if (raw === governanceResourceStatus(row)) return ''
  // Only known enum keys become tooltip text; never echo diagnostics or secrets.
  return t(`settings.resourceStatusDetails.${raw}`)
}
