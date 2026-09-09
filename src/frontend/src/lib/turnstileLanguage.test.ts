import { describe, expect, it } from 'vitest'

import { turnstileLanguageFromAppLocale } from './turnstileLanguage'

describe('turnstileLanguageFromAppLocale', () => {
  it.each([
    ['zh-hans', 'zh-cn'],
    ['zh-cn', 'zh-cn'],
    ['zh', 'zh-cn'],
    ['ZH_HANS', 'zh-cn'],
    ['es', 'es'],
    ['en-US', 'en-us'],
    ['', 'en'],
    ['  ', 'en'],
  ])('maps %s to %s', (appLocale, expected) => {
    expect(turnstileLanguageFromAppLocale(appLocale)).toBe(expected)
  })
})
