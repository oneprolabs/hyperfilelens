export interface SiteLanguage {
  code: string
  label: string
  path: string
}

// Add new locales here — every LanguageSwitcher instance picks it up automatically.
export const siteLanguages: SiteLanguage[] = [
  { code: 'en', label: 'English', path: '/en/' },
  { code: 'zh', label: '简体中文', path: '/zh/' },
]
