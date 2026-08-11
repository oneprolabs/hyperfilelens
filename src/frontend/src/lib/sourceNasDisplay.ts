/** NAS mount source URI as used by mount(8): //host/share (CIFS) or host:/export (NFS). */

export type NasLikeResource = {
  resource_type?: string
  config?: Record<string, unknown>
  connection_summary?: string
  mount_point?: string
  mount_status?: string
  status?: string
  effective_status?: string
  connection_test_status?: string
}

export type SourceNasProxyStatus = 'online' | 'reconnecting' | 'offline'
export type SourceNasRuntimeStatus =
  | 'active'
  | 'inactive'
  | 'removing'
  | 'remove_failed'
  | 'error'
  | 'probing'
  | 'online'
  | 'unverified'
  | SourceNasProxyStatus

export type SourceNasStatusPresentation = {
  status: SourceNasRuntimeStatus
  labelKey: string
  tone: 'success' | 'warning' | 'danger' | 'info'
}

const SOURCE_NAS_STATUS_PRESENTATIONS: Record<
  SourceNasRuntimeStatus,
  Omit<SourceNasStatusPresentation, 'status'>
> = {
  active: {
    labelKey: 'protection.sourceResources.lifecycleRegistered',
    tone: 'success',
  },
  inactive: {
    labelKey: 'protection.sourceResources.nodeStatusOffline',
    tone: 'warning',
  },
  removing: {
    labelKey: 'protection.backupsPage.sourcePendingDeleting',
    tone: 'info',
  },
  remove_failed: {
    labelKey: 'protection.backupsPage.sourcePendingDeleteFailed',
    tone: 'danger',
  },
  error: {
    labelKey: 'protection.sourceResources.mountStatus.error',
    tone: 'danger',
  },
  probing: {
    labelKey: 'protection.sourceResources.capacitySyncing',
    tone: 'warning',
  },
  online: {
    labelKey: 'protection.sourceResources.nodeStatusOnline',
    tone: 'success',
  },
  unverified: {
    labelKey: 'protection.sourceResources.mountStatus.unmounted',
    tone: 'warning',
  },
  reconnecting: {
    labelKey: 'protection.sourceResources.nodeStatusReconnecting',
    tone: 'info',
  },
  offline: {
    labelKey: 'protection.sourceResources.nodeStatusOffline',
    tone: 'danger',
  },
}

function normalizedStatus(value?: string | null): string {
  return String(value || '').trim().toLowerCase()
}

/** Resolve NAS health before falling back to the bound Proxy connection state. */
export function sourceNasStatusPresentation(
  row: NasLikeResource,
  proxyStatus: SourceNasProxyStatus,
): SourceNasStatusPresentation {
  const effectiveStatus = normalizedStatus(row.effective_status)
  if (effectiveStatus in SOURCE_NAS_STATUS_PRESENTATIONS) {
    const status = effectiveStatus as SourceNasRuntimeStatus
    return { status, ...SOURCE_NAS_STATUS_PRESENTATIONS[status] }
  }

  const connectionTestStatus = normalizedStatus(row.connection_test_status)
  const mountStatus = normalizedStatus(row.mount_status)
  const resourceStatus = normalizedStatus(row.status)
  let status: SourceNasRuntimeStatus = proxyStatus
  if (resourceStatus === 'removing' || resourceStatus === 'remove_failed' || resourceStatus === 'error' || resourceStatus === 'probing' || resourceStatus === 'active' || resourceStatus === 'inactive') {
    status = resourceStatus as SourceNasRuntimeStatus
  } else if (
    connectionTestStatus === 'failed'
    || mountStatus === 'error'
    || resourceStatus === 'error'
  ) {
    status = 'error'
  } else if (connectionTestStatus === 'pending' || connectionTestStatus === 'running') {
    status = 'probing'
  } else if (mountStatus === 'mounted' || connectionTestStatus === 'success') {
    status = 'online'
  } else if (mountStatus === 'unmounted') {
    status = 'unverified'
  }
  return { status, ...SOURCE_NAS_STATUS_PRESENTATIONS[status] }
}

function configString(config: Record<string, unknown> | undefined, key: string): string {
  const value = config?.[key]
  return typeof value === 'string' ? value.trim() : ''
}

export function nasMountProtocol(row: NasLikeResource): 'smb' | 'nfs' | null {
  const resourceType = (row.resource_type || '').toLowerCase()
  const protocol = configString(row.config, 'protocol').toLowerCase()
  if (resourceType === 'cifs' || protocol === 'smb' || protocol === 'cifs') return 'smb'
  if (resourceType === 'nfs' || protocol === 'nfs') return 'nfs'
  if (configString(row.config, 'share')) return 'smb'
  if (configString(row.config, 'export_path')) return 'nfs'
  const summary = (row.connection_summary || '').trim()
  if (summary.startsWith('//') || summary.startsWith('\\\\')) return 'smb'
  if (summary.includes(' · ')) {
    const path = summary.split('·')[1]?.trim() || ''
    if (path.startsWith('/')) return 'nfs'
    if (path) return 'smb'
  }
  return null
}

