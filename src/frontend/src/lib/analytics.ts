import type { LocationQuery, RouteLocationNormalizedLoaded, Router } from 'vue-router'
import { i18n } from '../i18n'

type GoogleTag = (...args: unknown[]) => void
type AppAnalyticsEvent = 'login' | 'sign_up' | 'sign_up_started'
type AppAnalyticsParameters = { method: 'email' | 'google' }

export type AnalyticsPageSurface = 'console' | 'admin'

export interface AnalyticsPageMetadata {
  pageKey: string
  pageGroup: string
  pageSurface: AnalyticsPageSurface
  pagePath: string
  titleKey: string
  title: string
}

declare global {
  interface Window {
    dataLayer?: unknown[]
    gtag?: GoogleTag
  }
}

const GA4_MEASUREMENT_ID = /^G-[A-Z0-9]+$/
const GOOGLE_TAG_SCRIPT = 'script[data-hfl-google-analytics]'

let activeMeasurementId = ''
let activePage: AnalyticsPageMetadata | null = null
let lastTrackedPageSignature = ''
let localeObserver: MutationObserver | null = null
let pageTrackingReady = false
let pendingPage: AnalyticsPageMetadata | null = null
let analyticsRouter: Router | null = null

function configuredMeasurementId(): string {
  if (window.__HFL_APP_CONFIG__?.sentrySurface === 'admin') return ''
  return String(window.__HFL_APP_CONFIG__?.gaMeasurementId || '').trim()
}

function sanitizedReferrer(): string {
  const value = document.referrer.trim()
  if (!value) return ''
  try {
    const parsed = new URL(value)
    return parsed.origin
  } catch {
    return ''
  }
}

export function analyticsRoutePath(
  route: Pick<RouteLocationNormalizedLoaded, 'matched' | 'path'> & { query?: LocationQuery },
): string {
  return normalizeRoutePath(analyticsRouteTemplate(route), route.query)
}

function queryValue(query: LocationQuery | undefined, key: string): string {
  const value = query?.[key]
  if (Array.isArray(value)) return String(value[0] || '')
  return String(value || '')
}

function normalizeRoutePath(path: string, query: LocationQuery | undefined): string {
  if (path === '/protection/backup-sources') {
    return queryValue(query, 'tab') === 'nas'
      ? '/protection/source-nas'
      : '/protection/source-hosts'
  }
  if (path === '/node/repositories') {
    const tab = queryValue(query, 'tab')
    if (tab === 'nas') return '/protection/target-nas'
    if (tab === 'proxy_fs') return '/protection/local-disks'
    return '/protection/object-storage'
  }
  if (path === '/protection/policies') {
    return queryValue(query, 'tab') === 'filter'
      ? '/protection/file-filters'
      : '/protection/backup-policies'
  }
  if (path === '/protection/backups') {
    const step = queryValue(query, 'step')
    if (step && ['source', 'backup-config', 'start-backup'].includes(step)) {
      return `/protection/backup-wizard/${step}`
    }
  }
  return path
}

function currentUiLanguage(): string {
  return String(i18n.global.locale.value || 'en').toLowerCase()
}

function titleForPage(titleKey: string): string {
  if (!titleKey) return 'Page'
  const translated = String(i18n.global.t(titleKey)).trim()
  return translated && translated !== titleKey ? translated : 'Page'
}

function pageMetadata(
  pageKey: string,
  pageGroup: string,
  pageSurface: AnalyticsPageSurface,
  pagePath: string,
  titleKey: string,
): AnalyticsPageMetadata {
  const suffix = pageSurface === 'admin'
    ? 'HyperFileLens Admin Console'
    : 'HyperFileLens Console'
  return {
    pageKey,
    pageGroup,
    pageSurface,
    pagePath,
    titleKey,
    title: `${titleForPage(titleKey)} | ${suffix}`,
  }
}

