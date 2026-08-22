import {
  ERROR_CODE_FALLBACK_EN,
  ERROR_CODE_I18N_KEYS,
  canonicalizeQuotaType,
  isBrowserNetworkMessage,
  isRegistryCode,
  quotaTypeMeterLabel,
} from './registry'
import { normalizeThrownError } from './normalizer'
import type { AppErrorShape } from './types'
import { quotaMeterLabelKey } from '../licenseQuotaDisplay'

export type TranslateFn = (key: string, params?: Record<string, unknown>) => string

function resolveQuotaMeterLabel(quotaType: unknown, t?: TranslateFn): string {
  const fallback = quotaTypeMeterLabel(quotaType)
  const labelKey = quotaMeterLabelKey(canonicalizeQuotaType(quotaType))
  if (t && labelKey) {
    const translated = t(labelKey)
    if (translated && translated !== labelKey) return translated
  }
  return fallback || 'resource'
}

function resolveSubscriptionQuotaMessage(
  meta: Record<string, unknown> | undefined,
  t?: TranslateFn,
): string {
  const scope = String(meta?.scope || 'organization').toLowerCase()
  const meter = resolveQuotaMeterLabel(meta?.quota_type, t)
  const hasMeter = Boolean(quotaTypeMeterLabel(meta?.quota_type))

  if (scope === 'gateway') {
    return t
      ? t('errors.codes.subscriptionQuotaExceededGateway')
      : 'The shared Data Gateway currently has insufficient capacity. Try again later or contact your platform administrator.'
  }

  if (scope === 'instance') {
    if (hasMeter) {
      return t
        ? interpolate(t('errors.codes.subscriptionQuotaExceededInstanceMeter', { meter }), { meter })
        : interpolate(
            'Shared instance {meter} capacity is full. Contact your platform administrator to raise the deployment grant or free capacity from other organizations.',
            { meter },
          )
    }
    return t
      ? t('errors.codes.subscriptionQuotaExceededInstance')
      : 'Shared instance capacity is full. Contact your platform administrator to raise the deployment grant or free capacity from other organizations.'
  }

  if (hasMeter) {
    return t
      ? interpolate(t('errors.codes.subscriptionQuotaExceededMeter', { meter }), { meter })
      : interpolate(
          '{meter} quota is full for this organization. Contact your platform administrator to raise limits.',
          { meter },
        )
  }

  return t
    ? t(ERROR_CODE_I18N_KEYS['SUBSCRIPTION.QUOTA_EXCEEDED'])
    : ERROR_CODE_FALLBACK_EN['SUBSCRIPTION.QUOTA_EXCEEDED']
}

function interpolate(template: string, params?: Record<string, unknown>): string {
  if (!params) return template
  return template.replace(/\{(\w+)\}/g, (_, key: string) => {
    const value = params[key]
    return value == null ? '' : String(value)
  })
}

function metaFromError(err: AppErrorShape): Record<string, unknown> | undefined {
  if (err.meta && typeof err.meta === 'object') return err.meta
  return undefined
}

function firstFieldError(fields: Record<string, string[]> | undefined): string | undefined {
  if (!fields) return undefined
  for (const messages of Object.values(fields)) {
    const message = messages.find((item) => item.trim())
    if (message) return message
  }
  return undefined
}

export function resolveErrorCode(err: unknown): string {
  const normalized = normalizeThrownError(err)
  const code = normalized.errorCode || normalized.code || ''
  if (isRegistryCode(code)) return code
  if (normalized.message && isBrowserNetworkMessage(normalized.message)) return 'NETWORK.UNAVAILABLE'
  return code || 'UNKNOWN.ERROR'
}

export function resolveErrorMessage(
  err: unknown,
  t?: TranslateFn,
  fallback = 'Request failed',
): string {
  const normalized = normalizeThrownError(err)
  const code = resolveErrorCode(err)
  const meta = metaFromError(normalized)

  if (code === 'CLIENT.ABORTED') return ''

  if (code === 'VALIDATION.FAILED') {
    const fieldMessage = firstFieldError(normalized.fields)
    if (fieldMessage) return fieldMessage
  }

  if (code === 'SUBSCRIPTION.QUOTA_EXCEEDED') {
    return resolveSubscriptionQuotaMessage(meta, t)
  }

  if (t && isRegistryCode(code)) {
    const key = ERROR_CODE_I18N_KEYS[code]
    if (key) return interpolate(t(key, meta), meta)
  }

  if (isRegistryCode(code)) {
    return interpolate(ERROR_CODE_FALLBACK_EN[code] || fallback, meta)
  }

  if (normalized.message && !isBrowserNetworkMessage(normalized.message)) {
    const legacyAgentMessage = humanizeLegacyAgentWsMessage(normalized.message, t)
    if (legacyAgentMessage) return legacyAgentMessage
  }

  if (normalized.message && isBrowserNetworkMessage(normalized.message)) {
    return t
      ? interpolate(t(ERROR_CODE_I18N_KEYS['NETWORK.UNAVAILABLE']), meta)
      : ERROR_CODE_FALLBACK_EN['NETWORK.UNAVAILABLE']
  }

  return fallback
}

export function resolveErrorMessageI18n(
  err: unknown,
  t: TranslateFn,
  fallback = 'Request failed',
): string {
  return resolveErrorMessage(err, t, fallback)
}

const LEGACY_AGENT_WS_NOT_ROUTABLE = 'agent websocket is not routable'
const LEGACY_AGENT_WS_RECONNECTING = 'agent websocket is reconnecting'

function humanizeLegacyAgentWsMessage(message: string, t?: TranslateFn): string | null {
  const normalized = message.trim().toLowerCase()
  if (normalized.includes(LEGACY_AGENT_WS_NOT_ROUTABLE)) {
    if (t) return t('errors.agentWsNotRoutable')
    return 'The agent node is offline or unreachable. Wait until the host is back online and try again, or use Force Cleanup to record the retained installation.'
  }
  if (normalized.includes(LEGACY_AGENT_WS_RECONNECTING)) {
    if (t) return t('errors.agentWsReconnecting')
    return 'The agent node is reconnecting. Wait a moment and try again.'
  }
  return null
}

/** Map internal agent websocket errors in plain strings to user-facing copy. */
export function humanizeLegacyErrorMessage(
  message: string,
  t?: TranslateFn,
  fallback?: string,
): string {
  const trimmed = message.trim()
  if (!trimmed) return fallback ?? ''
  const legacy = humanizeLegacyAgentWsMessage(trimmed, t)
  if (legacy) return legacy
  return trimmed
}
