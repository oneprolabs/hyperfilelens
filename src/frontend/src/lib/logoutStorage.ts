import {
  LANG_STORAGE_KEY,
  clearLoginLocaleSelection,
  clearPendingLoginLocale,
} from '../i18n'

/** Clear browser-local user state while retaining the anonymous language preference. */
export function clearLogoutBrowserStorage(): void {
  try {
    const keys = Array.from(
      { length: localStorage.length },
      (_, index) => localStorage.key(index),
    ).filter((key): key is string => Boolean(key))

    for (const key of keys) {
      if (key !== LANG_STORAGE_KEY) localStorage.removeItem(key)
    }
  } catch {
    /* Storage may be unavailable in privacy-restricted browser contexts. */
  }

  clearLoginLocaleSelection()
  clearPendingLoginLocale()
}
