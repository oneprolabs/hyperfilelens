import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

export const ALERT_POLICY_TYPES = ['metric', 'task', 'event', 'system', 'availability'] as const
export type AlertPolicyType = (typeof ALERT_POLICY_TYPES)[number]

export const ALERT_RESOURCE_TYPES = [
  'task',
  'system',
  'sync_proxy',
  'gateway',
  'agent_proxy',
  'backup_repository',
  'source_resource',
  'target_storage',
  'system_service',
] as const
export type AlertResourceType = (typeof ALERT_RESOURCE_TYPES)[number]

export function useAlertPolicyLabels() {
  const { t, te } = useI18n()

  function policyTypeLabel(type?: string | null): string {
    if (!type) return t('ops.task.emptyMark')
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

  function resourceTypeDescription(type?: string | null): string {
    if (!type) return ''
    const key = `ops.alertsCenter.resourceTypeDescriptions.${type}`
    // Descriptions are optional; querying a missing key emits an intlify warning
    // even if we subsequently replace the returned key with an empty string.
    if (!te(key) && !te(key, 'en')) return ''
    return t(key)
  }

  function policyTypeDescription(type?: string | null): string {
    if (!type) return ''
    const key = `ops.alertsCenter.policyTypeDescriptions.${type}`
    return te(key) || te(key, 'en') ? t(key) : ''
  }

  const policyTypeOptions = computed(() =>
    ALERT_POLICY_TYPES.map((value) => ({
      value,
      label: policyTypeLabel(value),
    })),
  )

  const resourceTypeOptions = computed(() =>
    ALERT_RESOURCE_TYPES.map((value) => ({
      value,
      label: resourceTypeLabel(value),
    })),
  )

  return {
    policyTypeLabel,
    resourceTypeLabel,
    resourceTypeDescription,
    policyTypeDescription,
    policyTypeOptions,
    resourceTypeOptions,
  }
}
