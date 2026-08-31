const LEGACY_STEP2_STORAGE_KEY = 'protection-flow-step2-sources'

export function normalizeSourceIdList(ids: string[]) {
  const seen = new Set<string>()
  return ids.filter((id) => {
    if (!id || seen.has(id)) return false
    seen.add(id)
    return true
  })
}

export function isBackupSelectableId(id: string) {
  return /^agent:\d+$/.test(id) || /^nas:\d+$/.test(id)
}

function readJsonArray(raw: string | null) {
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed)
      ? parsed.filter((id): id is string => typeof id === 'string' && id.length > 0)
      : []
  } catch {
    return []
  }
}

export function readLegacyRealStep2Sources() {
  if (typeof localStorage === 'undefined') return []
  return normalizeSourceIdList(
    readJsonArray(localStorage.getItem(LEGACY_STEP2_STORAGE_KEY)).filter(isBackupSelectableId),
  )
}

export function clearLegacyStep2Sources() {
  if (typeof localStorage === 'undefined') return
  localStorage.removeItem(LEGACY_STEP2_STORAGE_KEY)
}
