/** Singular resource type labels; navigation keeps its existing plural titles. */
const RESOURCE_TYPE_KEYS: Record<string, string> = {
  'Source Host': 'sourceHost',
  'Source NAS': 'sourceNas',
  'Proxy Host': 'proxyHost',
  'Object Storage': 'objectStorage',
  'Target NAS': 'targetNas',
  'Local Disk': 'localDisk',
  'Private Data Gateway': 'privateDataGateway',
  'Backup Configuration': 'backupConfiguration',
  'Restore Plan': 'restorePlan',
  'Backup Policy': 'backupPolicy',
  'File Filter': 'fileFilter',
  'Alert Rule': 'alertRule',
  'Notification Channel': 'notificationChannel',
}

export function governanceResourceTypeLabel(type: string, t: (key: string) => string): string {
  const key = RESOURCE_TYPE_KEYS[type]
  return key ? t(`settings.resourceTypes.${key}`) : type
}
