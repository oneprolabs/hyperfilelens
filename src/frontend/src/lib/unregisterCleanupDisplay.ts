import type { ComposerTranslation } from 'vue-i18n'

function translated(key: string, t: ComposerTranslation): string | undefined {
  const value = t(key, {}, { missingWarn: false, fallbackWarn: false })
  return value === key ? undefined : value
}

/** Translate known legacy diagnostics without changing object names or unknown evidence. */
export function unregisterCleanupText(text: string, t: ComposerTranslation): string {
  // Match complete legacy messages only: do not rewrite paths, names or arbitrary
  // vendor diagnostics which happen to contain these words.
  const legacyKey: Record<string, string> = {
    'The NAS mount could not be removed': 'ops.task.failureDetails.reason.unmount_failed',
    'Unmount failed': 'ops.task.failureDetails.reason_message.Unmount failed',
    'Unmount failed and the source remains registered': 'protection.backupsPage.unregisterUnmountFailedRegistered',
    'Synthetic test failure: Agent offline; remote uninstall could not complete. No real Agent was contacted': 'protection.backupsPage.syntheticUnregisterAgentOffline',
    'Diagnostic fixture only. Do not retry or force cleanup; use it to inspect failure detail availability': 'protection.backupsPage.syntheticUnregisterDiagnostic',
  }
  const message = text.trim().replace(/\.$/, '')
  const key = Object.hasOwn(legacyKey, message) ? legacyKey[message] : undefined
  if (key) return t(key)
  return text
    .replace(
      /^Backup source was not deleted\.?$/i,
      () => t('ops.task.failureDetails.reason.SOURCE_UNREGISTER_CLEANUP_FAILED'),
    )
    .replace(
      /Remote uninstall was skipped because the Agent was offline\./g,
      () => t('protection.backupsPage.unregisterOfflineUninstallSkipped'),
    )
    .replace(
      /Agent "([^"]+)" is offline — uninstall was skipped\./g,
      (_, name: string) => t('protection.backupsPage.unregisterOfflineUninstallSkippedNamed', { name }),
    )
    .replace(
      /Review retained resources and complete any remaining cleanup before retrying\.?/g,
      () => t('ops.task.failureDetails.suggestion.review_cleanup'),
    )
}

export function unregisterCleanupSuggestion(
  item: { code?: string; detail: string },
  t: ComposerTranslation,
): string {
  return translated(`ops.task.failureDetails.suggestion.${item.code || ''}`, t)
    || unregisterCleanupText(item.detail, t)
}

export function unregisterRetainedResourceLabel(resource: string, t: ComposerTranslation): string {
  const match = /^(mount|source_nas_mount|source_nas_mount_directory):([1-9]\d*)$/.exec(resource)
  if (match) {
    return t(match[1] === 'source_nas_mount_directory'
      ? 'protection.backupsPage.unregisterRetainedNasMountDirectory'
      : 'protection.backupsPage.unregisterRetainedNasMount', { id: match[2] })
  }
  return translated(`ops.task.failureDetails.retainedResource.${resource}`, t) || resource
}
