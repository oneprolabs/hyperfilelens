import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { useI18n } from 'vue-i18n'

/** User-facing alert types (create + filter share this order). Default create type is Metric. */
export const ALERT_POLICY_TYPES = ['metric', 'health', 'task', 'event'] as const
export const DEFAULT_ALERT_POLICY_TYPE = ALERT_POLICY_TYPES[0]
export type AlertPolicyType = (typeof ALERT_POLICY_TYPES)[number]

/**
 * Canonical Resource Type order (hosts → data plane → jobs → platform services).
 * Dropdowns and filters share this order; Alert Type then narrows the visible set.
 */
export const ALERT_RESOURCE_TYPES = [
  'system',
  'agent_proxy',
  'sync_proxy',
  'gateway',
  'source_resource',
  'backup_repository',
  'target_storage',
  'task',
  'system_service',
] as const
export type AlertResourceType = (typeof ALERT_RESOURCE_TYPES)[number]

/** Resource types with a numeric metric catalog. */
export const METRIC_ALERT_RESOURCE_TYPES = [
  'system',
  'agent_proxy',
  'sync_proxy',
  'gateway',
  'source_resource',
  'backup_repository',
  'target_storage',
] as const

/** Hosts that report heartbeats for Health rules. */
export const HEALTH_ALERT_RESOURCE_TYPES = [
  'agent_proxy',
  'sync_proxy',
  'gateway',
] as const

export const TASK_ALERT_RESOURCE_TYPES = ['task'] as const

/** Event rules target platform/config/data entities, not host metric catalogs. */
export const EVENT_ALERT_RESOURCE_TYPES = [
  'system',
  'backup_repository',
  'task',
  'system_service',
] as const

/** Platform-only resource targets added for Admin Console Alerting. */
export const PLATFORM_ALERT_RESOURCE_TYPES = [
  'license',
  'user',
] as const

export function alertResourceTypesFor(
  alertType?: string | null,
  platform = false,
): readonly string[] {
  switch (alertType) {
    case 'metric':
      return METRIC_ALERT_RESOURCE_TYPES
    case 'health':
      return HEALTH_ALERT_RESOURCE_TYPES
    case 'task':
      return TASK_ALERT_RESOURCE_TYPES
    case 'event':
      return platform
        ? [...EVENT_ALERT_RESOURCE_TYPES, ...PLATFORM_ALERT_RESOURCE_TYPES]
        : EVENT_ALERT_RESOURCE_TYPES
    default:
      return platform
        ? [...ALERT_RESOURCE_TYPES, ...PLATFORM_ALERT_RESOURCE_TYPES]
        : ALERT_RESOURCE_TYPES
  }
}

export function isAllowedAlertResourceType(
  alertType: string | null | undefined,
  resourceType: string | null | undefined,
  platform = false,
): boolean {
  return !!resourceType && alertResourceTypesFor(alertType, platform).includes(resourceType)
}

export function defaultResourceTypeFor(alertType?: string | null, platform = false): string {
  return alertResourceTypesFor(alertType, platform)[0] ?? 'system'
}

export function isMetricAlertResourceType(type?: string | null): boolean {
  return !!type && (METRIC_ALERT_RESOURCE_TYPES as readonly string[]).includes(type)
}

export function useAlertPolicyLabels(options?: { platform?: MaybeRefOrGetter<boolean> }) {
  const { t, te } = useI18n()
  const platform = computed(() => Boolean(toValue(options?.platform)))

  function policyTypeLabel(type?: string | null): string {
    if (!type) return t('ops.task.emptyMark')
    // Legacy stored values still render after migration edge cases.
    if (type === 'availability') return t('ops.alertsCenter.policyTypes.health')
    if (type === 'system') return t('ops.alertsCenter.policyTypes.metric')
    const key = `ops.alertsCenter.policyTypes.${type}`
    const translated = t(key)
    return translated !== key ? translated : type
  }

  function resourceTypeLabel(type?: string | null): string {
    if (!type) return t('ops.task.emptyMark')
    const key = `ops.alertsCenter.resourceTypes.${type}`
    const translated = t(key)
    return translated !== key ? translated : type
  }

  function metricLabel(metricKey?: string | null): string {
    if (!metricKey) return t('ops.task.emptyMark')
    const key = `ops.alertsCenter.metrics.${metricKey}`
    const translated = t(key)
    return translated !== key ? translated : metricKey
  }

  function healthCheckLabel(checkType?: string | null): string {
    if (!checkType) return t('ops.task.emptyMark')
    const key = `ops.alertsCenter.healthChecks.${checkType}`
    const translated = t(key)
    return translated !== key ? translated : checkType
  }

  function catalogLabel(prefix: string, value?: string | null): string {
    if (!value) return t('ops.task.emptyMark')
    const key = `ops.alertsCenter.${prefix}.${value}`
    const translated = t(key)
    return translated !== key ? translated : value
  }

  const taskTypeLabel = (value?: string | null) => catalogLabel('taskTypes', value)
  const taskEventLabel = (value?: string | null) => catalogLabel('taskEvents', value)
  const eventCategoryLabel = (value?: string | null) => catalogLabel('eventCategories', value)
  const eventTypeLabel = (value?: string | null) => catalogLabel('events', value)

  function resourceTypeDescription(type?: string | null): string {
    if (!type) return ''
    const key = `ops.alertsCenter.resourceTypeDescriptions.${type}`
    if (!te(key) && !te(key, 'en')) return ''
    return t(key)
  }

  function policyTypeDescription(type?: string | null): string {
    if (!type) return ''
    if (type === 'availability') type = 'health'
    if (type === 'system') type = 'metric'
    const key = `ops.alertsCenter.policyTypeDescriptions.${type}`
    return te(key) || te(key, 'en') ? t(key) : ''
  }

  const policyTypeOptions = computed(() =>
    ALERT_POLICY_TYPES.map((value) => ({
      value,
      label: policyTypeLabel(value),
    })),
  )

  /** Full catalog in canonical order (list filters / labels). Prefer alertResourceTypesFor in editors. */
  const resourceTypeOptions = computed(() => {
    const values = platform.value
      ? [...ALERT_RESOURCE_TYPES, ...PLATFORM_ALERT_RESOURCE_TYPES]
      : [...ALERT_RESOURCE_TYPES]
    return values.map((value) => ({
      value,
      label: resourceTypeLabel(value),
    }))
  })

  function resourceTypeOptionsFor(alertType?: string | null) {
    return alertResourceTypesFor(alertType, platform.value).map((value) => ({
      value,
      label: resourceTypeLabel(value),
    }))
  }

  return {
    policyTypeLabel,
    resourceTypeLabel,
    metricLabel,
    healthCheckLabel,
    taskTypeLabel,
    taskEventLabel,
    eventCategoryLabel,
    eventTypeLabel,
    resourceTypeDescription,
    policyTypeDescription,
    policyTypeOptions,
    resourceTypeOptions,
    resourceTypeOptionsFor,
  }
}
