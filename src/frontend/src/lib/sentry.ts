import type * as SentryTypes from '@sentry/vue'
import type { App } from 'vue'
import type { Router } from 'vue-router'

type SentryModule = typeof import('./sentrySdk')
type SentryLoader = () => Promise<SentryModule>

const loadSentryModule: SentryLoader = () => import('./sentrySdk')

const FILTERED = '[Filtered]'
const SAFE_SPAN_FIELDS = [
  'is_segment',
  'op',
  'origin',
  'parent_span_id',
  'same_process_as_parent',
  'segment_id',
  'span_id',
  'start_timestamp',
  'status',
  'timestamp',
  'trace_id',
] as const
const SAFE_TRACE_CONTEXT_FIELDS = [
  'op',
  'origin',
  'parent_span_id',
  'span_id',
  'status',
  'trace_id',
] as const

function validBrowserDsn(value: string): boolean {
  if (!value || /\s/.test(value)) return false
  try {
    const parsed = new URL(value)
    const projectId = parsed.pathname.replace(/\/+$/, '').split('/').at(-1)
    return ['http:', 'https:'].includes(parsed.protocol)
      && Boolean(parsed.hostname && parsed.username)
      && !parsed.password
      && /^\d+$/.test(projectId || '')
      && !parsed.search
      && !parsed.hash
  } catch {
    return false
  }
}

function sampleRate(value: unknown): number {
  const parsed = Number.parseFloat(String(value ?? ''))
  if (!Number.isFinite(parsed)) return 0
  return Math.max(0, Math.min(1, parsed))
}

function sanitizedOrigin(value: unknown): string | undefined {
  if (typeof value !== 'string' || !value.trim()) return undefined
  try {
    const parsed = new URL(value, window.location.origin)
    return parsed.origin
  } catch {
    return undefined
  }
}

function sanitizeSpan<T>(span: T): T {
  const source = span as Record<string, unknown>
  const sanitized: Record<string, unknown> = { data: {} }
  for (const key of SAFE_SPAN_FIELDS) {
    const value = source[key]
    if (['boolean', 'number', 'string'].includes(typeof value)) sanitized[key] = value
  }
  return sanitized as T
}

function sanitizeTraceContext(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== 'object') return undefined
  const source = value as Record<string, unknown>
  const sanitized: Record<string, unknown> = { data: {} }
  for (const key of SAFE_TRACE_CONTEXT_FIELDS) {
    const field = source[key]
    if (typeof field === 'string') sanitized[key] = field
  }
  return typeof sanitized.trace_id === 'string' && typeof sanitized.span_id === 'string'
    ? sanitized
    : undefined
}

function sanitizeEvent(
  event: SentryTypes.Event,
  routePath: string,
  surface: 'admin' | 'tenant',
): SentryTypes.Event {
  delete event.user
  delete event.message
  delete event.logentry
  delete event.extra
  delete event.fingerprint
  const traceContext = sanitizeTraceContext(event.contexts?.trace)
  event.contexts = traceContext ? { trace: traceContext } : undefined
  if (event.request) {
    const origin = sanitizedOrigin(event.request.url)
    event.request = {
      headers: {},
      ...(origin ? { url: `${origin}${routePath}` } : {}),
    }
  }
  event.transaction = routePath
  event.transaction_info = { source: 'route' }
  event.spans = event.spans?.map(sanitizeSpan)
  event.breadcrumbs = event.breadcrumbs?.map((breadcrumb) => ({
    category: breadcrumb.category,
    level: breadcrumb.level,
    timestamp: breadcrumb.timestamp,
    type: breadcrumb.type,
  }))
  for (const exception of event.exception?.values || []) {
    if (exception.value) exception.value = FILTERED
    if (exception.mechanism) delete exception.mechanism.data
    for (const frame of exception.stacktrace?.frames || []) delete frame.vars
  }
  event.tags = { component: 'hfl-frontend', product: 'hyperfilelens', surface }
  return event
}

export async function initSentry(
  app: App,
  router: Router,
  load: SentryLoader = loadSentryModule,
): Promise<void> {
  const config = window.__HFL_APP_CONFIG__
  if (!config?.sentryEnabled) return

  const dsn = String(config.sentryDsn || '').trim()
  const environment = String(config.sentryEnvironment || '').trim()
  const release = String(config.sentryRelease || '').trim()
  const surface = config.sentrySurface === 'admin' ? 'admin' : 'tenant'
  if (!validBrowserDsn(dsn) || !environment || !release) {
    console.warn('[sentry] Invalid runtime configuration; browser reporting is disabled.')
    return
  }

  try {
    const Sentry = await load()
    let activeRoutePath = '/'
    router.afterEach?.((route) => {
      const routeTemplate = route.matched?.at(-1)?.path || '/'
      activeRoutePath = routeTemplate.startsWith('/') ? routeTemplate : `/${routeTemplate}`
    })
    Sentry.init({
      app,
      dsn,
      environment,
      release,
      integrations: [Sentry.browserTracingIntegration({ router })],
      tracesSampleRate: sampleRate(config.sentryTracesSampleRate),
      sendDefaultPii: false,
      beforeSend: (event) => sanitizeEvent(event, activeRoutePath, surface),
      beforeSendTransaction: (event) => sanitizeEvent(event, activeRoutePath, surface),
      beforeSendSpan: sanitizeSpan,
      initialScope: {
        tags: {
          product: 'hyperfilelens',
          component: 'hfl-frontend',
          surface,
        },
      },
    })
  } catch (error) {
    console.warn('[sentry] Initialization failed; the application will continue.', error)
  }
}
