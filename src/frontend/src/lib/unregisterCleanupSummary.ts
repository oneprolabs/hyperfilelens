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
  const diagnostics: Array<{ text: string; scope?: string; sourceId?: string }> = []
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
      diagnostics.push({ text, scope: scoped, sourceId: item.sourceId })
      failures.add(text)
    }
  }
  // Prefer source-scoped copies over an identical unscoped aggregate, without
  // merging identical errors belonging to different sources.
  const scopedTexts = new Set(diagnostics.filter(item => item.scope).map(item => item.text))
  const typedIds = new Map<string, Set<string>>()
  for (const { sourceId } of diagnostics) {
    const match = /^(agent|nas):(\d+)$/.exec(sourceId || '')
    if (!match) continue
    const ids = typedIds.get(match[2]!) || new Set<string>()
    ids.add(sourceId!)
    typedIds.set(match[2]!, ids)
  }
  const canonicalId = (id: string) => {
    const aliases = typedIds.get(id)
    return aliases?.size === 1 ? [...aliases][0]! : id
  }
  const identitiesByName = new Map<string, Set<string>>()
  for (const item of diagnostics) {
    if (!item.scope || !item.sourceId) continue
    const identities = identitiesByName.get(item.scope) || new Set<string>()
    identities.add(canonicalId(item.sourceId))
    identitiesByName.set(item.scope, identities)
  }
  for (const { text, scope, sourceId } of diagnostics) {
    if (!scope && scopedTexts.has(text)) continue
    const label = scope && (identitiesByName.get(scope)?.size || 0) > 1 ? sourceId || scope : scope
    reasons.add(label && !text.includes(label)
      ? t('protection.backupsPage.unregisterCleanupDiagnosticNamed', { name: label, detail: text })
      : text)
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
