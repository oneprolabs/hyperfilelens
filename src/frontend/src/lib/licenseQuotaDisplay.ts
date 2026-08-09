import type { LicenseUsage } from './subscriptionApi'

export type QuotaUsageRow = {
  key: string
  labelKey: string
  used: number
  limit: number
  suffix?: string
}

type QuotaDisplayDef = {
  key: string
  labelKey: string
  usageKey: string
  limitKey: string
  suffix?: string
}

/**
 * API meter / pool keys → vue-i18n paths under `licenseQuota.*`.
 * Used by tenant Subscription, Dashboard, Instance License, and Org quotas.
 */
export const QUOTA_METER_LABEL_KEY: Record<string, string> = {
  max_organizations: 'licenseQuota.organizations',
  max_users: 'licenseQuota.users',
  max_storage_gb: 'licenseQuota.storageGb',
  max_nodes: 'licenseQuota.nodes',
  max_gateways: 'licenseQuota.privateGateways',
  max_public_gateways: 'licenseQuota.publicGateways',
  max_public_gateway_capacity_gb: 'licenseQuota.publicGatewayCapacityGb',
  ai_tokens: 'licenseQuota.aiTokens',
  ai_requests: 'licenseQuota.aiTokens',
  ai_insights_quota: 'licenseQuota.aiTokens',
  max_source_hosts: 'licenseQuota.sourceHosts',
  max_source_nas: 'licenseQuota.sourceNas',
  max_proxies: 'licenseQuota.sourceAgents',
  max_source_proxies: 'licenseQuota.sourceAgents',
  max_object_storage: 'licenseQuota.objectStorage',
  max_target_nas: 'licenseQuota.targetNas',
  max_standalone_disk: 'licenseQuota.standaloneDisk',
  max_protected_sources: 'licenseQuota.protectedSources',
  max_alert_policies: 'licenseQuota.alertPolicies',
  gateway_select_max_files: 'licenseQuota.gatewaySelectFiles',
  gateway_select_max_bytes: 'licenseQuota.gatewaySelectBytes',
}

/** Resolve i18n key for a quota/pool meter; empty string if unknown. */
export function quotaMeterLabelKey(meterKey: string): string {
  return QUOTA_METER_LABEL_KEY[meterKey] || ''
}

/** Org-facing meters on Subscription (limits + live usage). */
export const SUBSCRIPTION_QUOTA_DEFS: QuotaDisplayDef[] = [
  {
    key: 'users',
    labelKey: 'licenseQuota.users',
    usageKey: 'users_count',
    limitKey: 'max_users',
  },
  {
    key: 'storage',
    labelKey: 'licenseQuota.storageGb',
    usageKey: 'storage_used_gb',
    limitKey: 'max_storage_gb',
    suffix: 'GiB',
  },
  {
    key: 'privateGateways',
    labelKey: 'licenseQuota.privateGateways',
    usageKey: 'gateways_count',
    limitKey: 'max_gateways',
  },
  {
    key: 'publicGatewayCapacity',
    labelKey: 'licenseQuota.publicGatewayCapacityGb',
    usageKey: 'public_gateway_capacity_used_gb',
    limitKey: 'max_public_gateway_capacity_gb',
    suffix: 'GiB',
  },
  {
    key: 'agents',
    labelKey: 'licenseQuota.sourceHosts',
    usageKey: 'agents_count',
    limitKey: 'max_source_hosts',
  },
  {
    key: 'proxies',
    labelKey: 'licenseQuota.sourceAgents',
    usageKey: 'proxies_count',
    limitKey: 'max_proxies',
  },
  {
    key: 'sourceNas',
    labelKey: 'licenseQuota.sourceNas',
    usageKey: 'source_nas_count',
    limitKey: 'max_source_nas',
  },
  {
    key: 'objectStorage',
    labelKey: 'licenseQuota.objectStorage',
    usageKey: 'object_storage_count',
    limitKey: 'max_object_storage',
  },
  {
    key: 'targetNas',
    labelKey: 'licenseQuota.targetNas',
    usageKey: 'target_nas_count',
    limitKey: 'max_target_nas',
  },
  {
    key: 'standaloneDisk',
    labelKey: 'licenseQuota.standaloneDisk',
    usageKey: 'standalone_disk_count',
    limitKey: 'max_standalone_disk',
  },
  {
    key: 'protectedSources',
    labelKey: 'licenseQuota.protectedSources',
    usageKey: 'protected_sources_count',
    limitKey: 'max_protected_sources',
  },
  {
    key: 'aiTokens',
    labelKey: 'licenseQuota.aiTokens',
    usageKey: 'ai_tokens_used',
    limitKey: 'ai_tokens',
    suffix: 'tokens',
  },
  {
    key: 'alertPolicies',
    labelKey: 'licenseQuota.alertPolicies',
    usageKey: 'alert_policies_count',
    limitKey: 'max_alert_policies',
  },
]

