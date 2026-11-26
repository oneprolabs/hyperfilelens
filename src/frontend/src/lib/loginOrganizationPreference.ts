const PREFIX = 'hfl_login_org:'

function preferenceKey(userId: number | undefined): string {
  return Number.isSafeInteger(userId) && Number(userId) > 0 ? `${PREFIX}${userId}` : ''
}

/** Only non-sensitive, account-scoped login hints may survive logout. */
export function isLoginOrganizationPreferenceKey(key: string): boolean {
  if (!key.startsWith(PREFIX)) return false
  const id = key.slice(PREFIX.length)
  return /^[1-9]\d*$/.test(id) && preferenceKey(Number(id)) === key
}

export function readLoginOrganizationPreference(userId: number | undefined): string {
  const key = preferenceKey(userId)
  if (!key) return ''
  try {
    return localStorage.getItem(key)?.trim() || ''
  } catch {
    return ''
  }
}

/** Call only after the server confirms this user's organization context. */
export function rememberLoginOrganization(userId: number, orgKey: string): void {
  const key = preferenceKey(userId)
  if (!key || !orgKey.trim()) return
  try {
    localStorage.setItem(key, orgKey.trim())
  } catch {
    // A blocked preference store must never prevent authentication.
  }
}
