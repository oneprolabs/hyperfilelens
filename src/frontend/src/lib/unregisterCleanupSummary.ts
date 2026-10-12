import type { ComposerTranslation } from 'vue-i18n'
import { unregisterCleanupText } from './unregisterCleanupDisplay'
import { unregisterReasonLabel } from './backupSourceUnregisterDialog'

export type CleanupEvidence = {
  detail: string
  code?: string
  sourceId?: string
  sourceName?: string
}

/** Collapse known uninstall events before translation; never discard unrecognized evidence. */
export function summarizeUnregisterCleanup(
  evidence: CleanupEvidence[],
  t: ComposerTranslation,
) {
  const reasons = new Set<string>()
  const failures = new Set<string>()
  const offlineTargets = new Set<string>()
  let hasOfflineUninstall = false
  for (const item of evidence) {
    const names: string[] = []
    let matched = false
    const remaining = item.detail
      .replace(/Remote uninstall was skipped because the Agent was offline\./g, () => {
        matched = true
        return ''
      })
      .replace(/Agent "([^"]+)" is offline — uninstall was skipped\./g, (_, name: string) => {
        matched = true
        names.push(name)
        return ''
      })
      .trim()
    if (matched) {
      hasOfflineUninstall = true
      if (item.sourceId) offlineTargets.add(item.sourceId)
      else {
        if (item.sourceName) offlineTargets.add(item.sourceName)
        names.forEach(name => offlineTargets.add(name))
      }
    }
    if (remaining) {
      const text = item.code && item.code !== 'agent_offline'
        ? unregisterReasonLabel({ code: item.code, detail: remaining, source_id: item.sourceId, source_name: item.sourceName }, t)
        : unregisterCleanupText(remaining, t)
      const scoped = item.sourceName || item.sourceId
      reasons.add(scoped && !text.includes(scoped) ? t('protection.backupsPage.unregisterCleanupDiagnosticNamed', { name: scoped, detail: text }) : text)
      failures.add(text)
    }
  }
  return {
    hasOfflineUninstall,
    offlineTargets,
    reasons: [
      ...(hasOfflineUninstall ? [t('protection.backupsPage.unregisterOfflineCleanupCause')] : []),
      ...reasons,
    ],
    failures: [...failures],
  }
}
