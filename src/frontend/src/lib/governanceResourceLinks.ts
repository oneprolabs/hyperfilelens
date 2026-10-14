/** Read-only business destinations. Never route a directory link into an editor. */
export function governanceResourceLink(resource: {
  resource_type?: string
  resourceType?: string
  type?: string
  resource_id?: number
  resourceId?: number
  source_id?: string
  sourceId?: string
}): string | null {
  const resourceType = resource.resource_type || resource.resourceType
  const resourceId = resource.resource_id ?? resource.resourceId
  switch (resourceType) {
    case 'node':
      return resource.type === 'Private Data Gateway' ? '/insight/gateways' : '/node/agents'
    case 'repository':
      return '/node/repositories'
    case 'source_resource':
      return '/protection/backup-sources'
    case 'backup_config':
      return resourceId ? `/protection/backups/${resourceId}` : '/protection/backups'
    case 'restore_plan':
      return '/protection/backups?step=3'
    case 'backup_policy':
      return '/protection/policies?tab=backup'
    case 'file_filter_rule':
      return '/protection/policies?tab=filter'
    case 'alert_policy':
      return '/ops/alerts/rules'
    case 'notification_channel':
      return '/ops/channels'
    default:
      return null
  }
}
