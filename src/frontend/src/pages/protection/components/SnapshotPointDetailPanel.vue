<script setup lang="ts">
import { computed, nextTick, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronRight, Download, File, Folder, LoaderCircle, RefreshCw } from 'lucide-vue-next'
import type { ElTree } from 'element-plus'
import HflHelpTip from '../../../components/HflHelpTip.vue'
import { apiErrorMessage } from '../../../lib/api'
import { formatAppDateTime } from '../../../lib/dateTime'
import { toApiError } from '../../../lib/errors'
import {
  browseBackupSnapshotDirectory,
  createBackupSnapshotDownloadTask,
  createSnapshotArtifactDownloadUrl,
  type BackupSnapshotBrowserEntry,
  type BackupSourceSnapshot,
  type BackupSourceSnapshotDirectory,
} from '../../../lib/protectionBackupConfigApi'
import { lifecycleStatusTagAttrs } from '../../../lib/statusTag'
import { pushToast } from '../../../lib/toast/store'
import { getTask } from '../../../lib/taskApi'
import SnapshotDownloadLimitsPopover from './SnapshotDownloadLimitsPopover.vue'
import { isSnapshotDirectoryBrowsable } from './snapshotBrowseEligibility'
import { snapshotDownloadSizeLimit } from './snapshotDownloadFeedback'

type SnapshotBrowserTreeNode = BackupSnapshotBrowserEntry & {
  id: string
  label: string
  disabled?: boolean
  loaded?: boolean
  isLeaf?: boolean
  children?: SnapshotBrowserTreeNode[]
  loadMore?: boolean
  nextCursor?: string
  parentPath?: string
  loadedCount?: number
  loadingMore?: boolean
}

type BrowserTreeInstance = InstanceType<typeof ElTree>

type DirectoryBrowserState = {
  expanded: boolean
  loaded: boolean
  loading: boolean
  error: string
  path: string
  entries: BackupSnapshotBrowserEntry[]
  treeEntries: SnapshotBrowserTreeNode[]
  treeVersion: number
  rootChecked: boolean
  selectedPaths: Set<string>
  fileChecked: boolean
  requestRevision: number
}

type DownloadPhase = 'idle' | 'calculating' | 'preparing'

const DIRECTORY_PAGE_LIMIT = 200

const props = withDefaults(defineProps<{
  snapshot: BackupSourceSnapshot | null
  loading?: boolean
  error?: string
  sourceKind?: 'agent' | 'nas'
}>(), {
  loading: false,
  error: '',
  sourceKind: 'agent',
})

const emit = defineEmits<{
  retry: []
}>()

const { t } = useI18n()
const browserStates = reactive(new Map<number, DirectoryBrowserState>())
const browserTreeRefs = new Map<number, BrowserTreeInstance>()
const browserRequestControllers = new Map<string, AbortController>()
const downloadController = ref<AbortController | null>(null)
const downloadPhase = ref<DownloadPhase>('idle')

const snapshotDirectories = computed(() => props.snapshot?.directories || [])
const selectedCount = computed(() => snapshotDirectories.value.reduce(
  (total, directory) => total + directorySelectedCount(directory),
  0,
))
const downloadLimits = computed(() => {
  const limits = props.snapshot?.download_limits
  const maxItems = Math.floor(Number(limits?.max_selected_items || 0))
  const maxSizeBytes = Math.floor(Number(limits?.max_logical_size_bytes || 0))
  if (maxItems <= 0 || maxSizeBytes <= 0) return null
  return { maxItems, maxSizeBytes }
})
const maxSelectedItems = computed(() => downloadLimits.value?.maxItems ?? 100)
const selectedDownloadGroups = computed(() => snapshotDirectories.value.flatMap((directory) => {
  const state = browserStates.get(directory.id)
  if (!state) return []
  const paths = directory.path_type === 'file'
    ? (state.fileChecked ? [''] : [])
    : state.rootChecked
      ? ['']
      : normalizeBrowserDownloadPaths(Array.from(state.selectedPaths))
  return paths.length ? [{ directory_id: directory.id, paths }] : []
}))
const downloadingSelected = computed(() => downloadPhase.value !== 'idle')

