import type {
  BackupSelectableSource,
  BackupSourceDirectoryEntry,
  BackupSourceDirectoryList,
} from './sourceApi'
import { normalizeThrownError } from './errors'
import { usesUtf8Iocharset } from './nasMountOptions'

export type BackupSourceDirectoryTreeSource = Pick<
  BackupSelectableSource,
  'type' | 'platform' | 'protocol' | 'mount_options'
>

function pathBasename(path: string) {
  return String(path || '').split(/[\\/]/).filter(Boolean).pop() || ''
}

function containsRepeatedQuestionMarks(value: unknown) {
  return /\?{2,}/.test(String(value || ''))
}

export function isLikelySmbFilenameEncodingIssue(params: {
  source: BackupSourceDirectoryTreeSource | null | undefined
  label?: unknown
  path?: unknown
}) {
  const { source } = params
  if (source?.type !== 'nas' || source.protocol !== 'smb') return false
  if (usesUtf8Iocharset(source.mount_options)) return false
  return containsRepeatedQuestionMarks(params.label)
    || containsRepeatedQuestionMarks(pathBasename(String(params.path || '')))
}

export function isLikelySmbFilenamePathNotFound(params: {
  source: BackupSourceDirectoryTreeSource | null | undefined
  path: string
  error: unknown
}) {
  if (!isLikelySmbFilenameEncodingIssue({ source: params.source, path: params.path })) {
    return false
  }
  const normalized = normalizeThrownError(params.error)
  const diagnostic = String(normalized.meta?.diagnostic || '').trim().toLowerCase()
  return normalized.errorCode === 'AGENT.EXPLORER_LIST_FAILED'
    && diagnostic.includes('path not found')
}

export function shouldUseSingleDirectoryRoot(
  source: BackupSourceDirectoryTreeSource | null | undefined,
  parentPath: string,
) {
  if (parentPath || !source) return false
  if (source.type === 'nas') return true
  return source.type === 'host' && (source.platform === 'linux' || source.platform === 'macos')
}

export function selectBackupSourceDirectoryTreeEntries(params: {
  source: BackupSourceDirectoryTreeSource | null | undefined
  parentPath: string
  result: Pick<BackupSourceDirectoryList, 'root' | 'entries'>
}): BackupSourceDirectoryEntry[] {
  const { source, parentPath, result } = params
  if (shouldUseSingleDirectoryRoot(source, parentPath)) {
    return result.root ? [result.root] : []
  }
  if (result.entries.length > 0) return result.entries
  return !parentPath && result.root ? [result.root] : []
}

export function shouldAutoExpandRefreshedDirectory(params: {
  wasExpanded: boolean
  hasChildren: boolean
  expansionRevisionAtStart: number
  expansionRevisionAfterRefresh: number
}) {
  return !params.wasExpanded
    && params.hasChildren
    && params.expansionRevisionAtStart === params.expansionRevisionAfterRefresh
}
