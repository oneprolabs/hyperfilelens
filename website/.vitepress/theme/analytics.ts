import { inBrowser } from 'vitepress'

type GoogleTag = (...args: unknown[]) => void
export type WebsiteOpenAppPlacement =
  | 'header'
  | 'hero'
  | 'hosted_service'
  | 'cta'
  | 'docs_header'
  | 'footer'

declare global {
  interface Window {
    __HFL_WEBSITE_CONFIG__?: {
      appUrl?: string
      gaMeasurementId?: string
    }
    dataLayer?: unknown[]
    gtag?: GoogleTag
  }
}

const GA4_MEASUREMENT_ID = /^G-[A-Z0-9]+$/
const NAVIGATION_FALLBACK_MS = 400
let activeMeasurementId = ''
let activePagePath = '/'
let lastTrackedPagePath = ''

function sanitizedPath(value: string): string {
  try {
    const parsed = new URL(value, window.location.origin)
    return parsed.pathname || '/'
  } catch {
    return '/'
  }
}

function sanitizedReferrer(): string {
  if (!document.referrer) return ''
  try {
    const parsed = new URL(document.referrer)
    return parsed.origin
  } catch {
    return ''
  }
}

function configureGoogleTag(): boolean {
  if (!inBrowser) return false
  const measurementId = String(
    window.__HFL_WEBSITE_CONFIG__?.gaMeasurementId || '',
  ).trim()
  if (!measurementId) return false
  if (!GA4_MEASUREMENT_ID.test(measurementId)) {
    console.warn('[analytics] Invalid GA4 measurement ID; analytics is disabled.')
    return false
  }
  if (activeMeasurementId === measurementId) return true

  activeMeasurementId = measurementId
  lastTrackedPagePath = ''
  window.dataLayer = window.dataLayer || []
  // Google Tag distinguishes its Arguments command object from a normal Array.
  window.gtag = function gtag() {
    // eslint-disable-next-line prefer-rest-params
    window.dataLayer?.push(arguments)
  }
  window.gtag('js', new Date())
  window.gtag('config', measurementId, { send_page_view: false })

  const script = document.createElement('script')
  script.async = true
  script.dataset.hflGoogleAnalytics = 'true'
  script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`
  document.head.appendChild(script)
  return true
}

export function trackWebsitePageView(value: string): void {
  if (!activeMeasurementId || !window.gtag) return
  const path = sanitizedPath(value)
  if (path === lastTrackedPagePath) return
  lastTrackedPagePath = path
  activePagePath = path
  window.gtag('event', 'page_view', {
    page_location: `${window.location.origin}${path}`,
    page_path: path,
    page_referrer: sanitizedReferrer(),
    page_title: 'HyperFileLens Website',
  })
}

function websiteEventContext() {
  return {
    page_location: `${window.location.origin}${activePagePath}`,
    page_path: activePagePath,
    page_referrer: sanitizedReferrer(),
    page_title: 'HyperFileLens Website',
  }
}

export function trackWebsiteOpenApp(
  placement: WebsiteOpenAppPlacement,
  navigate?: () => void,
): void {
  if (!activeMeasurementId || !window.gtag) {
    navigate?.()
    return
  }

  if (!navigate) {
    window.gtag('event', 'website_open_app', {
      placement,
      ...websiteEventContext(),
    })
    return
  }

  let completed = false
  const completeNavigation = () => {
    if (completed) return
    completed = true
    window.clearTimeout(fallback)
    navigate()
  }
  const fallback = window.setTimeout(completeNavigation, NAVIGATION_FALLBACK_MS)
  try {
    window.gtag('event', 'website_open_app', {
      placement,
      ...websiteEventContext(),
      transport_type: 'beacon',
      event_callback: completeNavigation,
      event_timeout: NAVIGATION_FALLBACK_MS - 50,
    })
  } catch {
    completeNavigation()
  }
}

export function initWebsiteAnalytics(): void {
  if (!configureGoogleTag()) return
  trackWebsitePageView(window.location.pathname)
}
