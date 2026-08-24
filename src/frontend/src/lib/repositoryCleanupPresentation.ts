import type { ComposerTranslation } from 'vue-i18n'

import type { StorageRepositoryCleanupBlocker } from './storageRepositoryApi'

const CLEANUP_MESSAGE_KEYS: Record<string, string> = {
  repository_initialization_in_progress: 'repositoriesPage.cleanupInitializationInProgress',
  repository_ownership_unverified: 'repositoriesPage.cleanupOwnershipUnverified',
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