function fmtBytes(value: number) {
  if (!value || value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let normalized = value
  let unitIndex = 0
  while (normalized >= 1024 && unitIndex < units.length - 1) {
    normalized /= 1024
    unitIndex += 1
  }
  return `${normalized.toFixed(unitIndex >= 2 ? 1 : 0)} ${units[unitIndex]}`
}

function fmtReferenceBytes(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—'
  return fmtBytes(Math.max(0, Number(value)))
}

function fmtReferencePercent(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—'
  return `${(Math.max(0, Number(value)) * 100).toFixed(1)}%`
}

function fmtCombinedReduction(snapshot: BackupSourceSnapshot | null) {
  if (!snapshot?.storage_stats_available) return '—'
  if (snapshot.fully_reused) return t('protection.backupsPage.snapshotStorageFullyReused')
  const value = Number(snapshot.combined_reduction_ratio)
  if (!Number.isFinite(value) || value <= 0) return '—'
  return `${value.toFixed(2)} : 1`
}

function formatNullableTime(value?: string | null) {
  return formatAppDateTime(value, t('protection.backupDetail.durationDash'))
}

function snapshotStatusLabel(status?: string) {
  const normalized = String(status || '').toLowerCase()
  if (normalized === 'available') return t('protection.backupsPage.snapshotStatusAvailable')
  if (normalized === 'partial') return t('protection.backupsPage.snapshotStatusPartial')
  if (normalized === 'failed') return t('protection.backupsPage.snapshotStatusFailed')
  if (normalized === 'creating') return t('protection.backupsPage.snapshotStatusCreating')
  if (normalized === 'running') return t('protection.backupsPage.snapshotStatusRunning')
  if (normalized === 'deleted') return t('protection.backupsPage.snapshotStatusDeleted')
  if (normalized === 'deleting') return t('protection.backupsPage.snapshotStatusDeleting')
  if (normalized === 'delete_failed') return t('protection.backupsPage.snapshotStatusDeleteFailed')
  return status || t('protection.backupDetail.durationDash')
}

function snapshotStatusInProgress(status?: string) {
  const normalized = String(status || '').toLowerCase()
  return normalized === 'creating' || normalized === 'running'
}

function snapshotDisplaySize(snapshot: BackupSourceSnapshot | null) {
  if (!snapshot) return 0
  const value = Number(snapshot.recoverable_size_bytes || snapshot.total_size_bytes || 0)
  if (value > 0) return value
  return (snapshot.directories || [])
    .filter((directory) => directory.status === 'available')
    .reduce((total, directory) => total + Number(directory.size_bytes || 0), 0)
}

function createDirectoryBrowserState(): DirectoryBrowserState {
  return {
    expanded: false,
    loaded: false,
    loading: false,
    error: '',
    path: '',
    entries: [],
    treeEntries: [],
    treeVersion: 0,
    rootChecked: false,
    selectedPaths: new Set(),
    fileChecked: false,
    requestRevision: 0,
  }
}

function directoryBrowserState(directory: BackupSourceSnapshotDirectory) {
  let state = browserStates.get(directory.id)
  if (!state) {
    state = reactive(createDirectoryBrowserState()) as DirectoryBrowserState
    browserStates.set(directory.id, state)
  }
  return state
}

function abortBrowserRequests() {
  for (const controller of browserRequestControllers.values()) controller.abort()
  browserRequestControllers.clear()
}

function resetBrowserState() {
  abortBrowserRequests()
  downloadController.value?.abort()
  downloadController.value = null
  downloadPhase.value = 'idle'
  for (const state of browserStates.values()) state.requestRevision += 1
  browserStates.clear()
  browserTreeRefs.clear()
}

function nextBrowserRequestSignal(key: string) {
  browserRequestControllers.get(key)?.abort()
  const controller = new AbortController()
  browserRequestControllers.set(key, controller)
  return controller.signal
}

function releaseBrowserRequestSignal(key: string, signal: AbortSignal) {
  if (browserRequestControllers.get(key)?.signal === signal) {
    browserRequestControllers.delete(key)
  }
}

function setBrowserTreeRef(directoryId: number, instance: unknown) {
  if (instance) browserTreeRefs.set(directoryId, instance as BrowserTreeInstance)
  else browserTreeRefs.delete(directoryId)
}

function browserTree(directoryId: number) {
  return browserTreeRefs.get(directoryId) ?? null
}

function directorySelectedCount(directory: BackupSourceSnapshotDirectory) {
  const state = browserStates.get(directory.id)
  if (!state) return 0
  return directory.path_type === 'file'
    ? (state.fileChecked ? 1 : 0)
    : state.rootChecked
      ? 1
      : state.selectedPaths.size
}

function sourcePathChecked(directory: BackupSourceSnapshotDirectory) {
  const state = directoryBrowserState(directory)
  return directory.path_type === 'file' ? state.fileChecked : state.rootChecked
}

function directoryItemCount(directory: BackupSourceSnapshotDirectory) {
  const state = browserStates.get(directory.id)
  if (!state) return 0
  return directory.path_type === 'file' ? 1 : state.entries.length
}

function snapshotFileFallbackName(directory: BackupSourceSnapshotDirectory) {
  return directory.display_name || directory.source_path.split(/[\\/]/).filter(Boolean).pop() || 'snapshot-file'
}

function canBrowseDirectory(directory: BackupSourceSnapshotDirectory) {
  return isSnapshotDirectoryBrowsable(props.snapshot?.status, directory)
}

function directoryBrowseUnavailableReason(directory: BackupSourceSnapshotDirectory) {
  const snapshotStatus = String(props.snapshot?.status || '').toLowerCase()
  if (snapshotStatus !== 'available' && snapshotStatus !== 'partial') {
    return t('protection.backupsPage.snapshotBrowserSnapshotStatusUnavailable', {
      status: snapshotStatusLabel(props.snapshot?.status),
    })
  }
  if (String(directory.status || '').toLowerCase() !== 'available') {
    return t('protection.backupsPage.snapshotBrowserSourcePathStatusUnavailable', {
      status: snapshotStatusLabel(directory.status),
    })
  }
  if (!directory.kopia_snapshot_id) {
    return t('protection.backupsPage.snapshotBrowserSourcePathSnapshotUnavailable')
  }
  return ''
}

function browseSnapshotDirectory(directory: BackupSourceSnapshotDirectory) {
  if (!canBrowseDirectory(directory)) return
  const state = directoryBrowserState(directory)
  state.expanded = !state.expanded
  if (state.expanded && !state.loaded && !state.loading) void openDirectory(directory, state.path)
}

async function openDirectory(
  directory: BackupSourceSnapshotDirectory,
  path = '',
  options: { force?: boolean } = {},
) {
  if (!canBrowseDirectory(directory)) return
  const snapshotId = props.snapshot?.id
  const state = directoryBrowserState(directory)
  state.expanded = true
  if (state.loaded && !options.force) return
  const revision = ++state.requestRevision
  state.error = ''
  state.path = path
  if (options.force) {
    state.loaded = false
    state.entries = []
    state.treeEntries = []
    state.treeVersion += 1
  }
  if (directory.path_type === 'file') {
    state.loaded = true
    state.loading = false
    return
  }

  state.loading = true
  const requestKey = `root:${directory.id}`
  const signal = nextBrowserRequestSignal(requestKey)
  try {
    const result = await browseBackupSnapshotDirectory(directory.id, {
      path,
      limit: DIRECTORY_PAGE_LIMIT,
    }, { signal })
    if (
      revision !== state.requestRevision
      || props.snapshot?.id !== snapshotId
      || browserStates.get(directory.id) !== state
    ) return
    state.path = result.path || ''
    state.entries = result.entries
    state.treeEntries = browserPageTreeNodes(state, result, state.path, 0)
    state.treeVersion += 1
    state.loaded = true
    refreshBrowserTreeDisabled(state)
    await nextTick()
    syncBrowserTreeCheckedKeys(directory.id, state)
  } catch (error) {
    if (signal.aborted || revision !== state.requestRevision || props.snapshot?.id !== snapshotId) return
    state.error = apiErrorMessage(error, t('errors.generic.loadFailed'))
  } finally {
    releaseBrowserRequestSignal(requestKey, signal)
    if (revision === state.requestRevision) state.loading = false
  }
}

function isRelatedBrowserPath(first: string, second: string) {
  return first === second || first.startsWith(`${second}/`) || second.startsWith(`${first}/`)
}

function isBrowserPathDisabled(state: DirectoryBrowserState, path: string) {
  if (state.rootChecked) return true
  for (const selectedPath of state.selectedPaths) {
    if (selectedPath !== path && isRelatedBrowserPath(path, selectedPath)) return true
  }
  return false
}

function browserEntryToTreeNode(
  state: DirectoryBrowserState,
  entry: BackupSnapshotBrowserEntry,
): SnapshotBrowserTreeNode {
  return {
    ...entry,
    id: entry.path,
    label: entry.name,
    disabled: !entry.downloadable || isBrowserPathDisabled(state, entry.path),
    loaded: entry.type !== 'dir',
    isLeaf: entry.type !== 'dir',
    children: entry.type === 'dir' ? [] : undefined,
  }
}

function browserPageTreeNodes(
  state: DirectoryBrowserState,
  result: Awaited<ReturnType<typeof browseBackupSnapshotDirectory>>,
  parentPath: string,
  previouslyLoaded: number,
) {
  const entries = result.entries.map((entry) => browserEntryToTreeNode(state, entry))
  const loadedCount = previouslyLoaded + entries.length
  if (result.has_more && result.next_cursor) {
    entries.push({
      id: `snapshot-browser-load-more:${parentPath}:${result.next_cursor}`,
      label: t('protection.backupsPage.snapshotBrowserLoadMore'),
      name: t('protection.backupsPage.snapshotBrowserLoadMore'),
      path: parentPath,
      type: 'load-more',
      size_bytes: 0,
      downloadable: false,
      disabled: true,
      loaded: true,
      isLeaf: true,
      loadMore: true,
      nextCursor: result.next_cursor,
      parentPath,
      loadedCount,
    })
  }
  return entries
}

function refreshBrowserTreeDisabled(
  state: DirectoryBrowserState,
  nodes: SnapshotBrowserTreeNode[] = state.treeEntries,
) {
  for (const node of nodes) {
    node.disabled = node.loadMore || !node.downloadable || isBrowserPathDisabled(state, node.path)
    if (node.children?.length) refreshBrowserTreeDisabled(state, node.children)
  }
  state.treeEntries = [...state.treeEntries]
}

async function loadBrowserTreeNode(
  directory: BackupSourceSnapshotDirectory,
  node: { data?: SnapshotBrowserTreeNode; level: number },
  resolve: (data: SnapshotBrowserTreeNode[]) => void,
) {
  const state = directoryBrowserState(directory)
  if (node.level === 0) {
    resolve(state.treeEntries)
    return
  }
  const data = node.data
  if (!data || data.type !== 'dir') {
    resolve([])
    return
  }
  const treeVersion = state.treeVersion
  const requestKey = `node:${directory.id}:${data.path}`
  const signal = nextBrowserRequestSignal(requestKey)
  try {
    const result = await browseBackupSnapshotDirectory(directory.id, {
      path: data.path,
      limit: DIRECTORY_PAGE_LIMIT,
    }, { signal })
    if (browserStates.get(directory.id) !== state || state.treeVersion !== treeVersion) {
      resolve([])
      return
    }
    const children = browserPageTreeNodes(state, result, data.path, 0)
    data.children = children
    data.loaded = true
    resolve(children)
    refreshBrowserTreeDisabled(state)
  } catch (error) {
    data.loaded = false
    if (!signal.aborted) showFeedback(apiErrorMessage(error, t('errors.generic.loadFailed')), 'error')
    resolve([])
  } finally {
    releaseBrowserRequestSignal(requestKey, signal)
  }
}

function replaceBrowserLoadMoreNode(
  nodes: SnapshotBrowserTreeNode[],
  id: string,
  replacements: SnapshotBrowserTreeNode[],
): boolean {
  const index = nodes.findIndex((node) => node.id === id)
  if (index >= 0) {
    nodes.splice(index, 1, ...replacements)
    return true
  }
  return nodes.some((node) => node.children && replaceBrowserLoadMoreNode(node.children, id, replacements))
}

async function loadMoreBrowserTreeEntries(
  directory: BackupSourceSnapshotDirectory,
  data: SnapshotBrowserTreeNode,
) {
  if (!data.loadMore || !data.nextCursor || data.loadingMore) return
  const state = directoryBrowserState(directory)
  const treeVersion = state.treeVersion
  const parentPath = data.parentPath || ''
  const requestKey = `page:${directory.id}:${parentPath}:${data.nextCursor}`
  const signal = nextBrowserRequestSignal(requestKey)
  data.loadingMore = true
  state.treeEntries = [...state.treeEntries]
  try {
    const result = await browseBackupSnapshotDirectory(directory.id, {
      path: parentPath,
      limit: DIRECTORY_PAGE_LIMIT,
      cursor: data.nextCursor,
    }, { signal })
    if (browserStates.get(directory.id) !== state || state.treeVersion !== treeVersion) return
    const replacements = browserPageTreeNodes(state, result, parentPath, data.loadedCount || 0)
    if (!replaceBrowserLoadMoreNode(state.treeEntries, data.id, replacements)) return
    for (const replacement of replacements) browserTree(directory.id)?.insertBefore(replacement, data)
    browserTree(directory.id)?.remove(data)
    if (parentPath === state.path) state.entries = [...state.entries, ...result.entries]
    state.treeEntries = [...state.treeEntries]
    refreshBrowserTreeDisabled(state)
    await nextTick()
    syncBrowserTreeCheckedKeys(directory.id, state)
  } catch (error) {
    if (!signal.aborted) showFeedback(apiErrorMessage(error, t('errors.generic.loadFailed')), 'error')
  } finally {
    releaseBrowserRequestSignal(requestKey, signal)
    data.loadingMore = false
    state.treeEntries = [...state.treeEntries]
  }
}

function syncBrowserTreeCheckedKeys(directoryId: number, state: DirectoryBrowserState) {
  browserTree(directoryId)?.setCheckedKeys(Array.from(state.selectedPaths))
}

function onBrowserTreeCheckChange(
  directory: BackupSourceSnapshotDirectory,
  data: SnapshotBrowserTreeNode,
  checked: boolean,
) {
  if (!data.downloadable || data.loadMore) return
  const state = directoryBrowserState(directory)
  if (state.rootChecked) return
  const next = new Set(state.selectedPaths)
  if (!checked) {
    next.delete(data.path)
  } else {
    for (const selectedPath of Array.from(next)) {
      if (isRelatedBrowserPath(data.path, selectedPath)) next.delete(selectedPath)
    }
    next.add(data.path)
  }
  const projectedCount = selectedCount.value - state.selectedPaths.size + next.size
  if (projectedCount > maxSelectedItems.value) {
    showFeedback(
      t('protection.backupsPage.snapshotBrowserSelectionLimit', { n: maxSelectedItems.value }),
      'warning',
    )
    syncBrowserTreeCheckedKeys(directory.id, state)
    return
  }
  state.selectedPaths = next
  refreshBrowserTreeDisabled(state)
  syncBrowserTreeCheckedKeys(directory.id, state)
}

function onSourcePathCheckChange(directory: BackupSourceSnapshotDirectory, checked: unknown) {
  if (directory.path_type === 'file') {
    onSnapshotFileCheckChange(directory, checked)
    return
  }
  const state = directoryBrowserState(directory)
  const nextChecked = Boolean(checked)
  const projectedCount = selectedCount.value
    - (state.rootChecked ? 1 : state.selectedPaths.size)
    + (nextChecked ? 1 : 0)
  if (nextChecked && projectedCount > maxSelectedItems.value) {
    showFeedback(
      t('protection.backupsPage.snapshotBrowserSelectionLimit', { n: maxSelectedItems.value }),
      'warning',
    )
    return
  }
  state.rootChecked = nextChecked
  if (nextChecked) state.selectedPaths = new Set()
  refreshBrowserTreeDisabled(state)
  syncBrowserTreeCheckedKeys(directory.id, state)
}

function onSnapshotFileCheckChange(directory: BackupSourceSnapshotDirectory, checked: unknown) {
  const state = directoryBrowserState(directory)
  const nextChecked = Boolean(checked)
  if (nextChecked && !state.fileChecked && selectedCount.value >= maxSelectedItems.value) {
    showFeedback(
      t('protection.backupsPage.snapshotBrowserSelectionLimit', { n: maxSelectedItems.value }),
      'warning',
    )
    return
  }
  state.fileChecked = nextChecked
}

function clearDownloadSelection() {
  for (const directory of snapshotDirectories.value) {
    const state = browserStates.get(directory.id)
    if (!state) continue
    state.rootChecked = false
    state.fileChecked = false
    state.selectedPaths = new Set()
    refreshBrowserTreeDisabled(state)
    syncBrowserTreeCheckedKeys(directory.id, state)
  }
}

function normalizeBrowserDownloadPaths(paths: string[]) {
  const sorted = [...paths].sort((first, second) => first.length - second.length)
  const kept: string[] = []
  for (const path of sorted) {
    if (kept.some((parent) => path === parent || path.startsWith(`${parent}/`))) continue
    kept.push(path)
  }
  return kept.sort()
}

function showFeedback(message: string, type: 'error' | 'warning', title?: string) {
  pushToast({ message, type, title })
}

function showDownloadError(error: unknown) {
  const sizeLimit = snapshotDownloadSizeLimit(error)
  if (sizeLimit) {
    showFeedback(
      t('protection.backupsPage.snapshotBrowserDownloadLimitExceeded', {
        selected: fmtBytes(sizeLimit.selectedBytes),
        limit: fmtBytes(sizeLimit.limitBytes),
      }),
      'error',
      t('protection.backupsPage.snapshotBrowserDownloadLimitTitle'),
    )
    return
  }
  showFeedback(apiErrorMessage(error, t('errors.generic.requestFailed')), 'error')
}

function wait(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const timeoutId = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    function onAbort() {
      window.clearTimeout(timeoutId)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

function artifactIdFromTask(task: { result_payload?: unknown }) {
  const payload = task.result_payload && typeof task.result_payload === 'object'
    ? task.result_payload as Record<string, unknown>
    : {}
  const artifactId = Number(payload.artifact_id || 0)
  return Number.isFinite(artifactId) && artifactId > 0 ? artifactId : 0
}

async function waitForDownloadArtifact(taskUuid: string, signal: AbortSignal) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const task = await getTask(taskUuid, { signal })
    if (task.status === 'success') {
      const artifactId = artifactIdFromTask(task)
      if (artifactId > 0) return artifactId
      throw new Error(t('protection.backupsPage.snapshotBrowserDownloadNotReady'))
    }
    if (task.status === 'failed' || task.status === 'cancelled' || task.status === 'timeout') {
      throw new Error(task.error_message || t('protection.backupsPage.snapshotBrowserDownloadFailed'))
    }
    await wait(1000, signal)
  }
  throw new Error(t('protection.backupsPage.snapshotBrowserDownloadTimeout'))
}

async function startNativeArtifactDownload(artifactId: number) {
  const result = await createSnapshotArtifactDownloadUrl(artifactId)
  const anchor = document.createElement('a')
  anchor.href = result.url
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

async function downloadSelection() {
  if (!props.snapshot?.id || !selectedCount.value || downloadingSelected.value) {
    showFeedback(t('protection.backupsPage.snapshotBrowserSelectBeforeDownload'), 'warning')
    return
  }
  downloadController.value?.abort()
  const controller = new AbortController()
  downloadController.value = controller
  downloadPhase.value = 'calculating'
  try {
    const task = await createBackupSnapshotDownloadTask(props.snapshot.id, selectedDownloadGroups.value)
    downloadPhase.value = 'preparing'
    const artifactId = await waitForDownloadArtifact(task.task_uuid, controller.signal)
    if (!controller.signal.aborted) await startNativeArtifactDownload(artifactId)
  } catch (error) {
    if (!controller.signal.aborted) {
      const apiError = toApiError(error)
      if (apiError?.errorCode === 'PROTECTION.SNAPSHOT_MULTI_DOWNLOAD_UPGRADE_REQUIRED') {
        showFeedback(
          t(props.sourceKind === 'nas'
            ? 'protection.backupsPage.snapshotBrowserNasProxyUpgradeRequired'
            : 'protection.backupsPage.snapshotBrowserSourceUpgradeRequired'),
          'warning',
        )
      } else {
        showDownloadError(error)
      }
    }
  } finally {
    if (downloadController.value === controller) {
      downloadController.value = null
      downloadPhase.value = 'idle'
    }
  }
}

watch(() => props.snapshot?.id, resetBrowserState)
onUnmounted(resetBrowserState)
</script>

<template>
  <div class="snapshot-point-detail-panel">
    <section class="snapshot-point-detail-section snapshot-point-detail-section--overview">
      <div
        v-loading="loading && !snapshot"
        class="snapshot-point-detail-section__scroll"
      >
        <ElAlert
          v-if="error && !snapshot"
          :title="error"
          type="error"
          show-icon
          :closable="false"
        />
        <dl
          v-else-if="snapshot"
          class="snapshot-point-detail-metrics"
        >
          <div class="snapshot-point-detail-metrics__item">
            <dt>
              <span>{{ t('protection.backupsPage.snapshotRecoverableData') }}</span>
              <HflHelpTip
                :content="t('protection.backupsPage.snapshotRecoverableDataHint')"
                :aria-label="t('protection.backupsPage.snapshotRecoverableDataHint')"
                :size="13"
                popper-class="snapshot-metric-help-popper"
              />
            </dt>
            <dd>{{ fmtBytes(snapshotDisplaySize(snapshot)) }}</dd>
          </div>
          <div class="snapshot-point-detail-metrics__item">
            <dt>
              <span>{{ t('protection.backupsPage.snapshotNewOriginalData') }}</span>
              <HflHelpTip
                :content="t('protection.backupsPage.snapshotNewOriginalDataHint')"
                :aria-label="t('protection.backupsPage.snapshotNewOriginalDataHint')"
                :size="13"
                popper-class="snapshot-metric-help-popper"
              />
            </dt>
            <dd>{{ fmtReferenceBytes(snapshot.new_original_content_bytes) }}</dd>
          </div>
          <div class="snapshot-point-detail-metrics__item">
            <dt>
              <span>{{ t('protection.backupsPage.snapshotNewStorage') }}</span>
              <HflHelpTip
                :content="t('protection.backupsPage.snapshotNewStorageHint')"
                :aria-label="t('protection.backupsPage.snapshotNewStorageHint')"
                :size="13"
                popper-class="snapshot-metric-help-popper"
              />
            </dt>
            <dd>{{ fmtReferenceBytes(snapshot.new_packed_content_bytes) }}</dd>
          </div>
          <div class="snapshot-point-detail-metrics__item">
            <dt>
              <span>{{ t('protection.backupsPage.snapshotDataReuse') }}</span>
              <HflHelpTip
                :content="t('protection.backupsPage.snapshotDataReuseHint')"
                :aria-label="t('protection.backupsPage.snapshotDataReuseHint')"
                :size="13"
                popper-class="snapshot-metric-help-popper"
              />
            </dt>
            <dd>{{ fmtReferencePercent(snapshot.data_reuse_ratio) }}</dd>
          </div>
          <div class="snapshot-point-detail-metrics__item">
            <dt>
              <span>{{ t('protection.backupsPage.snapshotCompressionSavings') }}</span>
              <HflHelpTip
                :content="t('protection.backupsPage.snapshotCompressionSavingsHint')"
                :aria-label="t('protection.backupsPage.snapshotCompressionSavingsHint')"
                :size="13"
                popper-class="snapshot-metric-help-popper"
              />
            </dt>
            <dd>{{ fmtReferencePercent(snapshot.compression_savings_ratio) }}</dd>
          </div>
          <div class="snapshot-point-detail-metrics__item">
            <dt>
              <span>{{ t('protection.backupsPage.snapshotCombinedReduction') }}</span>
              <HflHelpTip
                :content="t('protection.backupsPage.snapshotCombinedReductionHint')"
                :aria-label="t('protection.backupsPage.snapshotCombinedReductionHint')"
                :size="13"
                popper-class="snapshot-metric-help-popper"
              />
            </dt>
            <dd>{{ fmtCombinedReduction(snapshot) }}</dd>
          </div>
        </dl>
      </div>
    </section>

    <section class="snapshot-point-detail-section snapshot-point-detail-section--browser">
      <div class="snapshot-point-detail-section__title snapshot-point-detail-browser__title">
        <span>{{ t('protection.backupsPage.snapshotBrowserPreviewTitle') }}</span>
        <div
          v-if="snapshotDirectories.length"
          class="snapshot-point-detail-browser__actions"
        >
          <span v-if="downloadLimits">
            {{ t('protection.backupsPage.snapshotBrowserSelectedLimitCount', {
              selected: selectedCount,
              limit: downloadLimits.maxItems,
            }) }}
          </span>
          <span v-else>{{ t('protection.backupsPage.snapshotBrowserSelectedCount', { n: selectedCount }) }}</span>
          <ElButton
            v-if="selectedCount"
            size="small"
            :disabled="downloadingSelected"
            @click="clearDownloadSelection"
          >
            {{ t('protection.backupsPage.snapshotBrowserClearSelection') }}
          </ElButton>
          <SnapshotDownloadLimitsPopover
            v-if="downloadLimits"
            :max-items="downloadLimits.maxItems"
            :max-size-bytes="downloadLimits.maxSizeBytes"
          />
          <ElButton
            type="primary"
            size="small"
            :loading="downloadingSelected"
            :disabled="!selectedCount"
            @click="downloadSelection"
          >
            <Download :size="14" />
            {{ downloadPhase === 'calculating'
              ? t('protection.backupsPage.snapshotBrowserCalculatingSize')
              : downloadPhase === 'preparing'
                ? t('protection.backupsPage.snapshotBrowserPreparingDownload')
                : t('protection.backupsPage.snapshotBrowserDownloadSelected') }}
          </ElButton>
        </div>
      </div>

      <ElAlert
        v-if="error"
        :title="error"
        type="error"
        show-icon
        :closable="false"
        class="snapshot-point-detail-browser__error"
      >
        <template #default>
          <ElButton
            size="small"
            @click="emit('retry')"
          >
            <RefreshCw :size="14" />
            {{ t('common.retry') }}
          </ElButton>
        </template>
      </ElAlert>

      <div
        v-if="snapshotDirectories.length"
        class="snapshot-point-detail-source-tree"
      >
        <div class="snapshot-point-detail-source-tree__header">
          <span class="snapshot-point-detail-source-tree__header-path">
            {{ t('protection.backupDetail.colBackupDir') }}
          </span>
          <span class="snapshot-point-detail-source-tree__header-metric">
            {{ t('protection.backupsPage.snapshotRecoverableData') }}
          </span>
          <span class="snapshot-point-detail-source-tree__header-metric">
            {{ t('protection.backupsPage.snapshotBrowserFileDirCount') }}
          </span>
          <span class="snapshot-point-detail-source-tree__header-status">
            {{ t('protection.backupDetail.labelStatus') }}
          </span>
          <span class="snapshot-point-detail-source-tree__header-selected">
            {{ t('protection.backupsPage.snapshotBrowserSelected') }}
          </span>
        </div>

        <div
          v-for="directory in snapshotDirectories"
          :key="directory.id"
          class="snapshot-point-detail-source-tree__group"
          :class="{
            'is-expanded': directoryBrowserState(directory).expanded,
            'is-disabled': !canBrowseDirectory(directory),
          }"
        >
          <div class="snapshot-point-detail-source-tree__root">
            <div class="snapshot-point-detail-source-tree__root-main">
              <button
                type="button"
                class="snapshot-point-detail-source-tree__toggle"
                :disabled="!canBrowseDirectory(directory)"
                :aria-label="directory.source_path"
                :aria-expanded="directoryBrowserState(directory).expanded"
                @click="browseSnapshotDirectory(directory)"
              >
                <ChevronRight
                  :size="16"
                  class="snapshot-point-detail-source-tree__chevron"
                  :class="{ 'is-expanded': directoryBrowserState(directory).expanded }"
                  aria-hidden="true"
                />
              </button>
              <ElCheckbox
                :model-value="sourcePathChecked(directory)"
                :disabled="!canBrowseDirectory(directory)"
                :aria-label="directory.source_path"
                @change="onSourcePathCheckChange(directory, $event)"
                @click.stop
              />
              <component
                :is="directory.path_type === 'file' ? File : Folder"
                :size="17"
                :class="directory.path_type === 'file'
                  ? 'snapshot-point-detail-table__file-icon'
                  : 'snapshot-point-detail-table__folder-icon'"
              />
              <button
                type="button"
                class="snapshot-point-detail-source-tree__path"
                :disabled="!canBrowseDirectory(directory)"
                :aria-expanded="directoryBrowserState(directory).expanded"
                @click="browseSnapshotDirectory(directory)"
              >
                <code>{{ directory.source_path }}</code>
              </button>
            </div>
            <div class="snapshot-point-detail-source-tree__metric">
              {{ fmtBytes(Number(directory.recoverable_size_bytes ?? directory.size_bytes ?? 0)) }}
            </div>
            <div class="snapshot-point-detail-source-tree__metric">
              {{ directory.file_count }}/{{ directory.dir_count }}
            </div>
            <div class="snapshot-point-detail-source-tree__status">
              <ElTag
                size="small"
                class="snapshot-status-tag"
                v-bind="lifecycleStatusTagAttrs(directory.status)"
              >
                <LoaderCircle
                  v-if="snapshotStatusInProgress(directory.status)"
                  :size="12"
                  class="snapshot-status-tag__spinner"
                  aria-hidden="true"
                />
                {{ snapshotStatusLabel(directory.status) }}
              </ElTag>
              <HflHelpTip
                v-if="!canBrowseDirectory(directory)"
                :content="directoryBrowseUnavailableReason(directory)"
                :aria-label="directoryBrowseUnavailableReason(directory)"
                :size="14"
                placement="left"
                popper-class="snapshot-source-path-help-popper"
              />
            </div>
            <div class="snapshot-point-detail-source-tree__selected">
              <span>{{ directorySelectedCount(directory) }}</span>
            </div>
          </div>

          <div
            v-if="directoryBrowserState(directory).expanded"
            class="snapshot-point-detail-source-tree__children"
          >
            <ElAlert
              v-if="directoryBrowserState(directory).error"
              :title="directoryBrowserState(directory).error"
              type="error"
              show-icon
              :closable="false"
              class="snapshot-point-detail-source-tree__error"
            >
              <template #default>
                <ElButton
                  size="small"
                  @click="openDirectory(directory, directoryBrowserState(directory).path, { force: true })"
                >
                  <RefreshCw :size="14" />
                  {{ t('common.retry') }}
                </ElButton>
              </template>
            </ElAlert>
            <div
              v-loading="directoryBrowserState(directory).loading"
              class="snapshot-point-detail-table"
            >
              <div class="snapshot-point-detail-table__header">
                <span>{{ t('protection.backupsPage.snapshotBrowserName') }}</span>
                <span>{{ t('protection.backupsPage.snapshotBrowserPath') }}</span>
                <span>{{ t('protection.backupsPage.snapshotBrowserSize') }}</span>
                <span>{{ t('protection.backupsPage.snapshotBrowserModified') }}</span>
              </div>

              <div
                v-if="directory.path_type === 'file'"
                class="snapshot-point-detail-table__row"
              >
                <ElCheckbox
                  :model-value="directoryBrowserState(directory).fileChecked"
                  @change="onSnapshotFileCheckChange(directory, $event)"
                />
                <span class="snapshot-point-detail-table__name">
                  <File
                    :size="16"
                    class="snapshot-point-detail-table__file-icon"
                  />
                  <span class="snapshot-point-detail-truncate">{{ snapshotFileFallbackName(directory) }}</span>
                </span>
                <span class="snapshot-point-detail-table__path">{{ directory.source_path }}</span>
                <span>{{ fmtBytes(directory.size_bytes) }}</span>
                <span>{{ formatNullableTime(directory.created_at) }}</span>
              </div>

              <ElTree
                v-else
                :ref="(instance) => setBrowserTreeRef(directory.id, instance)"
                :key="`${directory.id}:${directoryBrowserState(directory).path}:${directoryBrowserState(directory).treeVersion}`"
                node-key="id"
                show-checkbox
                check-strictly
                lazy
                :load="(node, resolve) => loadBrowserTreeNode(directory, node, resolve)"
                :props="{ children: 'children', label: 'label', disabled: 'disabled', isLeaf: 'isLeaf' }"
                class="snapshot-point-detail-tree"
                empty-text=" "
                @check-change="(data, checked) => onBrowserTreeCheckChange(directory, data, checked)"
              >
                <template #default="{ data }">
                  <div
                    v-if="data.loadMore"
                    class="snapshot-point-detail-tree__load-more"
                  >
                    <span>{{ t('protection.backupsPage.snapshotBrowserPartialCount', { n: data.loadedCount }) }}</span>
                    <ElButton
                      type="primary"
                      link
                      :loading="data.loadingMore"
                      @click.stop="loadMoreBrowserTreeEntries(directory, data)"
                    >
                      {{ t('protection.backupsPage.snapshotBrowserLoadMore') }}
                    </ElButton>
                  </div>
                  <div
                    v-else
                    class="snapshot-point-detail-tree__row"
                  >
                    <span class="snapshot-point-detail-table__name">
                      <Folder
                        v-if="data.type === 'dir'"
                        :size="16"
                        class="snapshot-point-detail-table__folder-icon"
                      />
                      <File
                        v-else
                        :size="16"
                        class="snapshot-point-detail-table__file-icon"
                      />
                      <span class="snapshot-point-detail-truncate">{{ data.name }}</span>
                    </span>
                    <span class="snapshot-point-detail-table__path">{{ data.path }}</span>
                    <span>{{ data.type === 'dir' ? '—' : fmtBytes(data.size_bytes) }}</span>
                    <span>{{ formatNullableTime(data.modified_at) }}</span>
                  </div>
                </template>
              </ElTree>

              <ElEmpty
                v-if="!directoryBrowserState(directory).loading
                  && !directoryBrowserState(directory).error
                  && !directoryItemCount(directory)"
                :description="t('protection.backupsPage.snapshotBrowserEmpty')"
                :image-size="48"
                class="snapshot-point-detail-browser__empty"
              />
            </div>
          </div>
        </div>
      </div>

      <ElEmpty
        v-else
        v-loading="loading"
        :description="loading
          ? t('common.loading')
          : t('protection.backupsPage.snapshotBrowserEmptyDirectories')"
        :image-size="48"
        class="snapshot-point-detail-browser__empty"
      />

      <footer
        v-if="snapshotDirectories.length"
        class="snapshot-point-detail-browser__footer"
      >
        <span>{{ t('protection.backupsPage.snapshotBrowserSelectedCount', { n: selectedCount }) }}</span>
        <span>{{ t('protection.backupsPage.snapshotBrowserFooterHint') }}</span>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.snapshot-point-detail-panel {
  display: grid;
  width: 100%;
  height: 100%;
  min-height: 0;
  grid-template-rows: 116px minmax(0, 1fr);
  gap: 10px;
  overflow: hidden;
}

.snapshot-point-detail-section {
  display: grid;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 1px solid rgb(226 232 240);
  border-radius: 9px;
  background: #fff;
}

.snapshot-point-detail-section--overview {
  grid-template-rows: minmax(0, 1fr);
}

.snapshot-point-detail-section--browser {
  grid-template-rows: auto auto minmax(0, 1fr) auto;
}

.snapshot-point-detail-section__title {
  position: relative;
  display: flex;
  min-height: 42px;
  align-items: center;
  padding: 9px 16px 9px 20px;
  border-bottom: 1px solid rgb(226 232 240);
  background: #fff;
  color: rgb(30 41 59);
  font-size: 13px;
  font-weight: 650;
}

.snapshot-point-detail-section__title::before {
  position: absolute;
  top: 11px;
  bottom: 11px;
  left: 9px;
  width: 3px;
  border-radius: 999px;
  background: var(--color-primary);
  content: '';
}

.snapshot-point-detail-section__scroll {
  min-height: 0;
  overflow: auto;
  padding: 6px 10px;
}

.snapshot-point-detail-metrics {
  display: grid;
  min-width: 560px;
  grid-template-columns: repeat(3, minmax(150px, 1fr));
  gap: 6px;
  margin: 0;
}

.snapshot-point-detail-metrics__item {
  min-width: 0;
  padding: 4px 8px;
  border-radius: 6px;
  background: rgb(248 250 252);
}

.snapshot-point-detail-metrics__item dt {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 5px;
  color: rgb(100 116 139);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.snapshot-point-detail-metrics__item dd {
  overflow: hidden;
  margin: 2px 0 0;
  color: rgb(30 41 59);
  font-size: 13px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.snapshot-point-detail-browser__title {
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px 16px;
}

.snapshot-point-detail-browser__actions,
.snapshot-point-detail-table__name {
  display: inline-flex;
  min-width: 0;
  align-items: center;
}

.snapshot-point-detail-browser__actions {
  flex: 0 0 auto;
  gap: 10px;
  color: rgb(100 116 139);
  font-size: 11px;
  font-weight: 400;
  white-space: nowrap;
}

.snapshot-point-detail-browser__actions :deep(.el-button) {
  display: inline-flex;
  gap: 6px;
}

.snapshot-point-detail-browser__error {
  margin: 8px 10px;
}

.snapshot-point-detail-source-tree {
  min-width: 0;
  min-height: 0;
  overflow: auto;
  background: #fff;
}

.snapshot-point-detail-source-tree__header,
.snapshot-point-detail-source-tree__root {
  display: grid;
  box-sizing: border-box;
  min-width: 640px;
  grid-template-columns: minmax(280px, 4fr) minmax(102px, 0.7fr) minmax(92px, 0.65fr) 92px 62px;
  align-items: center;
}

.snapshot-point-detail-source-tree__header {
  position: sticky;
  z-index: 2;
  top: 0;
  min-height: 34px;
  border-bottom: 1px solid rgb(203 213 225);
  background: rgb(241 245 249);
  color: rgb(71 85 105);
  font-size: 11px;
  font-weight: 600;
}

.snapshot-point-detail-source-tree__header > span {
  padding: 0 14px;
}

.snapshot-point-detail-source-tree__header-path {
  padding-left: 84px !important;
}

.snapshot-point-detail-source-tree__header-metric,
.snapshot-point-detail-source-tree__metric {
  text-align: right;
}

.snapshot-point-detail-source-tree__header-status,
.snapshot-point-detail-source-tree__header-selected {
  text-align: center;
}

.snapshot-point-detail-source-tree__group {
  min-width: 640px;
  border-bottom: 1px solid rgb(226 232 240);
}

.snapshot-point-detail-source-tree__group.is-expanded {
  box-shadow: inset 3px 0 0 var(--color-primary);
}

.snapshot-point-detail-source-tree__root {
  min-height: 40px;
  background: #fff;
  transition: background-color 150ms ease;
}

.snapshot-point-detail-source-tree__group:not(.is-disabled):not(.is-expanded) .snapshot-point-detail-source-tree__root:hover {
  background: rgb(250 250 252);
}

.snapshot-point-detail-source-tree__group.is-expanded .snapshot-point-detail-source-tree__root {
  background: rgb(245 243 255);
}

.snapshot-point-detail-source-tree__root-main {
  display: grid;
  min-width: 0;
  min-height: 39px;
  grid-template-columns: 20px 18px 18px minmax(0, 1fr);
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  color: rgb(30 41 59);
}

.snapshot-point-detail-source-tree__toggle,
.snapshot-point-detail-source-tree__path {
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.snapshot-point-detail-source-tree__toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.snapshot-point-detail-source-tree__path {
  text-align: left;
}

.snapshot-point-detail-source-tree__path code {
  display: block;
  min-width: 0;
  color: inherit;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.45;
  overflow-wrap: anywhere;
  white-space: normal;
  word-break: break-word;
}

.snapshot-point-detail-source-tree__chevron {
  color: rgb(100 116 139);
  transition: transform 150ms ease;
}

.snapshot-point-detail-source-tree__chevron.is-expanded {
  transform: rotate(90deg);
}

.snapshot-point-detail-source-tree__status,
.snapshot-point-detail-source-tree__selected {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 6px;
}

.snapshot-point-detail-source-tree__metric,
.snapshot-point-detail-source-tree__selected {
  color: rgb(71 85 105);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.snapshot-point-detail-source-tree__metric {
  padding: 0 8px;
  white-space: nowrap;
}

.snapshot-point-detail-source-tree__selected span {
  display: inline-flex;
  min-width: 24px;
  justify-content: center;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgb(238 242 255);
  color: rgb(79 70 229);
  font-weight: 650;
}

.snapshot-point-detail-source-tree__group.is-disabled .snapshot-point-detail-source-tree__root {
  background: rgb(248 250 252 / 72%);
}

.snapshot-point-detail-source-tree__group.is-disabled .snapshot-point-detail-source-tree__root-main {
  color: rgb(148 163 184);
}

.snapshot-point-detail-source-tree__group.is-disabled .snapshot-point-detail-source-tree__toggle,
.snapshot-point-detail-source-tree__group.is-disabled .snapshot-point-detail-source-tree__path {
  cursor: not-allowed;
}

.snapshot-point-detail-source-tree__children {
  margin-left: 24px;
  border-top: 1px solid rgb(221 214 254);
  border-left: 1px solid rgb(226 232 240);
  background: #fff;
}

.snapshot-point-detail-source-tree__error {
  margin: 8px 10px;
}

.snapshot-point-detail-table {
  position: relative;
  min-height: 0;
  overflow: auto;
}

.snapshot-point-detail-table__header {
  display: grid;
  min-width: 600px;
  min-height: 32px;
  grid-template-columns: minmax(140px, 1.25fr) minmax(130px, 0.9fr) 76px 130px;
  align-items: center;
  padding: 0 10px 0 56px;
  border-bottom: 1px solid rgb(226 232 240);
  background: #fff;
  color: rgb(100 116 139);
  font-size: 11px;
  font-weight: 500;
}

.snapshot-point-detail-table__row {
  display: grid;
  min-width: 600px;
  min-height: 34px;
  grid-template-columns: 34px minmax(140px, 1.25fr) minmax(130px, 0.9fr) 76px 130px;
  align-items: center;
  padding: 0 10px;
  border-bottom: 1px solid rgb(241 245 249);
  color: rgb(51 65 85);
  font-size: 12px;
}

.snapshot-point-detail-table__name {
  gap: 7px;
  padding-right: 12px;
}

.snapshot-point-detail-table__path {
  overflow: hidden;
  padding-right: 12px;
  color: rgb(100 116 139);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.snapshot-point-detail-table__folder-icon {
  flex: 0 0 auto;
  color: rgb(217 119 6);
}

.snapshot-point-detail-table__file-icon {
  flex: 0 0 auto;
  color: rgb(59 130 246);
}

.snapshot-point-detail-tree {
  min-width: 600px;
  padding: 2px 0;
  background: transparent;
}

.snapshot-point-detail-tree :deep(.el-tree-node__content) {
  height: 34px;
  border-bottom: 1px solid rgb(241 245 249);
}

.snapshot-point-detail-tree__row {
  display: grid;
  width: 100%;
  min-width: 0;
  flex: 1;
  grid-template-columns: minmax(140px, 1.25fr) minmax(130px, 0.9fr) 76px 130px;
  align-items: center;
  padding-right: 10px;
  color: rgb(51 65 85);
  font-size: 12px;
}

.snapshot-point-detail-tree__load-more {
  display: flex;
  width: 100%;
  min-height: 38px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-right: 14px;
  color: rgb(100 116 139);
  font-size: 11px;
}

.snapshot-point-detail-tree :deep(.el-tree-node__content:has(.snapshot-point-detail-tree__load-more) > .el-checkbox) {
  visibility: hidden;
}

.snapshot-point-detail-browser__empty {
  display: grid;
  height: 100%;
  min-height: 0;
  place-items: center;
}

.snapshot-point-detail-browser__footer {
  display: flex;
  min-height: 34px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 12px;
  border-top: 1px solid rgb(226 232 240);
  background: rgb(248 250 252);
  color: rgb(100 116 139);
  font-size: 11px;
}

.snapshot-point-detail-truncate {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.snapshot-status-tag {
  gap: 4px;
}

.snapshot-status-tag__spinner {
  animation: snapshot-detail-spin 0.8s linear infinite;
}

@keyframes snapshot-detail-spin {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .snapshot-status-tag__spinner {
    animation: none;
  }
}

:global(.snapshot-metric-help-popper.el-popper),
:global(.snapshot-source-path-help-popper.el-popper) {
  box-sizing: border-box;
  width: max-content;
  max-width: min(340px, calc(100vw - 32px)) !important;
  z-index: 3800 !important;
  padding: 11px 13px !important;
  border: 1px solid rgb(203 213 225) !important;
  border-radius: 8px !important;
  background: rgb(255 255 255) !important;
  color: rgb(51 65 85) !important;
  font-size: 12px;
  font-weight: 400;
  line-height: 1.65;
  overflow-wrap: anywhere;
  text-align: left;
  white-space: normal;
  box-shadow: 0 10px 28px rgb(15 23 42 / 16%) !important;
}

:global(.snapshot-metric-help-popper.el-popper .el-popper__arrow::before),
:global(.snapshot-source-path-help-popper.el-popper .el-popper__arrow::before) {
  border-color: rgb(203 213 225) !important;
  background: rgb(255 255 255) !important;
}

@media (max-width: 960px) {
  .snapshot-point-detail-panel {
    grid-template-rows: 116px minmax(0, 1fr);
  }

  .snapshot-point-detail-metrics {
    grid-template-columns: repeat(3, minmax(120px, 1fr));
  }
}
</style>