/** Compact source-focused strip for the tenant dashboard. */
export const DASHBOARD_QUOTA_DEFS: QuotaDisplayDef[] = [
  {
    key: 'agents',
    labelKey: 'licenseQuota.sourceHosts',
    usageKey: 'agents_count',
    limitKey: 'max_source_hosts',
  },
  {
    key: 'sourceNas',
    labelKey: 'licenseQuota.sourceNas',
    usageKey: 'source_nas_count',
    limitKey: 'max_source_nas',
  },
  {
    key: 'proxies',
    labelKey: 'licenseQuota.sourceAgents',
    usageKey: 'proxies_count',
    limitKey: 'max_proxies',
  },
  {
    key: 'objectStorage',
    labelKey: 'licenseQuota.objectStorage',
    usageKey: 'object_storage_count',
    limitKey: 'max_object_storage',
  },
  {
    key: 'targetNas',
    labelKey: 'licenseQuota.targetNas',
    usageKey: 'target_nas_count',
    limitKey: 'max_target_nas',
  },
  {
    key: 'standaloneDisk',
    labelKey: 'licenseQuota.standaloneDisk',
    usageKey: 'standalone_disk_count',
    limitKey: 'max_standalone_disk',
  },
]

export const SUBSCRIPTION_QUOTA_FALLBACK_LIMITS: Record<string, number> = {
  max_users: 500,
  max_storage_gb: 5000,
  max_gateways: 50,
  max_public_gateway_capacity_gb: 5000,
  max_source_hosts: 200,
  max_proxies: 200,
  max_source_proxies: 200,
  max_source_nas: 200,
  max_object_storage: 200,
  max_target_nas: 200,
  max_standalone_disk: 200,
  max_protected_sources: 500,
  ai_tokens: 50_000_000,
  ai_requests: 50_000_000,
  max_alert_policies: 500,
}

export function quotaDefsForDashboard(): QuotaDisplayDef[] {
  return DASHBOARD_QUOTA_DEFS
}

export function quotaDefsForSubscription(): QuotaDisplayDef[] {
  return SUBSCRIPTION_QUOTA_DEFS
}

export function buildQuotaRows(
  usage: LicenseUsage,
  limits: Record<string, number>,
  license?: Record<string, unknown>,
  options?: { subscription?: boolean },
): QuotaUsageRow[] {
  const defs = options?.subscription ? quotaDefsForSubscription() : quotaDefsForDashboard()
  const mergedLimits = { ...SUBSCRIPTION_QUOTA_FALLBACK_LIMITS, ...limits }
  // Canonical meter is ai_tokens; license payloads may only expose ai_insights_quota.
  if (mergedLimits.ai_tokens == null && mergedLimits.ai_insights_quota != null) {
    mergedLimits.ai_tokens = Number(mergedLimits.ai_insights_quota)
  }
  if (license) {
    for (const { limitKey } of defs) {
      if (license[limitKey] !== undefined) mergedLimits[limitKey] = Number(license[limitKey])
    }
    if (license.ai_insights_quota !== undefined) {
      const tokens = Number(license.ai_insights_quota)
      mergedLimits.ai_insights_quota = tokens
      mergedLimits.ai_tokens = tokens
    }
  }

  return defs.map(({ key, labelKey, usageKey, limitKey, suffix }) => ({
    key,
    labelKey,
    used: Number(usage[usageKey]) || 0,
    limit: Number(mergedLimits[limitKey]) || 0,
    suffix,
  }))
}

export function quotaUsagePercent(used: number, limit: number): number {
  if (limit < 0) return 0
  if (!limit) return 0
  return Math.min(100, Math.round((used / limit) * 100))
}
