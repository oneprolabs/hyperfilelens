import { toApiError } from '../../../lib/errors'

export const SNAPSHOT_DOWNLOAD_SIZE_LIMIT_EXCEEDED_CODE =
  'PROTECTION.SNAPSHOT_DOWNLOAD_SIZE_LIMIT_EXCEEDED'

type SnapshotDownloadSizeLimit = {
  selectedBytes: number
  limitBytes: number
}

function validByteCount(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? number : null
}

function textCandidates(error: unknown) {
  if (error instanceof Error) return [error.message]
  if (!error || typeof error !== 'object') return [String(error || '')]
  const record = error as Record<string, unknown>
  return [
    record.message,
    record.fields,
    record.detail,
  ].map((value) => {
    if (typeof value === 'string') return value
    try {
      return value ? JSON.stringify(value) : ''
    } catch {
      return ''
    }
  })
}

function legacySizeLimit(error: unknown): SnapshotDownloadSizeLimit | null {
  for (const message of textCandidates(error)) {
    const match = message.match(
      /Selected data is (\d+) bytes, exceeding the (\d+)-byte download limit/i,
    )
    if (match) {
      return {
        selectedBytes: Number(match[1]),
        limitBytes: Number(match[2]),
      }
    }
  }
  return null
}

/**
 * Prefer structured size metadata, while retaining a legacy parser so the UI
 * remains friendly during rolling upgrades where the backend may still return
 * the old raw-byte validation message.
 */
export function snapshotDownloadSizeLimit(error: unknown): SnapshotDownloadSizeLimit | null {
  const normalized = toApiError(error)
  const selectedBytes = validByteCount(normalized.meta?.selected_size_bytes)
  const limitBytes = validByteCount(normalized.meta?.max_size_bytes)
  if (
    normalized.errorCode === SNAPSHOT_DOWNLOAD_SIZE_LIMIT_EXCEEDED_CODE
    && selectedBytes !== null
    && limitBytes !== null
  ) {
    return { selectedBytes, limitBytes }
  }
  return legacySizeLimit(error)
}
