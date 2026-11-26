import type { ComposerTranslation } from 'vue-i18n'

type Data = Record<string, unknown>
const codes = new Set(['CACHE_SPACE_INSUFFICIENT', 'CACHE_SPACE_CHECK_FAILED'])
const key = 'ops.task.failureDetails.cache'

function record(value: unknown): Data {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}
}

function bytes(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : undefined
}

type CacheFailure = {
  code: string
  path: string
  available: number | undefined
  blocking: number | undefined
  warning: number | undefined
  raw: string
}

/** Prefer task/event facts; parse the older Agent message only for missing facts. */
export function cacheFailureDetails(value: unknown): CacheFailure | null {
  const input = record(value)
  const payload = record(input.result_payload)
  const terminal = record(input.terminal_failure || payload.terminal_failure)
  const error = record(input.error_details)
  const technical = record(error.technical_detail)
  const sources = [terminal, input, payload, error, record(technical.terminal_failure), technical]
  for (const source of sources) {
    const message = String(source.error_message || source.message || source.summary || '').trim()
    const explicit = String(source.error_code || source.reason_code || '')
    const code = codes.has(explicit) ? explicit : message.match(/^(?:\[(CACHE_SPACE_(?:INSUFFICIENT|CHECK_FAILED))\]\s*)?(CACHE_SPACE_(?:INSUFFICIENT|CHECK_FAILED)):/)?.[2]
    if (!code) continue
    const raw = message || String(input.error_message || '')
    const check = sources.map(item => record(item.cache_space_check)).find(item => Object.keys(item).length) || {}
    const policy = record(check.policy)
    const legacy = raw.match(/cache "([\s\S]*?)" has (\d+) available bytes; backup requires (\d+) bytes \(warning (\d+)\)/)
    const legacyPath = raw.match(/(?:cache|cache space for) "([\s\S]*?)"/)?.[1]
    return {
      code,
      path: String(check.cache_root || terminal.cache_root || source.cache_root || legacy?.[1] || legacyPath || ''),
      // A failed query is unknown, even if an older payload serializes zero.
      available: code === 'CACHE_SPACE_CHECK_FAILED' ? undefined : bytes(check.available_bytes) ?? bytes(legacy ? Number(legacy[2]) : undefined),
      blocking: bytes(policy.blocking_bytes) ?? bytes(legacy ? Number(legacy[3]) : undefined),
      warning: bytes(policy.warning_bytes) ?? bytes(legacy ? Number(legacy[4]) : undefined),
      raw,
    }
  }
  const events = Array.isArray(input.recent_events) ? input.recent_events : []
  for (const event of events) {
    const found = cacheFailureDetails(record(record(event).metadata))
    if (found) return found
  }
  return null
}

export function cacheFailurePresentation(value: unknown, t: ComposerTranslation) {
  const detail = cacheFailureDetails(value)
  if (!detail) return null
  const blocked = detail.code === 'CACHE_SPACE_INSUFFICIENT'
  const size = (value: number) => {
    const gib = value / (2 ** 30)
    // Keep the preflight decision truthful when values sit just below a threshold.
    return value === 0 ? '0 GiB' : gib < 0.01 ? '< 0.01 GiB' : `${Math.floor(gib * 100) / 100} GiB`
  }
  return {
    ...detail,
    title: t(`${key}.${blocked ? 'insufficientTitle' : 'checkFailedTitle'}`),
    description: t(`${key}.${blocked ? 'insufficientDescription' : 'checkFailedDescription'}`),
    measurements: [
      { label: t(`${key}.available`), value: detail.available === undefined ? t(`${key}.unknown`) : size(detail.available) },
      ...(detail.blocking === undefined ? [] : [{ label: t(`${key}.blocking`), value: size(detail.blocking) }]),
      ...(detail.warning === undefined ? [] : [{ label: t(`${key}.warning`), value: size(detail.warning) }]),
    ],
    resolutions: (blocked ? ['freeSpace', 'relocate', 'lowerBlocking', 'retry'] : ['checkMount', 'checkPermissions', 'retryCheck'])
      .map(item => t(`${key}.${item}`)),
  }
}
