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

export const siteTrialLabels = {
  en: 'Try free',
  zh: '免费试用',
} as const

export const zhDocA11yLabels = {
  mobileNavigation: '移动导航',
  search: '搜索文档',
  sidebar: '侧边栏导航',
  pager: '文档翻页',
  copyCode: '复制代码',
  codeCopied: '已复制',
} as const

export const enDocA11yLabels = {
  copyCode: 'Copy code',
  codeCopied: 'Copied',
} as const
