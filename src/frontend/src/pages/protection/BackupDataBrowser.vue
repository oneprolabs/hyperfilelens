<script setup lang="ts">
import '../../styles/fullscreen-form-styles'
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ArrowLeft,
  ChevronRight,
  Download,
  File,
  Folder,
  FolderOpen,
  LoaderCircle,
  MoreHorizontal,
  RefreshCw,
  RotateCcw,
  Search,
} from 'lucide-vue-next'
import type { ElTree } from 'element-plus'
import ModulePage from '../../components/ModulePage.vue'
import HflHelpTip from '../../components/HflHelpTip.vue'
import HflPopover from '../../components/HflPopover.vue'
import HflPagination from '../../components/HflPagination.vue'
import { useListSearch } from '../../composables/useListSearch'
import { usePageRequestScope } from '../../composables/usePageRequestScope'
import { useProtectionSideNav } from '../../composables/useProtectionSideNav'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import { apiErrorMessage } from '../../lib/api'
import { formatAppDateTime } from '../../lib/dateTime'
import { toApiError } from '../../lib/errors'
import { lifecycleStatusTagAttrs } from '../../lib/statusTag'
import { pushToast } from '../../lib/toast/store'
import {
  browseBackupSnapshotDirectory,
  createBackupSnapshotDownloadTask,
  createSnapshotArtifactDownloadUrl,
  getBackupSourceSnapshot,
  listBackupSourceSnapshots,
  type BackupSnapshotBrowserEntry,
  type BackupSourceSnapshot,
  type BackupSourceSnapshotDirectory,
} from '../../lib/protectionBackupConfigApi'
import { listBackupSelectableSources, type BackupSelectableSource } from '../../lib/sourceApi'
import { getTask } from '../../lib/taskApi'
import {
  browsableSnapshotDirectories,
  isSnapshotDirectoryBrowsable,
} from './components/snapshotBrowseEligibility'
import { snapshotDownloadSizeLimit } from './components/snapshotDownloadFeedback'
import SnapshotDownloadLimitsPopover from './components/SnapshotDownloadLimitsPopover.vue'
import { isSnapshotRestorable } from './lib/snapshotRestore'

type SourceEndpoint = {
  sourceType: 'agent' | 'nas'
  sourceRefId: number
  sourceId: string
}

type SnapshotTimeMode = 'all' | '24h' | '7d' | '30d' | 'range'

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

const SNAPSHOT_PAGE_SIZE = 10
const SNAPSHOT_PAGE_SIZE_OPTIONS = [10, 20, 30, 50, 100]
const SNAPSHOT_STATUS_OPTIONS = ['creating', 'available', 'partial', 'failed', 'deleting', 'delete_failed', 'deleted']
const HIDDEN_SNAPSHOT_STATUSES = ['failed']
const DIRECTORY_PAGE_LIMIT = 200
const BACKUP_PATH_POPOVER_HIDE_AFTER_MS = 350
const TABLE_HEADER_STYLE: Record<string, string> = {
  background: 'rgba(248, 250, 252, 0.96)',
  color: 'rgb(71 85 105)',
  fontWeight: '600',
  whiteSpace: 'nowrap',
}

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const protectionMenus = useProtectionSideNav()
const requests = usePageRequestScope()
const { drawerSize } = useResponsiveDrawerWidth(1)

function showSnapshotBrowserMessage(
  message: string,
  type: 'error' | 'warning',
  options: { duration?: number; title?: string } = {},
) {
  pushToast({
    message,
    type,
    ...options,
  })
}

function showSnapshotDownloadError(error: unknown) {
  const sizeLimit = snapshotDownloadSizeLimit(error)
  if (sizeLimit) {
    showSnapshotBrowserMessage(
      t('protection.backupsPage.snapshotBrowserDownloadLimitExceeded', {
        selected: fmtBytes(sizeLimit.selectedBytes),
        limit: fmtBytes(sizeLimit.limitBytes),
      }),
      'error',
      { title: t('protection.backupsPage.snapshotBrowserDownloadLimitTitle') },
    )
    return
  }
  showSnapshotBrowserMessage(apiErrorMessage(error, t('errors.generic.requestFailed')), 'error')
}

const source = ref<BackupSelectableSource | null>(null)
const sourceLoading = ref(false)
const sourceError = ref('')
const snapshotRows = ref<BackupSourceSnapshot[]>([])
const snapshotsLoading = ref(false)
const snapshotsError = ref('')
const snapshotFilterId = ref('')
const snapshotFilterStatus = ref('')
const snapshotFilterTimeMode = ref<SnapshotTimeMode>('all')
const snapshotFilterDateRange = ref<[Date, Date] | null>(null)
const snapshotPagination = reactive({ page: 1, pageSize: SNAPSHOT_PAGE_SIZE, count: 0 })

const snapshotDrawerOpen = ref(false)
const activeSnapshotSummary = ref<BackupSourceSnapshot | null>(null)
const activeSnapshotDetail = ref<BackupSourceSnapshot | null>(null)
const snapshotDetailLoading = ref(false)
const snapshotDetailError = ref('')
const browserStates = reactive(new Map<number, DirectoryBrowserState>())
const browserTreeRefs = new Map<number, BrowserTreeInstance>()
const browserRequestControllers = new Map<string, AbortController>()
const downloadPhase = ref<DownloadPhase>('idle')

let sourceRequestRevision = 0
let snapshotListRequestRevision = 0
let snapshotDetailRequestRevision = 0
let suppressSnapshotReload = false

const {
  appliedSearch: appliedSnapshotFilterId,
  clearSearch: clearSnapshotSearch,
  resetSearch: resetSnapshotSearch,
  runSearchNow: runSnapshotSearchNow,
} = useListSearch(snapshotFilterId, reloadSnapshotsFromFirstPage)

function routeParam(value: unknown) {
  return Array.isArray(value) ? String(value[0] || '') : String(value || '')
}

const endpoint = computed<SourceEndpoint | null>(() => {
  const sourceType = routeParam(route.params.sourceType)
  const sourceRefId = Number(routeParam(route.params.sourceRefId))
  if ((sourceType !== 'agent' && sourceType !== 'nas') || !Number.isInteger(sourceRefId) || sourceRefId <= 0) {
    return null
  }
  return {
    sourceType,
    sourceRefId,
    sourceId: `${sourceType}:${sourceRefId}`,
  }
})

