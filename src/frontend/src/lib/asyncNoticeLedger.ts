import { safeErrorDetailText, type ErrorDetailsPayload } from './errors/details'

export function asyncNoticeScope() {
  try { return localStorage.getItem('hfl_org_key') || 'session' } catch { return 'session' }
}

/** A bounded, redacted, tab-local ledger survives toast dismissal and reload. */
export function claimTaskNotice(details: ErrorDetailsPayload) {
  if (!details.taskUuid) return true
  try {
    const key = `hfl-async-notices:${asyncNoticeScope()}`
    const seen: string[] = JSON.parse(sessionStorage.getItem(key) || '[]')
    const identity = safeErrorDetailText([details.taskUuid, details.taskAttempt, details.errorCode, details.severity, details.summary, details.reasons?.slice().sort(),
      details.entities?.slice().sort((a, b) => `${a.type}:${a.id}`.localeCompare(`${b.type}:${b.id}`)),
      details.cleanupResidue ? {
        ...details.cleanupResidue,
        failures: details.cleanupResidue.failures?.slice().sort(),
        retainedResources: details.cleanupResidue.retainedResources?.slice().sort(),
        skippedItems: details.cleanupResidue.skippedItems?.slice().sort(),
      } : undefined])
    if (seen.includes(identity)) return false
    sessionStorage.setItem(key, JSON.stringify([...seen.slice(-199), identity]))
  } catch { /* Visible-toast dedup remains active when storage is unavailable. */ }
  return true
}
