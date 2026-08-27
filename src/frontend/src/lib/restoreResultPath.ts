export type RestoreResultPathInput = {
  key: string
  sourceDirectoryId: number
  sourcePath: string
  selectedPaths?: string[]
  sourcePathType?: 'directory' | 'file' | 'unknown'
  restoreDirectory: string
}

export type RestoreResultPath = {
  key: string
  path: string
}

type PreparedRestorePath = RestoreResultPathInput & {
  sourceToken: string
  selectedPaths: string[]
  effectiveSourcePath: string
  naturalTargetPath: string
}

function isWindowsPath(path: string) {
  return path.includes('\\') || /^[A-Za-z]:/.test(path)
}

function normalizePosixPath(path: string) {
  const raw = String(path || '').trim() || '/'
  const absolute = raw.startsWith('/')
  const parts: string[] = []
  for (const part of raw.split('/')) {
    if (!part || part === '.') continue
    if (part === '..') parts.pop()
    else parts.push(part)
  }
  const normalized = parts.join('/')
  if (!normalized) return absolute ? '/' : '.'
  return absolute ? `/${normalized}` : normalized
}

function normalizeWindowsPath(path: string) {
  const raw = String(path || '').trim().replaceAll('/', '\\')
  const drive = /^[A-Za-z]:/.exec(raw)?.[0] || ''
  const absolute = raw.slice(drive.length).startsWith('\\')
  const parts: string[] = []
  for (const part of raw.slice(drive.length).split('\\')) {
    if (!part || part === '.') continue
    if (part === '..') parts.pop()
    else parts.push(part)
  }
  const prefix = drive ? `${drive}${absolute ? '\\' : ''}` : absolute ? '\\' : ''
  return `${prefix}${parts.join('\\')}` || '.'
}

function normalizePath(path: string) {
  return isWindowsPath(path) ? normalizeWindowsPath(path) : normalizePosixPath(path)
}

function pathParts(path: string) {
  let raw = String(path || '').trim().replaceAll('\\', '/')
  if (/^[A-Za-z]:/.test(raw)) raw = raw.slice(2)
  return raw.split('/').filter((part) => part && part !== '.')
}

function pathBasename(path: string) {
  return pathParts(path).at(-1) || 'snapshot'
}

function pathParent(path: string) {
  const normalized = normalizePath(path)
  const separator = isWindowsPath(normalized) ? '\\' : '/'
  const index = normalized.lastIndexOf(separator)
  if (index < 0) return '.'
  if (index === 0) return separator
  if (separator === '\\' && index === 2 && /^[A-Za-z]:/.test(normalized)) return normalized.slice(0, 3)
  return normalized.slice(0, index)
}

function joinTargetPath(parent: string, leaf: string) {
  const windows = isWindowsPath(parent) || isWindowsPath(leaf)
  const separator = windows ? '\\' : '/'
  return normalizePath(`${String(parent || '').replace(/[\\/]+$/, '')}${separator}${String(leaf || '').replace(/^[\\/]+/, '')}`)
}

function effectiveSourcePath(sourcePath: string, selectedPaths: string[]) {
  if (selectedPaths.length !== 1) return normalizePath(sourcePath)
  const selectedPath = String(selectedPaths[0] || '').trim()
  if (!selectedPath || pathBasename(sourcePath) === pathBasename(selectedPath)) return normalizePath(sourcePath)
  return joinTargetPath(sourcePath, selectedPath)
}

function restoreSlug(value: string) {
  return Array.from(String(value || ''))
    .map((char) => /[\p{L}\p{N}.-]/u.test(char) ? char : '_')
    .join('')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
}

function fileExtension(path: string) {
  const leaf = pathBasename(path)
  const dot = leaf.lastIndexOf('.')
  return dot > 0 ? leaf.slice(dot) : ''
}