function routeDeclaredPageMetadata(
  route: Pick<RouteLocationNormalizedLoaded, 'matched' | 'path'>,
): AnalyticsPageMetadata | null {
  for (const record of [...route.matched].reverse()) {
    const declared = record.meta?.analytics
    if (!declared || typeof declared !== 'object') continue
    const pageKey = String((declared as Record<string, unknown>).pageKey || '').trim()
    if (!pageKey) continue
    const pageGroup = String((declared as Record<string, unknown>).pageGroup || 'other').trim()
    const titleKey = String((declared as Record<string, unknown>).titleKey || '').trim()
    const pageSurface = (declared as Record<string, unknown>).pageSurface === 'admin' ? 'admin' : 'console'
    return pageMetadata(pageKey, pageGroup, pageSurface, analyticsRouteTemplate(route), titleKey)
  }
  return null
}

export function analyticsPageMetadata(
  route: Pick<RouteLocationNormalizedLoaded, 'matched' | 'path'> & { query?: LocationQuery },
): AnalyticsPageMetadata {
  const rawPath = analyticsRouteTemplate(route)
  const pagePath = normalizeRoutePath(rawPath, route.query)
  const declaredPage = routeDeclaredPageMetadata(route)
  if (declaredPage) return { ...declaredPage, pagePath }
  if (rawPath.startsWith('/platform-ops')) {
    // Extension routes must declare their own metadata. Keep undeclared admin
    // paths in the same privacy-safe fallback as every other unknown route.
    return pageMetadata('route.other', 'platform', 'admin', '/other', 'platformOps.nav.title')
  }
  if (pagePath === '/') {
    return pageMetadata('overview.dashboard', 'overview', 'console', pagePath, 'nav.overview')
  }
  const staticPages: Record<string, [string, string, string]> = {
    '/protection/backups': ['protection.backup_wizard', 'protection', 'protection.side.dataProtection'],
    '/protection/backup-wizard/source': ['protection.backup_wizard', 'protection', 'protection.side.dataProtection'],
    '/protection/backup-wizard/backup-config': ['protection.backup_wizard', 'protection', 'protection.side.dataProtection'],
    '/protection/backup-wizard/start-backup': ['protection.backup_wizard', 'protection', 'protection.side.dataProtection'],
    '/protection/source-hosts': ['protection.source_hosts', 'protection', 'protection.side.sourceHosts'],
    '/protection/source-nas': ['protection.source_nas', 'protection', 'protection.side.sourceNas'],
    '/node/agents': ['protection.proxy_hosts', 'protection', 'protection.side.sourceAgents'],
    '/node/snapshots': ['protection.snapshots', 'protection', 'protection.side.snapshots'],
    '/protection/object-storage': ['protection.object_storage', 'protection', 'protection.side.objectStorage'],
    '/protection/target-nas': ['protection.target_nas', 'protection', 'repositoriesPage.tabNas'],
    '/protection/local-disks': ['protection.local_disks', 'protection', 'protection.side.standaloneDisks'],
    '/protection/backup-policies': ['protection.backup_policies', 'protection', 'protection.side.backupPolicies'],
    '/protection/file-filters': ['protection.file_filters', 'protection', 'protection.side.fileFilterRules'],
    '/insight/copilot': ['insight.ai_copilot', 'insights', 'insight.side.copilot'],
    '/insight/copilot/new': ['insight.ai_copilot', 'insights', 'insight.side.copilot'],
    '/insight/copilot/shared': ['insight.ai_copilot', 'insights', 'insight.side.copilot'],
    '/insight/gateways': ['insight.data_gateways', 'insights', 'insight.side.dataGateway'],
    '/insight/usage': ['insight.usage', 'insights', 'insight.side.usage'],
    '/node/organization': ['configuration.organization', 'configuration', 'settings.nav.organizationHub'],
    '/node/members': ['configuration.members', 'configuration', 'settings.nav.members'],
    '/node/subscription': ['configuration.subscription', 'configuration', 'settings.nav.subscription'],
    '/ops/health': ['operations.operational_health', 'operations', 'ops.nav.operationalHealth'],
    '/ops/alerts': ['operations.alerts', 'operations', 'ops.nav.alerts'],
    '/ops/alerts/rules': ['operations.alert_rules', 'operations', 'ops.nav.alertRules'],
    '/ops/channels': ['operations.notification_channels', 'operations', 'ops.nav.notificationChannels'],
    '/ops/delivery-history': ['operations.delivery_history', 'operations', 'ops.nav.deliveryHistory'],
    '/ops/tasks': ['operations.tasks', 'operations', 'ops.task.sideTasks'],
    '/ops/audit-logs': ['operations.audit_logs', 'operations', 'ops.task.sideAudit'],
    '/account/profile': ['account.profile', 'account', 'account.pageProfileTitle'],
    '/account/notifications': ['account.notifications', 'account', 'account.pageNotificationsTitle'],
    '/search': ['global.search', 'global', 'nav.searchPlaceholder'],
    '/login': ['auth.login', 'auth', 'login.btnSubmit'],
    '/register': ['auth.register', 'auth', 'register.welcomeTitle'],
    '/auth/oauth/callback': ['auth.oauth_callback', 'auth', 'login.googleBtn'],
    '/auth/oauth/error': ['auth.oauth_error', 'auth', 'login.googleErrorTitle'],
  }
  const staticPage = staticPages[pagePath]
  if (staticPage) return pageMetadata(staticPage[0], staticPage[1], 'console', pagePath, staticPage[2])
  const prefixPages: Array<[string, string, string, string]> = [
    ['/protection/backups/create', 'protection.backup_wizard', 'protection', 'protection.side.dataProtection'],
    ['/protection/policies/create', 'protection.backup_policies', 'protection', 'protection.side.backupPolicies'],
    ['/protection/policies/', 'protection.backup_policies', 'protection', 'protection.side.backupPolicies'],
    ['/protection/file-filter-rules/help', 'protection.file_filters', 'protection', 'protection.side.fileFilterRules'],
    ['/node/repositories/s3/', 'protection.object_storage', 'protection', 'protection.side.objectStorage'],
    ['/node/repositories/nas/', 'protection.target_nas', 'protection', 'repositoriesPage.tabNas'],
    ['/node/repositories/proxy-fs/', 'protection.local_disks', 'protection', 'protection.side.standaloneDisks'],
    ['/node/nodes/deploy', 'protection.node_deployment', 'protection', 'nodesDeploy.pageTitle'],
    ['/ops/alerts/rules/', 'operations.alert_rules', 'operations', 'ops.nav.alertRules'],
    ['/ops/channels/', 'operations.notification_channels', 'operations', 'ops.nav.notificationChannels'],
  ]
  const prefixedPage = prefixPages.find(([prefix]) => rawPath.startsWith(prefix))
  if (prefixedPage) {
    return pageMetadata(prefixedPage[1], prefixedPage[2], 'console', pagePath, prefixedPage[3])
  }
  if (rawPath.includes('/protection/backups/:backupId/snapshots/:snapshotId')) {
    return pageMetadata('protection.snapshot_detail', 'protection', 'console', rawPath, 'analytics.pages.snapshotDetails')
  }
  if (rawPath.includes('/protection/backups/:backupId')) {
    return pageMetadata('protection.backup_detail', 'protection', 'console', rawPath, 'analytics.pages.backupDetails')
  }
  if (rawPath.includes('/protection/restore/snapshots/:snapshotId')) {
    return pageMetadata('protection.restore', 'protection', 'console', rawPath, 'analytics.pages.restore')
  }
  return pageMetadata('route.other', 'other', 'console', '/other', 'nav.navigation')
}

