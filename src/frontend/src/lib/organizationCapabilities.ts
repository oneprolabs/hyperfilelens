/** Product availability is independent of Role permissions and resource scope. */
export type OrganizationCapability =
  | 'managed_storage'
  | 'object_storage'
  | 'target_nas'
  | 'local_disks'
  | 'source_nas'
  | 'proxy_hosts'
  | 'source_tags'
export type OrganizationPolicyProfile = {
  organization_kind?: 'system_default' | 'personal' | 'tenant' | ''
  organization_mode?: 'standard' | 'personal' | 'team' | ''
  organization_capabilities?: Partial<Record<OrganizationCapability, boolean>>
}

export function sourcePermissionCapability(permission: string): OrganizationCapability | undefined {
  const prefix = permission.slice(0, permission.lastIndexOf('.'))
  const capabilities: Record<string, OrganizationCapability> = {
    'protection.source_nas': 'source_nas',
    'protection.proxy_hosts': 'proxy_hosts',
    'protection.tags': 'source_tags',
  }
  return capabilities[prefix]
}

export function organizationCapabilityAvailable(
  profile: OrganizationPolicyProfile | undefined,
  capability: OrganizationCapability,
  enterprise = __HFL_EXTENSIONS_FRONTEND__,
): boolean {
  if (!enterprise) return capability !== 'managed_storage'
  if (profile?.organization_capabilities) {
    return profile.organization_capabilities[capability] === true
  }
  // Compatibility with an older server. Once a capability map is present,
  // missing/unknown entries are denied rather than inferred from a Role.
  if (profile?.organization_mode === 'personal') return capability === 'managed_storage'
  if (capability === 'managed_storage') return false
  return true
}