function isFileLike(item: PreparedRestorePath) {
  const selectedLeaf = item.selectedPaths.length ? pathBasename(item.selectedPaths.at(-1) || '') : ''
  if (fileExtension(selectedLeaf)) return true
  if (item.sourcePathType === 'file') return true
  if (item.sourcePathType === 'directory') return false
  return Boolean(fileExtension(item.effectiveSourcePath))
}

function safeRestoreName(item: PreparedRestorePath) {
  const parts = pathParts(item.effectiveSourcePath)
  const basename = parts.at(-1) || 'snapshot'
  const parentParts = parts.slice(0, -1)
  if (isFileLike(item)) {
    const extension = fileExtension(basename)
    const stem = extension ? basename.slice(0, -extension.length) : basename
    const displayName = restoreSlug(stem || basename) || 'snapshot'
    const sourceSlug = restoreSlug(parentParts.join('/')) || 'root'
    return `${displayName}--from-${sourceSlug}${extension}`
  }
  const displayName = restoreSlug(basename) || 'snapshot'
  const sourceSlug = restoreSlug(parentParts.join('/')) || restoreSlug(parts.join('/')) || 'root'
  return `${displayName}--from-${sourceSlug}`
}

function numberedTargetPath(path: string, counter: number, item: PreparedRestorePath) {
  const extension = isFileLike(item) ? fileExtension(item.effectiveSourcePath) : ''
  const leaf = pathBasename(path)
  const numberedLeaf = extension && leaf.endsWith(extension)
    ? `${leaf.slice(0, -extension.length)}-${counter}${extension}`
    : `${leaf}-${counter}`
  return joinTargetPath(pathParent(path), numberedLeaf)
}

/** Mirrors the restore service's final target-path calculation for pre-submit previews. */
export function resolveRestoreResultPaths(inputs: RestoreResultPathInput[]): RestoreResultPath[] {
  const seenSources = new Set<string>()
  const keysBySource = new Map<string, string[]>()
  const prepared: PreparedRestorePath[] = []
  for (const input of inputs) {
    const selectedPaths = input.selectedPaths || []
    const effective = effectiveSourcePath(input.sourcePath, selectedPaths)
    const naturalTargetPath = selectedPaths.length <= 1
      ? joinTargetPath(input.restoreDirectory, pathBasename(effective))
      : normalizePath(input.restoreDirectory)
    const sourceToken = JSON.stringify([
      input.sourceDirectoryId,
      selectedPaths,
      naturalTargetPath,
    ])
    const keys = keysBySource.get(sourceToken) || []
    if (!keys.includes(input.key)) keys.push(input.key)
    keysBySource.set(sourceToken, keys)
    if (seenSources.has(sourceToken)) continue
    seenSources.add(sourceToken)
    prepared.push({
      ...input,
      sourceToken,
      selectedPaths,
      effectiveSourcePath: effective,
      naturalTargetPath,
    })
  }

  const naturalGroups = new Map<string, PreparedRestorePath[]>()
  for (const item of prepared) {
    const group = naturalGroups.get(item.naturalTargetPath) || []
    group.push(item)
    naturalGroups.set(item.naturalTargetPath, group)
  }

  const finalTargets = new Map<string, string>()
  const result: RestoreResultPath[] = []
  for (const group of naturalGroups.values()) {
    for (const item of group) {
      let targetPath = group.length > 1
        ? joinTargetPath(pathParent(item.naturalTargetPath), safeRestoreName(item))
        : item.naturalTargetPath
      const finalKey = JSON.stringify([item.sourceDirectoryId, item.selectedPaths])
      const baseTargetPath = targetPath
      let counter = 2
      while (finalTargets.has(targetPath) && finalTargets.get(targetPath) !== finalKey) {
        targetPath = numberedTargetPath(baseTargetPath, counter, item)
        counter += 1
      }
      finalTargets.set(targetPath, finalKey)
      for (const key of keysBySource.get(item.sourceToken) || [item.key]) {
        result.push({ key, path: targetPath })
      }
    }
  }
  return result
}