function analyticsRouteTemplate(
  route: Pick<RouteLocationNormalizedLoaded, 'matched' | 'path'>,
): string {
  const matchedPath = route.matched.reduce((path, record) => {
    const segment = record.path.trim()
    if (!segment) return path
    if (segment.startsWith('/')) return segment
    return `${path.replace(/\/$/, '')}/${segment}`
  }, '')
  const path = matchedPath || route.path || '/'
  return path.startsWith('/') ? path : `/${path}`
}

function trackPageView(page: AnalyticsPageMetadata): void {
  if (!activeMeasurementId || !window.gtag) return
  const locale = currentUiLanguage()
  const signature = `${page.pageSurface}|${page.pageKey}|${page.pagePath}`
  activePage = page
  document.title = page.title
  if (!pageTrackingReady) {
    pendingPage = page
    return
  }
  pendingPage = null
  if (signature === lastTrackedPageSignature) return
  lastTrackedPageSignature = signature
  window.gtag('event', 'page_view', {
    page_location: `${window.location.origin}${page.pagePath}`,
    page_path: page.pagePath,
    page_referrer: sanitizedReferrer(),
    page_title: activePage.title,
    page_key: activePage.pageKey,
    page_group: activePage.pageGroup,
    page_surface: activePage.pageSurface,
    ui_language: locale,
  })
}

