import { unwrapProblemDetails } from './errors'
import { getCorrelationHeaders } from './requestContext'

export type RefreshTokenResult = {
  ok: boolean
  errorCode?: string
  status?: number
  networkError?: boolean
}

const API_BASE = import.meta.env.VITE_API_BASE?.toString() || ''

let refreshPromise: Promise<RefreshTokenResult> | null = null

async function readBody(res: Response) {
  const text = await res.text()
  try {
    return text ? JSON.parse(text) : null
  } catch {
    return text
  }
}

function extractRefreshErrorCode(data: unknown): string | undefined {
  const problem = unwrapProblemDetails(data)
  if (problem?.code && typeof problem.code === 'string') return problem.code
  if (problem?.error_code && typeof problem.error_code === 'string') return problem.error_code

  if (data && typeof data === 'object') {
    const d = data as Record<string, unknown>
    if ('data' in d && typeof d.data === 'object') {
      const inner = d.data as Record<string, unknown>
      if (typeof inner.code === 'string' && inner.code.includes('.')) return inner.code
      if (typeof inner.error_code === 'string') return inner.error_code
      if ('error' in inner && typeof inner.error === 'object') {
        const err = inner.error as Record<string, unknown>
        if (typeof err.error_code === 'string') return err.error_code
      }
    }
    if ('error' in d && typeof d.error === 'object') {
      const err = d.error as Record<string, unknown>
      if (typeof err.error_code === 'string') return err.error_code
    }
    if (typeof d.error_code === 'string') return d.error_code
  }
  return undefined
}

async function refreshToken(): Promise<RefreshTokenResult> {
  try {
    const res = await fetch(`${API_BASE}/api/v1/auth/token/refresh`, {
      method: 'POST',
      credentials: 'include',
      headers: getCorrelationHeaders(),
    })
    if (res.ok) return { ok: true, status: res.status }
    const data = await readBody(res)
    return { ok: false, errorCode: extractRefreshErrorCode(data), status: res.status }
  } catch {
    return { ok: false, networkError: true }
  }
}

export function refreshAuthToken(): Promise<RefreshTokenResult> {
  if (!refreshPromise) {
    refreshPromise = refreshToken().finally(() => {
      refreshPromise = null
    })
  }
  return refreshPromise
}
