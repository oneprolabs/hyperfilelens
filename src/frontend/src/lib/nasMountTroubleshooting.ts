export const NAS_MOUNT_OPTIONS_FOCUS_QUERY = 'mount-options'
export const NAS_REPOSITORY_WRITE_DENIED = 'NAS_REPOSITORY_WRITE_DENIED'

export const SMB_MOUNT_OPTION_EXAMPLES = [
  'vers=3.0',
  'vers=2.0',
  'uid=1000,gid=1000,vers=3.0',
  'vers=3.0,sec=ntlmssp',
] as const

const SMB_MOUNT_NEGOTIATION_PATTERNS = [
  /mount error\(95\)/i,
  /operation not supported/i,
  /invalid argument/i,
]

const SMB_MOUNT_CONTEXT_PATTERNS = [
  /mount\.cifs/i,
  /\bcifs\b/i,
  /\bsmb\b/i,
]

export function isSmbMountNegotiationError(message: string): boolean {
  const normalized = String(message || '').trim()
  if (!normalized) return false
  return (
    SMB_MOUNT_NEGOTIATION_PATTERNS.some((pattern) => pattern.test(normalized)) &&
    SMB_MOUNT_CONTEXT_PATTERNS.some((pattern) => pattern.test(normalized))
  )
}

export function nasRepositoryFailureMessage(
  errorCode: string | null | undefined,
  fallback: string | null | undefined,
  t: (key: string) => string,
): string {
  if (String(errorCode || '').trim() === NAS_REPOSITORY_WRITE_DENIED) {
    return t('repositoriesPage.nasRepositoryWriteDenied')
  }
  return String(fallback || '').trim()
}

export function buildNasRepairMountOptionsPath(repositoryId: number): string {
  return `/node/repositories/nas/${repositoryId}/repair?focus=${NAS_MOUNT_OPTIONS_FOCUS_QUERY}`
}
