import { apiErrorMessageI18n } from './api'
import { toErrorDetails, type ErrorDetailsPayload } from './errors/details'

type Translate = (key: string) => string

// DingTalk can return a Chinese-only keyword rejection, regardless of the UI
// locale. Construct the provider markers without embedding localized copy in
// the English application source.
const chineseKeyword = String.fromCodePoint(20851, 38190, 35789)
const chineseMismatch = String.fromCodePoint(19981, 21305, 37197)
const chineseNotIncluded = [
  String.fromCodePoint(19981, 21253, 21547),
  String.fromCodePoint(26410, 21253, 21547),
]

function isKeywordRejection(diagnostic: string): boolean {
  if (/keyword.{0,30}(?:mismatch|not match|not found)/i.test(diagnostic)) return true
  const keywordIndex = diagnostic.indexOf(chineseKeyword)
  if (keywordIndex === -1) return false
  const nearby = diagnostic.slice(Math.max(0, keywordIndex - 15), keywordIndex + chineseKeyword.length + 15)
  return nearby.includes(chineseMismatch) || chineseNotIncluded.some((marker) => nearby.includes(marker))
}

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
    const isKeywordFailure = isKeywordRejection(rejection.diagnostic)
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
