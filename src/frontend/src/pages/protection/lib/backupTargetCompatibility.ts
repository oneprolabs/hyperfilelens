export type BackupTargetCompatibilitySource = {
  sourceType: 'host' | 'nas'
  boundNodeId?: number | string | null
  platform?: string | null
}

export type BackupTargetCompatibilityRepository = {
  repoType: string
  bindNodeType?: string | null
  bindNodeId?: number | string | null
  crossProxyReady?: boolean
}

export type BackupTargetIncompatibilityReason =
  | 'direct_nas_linux_only'
  | 'cross_proxy_unavailable'

export function backupTargetIncompatibilityReason(
  source: BackupTargetCompatibilitySource,
  repository: BackupTargetCompatibilityRepository,
): BackupTargetIncompatibilityReason | null {
  const directNas = repository.repoType === 'NAS'
    && (repository.bindNodeType !== 'proxy' || !repository.bindNodeId)
  if (source.sourceType === 'host' && directNas && source.platform !== 'linux') {
    return 'direct_nas_linux_only'
  }
  if (source.sourceType !== 'nas') return null
  if (repository.repoType !== 'NAS' && repository.repoType !== 'PROXY_FS') return null
  if (repository.bindNodeType !== 'proxy' || !repository.bindNodeId) return null
  if (!source.boundNodeId) return 'cross_proxy_unavailable'
  if (Number(source.boundNodeId) === Number(repository.bindNodeId)) return null
  return repository.crossProxyReady === true ? null : 'cross_proxy_unavailable'
}

export function isBackupTargetCompatible(
  source: BackupTargetCompatibilitySource,
  repository: BackupTargetCompatibilityRepository,
) {
  return backupTargetIncompatibilityReason(source, repository) === null
}

export function backupTargetIncompatibilityReasonForSources(
  sources: BackupTargetCompatibilitySource[],
  repository: BackupTargetCompatibilityRepository,
): BackupTargetIncompatibilityReason | null {
  const reasons = sources
    .map(source => backupTargetIncompatibilityReason(source, repository))
    .filter((reason): reason is BackupTargetIncompatibilityReason => Boolean(reason))
  if (reasons.includes('direct_nas_linux_only')) return 'direct_nas_linux_only'
  return reasons[0] ?? null
}

export function requiresCrossProxyRepositoryServerHost(
  sources: BackupTargetCompatibilitySource[],
  repositoryBindNodeId?: number | string | null,
) {
  if (!repositoryBindNodeId) return false
  return sources.some((source) => (
    source.sourceType === 'nas'
    && (!source.boundNodeId || Number(source.boundNodeId) !== Number(repositoryBindNodeId))
  ))
}
