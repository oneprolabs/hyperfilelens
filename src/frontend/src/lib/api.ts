import { clearAuth } from '../composables/useAuth'
import {
  isAbortError as errorsIsAbortError,
  isBrowserNetworkMessage,
  networkUnavailableError,
  problemDetailsToApiError,
  resolveErrorMessage,
  resolveErrorMessageI18n,
  unwrapProblemDetails,
} from './errors'
import { getCorrelationHeaders } from './requestContext'
import { getTraceId, logger, setTraceId } from './logger'
import { refreshAuthToken } from './authRefresh'
import { getRouteRequestSignal } from './routeRequestAbort'
import { isSessionInvalidReason, storeSessionNotice } from './sessionNotice'

export type ApiError = {
  status: number
  message: string
  code?: string
  errorCode?: string
  detail?: unknown
  fields?: Record<string, string[]>
}

const API_BASE = import.meta.env.VITE_API_BASE?.toString() || ''

let currentLocale = 'en'
let isHandlingSessionExpired = false

export function setLocale(locale: string) {
  currentLocale = locale
}

export function getLocale() {
  return currentLocale
}

async function readBody(res: Response) {
  const text = await res.text()
  try {
    return text ? JSON.parse(text) : null
  } catch {
    return text
  }
}

function extractTopLevelCode(data: unknown): string | undefined {
  if (data && typeof data === 'object') {
    const d = data as Record<string, unknown>
    if ('code' in d && typeof d.code === 'string') return d.code
  }
  return undefined
}

function extractErrorCode(data: unknown): string | undefined {
  const problem = unwrapProblemDetails(data)
  if (problem?.code && typeof problem.code === 'string') return problem.code
  if (problem?.error_code && typeof problem.error_code === 'string') return problem.error_code

  if (data && typeof data === 'object') {
    const d = data as Record<string, unknown>
    if ('data' in d && d.data && typeof d.data === 'object') {
      const inner = d.data as Record<string, unknown>
      if (typeof inner.code === 'string' && inner.code.includes('.')) return inner.code
      if ('error_code' in inner) return inner.error_code as string
      if ('error' in inner && typeof inner.error === 'object') {
        const err = inner.error as Record<string, unknown>
        if ('error_code' in err) return err.error_code as string
      }
    }
    if ('error' in d && typeof d.error === 'object') {
      const err = d.error as Record<string, unknown>
      if ('error_code' in err) return err.error_code as string
    }
    if ('error_code' in d) return d.error_code as string
  }
  return undefined
}

function extractFields(data: unknown): Record<string, string[]> | undefined {
  if (data && typeof data === 'object') {
    const d = data as Record<string, unknown>
    // data.data.error.fields (the actual response structure)
    if ('data' in d && d.data && typeof d.data === 'object') {
      const inner = d.data as Record<string, unknown>
      if ('error' in inner && typeof inner.error === 'object') {
        const err = inner.error as Record<string, unknown>
        if ('fields' in err && typeof err.fields === 'object') return err.fields as Record<string, string[]>
      }
    }
    // data.error.fields (single-nested)
    if ('error' in d && typeof d.error === 'object') {
      const err = d.error as Record<string, unknown>
      if ('fields' in err && typeof err.fields === 'object') return err.fields as Record<string, string[]>
    }
    if ('fields' in d && typeof d.fields === 'object') return d.fields as Record<string, string[]>
  }
  return undefined
}

function extractMessage(data: unknown): string | undefined {
  if (data && typeof data === 'object') {
    const d = data as Record<string, unknown>
    // data.data.error.message (the actual response structure)
    if ('data' in d && d.data && typeof d.data === 'object') {
      const inner = d.data as Record<string, unknown>
      if ('message' in inner) return inner.message as string
      if ('error' in inner && typeof inner.error === 'object') {
        const err = inner.error as Record<string, unknown>
        if ('message' in err) return err.message as string
      }
    }
    // data.error.message (single-nested)
    if ('error' in d && typeof d.error === 'object') {
      const err = d.error as Record<string, unknown>
      if ('message' in err) return err.message as string
    }
    if ('message' in d) return d.message as string
  }
  return undefined
}

async function handleSessionExpired(reason = 'REFRESH_EXPIRED'): Promise<void> {
  if (isHandlingSessionExpired) return
  isHandlingSessionExpired = true

  try {
    void fetch(`${API_BASE}/api/v1/auth/logout`, {
      method: 'POST',
      credentials: 'include',
      headers: getCorrelationHeaders({
        'Accept-Language': currentLocale,
      }),
    }).catch(() => undefined)

    const { router } = await import('../router')
    const currentRoute = router.currentRoute.value
    const redirect = currentRoute.fullPath || '/'

    clearAuth()

    if (currentRoute.path !== '/login') {
      storeSessionNotice(reason)
      await router.replace({
        path: '/login',
        query: {
          redirect,
        },
      })
    }
  } catch {
    clearAuth()
    if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
      const redirect = `${window.location.pathname}${window.location.search}${window.location.hash}`
      storeSessionNotice(reason)
      const params = new URLSearchParams({ redirect })
      window.location.assign(`/login?${params.toString()}`)
    }
  } finally {
    window.setTimeout(() => {
      isHandlingSessionExpired = false
    }, 1000)
  }
}

