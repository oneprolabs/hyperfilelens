import { impersonationHeaders } from './impersonation'
import { getEffectiveOrgKey } from '../composables/useAuth'
import { getTraceId } from './logger'

/** Trace id for ``X-Request-Id`` (hex, no ``t-`` prefix — nginx/backend add it in logs). */
export function getRequestTraceId(): string {
  return getTraceId().replace(/^t-/, '')
}

/** Headers shared by ``api()`` and direct ``fetch`` so nginx access logs stay grep-able. */
export function getCorrelationHeaders(extra?: Record<string, string>): Record<string, string> {
  return {
    'X-Request-Id': getRequestTraceId(),
    'X-Org-Key': getEffectiveOrgKey(),
    ...(extra || {}),
    ...impersonationHeaders(),
  }
}
