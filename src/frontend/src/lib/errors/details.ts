import { reactive, readonly, type DeepReadonly } from 'vue'
import { normalizeThrownError } from './normalizer'
import { resolveErrorMessage } from './resolver'

export type ErrorEntity = {
  id: string | number
  name: string
  type: 'source' | 'repository' | 'node' | 'child_task'
  error?: string
}

export type ErrorDetailsPayload = {
  title: string
  summary: string
  errorCode?: string
  traceId?: string
  issue?: string
  reasons?: string[]
  resolutions?: string[]
  rawDetail?: unknown
  /** R2 / R11 — distinct chrome: error (red) vs warning (amber) */
  severity?: 'error' | 'warning'
  /** R3 — "open related task" link in dialog footer */
  taskUuid?: string
  /** R4 — for correlation / support bundle */
  taskType?: string
  /** R7 — per-source / repository / node / child-task detail */
  entities?: ErrorEntity[]
  /** R4 — which step failed */
  failedStep?: string
  /** R4 — partial / warning residue details */
  cleanupResidue?: {
    hasResidue: boolean
    retainedResources?: string[]
    failures?: string[]
    skippedItems?: string[]
  }
}

export type ErrorDetailsOverrides = Partial<ErrorDetailsPayload>

const SENSITIVE_KEY = /(authorization|cookie|password|passwd|secret|token|api[_-]?key|access[_-]?key|oauth[_-]?code|captcha)/i
const MAX_COPY_DEPTH = 8

