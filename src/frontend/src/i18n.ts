import { createI18n } from 'vue-i18n'
import { en } from './locales'

export const LANG_STORAGE_KEY = 'hfl.lang'
export const DEFAULT_LOCALE = 'en'

let authenticatedLocale: string | null = null
let languagePacksLoaded = false
const localeAliases = new Map<string, string>()

export const i18n = createI18n({
  legacy: false,
  locale: DEFAULT_LOCALE,
  fallbackLocale: DEFAULT_LOCALE,
  messages: { en },
})

export function registerLocale(
  code: string,
  messages: Record<string, unknown>,
  aliases: string[] = [],
) {
  i18n.global.setLocaleMessage(code, messages)
  for (const alias of aliases) localeAliases.set(alias.toLowerCase(), code)
}

export function unregisterLocale(code: string) {
  if (code === DEFAULT_LOCALE) return
  delete (i18n.global.messages.value as Record<string, unknown>)[code]
  for (const [alias, target] of localeAliases) {
    if (target === code) localeAliases.delete(alias)
  }
}

export function getAvailableLocaleCodes(): string[] {
  return Object.keys(i18n.global.messages.value)
}

export function normalizeStoredLocale(stored: string | null | undefined): string {
  if (!stored) return DEFAULT_LOCALE
  const normalized = stored.toLowerCase()
  if (getAvailableLocaleCodes().includes(normalized)) return normalized
  return localeAliases.get(normalized) ?? DEFAULT_LOCALE
}

function readStoredLocale(): string | null {
  try {
    return localStorage.getItem(LANG_STORAGE_KEY)
  } catch {
    return null
  }
}

function activateLocale(code: string, persist: boolean): string {
  const resolved = normalizeStoredLocale(code)
  i18n.global.locale.value = resolved
  if (typeof document !== 'undefined') {
    document.documentElement.lang = resolved
  }
  if (persist) {
    try {
      localStorage.setItem(LANG_STORAGE_KEY, resolved)
    } catch {
      /* Storage may be unavailable in privacy-restricted browser contexts. */
    }
  }
  return resolved
}

function applyPreferredLocale(): string {
  return activateLocale(authenticatedLocale || readStoredLocale() || DEFAULT_LOCALE, true)
}

/** Apply a user selection immediately and retain it for anonymous sessions. */
export function selectLocale(code: string): string {
  return activateLocale(code, true)
}

/** Set the signed-in user's preference without resolving it before packs load. */
export function setAuthenticatedLocalePreference(code: string | null | undefined): void {
  authenticatedLocale = code?.trim() || null
  if (languagePacksLoaded) applyPreferredLocale()
}

/** Finish locale startup after all optional message catalogs have been registered. */
export function resolveLocaleAfterPacksLoaded(): string {
  languagePacksLoaded = true
  return applyPreferredLocale()
}
