import type { ComposerTranslation } from 'vue-i18n'

function translated(key: string, t: ComposerTranslation): string | undefined {
  const value = t(key, {}, { missingWarn: false, fallbackWarn: false })
  return value === key ? undefined : value
}

/** Translate known legacy diagnostics without changing object names or unknown evidence. */
export function unregisterCleanupText(text: string, t: ComposerTranslation): string {
  return text
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
  return translated(`ops.task.failureDetails.retainedResource.${resource}`, t) || resource
}
