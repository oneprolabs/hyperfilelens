import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'

type GoogleTag = (...args: unknown[]) => void
type AppAnalyticsEvent = 'login' | 'sign_up' | 'sign_up_started'
type AppAnalyticsParameters = { method: 'email' | 'google' }

declare global {
  interface Window {
    dataLayer?: unknown[]
    gtag?: GoogleTag
  }
}

const GA4_MEASUREMENT_ID = /^G-[A-Z0-9]+$/
const GOOGLE_TAG_SCRIPT = 'script[data-hfl-google-analytics]'

let activeMeasurementId = ''
let activePagePath = '/'

function configuredMeasurementId(): string {
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

function trackPageView(path: string): void {
  if (!activeMeasurementId || !window.gtag) return
  activePagePath = path
  window.gtag('event', 'page_view', {
    page_location: `${window.location.origin}${path}`,
    page_path: path,
    page_referrer: sanitizedReferrer(),
    page_title: 'HyperFileLens',
  })
}

export function trackAppEvent(
  name: AppAnalyticsEvent,
  parameters: AppAnalyticsParameters,
): void {
  if (!activeMeasurementId || !window.gtag) return
  window.gtag('event', name, {
    ...parameters,
    page_location: `${window.location.origin}${activePagePath}`,
    page_path: activePagePath,
    page_referrer: sanitizedReferrer(),
    page_title: 'HyperFileLens',
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
  router.afterEach((route) => trackPageView(analyticsRoutePath(route)))
}