function redactSensitiveText(value: string): string {
  return value
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, 'Bearer [REDACTED]')
    .replace(/([?&](?:authorization|cookie|password|passwd|secret|token|api[_-]?key|access[_-]?key|oauth[_-]?code|captcha)\s*=\s*)([^&#\s]+)/gi, '$1[REDACTED]')
    .replace(
      /((?:authorization|cookie|password|passwd|secret|token|api[_-]?key|access[_-]?key|oauth[_-]?code|captcha)\s*["']?\s*[:=]\s*)(["'][^"']*["']|[^\s,;}&]+)/gi,
      '$1[REDACTED]',
    )
}

function redactValue(value: unknown, depth = 0, seen = new WeakSet<object>()): unknown {
  if (depth > MAX_COPY_DEPTH) return '[Truncated]'
  if (value == null || typeof value === 'number' || typeof value === 'boolean') return value
  if (typeof value === 'string') return redactSensitiveText(value)
  if (typeof value !== 'object') return String(value)
  if (seen.has(value)) return '[Circular]'
  seen.add(value)

  if (Array.isArray(value)) return value.map((item) => redactValue(item, depth + 1, seen))

  const output: Record<string, unknown> = {}
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    output[key] = SENSITIVE_KEY.test(key) ? '[REDACTED]' : redactValue(item, depth + 1, seen)
  }
  return output
}

export function safeErrorDetailText(value: unknown): string {
  if (value == null || value === '') return ''
  if (typeof value === 'string') return redactSensitiveText(value)
  try {
    return JSON.stringify(redactValue(value), null, 2)
  } catch {
    return String(value)
  }
}

export function toErrorDetails(error: unknown, overrides: ErrorDetailsOverrides = {}): ErrorDetailsPayload {
  const normalized = normalizeThrownError(error)
  const summary = overrides.summary || resolveErrorMessage(error, undefined, normalized.message || 'Request failed')
  const normalizedCode = normalized.errorCode || normalized.code
  return sanitizeErrorDetails({
    ...overrides,
    title: overrides.title || summary || 'Operation failed',
    summary: summary || 'Request failed',
    errorCode: overrides.errorCode || (normalizedCode === 'UNKNOWN.ERROR' ? undefined : normalizedCode),
    traceId: overrides.traceId || normalized.traceId,
    issue: overrides.issue || summary,
    reasons: overrides.reasons,
    resolutions: overrides.resolutions,
    rawDetail: overrides.rawDetail ?? normalized.detail,
  })
}

function sanitizeErrorDetails(payload: ErrorDetailsPayload): ErrorDetailsPayload {
  return {
    ...payload,
    rawDetail: redactValue(payload.rawDetail),
    errorCode: payload.errorCode ? redactSensitiveText(payload.errorCode) : undefined,
    traceId: payload.traceId ? redactSensitiveText(payload.traceId) : undefined,
    failedStep: payload.failedStep ? redactSensitiveText(payload.failedStep) : undefined,
    cleanupResidue: payload.cleanupResidue ? {
      ...payload.cleanupResidue,
      retainedResources: payload.cleanupResidue.retainedResources?.map(redactSensitiveText),
      failures: payload.cleanupResidue.failures?.map(redactSensitiveText),
      skippedItems: payload.cleanupResidue.skippedItems?.map(redactSensitiveText),
    } : undefined,
    title: redactSensitiveText(payload.title),
    summary: redactSensitiveText(payload.summary),
    issue: payload.issue ? redactSensitiveText(payload.issue) : undefined,
    reasons: payload.reasons?.map(redactSensitiveText),
    resolutions: payload.resolutions?.map(redactSensitiveText),
    entities: payload.entities?.map(e => ({
      ...e,
      id: typeof e.id === 'string' ? redactSensitiveText(e.id) : e.id,
      name: redactSensitiveText(e.name),
      error: e.error ? redactSensitiveText(e.error) : undefined,
    })),
  }
}

const detailsState = reactive<{ current: ErrorDetailsPayload | null; currentTaskUuid?: string }>({ current: null })

export const errorDetailsState = readonly(detailsState)

export function openErrorDetails(
  payload: ErrorDetailsPayload | { error: unknown; overrides?: ErrorDetailsOverrides },
  context: { currentTaskUuid?: string } = {},
) {
  detailsState.currentTaskUuid = context.currentTaskUuid
  detailsState.current = 'error' in payload
    ? toErrorDetails(payload.error, payload.overrides)
    : sanitizeErrorDetails(payload)
}

export function closeErrorDetails() {
  detailsState.current = null
  detailsState.currentTaskUuid = undefined
}

export function errorDetailsCopyText(payload: ErrorDetailsPayload | DeepReadonly<ErrorDetailsPayload>): string {
  const sections = [
    payload.title,
    payload.summary,
    payload.severity ? `Severity: ${payload.severity}` : '',
    payload.errorCode ? `Error code: ${payload.errorCode}` : '',
    payload.traceId ? `Error ID: ${payload.traceId}` : '',
    payload.taskUuid ? `Task UUID: ${payload.taskUuid}` : '',
    payload.taskType ? `Task type: ${payload.taskType}` : '',
    payload.failedStep ? `Failed step: ${payload.failedStep}` : '',
    payload.issue ? `Issue:\n${payload.issue}` : '',
    payload.reasons?.length ? `Possible reasons:\n${payload.reasons.map((item, index) => `${index + 1}. ${item}`).join('\n')}` : '',
    payload.resolutions?.length ? `How to resolve:\n${payload.resolutions.map((item, index) => `${index + 1}. ${item}`).join('\n')}` : '',
    payload.entities?.length ? `Affected entities:\n${payload.entities.map(e => `  - [${e.type}] ${e.name} (ID: ${e.id})${e.error ? `: ${e.error}` : ''}`).join('\n')}` : '',
    payload.cleanupResidue?.hasResidue ? 'Cleanup residue: present' : '',
    payload.cleanupResidue?.retainedResources?.length ? `Retained resources:\n${payload.cleanupResidue.retainedResources.map(r => `  - ${r}`).join('\n')}` : '',
    payload.cleanupResidue?.failures?.length ? `Cleanup failures:\n${payload.cleanupResidue.failures.map(r => `  - ${r}`).join('\n')}` : '',
    payload.cleanupResidue?.skippedItems?.length ? `Skipped items:\n${payload.cleanupResidue.skippedItems.map(r => `  - ${r}`).join('\n')}` : '',
    safeErrorDetailText(payload.rawDetail) ? `Original details:\n${safeErrorDetailText(payload.rawDetail)}` : '',
  ]
  return redactSensitiveText(sections.filter(Boolean).join('\n\n'))
}
