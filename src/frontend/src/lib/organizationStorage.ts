/** Shared tab-scoped context for API headers, drafts, and asynchronous notices. */
export function readStoredOrganizationKey(): string {
  try {
    const pinned = sessionStorage.getItem('hfl_org_key')
    if (pinned) return pinned
  } catch {
    // A blocked tab store must not hide an available stored default.
  }
  try {
    return localStorage.getItem('hfl_org_key') || ''
  } catch {
    return ''
  }
}
