// A cached document must never adopt a later document's tab context.
let frozenDocumentOrganization: string | null = null

export function isOrganizationContextFrozen(): boolean {
  return frozenDocumentOrganization !== null
}

export function freezeOrganizationContext(): void {
  if (!isOrganizationContextFrozen()) {
    frozenDocumentOrganization = readStoredOrganizationKey()
  }
}

export function releaseOrganizationContext(): void {
  frozenDocumentOrganization = null
}

/** Shared tab-scoped context for API headers, drafts, and asynchronous notices. */
export function readStoredOrganizationKey(): string {
  if (frozenDocumentOrganization !== null) return frozenDocumentOrganization
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

/** Verify tab storage without ever installing the target in the old document. */
export function prepareTabOrganizationContext(): void {
  if (isOrganizationContextFrozen()) {
    throw new Error('Organization context is waiting for a document reload.')
  }
  const currentKey = readStoredOrganizationKey()
  if (!currentKey) throw new Error('An active organization is required.')
  const storage = sessionStorage
  const probeKey = 'hfl_org_switch_probe'
  try {
    storage.setItem(probeKey, 'available')
    if (storage.getItem(probeKey) !== 'available') {
      throw new Error('Tab organization storage is unavailable.')
    }
    // Pin the current context even if it was inherited from the shared default.
    storage.setItem('hfl_org_key', currentKey)
    if (storage.getItem('hfl_org_key') !== currentKey) {
      throw new Error('Tab organization storage is unavailable.')
    }
  } finally {
    storage.removeItem(probeKey)
  }
}

/** A remembered browser choice is only a hint within server-returned memberships. */
export function chooseLoginOrganization(
  organizations: Array<{ org_key: string }>, rememberedKey: string, fallbackKey = '',
): string {
  return organizations.find(org => org.org_key === rememberedKey)?.org_key
    || organizations.find(org => org.org_key === fallbackKey)?.org_key
    || organizations[0]?.org_key || ''
}
