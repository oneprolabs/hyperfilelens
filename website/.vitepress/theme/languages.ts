export interface SiteLanguageLink {
  code: string
  label: string
  href: string
}

export interface SiteLanguage {
  code: string
  label: string
  path: string
}

// Add new locales here — every LanguageSwitcher instance picks it up automatically.
export const siteLanguages: SiteLanguage[] = [
  { code: 'en', label: 'English', path: '/' },
  { code: 'zh', label: '简体中文', path: '/zh/' },
  { code: 'es', label: 'Español', path: '/es/' },
]

export const siteLanguageMenuLabels: Record<string, string> = {
  en: 'Change language',
  zh: '切换语言',
  es: 'Cambiar idioma',
}

/** Strip /zh or /es prefix so the remainder can be re-prefixed for another locale. */
export function pathWithoutLocale(pathname: string): string {
  const match = pathname.match(/^\/(zh|es)(?=\/|$)/)
  if (!match) return pathname || '/'
  const rest = pathname.slice(match[0].length)
  return rest ? rest : '/'
}

/**
 * Keep the visitor on the same marketing/blog surface when switching language.
 * Homepage stays on home; /blog and /blog/posts/* stay on the matching locale path.
 */
export function localizedSitePath(pathname: string, locale: string, hash = ''): string {
  let rest = pathWithoutLocale(pathname)
  if (!rest.startsWith('/')) rest = `/${rest}`

  const isBlog = rest === '/blog' || rest === '/blog/' || rest.startsWith('/blog/')
  if (!isBlog) {
    return `${locale === 'en' ? '/' : `/${locale}/`}${hash}`
  }

  // Blog index prefers a trailing slash; post slugs keep their current shape.
  let blogPath = rest === '/blog' ? '/blog/' : rest
  if (blogPath === '/blog/') {
    // already normalized
  } else if (blogPath.endsWith('/') && /\/blog\/posts\/[^/]+\/$/.test(blogPath)) {
    blogPath = blogPath.replace(/\/$/, '')
  }

  const targetPath = locale === 'en' ? blogPath : `/${locale}${blogPath}`
  return `${targetPath}${hash}`
}

export const siteTrialLabels = {
  en: 'Try free',
  zh: '免费试用',
  es: 'Prueba gratis',
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
  mobileNavigation: 'Mobile navigation',
  search: 'Search documentation',
  sidebar: 'Sidebar navigation',
  pager: 'Documentation pagination',
  copyCode: 'Copy code',
  codeCopied: 'Copied',
} as const

const englishDocRoutes = new Set([
  '/docs',
  '/docs/getting-started/install',
  '/docs/getting-started/sign-in',
  '/docs/getting-started/configure-external-access',
  '/docs/getting-started/add-source',
  '/docs/getting-started/configure-source',
  '/docs/getting-started/add-target',
  '/docs/getting-started/first-backup',
  '/docs/getting-started/verify-backup',
  '/docs/getting-started/first-restore',
  '/docs/getting-started/configure-insights-model',
  '/docs/getting-started/first-insight',
  '/docs/product',
  '/docs/backup-restore',
  '/docs/backup-restore/sources',
  '/docs/backup-restore/targets',
  '/docs/backup-restore/create-backup',
  '/docs/backup-restore/policies',
  '/docs/backup-restore/snapshots',
  '/docs/backup-restore/restore',
  '/docs/backup-restore/recover-to-new-host',
  '/docs/insights',
  '/docs/insights/prepare',
  '/docs/insights/copilot',
  '/docs/insights/models',
  '/docs/insights/data-gateway',
  '/docs/insights/privacy',
  '/docs/governance',
  '/docs/governance/organization',
  '/docs/governance/members',
  '/docs/governance/groups',
  '/docs/governance/roles',
  '/docs/governance/resources',
  '/docs/governance/usage',
  '/docs/deployment',
  '/docs/deployment/requirements',
  '/docs/deployment/network',
  '/docs/deployment/post-install',
  '/docs/deployment/agent',
  '/docs/deployment/proxy',
  '/docs/deployment/data-gateway',
  '/docs/deployment/lifecycle',
  '/docs/deployment/operations',
  '/docs/deployment/uninstall',
  '/docs/help',
  '/docs/reference',
  '/docs/reference/support-matrix',
  '/docs/reference/limitations-security',
  '/docs/reference/windows-limitations',
  '/docs/faq/backup-scope',
  '/docs/troubleshooting',
  '/docs/troubleshooting/account-sign-in',
  '/docs/troubleshooting/installation-nodes',
  '/docs/troubleshooting/protection',
  '/docs/troubleshooting/insights',
])

/** Docs use English/Chinese only; fall back when a page has no English version. */
export function localizedDocPath(pathname: string, locale: 'en' | 'zh'): string {
  const route = pathWithoutLocale(pathname).replace(/\/+$/, '') || '/docs'
  if (locale === 'zh') return `/zh${route}`
  if (englishDocRoutes.has(route)) return route
  if (/^\/docs\/(product|backup-restore|insights)\//.test(route)) return '/docs/product'
  return '/docs'
}