export function trackAppEvent(
  name: AppAnalyticsEvent,
  parameters: AppAnalyticsParameters,
): void {
  if (!activeMeasurementId || !window.gtag) return
  const page = activePage
  window.gtag('event', name, {
    ...parameters,
    page_location: `${window.location.origin}${page?.pagePath || '/'}`,
    page_path: page?.pagePath || '/',
    page_referrer: sanitizedReferrer(),
    page_title: page?.title || 'HyperFileLens Console',
    ...(page ? {
      page_key: page.pageKey,
      page_group: page.pageGroup,
      page_surface: page.pageSurface,
      ui_language: currentUiLanguage(),
    } : {}),
  })
}

function installGoogleTag(measurementId: string): boolean {
  if (!measurementId) return false
  if (!GA4_MEASUREMENT_ID.test(measurementId)) {
    console.warn('[analytics] Invalid GA4 measurement ID; analytics is disabled.')
    return false
  }
  if (activeMeasurementId === measurementId) return true

  activeMeasurementId = measurementId
  activePage = null
  lastTrackedPageSignature = ''
  pageTrackingReady = false
  pendingPage = null
  window.dataLayer = window.dataLayer || []
  // Google Tag distinguishes its Arguments command object from a normal Array.
  window.gtag = function gtag() {
    // eslint-disable-next-line prefer-rest-params
    window.dataLayer?.push(arguments)
  }
  window.gtag('js', new Date())
  window.gtag('config', measurementId, { send_page_view: false })

  if (!document.querySelector(GOOGLE_TAG_SCRIPT)) {
    const script = document.createElement('script')
    script.async = true
    script.dataset.hflGoogleAnalytics = 'true'
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`
    document.head.appendChild(script)
  }
  return true
}

export function initAppAnalytics(router: Router): void {
  if (!installGoogleTag(configuredMeasurementId())) return
  analyticsRouter = router
  router.afterEach((route) => trackPageView(analyticsPageMetadata(route)))
  const currentRoute = router.currentRoute?.value
  if (currentRoute?.matched.length) trackPageView(analyticsPageMetadata(currentRoute))
  if (typeof MutationObserver !== 'undefined' && !localeObserver) {
    localeObserver = new MutationObserver(() => {
      const currentRoute = router.currentRoute?.value
      if (currentRoute) trackPageView(analyticsPageMetadata(currentRoute))
    })
    localeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['lang'],
    })
  }
}

/** Release the latest pending page view after optional language packs select the UI locale. */
export function activateAppAnalytics(): void {
  const currentRoute = analyticsRouter?.currentRoute?.value
  if (currentRoute?.matched.length) pendingPage = analyticsPageMetadata(currentRoute)
  pageTrackingReady = true
  if (pendingPage) trackPageView(pendingPage)
}