const endpointKey = computed(() => endpoint.value?.sourceId || '')
const activeSnapshot = computed(() => activeSnapshotDetail.value ?? activeSnapshotSummary.value)
const activeSnapshotId = computed(() => activeSnapshot.value?.id ?? 0)
const selectedSnapshotDirectories = computed(() => activeSnapshotDetail.value?.directories || [])
const selectedCount = computed(() => selectedSnapshotDirectories.value.reduce(
  (total, directory) => total + directorySelectedCount(directory),
  0,
))
const activeSnapshotDownloadLimits = computed(() => {
  const limits = activeSnapshotDetail.value?.download_limits
  const maxItems = Math.floor(Number(limits?.max_selected_items || 0))
  const maxSizeBytes = Math.floor(Number(limits?.max_logical_size_bytes || 0))
  if (maxItems <= 0 || maxSizeBytes <= 0) return null
  return { maxItems, maxSizeBytes }
})
const snapshotDownloadMaxItems = computed(() => activeSnapshotDownloadLimits.value?.maxItems ?? 100)
const selectedDownloadGroups = computed(() => selectedSnapshotDirectories.value.flatMap((directory) => {
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
const snapshotStatusOptions = computed(() => SNAPSHOT_STATUS_OPTIONS.map((value) => ({
  value,
  label: snapshotStatusLabel(value),
})))
const timeModeOptions = computed(() => [
  { value: 'all' as const, label: t('ops.task.timeAll') },
  { value: '24h' as const, label: t('ops.task.time24h') },
  { value: '7d' as const, label: t('ops.task.time7d') },
  { value: '30d' as const, label: t('ops.task.time30d') },
  { value: 'range' as const, label: t('ops.task.timeRange') },
])

function positiveQueryNumber(value: unknown) {
  const normalized = Array.isArray(value) ? value[0] : value
  const number = Number(normalized)
  return Number.isInteger(number) && number > 0 ? number : 0
}

function queryText(value: unknown) {
  return Array.isArray(value) ? String(value[0] || '') : String(value || '')
}

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

function snapshotIdLabel(snapshot: BackupSourceSnapshot | null) {
  if (!snapshot) return activeSnapshotId.value ? `#${activeSnapshotId.value}` : '—'
  return snapshot.snapshot_uid || `#${snapshot.id}`
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

function sourceContext() {
  const currentSource = source.value
  if (!currentSource) return ''
  return currentSource.hostname || currentSource.node_name || currentSource.node_ip || ''
}

function snapshotDisplayDirectories(snapshot: BackupSourceSnapshot | null) {
  return snapshot?.directories?.filter((directory) => directory.status === 'available') || []
}

function snapshotDisplaySize(snapshot: BackupSourceSnapshot | null) {
  if (!snapshot) return 0
  const value = Number(snapshot.recoverable_size_bytes || snapshot.total_size_bytes || 0)
  if (value > 0) return value
  return snapshotDisplayDirectories(snapshot).reduce((total, directory) => total + Number(directory.size_bytes || 0), 0)
}

function snapshotDisplayFileCount(snapshot: BackupSourceSnapshot) {
  const value = Number(snapshot.file_count || 0)
  if (value > 0) return value
  return snapshotDisplayDirectories(snapshot).reduce((total, directory) => total + Number(directory.file_count || 0), 0)
}

function snapshotDisplayDirCount(snapshot: BackupSourceSnapshot) {
  const value = Number(snapshot.dir_count || 0)
  if (value > 0) return value
  return snapshotDisplayDirectories(snapshot).reduce((total, directory) => total + Number(directory.dir_count || 0), 0)
}

function snapshotBackupPaths(snapshot: BackupSourceSnapshot) {
  return Array.from(new Set(
    (snapshot.directories || [])
      .map((directory) => String(directory.source_path || '').trim())
      .filter(Boolean),
  ))
}

function restoreBlockedByRestore() {
  const runtime = source.value?.runtime?.restore
  return runtime?.running === true || runtime?.stopping === true
}

function canRestoreSnapshot(snapshot: BackupSourceSnapshot) {
  return !restoreBlockedByRestore() && isSnapshotRestorable(snapshot)
}

function snapshotRestoreDisabledReason(snapshot: BackupSourceSnapshot) {
  if (restoreBlockedByRestore()) {
    return t('protection.backupsPage.msgRestoreAlreadyRunning')
  }
  const status = String(snapshot.status || '').toLowerCase()
  if (status !== 'available' && status !== 'partial') {
    return t('protection.backupsPage.snapshotReasonStatusUnavailable', {
      status: snapshotStatusLabel(snapshot.status),
    })
  }
  return t('protection.backupsPage.snapshotReasonNoUsableDirectories')
}

function openSnapshotRestore(snapshot: BackupSourceSnapshot) {
  if (!canRestoreSnapshot(snapshot)) return
  void router.push({
    name: 'protection-snapshot-restore',
    params: { snapshotId: String(snapshot.id) },
  })
}

function backToBackups() {
  void router.push({
    path: '/protection/backups',
    query: { step: 'start-backup' },
  })
}

function snapshotFileFallbackName(directory: BackupSourceSnapshotDirectory) {
  return directory.display_name || directory.source_path.split(/[\\/]/).filter(Boolean).pop() || 'snapshot-file'
}

function isoDateParam(date?: Date | null) {
  return date instanceof Date && Number.isFinite(date.getTime()) ? date.toISOString() : undefined
}

function snapshotTimeRangeParams() {
  const now = new Date()
  if (snapshotFilterTimeMode.value === '24h') {
    return { from: new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString(), to: undefined }
  }
  if (snapshotFilterTimeMode.value === '7d') {
    return { from: new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString(), to: undefined }
  }
  if (snapshotFilterTimeMode.value === '30d') {
    return { from: new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString(), to: undefined }
  }
  if (snapshotFilterTimeMode.value === 'range' && snapshotFilterDateRange.value) {
    const end = new Date(snapshotFilterDateRange.value[1])
    end.setMilliseconds(999)
    return { from: isoDateParam(snapshotFilterDateRange.value[0]), to: isoDateParam(end) }
  }
  return { from: undefined, to: undefined }
}

function onSnapshotTimeModeChange(value: SnapshotTimeMode) {
  if (value !== 'range') snapshotFilterDateRange.value = null
}

function reloadSnapshotsFromFirstPage() {
  if (snapshotPagination.page !== 1) {
    snapshotPagination.page = 1
    return
  }
  void loadSnapshotRows()
}

async function loadSource() {
  const currentEndpoint = endpoint.value
  const revision = ++sourceRequestRevision
  requests.abortScope('backup-data-browser-source')
  source.value = null
  sourceError.value = ''
  if (!currentEndpoint) {
    sourceError.value = t('protection.backupDetail.notFound')
    return
  }

  const signal = requests.nextSignal('backup-data-browser-source')
  sourceLoading.value = true
  try {
    const result = await listBackupSelectableSources({
      ids: currentEndpoint.sourceId,
      page: 1,
      page_size: 1,
      expand: 'runtime',
    }, { signal })
    if (revision !== sourceRequestRevision || signal.aborted) return
    source.value = result.results.find((candidate) => candidate.id === currentEndpoint.sourceId) ?? null
    if (!source.value) sourceError.value = t('protection.backupDetail.notFound')
  } catch (error) {
    if (!requests.isAbortError(error) && revision === sourceRequestRevision) {
      sourceError.value = apiErrorMessage(error, t('errors.generic.loadFailed'))
    }
  } finally {
    requests.releaseSignal('backup-data-browser-source', signal)
    if (revision === sourceRequestRevision) sourceLoading.value = false
  }
}

async function loadSnapshotRows(options: { restoreDrawer?: boolean } = {}) {
  const currentEndpoint = endpoint.value
  const revision = ++snapshotListRequestRevision
  requests.abortScope('backup-data-browser-list')
  snapshotsError.value = ''
  if (!currentEndpoint) {
    snapshotRows.value = []
    snapshotPagination.count = 0
    return
  }

  const signal = requests.nextSignal('backup-data-browser-list')
  snapshotsLoading.value = true
  try {
    const startedRange = snapshotTimeRangeParams()
    const result = await listBackupSourceSnapshots({
      page: snapshotPagination.page,
      page_size: snapshotPagination.pageSize,
      snapshot_uid: appliedSnapshotFilterId.value || undefined,
      source_type: currentEndpoint.sourceType,
      source_ref_id: currentEndpoint.sourceRefId,
      status: snapshotFilterStatus.value || undefined,
      exclude_status: snapshotFilterStatus.value ? undefined : HIDDEN_SNAPSHOT_STATUSES.join(','),
      started_from: startedRange.from,
      started_to: startedRange.to,
      ordering: '-created_at',
      include_directory_snapshots: 1,
    }, { signal })
    if (revision !== snapshotListRequestRevision || signal.aborted) return
    snapshotRows.value = result.results
    snapshotPagination.count = result.count
    if (options.restoreDrawer) void restoreDrawerFromRoute()
  } catch (error) {
    if (!requests.isAbortError(error) && revision === snapshotListRequestRevision) {
      snapshotsError.value = apiErrorMessage(error, t('errors.generic.loadFailed'))
      snapshotRows.value = []
      snapshotPagination.count = 0
    }
  } finally {
    requests.releaseSignal('backup-data-browser-list', signal)
    if (revision === snapshotListRequestRevision) snapshotsLoading.value = false
  }
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

function resetBrowsers() {
  abortBrowserRequests()
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

function abortBrowserRequests() {
  for (const controller of browserRequestControllers.values()) controller.abort()
  browserRequestControllers.clear()
}

requests.registerCleanup(abortBrowserRequests)

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

function syncSnapshotRoute(snapshotId: number) {
  void router.replace({
    path: route.path,
    query: {
      ...route.query,
      snapshot: String(snapshotId),
      directory: undefined,
      path: undefined,
    },
  }).catch(() => undefined)
}

function syncDirectoryRoute(directoryId: number, path: string) {
  void router.replace({
    path: route.path,
    query: {
      ...route.query,
      snapshot: String(activeSnapshotId.value),
      directory: String(directoryId),
      path: path || undefined,
    },
  }).catch(() => undefined)
}

function clearDrawerRoute() {
  void router.replace({
    path: route.path,
    query: {
      ...route.query,
      snapshot: undefined,
      directory: undefined,
      path: undefined,
    },
  }).catch(() => undefined)
}

function scheduleDirectoryLoad(directory: BackupSourceSnapshotDirectory, path = '') {
  const snapshotId = activeSnapshotId.value
  const directoryId = directory.id
  const state = directoryBrowserState(directory)
  state.expanded = true
  state.loading = directory.path_type !== 'file'
  void nextTick().then(() => {
    window.requestAnimationFrame(() => {
      if (
        !snapshotDrawerOpen.value
        || activeSnapshotId.value !== snapshotId
        || !browserStates.get(directoryId)?.expanded
      ) return
      void openDirectory(directory, path)
    })
  })
}

async function applySnapshotDetail(
  detail: BackupSourceSnapshot,
  options: { restoreSelection?: boolean; preserveBrowsers?: boolean } = {},
) {
  activeSnapshotDetail.value = detail
  snapshotRows.value = snapshotRows.value.map((row) => row.id === detail.id ? detail : row)
  snapshotDetailLoading.value = false
  if (options.preserveBrowsers) return
  const browsableDirectories = browsableSnapshotDirectories(detail)
  const requestedDirectoryId = options.restoreSelection ? positiveQueryNumber(route.query.directory) : 0
  const directory = browsableDirectories.find((candidate) => candidate.id === requestedDirectoryId)
    ?? null
  snapshotDetailLoading.value = false
  resetBrowsers()
  for (const candidate of detail.directories || []) directoryBrowserState(candidate)
  if (!snapshotDrawerOpen.value || activeSnapshotId.value !== detail.id) return
  if (!directory) return
  const requestedPath = options.restoreSelection && directory.path_type !== 'file'
    ? queryText(route.query.path)
    : ''
  scheduleDirectoryLoad(directory, requestedPath)
}

async function loadSnapshotDetail(
  snapshotId: number,
  options: { restoreSelection?: boolean; preserveBrowsers?: boolean } = {},
) {
  const currentEndpoint = endpoint.value
  const revision = ++snapshotDetailRequestRevision
  requests.abortScope('backup-data-browser-detail')
  snapshotDetailError.value = ''
  snapshotDetailLoading.value = true
  if (!currentEndpoint) return

  const signal = requests.nextSignal('backup-data-browser-detail')
  try {
    const detail = await getBackupSourceSnapshot(snapshotId, { signal })
    if (
      revision !== snapshotDetailRequestRevision
      || signal.aborted
      || !snapshotDrawerOpen.value
      || Number(detail.source_ref_id) !== currentEndpoint.sourceRefId
      || detail.source_type !== currentEndpoint.sourceType
    ) return
    activeSnapshotSummary.value ||= detail
    await applySnapshotDetail(detail, options)
  } catch (error) {
    if (!requests.isAbortError(error) && revision === snapshotDetailRequestRevision) {
      snapshotDetailError.value = apiErrorMessage(error, t('errors.generic.loadFailed'))
    }
  } finally {
    requests.releaseSignal('backup-data-browser-detail', signal)
    if (revision === snapshotDetailRequestRevision) {
      snapshotDetailLoading.value = false
    }
  }
}

function openSnapshotDrawer(row: BackupSourceSnapshot) {
  snapshotDetailRequestRevision += 1
  resetBrowsers()
  activeSnapshotSummary.value = row
  activeSnapshotDetail.value = null
  snapshotDetailError.value = ''
  snapshotDrawerOpen.value = true
  syncSnapshotRoute(row.id)
  void loadSnapshotDetail(row.id)
}

async function restoreDrawerFromRoute() {
  const snapshotId = positiveQueryNumber(route.query.snapshot)
  if (!snapshotId) return
  const row = snapshotRows.value.find((candidate) => candidate.id === snapshotId) ?? null
  snapshotDetailRequestRevision += 1
  resetBrowsers()
  activeSnapshotSummary.value = row
  activeSnapshotDetail.value = null
  snapshotDetailError.value = ''
  snapshotDrawerOpen.value = true
  await loadSnapshotDetail(snapshotId, { restoreSelection: true })
}

function onSnapshotDrawerClosed() {
  requests.abortScope('backup-data-browser-detail')
  requests.abortScope('backup-data-browser-download')
  snapshotDetailRequestRevision += 1
  activeSnapshotSummary.value = null
  activeSnapshotDetail.value = null
  snapshotDetailLoading.value = false
  snapshotDetailError.value = ''
  downloadPhase.value = 'idle'
  resetBrowsers()
  clearDrawerRoute()
}

function canBrowseDirectory(directory: BackupSourceSnapshotDirectory) {
  return isSnapshotDirectoryBrowsable(activeSnapshot.value?.status, directory)
}

function directoryBrowseUnavailableReason(directory: BackupSourceSnapshotDirectory) {
  const snapshotStatus = String(activeSnapshot.value?.status || '').toLowerCase()
  if (snapshotStatus !== 'available' && snapshotStatus !== 'partial') {
    return t('protection.backupsPage.snapshotBrowserSnapshotStatusUnavailable', {
      status: snapshotStatusLabel(activeSnapshot.value?.status),
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
  if (state.expanded) {
    state.expanded = false
    if (positiveQueryNumber(route.query.directory) === directory.id) {
      syncSnapshotRoute(activeSnapshotId.value)
    }
    return
  }
  state.expanded = true
  if (!state.loaded && !state.loading) void openDirectory(directory, state.path)
}

async function openDirectory(
  directory: BackupSourceSnapshotDirectory,
  path = '',
  options: { force?: boolean } = {},
) {
  if (!canBrowseDirectory(directory)) return
  const snapshotId = activeSnapshotId.value
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
    syncDirectoryRoute(directory.id, '')
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
      || !snapshotDrawerOpen.value
      || activeSnapshotId.value !== snapshotId
      || browserStates.get(directory.id) !== state
    ) return
    state.path = result.path || ''
    state.entries = result.entries
    state.treeEntries = browserPageTreeNodes(state, result, state.path, 0)
    state.treeVersion += 1
    state.loaded = true
    refreshBrowserTreeDisabled(state)
    if (state.expanded) syncDirectoryRoute(directory.id, state.path)
    await nextTick()
    syncBrowserTreeCheckedKeys(directory.id, state)
  } catch (error) {
    if (
      requests.isAbortError(error)
      || revision !== state.requestRevision
      || activeSnapshotId.value !== snapshotId
    ) return
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
  const directoryId = directory.id
  const treeVersion = state.treeVersion
  const requestKey = `node:${directoryId}:${data.path}`
  const signal = nextBrowserRequestSignal(requestKey)
  try {
    const result = await browseBackupSnapshotDirectory(directoryId, {
      path: data.path,
      limit: DIRECTORY_PAGE_LIMIT,
    }, { signal })
    if (browserStates.get(directoryId) !== state || state.treeVersion !== treeVersion) {
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
    if (!requests.isAbortError(error)) {
      showSnapshotBrowserMessage(apiErrorMessage(error, t('errors.generic.loadFailed')), 'error')
    }
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
  const directoryId = directory.id
  const parentPath = data.parentPath || ''
  const treeVersion = state.treeVersion
  const requestKey = `page:${directoryId}:${parentPath}:${data.nextCursor}`
  const signal = nextBrowserRequestSignal(requestKey)
  data.loadingMore = true
  state.treeEntries = [...state.treeEntries]
  try {
    const result = await browseBackupSnapshotDirectory(directoryId, {
      path: parentPath,
      limit: DIRECTORY_PAGE_LIMIT,
      cursor: data.nextCursor,
    }, { signal })
    if (browserStates.get(directoryId) !== state || state.treeVersion !== treeVersion) return
    const replacements = browserPageTreeNodes(state, result, parentPath, data.loadedCount || 0)
    if (!replaceBrowserLoadMoreNode(state.treeEntries, data.id, replacements)) return
    for (const replacement of replacements) {
      browserTree(directoryId)?.insertBefore(replacement, data)
    }
    browserTree(directoryId)?.remove(data)
    if (parentPath === state.path) {
      state.entries = [...state.entries, ...result.entries]
    }
    state.treeEntries = [...state.treeEntries]
    refreshBrowserTreeDisabled(state)
    await nextTick()
    syncBrowserTreeCheckedKeys(directoryId, state)
  } catch (error) {
    if (!requests.isAbortError(error)) {
      showSnapshotBrowserMessage(apiErrorMessage(error, t('errors.generic.loadFailed')), 'error')
    }
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
  if (projectedCount > snapshotDownloadMaxItems.value) {
    showSnapshotBrowserMessage(
      t('protection.backupsPage.snapshotBrowserSelectionLimit', { n: snapshotDownloadMaxItems.value }),
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
  if (nextChecked && projectedCount > snapshotDownloadMaxItems.value) {
    showSnapshotBrowserMessage(
      t('protection.backupsPage.snapshotBrowserSelectionLimit', { n: snapshotDownloadMaxItems.value }),
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
  if (nextChecked && !state.fileChecked && selectedCount.value >= snapshotDownloadMaxItems.value) {
    showSnapshotBrowserMessage(
      t('protection.backupsPage.snapshotBrowserSelectionLimit', { n: snapshotDownloadMaxItems.value }),
      'warning',
    )
    return
  }
  state.fileChecked = nextChecked
}

function clearDownloadSelection() {
  for (const directory of selectedSnapshotDirectories.value) {
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
  const snapshotId = activeSnapshotId.value
  if (!snapshotId || !selectedCount.value || downloadingSelected.value) {
    showSnapshotBrowserMessage(
      t('protection.backupsPage.snapshotBrowserSelectBeforeDownload'),
      'warning',
    )
    return
  }

  const signal = requests.nextSignal('backup-data-browser-download')
  downloadPhase.value = 'calculating'
  try {
    const task = await createBackupSnapshotDownloadTask(snapshotId, selectedDownloadGroups.value)
    downloadPhase.value = 'preparing'
    const artifactId = await waitForDownloadArtifact(task.task_uuid, signal)
    if (!signal.aborted) await startNativeArtifactDownload(artifactId)
  } catch (error) {
    if (!requests.isAbortError(error)) {
      const apiError = toApiError(error)
      const upgradeRequired = apiError?.errorCode === 'PROTECTION.SNAPSHOT_MULTI_DOWNLOAD_UPGRADE_REQUIRED'
      if (upgradeRequired) {
        showSnapshotBrowserMessage(
          t(source.value?.kind === 'nas' || endpoint.value?.sourceType === 'nas'
            ? 'protection.backupsPage.snapshotBrowserNasProxyUpgradeRequired'
            : 'protection.backupsPage.snapshotBrowserSourceUpgradeRequired'),
          'warning',
          { duration: 8000 },
        )
      } else {
        showSnapshotDownloadError(error)
      }
    }
  } finally {
    requests.releaseSignal('backup-data-browser-download', signal)
    if (!signal.aborted) downloadPhase.value = 'idle'
  }
}

async function loadPage() {
  requests.abortScope('backup-data-browser-download')
  snapshotDrawerOpen.value = false
  suppressSnapshotReload = true
  resetSearchState()
  await nextTick()
  suppressSnapshotReload = false
  await Promise.all([
    loadSource(),
    loadSnapshotRows({ restoreDrawer: true }),
  ])
}

function resetSearchState() {
  resetSnapshotSearch()
  snapshotFilterStatus.value = ''
  snapshotFilterTimeMode.value = 'all'
  snapshotFilterDateRange.value = null
  snapshotPagination.page = 1
  snapshotPagination.pageSize = SNAPSHOT_PAGE_SIZE
  snapshotPagination.count = 0
  snapshotRows.value = []
  snapshotsError.value = ''
}

watch(endpointKey, () => {
  void loadPage()
}, { immediate: true })

watch(
  () => [snapshotFilterStatus.value, snapshotFilterTimeMode.value, snapshotFilterDateRange.value] as const,
  () => {
    if (!suppressSnapshotReload) runSnapshotSearchNow()
  },
)

watch(
  () => [snapshotPagination.page, snapshotPagination.pageSize] as const,
  () => {
    if (!suppressSnapshotReload) void loadSnapshotRows()
  },
)
</script>

<template>
  <ModulePage
    :title="t('protection.moduleTitle')"
    :page-title-override="t('protection.backupsPage.snapshotBrowserPageTitle')"
    :menus="protectionMenus"
    hide-page-title
  >
    <div class="backup-data-browser-page">
      <header class="fullscreen-form-header backup-data-browser-header">
        <button
          type="button"
          class="fullscreen-form-header__back"
          :title="t('protection.backupDetail.backToList')"
          :aria-label="t('protection.backupDetail.backToList')"
          @click="backToBackups"
        >
          <ArrowLeft
            class="fullscreen-form-header__back-icon"
            :size="18"
          />
        </button>
        <div class="fullscreen-form-header__content">
          <h1 class="fullscreen-form-header__title">
            {{ t('protection.backupsPage.snapshotBrowserPageTitle') }}
          </h1>
          <p class="fullscreen-form-header__desc">
            {{ t('protection.backupsPage.snapshotBrowserPageDesc') }}
          </p>
        </div>
      </header>

      <ElAlert
        v-if="sourceError"
        :title="sourceError"
        type="error"
        show-icon
        :closable="false"
      >
        <template #default>
          <ElButton
            size="small"
            @click="loadSource"
          >
            <RefreshCw :size="14" />
            {{ t('common.retry') }}
          </ElButton>
        </template>
      </ElAlert>

      <section
        v-if="source || sourceLoading"
        v-loading="sourceLoading"
        class="backup-data-source-summary"
      >
        <div class="backup-data-source-summary__icon">
          <FolderOpen :size="22" />
        </div>
        <div class="backup-data-source-summary__main">
          <h2 :title="source?.name || ''">
            {{ source?.name || '—' }}
          </h2>
          <span
            v-if="sourceContext()"
            :title="sourceContext()"
          >{{ sourceContext() }}</span>
        </div>
        <ElTag
          v-if="source?.availability"
          size="small"
          :type="source.availability === 'online' ? 'success' : 'danger'"
        >
          {{ source.availability === 'online'
            ? t('protection.backupsPage.sourceStatusOnline')
            : t('protection.backupsPage.sourceStatusOffline') }}
        </ElTag>
      </section>

      <section class="hfl-list-shell backup-data-snapshot-list">
        <div class="hfl-list-toolbar backup-data-snapshot-toolbar">
          <ElInput
            v-model="snapshotFilterId"
            clearable
            class="hfl-list-search backup-data-snapshot-toolbar__search"
            :placeholder="t('protection.backupsPage.flowSnapshotSearchPlaceholder')"
            @clear="clearSnapshotSearch"
          >
            <template #prefix>
              <Search
                :size="16"
                class="text-slate-400"
              />
            </template>
          </ElInput>
          <ElSelect
            v-model="snapshotFilterStatus"
            clearable
            :placeholder="t('ops.task.filterStatus')"
            class="backup-data-snapshot-toolbar__status"
          >
            <ElOption
              v-for="option in snapshotStatusOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </ElSelect>
          <ElSelect
            v-model="snapshotFilterTimeMode"
            :placeholder="t('protection.backupsPage.flowSnapshotStartTime')"
            class="backup-data-snapshot-toolbar__time"
            @change="onSnapshotTimeModeChange"
          >
            <ElOption
              v-for="option in timeModeOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </ElSelect>
          <ElDatePicker
            v-if="snapshotFilterTimeMode === 'range'"
            v-model="snapshotFilterDateRange"
            type="datetimerange"
            format="YYYY-MM-DD HH:mm:ss"
            :default-time="[new Date(2000, 0, 1, 0, 0, 0), new Date(2000, 0, 1, 23, 59, 59)]"
            :start-placeholder="t('ops.task.startTime')"
            :end-placeholder="t('ops.task.endTime')"
            class="backup-data-snapshot-toolbar__range"
          />
          <div class="hfl-list-toolbar__right">
            <ElButton
              class="hfl-refresh-button"
              :title="t('ops.task.btnRefresh')"
              :aria-label="t('ops.task.btnRefresh')"
              :disabled="snapshotsLoading"
              @click="loadSnapshotRows"
            >
              <RefreshCw
                :size="16"
                :class="{ 'is-spinning': snapshotsLoading }"
              />
            </ElButton>
          </div>
        </div>

        <ElAlert
          v-if="snapshotsError"
          :title="snapshotsError"
          type="error"
          show-icon
          :closable="false"
        />

        <ElTable
          v-if="snapshotRows.length || snapshotsLoading"
          v-table-column-resize="'protection.backupDataBrowser.snapshots'"
          v-table-overflow-title
          v-loading="snapshotsLoading"
          :data="snapshotRows"
          stripe
          row-key="id"
          :header-cell-style="TABLE_HEADER_STYLE"
          class="hfl-list-table backup-data-snapshot-table"
        >
          <ElTableColumn
            :label="t('protection.backupDetail.colSnapId')"
            width="200"
            fixed
          >
            <template #default="{ row }">
              <button
                type="button"
                class="hfl-table-name-link hfl-table-cell-mono hfl-table-name-link--single"
                @click.stop="openSnapshotDrawer(row)"
              >
                {{ row.snapshot_uid || `#${row.id}` }}
              </button>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('protection.backupDetail.labelStatus')"
            width="110"
          >
            <template #default="{ row }">
              <ElTag
                size="small"
                class="snapshot-status-tag"
                v-bind="lifecycleStatusTagAttrs(row.status)"
              >
                <LoaderCircle
                  v-if="snapshotStatusInProgress(row.status)"
                  :size="12"
                  class="snapshot-status-tag__spinner"
                  aria-hidden="true"
                />
                {{ snapshotStatusLabel(row.status) }}
              </ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('protection.backupDetail.colSnapStart')"
            width="160"
          >
            <template #default="{ row }">
              <span
                class="hfl-table-cell-time"
                :class="{ 'hfl-empty-mark': !(row.started_at || row.created_at) }"
              >{{ formatNullableTime(row.started_at || row.created_at) }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('protection.backupDetail.colSnapEnd')"
            width="160"
          >
            <template #default="{ row }">
              <span
                class="hfl-table-cell-time"
                :class="{ 'hfl-empty-mark': !row.finished_at }"
              >{{ formatNullableTime(row.finished_at) }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('protection.backupsPage.snapshotNewStorage')"
            width="140"
            align="right"
            label-class-name="hfl-table-no-tooltip"
          >
            <template #header>
              <span class="snapshot-point-table-header-with-tip">
                <span>{{ t('protection.backupsPage.snapshotNewStorage') }}</span>
                <HflHelpTip
                  :content="t('protection.backupsPage.snapshotNewStorageHint')"
                  :aria-label="t('protection.backupsPage.snapshotNewStorageHint')"
                  :size="13"
                  popper-class="snapshot-metric-help-popper"
                />
              </span>
            </template>
            <template #default="{ row }">
              {{ fmtReferenceBytes(row.new_packed_content_bytes) }}
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('protection.backupsPage.snapshotRecoverableData')"
            width="135"
            align="right"
            label-class-name="hfl-table-no-tooltip"
          >
            <template #header>
              <span class="snapshot-point-table-header-with-tip">
                <span>{{ t('protection.backupsPage.snapshotRecoverableData') }}</span>
                <HflHelpTip
                  :content="t('protection.backupsPage.snapshotRecoverableDataHint')"
                  :aria-label="t('protection.backupsPage.snapshotRecoverableDataHint')"
                  :size="13"
                  popper-class="snapshot-metric-help-popper"
                />
              </span>
            </template>
            <template #default="{ row }">
              {{ fmtBytes(snapshotDisplaySize(row)) }}
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('protection.backupsPage.snapshotBrowserFileDirCount')"
            min-width="140"
            align="right"
          >
            <template #default="{ row }">
              {{ snapshotDisplayFileCount(row) }}/{{ snapshotDisplayDirCount(row) }}
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('protection.backupsPage.flowBackupColBackupDirs')"
            min-width="360"
            class-name="hfl-table-no-tooltip"
          >
            <template #default="{ row }">
              <HflPopover
                v-if="snapshotBackupPaths(row).length"
                placement="right-start"
                trigger="hover"
                :hide-after="BACKUP_PATH_POPOVER_HIDE_AFTER_MS"
                :width="400"
                append-to-body
              >
                <template #reference>
                  <div class="snapshot-backup-path-preview">
                    <span class="snapshot-backup-path-preview__path">
                      {{ snapshotBackupPaths(row)[0] }}
                    </span>
                    <span
                      v-if="snapshotBackupPaths(row).length > 1"
                      class="snapshot-backup-path-preview__more"
                      aria-hidden="true"
                    >
                      <MoreHorizontal :size="16" />
                    </span>
                  </div>
                </template>
                <ul class="snapshot-backup-path-popover">
                  <li
                    v-for="path in snapshotBackupPaths(row)"
                    :key="`${row.id}-${path}`"
                  >
                    <code>{{ path }}</code>
                  </li>
                </ul>
              </HflPopover>
              <span
                v-else
                class="hfl-empty-mark"
              >{{ t('protection.backupDetail.durationDash') }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('protection.sourceResources.colActions')"
            width="195"
            fixed="right"
            align="center"
            class-name="hfl-table-actions-col hfl-table-no-tooltip"
            header-class-name="hfl-table-actions-col"
          >
            <template #default="{ row }">
              <div class="snapshot-point-actions">
                <ElTooltip
                  :disabled="canRestoreSnapshot(row)"
                  :content="snapshotRestoreDisabledReason(row)"
                  placement="top"
                >
                  <span class="snapshot-point-actions__tooltip-wrap">
                    <button
                      type="button"
                      class="snapshot-point-actions__button snapshot-point-actions__button--restore"
                      :disabled="!canRestoreSnapshot(row)"
                      @click.stop="openSnapshotRestore(row)"
                    >
                      <RotateCcw
                        :size="14"
                        class="snapshot-point-actions__icon"
                        aria-hidden="true"
                      />
                      <span>{{ t('protection.backupsPage.snapshotRecoverAction') }}</span>
                    </button>
                  </span>
                </ElTooltip>
                <button
                  type="button"
                  class="snapshot-point-actions__button snapshot-point-actions__button--browse"
                  @click.stop="openSnapshotDrawer(row)"
                >
                  <FolderOpen
                    :size="14"
                    class="snapshot-point-actions__icon"
                    aria-hidden="true"
                  />
                  <span>{{ t('protection.backupsPage.snapshotBrowserBrowse') }}</span>
                </button>
              </div>
            </template>
          </ElTableColumn>
        </ElTable>

        <ElEmpty
          v-else
          :description="t('protection.backupDetail.emptySnapshots')"
          :image-size="72"
        />

        <div
          v-if="snapshotPagination.count > 0"
          class="hfl-list-footer"
        >
          <HflPagination
            v-model:current-page="snapshotPagination.page"
            v-model:page-size="snapshotPagination.pageSize"
            class="hfl-list-footer__pagination"
            layout="total, sizes, prev, pager, next"
            :total="snapshotPagination.count"
            :page-sizes="SNAPSHOT_PAGE_SIZE_OPTIONS"
            @size-change="snapshotPagination.page = 1"
          />
        </div>
      </section>
    </div>
  </ModulePage>

  <ElDrawer
    v-model="snapshotDrawerOpen"
    direction="rtl"
    destroy-on-close
    :modal="true"
    :size="drawerSize"
    :z-index="3000"
    class="hfl-detail-drawer backup-data-snapshot-drawer"
    @closed="onSnapshotDrawerClosed"
  >
    <template #header>
      <div class="backup-data-snapshot-drawer__header">
        <div class="backup-data-snapshot-drawer__heading">
          <h2>{{ snapshotIdLabel(activeSnapshot) }}</h2>
        </div>
        <div class="backup-data-snapshot-drawer__meta">
          <ElTag
            v-if="activeSnapshot"
            size="small"
            class="snapshot-status-tag"
            v-bind="lifecycleStatusTagAttrs(activeSnapshot.status)"
          >
            <LoaderCircle
              v-if="snapshotStatusInProgress(activeSnapshot.status)"
              :size="12"
              class="snapshot-status-tag__spinner"
              aria-hidden="true"
            />
            {{ snapshotStatusLabel(activeSnapshot.status) }}
          </ElTag>
          <span v-if="activeSnapshot">
            {{ formatNullableTime(activeSnapshot.finished_at || activeSnapshot.started_at || activeSnapshot.created_at) }}
          </span>
        </div>
        <button
          type="button"
          class="backup-data-snapshot-drawer__refresh"
          :title="t('common.refresh')"
          :aria-label="t('common.refresh')"
          :disabled="snapshotDetailLoading || !activeSnapshotId"
          @click="loadSnapshotDetail(activeSnapshotId, { preserveBrowsers: true })"
        >
          <RefreshCw
            :size="18"
            :class="{ 'snapshot-status-tag__spinner': snapshotDetailLoading }"
          />
        </button>
      </div>
    </template>

    <div class="backup-data-snapshot-drawer__body">
      <section class="backup-data-drawer-section backup-data-drawer-section--overview">
        <div class="backup-data-drawer-section__title">
          {{ t('protection.backupsPage.snapshotStorageEfficiencyTitle') }}
        </div>
        <div
          v-loading="snapshotDetailLoading && !activeSnapshot"
          class="backup-data-drawer-section__scroll"
        >
          <ElAlert
            v-if="snapshotDetailError && !activeSnapshot"
            :title="snapshotDetailError"
            type="error"
            show-icon
            :closable="false"
          />
          <dl
            v-else-if="activeSnapshot"
            class="backup-data-snapshot-metrics"
          >
            <div class="backup-data-snapshot-metrics__item">
              <dt>
                <span class="backup-data-snapshot-metrics__label">{{ t('protection.backupsPage.snapshotRecoverableData') }}</span>
                <HflHelpTip
                  :content="t('protection.backupsPage.snapshotRecoverableDataHint')"
                  :aria-label="t('protection.backupsPage.snapshotRecoverableDataHint')"
                  :size="13"
                  popper-class="snapshot-metric-help-popper"
                />
              </dt>
              <dd>{{ fmtBytes(snapshotDisplaySize(activeSnapshot)) }}</dd>
            </div>
            <div class="backup-data-snapshot-metrics__item">
              <dt>
                <span class="backup-data-snapshot-metrics__label">{{ t('protection.backupsPage.snapshotNewOriginalData') }}</span>
                <HflHelpTip
                  :content="t('protection.backupsPage.snapshotNewOriginalDataHint')"
                  :aria-label="t('protection.backupsPage.snapshotNewOriginalDataHint')"
                  :size="13"
                  popper-class="snapshot-metric-help-popper"
                />
              </dt>
              <dd>{{ fmtReferenceBytes(activeSnapshot.new_original_content_bytes) }}</dd>
            </div>
            <div class="backup-data-snapshot-metrics__item">
              <dt>
                <span class="backup-data-snapshot-metrics__label">{{ t('protection.backupsPage.snapshotNewStorage') }}</span>
                <HflHelpTip
                  :content="t('protection.backupsPage.snapshotNewStorageHint')"
                  :aria-label="t('protection.backupsPage.snapshotNewStorageHint')"
                  :size="13"
                  popper-class="snapshot-metric-help-popper"
                />
              </dt>
              <dd>{{ fmtReferenceBytes(activeSnapshot.new_packed_content_bytes) }}</dd>
            </div>
            <div class="backup-data-snapshot-metrics__item">
              <dt>
                <span class="backup-data-snapshot-metrics__label">{{ t('protection.backupsPage.snapshotDataReuse') }}</span>
                <HflHelpTip
                  :content="t('protection.backupsPage.snapshotDataReuseHint')"
                  :aria-label="t('protection.backupsPage.snapshotDataReuseHint')"
                  :size="13"
                  popper-class="snapshot-metric-help-popper"
                />
              </dt>
              <dd>{{ fmtReferencePercent(activeSnapshot.data_reuse_ratio) }}</dd>
            </div>
            <div class="backup-data-snapshot-metrics__item">
              <dt>
                <span class="backup-data-snapshot-metrics__label">{{ t('protection.backupsPage.snapshotCompressionSavings') }}</span>
                <HflHelpTip
                  :content="t('protection.backupsPage.snapshotCompressionSavingsHint')"
                  :aria-label="t('protection.backupsPage.snapshotCompressionSavingsHint')"
                  :size="13"
                  popper-class="snapshot-metric-help-popper"
                />
              </dt>
              <dd>{{ fmtReferencePercent(activeSnapshot.compression_savings_ratio) }}</dd>
            </div>
            <div class="backup-data-snapshot-metrics__item">
              <dt>
                <span class="backup-data-snapshot-metrics__label">{{ t('protection.backupsPage.snapshotCombinedReduction') }}</span>
                <HflHelpTip
                  :content="t('protection.backupsPage.snapshotCombinedReductionHint')"
                  :aria-label="t('protection.backupsPage.snapshotCombinedReductionHint')"
                  :size="13"
                  popper-class="snapshot-metric-help-popper"
                />
              </dt>
              <dd>{{ fmtCombinedReduction(activeSnapshot) }}</dd>
            </div>
          </dl>
        </div>
      </section>

      <section class="backup-data-drawer-section backup-data-drawer-section--contents">
        <div class="backup-data-drawer-section__title backup-data-browser-files__title">
          <span class="backup-data-browser-files__title-label">
            {{ t('protection.backupsPage.snapshotBrowserPreviewTitle') }}
          </span>
          <div
            v-if="selectedSnapshotDirectories.length"
            class="backup-data-browser-files__actions"
          >
            <span v-if="activeSnapshotDownloadLimits">
              {{ t('protection.backupsPage.snapshotBrowserSelectedLimitCount', {
                selected: selectedCount,
                limit: activeSnapshotDownloadLimits.maxItems,
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
              v-if="activeSnapshotDownloadLimits"
              :max-items="activeSnapshotDownloadLimits.maxItems"
              :max-size-bytes="activeSnapshotDownloadLimits.maxSizeBytes"
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
          v-if="snapshotDetailError"
          :title="snapshotDetailError"
          type="error"
          show-icon
          :closable="false"
          class="backup-data-browser-files__error"
        >
          <template #default>
            <ElButton
              size="small"
              @click="loadSnapshotDetail(activeSnapshotId)"
            >
              <RefreshCw :size="14" />
              {{ t('common.retry') }}
            </ElButton>
          </template>
        </ElAlert>

        <div
          v-if="selectedSnapshotDirectories.length"
          class="backup-data-browser-source-tree"
        >
          <div class="backup-data-browser-source-tree__header">
            <span class="backup-data-browser-source-tree__header-path">
              {{ t('protection.backupDetail.colBackupDir') }}
            </span>
            <span class="backup-data-browser-source-tree__header-metric">
              {{ t('protection.backupsPage.snapshotRecoverableData') }}
            </span>
            <span class="backup-data-browser-source-tree__header-metric">
              {{ t('protection.backupsPage.snapshotBrowserFileDirCount') }}
            </span>
            <span class="backup-data-browser-source-tree__header-status">
              {{ t('protection.backupDetail.labelStatus') }}
            </span>
            <span class="backup-data-browser-source-tree__header-selected">
              {{ t('protection.backupsPage.snapshotBrowserSelected') }}
            </span>
          </div>

          <div
            v-for="directory in selectedSnapshotDirectories"
            :key="directory.id"
            class="backup-data-browser-source-tree__group"
            :class="{
              'is-expanded': directoryBrowserState(directory).expanded,
              'is-disabled': !canBrowseDirectory(directory),
            }"
          >
            <div class="backup-data-browser-source-tree__root">
              <div class="backup-data-browser-source-tree__root-main">
                <button
                  type="button"
                  class="backup-data-browser-source-tree__toggle"
                  :disabled="!canBrowseDirectory(directory)"
                  :aria-label="directory.source_path"
                  :aria-expanded="directoryBrowserState(directory).expanded"
                  @click="browseSnapshotDirectory(directory)"
                >
                  <ChevronRight
                    :size="16"
                    class="backup-data-browser-source-tree__chevron"
                    :class="{ 'is-expanded': directoryBrowserState(directory).expanded }"
                    aria-hidden="true"
                  />
                </button>
                <ElCheckbox
                  class="backup-data-browser-source-tree__root-checkbox"
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
                    ? 'backup-data-browser-table__file-icon'
                    : 'backup-data-browser-table__folder-icon'"
                />
                <button
                  type="button"
                  class="backup-data-browser-source-tree__path"
                  :disabled="!canBrowseDirectory(directory)"
                  :aria-expanded="directoryBrowserState(directory).expanded"
                  @click="browseSnapshotDirectory(directory)"
                >
                  <code :title="directory.source_path">{{ directory.source_path }}</code>
                </button>
              </div>
              <div class="backup-data-browser-source-tree__metric">
                {{ fmtBytes(Number(directory.recoverable_size_bytes ?? directory.size_bytes ?? 0)) }}
              </div>
              <div class="backup-data-browser-source-tree__metric">
                {{ directory.file_count }}/{{ directory.dir_count }}
              </div>
              <div class="backup-data-browser-source-tree__status">
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
              <div class="backup-data-browser-source-tree__selected">
                <span>{{ directorySelectedCount(directory) }}</span>
              </div>
            </div>

            <div
              v-if="directoryBrowserState(directory).expanded"
              class="backup-data-browser-source-tree__children"
            >
              <ElAlert
                v-if="directoryBrowserState(directory).error"
                :title="directoryBrowserState(directory).error"
                type="error"
                show-icon
                :closable="false"
                class="backup-data-browser-source-tree__error"
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
                class="backup-data-browser-table"
              >
                <div class="backup-data-browser-table__header">
                  <span>{{ t('protection.backupsPage.snapshotBrowserName') }}</span>
                  <span>{{ t('protection.backupsPage.snapshotBrowserPath') }}</span>
                  <span>{{ t('protection.backupsPage.snapshotBrowserSize') }}</span>
                  <span>{{ t('protection.backupsPage.snapshotBrowserModified') }}</span>
                </div>

                <div
                  v-if="directory.path_type === 'file'"
                  class="backup-data-browser-table__row"
                >
                  <ElCheckbox
                    :model-value="directoryBrowserState(directory).fileChecked"
                    @change="onSnapshotFileCheckChange(directory, $event)"
                  />
                  <span class="backup-data-browser-table__name">
                    <File
                      :size="16"
                      class="backup-data-browser-table__file-icon"
                    />
                    <span class="truncate">{{ snapshotFileFallbackName(directory) }}</span>
                  </span>
                  <span class="backup-data-browser-table__path">{{ directory.source_path }}</span>
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
                  class="backup-data-browser-tree"
                  empty-text=" "
                  @check-change="(data, checked) => onBrowserTreeCheckChange(directory, data, checked)"
                >
                  <template #default="{ data }">
                    <div
                      v-if="data.loadMore"
                      class="backup-data-browser-tree__load-more"
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
                      class="backup-data-browser-tree__row"
                    >
                      <span class="backup-data-browser-table__name">
                        <Folder
                          v-if="data.type === 'dir'"
                          :size="16"
                          class="backup-data-browser-table__folder-icon"
                        />
                        <File
                          v-else
                          :size="16"
                          class="backup-data-browser-table__file-icon"
                        />
                        <span class="truncate">{{ data.name }}</span>
                      </span>
                      <span class="backup-data-browser-table__path">{{ data.path }}</span>
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
                  class="backup-data-browser-table__empty"
                />
              </div>
            </div>
          </div>
        </div>

        <ElEmpty
          v-else
          v-loading="snapshotDetailLoading"
          :description="snapshotDetailLoading
            ? t('common.loading')
            : t('protection.backupsPage.snapshotBrowserEmptyDirectories')"
          :image-size="48"
          class="backup-data-browser-files__empty"
        />

        <footer
          v-if="selectedSnapshotDirectories.length"
          class="backup-data-browser-files__footer"
        >
          <span>{{ t('protection.backupsPage.snapshotBrowserSelectedCount', { n: selectedCount }) }}</span>
          <span>{{ t('protection.backupsPage.snapshotBrowserFooterHint') }}</span>
        </footer>
      </section>
    </div>
  </ElDrawer>
</template>

<style scoped>
.snapshot-status-tag {
  flex-shrink: 0;
  white-space: nowrap;
}

.snapshot-status-tag :deep(.el-tag__content) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.snapshot-status-tag__spinner {
  flex: 0 0 auto;
  animation: snapshot-status-spin 0.8s linear infinite;
  transform-origin: center;
}

@keyframes snapshot-status-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .snapshot-status-tag__spinner {
    animation: none;
  }
}

.backup-data-browser-page {
  display: flex;
  min-height: 0;
  flex-direction: column;
  gap: 14px;
}

.backup-data-browser-header {
  width: 100%;
  margin: 0;
}

.backup-data-source-summary {
  display: flex;
  min-height: 76px;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border: 1px solid rgb(226 232 240);
  border-radius: 10px;
  background: #fff;
}

.backup-data-source-summary__icon {
  display: grid;
  width: 42px;
  height: 42px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 10px;
  background: rgb(238 242 255);
  color: rgb(79 70 229);
}

.backup-data-source-summary__main {
  min-width: 0;
  flex: 1;
}

.backup-data-source-summary__main h2 {
  overflow: hidden;
  margin: 0;
  color: rgb(15 23 42);
  font-size: 16px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.backup-data-source-summary__main span {
  display: block;
  overflow: hidden;
  margin-top: 4px;
  color: rgb(100 116 139);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.backup-data-snapshot-list {
  min-width: 0;
}

.backup-data-drawer-section__title {
  position: relative;
  display: flex;
  min-height: 42px;
  align-items: center;
  padding: 9px 16px 9px 20px;
  border-bottom: 1px solid rgb(226 232 240);
  background: rgb(255 255 255);
  color: rgb(30 41 59);
  font-size: 13px;
  font-weight: 650;
}

.backup-data-drawer-section__title::before {
  position: absolute;
  top: 11px;
  bottom: 11px;
  left: 9px;
  width: 3px;
  border-radius: 999px;
  background: var(--color-primary);
  content: '';
}

.backup-data-snapshot-toolbar {
  margin-bottom: 16px;
}

.backup-data-snapshot-toolbar__search {
  width: min(300px, 100%);
}

.backup-data-snapshot-toolbar__status {
  width: 130px;
}

.backup-data-snapshot-toolbar__time {
  width: 150px;
}

.backup-data-snapshot-toolbar__range {
  width: 360px;
}

.backup-data-snapshot-table {
  width: 100%;
}

.snapshot-point-table-header-with-tip {
  display: inline-flex;
  width: 100%;
  min-width: 0;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
}

.snapshot-backup-path-preview {
  display: inline-flex;
  width: fit-content;
  max-width: 100%;
  align-items: center;
  color: var(--el-text-color-secondary, #909399);
  font-size: 12px;
  line-height: 18px;
}

.snapshot-backup-path-preview__path {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.snapshot-backup-path-preview__more {
  display: inline-flex;
  width: 18px;
  height: 18px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  margin-left: 8px;
  border-radius: 999px;
  background: rgb(241 245 249);
  color: rgb(100 116 139);
  box-shadow: 0 0 0 1px rgba(226, 232, 240, 0.9);
  pointer-events: none;
}

.snapshot-backup-path-popover {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 260px;
  padding: 0;
  margin: 0;
  overflow: auto;
  list-style: none;
}

.snapshot-backup-path-popover code {
  color: rgb(30 41 59);
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.snapshot-point-actions {
  display: flex;
  box-sizing: border-box;
  width: 100%;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 3px;
  white-space: nowrap;
}

.snapshot-point-actions__button {
  appearance: button;
  display: inline-flex;
  box-sizing: border-box;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 3px 8px;
  margin: 0;
  border: 1px solid;
  border-radius: 6px;
  background: #fff;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  line-height: 16px;
  white-space: nowrap;
  box-shadow: 0 1px 2px 0 rgb(0 0 0 / 5%);
  cursor: pointer;
  transition: all 150ms cubic-bezier(0.4, 0, 0.2, 1);
}

.snapshot-point-actions__tooltip-wrap {
  display: inline-flex;
}

.snapshot-point-actions__icon {
  width: 14px;
  height: 14px;
  flex: 0 0 14px;
}

.snapshot-point-actions__button--restore,
.snapshot-point-actions__button--browse {
  border-color: oklch(87% 0.065 274.039);
  color: oklch(51.1% 0.262 276.966);
}

.snapshot-point-actions__button--restore .snapshot-point-actions__icon,
.snapshot-point-actions__button--browse .snapshot-point-actions__icon {
  color: oklch(58.5% 0.233 277.117);
}

.snapshot-point-actions__button--restore:not(:disabled):hover,
.snapshot-point-actions__button--browse:not(:disabled):hover {
  border-color: oklch(78.5% 0.115 274.713);
  background: oklch(96.2% 0.018 272.314);
}

.snapshot-point-actions__button:disabled {
  border-color: oklch(92.9% 0.013 255.508);
  background: oklch(98.4% 0.003 247.858);
  color: oklch(70.4% 0.04 256.788);
  opacity: 0.7;
  box-shadow: none;
  cursor: not-allowed;
}

.snapshot-point-actions__button:disabled .snapshot-point-actions__icon {
  color: oklch(70.4% 0.04 256.788);
}

.snapshot-point-actions__button:focus-visible {
  outline: 2px solid rgba(99, 102, 241, 0.28);
  outline-offset: 2px;
}

.backup-data-snapshot-drawer__header {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  justify-content: flex-start;
  gap: 16px;
}

.backup-data-snapshot-drawer__heading {
  min-width: 0;
  flex: 0 1 auto;
}

.backup-data-snapshot-drawer__refresh {
  display: inline-flex;
  flex: 0 0 32px;
  align-items: center;
  justify-content: center;
  height: 32px;
  margin-left: auto;
  border: 0;
  border-radius: 6px;
  color: rgb(71 85 105);
  background: transparent;
  cursor: pointer;
}

.backup-data-snapshot-drawer__refresh:hover:not(:disabled) {
  background: rgb(241 245 249);
  color: rgb(15 23 42);
}

.backup-data-snapshot-drawer__refresh:disabled {
  color: rgb(148 163 184);
  cursor: not-allowed;
}

.backup-data-snapshot-drawer__heading h2 {
  overflow: hidden;
  margin: 0;
  color: rgb(15 23 42);
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace);
  font-size: 16px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.backup-data-snapshot-drawer__meta {
  display: flex;
  flex: 0 0 auto;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  color: rgb(100 116 139);
  font-size: 12px;
}

:deep(.backup-data-snapshot-drawer .el-drawer__body) {
  min-height: 0;
  overflow: hidden;
  padding: 12px 16px 16px;
}

.backup-data-snapshot-drawer__body {
  display: grid;
  width: 100%;
  height: 100%;
  min-height: 0;
  grid-template-rows: minmax(112px, 0.58fr) minmax(0, 3.53fr);
  gap: 10px;
  overflow: hidden;
}

.backup-data-drawer-section {
  display: grid;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 1px solid rgb(226 232 240);
  border-radius: 9px;
  background: #fff;
}

.backup-data-drawer-section--overview {
  grid-template-rows: auto minmax(0, 1fr);
}

.backup-data-drawer-section--contents {
  grid-template-rows: auto auto minmax(0, 1fr) auto;
}

.backup-data-drawer-section--contents > .backup-data-drawer-section__title {
  grid-row: 1;
}

.backup-data-drawer-section--contents > .backup-data-browser-files__error {
  grid-row: 2;
}

.backup-data-drawer-section--contents > .backup-data-browser-source-tree,
.backup-data-drawer-section--contents > .backup-data-browser-files__empty {
  grid-row: 3;
}

.backup-data-drawer-section--contents > .backup-data-browser-files__footer {
  grid-row: 4;
}

.backup-data-drawer-section__scroll {
  min-height: 0;
  overflow: auto;
  padding: 10px 14px;
}

.backup-data-browser-files__error {
  margin: 8px 10px;
}

.backup-data-snapshot-metrics {
  display: grid;
  min-width: 760px;
  grid-template-columns: repeat(6, minmax(120px, 1fr));
  gap: 8px;
  margin: 0;
}

.backup-data-snapshot-metrics__item {
  min-width: 0;
  padding: 7px 10px;
  border-radius: 7px;
  background: rgb(248 250 252);
}

.backup-data-snapshot-metrics__item dt {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 5px;
  color: rgb(100 116 139);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.backup-data-snapshot-metrics__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.backup-data-snapshot-metrics__item dt :deep(.hfl-help-tip) {
  flex: 0 0 18px;
}

.backup-data-snapshot-metrics__item dd {
  overflow: hidden;
  margin: 4px 0 0;
  color: rgb(30 41 59);
  font-size: 14px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:global(.snapshot-metric-help-popper.el-popper),
:global(.snapshot-source-path-help-popper.el-popper) {
  box-sizing: border-box;
  width: max-content;
  max-width: min(320px, calc(100vw - 32px)) !important;
  z-index: 3600 !important;
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

.backup-data-browser-files__title {
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px 16px;
  background: rgb(255 255 255);
}

.backup-data-browser-files__title-label {
  min-width: 180px;
}

.backup-data-browser-files__actions,
.backup-data-browser-table__name {
  display: inline-flex;
  min-width: 0;
  align-items: center;
}

.backup-data-browser-source-tree {
  min-width: 0;
  min-height: 0;
  overflow: auto;
  background: rgb(255 255 255);
}

.backup-data-browser-source-tree__header,
.backup-data-browser-source-tree__root {
  display: grid;
  box-sizing: border-box;
  min-width: 780px;
  grid-template-columns:
    minmax(360px, 3fr)
    minmax(92px, 0.55fr)
    minmax(118px, 0.7fr)
    104px
    72px;
  align-items: center;
}

.backup-data-browser-source-tree__header {
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

.backup-data-browser-source-tree__header > span {
  padding: 0 14px;
}

.backup-data-browser-source-tree__header-path {
  padding-left: 84px !important;
}

.backup-data-browser-source-tree__header-metric {
  text-align: right;
}

.backup-data-browser-source-tree__header-status,
.backup-data-browser-source-tree__header-selected {
  text-align: center;
}

.backup-data-browser-source-tree__group {
  min-width: 780px;
  border-bottom: 1px solid rgb(226 232 240);
}

.backup-data-browser-source-tree__group.is-expanded {
  box-shadow: inset 3px 0 0 var(--color-primary);
}

.backup-data-browser-source-tree__root {
  min-height: 44px;
  background: rgb(255 255 255);
  transition: background-color 150ms ease;
}

.backup-data-browser-source-tree__group:not(.is-disabled):not(.is-expanded)
  .backup-data-browser-source-tree__root:hover {
  background: rgb(250 250 252);
}

.backup-data-browser-source-tree__group.is-expanded .backup-data-browser-source-tree__root {
  background: rgb(245 243 255);
}

.backup-data-browser-source-tree__group.is-expanded .backup-data-browser-source-tree__chevron {
  color: var(--color-primary);
}

.backup-data-browser-source-tree__root-main {
  display: grid;
  min-height: 43px;
  min-width: 0;
  grid-template-columns: 20px 18px 18px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  color: rgb(30 41 59);
}

.backup-data-browser-source-tree__toggle,
.backup-data-browser-source-tree__path {
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.backup-data-browser-source-tree__toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.backup-data-browser-source-tree__path {
  text-align: left;
}

.backup-data-browser-source-tree__toggle:focus-visible,
.backup-data-browser-source-tree__path:focus-visible {
  outline: 2px solid rgb(99 102 241 / 32%);
  outline-offset: 2px;
}

.backup-data-browser-source-tree__path code {
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

.backup-data-browser-source-tree__chevron {
  color: rgb(100 116 139);
  transition: transform 150ms ease;
}

.backup-data-browser-source-tree__chevron.is-expanded {
  transform: rotate(90deg);
}

.backup-data-browser-source-tree__status {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 8px;
}

.backup-data-browser-source-tree__metric {
  padding: 0 14px;
  color: rgb(71 85 105);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  text-align: right;
  white-space: nowrap;
}

.backup-data-browser-source-tree__selected {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 8px;
  color: rgb(71 85 105);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.backup-data-browser-source-tree__selected span {
  display: inline-flex;
  min-width: 24px;
  justify-content: center;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgb(238 242 255);
  color: rgb(79 70 229);
  font-weight: 650;
}

.backup-data-browser-source-tree__status :deep(.hfl-help-tip) {
  flex: 0 0 18px;
}

.backup-data-browser-source-tree__group.is-disabled .backup-data-browser-source-tree__root {
  background: rgb(248 250 252 / 72%);
}

.backup-data-browser-source-tree__group.is-disabled .backup-data-browser-source-tree__root-main {
  color: rgb(148 163 184);
}

.backup-data-browser-source-tree__group.is-disabled .backup-data-browser-source-tree__toggle,
.backup-data-browser-source-tree__group.is-disabled .backup-data-browser-source-tree__path {
  cursor: not-allowed;
}

.backup-data-browser-source-tree__group.is-disabled .backup-data-browser-table__folder-icon,
.backup-data-browser-source-tree__group.is-disabled .backup-data-browser-table__file-icon,
.backup-data-browser-source-tree__group.is-disabled .backup-data-browser-source-tree__chevron {
  color: rgb(148 163 184);
}

.backup-data-browser-source-tree__children {
  margin-left: 28px;
  border-top: 1px solid rgb(221 214 254);
  border-left: 1px solid rgb(226 232 240);
  background: rgb(255 255 255);
}

.backup-data-browser-source-tree__error {
  margin: 8px 10px;
}

.backup-data-browser-source-tree__children .backup-data-browser-table {
  overflow: visible;
}

.backup-data-browser-files__actions {
  flex: 0 0 auto;
  gap: 10px;
  color: rgb(100 116 139);
  font-size: 11px;
  font-weight: 400;
  white-space: nowrap;
}

.backup-data-browser-files__actions :deep(.el-button) {
  display: inline-flex;
  gap: 6px;
}

.backup-data-browser-table {
  position: relative;
  min-height: 0;
  overflow: auto;
}

.backup-data-browser-table__header {
  display: grid;
  grid-template-columns: minmax(180px, 1.35fr) minmax(180px, 1fr) 100px 160px;
  align-items: center;
  min-width: 750px;
  padding: 0 14px 0 66px;
}

.backup-data-browser-table__header {
  position: sticky;
  z-index: 1;
  top: 0;
  min-height: 34px;
  border-bottom: 1px solid rgb(226 232 240);
  background: rgb(248 250 252);
  color: rgb(100 116 139);
  font-size: 11px;
  font-weight: 600;
}

.backup-data-browser-source-tree__children .backup-data-browser-table__header {
  background: rgb(255 255 255);
  color: rgb(100 116 139);
  font-weight: 500;
}

.backup-data-browser-table__row {
  display: grid;
  grid-template-columns: 38px minmax(180px, 1.35fr) minmax(180px, 1fr) 100px 160px;
  align-items: center;
  min-width: 750px;
  min-height: 38px;
  padding: 0 14px;
  border-bottom: 1px solid rgb(241 245 249);
  color: rgb(51 65 85);
  font-size: 12px;
}

.backup-data-browser-table__row:hover {
  background: rgb(248 250 252);
}

.backup-data-browser-table__row > :nth-child(4),
.backup-data-browser-table__row > :nth-child(5) {
  color: rgb(71 85 105);
  white-space: nowrap;
}

.backup-data-browser-table__name {
  gap: 7px;
  padding-right: 12px;
}

.backup-data-browser-table__path {
  overflow: hidden;
  padding-right: 12px;
  color: rgb(100 116 139);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.backup-data-browser-table__folder-icon {
  flex: 0 0 auto;
  color: rgb(217 119 6);
}

.backup-data-browser-table__file-icon {
  flex: 0 0 auto;
  color: rgb(59 130 246);
}

.backup-data-browser-tree {
  min-width: 750px;
  padding: 2px 0;
  background: transparent;
}

.backup-data-browser-tree :deep(.el-tree-node__content) {
  height: 38px;
  border-bottom: 1px solid rgb(241 245 249);
}

.backup-data-browser-tree :deep(.el-tree-node__content:hover) {
  background: rgb(248 250 252);
}

.backup-data-browser-tree__row {
  display: grid;
  width: 100%;
  min-width: 0;
  flex: 1;
  grid-template-columns: minmax(180px, 1.35fr) minmax(180px, 1fr) 100px 160px;
  align-items: center;
  gap: 0;
  padding-right: 14px;
  color: rgb(51 65 85);
  font-size: 12px;
}

.backup-data-browser-tree__row > :nth-child(3),
.backup-data-browser-tree__row > :nth-child(4) {
  color: rgb(71 85 105);
  white-space: nowrap;
}

.backup-data-browser-tree__load-more {
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

.backup-data-browser-tree :deep(.el-tree-node__content:has(.backup-data-browser-tree__load-more) > .el-checkbox) {
  visibility: hidden;
}

.backup-data-browser-table__empty,
.backup-data-browser-files__empty {
  display: grid;
  height: 100%;
  min-height: 0;
  place-items: center;
}

.backup-data-browser-files__footer {
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

@media (max-width: 900px) {
  .backup-data-snapshot-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .backup-data-snapshot-toolbar__search,
  .backup-data-snapshot-toolbar__status,
  .backup-data-snapshot-toolbar__time,
  .backup-data-snapshot-toolbar__range {
    width: 100%;
  }

  .backup-data-snapshot-drawer__header,
  .backup-data-browser-files__footer {
    align-items: flex-start;
    flex-direction: column;
  }

  .backup-data-snapshot-drawer__meta,
  .backup-data-browser-files__actions {
    width: 100%;
    justify-content: flex-start;
  }

  .backup-data-browser-files__actions {
    flex-wrap: wrap;
  }

}

@media (max-height: 680px) {
  .backup-data-snapshot-drawer__body {
    grid-template-rows: minmax(104px, 0.52fr) minmax(0, 3.26fr);
  }
}
</style>
