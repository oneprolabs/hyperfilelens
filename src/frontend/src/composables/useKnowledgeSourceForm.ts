import { computed, onScopeDispose, ref, watch, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../lib/api'
import {
  browseGatewayDirectory,
  browseCopilotSnapshotDirectory,
  createKnowledgeSource,
  fetchKnowledgeSource,
  listLensGateways,
  patchKnowledgeSource,
  type LensGatewayInsight,
  type LensIngestPolicy,
} from '../lib/lensApi'
import {
  getBackupSourceSnapshot,
  listBackupSourceSnapshots,
  type BackupSourceSnapshot,
} from '../lib/protectionBackupConfigApi'
import {
  defaultLensIngestPolicy,
  normalizeLensIngestPolicy,
} from '../lib/knowledgeSourceIngestPolicy'

export const SNAPSHOT_PICKER_LATEST = 'latest' as const
export type SnapshotPickerValue = typeof SNAPSHOT_PICKER_LATEST | number

export type BackupSourcePickerOption = {
  backupConfigId: number
  label: string
  sourceType: string
  sourceRefId: number
}

export type BackupScopePickerNode = {
  id: string
  label: string
  path: string
  type: 'dir' | 'file'
  directoryId?: number
  browsePath?: string
  sourceRootPath?: string
  sizeBytes?: number
  fileCount?: number
  isLeaf: boolean
}

export type BackupScopeEntry = {
  id: string
  path: string
  directoryId: number | null
  pathType: 'dir' | 'file' | 'unknown'
  knownSizeBytes: number | null
  knownFileCount: number | null
  revision: number
}

export type KnowledgeSourceType = 'backup_source' | 'gateway_local'

export type KnowledgeSourceScopePayload = {
  source_path: string
  backup_snapshot_directory_id: number
  path_type?: 'dir' | 'file' | 'unknown'
}

export type GatewayDirTreeNode = {
  id: string
  label: string
  path: string
  isLeaf: boolean
}

const defaultIngestPolicy = defaultLensIngestPolicy

let backupScopeEntrySeq = 0

function createBackupScopeEntry(): BackupScopeEntry {
  backupScopeEntrySeq += 1
  return {
    id: `ks-scope-${backupScopeEntrySeq}`,
    path: '',
    directoryId: null,
    pathType: 'unknown',
    knownSizeBytes: null,
    knownFileCount: null,
    revision: 0,
  }
}

export function useKnowledgeSourceForm(
  editingId: Ref<number | null>,
  sourceType: Ref<KnowledgeSourceType> = ref('backup_source'),
  options: { snapshotGatewayLinkId?: Ref<number | null> } = {},
) {
  const { t } = useI18n()

  const loading = ref(false)
  const saving = ref(false)
  const gatewaysRefreshing = ref(false)

  const name = ref('')
  const gatewayId = ref<number | null>(null)
  const gateways = ref<LensGatewayInsight[]>([])
  const snapshots = ref<BackupSourceSnapshot[]>([])
  const snapshotLoading = ref(false)
  const selectedBackupConfigId = ref<number | null>(null)
  const snapshotPickerValue = ref<SnapshotPickerValue>(SNAPSHOT_PICKER_LATEST)
  const snapshotDetail = ref<BackupSourceSnapshot | null>(null)
  const backupScopeEntries = ref<BackupScopeEntry[]>([createBackupScopeEntry()])
  const latestBackupScopeValidation = new Map<string, number>()
  const pendingBackupScopeBlurValidation = new Map<string, number>()
  let backupScopeValidationSequence = 0
  let backupScopeBlurSequence = 0
  let scopeDisposed = false
  const snapshotBrowseControllers = new Set<AbortController>()
  type SnapshotBrowseResult = Awaited<ReturnType<typeof browseCopilotSnapshotDirectory>>
  const snapshotBrowseCache = new Map<string, SnapshotBrowseResult>()
  const SNAPSHOT_BROWSE_CACHE_LIMIT = 64
  const openBackupScopePickerId = ref<string | null>(null)
  const backupScopeTreeRevision = ref(0)
  const backupScopeBrowseLoading = ref(false)
  const gatewayBrowsePath = ref('')
  const gatewayBrowseRoot = ref('')
  const gatewayBrowseLoading = ref(false)
  const gatewaySelectedPath = ref('')
  const gatewayDirPickerOpen = ref(false)
  const gatewayDirTreeRevision = ref(0)
  const scanEnabled = ref(true)
  const ingestPolicy = ref<LensIngestPolicy>(defaultIngestPolicy())

  const readOnlyGatewayName = ref('')
  const readOnlySourcePath = ref('')

  async function browseInsightSnapshotDirectory(
    directoryId: number,
    params: { path?: string; limit?: number },
  ) {
    const snapshotId = effectiveSnapshotId.value
    const gatewayLinkId = snapshotReaderGatewayLinkId.value
    if (!snapshotId) throw new Error('Select a backup snapshot before browsing files.')
    if (!gatewayLinkId) throw new Error('Select an available Data Gateway before browsing files.')
    const path = params.path || ''
    const limit = Math.max(1, Math.min(params.limit || 500, 500))
    const cacheKey = [snapshotId, gatewayLinkId, directoryId, path, limit].join(':')
    const cached = snapshotBrowseCache.get(cacheKey)
    if (cached) return cached
    const controller = new AbortController()
    snapshotBrowseControllers.add(controller)
    try {
      const result = await browseCopilotSnapshotDirectory(
        directoryId,
        {
          ...params,
          path,
          limit,
          backupSourceSnapshotId: snapshotId,
          gatewayLinkId,
        },
        controller.signal,
      )
      snapshotBrowseCache.set(cacheKey, result)
      while (snapshotBrowseCache.size > SNAPSHOT_BROWSE_CACHE_LIMIT) {
        const oldest = snapshotBrowseCache.keys().next().value
        if (oldest == null) break
        snapshotBrowseCache.delete(oldest)
      }
      return result
    } catch (error) {
      // Do not retain failed or canceled browse attempts; a later expansion
      // can make a fresh request after the Reader recovers.
      snapshotBrowseCache.delete(cacheKey)
      throw error
    } finally {
      snapshotBrowseControllers.delete(controller)
    }
  }

  function cancelSnapshotBrowseRequests() {
    for (const controller of snapshotBrowseControllers) controller.abort()
    snapshotBrowseControllers.clear()
  }

  function isAbortError(error: unknown): boolean {
    return (error as { name?: string } | null)?.name === 'AbortError'
  }

  const isEditing = computed(() => editingId.value != null)
  const isBackupSource = computed(() => sourceType.value === 'backup_source')
  const isGatewayLocal = computed(() => sourceType.value === 'gateway_local')

  const aiReadyGateways = computed(() =>
    gateways.value.filter(
      (row) => row.ai_enabled && row.sidecar_status === 'online' && row.availability === 'online',
    ),
  )

  const onlineGateways = computed(() =>
    gateways.value.filter((row) => row.availability === 'online'),
  )

  const offlineGatewayCount = computed(() =>
    gateways.value.filter((row) => row.availability !== 'online').length,
  )

  const gatewaysForPicker = computed(() => {
    const online = onlineGateways.value
    if (!isEditing.value || gatewayId.value == null) return online
    const current = gateways.value.find((row) => row.id === gatewayId.value)
    if (current && !online.some((row) => row.id === current.id)) {
      return [current, ...online]
    }
    return online
  })

  const selectedGateway = computed(() =>
    gatewaysForPicker.value.find((row) => row.id === gatewayId.value)
      ?? gateways.value.find((row) => row.id === gatewayId.value)
      ?? null,
  )

  const snapshotReaderGatewayLinkId = computed(() => {
    const explicit = options.snapshotGatewayLinkId?.value
    if (explicit != null) return explicit
    return selectedGateway.value?.gateway_link_id ?? null
  })

  const snapshotDirectories = computed(() => snapshotDetail.value?.directories ?? [])

  const backupSourceOptions = computed((): BackupSourcePickerOption[] => {
    const byConfig = new Map<number, BackupSourcePickerOption>()
    for (const row of snapshots.value) {
      const configId = Number(row.backup_config_id)
      if (!Number.isFinite(configId) || configId <= 0 || byConfig.has(configId)) continue
      byConfig.set(configId, {
        backupConfigId: configId,
        label: row.source_display_name || row.backup_config_name || `#${configId}`,
        sourceType: row.source_type,
        sourceRefId: row.source_ref_id,
      })
    }
    return [...byConfig.values()].sort((a, b) => a.label.localeCompare(b.label))
  })

  const snapshotsForSelectedBackupSource = computed(() => {
    if (selectedBackupConfigId.value == null) return []
    return snapshots.value
      .filter((row) => row.backup_config_id === selectedBackupConfigId.value)
      .slice()
      .sort((a, b) => {
        const aTime = Date.parse(a.finished_at || a.started_at || a.created_at || '')
        const bTime = Date.parse(b.finished_at || b.started_at || b.created_at || '')
        return bTime - aTime
      })
  })

  const effectiveSnapshotId = computed((): number | null => {
    if (selectedBackupConfigId.value == null) return null
    if (snapshotPickerValue.value === SNAPSHOT_PICKER_LATEST) {
      return snapshotsForSelectedBackupSource.value[0]?.id ?? null
    }
    const picked = Number(snapshotPickerValue.value)
    if (!Number.isFinite(picked)) return null
    return snapshotsForSelectedBackupSource.value.some((row) => row.id === picked) ? picked : null
  })

  const effectiveSourcePath = computed(() => {
    if (isGatewayLocal.value) return gatewaySelectedPath.value.trim()
    const paths = backupScopeEntries.value
      .map((row) => row.path.trim())
      .filter(Boolean)
    return paths.join(', ')
  })

  const backupScopeValid = computed(() => {
    if (!isBackupSource.value) return true
    const filled = backupScopeEntries.value.filter((row) => row.path.trim())
    if (filled.length === 0) return false
    return filled.every((row) => Boolean(row.directoryId))
  })

  function normalizeGatewayDirPath(path: string): string {
    return path.trim().replace(/\/+$/, '') || path.trim()
  }

  function isPathUnderGatewayWorkspace(path: string): boolean {
    const root = normalizeGatewayDirPath(gatewayBrowseRoot.value)
    if (!root) return true
    const normalized = normalizeGatewayDirPath(path)
    if (!normalized) return false
    return normalized === root || normalized.startsWith(`${root}/`)
  }

  function gatewayDirBasename(path: string): string {
    const normalized = normalizeGatewayDirPath(path)
    if (!normalized || normalized === '/') return '/'
    const idx = normalized.lastIndexOf('/')
    return idx >= 0 ? normalized.slice(idx + 1) : normalized
  }

  const gatewayDirectoryValid = computed(() => {
    if (!isGatewayLocal.value) return true
    const path = gatewaySelectedPath.value.trim()
    if (!path) return false
    return isPathUnderGatewayWorkspace(path)
  })

  const canSubmit = computed(() => {
    if (isEditing.value) return Boolean(name.value.trim())
    if (!name.value.trim() || !gatewayId.value) return false
    if (isGatewayLocal.value) return gatewayDirectoryValid.value
    return Boolean(
      selectedBackupConfigId.value
      && effectiveSnapshotId.value
      && backupScopeValid.value,
    )
  })

  const canSubmitCreate = computed(() => canSubmit.value)

  function resetBackupScopeState() {
    cancelSnapshotBrowseRequests()
    latestBackupScopeValidation.clear()
    pendingBackupScopeBlurValidation.clear()
    backupScopeEntries.value = [createBackupScopeEntry()]
    openBackupScopePickerId.value = null
    backupScopeTreeRevision.value += 1
  }

  function resetBackupSelectionState() {
    selectedBackupConfigId.value = null
    snapshotPickerValue.value = SNAPSHOT_PICKER_LATEST
    snapshotDetail.value = null
    resetBackupScopeState()
  }

  function resetBackupBrowseState() {
    snapshots.value = []
    resetBackupSelectionState()
  }

  function resetGatewayBrowseState() {
    gatewayBrowsePath.value = ''
    gatewayBrowseRoot.value = ''
    gatewaySelectedPath.value = ''
    gatewayDirPickerOpen.value = false
    gatewayDirTreeRevision.value += 1
  }

  function resetCreateForm() {
    name.value = ''
    gatewayId.value = null
    resetBackupBrowseState()
    resetGatewayBrowseState()
    scanEnabled.value = true
    ingestPolicy.value = defaultIngestPolicy()
    readOnlyGatewayName.value = ''
    readOnlySourcePath.value = ''
  }

  async function loadGateways() {
    gateways.value = await listLensGateways()
  }

  async function refreshGateways() {
    gatewaysRefreshing.value = true
    try {
      await loadGateways()
    } finally {
      gatewaysRefreshing.value = false
    }
  }

  async function loadSnapshots() {
    snapshotLoading.value = true
    try {
      const page = await listBackupSourceSnapshots({
        page: 1,
        page_size: 50,
        status: 'available',
        ordering: '-created_at',
      })
      snapshots.value = page.results
    } finally {
      snapshotLoading.value = false
    }
  }

  async function loadSnapshotDetail(id: number) {
    snapshotDetail.value = await getBackupSourceSnapshot(id)
    resetBackupScopeState()
  }

  function isWindowsPath(path: string) {
    return /^[a-zA-Z]:($|[\\/])/.test(path)
  }

  function joinPathBySep(parent: string, name: string, sep: '/' | '\\') {
    if (!name) return parent
    const trimmedParent = parent.replace(/[\\/]+$/, '')
    if (!trimmedParent) return `${sep === '\\' ? '' : '/'}${name}`
    return `${trimmedParent}${sep}${name}`
  }

  function basenamePath(path: string) {
    const parts = path.split(/[\\/]/).filter(Boolean)
    return parts[parts.length - 1] || path
  }

  function normalizePathForCompare(path: string) {
    return path.replace(/\\/g, '/').replace(/\/+$/, '') || '/'
  }

  function isSameOrAncestorPath(ancestor: string, path: string) {
    const a = normalizePathForCompare(ancestor)
    const p = normalizePathForCompare(path)
    const prefix = a === '/' ? '/' : `${a}/`
    return p === a || p.startsWith(prefix)
  }

  function findSnapshotDirectoryForPath(path: string) {
    return [...snapshotDirectories.value]
      .filter((directory) => directory.source_path && isSameOrAncestorPath(directory.source_path, path))
      .sort((a, b) => b.source_path.length - a.source_path.length)[0] ?? null
  }

  function relativeSnapshotPath(rootPath: string, absolutePath: string) {
    const root = normalizePathForCompare(rootPath)
    const full = normalizePathForCompare(absolutePath)
    if (full === root) return ''
    const prefix = root === '/' ? '/' : `${root}/`
    if (!full.startsWith(prefix)) return absolutePath
    return full.slice(prefix.length)
  }

  function backupScopePickerNodeId(directoryId: number | undefined, path: string, type: string) {
    return `${directoryId || 0}:${type}:${path || '__root__'}`
  }

  function backupScopeRootNodes(): BackupScopePickerNode[] {
    return snapshotDirectories.value
      .filter((directory) => directory.source_path)
      .map((directory) => {
        const sourceType = directory.path_type === 'file' ? 'file' : 'dir'
        return {
          id: backupScopePickerNodeId(directory.id, directory.source_path, sourceType),
          label: directory.display_name || directory.source_path,
          path: directory.source_path,
          type: sourceType,
          directoryId: directory.id,
          browsePath: '',
          sourceRootPath: directory.source_path,
          sizeBytes: Math.max(0, Number(directory.size_bytes || 0)),
          fileCount: sourceType === 'file' ? 1 : Math.max(0, Number(directory.file_count || 0)),
          isLeaf: sourceType === 'file',
        }
      })
  }

  async function loadBackupScopePickerNode(node: unknown, resolve: (nodes: BackupScopePickerNode[]) => void) {
    const treeNode = node as { level?: number; data?: BackupScopePickerNode }
    if (!treeNode.level) {
      resolve(backupScopeRootNodes())
      return
    }
    const data = treeNode.data
    if (!data?.directoryId || data.type !== 'dir') {
      resolve([])
      return
    }
    backupScopeBrowseLoading.value = true
    try {
      const result = await browseInsightSnapshotDirectory(data.directoryId, {
        path: data.browsePath || '',
        limit: 500,
      })
      const sep: '/' | '\\' = isWindowsPath(data.sourceRootPath || data.path) ? '\\' : '/'
      resolve(result.entries.map((entry) => {
        const isDir = entry.type === 'dir'
        const fileSize = entry.size_known === true && entry.size_bytes != null
          ? Number(entry.size_bytes)
          : null
        const relativePath = (entry.path ?? '').replace(/^\/+/, '')
        const fullPath = relativePath
          ? joinPathBySep(data.sourceRootPath || data.path, relativePath, sep)
          : data.sourceRootPath || data.path
        return {
          id: backupScopePickerNodeId(data.directoryId, fullPath, isDir ? 'dir' : 'file'),
          label: entry.name || basenamePath(fullPath),
          path: fullPath,
          type: isDir ? 'dir' : 'file',
          directoryId: data.directoryId,
          browsePath: relativePath,
          sourceRootPath: data.sourceRootPath || data.path,
          sizeBytes: !isDir && fileSize != null && Number.isFinite(fileSize) && fileSize >= 0
            ? fileSize
            : undefined,
          fileCount: isDir ? undefined : 1,
          isLeaf: !isDir,
        }
      }))
    } catch (err) {
      if (!isAbortError(err)) {
        ElMessage.error({ message: apiErrorMessage(err, t('insight.kb.backupScopeBrowseFailed')), grouping: true })
      }
      resolve([])
    } finally {
      backupScopeBrowseLoading.value = false
    }
  }

  function setBackupScopePickerOpen(entryId: string | null, open: boolean) {
    openBackupScopePickerId.value = open ? entryId : null
    if (!open && entryId) {
      const entry = backupScopeEntries.value.find((row) => row.id === entryId)
      if (entry?.path.trim() && entry.directoryId == null) {
        void scheduleBackupScopeEntryValidation(entryId)
      }
    }
  }

  function addBackupScopeEntry() {
    backupScopeEntries.value = [...backupScopeEntries.value, createBackupScopeEntry()]
  }

  function removeBackupScopeEntry(entryId: string) {
    if (backupScopeEntries.value.length <= 1) return
    latestBackupScopeValidation.delete(entryId)
    pendingBackupScopeBlurValidation.delete(entryId)
    backupScopeEntries.value = backupScopeEntries.value.filter((row) => row.id !== entryId)
    if (openBackupScopePickerId.value === entryId) {
      openBackupScopePickerId.value = null
    }
  }

  function updateBackupScopeEntryInput(entryId: string, value: string) {
    backupScopeEntries.value = backupScopeEntries.value.map((row) =>
      row.id === entryId
        ? {
            ...row,
            path: value,
            directoryId: null,
            pathType: 'unknown',
            knownSizeBytes: null,
            knownFileCount: null,
            revision: row.revision + 1,
          }
        : row,
    )
  }

  function pickBackupScopeForEntry(entryId: string, node: BackupScopePickerNode) {
    if (!node.directoryId || !node.path) return
    backupScopeEntries.value = backupScopeEntries.value.map((row) =>
      row.id === entryId
        ? {
            ...row,
            path: node.path,
            directoryId: node.directoryId ?? null,
            pathType: node.type,
            knownSizeBytes: node.sizeBytes ?? null,
            knownFileCount: node.fileCount ?? null,
            revision: row.revision + 1,
          }
        : row,
    )
    openBackupScopePickerId.value = null
  }

  function isCurrentBackupScopeValidation(
    entryId: string,
    revision: number,
    snapshotId: number,
    requestId: number,
  ): boolean {
    const current = backupScopeEntries.value.find((row) => row.id === entryId)
    return Boolean(
      !scopeDisposed
      && current
      && current.revision === revision
      && effectiveSnapshotId.value === snapshotId
      && latestBackupScopeValidation.get(entryId) === requestId
    )
  }

  async function validateBackupScopeEntry(entryId: string, showMessage = true): Promise<boolean> {
    if (scopeDisposed) return false
    const entry = backupScopeEntries.value.find((row) => row.id === entryId)
    if (!entry) return false
    const rawPath = entry.path.trim()
    if (!rawPath) {
      if (showMessage) ElMessage.warning({ message: t('protection.backupsPage.msgManualPathRequired'), grouping: true })
      return false
    }
    if (entry.directoryId != null) return true
    if (!effectiveSnapshotId.value) {
      if (showMessage) ElMessage.warning({ message: t('insight.kb.backupScopePickSnapshotFirst'), grouping: true })
      return false
    }
    const validationRevision = entry.revision
    const validationSnapshotId = effectiveSnapshotId.value
    backupScopeValidationSequence += 1
    const validationRequestId = backupScopeValidationSequence
    latestBackupScopeValidation.set(entryId, validationRequestId)
    const directory = findSnapshotDirectoryForPath(rawPath)
    if (!directory?.id) {
      if (showMessage) ElMessage.warning({ message: t('protection.backupsPage.msgRestoreScopeOutsideSnapshot'), grouping: true })
      return false
    }
    const relativePath = relativeSnapshotPath(directory.source_path, rawPath)
    try {
      if (relativePath) {
        await browseInsightSnapshotDirectory(directory.id, { path: relativePath, limit: 1 })
      }
      if (!isCurrentBackupScopeValidation(
        entryId,
        validationRevision,
        validationSnapshotId,
        validationRequestId,
      )) {
        return false
      }
      backupScopeEntries.value = backupScopeEntries.value.map((row) =>
        row.id === entryId
          ? (() => {
              const isRoot = normalizePathForCompare(rawPath) === normalizePathForCompare(directory.source_path)
              const pathType = isRoot
                ? (directory.path_type === 'file' ? 'file' : 'dir')
                : row.pathType
              return {
                ...row,
                path: rawPath,
                directoryId: directory.id,
                pathType,
                knownSizeBytes: isRoot ? Math.max(0, Number(directory.size_bytes || 0)) : null,
                knownFileCount: isRoot
                  ? (pathType === 'file' ? 1 : Math.max(0, Number(directory.file_count || 0)))
                  : null,
              }
            })()
          : row,
      )
      openBackupScopePickerId.value = null
      return true
    } catch (err) {
      if (
        showMessage
        && isCurrentBackupScopeValidation(
          entryId,
          validationRevision,
          validationSnapshotId,
          validationRequestId,
        )
      ) {
        ElMessage.error({ message: apiErrorMessage(err, t('protection.backupsPage.msgRestoreScopeVerifyFailed')), grouping: true })
      }
      return false
    }
  }

  async function scheduleBackupScopeEntryValidation(entryId: string): Promise<boolean> {
    // Popover close and input blur can arrive in either order. Coalesce both
    // events after the current interaction and keep only the latest schedule.
    if (scopeDisposed) return false
    backupScopeBlurSequence += 1
    const blurRequestId = backupScopeBlurSequence
    pendingBackupScopeBlurValidation.set(entryId, blurRequestId)
    try {
      await new Promise<void>((resolve) => window.setTimeout(resolve, 0))
      if (
        scopeDisposed
        || pendingBackupScopeBlurValidation.get(entryId) !== blurRequestId
      ) {
        return false
      }
      const entry = backupScopeEntries.value.find((row) => row.id === entryId)
      if (
        !entry?.path.trim()
        || entry.directoryId != null
        || openBackupScopePickerId.value === entryId
      ) {
        return Boolean(entry?.directoryId)
      }
      return validateBackupScopeEntry(entryId)
    } finally {
      if (pendingBackupScopeBlurValidation.get(entryId) === blurRequestId) {
        pendingBackupScopeBlurValidation.delete(entryId)
      }
    }
  }

  function validateBackupScopeEntryOnBlur(entryId: string): Promise<boolean> {
    return scheduleBackupScopeEntryValidation(entryId)
  }

  async function validateAllBackupScopeEntries(showMessage = true): Promise<boolean> {
    const rows = backupScopeEntries.value
    if (!rows.some((row) => row.path.trim())) {
      if (showMessage) ElMessage.warning({ message: t('insight.kb.restoreScopeRequired'), grouping: true })
      return false
    }
    for (const row of rows) {
      if (!row.path.trim()) {
        if (showMessage) ElMessage.warning({ message: t('insight.kb.restoreScopeRowRequired'), grouping: true })
        return false
      }
      if (!row.directoryId) {
        const ok = await validateBackupScopeEntry(row.id, showMessage)
        if (!ok) return false
      }
    }
    return true
  }

  async function loadGatewayBrowse(path = '') {
    if (!gatewayId.value) return
    gatewayBrowseLoading.value = true
    try {
      const result = await browseGatewayDirectory(gatewayId.value, { path, limit: 200 })
      gatewayBrowsePath.value = result.path
      gatewayBrowseRoot.value = result.root_path
      if (!gatewaySelectedPath.value) {
        gatewaySelectedPath.value = result.path
      }
      return result
    } catch (err) {
      ElMessage.error({ message: apiErrorMessage(err, t('insight.kb.gatewayBrowseFailed')), grouping: true })
      return null
    } finally {
      gatewayBrowseLoading.value = false
    }
  }

  async function loadGatewayDirTreeNode(node: unknown, resolve: (nodes: GatewayDirTreeNode[]) => void) {
    if (!gatewayId.value) {
      resolve([])
      return
    }
    const treeNode = node as { level?: number; data?: GatewayDirTreeNode }
    const parentPath = treeNode.level === 0 ? '' : (treeNode.data?.path || '')
    gatewayBrowseLoading.value = true
    try {
      const result = await browseGatewayDirectory(gatewayId.value, { path: parentPath || undefined, limit: 200 })
      gatewayBrowseRoot.value = result.root_path
      const childDirs = result.entries
        .filter((entry) => entry.type === 'dir' && isPathUnderGatewayWorkspace(entry.path))
        .map((entry) => ({
          id: entry.path,
          label: entry.name,
          path: entry.path,
          isLeaf: false,
        }))

      if (treeNode.level === 0) {
        const rootPath = result.path || result.root_path
        resolve([
          {
            id: rootPath,
            label: gatewayDirBasename(rootPath),
            path: rootPath,
            isLeaf: childDirs.length === 0,
          },
        ])
        return
      }

      resolve(childDirs)
    } catch (err) {
      ElMessage.error({ message: apiErrorMessage(err, t('insight.kb.gatewayBrowseFailed')), grouping: true })
      resolve([])
    } finally {
      gatewayBrowseLoading.value = false
    }
  }

  function setGatewayDirPickerOpen(open: boolean) {
    gatewayDirPickerOpen.value = open
  }

  function updateGatewayDirectoryInput(value: string) {
    gatewaySelectedPath.value = value
    gatewayBrowsePath.value = value
  }

  function validateGatewayDirectoryPath(showMessage = true): boolean {
    const path = gatewaySelectedPath.value.trim()
    if (!path) {
      if (showMessage) ElMessage.warning({ message: t('insight.kb.gatewayDirectoryRequired'), grouping: true })
      return false
    }
    if (!isPathUnderGatewayWorkspace(path)) {
      if (showMessage) {
        ElMessage.warning(
          t('insight.kb.gatewayDirectoryOutOfWorkspace', {
            root: gatewayBrowseRoot.value || '/workspace',
          }),
        )
      }
      return false
    }
    return true
  }

  function pickGatewayDirectory(path: string) {
    if (!isPathUnderGatewayWorkspace(path)) {
      ElMessage.warning(
        t('insight.kb.gatewayDirectoryOutOfWorkspace', {
          root: gatewayBrowseRoot.value || '/workspace',
        }),
      )
      return
    }
    gatewaySelectedPath.value = path
    gatewayBrowsePath.value = path
    gatewayDirPickerOpen.value = false
  }

  async function hydrateEditForm(row: Awaited<ReturnType<typeof fetchKnowledgeSource>>) {
    name.value = row.name
    scanEnabled.value = row.scan_enabled
    ingestPolicy.value = normalizeLensIngestPolicy(row.ingest_policy)
    gatewayId.value = row.gateway
    readOnlyGatewayName.value = row.gateway_name
    readOnlySourcePath.value = row.source_path

    if (row.backup_source_snapshot_id) {
      sourceType.value = 'backup_source'
      await Promise.all([loadGateways(), loadSnapshots()])
      try {
        const snap = await getBackupSourceSnapshot(row.backup_source_snapshot_id)
        snapshotDetail.value = snap
        if (!snapshots.value.some((item) => item.id === snap.id)) {
          snapshots.value = [snap, ...snapshots.value]
        }
        selectedBackupConfigId.value = snap.backup_config_id
        snapshotPickerValue.value = snap.id
      } catch {
        snapshotPickerValue.value = row.backup_source_snapshot_id
      }

      const scopes = row.source_scopes_json?.length
        ? row.source_scopes_json
        : row.source_path
          ? [{
              source_path: row.source_path,
              backup_snapshot_directory_id: row.backup_snapshot_directory_id ?? 0,
            }]
          : []

      backupScopeEntries.value = scopes
        .filter((scope) => scope.source_path?.trim())
        .map((scope) => {
          const entry = createBackupScopeEntry()
          entry.path = scope.source_path.trim()
          entry.directoryId = scope.backup_snapshot_directory_id || null
          return entry
        })
      if (backupScopeEntries.value.length === 0) {
        backupScopeEntries.value = [createBackupScopeEntry()]
      }
      return
    }

    sourceType.value = 'gateway_local'
    await loadGateways()
    gatewaySelectedPath.value = row.source_path
    if (row.gateway) {
      await loadGatewayBrowse('')
    }
  }

  async function init() {
    loading.value = true
    try {
      resetCreateForm()
      if (isEditing.value && editingId.value != null) {
        const row = await fetchKnowledgeSource(editingId.value)
        await hydrateEditForm(row)
      } else {
        await Promise.all([loadGateways(), loadSnapshots()])
      }
    } catch (err) {
      ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.loadFailed')), grouping: true })
    } finally {
      loading.value = false
    }
  }

  async function submit(): Promise<boolean> {
    saving.value = true
    try {
      if (isEditing.value && editingId.value != null) {
        await patchKnowledgeSource(editingId.value, {
          name: name.value.trim(),
          scan_enabled: scanEnabled.value,
          ingest_policy: normalizeLensIngestPolicy(ingestPolicy.value),
        })
        ElMessage.success({ message: t('insight.kb.saveSuccess'), grouping: true })
      } else {
        if (!gatewayId.value) return false
        if (isGatewayLocal.value) {
          if (!validateGatewayDirectoryPath(false)) return false
          await createKnowledgeSource({
            name: name.value.trim(),
            gateway: gatewayId.value,
            source_path: gatewaySelectedPath.value.trim(),
            scan_enabled: scanEnabled.value,
            ingest_policy: normalizeLensIngestPolicy(ingestPolicy.value),
          })
        } else {
          if (!selectedBackupConfigId.value || !effectiveSnapshotId.value) {
            return false
          }
          if (!(await validateAllBackupScopeEntries(false))) return false
          const sourceScopes: KnowledgeSourceScopePayload[] = backupScopeEntries.value
            .filter((row) => row.path.trim() && row.directoryId)
            .map((row) => ({
              source_path: row.path.trim(),
              backup_snapshot_directory_id: row.directoryId as number,
            }))
          if (sourceScopes.length === 0) return false
          await createKnowledgeSource({
            name: name.value.trim(),
            gateway: gatewayId.value,
            backup_source_snapshot_id: effectiveSnapshotId.value,
            backup_snapshot_directory_id: sourceScopes[0].backup_snapshot_directory_id,
            source_path: sourceScopes[0].source_path,
            source_scopes: sourceScopes,
            linked_version_mode: snapshotPickerValue.value === SNAPSHOT_PICKER_LATEST
              ? 'latest'
              : 'pinned',
            pinned_snapshot_id: snapshotPickerValue.value === SNAPSHOT_PICKER_LATEST
              ? null
              : effectiveSnapshotId.value,
            scan_enabled: scanEnabled.value,
            ingest_policy: normalizeLensIngestPolicy(ingestPolicy.value),
          })
        }
        ElMessage.success({ message: t('insight.kb.createSuccess'), grouping: true })
      }
      return true
    } catch (err) {
      ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
      return false
    } finally {
      saving.value = false
    }
  }

  watch(effectiveSnapshotId, (id) => {
    if (id && !isEditing.value && isBackupSource.value) void loadSnapshotDetail(id)
  })

  watch(selectedBackupConfigId, (id, prev) => {
    if (isEditing.value || id === prev) return
    snapshotPickerValue.value = SNAPSHOT_PICKER_LATEST
    snapshotDetail.value = null
    resetBackupScopeState()
  })

  watch(sourceType, (next, prev) => {
    if (isEditing.value || next === prev) return
    gatewayId.value = null
    if (next === 'backup_source') {
      resetGatewayBrowseState()
      resetBackupSelectionState()
      if (snapshots.value.length === 0) void loadSnapshots()
    } else {
      resetBackupSelectionState()
      resetGatewayBrowseState()
    }
  })

  watch(gatewayId, (id, prev) => {
    if (isEditing.value || id === prev) return
    if (!isGatewayLocal.value) return
    resetGatewayBrowseState()
    if (id) void loadGatewayBrowse('')
  })

  onScopeDispose(() => {
    scopeDisposed = true
    cancelSnapshotBrowseRequests()
    latestBackupScopeValidation.clear()
    pendingBackupScopeBlurValidation.clear()
  })

  return {
    loading,
    saving,
    gatewaysRefreshing,
    isEditing,
    isBackupSource,
    isGatewayLocal,
    name,
    gatewayId,
    gateways,
    aiReadyGateways,
    onlineGateways,
    offlineGatewayCount,
    gatewaysForPicker,
    selectedGateway,
    snapshots,
    snapshotLoading,
    selectedBackupConfigId,
    snapshotPickerValue,
    backupSourceOptions,
    snapshotsForSelectedBackupSource,
    effectiveSnapshotId,
    SNAPSHOT_PICKER_LATEST,
    snapshotDetail,
    snapshotDirectories,
    backupScopeEntries,
    openBackupScopePickerId,
    backupScopeTreeRevision,
    backupScopeBrowseLoading,
    gatewayBrowsePath,
    gatewayBrowseRoot,
    gatewayBrowseLoading,
    gatewaySelectedPath,
    gatewayDirectoryValid,
    gatewayDirPickerOpen,
    gatewayDirTreeRevision,
    effectiveSourcePath,
    scanEnabled,
    ingestPolicy,
    readOnlyGatewayName,
    readOnlySourcePath,
    canSubmit,
    canSubmitCreate,
    init,
    submit,
    loadSnapshots,
    refreshGateways,
    loadBackupScopePickerNode,
    setBackupScopePickerOpen,
    addBackupScopeEntry,
    removeBackupScopeEntry,
    updateBackupScopeEntryInput,
    validateBackupScopeEntry,
    validateBackupScopeEntryOnBlur,
    validateAllBackupScopeEntries,
    pickBackupScopeForEntry,
    loadGatewayDirTreeNode,
    setGatewayDirPickerOpen,
    updateGatewayDirectoryInput,
    validateGatewayDirectoryPath,
    pickGatewayDirectory,
  }
}
