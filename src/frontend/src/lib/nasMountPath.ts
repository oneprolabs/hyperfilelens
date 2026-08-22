export const DEFAULT_AGENT_DATA_DIR = '/opt/hyperfilelens-agent'

export type NasSourceProtocol = 'smb' | 'nfs'

export type GeneratedNasSourceParams = {
  protocol: NasSourceProtocol
  smbServer?: string
  smbShare?: string
  nfsHost?: string
  nfsExport?: string
}

function normalizeNasMountServer(value: string, fallback: string): string {
  const normalized = value
    .trim()
    .replace(/^[\\/:\s]+|[\\/:\s]+$/g, '')
    .replace(/[^a-zA-Z0-9._-]+/g, '-')
    .replace(/-+/g, '-')
  return normalized || fallback
}

function normalizeNasMountResource(value: string, fallback: string): string {
  const normalized = value
    .trim()
    .replace(/^\/+/, '')
    .replace(/[\\/]+/g, '_')
    .replace(/[^a-zA-Z0-9._-]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
  return normalized || fallback
}

function buildNasResourceKey(params: GeneratedNasSourceParams) {
  if (params.protocol === 'smb') {
    return {
      protocolLabel: 'SMB',
      protocolDir: 'smb',
      server: normalizeNasMountServer(params.smbServer ?? '', 'smb'),
      resource: normalizeNasMountResource(params.smbShare ?? '', 'data'),
    }
  }

  return {
    protocolLabel: 'NFS',
    protocolDir: 'nfs',
    server: normalizeNasMountServer(params.nfsHost ?? '', 'nfs'),
    resource: normalizeNasMountResource(params.nfsExport ?? '', 'data'),
  }
}

export function buildGeneratedNasName(params: GeneratedNasSourceParams): string {
  const { protocolLabel, server, resource } = buildNasResourceKey(params)
  return `${protocolLabel}_${server}_${resource}`
}

export function buildGeneratedNasMountDir(params: GeneratedNasSourceParams): string {
  const { protocolDir, server, resource } = buildNasResourceKey(params)
  return `${DEFAULT_AGENT_DATA_DIR}/mounts/custom/${protocolDir}-${server}-${resource}`
}

export function customMountPath(leaf: string): string {
  const slug = leaf.trim().replace(/^\/+|\/+$/g, '')
  return `${DEFAULT_AGENT_DATA_DIR}/mounts/custom/${slug}`
}
