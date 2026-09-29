import { apiErrorMessageI18n } from './api'
import { toErrorDetails, type ErrorDetailsPayload } from './errors/details'

type Translate = (key: string) => string

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

/** Test errors can arrive inside either a DRF response or the API envelope. */
function findRejection(value: unknown, depth = 0): { code: string; diagnostic: string } | null {
  const object = record(value)
  if (!object || depth > 5) return null
  const code = object.errorCode ?? object.error_code ?? object.code
  if (code === 'NOTIFICATION.DINGTALK_REJECTED') {
    const nested = [object.detail, object.body, object.data]
      .map((child) => findRejection(child, depth + 1))
      .find((found) => found?.diagnostic)
    const diagnostic = typeof object.error === 'string'
      ? object.error.trim()
      : nested?.diagnostic || ''
    return { code, diagnostic }
  }
  for (const child of [object.detail, object.body, object.data]) {
    const found = findRejection(child, depth + 1)
    if (found) return found
  }
  return null
}

export function notificationTestFailureDetails(
  error: unknown,
  t: Translate,
): ErrorDetailsPayload {
  const title = t('ops.notification.testFailed')
  const rejection = findRejection(error)
  if (rejection) {
    const isKeywordFailure = /keyword.{0,30}(?:mismatch|not match|not found)|(?:不包含|未包含).{0,15}关键词|关键词.{0,15}(?:不匹配|不包含)/i.test(rejection.diagnostic)
    const summary = t('errors.codes.notificationDingtalkRejected')
    return toErrorDetails(error, {
      title,
      summary,
      issue: summary,
      errorCode: rejection.code,
      // Provider diagnostics are not localized. Keep them in the sanitized
      // technical detail, but show a localized explanation to the user.
      reasons: [t(isKeywordFailure
        ? 'ops.notification.dingtalkKeywordReason'
        : 'ops.notification.dingtalkSecurityReason')],
      resolutions: [t(isKeywordFailure
        ? 'ops.notification.dingtalkKeywordResolution'
        : 'ops.notification.dingtalkSecurityResolution')],
      rawDetail: record(error)?.detail ?? error,
    })
  }

  const fallbackError = record(error)?.error
  const summary = typeof fallbackError === 'string' && fallbackError.trim()
    ? fallbackError
    : apiErrorMessageI18n(error, t, title)
  return toErrorDetails(error, {
    title,
    summary,
    issue: summary,
  })
}
