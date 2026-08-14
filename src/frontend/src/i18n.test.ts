// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'
import {
  DEFAULT_LOCALE,
  LANG_STORAGE_KEY,
  i18n,
  registerLocale,
  resolveLocaleAfterPacksLoaded,
  selectLocale,
  setAuthenticatedLocalePreference,
} from './i18n'

describe('locale preference resolution', () => {
  it('waits for optional packs and gives an authenticated profile precedence', () => {
    localStorage.clear()
    localStorage.setItem(LANG_STORAGE_KEY, DEFAULT_LOCALE)
    registerLocale('zh-hans', { common: { confirm: 'Confirm' } }, ['zh', 'zh-cn'])

    expect(selectLocale('zh')).toBe('zh-hans')
    selectLocale(DEFAULT_LOCALE)

    setAuthenticatedLocalePreference('zh-hans')
    expect(i18n.global.locale.value).toBe(DEFAULT_LOCALE)

    resolveLocaleAfterPacksLoaded()
    expect(i18n.global.locale.value).toBe('zh-hans')
    expect(document.documentElement.lang).toBe('zh-hans')

    selectLocale(DEFAULT_LOCALE)
    expect(i18n.global.locale.value).toBe(DEFAULT_LOCALE)
    expect(localStorage.getItem(LANG_STORAGE_KEY)).toBe(DEFAULT_LOCALE)
  })
})