function buildApiError(res: Response, data: unknown): ApiError {
  const fromProblem = problemDetailsToApiError(res, data)
  if (fromProblem.errorCode) return fromProblem as ApiError

  const code = extractTopLevelCode(data)
  const errorCode = extractErrorCode(data)
  const fields = extractFields(data)
  const msg = extractMessage(data) || res.statusText || 'Request failed'
  return {
    status: res.status,
    message: isBrowserNetworkMessage(msg) ? '' : msg,
    errorCode: errorCode || (isBrowserNetworkMessage(msg) ? 'NETWORK.UNAVAILABLE' : undefined),
    code,
    detail: data,
    fields,
  }
}

/** Human-readable message from thrown {@link ApiError} or unknown values. */
export function apiErrorMessage(err: unknown, fallback = 'Request failed'): string {
  return resolveErrorMessage(err, undefined, fallback)
}

/** Like {@link apiErrorMessage}, but maps stable backend errors to i18n keys when possible. */
export function apiErrorMessageI18n(
  err: unknown,
  t: (key: string) => string,
  fallback = 'Request failed',
): string {
  return resolveErrorMessageI18n(err, t, fallback)
}

export function isAbortError(err: unknown): boolean {
  return errorsIsAbortError(err)
}

function readCsrfToken(): string {
  if (typeof document === 'undefined') return ''
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)
  return match ? decodeURIComponent(match[1]) : ''
}

function isUnsafeMethod(method: string): boolean {
  const m = method.toUpperCase()
  return m !== 'GET' && m !== 'HEAD' && m !== 'OPTIONS' && m !== 'TRACE'
}

function shouldAttachRouteSignal(method: string): boolean {
  const m = method.toUpperCase()
  return m === 'GET' || m === 'HEAD'
}

function mergeAbortSignals(...signals: (AbortSignal | null | undefined)[]): AbortSignal | undefined {
  const activeSignals = signals.filter((signal): signal is AbortSignal => !!signal)
  if (activeSignals.length === 0) return undefined
  if (activeSignals.length === 1) return activeSignals[0]
  if (activeSignals.some((signal) => signal.aborted)) {
    return AbortSignal.abort()
  }
  const controller = new AbortController()
  const abort = () => controller.abort()
  for (const signal of activeSignals) {
    signal.addEventListener('abort', abort, { once: true })
  }
  return controller.signal
}

function silentAbortError(): Error {
  const err = new Error('')
  err.name = 'AbortError'
  return err
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || 'GET').toUpperCase()
  const csrf = isUnsafeMethod(method) ? readCsrfToken() : ''
  const isMultipart = typeof FormData !== 'undefined' && init?.body instanceof FormData
  // A component-scoped signal already owns this request's lifecycle. Combining it
  // with the global route signal would also abort on same-page query changes.
  const routeSignal = shouldAttachRouteSignal(method) && !init?.signal
    ? getRouteRequestSignal()
    : undefined
  const signal = mergeAbortSignals(init?.signal, routeSignal)
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal,
      headers: {
        ...(isMultipart ? {} : { 'Content-Type': 'application/json' }),
        Accept: 'application/json',
        'Accept-Language': currentLocale,
        ...getCorrelationHeaders(),
        ...(csrf ? { 'X-CSRFToken': csrf } : {}),
        ...(init?.headers || {}),
      },
      credentials: 'include',
    })
  } catch (err) {
    if (errorsIsAbortError(err)) throw silentAbortError()
    throw networkUnavailableError()
  }

  const responseTrace = res.headers.get('X-Request-Id')
  if (responseTrace) {
    setTraceId(responseTrace)
  }

  const body = await readBody(res)

  const data = (body && typeof body === 'object' && 'body' in body)
    ? (body as { status: number; body: unknown }).body
    : body

  if (!res.ok) {
    logger.warn('api.ts', 340, 'api request failed', {
      path,
      method,
      status: res.status,
      trace_id: getTraceId(),
    })
    // Handle 401 - attempt token refresh
    if (res.status === 401) {
      const errorCode = extractErrorCode(data)

      // These security/lifecycle errors cannot be repaired by refreshing.
      // A plain missing/expired access token must still try the refresh endpoint.
      if (isSessionInvalidReason(errorCode)) {
        void handleSessionExpired(errorCode)
        throw buildApiError(res, data)
      }

      // Try to refresh token
      const refreshed = await refreshAuthToken()
      const headers = (init?.headers ?? {}) as Record<string, string>
      if (refreshed.ok && headers['X-Retry'] !== 'true') {
        // Retry the original request after cookies are renewed
        return api<T>(path, {
          ...init,
          headers: {
            ...headers,
            'X-Retry': 'true',
          },
        })
      }

      // Refresh failed or already retried
      void handleSessionExpired(refreshed.errorCode || errorCode || 'REFRESH_EXPIRED')
      throw buildApiError(res, data)
    }

    throw buildApiError(res, data)
  }

  return data as T
}