function nfsExportPath(row: NasLikeResource): string {
  const exportPath = configString(row.config, 'export_path')
  if (exportPath) return exportPath
  if ((row.resource_type || '').toLowerCase() === 'nfs') {
    return configString(row.config, 'path')
  }
  return ''
}

function smbShare(row: NasLikeResource): string {
  return configString(row.config, 'share').replace(/^\/+|\/+$/g, '')
}

function serverFromSummary(summary: string): string {
  const trimmed = summary.trim()
  if (!trimmed) return ''
  if (trimmed.startsWith('//')) {
    const rest = trimmed.slice(2)
    const slash = rest.indexOf('/')
    return slash >= 0 ? rest.slice(0, slash) : rest
  }
  if (trimmed.includes(' · ')) return trimmed.split('·')[0]?.trim() || ''
  const colon = trimmed.indexOf(':')
  if (colon > 0 && !trimmed.startsWith('http')) return trimmed.slice(0, colon)
  return ''
}

function shareOrExportFromSummary(summary: string, protocol: 'smb' | 'nfs' | null): string {
  const trimmed = summary.trim()
  if (!trimmed) return ''
  if (trimmed.startsWith('//')) {
    const rest = trimmed.slice(2)
    const slash = rest.indexOf('/')
    return slash >= 0 ? rest.slice(slash + 1).replace(/^\/+|\/+$/g, '') : ''
  }
  if (trimmed.includes(' · ')) return trimmed.split('·')[1]?.trim() || ''
  if (protocol === 'nfs') {
    const colon = trimmed.indexOf(':')
    if (colon > 0) return trimmed.slice(colon + 1)
  }
  return ''
}

export function nasServerAddress(row: NasLikeResource): string {
  const server = configString(row.config, 'server')
  if (server) return server
  const fromSummary = serverFromSummary(row.connection_summary || '')
  return fromSummary || '—'
}

export function nasShareOrExport(row: NasLikeResource): string {
  const protocol = nasMountProtocol(row)
  if (protocol === 'smb') {
    const share = smbShare(row)
    if (share) return share
  }
  if (protocol === 'nfs') {
    const exportPath = nfsExportPath(row)
    if (exportPath) return exportPath
  }
  const fromSummary = shareOrExportFromSummary(row.connection_summary || '', protocol)
  return fromSummary || '—'
}

/** Protocol-native label key for the share/export value column. */
export function nasPathKindLabelKey(row: NasLikeResource): string {
  const protocol = nasMountProtocol(row)
  if (protocol === 'smb') return 'protection.sourceResources.colNasShareName'
  if (protocol === 'nfs') return 'protection.sourceResources.colNasExportPath'
  return 'protection.sourceResources.colNasShareExport'
}

export function nasProxyMountPoint(row: NasLikeResource): string {
  const path = configString(row.config, 'path')
  if (path) return path
  const mountPoint = (row.mount_point || '').trim()
  return mountPoint || '—'
}

/** Mask shown when a NAS source has a stored password (API never returns the secret). */
export const SOURCE_PASSWORD_MASK = '••••••••'

export function sourceHasPassword(credentials?: Record<string, unknown> | null): boolean {
  if (!credentials || typeof credentials !== 'object') return false
  // Public credential hint from source_credential_hint(); password itself is never serialized.
  if (credentials.has_password === true) return true
  const password = credentials.password
  return typeof password === 'string' && Boolean(password.trim())
}

export function sourcePasswordDisplay(
  credentials?: Record<string, unknown> | null,
  emptyLabel = '—',
): string {
  return sourceHasPassword(credentials) ? SOURCE_PASSWORD_MASK : emptyLabel
}

export function sourceExternalId(row: { id: number; resource_type?: string }): string {
  const type = (row.resource_type || 'nas').toLowerCase()
  const prefix = type === 'local' ? 'AGT' : 'NAS'
  const idPart = String(Math.abs(row.id)).padStart(5, '0')
  const suffix = String.fromCharCode(65 + (Math.abs(row.id) % 26))
  return `${prefix}-${idPart}-${suffix}`
}

export function nasMountSourceUri(row: NasLikeResource): string {
  const summary = (row.connection_summary || '').trim()
  const server = configString(row.config, 'server') || serverFromSummary(summary)
  const protocol = nasMountProtocol(row)

  if (protocol === 'smb') {
    const share = smbShare(row) || shareOrExportFromSummary(summary, protocol)
    if (server && share) return `//${server}/${share}`
    if (server) return `//${server}`
    return share || '—'
  }

  if (protocol === 'nfs') {
    const exportPath = nfsExportPath(row) || shareOrExportFromSummary(summary, protocol)
    if (server && exportPath) return `${server}:${exportPath}`
    if (server) return server
    return exportPath || '—'
  }

  if (summary.startsWith('//')) return summary
  if (summary.startsWith('\\\\')) {
    return `//${summary.slice(2).replace(/\\/g, '/')}`
  }
  if (summary.includes(':') && !summary.includes(' · ')) return summary
  if (summary.includes(' · ')) {
    const [host, remote] = summary.split('·').map((part) => part.trim())
    if (host && remote) {
      if (remote.startsWith('/')) return `${host}:${remote}`
      return `//${host}/${remote.replace(/^\/+|\/+$/g, '')}`
    }
  }
  return summary || '—'
}
