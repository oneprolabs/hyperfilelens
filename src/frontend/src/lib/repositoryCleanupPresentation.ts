import type { ComposerTranslation } from 'vue-i18n'

import type { StorageRepositoryCleanupBlocker } from './storageRepositoryApi'

const CLEANUP_MESSAGE_KEYS: Record<string, string> = {
  legacy_local_disk_preserved: 'repositoriesPage.cleanupLegacyLocalDiskPreserved',
  associated_sources: 'repositoriesPage.cleanupBlockedSources',
  associated_snapshots: 'repositoriesPage.cleanupBlockedSnapshots',
  associated_restore_plans: 'repositoriesPage.cleanupBlockedRestorePlans',
  associated_restore_records: 'repositoriesPage.cleanupRetainedRestoreHistory',
  repository_initialization_in_progress: 'repositoriesPage.cleanupInitializationInProgress',
  repository_ownership_unverified: 'repositoriesPage.cleanupOwnershipUnverified',
  removed_repository_requires_force: 'repositoriesPage.residualAttentionDescription',
  historical_direct_nas_locations: 'repositoriesPage.cleanupHistoricalDirectNasData',
  physical_targets_to_cleanup: 'repositoriesPage.cleanupDirectNasData',
}

export function repositoryCleanupMessage(
  item: StorageRepositoryCleanupBlocker,
  t: ComposerTranslation,
): string {
  const key = CLEANUP_MESSAGE_KEYS[item.code]
  if (!key) return item.detail
  return t(key, { n: item.count ?? 0 })
}

const ASSOCIATION_SUMMARY_KEYS = [
  ['associated_sources', 'repositoriesPage.cleanupResourceSources'],
  ['associated_snapshots', 'repositoriesPage.cleanupResourceSnapshots'],
  ['associated_restore_plans', 'repositoriesPage.cleanupResourceRestorePlans'],
] as const

export function repositoryCleanupBlockerSummary(
  items: StorageRepositoryCleanupBlocker[],
  t: ComposerTranslation,
): { associationSummary: string; blockers: string[] } {
  const summarized = new Set<StorageRepositoryCleanupBlocker>()
  const parts: string[] = []
  for (const [code, key] of ASSOCIATION_SUMMARY_KEYS) {
    const matching = items.filter(item => item.code === code && Number.isFinite(item.count) && item.count! >= 0)
    if (!matching.length) continue
    matching.forEach(item => summarized.add(item))
    const count = matching.reduce((sum, item) => sum + item.count!, 0)
    if (count > 0) parts.push(t(key, { n: count }))
  }
  return {
    associationSummary: parts.length
      ? t('repositoriesPage.cleanupResourceSummary', { resources: parts.join(t('repositoriesPage.cleanupResourceSeparator')) })
      : '',
    blockers: items.filter(item => !summarized.has(item)).map(item =>
      ASSOCIATION_SUMMARY_KEYS.some(([code]) => code === item.code)
        ? item.detail
        : repositoryCleanupMessage(item, t),
    ),
  }
}
