import type { AppErrorShape, ProblemDetails } from './types'
import { isBrowserNetworkMessage } from './registry'

export function isAbortError(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false
  if ('name' in err && (err as { name?: unknown }).name === 'AbortError') return true
  const message = String(
    ('message' in err ? (err as { message?: unknown }).message : err) ?? '',
  ).toLowerCase()
  return message.includes('aborted') || message.includes('abort')
}

export function networkUnavailableError(): AppErrorShape {
  return {
    status: 0,
    message: '',
    code: 'NETWORK.UNAVAILABLE',
    errorCode: 'NETWORK.UNAVAILABLE',
    retryable: true,
  }
}

export function unwrapProblemDetails(data: unknown): ProblemDetails | null {
  if (!data || typeof data !== 'object') return null
  const root = data as Record<string, unknown>

  const candidates: unknown[] = [root]
  if ('data' in root) candidates.push(root.data)
  if ('body' in root && typeof root.body === 'object') {
    candidates.push((root.body as Record<string, unknown>).data)
    candidates.push(root.body)
  }

  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== 'object') continue
    const obj = candidate as Record<string, unknown>
    const code = obj.code
    if (typeof code === 'string' && code.includes('.')) {
      return obj as ProblemDetails
    }
    if (typeof obj.error_code === 'string' && obj.error_code) {
      return {
        status: typeof obj.status === 'number' ? obj.status : undefined,
        code: obj.error_code,
        error_code: obj.error_code,
        trace_id: typeof obj.trace_id === 'string' ? obj.trace_id : undefined,
        request_id: typeof obj.request_id === 'string' ? obj.request_id : undefined,
        message: typeof obj.message === 'string' ? obj.message : undefined,
        meta: typeof obj.meta === 'object' && obj.meta ? (obj.meta as Record<string, unknown>) : undefined,
      }
    }
  }
  return null
}

export function problemDetailsToApiError(res: Response, data: unknown): AppErrorShape {
  const problem = unwrapProblemDetails(data)
  const registryCode =
    (typeof problem?.code === 'string' && problem.code) ||
    (typeof problem?.error_code === 'string' && problem.error_code) ||
    ''

  const traceId =
    (typeof problem?.trace_id === 'string' && problem.trace_id) ||
    (typeof problem?.request_id === 'string' && problem.request_id) ||
    res.headers.get('X-Request-Id') ||
    undefined

  const fieldErrors = Array.isArray(problem?.errors) ? problem.errors : []
  const fields: Record<string, string[]> = {}
  for (const item of fieldErrors) {
    const field = item.field || 'non_field_errors'
    const msg = item.message || item.code || 'invalid'
    fields[field] = [...(fields[field] || []), msg]
  }

  return {
    status: res.status,
    message: typeof problem?.title === 'string' ? problem.title : res.statusText || 'Request failed',
    code: registryCode || undefined,
    errorCode: registryCode || undefined,
    traceId,
    retryable: Boolean(problem?.retryable),
    detail: data,
    fields: Object.keys(fields).length ? fields : undefined,
    meta: problem?.meta,
  }
}

export function normalizeThrownError(err: unknown): AppErrorShape {
  if (isAbortError(err)) {
    return {
      status: 0,
      message: '',
      code: 'CLIENT.ABORTED',
      errorCode: 'CLIENT.ABORTED',
    }
  }

  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    return networkUnavailableError()
  }

  if ((err instanceof TypeError && isBrowserNetworkMessage(err.message)) || (err instanceof Error && isBrowserNetworkMessage(err.message))) {
    return networkUnavailableError()
  }

  if (err && typeof err === 'object') {
    const o = err as AppErrorShape
    if (typeof o.status === 'number' && (o.errorCode || o.code || o.message)) {
      const code = o.errorCode || o.code
      if (o.message && isBrowserNetworkMessage(o.message)) {
        return { ...networkUnavailableError(), detail: o.detail, traceId: o.traceId }
      }
      return {
        status: o.status,
        message: o.message || '',
        code,
        errorCode: code,
        traceId: o.traceId,
        retryable: o.retryable,
        detail: o.detail,
        fields: o.fields,
        meta: o.meta,
      }
    }
  }

  if (err instanceof Error && err.message) {
    if (isBrowserNetworkMessage(err.message)) return networkUnavailableError()
    return {
      status: 0,
      message: err.message,
      code: 'UNKNOWN.ERROR',
      errorCode: 'UNKNOWN.ERROR',
    }
  }

  return {
    status: 0,
    message: typeof err === 'string' ? err : 'Request failed',
    code: 'UNKNOWN.ERROR',
    errorCode: 'UNKNOWN.ERROR',
  }
}

export function toApiError(err: unknown): AppErrorShape {
  return normalizeThrownError(err)
}
