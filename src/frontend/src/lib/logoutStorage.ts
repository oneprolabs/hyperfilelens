import {
  LANG_STORAGE_KEY,
  clearLoginLocaleSelection,
  clearPendingLoginLocale,
} from '../i18n'
import { isLoginOrganizationPreferenceKey } from './loginOrganizationPreference'

/** Clear identity and tenant state, retaining language and account-scoped login hints. */
export function clearLogoutBrowserStorage(): void {
  try {
    const keys = Array.from(
      { length: localStorage.length },
      (_, index) => localStorage.key(index),
    ).filter((key): key is string => Boolean(key))

    for (const key of keys) {
      if (key !== LANG_STORAGE_KEY && !isLoginOrganizationPreferenceKey(key)) localStorage.removeItem(key)
    }
  } catch {
    /* Storage may be unavailable in privacy-restricted browser contexts. */
  }

  clearLoginLocaleSelection()
  clearPendingLoginLocale()
  try {
    sessionStorage.removeItem('hfl_org_key')
  } catch {
    /* Storage may be unavailable in privacy-restricted browser contexts. */
  }
}
