<script setup lang="ts">
import { computed, nextTick, onUnmounted, reactive, ref, toRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import {
  Archive,
  ArrowLeft,
  Camera,
  Check,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleStop,
  CircleOff,
  Clock3,
  Copy,
  Download,
  File,
  Filter,
  Folder,
  FolderOpen,
  Globe,
  Info,
  Link2,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Scale,
  Search,
  ShieldAlert,
  ShieldCheck,
  Unlink,
  X,
} from 'lucide-vue-next'
import { ElMessage, ElMessageBox } from 'element-plus'
import { pushToast } from '../../../lib/toast/store'
import type { ElTree } from 'element-plus'
import HflPopover from '../../../components/HflPopover.vue'
import HflPagination from '../../../components/HflPagination.vue'
import { useDrawerTableMaxHeight } from '../../../composables/useDrawerTableMaxHeight'
import { apiErrorMessage } from '../../../lib/api'
import { copyTextToClipboard } from '../../../lib/clipboard'
import { getNode } from '../../../lib/nodeApi'
import type {
  BackupSnapshotBrowserEntry,
  BackupConfig,
  BackupConfigDirectory,
  BackupConfigDetail,
  BackupConfigRecoveryPlan,
  BackupSourceSnapshot,
  BackupSourceSnapshotDirectory,
} from '../../../lib/protectionBackupConfigApi'
import {
  browseBackupSnapshotDirectory,
  createBackupSnapshotDirectoryDownloadTask,
  createBackupSnapshotDirectoryBatchDownloadTask,
  createSnapshotArtifactDownloadUrl,
  deleteBackupConfig,
  getBackupSourceSnapshot,
  listBackupSourceSnapshots,
  retryBackupConfigProvision,
} from '../../../lib/protectionBackupConfigApi'
import {
  applyFilterIgnorePatternsToForm,
  applyLegacyFileFilterCacheFlag,
  backupPolicyToForm,
  compileFilterIgnorePatterns,
  createEmptyFileFilterForm,
  type FileFilterRuleForm,
  type MessageLocale,
} from '../../../lib/protectionPolicyFormModel'
import type { BackupPolicy, FileFilterRule } from '../../../lib/protectionPolicyApi'
import { getStorageRepository, type StorageRepository } from '../../../lib/storageRepositoryApi'
import { lifecycleStatusTagAttrs } from '../../../lib/statusTag'
import {
  getSourceResource,
  listBackupSelectableSources,
  type BackupSelectableRepositoryPreview,
  type BackupSelectableSource,
} from '../../../lib/sourceApi'
import {
  cancelProtectionBackupTask,
  fetchBackupTaskRuntime,
} from '../../../lib/protectionBackupTaskApi'
import {
  buildStopConfirmItemFromTask,
} from '../../../lib/protectionStopConfirm'
import { useProtectionStopConfirmDialog } from '../../../composables/useProtectionStopConfirmDialog'
import { useDrawerScrollReset } from '../../../composables/useDrawerScrollReset'
import ProtectionStopConfirmDialog from '../../../components/ProtectionStopConfirmDialog.vue'
import TaskProgressCell from './TaskProgressCell.vue'
import ResourceNameSummaryCell from '../../../components/ResourceNameSummaryCell.vue'
import TaskStatusTag from '../../../components/TaskStatusTag.vue'
import TaskTypeLabel from '../../../components/TaskTypeLabel.vue'
import FlowSourceSummaryCell from './FlowSourceSummaryCell.vue'
import FlowSourceConnectionCell from './FlowSourceConnectionCell.vue'
import TaskEventFailureDetails from './TaskEventFailureDetails.vue'
import {
  restoreRecordPathMappings,
  restoreRecordRuntimeMetricParts,
  restoreRecordSnapshotLabel,
  restoreRecordTargetDisplayPath,
  restoreRecordTaskStatus,
  shouldShowRestoreRecordProgress,
} from './restoreRecordDisplay'
import { isSnapshotDirectoryBrowsable } from './snapshotBrowseEligibility'
import {
  formatTaskProgressBarPercent,
  formatTaskProgressPercent,
  isTransferProgress,
  type TaskRuntimePayload,
  type TransferProgress,
} from '../../../lib/kopiaProgress'
import type { TaskEventRow, TaskResourceRow, TaskRow } from '../../../lib/taskApi'
import { getTask, listTaskEvents, listTasks } from '../../../lib/taskApi'
import type { RestoreEndpointType, RestoreRecord, RestoreRecordItem, RestoreRecordSearchField } from '../../../lib/restoreApi'
import { listRestoreRecords, fetchRestoreRecordRuntime } from '../../../lib/restoreApi'
import { formatLocalDateTime } from '../../../lib/dateTime'
import { resolveTaskBackupSourceResource, resolveTaskBackupSourceResourceFromPayload } from '../../../lib/taskBackupSourceResource'
import { parseTaskStepStatusEvent, taskEventMessageKey, taskEventObjectText } from '../../../lib/taskEventDisplay'
import { hasExpandableTaskStep, hasExpandedTaskStep } from '../../../lib/taskStepExpansion'
import { taskResourceSnapshot } from '../../../lib/taskOutcomeDisplay'
import {
  useFlowSourceAggregate,
  type DemoFlowTask,
  type FlowSourceRow,
} from '../composables/useFlowSourceAggregate'
import { usePageRequestScope } from '../../../composables/usePageRequestScope'
import { useResponsiveDrawerWidth } from '../../../composables/useResponsiveDrawerWidth'
import { useListSearch } from '../../../composables/useListSearch'
import { isSnapshotRestorable } from '../lib/snapshotRestore'
import { refreshFlowSourceDetailTab } from './flowSourceDetailRefresh'

export type FlowSourceDetailTab = 'overview' | 'snapshots' | 'restoreRecords' | 'tasks'

export type FlowSourceDetailTaskSubTab = 'history' | 'executions' | 'restore'

export type FlowSourceDetailTabInput =
  | FlowSourceDetailTab
  | 'configs'
  | 'executions'
  | 'restore'

type SnapshotBrowserTreeNode = BackupSnapshotBrowserEntry & {
  id: string
  label: string
  disabled?: boolean
  loaded?: boolean
  isLeaf?: boolean
  children?: SnapshotBrowserTreeNode[]
}

type ResourceDetailRow = {
  key: string
  id: number
  name: string
  type: string
  status: string
  statusValue: string
  updatedAt: string
  summary: string
  backupSource?: string
  endpointName?: string
  endpointIp?: string
  registeredAt?: string
  availability?: 'online' | 'offline'
  flowSource?: Awaited<ReturnType<typeof resolveTaskBackupSourceResource>>['flowSource']
}

type RecoveryPlanMappingRow = {
  key: string
  plan: BackupConfigRecoveryPlan
  sourcePath: string
  sourcePathType?: 'directory' | 'file' | 'unknown'
}

type PolicyRetentionDetailLine = {
  label?: string
  text: string
}

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    source: FlowSourceRow | null
    sourceRows?: FlowSourceRow[]
    drawerSize?: string
    initialTab?: FlowSourceDetailTabInput
    initialTaskSubTab?: FlowSourceDetailTaskSubTab
    initialTaskUuid?: string
    initialRestoreRecordId?: number | null
    initialRestoreRecordTaskUuid?: string
    scrollTo?: 'dirs' | 'targets' | null
    backupFlowTasks: DemoFlowTask[]
    restoreFlowTasks: DemoFlowTask[]
    backupConfigs?: BackupConfig[]
    backupConfigDetails?: Map<number, BackupConfigDetail>
    repositories?: Map<number, StorageRepository>
    backupPolicies?: Map<number, BackupPolicy>
    fileFilters?: Map<number, FileFilterRule>
    backupSnapshots?: BackupSourceSnapshot[]
    backupTasks?: TaskRow[]
    restoreBlockedByRestore?: boolean
  }>(),
  {
    drawerSize: '720px',
    initialTab: 'overview',
    initialTaskSubTab: 'history',
    initialTaskUuid: '',
    initialRestoreRecordId: null,
    initialRestoreRecordTaskUuid: '',
    scrollTo: null,
    sourceRows: () => [],
    backupConfigs: () => [],
    backupConfigDetails: () => new Map<number, BackupConfigDetail>(),
    repositories: () => new Map<number, StorageRepository>(),
    backupPolicies: () => new Map<number, BackupPolicy>(),
    fileFilters: () => new Map<number, FileFilterRule>(),
    backupSnapshots: () => [],
    backupTasks: () => [],
    restoreBlockedByRestore: false,
  },
)

const requests = usePageRequestScope()
const { drawerSize: nestedDrawerSize } = useResponsiveDrawerWidth(2)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  closed: []
  'start-backup': [sourceId: string]
  recover: [sourceId: string]
  'restore-snapshot': [payload: { snapshotId: number }]
  'view-all-restore': [sourceId: string]
  'config-changed': []
}>()

const { t, te } = useI18n()
const router = useRouter()
const stopConfirmDialog = useProtectionStopConfirmDialog()
const stopConfirmOpen = stopConfirmDialog.open
const stopConfirmKind = stopConfirmDialog.kind
const stopConfirmItems = stopConfirmDialog.items
const messageLocale = computed<MessageLocale>(() => 'en')

const TABLE_HEADER_STYLE: Record<string, string> = {
  background: 'rgba(248, 250, 252, 0.96)',
  color: 'rgb(71 85 105)',
  fontWeight: '600',
  whiteSpace: 'nowrap',
}

const backupTasksRef = toRef(props, 'backupFlowTasks')
const restoreTasksRef = toRef(props, 'restoreFlowTasks')

const {
  fmtLocalTime,
  aggregateForSource,
} = useFlowSourceAggregate(backupTasksRef, restoreTasksRef)

const activeTab = ref<FlowSourceDetailTab>('overview')
const activeTaskSubTab = ref<FlowSourceDetailTaskSubTab>('history')
const dirsSectionRef = ref<HTMLElement | null>(null)
const targetsSectionRef = ref<HTMLElement | null>(null)
const { tableMaxHeight: snapshotTableMaxHeight, containerRef: snapshotTableRef } = useDrawerTableMaxHeight()
const { tableMaxHeight: restoreTableMaxHeight, containerRef: restoreTableRef } = useDrawerTableMaxHeight()
const { tableMaxHeight: sourceTasksTableMaxHeight, containerRef: sourceTasksTableRef } = useDrawerTableMaxHeight()
const { tableMaxHeight: taskResourceTableMaxHeight, containerRef: taskResourceTableRef } = useDrawerTableMaxHeight()
const selectedSnapshotId = ref<number | null>(null)
const expandedSnapshotRowKeys = ref<number[]>([])
const snapshotDetailLoading = ref(false)
const snapshotDetailError = ref('')
const snapshotDetails = ref(new Map<number, BackupSourceSnapshot>())
const selectedSnapshotDirectory = ref<BackupSourceSnapshotDirectory | null>(null)
const fileBrowserDrawerOpen = ref(false)
const browserLoading = ref(false)
const browserError = ref('')
const browserPath = ref('')
const browserParentPath = ref('')
const browserEntries = ref<BackupSnapshotBrowserEntry[]>([])
const browserTreeRef = ref<InstanceType<typeof ElTree> | null>(null)
const browserTreeEntries = ref<SnapshotBrowserTreeNode[]>([])
const browserTreeVersion = ref(0)
const selectedBrowserPaths = ref<Set<string>>(new Set())
const downloadingSelected = ref(false)
const selectedSnapshotFileChecked = ref(false)
const downloadingSnapshotFile = ref(false)
const sourceSnapshotRows = ref<BackupSourceSnapshot[]>([])
const sourceSnapshotsLoading = ref(false)
const sourceSnapshotsError = ref('')
const sourceDetail = ref<BackupSelectableSource | null>(null)
const sourceDetailLoading = ref(false)
const sourceDetailError = ref('')
const restoreRecords = ref<RestoreRecord[]>([])
const restoreRecordsLoading = ref(false)
const restoreRecordsError = ref('')
const restoreRecordsSilentRefreshing = ref(false)
const expandedRestoreRecordRowKeys = ref<number[]>([])
const targetedRestoreRecord = ref<RestoreRecord | null>(null)
const appliedRestoreRecordTargetId = ref<number | null>(null)
const restoreRecordRuntimeById = ref<Map<number, TaskRuntimePayload>>(new Map())
const restoreRecordRuntimeLoadingIds = ref<Set<number>>(new Set())
let restoreRecordPollingTimer: ReturnType<typeof setInterval> | null = null
let provisionPollingTimer: ReturnType<typeof setInterval> | null = null
let provisionPollingInFlight = false
let provisionPollingAttempts = 0
const sourceTaskRows = ref<TaskRow[]>([])
const sourceTasksLoading = ref(false)
const sourceTasksError = ref('')
const sourceTasksTotal = ref(0)
const snapshotFilterId = ref('')
const snapshotFilterStatus = ref('')
const snapshotFilterTimeMode = ref<'all' | '24h' | '7d' | '30d' | 'range'>('all')
const snapshotFilterDateRange = ref<[Date, Date] | null>(null)
const restoreRecordFilterQuery = ref('')
const restoreRecordSearchField = ref<RestoreRecordSearchField>('restore_uid')
const restoreRecordFilterStatus = ref('')
const restoreRecordFilterSourceMode = ref<'' | 'plan' | 'manual'>('')
const restoreRecordFilterTimeMode = ref<'all' | '24h' | '7d' | '30d' | 'range'>('all')
const restoreRecordFilterDateRange = ref<[Date, Date] | null>(null)
const taskFilterId = ref('')
const taskFilterSearchField = ref<'name' | 'uuid'>('name')
const taskFilterType = ref('')
const taskFilterStatus = ref('')
const taskFilterTimeMode = ref<'all' | '24h' | '7d' | '30d' | 'range'>('all')
const taskFilterDateRange = ref<[Date, Date] | null>(null)
const taskAdvancedFilterOpen = ref(false)
const lastSourceTaskQuickTimeMode = ref<'all' | '24h' | '7d' | '30d'>('all')
const taskAdvancedFilterDraft = reactive({
  dateRange: null as [Date, Date] | null,
})
const {
  appliedSearch: appliedSnapshotFilterId,
  clearSearch: clearSnapshotSearch,
  resetSearch: resetSnapshotSearch,
  runSearchNow: runSnapshotSearchNow,
} = useListSearch(snapshotFilterId, () => reloadSnapshotsFromFirstPage())
const {
  appliedSearch: appliedRestoreRecordFilterQuery,
  clearSearch: clearRestoreRecordSearch,
  handleSearchFieldChange: handleRestoreRecordSearchFieldChange,
  resetSearch: resetRestoreRecordSearch,
  runSearchNow: runRestoreRecordSearchNow,
} = useListSearch(restoreRecordFilterQuery, () => reloadRestoreRecordsFromFirstPage())
const {
  appliedSearch: appliedTaskFilterId,
  clearSearch: clearSourceTaskSearch,
  handleSearchFieldChange: handleSourceTaskSearchFieldChange,
  resetSearch: resetSourceTaskSearch,
  runSearchNow: runSourceTaskSearchNow,
} = useListSearch(taskFilterId, () => {
  reloadSourceTasksFromFirstPage()
})
const DETAIL_PAGE_SIZE = 10
const DETAIL_PAGE_SIZE_OPTIONS = [10, 20, 30, 50, 100]
const RESTORE_RECORD_POLL_INTERVAL_MS = 3000
const PROVISION_POLL_INTERVAL_MS = 3000
const PROVISION_POLL_MAX_ATTEMPTS = 60
const HIDDEN_SOURCE_SNAPSHOT_STATUSES = ['failed']
const snapshotPagination = reactive({ page: 1, pageSize: DETAIL_PAGE_SIZE, count: 0 })
const restoreRecordPagination = reactive({ page: 1, pageSize: DETAIL_PAGE_SIZE, count: 0 })
const taskPagination = reactive({ page: 1, pageSize: DETAIL_PAGE_SIZE })
const taskDetailOpen = ref(false)
const activeTask = ref<TaskRow | null>(null)
const { drawerScrollAnchorRef, resetDrawerScroll } = useDrawerScrollReset()
const activeTaskLoading = ref(false)
const activeBackupSnapshot = ref<BackupSourceSnapshot | null>(null)
const backupTaskActionBusy = ref(false)
const taskDetailEvents = ref<TaskEventRow[]>([])
const activeTaskDetailTab = ref<'steps' | 'resources'>('steps')
const expandedTaskSteps = reactive<Record<string, boolean>>({})
const selectedResourceType = ref('')
const resourceLoading = ref(false)
const resourceDetails = reactive<Record<string, ResourceDetailRow[]>>({})
const resourceErrors = reactive<Record<string, string>>({})

const DEFAULT_TASK_STATUS_OPTIONS = ['pending', 'waiting', 'running', 'success', 'failed', 'cancelled', 'timeout']
const RESTORE_RECORD_STATUS_OPTIONS = ['success', 'running', 'failed', 'cancelled', 'pending', 'timeout']
const SNAPSHOT_STATUS_OPTIONS = ['creating', 'available', 'partial', 'failed', 'deleting', 'delete_failed', 'deleted']
const DEFAULT_TASK_TYPE_OPTIONS = ['backup', 'restore', 'snapshot_download', 'snapshot_delete', 'backup_config_reset', 'backup_config_provision']
const sourceId = computed(() => props.source?.id ?? '')
const aggregate = computed(() => (sourceId.value ? aggregateForSource(sourceId.value) : null))

function mapBackupSelectableSource(item: BackupSelectableSource): FlowSourceRow {
  return {
    id: item.id,
    name: item.name,
    hostname: item.hostname || item.name,
    nodeName: item.node_name || item.name,
    nodeIp: item.node_ip || '',
    status: item.status,
    availability: item.availability,
    registeredAt: item.registered_at || '',
    type: item.type,
    protocol: item.protocol,
    platform: item.platform,
    connectionUri: item.connection_uri || '',
    boundNodeId: item.bound_node_id ?? null,
    mountStatus: item.mount_status || '',
    mountPoint: item.mount_point || '',
    refId: item.ref_id,
    bound_node_id: item.bound_node_id ?? null,
    mount_status: item.mount_status,
    pipeline_step: item.pipeline_step,
    cpuCores: item.cpu_cores ?? null,
    memoryTotalBytes: item.memory_total_bytes ?? null,
    diskCount: item.disk_count ?? null,
  }
}

const overviewSource = computed(() => sourceDetail.value ? mapBackupSelectableSource(sourceDetail.value) : null)
const realSourceConfigs = computed(() => {
  return sourceDetail.value?.backup_configs?.configs || []
})
const currentSourceConfig = computed(() => realSourceConfigs.value[0] ?? null)
const provisionRetrying = ref(false)
const provisionDiscarding = ref(false)
const currentProvisionAlertTitle = computed(() => {
  const config = currentSourceConfig.value
  if (!config) return ''
  if (config.status === 'provisioning') {
    return t('protection.backupsPage.provisionStatusValidating')
  }
  switch (config.provisioning_error_code) {
    case 'AGENT_UPGRADE_REQUIRED':
      return t('protection.backupsPage.provisionStatusUpgradeRequired')
    case 'NAS_REPOSITORY_READ_ONLY':
      return t('protection.backupsPage.provisionStatusReadOnly')
    case 'NAS_REPOSITORY_WRITE_DENIED':
      return t('protection.backupsPage.provisionStatusWriteDenied')
    case 'NAS_MOUNT_SOURCE_MISMATCH':
      return t('protection.backupsPage.provisionStatusMountMismatch')
    case 'REPOSITORY_OWNERSHIP_INVALID':
    case 'AGENT_PROTOCOL_INVALID':
      return t('protection.backupsPage.provisionStatusOwnershipConflict')
    default:
      return t('protection.backupsPage.provisionStatusFailed')
  }
})

async function retryCurrentConfigProvision() {
  const config = currentSourceConfig.value
  if (!config || provisionRetrying.value) return
  provisionRetrying.value = true
  try {
    await retryBackupConfigProvision(config.id)
    await loadOverviewForSource()
    emit('config-changed')
    ElMessage.success({ message: t('protection.backupsPage.provisionRetryQueued'), grouping: true })
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err), grouping: true })
  } finally {
    provisionRetrying.value = false
  }
}

function openCurrentProvisionAgent() {
  const path = currentSourceConfig.value?.source_type === 'nas'
    ? '/node/agents'
    : '/protection/backup-sources?tab=host'
  router.push(path)
}

async function discardCurrentFailedConfig() {
  const config = currentSourceConfig.value
  if (!config || provisionDiscarding.value) return
  try {
    await ElMessageBox.confirm(
      t('protection.backupsPage.provisionDiscardConfirm'),
      t('protection.backupsPage.provisionDiscard'),
      { type: 'warning' },
    )
  } catch {
    return
  }
  provisionDiscarding.value = true
  try {
    await deleteBackupConfig(config.id)
    await loadOverviewForSource()
    emit('config-changed')
    ElMessage.success({ message: t('protection.backupsPage.provisionDiscarded'), grouping: true })
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err), grouping: true })
  } finally {
    provisionDiscarding.value = false
  }
}
const repositoryPreviewByConfigId = computed<Map<number, BackupSelectableRepositoryPreview>>(() => new Map(
  (sourceDetail.value?.backup_configs?.repos_preview || []).map((repo) => [repo.config_id, repo]),
))
const currentSourceCompression = computed(() => {
  const level = currentSourceConfig.value?.compression_level ?? 'balanced'
  if (level === 'none') {
    return {
      level,
      icon: CircleOff,
      title: t('protection.backupsPage.compressionNoneTitle'),
      description: t('protection.backupsPage.compressionNoneDescription'),
    }
  }
  if (level === 'high') {
    return {
      level,
      icon: Archive,
      title: t('protection.backupsPage.compressionHighTitle'),
      description: t('protection.backupsPage.compressionHighDescription'),
    }
  }
  return {
    level,
    icon: Scale,
    title: t('protection.backupsPage.compressionBalancedTitle'),
    description: t('protection.backupsPage.compressionBalancedDescription'),
  }
})
const realSourceSnapshots = computed(() => sourceSnapshotRows.value)
const pagedSourceSnapshots = computed(() => sourceSnapshotRows.value)
const sourceSnapshotTotal = computed(() => snapshotPagination.count)
const selectedSnapshot = computed(() => {
  if (!selectedSnapshotId.value) return null
  return snapshotDetails.value.get(selectedSnapshotId.value)
    ?? realSourceSnapshots.value.find((snapshot) => snapshot.id === selectedSnapshotId.value)
    ?? null
})
const selectedSnapshotDirectories = computed(() => selectedSnapshot.value?.directories || [])
const sourceEndpoint = computed((): { sourceType: RestoreEndpointType; sourceRefId: number } | null => {
  const source = props.source
  if (!source) return null
  const [rawType, rawRefId] = source.id.split(':')
  const sourceRefId = Number(rawRefId)
  if (!Number.isFinite(sourceRefId) || sourceRefId <= 0) return null
  const sourceType: RestoreEndpointType = rawType === 'nas' ? 'nas' : 'agent'
  return { sourceType, sourceRefId }
})
const backupSummarySnapshotCount = computed(() => {
  const count = Number(sourceDetail.value?.runtime?.restorable_snapshot_count)
  return Number.isFinite(count) ? count : null
})
const backupSummarySnapshotCountText = computed(() => {
  const count = backupSummarySnapshotCount.value
  if (count !== null) return String(count)
  return sourceDetailLoading.value ? '...' : '—'
})
const backupSummarySnapshotCountTitle = computed(() => sourceDetailError.value)
const browserBreadcrumbs = computed(() => {
  const parts = browserPath.value.split('/').filter(Boolean)
  const crumbs = [{ label: t('protection.backupsPage.snapshotBrowserRoot'), path: '' }]
  let current = ''
  for (const part of parts) {
    current = current ? `${current}/${part}` : part
    crumbs.push({ label: part, path: current })
  }
  return crumbs
})
const selectedBrowserPathList = computed(() => Array.from(selectedBrowserPaths.value).sort())
const selectedBrowserPathCount = computed(() => selectedBrowserPaths.value.size)
const selectedSnapshotDirectoryIsFile = computed(() => selectedSnapshotDirectory.value?.path_type === 'file')
const selectedSnapshotFileCount = computed(() => (selectedSnapshotDirectoryIsFile.value && selectedSnapshotFileChecked.value ? 1 : 0))
const displayedRestoreRecords = computed(() => {
  const target = targetedRestoreRecord.value
  if (!target || restoreRecords.value.some((record) => record.id === target.id)) return restoreRecords.value
  return [target, ...restoreRecords.value]
})
const hasRestoreRecordFilters = computed(() => Boolean(
  appliedRestoreRecordFilterQuery.value.trim()
  || restoreRecordFilterStatus.value
  || restoreRecordFilterSourceMode.value
  || restoreRecordFilterTimeMode.value !== 'all',
))
const hasActiveRestoreRecords = computed(() => displayedRestoreRecords.value.some((record) => {
  const status = String(record.task_summary?.status || '').toLowerCase()
  return status === 'pending' || status === 'running'
}))
const sourceRelatedTasks = computed(() => sourceTaskRows.value)
watch(
  () => [sourceSnapshotTotal.value, snapshotPagination.pageSize] as const,
  () => {
    const maxPage = Math.max(1, Math.ceil(sourceSnapshotTotal.value / snapshotPagination.pageSize) || 1)
    if (snapshotPagination.page > maxPage) snapshotPagination.page = maxPage
  },
)
const taskSearchFieldOptions = computed(() => [
  { value: 'name' as const, label: t('ops.task.searchName') },
  { value: 'uuid' as const, label: t('ops.task.searchUuid') },
])
const snapshotStatusOptions = computed(() => SNAPSHOT_STATUS_OPTIONS.map((value) => ({
  value,
  label: snapshotStatusLabel(value),
})))
const restoreRecordSearchFieldOptions = computed(() => [
  { value: 'restore_uid' as const, label: t('protection.backupsPage.flowRestoreSearchRecordId') },
  { value: 'snapshot_uid' as const, label: t('protection.backupsPage.flowRestoreSearchSnapshotId') },
  { value: 'task_uuid' as const, label: t('protection.backupsPage.flowRestoreSearchTaskId') },
])
const restoreRecordStatusOptions = computed(() => RESTORE_RECORD_STATUS_OPTIONS.map((value) => ({
  value,
  label: taskStatusLabel(value),
})))
const restoreRecordSourceModeOptions = computed(() => [
  { value: 'plan' as const, label: t('protection.backupsPage.flowRestoreRecordModePlan') },
  { value: 'manual' as const, label: t('protection.backupsPage.flowRestoreRecordModeManual') },
])
const taskTypeOptions = computed(() => {
  const values = Array.from(new Set([
    ...DEFAULT_TASK_TYPE_OPTIONS,
    ...sourceRelatedTasks.value.map((task) => task.task_type).filter(Boolean),
  ])).sort()
  return values.map((value) => ({ label: taskTypeLabel(value), value }))
})
const taskStatusOptions = computed(() => {
  const values = Array.from(new Set([
    ...DEFAULT_TASK_STATUS_OPTIONS,
    ...sourceRelatedTasks.value.map((task) => task.status).filter(Boolean),
  ])).sort()
  return values.map((value) => ({ label: taskStatusLabel(value), value }))
})
const sourceTaskTimeModeOptions = computed(() => [
  { value: 'all' as const, label: t('ops.task.timeAll') },
  { value: '24h' as const, label: t('ops.task.time24h') },
  { value: '7d' as const, label: t('ops.task.time7d') },
  { value: '30d' as const, label: t('ops.task.time30d') },
  { value: 'range' as const, label: t('ops.task.timeRange') },
])
const detailTimeModeOptions = sourceTaskTimeModeOptions
const sourceTaskDateRangeShortcuts = computed(() => [
  {
    text: t('ops.task.time24h'),
    value: () => {
      const end = new Date()
      const start = new Date(end.getTime() - 24 * 60 * 60 * 1000)
      return [start, end]
    },
  },
  {
    text: t('ops.task.time7d'),
    value: () => {
      const end = new Date()
      const start = new Date(end.getTime() - 7 * 24 * 60 * 60 * 1000)
      return [start, end]
    },
  },
  {
    text: t('ops.task.time30d'),
    value: () => {
      const end = new Date()
      const start = new Date(end.getTime() - 30 * 24 * 60 * 60 * 1000)
      return [start, end]
    },
  },
])
const sourceTaskAdvancedFilterCount = computed(() => {
  let count = 0
  if (taskFilterTimeMode.value === 'range' && taskFilterDateRange.value) count += 1
  return count
})
const stepsWithEvents = computed(() => {
  const steps = activeTask.value?.steps || []
  const grouped: Record<number, TaskEventRow[]> = {}
  for (const event of taskDetailEvents.value) {
    if (event.step_id == null) continue
    const key = event.step_id
    if (!grouped[key]) grouped[key] = []
    grouped[key].push(event)
  }
  return steps.map((step) => ({
    ...step,
    events: grouped[step.id] || [],
  }))
})
const hasExpandableTaskSteps = computed(() => hasExpandableTaskStep(stepsWithEvents.value))
const hasAnyExpandedTaskStep = computed(() => hasExpandedTaskStep(stepsWithEvents.value, isStepExpanded))
const unlinkedTaskEvents = computed(() => {
  const stepIds = new Set((activeTask.value?.steps || []).map((step) => step.id))
  return taskDetailEvents.value.filter((event) => event.step_id == null || !stepIds.has(event.step_id))
})
const activeTaskResources = computed(() => activeTask.value?.resources || [])
const groupedTaskResources = computed<Record<string, TaskResourceRow[]>>(() => {
  const groups: Record<string, TaskResourceRow[]> = {}
  for (const resource of activeTaskResources.value) {
    if (!groups[resource.resource_type]) groups[resource.resource_type] = []
    groups[resource.resource_type].push(resource)
  }
  return groups
})
const resourceTypeTabs = computed(() => Object.entries(groupedTaskResources.value).map(([type, rows]) => ({
  type,
  label: resourceTypeLabel(type),
  count: rows.length,
})))
const selectedResourceRows = computed(() => resourceDetails[selectedResourceType.value] || [])
const currentSourcePolicy = computed(() => {
  const config = currentSourceConfig.value
  if (!config?.backup_policy_id) return null
  return sourceDetail.value?.policies?.items?.find((item) => item.id === config.backup_policy_id) ?? null
})
const currentSourceFilter = computed(() => {
  const config = currentSourceConfig.value
  if (!config?.file_filter_rule_id) return null
  return sourceDetail.value?.filters?.items?.find((item) => item.id === config.file_filter_rule_id) ?? null
})
const currentSourceRecoveryPlans = computed(() => {
  const config = currentSourceConfig.value
  return config && 'recovery_plans' in config ? config.recovery_plans : []
})
const currentSourceRecoveryPlanMappings = computed<RecoveryPlanMappingRow[]>(() => {
  const plans = [...currentSourceRecoveryPlans.value].sort((a, b) => {
    const order = Number(a.sort_order || 0) - Number(b.sort_order || 0)
    return order || Number(a.id || 0) - Number(b.id || 0)
  })
  if (!plans.length) return []
  const snapshotMappings = plans
    .filter((plan) => plan.scope === 'snapshot')
    .map((plan, index) => ({
      key: `snapshot-plan-${plan.id || index}`,
      plan,
      sourcePath: '__whole_snapshot__',
      sourcePathType: 'directory' as const,
    }))
  const pathPlans = plans.filter((plan) => plan.scope !== 'snapshot')
  const config = currentSourceConfig.value
  const dirs = config ? configDirectories(config) : []
  return [
    ...snapshotMappings,
    ...pathPlans.map((plan, index) => {
      const sourcePath = plan.source_path || '—'
      const dir = dirs.find((item) =>
        Number(plan.backup_config_dir_id || 0) === Number(item.id)
        || (plan.source_path || '') === item.path,
      )
      const dirPathType = normalizeRecoveryPlanPathType(dir?.path_type)
      const sourcePathType = dir && plan.source_path === dir.path && dirPathType
        ? dirPathType
        : inferRecoveryPlanSourcePathType(plan.source_path || '')
      return {
        key: `plan-${plan.id || index}`,
        plan,
        sourcePath,
        sourcePathType,
      }
    }),
  ]
})
const drawerOpen = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})
const taskDetailDrawerSize = computed(() => {
  const raw = String(props.drawerSize || '').trim()
  const numeric = Number(raw.replace(/[^\d.]/g, ''))
  if (Number.isFinite(numeric) && numeric > 0) return `${Math.max(650, Math.min(775, (numeric - 120) * 1.25))}px`
  return '700px'
})
const activeTaskUuid = computed(() => activeTask.value?.task_uuid || '')
const activeTaskDependencies = computed(() =>
  (activeTask.value?.dependencies || []).filter((dependency) => dependency.is_active),
)
const canCancelBackupTask = computed(() => {
  const task = activeTask.value
  if (!task || task.task_type !== 'backup') return false
  return task.status === 'pending' || task.status === 'running'
})
const activeTransferProgress = ref<TransferProgress | null>(null)
let activeTransferProgressTimer: ReturnType<typeof setInterval> | null = null

async function refreshActiveTransferProgress() {
  const task = activeTask.value
  if (!task || (task.status !== 'pending' && task.status !== 'running')) {
    activeTransferProgress.value = null
    return
  }
  if (isTransferProgress(task.transfer_progress)) {
    activeTransferProgress.value = task.transfer_progress
  }
  try {
    if (task.task_type === 'backup' && task.task_uuid) {
      const payload = await fetchBackupTaskRuntime(task.task_uuid)
      activeTransferProgress.value = isTransferProgress(payload.transfer_progress)
        ? payload.transfer_progress
        : activeTransferProgress.value
      return
    }
    if (task.task_type === 'restore') {
      const record = displayedRestoreRecords.value.find((row) => row.task_uuid === task.task_uuid)
      if (record?.id) {
        const payload = await fetchRestoreRecordRuntime(record.id)
        activeTransferProgress.value = isTransferProgress(payload.transfer_progress)
          ? payload.transfer_progress
          : activeTransferProgress.value
      }
    }
  } catch {
    activeTransferProgress.value = isTransferProgress(task.transfer_progress) ? task.transfer_progress : null
  }
}

function syncActiveTransferProgressPolling() {
  if (activeTransferProgressTimer) {
    clearInterval(activeTransferProgressTimer)
    activeTransferProgressTimer = null
  }
  const task = activeTask.value
  if (!task || (task.status !== 'pending' && task.status !== 'running')) {
    activeTransferProgress.value = null
    return
  }
  void refreshActiveTransferProgress()
  activeTransferProgressTimer = setInterval(() => { void refreshActiveTransferProgress() }, 3000)
}

async function loadActiveBackupSnapshot(task: TaskRow) {
  if (task.task_type !== 'backup') {
    activeBackupSnapshot.value = null
    return
  }
  const payload = task.result_payload
  let snapshotId = payload && typeof payload === 'object' && !Array.isArray(payload)
    ? Number((payload as Record<string, unknown>).source_snapshot_id || 0)
    : 0
  if (!snapshotId && sourceId.value) {
    const [sourceType, rawRefId] = sourceId.value.split(':')
    const sourceRefId = Number(rawRefId)
    if (sourceType && Number.isFinite(sourceRefId)) {
      const page = await listBackupSourceSnapshots({
        source_type: sourceType,
        source_ref_id: sourceRefId,
      })
      const match = page.results.find((row) => row.task_uuid === task.task_uuid)
      if (match) snapshotId = match.id
    }
  }
  if (!snapshotId) {
    activeBackupSnapshot.value = null
    return
  }
  try {
    activeBackupSnapshot.value = await getBackupSourceSnapshot(snapshotId)
  } catch {
    activeBackupSnapshot.value = null
  }
}

async function cancelActiveBackupTask() {
  if (!activeTask.value || !canCancelBackupTask.value) return
  const confirmed = await stopConfirmDialog.confirmStopBackup([buildStopConfirmItemFromTask(activeTask.value)])
  if (!confirmed) return
  backupTaskActionBusy.value = true
  try {
    await cancelProtectionBackupTask(activeTask.value.task_uuid)
    ElMessage.success({ message: t('protection.backupsPage.backupTaskCancelSuccess'), grouping: true })
    await refreshActiveTask()
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err), grouping: true })
  } finally {
    backupTaskActionBusy.value = false
  }
}

function flowSourceNodeParts(row: { nodeName?: string; nodeIp?: string }) {
  const name = (row.nodeName || '').trim()
  const ip = (row.nodeIp || '').trim()
  return { name: name || '—', ip }
}

function flowSourceSecondaryInfo(row: FlowSourceRow) {
  const node = flowSourceNodeParts(row)
  if (row.type === 'nas') {
    return {
      nameLabel: t('protection.backupsPage.flowSourceDetailBoundProxy'),
      name: node.name,
      ipLabel: t('protection.backupsPage.flowSourceDetailProxyIp'),
      ip: node.ip || '—',
    }
  }
  return {
    nameLabel: t('protection.backupsPage.flowSourceDetailHostname'),
    name: row.hostname || '—',
    ipLabel: t('protection.backupsPage.flowSourceDetailHostIp'),
    ip: node.ip || '—',
  }
}

function flowSourceTypeClass(row: { type: 'host' | 'nas'; protocol?: 'nfs' | 'smb' }) {
  if (row.type === 'host') return 'dp-flow-type-pill--host'
  return row.protocol === 'smb' ? 'dp-flow-type-pill--nas-smb' : 'dp-flow-type-pill--nas-nfs'
}

function flowSourceTypeParts(row: { type: 'host' | 'nas'; protocol?: 'nfs' | 'smb' }) {
  if (row.type === 'host') {
    return { main: t('protection.backupsPage.sourceTypeHost'), suffix: '' }
  }
  return {
    main: 'NAS',
    suffix: row.protocol === 'smb' ? '(SMB/CIFS)' : '(NFS)',
  }
}

function flowSourceStatusLabel(status?: 'online' | 'offline') {
  return status === 'online'
    ? t('protection.backupsPage.nodeStatusOnline')
    : t('protection.backupsPage.nodeStatusOffline')
}

function flowSourceStatusTag(status?: 'online' | 'offline') {
  return status === 'online' ? 'success' : 'neutral'
}

function flowSourceStatusTagType(status?: 'online' | 'offline') {
  const tag = flowSourceStatusTag(status)
  return tag === 'neutral' ? undefined : tag
}

function flowSourceLifecycleStatusLabel(status: FlowSourceRow['status']) {
  if (status === 'active') return t('protection.sourceResources.lifecycleRegistered')
  const labelKey = {
    inactive: 'protection.sourceResources.status.inactive',
    error: 'protection.sourceResources.status.error',
    probing: 'protection.sourceResources.capacitySyncing',
    removing: 'protection.backupsPage.sourcePendingDeleting',
    remove_failed: 'protection.backupsPage.sourcePendingDeleteFailed',
  }[status] || `nodeLifecycle.state.${status}`
  return t(labelKey)
}

function flowSourceLifecycleStatusTag(status: FlowSourceRow['status']) {
  if (status === 'active') return 'success'
  if (status === 'inactive') return 'warning'
  if (status === 'error' || status === 'remove_failed' || status === 'failed' || status === 'upgrade_failed' || status === 'deregistration_failed') return 'danger'
  return 'info'
}

function flowSourceRegisteredAt(value?: string) {
  return formatLocalDateTime(value, '—')
}

function endpointUiId(type: string, refId: number) {
  return `${type}:${refId}`
}

function targetSourceRow(plan: BackupConfigRecoveryPlan) {
  const targetType = plan.target_type || (plan.restore_host_id ? 'agent' : '')
  const refId = Number(plan.target_ref_id ?? plan.restore_host_id ?? 0)
  if (!targetType || !Number.isFinite(refId) || refId <= 0) return null
  const id = endpointUiId(targetType, refId)
  return props.sourceRows.find((row) => row.id === id) ?? null
}

function configDirectories(config: BackupConfig | BackupConfigDetail) {
  return 'directories' in config ? config.directories : []
}

function backupConfigDirectoryKind(dir: BackupConfigDirectory) {
  const type = normalizeRecoveryPlanPathType(dir.path_type)
  const pathType = type || inferRecoveryPlanSourcePathType(dir.path || '')
  return pathType === 'file' ? 'file' : 'dir'
}

function backupConfigDirectoryIcon(dir: BackupConfigDirectory) {
  return backupConfigDirectoryKind(dir) === 'file' ? File : Folder
}

function configRepositoryName(config: BackupConfig | BackupConfigDetail) {
  const repo = repositoryPreviewByConfigId.value.get(config.id)
  return repo?.name || `#${config.repository_id}`
}

function configRepositoryLocation(config: BackupConfig | BackupConfigDetail) {
  return repositoryPreviewByConfigId.value.get(config.id)?.location || '—'
}

function configRepositoryType(config: BackupConfig | BackupConfigDetail) {
  const repo = repositoryPreviewByConfigId.value.get(config.id)
  const type = String(repo?.repo_type || '').toLowerCase()
  if (type === 's3') return 'S3'
  if (type === 'nas') return t('protection.backupsPage.repoTypeNas')
  if (type === 'proxy_fs') return t('protection.backupsPage.repoTypeProxyFs')
  return repo?.repo_type || '—'
}

function recoveryPlanStatusLabel(mappings: RecoveryPlanMappingRow[]) {
  return t('protection.backupsPage.recoveryPlanEnabledPathCount', { n: mappings.length })
}

function recoveryPlanConflictTone(plan: BackupConfigRecoveryPlan) {
  return plan.conflict_mode === 'overwrite' ? 'overwrite' : 'skip'
}

function recoveryPlanConflictSummary(plan: BackupConfigRecoveryPlan) {
  return plan.conflict_mode === 'overwrite'
    ? t('protection.backupsPage.createRecoveryConflictOverwrite')
    : t('protection.backupsPage.createRecoveryConflictSkip')
}

function recoveryPlanConflictFullLabel(plan: BackupConfigRecoveryPlan) {
  return plan.conflict_mode === 'overwrite'
    ? t('protection.backupsPage.createRecoveryConflictOverwriteFull')
    : t('protection.backupsPage.createRecoveryConflictSkipFull')
}

function recoveryPlanConflictDescription(plan: BackupConfigRecoveryPlan) {
  return recoveryPlanConflictFullLabel(plan)
}

function recoveryPlanSourcePathLabel(mapping: RecoveryPlanMappingRow) {
  const sourcePath = mapping.sourcePath || mapping.plan.source_path || ''
  if (recoveryPlanMappingSourceKind(mapping) === 'snapshot') return t('protection.backupsPage.recoveryWholeSnapshot')
  return sourcePath || '—'
}

function normalizeRecoveryPlanPathType(value: unknown): 'directory' | 'file' | 'unknown' | '' {
  const raw = String(value || '').trim().toLowerCase()
  if (raw === 'file') return 'file'
  if (raw === 'directory' || raw === 'dir') return 'directory'
  if (raw === 'unknown') return 'unknown'
  return ''
}

function inferRecoveryPlanSourcePathType(path: string): 'directory' | 'file' | 'unknown' {
  if (!path || path === '—' || path === '__whole_snapshot__') return 'unknown'
  const base = path.split(/[\\/]/).filter(Boolean).pop() || ''
  return /\.[A-Za-z0-9]{1,16}$/.test(base) ? 'file' : 'directory'
}

function recoveryPlanMappingSourceKind(mapping: RecoveryPlanMappingRow) {
  const sourcePath = mapping.sourcePath || mapping.plan.source_path || ''
  if (sourcePath === '__whole_snapshot__') return 'snapshot'
  return mapping.sourcePathType === 'file' ? 'file' : 'dir'
}

function recoveryPlanTargetName(plan: BackupConfigRecoveryPlan) {
  const row = targetSourceRow(plan)
  if (row) {
    const name = row.name || row.nodeName || row.hostname || ''
    return row.nodeIp ? `${name} ${row.nodeIp}` : name || '—'
  }
  const type = plan.target_type || (plan.restore_host_id ? 'agent' : '')
  const refId = plan.target_ref_id ?? plan.restore_host_id
  if (type === 'nas') return refId ? `NAS #${refId}` : 'NAS'
  if (type === 'agent') return refId ? `Host #${refId}` : 'Host'
  return refId ? `#${refId}` : '—'
}

function recoveryPlanTargetSummary(plan: BackupConfigRecoveryPlan) {
  const targetPath = plan.restore_dir || '—'
  const targetName = recoveryPlanTargetName(plan)
  return `${targetPath} (${targetName})`
}

function restoreRecordStatus(record: RestoreRecord) {
  return restoreRecordTaskStatus(record)
}

function restoreRecordProgressValue(record: RestoreRecord) {
  return formatTaskProgressBarPercent(record.task_summary?.progress ?? 0)
}

function restoreRecordProgressText(record: RestoreRecord) {
  return formatTaskProgressPercent(restoreRecordProgressValue(record))
}

function restoreRecordDuration(record: RestoreRecord) {
  return durationText(
    record.task_summary?.started_at || record.created_at,
    record.task_summary?.finished_at,
  )
}

function restoreRecordConflictLabel(mode?: string | null) {
  return mode === 'overwrite'
    ? t('protection.backupsPage.flowRestoreRecordConflictOverwrite')
    : t('protection.backupsPage.flowRestoreRecordConflictSkip')
}

function restoreRecordModeLabel(record: RestoreRecord) {
  return record.source_mode === 'plan'
    ? t('protection.backupsPage.flowRestoreRecordModePlan')
    : t('protection.backupsPage.flowRestoreRecordModeManual')
}

function restoreRecordModeTitle(record: RestoreRecord) {
  if (record.source_mode !== 'plan' || !record.plan_id) return restoreRecordModeLabel(record)
  return `${restoreRecordModeLabel(record)} #${record.plan_id}`
}

function restoreRecordTargetName(record: RestoreRecord) {
  const id = endpointUiId(record.target_type, Number(record.target_ref_id))
  const row = props.sourceRows.find((item) => item.id === id)
  if (row) {
    const name = row.name || row.nodeName || row.hostname || ''
    return row.nodeIp ? `${name} ${row.nodeIp}` : name || '—'
  }
  if (record.target_type === 'nas') return record.target_ref_id ? `NAS #${record.target_ref_id}` : 'NAS'
  return record.target_ref_id ? `Host #${record.target_ref_id}` : 'Host'
}

function restoreItemTargetSummary(record: RestoreRecord, item: RestoreRecordItem) {
  const targetPath = restoreRecordTargetDisplayPath(record, item)
  return `${targetPath} (${restoreRecordTargetName(record)})`
}

function restoreRecordTargetSummary(record: RestoreRecord) {
  return `${restoreRecordTargetDisplayPath(record)} (${restoreRecordTargetName(record)})`
}

function restoreItemSourceKind(item: RestoreRecordItem) {
  return inferRecoveryPlanSourcePathType(item.source_path || '') === 'file' ? 'file' : 'dir'
}

function resetExpandedRestoreItems() {
  expandedRestoreRecordRowKeys.value = []
}

function toggleRestoreRecord(record: RestoreRecord) {
  const expanding = !expandedRestoreRecordRowKeys.value.includes(record.id)
  expandedRestoreRecordRowKeys.value = expanding ? [record.id] : []
  if (expanding) void loadRestoreRecordRuntime(record)
}

function onRestoreRecordExpandChange(record: RestoreRecord, expandedRows: RestoreRecord[]) {
  const expanded = expandedRows.some((item) => item.id === record.id)
  expandedRestoreRecordRowKeys.value = expanded ? [record.id] : []
  if (expanded) void loadRestoreRecordRuntime(record)
}

function restoreRecordRuntime(record: RestoreRecord) {
  return restoreRecordRuntimeById.value.get(record.id) ?? null
}

function restoreRecordRuntimeLoading(record: RestoreRecord) {
  return restoreRecordRuntimeLoadingIds.value.has(record.id)
}

function restoreRecordMetrics(record: RestoreRecord) {
  return restoreRecordRuntimeMetricParts(
    t,
    restoreRecordRuntime(record),
    restoreRecordTaskStatus(record),
  )
}

async function loadRestoreRecordRuntime(record: RestoreRecord) {
  const signal = requests.nextSignal('flow-source-restore-record-runtime')
  restoreRecordRuntimeLoadingIds.value.add(record.id)
  try {
    const runtime = await fetchRestoreRecordRuntime(record.id, { signal })
    restoreRecordRuntimeById.value.set(record.id, runtime)
  } catch (err) {
    if (!requests.isAbortError(err)) {
      restoreRecordRuntimeById.value.delete(record.id)
    }
  } finally {
    requests.releaseSignal('flow-source-restore-record-runtime', signal)
    restoreRecordRuntimeLoadingIds.value.delete(record.id)
  }
}

function latestSnapshotForConfig(configId: number) {
  const snapshot = sourceDetail.value?.runtime?.latest_snapshot
  return snapshot && Number(snapshot.backup_config_id || 0) === configId ? snapshot : null
}

function taskPayload(task: TaskRow): Record<string, unknown> {
  return task.request_payload && typeof task.request_payload === 'object'
    ? task.request_payload as Record<string, unknown>
    : {}
}

function taskI18nLabel(scope: 'status' | 'taskType' | 'triggerType' | 'resourceType', value?: string | null) {
  if (!value) return t('protection.backupDetail.durationDash')
  const key = `ops.task.${scope}.${value}`
  return te(key) ? t(key) : t('ops.task.unknownValue')
}

function taskTypeLabel(value?: string | null) {
  return taskI18nLabel('taskType', value)
}

function taskStatusLabel(value?: string | null) {
  return taskI18nLabel('status', value)
}

function taskTriggerLabel(value?: string | null) {
  return taskI18nLabel('triggerType', value)
}

function resourceTypeLabel(value?: string | null) {
  return taskI18nLabel('resourceType', value)
}

function objectValue(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key]
    if (value !== undefined && value !== null && value !== '') return String(value)
  }
  return ''
}

function resourceSummary(raw: unknown) {
  if (!raw || typeof raw !== 'object') return ''
  const record = raw as Record<string, unknown>
  const config = record.config && typeof record.config === 'object'
    ? record.config as Record<string, unknown>
    : {}
  return [
    objectValue(record, ['connection_summary', 'bound_node_name', 'bind_node_display_name', 's3_bucket', 'kopia_snapshot_id']),
    objectValue(config, ['path', 'bucket', 'endpoint', 'server', 'export_path']),
  ].filter(Boolean).join(' · ')
}

function resourceValueLabel(value?: string | null) {
  if (!value) return t('protection.backupDetail.durationDash')
  const key = `ops.task.resourceValue.${value}`
  return te(key) ? t(key) : t('ops.task.unknownValue')
}

function normalizeResourceDetail(type: string, id: number, raw: unknown, source?: Awaited<ReturnType<typeof resolveTaskBackupSourceResource>>): ResourceDetailRow {
  const record = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
  const rawType = objectValue(record, ['resource_type', 'repo_type', 'role', 'snapshot_type'])
  const rawStatus = objectValue(record, ['status', 'health', 'mount_status', 'source_snapshot_status'])
  return {
    key: `${type}:${id}`,
    id,
    name: objectValue(record, ['name', 'display_name', 'hostname', 'snapshot_uid', 'task_uuid']) || String(id),
    type: rawType ? resourceValueLabel(rawType) : resourceTypeLabel(type),
    status: rawStatus ? resourceValueLabel(rawStatus) : '',
    statusValue: rawStatus,
    updatedAt: objectValue(record, ['updated_at', 'last_checked_at', 'created_at']),
    summary: resourceSummary(record) || t('protection.backupDetail.durationDash'),
    backupSource: source?.backupSource,
    endpointName: source?.endpointName,
    endpointIp: source?.endpointIp,
    registeredAt: source?.registeredAt,
    availability: source?.availability,
    flowSource: source?.flowSource,
  }
}

function taskResourceSubtype(resource: TaskResourceRow) {
  if (resource.resource_subtype) return resource.resource_subtype
  if (resource.resource_type !== 'backup_source') return ''
  return objectValue(activeTask.value ? taskPayload(activeTask.value) : {}, ['source_type']).toLowerCase()
}

async function fetchResourceDetail(resource: TaskResourceRow, signal?: AbortSignal) {
  const { resource_type: type, resource_id: id } = resource
  const subtype = taskResourceSubtype(resource)
  if (type === 'backup_source' && subtype === 'agent') return getNode(id, { signal })
  if (type === 'backup_source') return getSourceResource(id, { signal })
  if (type === 'repository' || type === 'target_repository') return getStorageRepository(id, { signal })
  if (type === 'snapshot') return getBackupSourceSnapshot(id, { signal })
  if (type === 'host') return getNode(id, { signal })
  return { id, resource_type: type, resource_subtype: subtype }
}

function resetResourceDetails() {
  selectedResourceType.value = ''
  for (const key of Object.keys(resourceDetails)) delete resourceDetails[key]
  for (const key of Object.keys(resourceErrors)) delete resourceErrors[key]
}

function firstResourceType() {
  return resourceTypeTabs.value[0]?.type || ''
}

async function loadResourceType(type: string) {
  if (!type) return
  selectedResourceType.value = type
  if (resourceDetails[type]) return
  const resources = groupedTaskResources.value[type] || []
  const signal = requests.nextSignal('flow-task-resources')
  resourceLoading.value = true
  delete resourceErrors[type]
  try {
    const results = await Promise.allSettled(resources.map(async (resource) => {
      try {
        // For source_unregister tasks with backup_source resources,
        // resolve from immutable task payload data first to avoid 404s.
        if (activeTask.value?.task_type === 'source_unregister' && resource.resource_type === 'backup_source') {
          const fromPayload = resolveTaskBackupSourceResourceFromPayload(resource, activeTask.value)
          if (fromPayload) {
            return normalizeResourceDetail(resource.resource_type, resource.resource_id, {}, fromPayload)
          }
        }
        const raw = await fetchResourceDetail(resource, signal)
        const source = resource.resource_type === 'backup_source'
          ? await resolveTaskBackupSourceResource(resource, taskResourceSubtype(resource), signal)
          : undefined
        return normalizeResourceDetail(resource.resource_type, resource.resource_id, raw, source)
      } catch (error) {
        // Fallback: try to resolve from task payload snapshot
        if (activeTask.value) {
          const snapshot = taskResourceSnapshot(activeTask.value, resource)
          if (snapshot) {
            return normalizeResourceDetail(resource.resource_type, resource.resource_id, snapshot)
          }
        }
        throw error
      }
    }))
    const rows = results.map((result, index) => {
      if (result.status === 'fulfilled') return result.value
      const resource = resources[index]
      return normalizeResourceDetail(resource.resource_type, resource.resource_id, resource)
    })
    resourceDetails[type] = rows
    const failedCount = results.filter((r) => r.status === 'rejected').length
    if (failedCount > 0) {
      const firstError = results.find((r) => r.status === 'rejected')
      if (firstError && firstError.status === 'rejected') {
        resourceErrors[type] = `${failedCount} resource(s) failed to load: ${apiErrorMessage(firstError.reason)}`
      }
    }
  } catch (err) {
    if (requests.isAbortError(err)) return
    resourceErrors[type] = apiErrorMessage(err)
    resourceDetails[type] = resources.map((resource) => normalizeResourceDetail(resource.resource_type, resource.resource_id, resource))
  } finally {
    requests.releaseSignal('flow-task-resources', signal)
    if (!signal.aborted) resourceLoading.value = false
  }
}

function openTaskResourceTab() {
  activeTaskDetailTab.value = 'resources'
  const type = selectedResourceType.value || firstResourceType()
  if (type) void loadResourceType(type)
}

function progressValue(task: TaskRow) {
  return formatTaskProgressBarPercent(task.progress)
}

function progressText(task: TaskRow) {
  return formatTaskProgressPercent(task.progress)
}

function stepDisplayName(stepName?: string | null, taskType?: string | null) {
  const step = String(stepName || '')
  if (!step) return t('protection.backupDetail.durationDash')
  if (taskType === 'snapshot_download') {
    if (step === 'restore') return t('ops.task.step.snapshot_download_restore')
    if (step === 'transfer') return t('ops.task.step.snapshot_download_transfer')
    if (step === 'finalize') return t('ops.task.step.snapshot_download_finalize')
  }
  const key = `ops.task.step.${step}`
  return te(key) ? t(key) : t('ops.task.unknownValue')
}

function taskEventMetadata(event: TaskEventRow) {
  const metadata = event.metadata
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) return {}
  return metadata as Record<string, unknown>
}

function taskEventMetadataText(event: TaskEventRow, keys: string[]) {
  const metadata = taskEventMetadata(event)
  for (const key of keys) {
    const value = metadata[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  }
  return ''
}

function taskEventMetadataList(event: TaskEventRow, key: string) {
  const value = taskEventMetadata(event)[key]
  if (!Array.isArray(value)) return ''
  const items = value
    .map((item) => String(item || '').trim())
    .filter(Boolean)
  if (items.length === 0) return ''
  if (items.length <= 2) return items.join(', ')
  return `${items.slice(0, 2).join(', ')} +${items.length - 2}`
}

function eventErrorText(event: TaskEventRow) {
  const step = activeTask.value?.steps?.find(item => item.id === event.step_id)
  if (event.message === 'Task finished with status failed' && step?.step_name === 'finalize_snapshot') return ''
  if (taskEventMetadata(event).failure_details) return ''
  const message = taskEventMetadataText(event, ['error_message'])
  if (!message) return ''
  const code = taskEventMetadataText(event, ['error_code'])
  return code ? `[${code}] ${message}` : message
}

function eventObjectText(event: TaskEventRow) {
  if (taskEventMetadata(event).backup_summary) return ''
  const canonicalValue = taskEventObjectText(event)
  if (canonicalValue) return canonicalValue
  const directValue = taskEventMetadataText(event, [
    'kopia_snapshot_display',
    'source_path',
    'target_path',
    'restore_path',
    'path',
    'local_path',
    'task_display_name',
    'name',
    'display_name',
    'restore_uid',
    'kopia_snapshot_id',
    'node_task_id',
    'source_snapshot_id',
    'source_snapshot_directory_id',
    'backup_config_dir_id',
    'directory_id',
    'repository_id',
    'node_id',
    'target_node_id',
  ])
  return directValue || taskEventMetadataList(event, 'paths')
}

function eventDisplayMessage(event: TaskEventRow) {
  const text = String(event.message || '')
  const key = taskEventMessageKey(text)
  if (key) return t(key)
  const stepStatus = parseTaskStepStatusEvent(text)
  if (stepStatus) {
    return t('ops.task.eventMessage.stepStatusUpdated', {
      step: stepDisplayName(stepStatus.step, activeTask.value?.task_type),
      status: taskStatusLabel(stepStatus.status),
    })
  }
  return text
}

function durationText(start?: string | null, end?: string | null) {
  if (!start || !end) return t('protection.backupDetail.durationDash')
  const startMs = new Date(start).getTime()
  const endMs = new Date(end).getTime()
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs < startMs) return t('protection.backupDetail.durationDash')
  const seconds = Math.max(0, Math.round((endMs - startMs) / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remain = seconds % 60
  return [hours, minutes, remain].map((part) => String(part).padStart(2, '0')).join(':')
}

function taskDuration(task?: TaskRow | null) {
  return durationText(task?.started_at || task?.created_at, task?.finished_at || task?.updated_at)
}

function stepDuration(index: number) {
  const current = stepsWithEvents.value[index]
  const next = stepsWithEvents.value[index + 1]
  const start = current?.events[0]?.created_at || current?.created_at || activeTask.value?.started_at || activeTask.value?.created_at
  const end = next?.events[0]?.created_at || current?.events[current.events.length - 1]?.created_at || activeTask.value?.finished_at
  return durationText(start, end)
}

function taskDetailTitle(task?: TaskRow | null) {
  return task?.display_name || stepDisplayName(task?.current_step, task?.task_type) || t('protection.backupsPage.backupTaskDrawerTitle')
}

function timelineIconClass(status?: string) {
  if (status === 'success') return 'dp-task-detail__timeline-icon--success'
  if (status === 'failed' || status === 'timeout') return 'dp-task-detail__timeline-icon--danger'
  if (status === 'running') return 'dp-task-detail__timeline-icon--running'
  if (status === 'cancelled') return 'dp-task-detail__timeline-icon--muted'
  return 'dp-task-detail__timeline-icon--pending'
}

function eventTone(event: TaskEventRow) {
  const level = String(event.level || '').toUpperCase()
  const message = String(event.message || '').toLowerCase()
  if (level === 'ERROR' || message.includes('failed') || message.includes('error')) return 'danger'
  if (message.includes('timeout')) return 'danger'
  if (message.includes('cancelled')) return 'muted'
  if (level === 'WARN' || level === 'WARNING') return 'warning'
  if (level === 'DEBUG') return 'muted'
  return 'success'
}

function eventMessageClass(event: TaskEventRow) {
  const tone = eventTone(event)
  return {
    'dp-task-detail__event-msg--danger': tone === 'danger',
    'dp-task-detail__event-msg--warning': tone === 'warning',
    'dp-task-detail__event-msg--muted': tone === 'muted',
  }
}

function stepKey(stepId: number | string) {
  return String(stepId)
}

function initExpandedSteps(task?: TaskRow | null) {
  for (const key of Object.keys(expandedTaskSteps)) delete expandedTaskSteps[key]
  for (const step of task?.steps || []) expandedTaskSteps[stepKey(step.id)] = true
}

function isStepExpanded(stepId: number | string) {
  return expandedTaskSteps[stepKey(stepId)] !== false
}

function toggleStep(stepId: number | string, eventCount: number) {
  if (eventCount === 0) return
  const key = stepKey(stepId)
  expandedTaskSteps[key] = !isStepExpanded(key)
}

function setAllStepsExpanded(expanded: boolean) {
  for (const step of activeTask.value?.steps || []) expandedTaskSteps[stepKey(step.id)] = expanded
}

function toggleAllTaskStepsExpanded() {
  setAllStepsExpanded(!hasAnyExpandedTaskStep.value)
}

async function copyText(value: string) {
  if (!value) return
  try {
    await copyTextToClipboard(value)
    pushToast({
      type: 'success',
      message: t('protection.backupsPage.flowSourceDetailCopied'),
      dedupeKey: 'copy:FlowSourceDetail',
      duration: 2000,
    })
  } catch (err) {
    console.error('Copy to clipboard failed:', err)
    pushToast({
      type: 'error',
      message: t('protection.backupsPage.flowSourceDetailCopyFailed'),
      dedupeKey: 'copy:FlowSourceDetail',
      duration: 3000,
    })
  }
}

function syncSourceTask(task: TaskRow) {
  const index = sourceTaskRows.value.findIndex((row) => row.task_uuid === task.task_uuid)
  if (index >= 0) sourceTaskRows.value[index] = task
  if (activeTask.value?.task_uuid === task.task_uuid) activeTask.value = task
}

async function refreshActiveTask() {
  if (!activeTask.value?.task_uuid) return
  activeTaskLoading.value = true
  const taskUuid = activeTask.value.task_uuid
  try {
    const [task, events] = await Promise.all([
      getTask(taskUuid),
      listTaskEvents(taskUuid, { page: 1, page_size: 500 }),
    ])
    activeTask.value = task
    taskDetailEvents.value = events.results
    syncSourceTask(task)
    initExpandedSteps(task)
    await loadActiveBackupSnapshot(task)
    syncActiveTransferProgressPolling()
    if (activeTaskDetailTab.value === 'resources') {
      const previousType = selectedResourceType.value
      resetResourceDetails()
      const type = groupedTaskResources.value[previousType] ? previousType : firstResourceType()
      if (type) void loadResourceType(type)
    }
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.loadFailed')), grouping: true })
  } finally {
    activeTaskLoading.value = false
  }
}

function openTaskStepsTab() {
  activeTaskDetailTab.value = 'steps'
  setAllStepsExpanded(true)
}

function onTaskDetailTabChange(name: string | number) {
  if (name === 'resources') openTaskResourceTab()
  else if (name === 'steps') openTaskStepsTab()
}

function openTaskDetail(task: TaskRow) {
  activeTask.value = task
  resetDrawerScroll()
  taskDetailEvents.value = task.recent_events || []
  activeTaskDetailTab.value = 'steps'
  taskDetailOpen.value = true
  resetResourceDetails()
  initExpandedSteps(task)
  void refreshActiveTask()
}

async function openTaskDetailByUuid(taskUuid?: string | null) {
  const uuid = String(taskUuid || '').trim()
  if (!uuid) return
  const existingTask = sourceRelatedTasks.value.find((task) => task.task_uuid === uuid)
    || sourceTaskRows.value.find((task) => task.task_uuid === uuid)
  if (existingTask) {
    openTaskDetail(existingTask)
    return
  }
  try {
    const task = await getTask(uuid)
    openTaskDetail(task)
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.loadFailed')), grouping: true })
  }
}

defineExpose({
  openTaskDetailByUuid,
})

function sourceTaskRowClassName({ row }: { row: TaskRow }) {
  return row.task_uuid === activeTaskUuid.value ? 'dp-source-task-table__row--active' : ''
}

function closeTaskDetail() {
  activeTask.value = null
  taskDetailEvents.value = []
  activeTaskDetailTab.value = 'steps'
  resetResourceDetails()
  initExpandedSteps(null)
}

function onTaskDetailClosed() {
  closeTaskDetail()
}

function resetSourceTaskAdvancedFilterDraft() {
  taskAdvancedFilterDraft.dateRange = null
}

function onSourceTaskTimeModeChange(value: string) {
  if (value === 'range') {
    openSourceTaskAdvancedFilterDrawer()
    return
  }
  lastSourceTaskQuickTimeMode.value = value as 'all' | '24h' | '7d' | '30d'
  taskFilterDateRange.value = null
}

function syncSourceTaskAdvancedFilterDraft() {
  taskAdvancedFilterDraft.dateRange = taskFilterDateRange.value
}

function openSourceTaskAdvancedFilterDrawer() {
  syncSourceTaskAdvancedFilterDraft()
  taskAdvancedFilterOpen.value = true
}

function applySourceTaskAdvancedFilters() {
  taskFilterDateRange.value = normalizeSourceTaskDateRange(taskAdvancedFilterDraft.dateRange)
  if (taskFilterDateRange.value) taskFilterTimeMode.value = 'range'
  else if (taskFilterTimeMode.value === 'range') taskFilterTimeMode.value = lastSourceTaskQuickTimeMode.value
  taskAdvancedFilterOpen.value = false
}

function normalizeSourceTaskDateRange(range: [Date, Date] | null) {
  if (!range) return null
  const [start, selectedEnd] = range
  const end = new Date(selectedEnd)
  const now = new Date()
  if (end.getFullYear() === now.getFullYear()
    && end.getMonth() === now.getMonth()
    && end.getDate() === now.getDate()
    && end.getHours() === 23 && end.getMinutes() === 59 && end.getSeconds() === 59) {
    return [start, now] as [Date, Date]
  }
  return [start, end] as [Date, Date]
}

function onSourceTaskAdvancedFilterClosed() {
  if (taskFilterTimeMode.value === 'range' && !taskFilterDateRange.value) taskFilterTimeMode.value = lastSourceTaskQuickTimeMode.value
  syncSourceTaskAdvancedFilterDraft()
}

function isoDateParam(date?: Date | null) {
  return date instanceof Date && Number.isFinite(date.getTime()) ? date.toISOString() : undefined
}

type DetailTimeMode = 'all' | '24h' | '7d' | '30d' | 'range'

function detailTimeRangeParams(mode: DetailTimeMode, range: [Date, Date] | null) {
  const now = new Date()
  if (mode === '24h') return { from: new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString(), to: undefined }
  if (mode === '7d') return { from: new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString(), to: undefined }
  if (mode === '30d') return { from: new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString(), to: undefined }
  if (mode === 'range' && range) {
    const end = new Date(range[1])
    end.setMilliseconds(999)
    return { from: isoDateParam(range[0]), to: isoDateParam(end) }
  }
  return { from: undefined, to: undefined }
}

function onSnapshotTimeModeChange(value: DetailTimeMode) {
  if (value !== 'range') snapshotFilterDateRange.value = null
}

function onRestoreRecordTimeModeChange(value: DetailTimeMode) {
  if (value !== 'range') restoreRecordFilterDateRange.value = null
}

function sourceTaskCreatedRangeParams() {
  const now = new Date()
  if (taskFilterTimeMode.value === '24h') return { created_after: new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString(), created_before: undefined }
  if (taskFilterTimeMode.value === '7d') return { created_after: new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString(), created_before: undefined }
  if (taskFilterTimeMode.value === '30d') return { created_after: new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString(), created_before: undefined }
  if (taskFilterTimeMode.value === 'range' && taskFilterDateRange.value) {
    return {
      created_after: isoDateParam(taskFilterDateRange.value[0]),
      created_before: (() => { const end = new Date(taskFilterDateRange.value[1]); end.setMilliseconds(999); return isoDateParam(end) })(),
    }
  }
  return { created_after: undefined, created_before: undefined }
}

function formatNullableTime(raw?: string | null) {
  return raw ? fmtLocalTime(raw) : t('protection.backupDetail.durationDash')
}

function fmtBytes(n: number) {
  if (!n || n <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let value = n
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i += 1
  }
  return `${value.toFixed(i >= 2 ? 1 : 0)} ${units[i]}`
}

function fmtReferenceBytes(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—'
  return fmtBytes(Math.max(0, Number(value)))
}

function fmtReferencePercent(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—'
  return `${(Math.max(0, Number(value)) * 100).toFixed(1)}%`
}

function fmtCombinedReduction(snapshot: BackupSourceSnapshot) {
  if (!snapshot.storage_stats_available) return '—'
  if (snapshot.fully_reused) return t('protection.backupsPage.snapshotStorageFullyReused')
  const value = Number(snapshot.combined_reduction_ratio)
  if (!Number.isFinite(value) || value <= 0) return '—'
  return `${value.toFixed(2)} : 1`
}

function snapshotDisplayDirectories(snapshot: BackupSourceSnapshot) {
  const detail = snapshotDetails.value.get(snapshot.id)
  const directories = detail?.directories?.length ? detail.directories : snapshot.directories || []
  return directories.filter((dir) => dir.status === 'available')
}

function snapshotDisplaySize(snapshot: BackupSourceSnapshot) {
  const value = Number(snapshot.recoverable_size_bytes || snapshot.total_size_bytes || 0)
  if (value > 0) return value
  return snapshotDisplayDirectories(snapshot).reduce((sum, dir) => sum + Number(dir.size_bytes || 0), 0)
}

function snapshotDisplayFileCount(snapshot: BackupSourceSnapshot) {
  const value = Number(snapshot.file_count || 0)
  if (value > 0) return value
  return snapshotDisplayDirectories(snapshot).reduce((sum, dir) => sum + Number(dir.file_count || 0), 0)
}

function snapshotDisplayDirCount(snapshot: BackupSourceSnapshot) {
  const value = Number(snapshot.dir_count || 0)
  if (value > 0) return value
  return snapshotDisplayDirectories(snapshot).reduce((sum, dir) => sum + Number(dir.dir_count || 0), 0)
}

function boolStatusLabel(value?: boolean) {
  return value ? t('protection.policiesPage.statusOn') : t('protection.policiesPage.statusOff')
}

function detailStatePillClass(active: boolean | undefined) {
  return ['hfl-state-pill', active ? 'hfl-state-pill--enabled' : 'hfl-state-pill--disabled']
}

function policyUsageValue(count: number | undefined) {
  const n = Number(count) || 0
  if (messageLocale.value === 'en' && n === 1) return '1 Backup Source'
  return t('protection.policiesPage.appliedToBackupSourcesCount', { n })
}

function policyScheduleCycleValue(policy: BackupPolicy) {
  const f = backupPolicyToForm(policy)
  if (!f.sectionScheduleEnabled) return t('protection.policiesPage.sectionOffHint')
  if (f.freqMode === 'advanced') return `${t('protection.policiesPage.freqAdvanced')}: ${f.cronExpr}`
  if (f.quickScheduleType === 'interval') {
    const unitKey = f.simpleIntervalUnit === 'minute' ? 'unitMinutes' : f.simpleIntervalUnit === 'hour' ? 'unitHours' : 'unitDays'
    const unit = t(`protection.policiesPage.${unitKey}`)
    return t('protection.policiesPage.previewScheduleInterval', {
      n: f.simpleIntervalValue,
      unit: Number(f.simpleIntervalValue) === 1 ? unit.replace(/s$/, '') : unit,
    })
  }
  if (f.quickScheduleType === 'daily') {
    return t('protection.policiesPage.previewScheduleDaily', { time: f.scheduleTime })
  }
  if (f.quickScheduleType === 'weekly') {
    const weekdayKeys = ['weekdayMon', 'weekdayTue', 'weekdayWed', 'weekdayThu', 'weekdayFri', 'weekdaySat', 'weekdaySun']
    const weekdays = f.scheduleWeekdays.map((day) => t(`protection.policiesPage.${weekdayKeys[day - 1]}`)).join(', ')
    return t('protection.policiesPage.previewScheduleWeekly', { weekdays, time: f.scheduleTime })
  }
  const dates = f.scheduleMonthDays.map(String)
  if (f.scheduleMonthEnd) dates.push(t('protection.policiesPage.scheduleMonthEnd'))
  return t('protection.policiesPage.previewScheduleMonthly', { dates: dates.join(', '), time: f.scheduleTime })
}

function policyScheduleDetailRows(policy: BackupPolicy | null | undefined) {
  if (!policy) return []
  return [
    { label: t('protection.policiesPage.scheduleCycle'), value: policyScheduleCycleValue(policy) },
    { label: t('protection.policiesPage.scheduleTimezone'), value: policy.schedule?.timezone || 'UTC' },
    { label: t('protection.policiesPage.scheduleStartsAt'), value: policy.schedule?.starts_at?.replace('T', ' ') || t('protection.policiesPage.timeDash') },
  ]
}

function policyRetentionDetailLines(policy: BackupPolicy | null | undefined): PolicyRetentionDetailLine[] {
  if (!policy) return [{ text: t('protection.backupsPage.policyConfigNotConfigured') }]
  const f = backupPolicyToForm(policy)
  if (!f.sectionRetentionEnabled) {
    return [{ text: t('protection.backupsPage.policyConfigNotConfigured') }]
  }
  const hasRetentionValues =
    Number(f.retentionRecentPoints) > 0 ||
    f.retentionShortHourly ||
    f.retentionMidDaily ||
    f.retentionLongMonthly
  if (!hasRetentionValues && policy.retention_summary) {
    return [{ text: policy.retention_summary }]
  }

  const recentPoints = Number(f.retentionRecentPoints)
  const lines: PolicyRetentionDetailLine[] = [{
    text: t(
      recentPoints === 1
        ? 'protection.policiesPage.retentionLatestOne'
        : 'protection.policiesPage.retentionLatestMany',
      { n: recentPoints },
    ),
  }]
  if (f.retentionShortHourly) {
    lines.push({ text: t('protection.policiesPage.shortDesc', { days: f.retentionShortDaysMax }) })
  }
  if (f.retentionMidDaily) {
    lines.push({ text: t('protection.policiesPage.midDesc', { start: f.retentionShortDaysMax, end: f.retentionMidDaysMax }) })
  }
  if (f.retentionLongMonthly) {
    lines.push({ text: t('protection.policiesPage.longDesc', { day: f.retentionMidDaysMax, months: f.retentionLongMonths }) })
  }
  return lines
}

function policyAdvancedDetailLines(policy: BackupPolicy | null | undefined) {
  if (!policy) return []
  const f = backupPolicyToForm(policy)
  return [
    { label: t('protection.policiesPage.errRow1Title'), enabled: f.errorIgnoreDirectory },
    { label: t('protection.policiesPage.errRow2Title'), enabled: f.errorIgnoreFile },
    { label: t('protection.policiesPage.errRow3Title'), enabled: f.errorIgnoreUnknownEntries },
  ]
}

function fileFilterLargeFileLabel(filter: FileFilterRule) {
  return filter.large_file_limit_enabled
    ? fmtBytes(Number(filter.large_file_bytes_max) || 0)
    : t('protection.policiesPage.previewNoLimit')
}

function fileFilterMaxSizeLimitValue(filter: FileFilterRule) {
  return `>${fileFilterLargeFileLabel(filter)}`
}

function fileFilterFormView(filter: FileFilterRule): FileFilterRuleForm {
  const form = createEmptyFileFilterForm()
  form.largeFileLimitEnabled = Boolean(filter.large_file_limit_enabled)
  form.currentFilesystemOnly = Boolean(filter.current_filesystem_only)
  applyFilterIgnorePatternsToForm(form, filter.ignore_patterns || '')
  applyLegacyFileFilterCacheFlag(form, Boolean(filter.ignore_cache_directories))
  return form
}

function fileFilterCompiledRuleLines(filter: FileFilterRule | null | undefined) {
  if (!filter) return []
  return compileFilterIgnorePatterns(fileFilterFormView(filter))
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
}

function snapshotStatusLabel(status?: string): string {
  const normalized = String(status || '').toLowerCase()
  if (normalized === 'available') return t('protection.backupsPage.snapshotStatusAvailable')
  if (normalized === 'partial') return t('protection.backupsPage.snapshotStatusPartial')
  if (normalized === 'failed') return t('protection.backupsPage.snapshotStatusFailed')
  if (normalized === 'creating') return t('protection.backupsPage.snapshotStatusCreating')
  if (normalized === 'deleted') return t('protection.backupsPage.snapshotStatusDeleted')
  if (normalized === 'deleting') return t('protection.backupsPage.snapshotStatusDeleting')
  if (normalized === 'delete_failed') return t('protection.backupsPage.snapshotStatusDeleteFailed')
  return status || t('protection.backupDetail.durationDash')
}

function isVisibleSourceSnapshot(snapshot: BackupSourceSnapshot) {
  return !HIDDEN_SOURCE_SNAPSHOT_STATUSES.includes(String(snapshot.status || '').toLowerCase())
}

function canBrowseSnapshotDirectory(dir: BackupSourceSnapshotDirectory) {
  return isSnapshotDirectoryBrowsable(selectedSnapshot.value?.status, dir)
}

function snapshotDirectoryKind(dir: BackupSourceSnapshotDirectory) {
  return dir.path_type === 'file' ? 'file' : 'dir'
}

function snapshotDirectoryIcon(dir: BackupSourceSnapshotDirectory) {
  return snapshotDirectoryKind(dir) === 'file' ? File : Folder
}

function snapshotFileFallbackName(dir: BackupSourceSnapshotDirectory) {
  return dir.display_name || dir.source_path.split(/[\\/]/).filter(Boolean).pop() || 'snapshot-file'
}

function clearSnapshotFileSelection() {
  selectedSnapshotFileChecked.value = false
  downloadingSnapshotFile.value = false
}

function openSnapshotFileBrowser(dir: BackupSourceSnapshotDirectory) {
  if (!canBrowseSnapshotDirectory(dir)) return
  selectedSnapshotDirectory.value = dir
  fileBrowserDrawerOpen.value = true
  browserLoading.value = false
  browserError.value = ''
  browserPath.value = ''
  browserParentPath.value = ''
  browserEntries.value = []
  browserTreeEntries.value = []
  browserTreeVersion.value += 1
  selectedBrowserPaths.value = new Set()
  browserTreeRef.value?.setCheckedKeys([])
  clearSnapshotFileSelection()
}

function toggleSnapshotFileSelection() {
  selectedSnapshotFileChecked.value = !selectedSnapshotFileChecked.value
}

async function downloadSelectedSnapshotFile() {
  const dir = selectedSnapshotDirectory.value
  if (!dir || !selectedSnapshotFileChecked.value || !selectedSnapshotDirectoryIsFile.value) {
    ElMessage.warning({ message: t('protection.backupsPage.snapshotBrowserSelectBeforeDownload'), grouping: true })
    return
  }
  downloadingSnapshotFile.value = true
  try {
    const task = await createBackupSnapshotDirectoryDownloadTask(dir.id, '')
    const artifactId = await waitForDownloadArtifact(task.task_uuid)
    await startNativeArtifactDownload(artifactId)
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
  } finally {
    downloadingSnapshotFile.value = false
  }
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

function closeSnapshotFileBrowser() {
  fileBrowserDrawerOpen.value = false
  clearSnapshotFileSelection()
}

function resetSnapshotBrowser() {
  selectedSnapshotDirectory.value = null
  fileBrowserDrawerOpen.value = false
  browserLoading.value = false
  browserError.value = ''
  browserPath.value = ''
  browserParentPath.value = ''
  browserEntries.value = []
  browserTreeEntries.value = []
  browserTreeVersion.value += 1
  selectedBrowserPaths.value = new Set()
  downloadingSelected.value = false
  clearSnapshotFileSelection()
}

async function selectSnapshot(row: BackupSourceSnapshot) {
  selectedSnapshotId.value = row.id
  resetSnapshotBrowser()
  snapshotDetailLoading.value = true
  snapshotDetailError.value = ''
  try {
    const detail = await getBackupSourceSnapshot(row.id)
    snapshotDetails.value.set(row.id, detail)
  } catch (err) {
    snapshotDetailError.value = apiErrorMessage(err, t('errors.generic.loadFailed'))
  } finally {
    snapshotDetailLoading.value = false
  }
}

async function expandSnapshot(row: BackupSourceSnapshot) {
  expandedSnapshotRowKeys.value = [row.id]
  await selectSnapshot(row)
}

function toggleSnapshot(row: BackupSourceSnapshot) {
  if (expandedSnapshotRowKeys.value.includes(row.id)) {
    expandedSnapshotRowKeys.value = []
    selectedSnapshotId.value = null
    resetSnapshotBrowser()
    return
  }
  void expandSnapshot(row)
}

function canRestoreSnapshot(row: BackupSourceSnapshot) {
  return !props.restoreBlockedByRestore && isSnapshotRestorable(row)
}

function snapshotRestoreDisabledReason(row: BackupSourceSnapshot) {
  if (props.restoreBlockedByRestore) {
    return t('protection.backupsPage.msgRestoreAlreadyRunning')
  }
  const status = String(row.status || '').toLowerCase()
  if (status !== 'available' && status !== 'partial') {
    return t('protection.backupsPage.snapshotReasonStatusUnavailable')
  }
  return t('protection.backupsPage.snapshotReasonNoUsableDirectories')
}

function openSnapshotRestore(row: BackupSourceSnapshot) {
  if (!canRestoreSnapshot(row)) return
  emit('restore-snapshot', { snapshotId: row.id })
}

function onSnapshotExpandChange(row: BackupSourceSnapshot, expandedRows: BackupSourceSnapshot[]) {
  const expanded = expandedRows.some((item) => item.id === row.id)
  if (!expanded) {
    expandedSnapshotRowKeys.value = expandedRows.map((item) => item.id)
    if (selectedSnapshotId.value === row.id) {
      selectedSnapshotId.value = null
      resetSnapshotBrowser()
    }
    return
  }
  void expandSnapshot(row)
}

async function openSnapshotDirectory(dir: BackupSourceSnapshotDirectory, path = '') {
  if (!canBrowseSnapshotDirectory(dir)) return
  if (snapshotDirectoryKind(dir) === 'file') {
    openSnapshotFileBrowser(dir)
    return
  }
  clearSnapshotFileSelection()
  const previousDirectory = selectedSnapshotDirectory.value
  const previousPath = browserPath.value
  const previousParentPath = browserParentPath.value
  const previousEntries = browserEntries.value
  const previousTreeEntries = browserTreeEntries.value
  selectedSnapshotDirectory.value = dir
  fileBrowserDrawerOpen.value = true
  browserLoading.value = true
  browserError.value = ''
  browserEntries.value = []
  browserTreeEntries.value = []
  browserTreeVersion.value += 1
  selectedBrowserPaths.value = new Set()
  browserTreeRef.value?.setCheckedKeys([])
  try {
    const result = await browseBackupSnapshotDirectory(dir.id, { path })
    browserPath.value = result.path || ''
    browserParentPath.value = result.parent_path || ''
    browserEntries.value = result.entries
    browserTreeEntries.value = result.entries.map((entry) => browserEntryToTreeNode(entry))
    browserTreeVersion.value += 1
    refreshBrowserTreeDisabled()
  } catch (err) {
    browserError.value = apiErrorMessage(err, t('errors.generic.loadFailed'))
    if (previousDirectory?.id === dir.id && (previousPath || previousEntries.length)) {
      browserPath.value = previousPath
      browserParentPath.value = previousParentPath
      browserEntries.value = previousEntries
      browserTreeEntries.value = previousTreeEntries
      browserTreeVersion.value += 1
    } else {
      browserPath.value = ''
      browserParentPath.value = ''
      browserEntries.value = []
      browserTreeEntries.value = []
      browserTreeVersion.value += 1
    }
  } finally {
    browserLoading.value = false
  }
}

function browserEntryToTreeNode(entry: BackupSnapshotBrowserEntry): SnapshotBrowserTreeNode {
  return {
    ...entry,
    id: entry.path,
    label: entry.name,
    disabled: isBrowserPathDisabled(entry.path),
    loaded: entry.type !== 'dir',
    isLeaf: entry.type !== 'dir',
    children: entry.type === 'dir' ? [] : undefined,
  }
}

function isRelatedBrowserPath(a: string, b: string) {
  return a === b || a.startsWith(`${b}/`) || b.startsWith(`${a}/`)
}

function isBrowserPathDisabled(path: string) {
  for (const selectedPath of selectedBrowserPaths.value) {
    if (selectedPath !== path && isRelatedBrowserPath(path, selectedPath)) return true
  }
  return false
}

function refreshBrowserTreeDisabled(nodes: SnapshotBrowserTreeNode[] = browserTreeEntries.value) {
  for (const node of nodes) {
    node.disabled = isBrowserPathDisabled(node.path)
    if (node.children?.length) refreshBrowserTreeDisabled(node.children)
  }
  browserTreeEntries.value = [...browserTreeEntries.value]
}

async function loadBrowserTreeNode(node: { data?: SnapshotBrowserTreeNode; level: number }, resolve: (data: SnapshotBrowserTreeNode[]) => void) {
  if (node.level === 0) {
    resolve(browserTreeEntries.value)
    return
  }
  const data = node.data
  if (!data || data.type !== 'dir' || !selectedSnapshotDirectory.value) {
    resolve([])
    return
  }
  try {
    const result = await browseBackupSnapshotDirectory(selectedSnapshotDirectory.value.id, { path: data.path })
    const children = result.entries.map((entry) => browserEntryToTreeNode(entry))
    data.children = children
    data.loaded = true
    resolve(children)
    refreshBrowserTreeDisabled()
  } catch (err) {
    data.loaded = false
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.loadFailed')), grouping: true })
    resolve([])
  }
}

function syncBrowserTreeCheckedKeys() {
  browserTreeRef.value?.setCheckedKeys(selectedBrowserPathList.value)
}

function normalizeBrowserDownloadPaths(paths: string[]): string[] {
  const sorted = [...paths].sort((a, b) => a.length - b.length)
  const kept: string[] = []
  for (const path of sorted) {
    if (kept.some((parent) => path === parent || path.startsWith(`${parent}/`))) continue
    kept.push(path)
  }
  return kept.sort()
}

function clearBrowserSelection() {
  selectedBrowserPaths.value = new Set()
  refreshBrowserTreeDisabled()
  syncBrowserTreeCheckedKeys()
}

function onBrowserTreeCheckChange(data: SnapshotBrowserTreeNode, checked: boolean) {
  const next = new Set(selectedBrowserPaths.value)
  if (!checked) {
    next.delete(data.path)
  } else {
    for (const path of Array.from(next)) {
      if (isRelatedBrowserPath(data.path, path)) next.delete(path)
    }
    next.add(data.path)
  }
  selectedBrowserPaths.value = next
  refreshBrowserTreeDisabled()
  syncBrowserTreeCheckedKeys()
}

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function artifactIdFromTask(task: { result_payload?: unknown }) {
  const payload = task.result_payload && typeof task.result_payload === 'object'
    ? task.result_payload as Record<string, unknown>
    : {}
  const id = Number(payload.artifact_id || 0)
  return Number.isFinite(id) && id > 0 ? id : 0
}

async function waitForDownloadArtifact(taskUuid: string) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const task = await getTask(taskUuid)
    if (task.status === 'success') {
      const artifactId = artifactIdFromTask(task)
      if (artifactId > 0) return artifactId
      throw new Error(t('protection.backupsPage.snapshotBrowserDownloadNotReady'))
    }
    if (task.status === 'failed' || task.status === 'cancelled' || task.status === 'timeout') {
      throw new Error(task.error_message || t('protection.backupsPage.snapshotBrowserDownloadFailed'))
    }
    await wait(1000)
  }
  throw new Error(t('protection.backupsPage.snapshotBrowserDownloadTimeout'))
}

async function loadOverviewForSource(options: { silent?: boolean } = {}) {
  const silent = options.silent === true
  const id = sourceId.value
  if (!silent) {
    sourceDetail.value = null
    sourceDetailError.value = ''
  }
  if (!id) return
  const signal = requests.nextSignal('flow-source-overview')
  if (!silent) sourceDetailLoading.value = true
  try {
    const result = await listBackupSelectableSources({
      ids: id,
      expand: 'backup_configs,policies,runtime',
    }, { signal })
    sourceDetail.value = result.results.find((item) => item.id === id) ?? null
    if (!sourceDetail.value && !silent) sourceDetailError.value = t('protection.backupDetail.notFound')
  } catch (err) {
    if (requests.isAbortError(err)) return
    if (!silent) sourceDetailError.value = apiErrorMessage(err)
  } finally {
    requests.releaseSignal('flow-source-overview', signal)
    if (!silent && !signal.aborted) sourceDetailLoading.value = false
  }
}

async function loadSnapshotsForSource() {
  const endpoint = sourceEndpoint.value
  sourceSnapshotRows.value = []
  // Tab activation refreshes the list while its controlled expansion state is retained.
  // Keep loaded details too, so a retained expanded row never falls back to an empty summary row.
  sourceSnapshotsError.value = ''
  if (!endpoint) {
    snapshotPagination.count = 0
    return
  }
  const signal = requests.nextSignal('flow-source-snapshots')
  sourceSnapshotsLoading.value = true
  try {
    const startedRange = detailTimeRangeParams(snapshotFilterTimeMode.value, snapshotFilterDateRange.value)
    const result = await listBackupSourceSnapshots({
      page: snapshotPagination.page,
      page_size: snapshotPagination.pageSize,
      snapshot_uid: appliedSnapshotFilterId.value || undefined,
      source_type: endpoint.sourceType,
      source_ref_id: endpoint.sourceRefId,
      status: snapshotFilterStatus.value || undefined,
      exclude_status: snapshotFilterStatus.value ? undefined : HIDDEN_SOURCE_SNAPSHOT_STATUSES.join(','),
      started_from: startedRange.from,
      started_to: startedRange.to,
      ordering: '-created_at',
    }, { signal })
    sourceSnapshotRows.value = result.results.filter(isVisibleSourceSnapshot)
    snapshotPagination.count = result.count
  } catch (err) {
    if (requests.isAbortError(err)) return
    sourceSnapshotsError.value = apiErrorMessage(err, t('errors.generic.loadFailed'))
    snapshotPagination.count = 0
  } finally {
    requests.releaseSignal('flow-source-snapshots', signal)
    if (!signal.aborted) sourceSnapshotsLoading.value = false
  }
}

async function downloadSelectedBrowserPaths() {
  if (!selectedSnapshotDirectory.value) return
  const paths = normalizeBrowserDownloadPaths(selectedBrowserPathList.value)
  if (!paths.length) {
    ElMessage.warning({ message: t('protection.backupsPage.snapshotBrowserSelectBeforeDownload'), grouping: true })
    return
  }
  downloadingSelected.value = true
  try {
    const task = await createBackupSnapshotDirectoryBatchDownloadTask(selectedSnapshotDirectory.value.id, paths)
    const artifactId = await waitForDownloadArtifact(task.task_uuid)
    await startNativeArtifactDownload(artifactId)
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
  } finally {
    downloadingSelected.value = false
  }
}

function restoreRecordMatchesEndpoint(
  record: RestoreRecord,
  endpoint: { sourceType: RestoreEndpointType; sourceRefId: number },
) {
  if (record.source_type === endpoint.sourceType
    && Number(record.source_ref_id) === endpoint.sourceRefId) return true
  const configId = Number(record.backup_config_id || 0)
  return configId > 0 && props.backupConfigs.some((config) => (
    config.id === configId
    && config.source_type === endpoint.sourceType
    && Number(config.source_ref_id) === endpoint.sourceRefId
  ))
}

async function resolveTargetedRestoreRecord(
  records: RestoreRecord[],
  endpoint: { sourceType: RestoreEndpointType; sourceRefId: number },
  signal: AbortSignal,
) {
  const targetId = Number(props.initialRestoreRecordId || 0)
  if (!targetId) {
    targetedRestoreRecord.value = null
    return null
  }
  const existing = records.find((record) => record.id === targetId && restoreRecordMatchesEndpoint(record, endpoint))
  if (existing) {
    targetedRestoreRecord.value = null
    return existing
  }
  if (targetedRestoreRecord.value?.id === targetId
    && restoreRecordMatchesEndpoint(targetedRestoreRecord.value, endpoint)) {
    return targetedRestoreRecord.value
  }
  const taskUuid = String(props.initialRestoreRecordTaskUuid || '').trim()
  if (!taskUuid) {
    targetedRestoreRecord.value = null
    return null
  }
  const result = await listRestoreRecords({ page: 1, page_size: 1, task_uuid: taskUuid }, { signal })
  const target = result.results.find((record) => (
    record.id === targetId && restoreRecordMatchesEndpoint(record, endpoint)
  )) ?? null
  targetedRestoreRecord.value = target
  return target
}

async function loadRestoreRecordsForSource(options: { silent?: boolean } = {}) {
  const silent = options.silent === true
  if (silent && restoreRecordsSilentRefreshing.value) return
  const endpoint = sourceEndpoint.value
  if (!silent) {
    restoreRecords.value = []
    restoreRecordsError.value = ''
  }
  if (!endpoint) {
    restoreRecordPagination.count = 0
    return
  }
  const signal = requests.nextSignal('flow-source-restore-records')
  if (silent) restoreRecordsSilentRefreshing.value = true
  else restoreRecordsLoading.value = true
  try {
    const createdRange = detailTimeRangeParams(restoreRecordFilterTimeMode.value, restoreRecordFilterDateRange.value)
    const result = await listRestoreRecords({
      page: restoreRecordPagination.page,
      page_size: restoreRecordPagination.pageSize,
      source_type: endpoint.sourceType,
      source_ref_id: endpoint.sourceRefId,
      search: appliedRestoreRecordFilterQuery.value || undefined,
      search_fields: restoreRecordSearchField.value,
      status: restoreRecordFilterStatus.value || undefined,
      source_mode: restoreRecordFilterSourceMode.value || undefined,
      created_from: createdRange.from,
      created_to: createdRange.to,
    }, { signal })
    restoreRecords.value = result.results
    let target: RestoreRecord | null = null
    if (hasRestoreRecordFilters.value) targetedRestoreRecord.value = null
    else target = await resolveTargetedRestoreRecord(result.results, endpoint, signal)
    const currentRecordIds = new Set(displayedRestoreRecords.value.map((record) => record.id))
    if (expandedRestoreRecordRowKeys.value.some((id) => !currentRecordIds.has(id))) {
      resetExpandedRestoreItems()
    }
    if (target && appliedRestoreRecordTargetId.value !== target.id) {
      appliedRestoreRecordTargetId.value = target.id
      expandedRestoreRecordRowKeys.value = [target.id]
      void loadRestoreRecordRuntime(target)
    }
    restoreRecordPagination.count = result.count
  } catch (err) {
    if (requests.isAbortError(err)) return
    if (!silent) {
      restoreRecordsError.value = apiErrorMessage(err, t('errors.generic.loadFailed'))
      restoreRecordPagination.count = 0
    }
  } finally {
    requests.releaseSignal('flow-source-restore-records', signal)
    if (silent) restoreRecordsSilentRefreshing.value = false
    else if (!signal.aborted) restoreRecordsLoading.value = false
  }
}

function stopRestoreRecordPolling() {
  if (!restoreRecordPollingTimer) return
  clearInterval(restoreRecordPollingTimer)
  restoreRecordPollingTimer = null
}

function syncRestoreRecordPolling() {
  stopRestoreRecordPolling()
  if (!props.modelValue || activeTab.value !== 'restoreRecords' || !hasActiveRestoreRecords.value) return
  restoreRecordPollingTimer = setInterval(() => {
    void loadRestoreRecordsForSource({ silent: true })
    const expandedId = expandedRestoreRecordRowKeys.value[0]
    const expandedRecord = displayedRestoreRecords.value.find((record) => record.id === expandedId)
    if (expandedRecord) void loadRestoreRecordRuntime(expandedRecord)
  }, RESTORE_RECORD_POLL_INTERVAL_MS)
}

function stopProvisionPolling() {
  if (provisionPollingTimer) {
    clearInterval(provisionPollingTimer)
    provisionPollingTimer = null
  }
  provisionPollingInFlight = false
  provisionPollingAttempts = 0
}

async function pollProvisionStatus() {
  if (provisionPollingInFlight || !props.modelValue || activeTab.value !== 'overview') return
  if (provisionPollingAttempts >= PROVISION_POLL_MAX_ATTEMPTS) {
    stopProvisionPolling()
    return
  }
  provisionPollingInFlight = true
  provisionPollingAttempts += 1
  try {
    await loadOverviewForSource({ silent: true })
    if (currentSourceConfig.value?.status !== 'provisioning') {
      emit('config-changed')
      stopProvisionPolling()
    }
  } finally {
    provisionPollingInFlight = false
  }
}

function syncProvisionPolling() {
  stopProvisionPolling()
  if (!props.modelValue || activeTab.value !== 'overview') return
  if (currentSourceConfig.value?.status !== 'provisioning') return
  provisionPollingTimer = setInterval(() => {
    void pollProvisionStatus()
  }, PROVISION_POLL_INTERVAL_MS)
}

async function loadTasksForSource() {
  const endpoint = sourceEndpoint.value
  sourceTaskRows.value = []
  sourceTasksError.value = ''
  if (!endpoint) {
    sourceTasksTotal.value = 0
    return
  }
  const signal = requests.nextSignal('flow-source-tasks')
  sourceTasksLoading.value = true
  try {
    const timeParams = sourceTaskCreatedRangeParams()
    const result = await listTasks({
      page: taskPagination.page,
      page_size: taskPagination.pageSize,
      resource_type: 'backup_source',
      resource_subtype: endpoint.sourceType,
      resource_id: endpoint.sourceRefId,
      search: appliedTaskFilterId.value.trim(),
      search_field: taskFilterSearchField.value,
      task_type: taskFilterType.value,
      exclude_insight_workspace_restores: 'true',
      status: taskFilterStatus.value,
      created_after: timeParams.created_after,
      created_before: timeParams.created_before,
    }, { signal })
    sourceTaskRows.value = result.results
    sourceTasksTotal.value = result.count
  } catch (err) {
    if (requests.isAbortError(err)) return
    sourceTasksError.value = apiErrorMessage(err, t('errors.generic.loadFailed'))
    sourceTasksTotal.value = 0
  } finally {
    requests.releaseSignal('flow-source-tasks', signal)
    if (!signal.aborted) sourceTasksLoading.value = false
  }
}

function refreshSourceDetailData() {
  if (!props.modelValue || !sourceId.value) return
  void refreshFlowSourceDetailTab(activeTab.value, {
    overview: loadOverviewForSource,
    snapshots: loadSnapshotsForSource,
    restoreRecords: loadRestoreRecordsForSource,
    tasks: loadTasksForSource,
  })
}

function scrollToSection() {
  if (!props.scrollTo) return
  const el = props.scrollTo === 'dirs' ? dirsSectionRef.value : targetsSectionRef.value
  el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function applyInitialTabs() {
  const raw = props.initialTab
  if (raw === 'restore') {
    activeTab.value = 'restoreRecords'
    activeTaskSubTab.value = raw
    return
  }
  if (raw === 'executions') {
    activeTab.value = 'tasks'
    activeTaskSubTab.value = raw
    return
  }
  if (raw === 'configs') {
    activeTab.value = 'overview'
    activeTaskSubTab.value = 'history'
    return
  }
  activeTab.value = raw
  if (raw === 'tasks') {
    activeTaskSubTab.value = props.initialTaskSubTab
  }
}

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return
    applyInitialTabs()
    await nextTick()
    if (activeTab.value === 'overview' && props.scrollTo) {
      scrollToSection()
    }
    if (props.initialTaskUuid) void openTaskDetailByUuid(props.initialTaskUuid)
  },
  { immediate: true },
)

watch(
  () => [props.modelValue, sourceId.value, activeTab.value] as const,
  ([open]) => {
    if (!open) return
    refreshSourceDetailData()
  },
  { immediate: true },
)

watch(activeTab, async () => {
  await nextTick()
  if (activeTab.value === 'overview' && props.scrollTo) scrollToSection()
})

watch(
  () => [props.modelValue, activeTab.value, hasActiveRestoreRecords.value] as const,
  syncRestoreRecordPolling,
  { immediate: true },
)

watch(
  () => [props.modelValue, activeTab.value, currentSourceConfig.value?.status] as const,
  syncProvisionPolling,
  { immediate: true },
)

watch(
  () => [
    props.initialTab,
    props.initialTaskSubTab,
    props.scrollTo,
    props.initialTaskUuid,
    props.initialRestoreRecordId,
    props.initialRestoreRecordTaskUuid,
  ] as const,
  async () => {
    if (!props.modelValue) return
    applyInitialTabs()
    targetedRestoreRecord.value = null
    appliedRestoreRecordTargetId.value = null
    resetExpandedRestoreItems()
    await nextTick()
    if (activeTab.value === 'overview' && props.scrollTo) scrollToSection()
    if (props.initialTaskUuid) void openTaskDetailByUuid(props.initialTaskUuid)
    if (activeTab.value === 'restoreRecords') void loadRestoreRecordsForSource()
  },
)

/* Pagination and filter changes are explicit refreshes within the active tab. */
function reloadSnapshotsFromFirstPage() {
  if (!props.modelValue || activeTab.value !== 'snapshots') return
  if (snapshotPagination.page !== 1) {
    snapshotPagination.page = 1
    return
  }
  void loadSnapshotsForSource()
}

function reloadRestoreRecordsFromFirstPage() {
  if (!props.modelValue || activeTab.value !== 'restoreRecords') return
  if (restoreRecordPagination.page !== 1) {
    restoreRecordPagination.page = 1
    return
  }
  void loadRestoreRecordsForSource()
}

watch(
  () => [restoreRecordPagination.page, restoreRecordPagination.pageSize] as const,
  () => {
    resetExpandedRestoreItems()
    if (props.modelValue && activeTab.value === 'restoreRecords') void loadRestoreRecordsForSource()
  },
)

watch(
  () => [
    snapshotFilterStatus.value,
    snapshotFilterTimeMode.value,
    snapshotFilterDateRange.value,
  ] as const,
  runSnapshotSearchNow,
)

watch(
  () => [
    restoreRecordFilterStatus.value,
    restoreRecordFilterSourceMode.value,
    restoreRecordFilterTimeMode.value,
    restoreRecordFilterDateRange.value,
  ] as const,
  runRestoreRecordSearchNow,
)

/*
 * Keep the watcher declarations below separate: task search is debounced, while
 * table pagination must request the selected page immediately.
 */
function reloadSourceTasksFromFirstPage() {
  if (!props.modelValue || activeTab.value !== 'tasks') return
  if (taskPagination.page !== 1) {
    taskPagination.page = 1
    return
  }
  void loadTasksForSource()
}

watch(
  () => [
    taskFilterType.value,
    taskFilterStatus.value,
    taskFilterTimeMode.value,
    taskFilterDateRange.value,
  ] as const,
  runSourceTaskSearchNow,
)

watch(
  () => [taskPagination.page, taskPagination.pageSize] as const,
  () => {
    if (props.modelValue && activeTab.value === 'tasks') void loadTasksForSource()
  },
)

watch(
  () => [snapshotPagination.page, snapshotPagination.pageSize] as const,
  () => {
    selectedSnapshotId.value = null
    expandedSnapshotRowKeys.value = []
    snapshotDetails.value = new Map()
    resetSnapshotBrowser()
    if (props.modelValue && activeTab.value === 'snapshots') void loadSnapshotsForSource()
  },
)

watch(sourceId, () => {
  sourceDetail.value = null
  sourceDetailError.value = ''
  selectedSnapshotId.value = null
  expandedSnapshotRowKeys.value = []
  snapshotDetails.value = new Map()
  snapshotPagination.page = 1
  snapshotPagination.pageSize = DETAIL_PAGE_SIZE
  snapshotPagination.count = 0
  sourceSnapshotRows.value = []
  sourceSnapshotsError.value = ''
  resetSnapshotSearch()
  snapshotFilterStatus.value = ''
  snapshotFilterTimeMode.value = 'all'
  snapshotFilterDateRange.value = null
  restoreRecords.value = []
  restoreRecordsError.value = ''
  resetRestoreRecordSearch()
  restoreRecordSearchField.value = 'restore_uid'
  restoreRecordFilterStatus.value = ''
  restoreRecordFilterSourceMode.value = ''
  restoreRecordFilterTimeMode.value = 'all'
  restoreRecordFilterDateRange.value = null
  targetedRestoreRecord.value = null
  appliedRestoreRecordTargetId.value = null
  restoreRecordRuntimeById.value = new Map()
  restoreRecordRuntimeLoadingIds.value = new Set()
  resetExpandedRestoreItems()
  restoreRecordPagination.page = 1
  restoreRecordPagination.pageSize = DETAIL_PAGE_SIZE
  restoreRecordPagination.count = 0
  sourceTaskRows.value = []
  sourceTasksError.value = ''
  sourceTasksTotal.value = 0
  resetSourceTaskSearch()
  taskFilterSearchField.value = 'name'
  taskFilterType.value = ''
  taskFilterStatus.value = ''
  taskFilterTimeMode.value = 'all'
  taskFilterDateRange.value = null
  resetSourceTaskAdvancedFilterDraft()
  taskAdvancedFilterOpen.value = false
  taskPagination.page = 1
  taskPagination.pageSize = DETAIL_PAGE_SIZE
  resetSnapshotBrowser()
})

watch(activeTaskUuid, () => {
  syncActiveTransferProgressPolling()
})

watch(
  () => activeTask.value?.status,
  (status) => {
    if (!status || !['success', 'failed', 'cancelled', 'timeout'].includes(status)) return
    const payload = activeTask.value?.result_payload
    const snapshotId = payload && typeof payload === 'object' && !Array.isArray(payload)
      ? Number((payload as Record<string, unknown>).source_snapshot_id || 0)
      : 0
    if (Number.isFinite(snapshotId) && snapshotId > 0) {
      snapshotDetails.value.delete(snapshotId)
      if (selectedSnapshotId.value === snapshotId) {
        void selectSnapshot({ id: snapshotId } as BackupSourceSnapshot)
      }
    }
    if (activeTask.value) void loadActiveBackupSnapshot(activeTask.value)
  },
)

onUnmounted(() => {
  if (activeTransferProgressTimer) clearInterval(activeTransferProgressTimer)
  stopRestoreRecordPolling()
  stopProvisionPolling()
})

function onClosed() {
  stopRestoreRecordPolling()
  stopProvisionPolling()
  requests.abortScope('flow-source-overview')
  requests.abortScope('flow-source-snapshots')
  requests.abortScope('flow-source-restore-records')
  requests.abortScope('flow-source-restore-record-runtime')
  requests.abortScope('flow-source-tasks')
  activeTab.value = 'overview'
  activeTaskSubTab.value = 'history'
  sourceDetail.value = null
  sourceDetailError.value = ''
  selectedSnapshotId.value = null
  expandedSnapshotRowKeys.value = []
  sourceSnapshotRows.value = []
  sourceSnapshotsError.value = ''
  resetSnapshotSearch()
  snapshotFilterStatus.value = ''
  snapshotFilterTimeMode.value = 'all'
  snapshotFilterDateRange.value = null
  snapshotPagination.page = 1
  snapshotPagination.pageSize = DETAIL_PAGE_SIZE
  snapshotPagination.count = 0
  restoreRecords.value = []
  restoreRecordsError.value = ''
  resetRestoreRecordSearch()
  restoreRecordSearchField.value = 'restore_uid'
  restoreRecordFilterStatus.value = ''
  restoreRecordFilterSourceMode.value = ''
  restoreRecordFilterTimeMode.value = 'all'
  restoreRecordFilterDateRange.value = null
  targetedRestoreRecord.value = null
  appliedRestoreRecordTargetId.value = null
  restoreRecordRuntimeById.value = new Map()
  restoreRecordRuntimeLoadingIds.value = new Set()
  resetExpandedRestoreItems()
  restoreRecordPagination.page = 1
  restoreRecordPagination.pageSize = DETAIL_PAGE_SIZE
  restoreRecordPagination.count = 0
  sourceTaskRows.value = []
  sourceTasksError.value = ''
  sourceTasksTotal.value = 0
  taskPagination.page = 1
  taskPagination.pageSize = DETAIL_PAGE_SIZE
  taskDetailOpen.value = false
  closeTaskDetail()
  resetSnapshotBrowser()
  snapshotDetails.value = new Map()
  activeBackupSnapshot.value = null
  emit('closed')
}

</script>

<template>
  <el-drawer
    v-model="drawerOpen"
    direction="rtl"
    :size="drawerSize"
    class="hfl-detail-drawer dp-flow-source-detail-drawer"
    :z-index="3000"
    @closed="onClosed"
  >
    <template
      v-if="source && aggregate"
      #header
    >
      <div class="dp-flow-source-detail-drawer__header min-w-0">
        <div class="flex flex-wrap items-center gap-2">
          <h2 class="hfl-detail-drawer__title m-0 truncate">
            {{ source.name }}
          </h2>
          <span
            class="dp-flow-type-pill shrink-0"
            :class="flowSourceTypeClass(source)"
          >
            <span class="dp-flow-type-pill__main">{{ flowSourceTypeParts(source).main }}</span>
            <span
              v-if="flowSourceTypeParts(source).suffix"
              class="dp-flow-type-pill__suffix"
            >
              {{ flowSourceTypeParts(source).suffix }}
            </span>
          </span>
          <el-tag
            size="small"
            :type="flowSourceStatusTagType(source.availability)"
          >
            {{ flowSourceStatusLabel(source.availability) }}
          </el-tag>
        </div>
        <p class="m-0 mt-1 truncate text-sm text-slate-500">
          {{ flowSourceNodeParts(source).name }}
          <template v-if="flowSourceNodeParts(source).ip">
            · {{ flowSourceNodeParts(source).ip }}
          </template>
        </p>
      </div>
    </template>

    <div
      v-if="source && aggregate"
      class="hfl-detail-drawer__body dp-flow-source-detail-drawer__body"
    >
      <el-tabs
        v-model="activeTab"
        class="hfl-detail-tabs dp-flow-source-detail-tabs"
      >
        <el-tab-pane
          :label="t('protection.backupsPage.flowSourceDetailTabOverview')"
          name="overview"
        >
          <div class="hfl-detail-sections dp-flow-source-overview">
            <el-alert
              v-if="sourceDetailError"
              :title="sourceDetailError"
              type="error"
              show-icon
              :closable="false"
            />
            <template v-if="overviewSource">
              <section class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">
                  {{ t('protection.backupsPage.flowSourceDetailSectionMeta') }}
                </h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('protection.backupsPage.flowSourceDetailBackupSource') }}</span>
                    <span class="hfl-detail-row__value dp-flow-source-info-value">
                      <span class="dp-flow-source-info-name">{{ overviewSource.name }}</span>
                      <span
                        class="dp-flow-type-pill shrink-0"
                        :class="flowSourceTypeClass(overviewSource)"
                      >
                        <span class="dp-flow-type-pill__main">{{ flowSourceTypeParts(overviewSource).main }}</span>
                        <span
                          v-if="flowSourceTypeParts(overviewSource).suffix"
                          class="dp-flow-type-pill__suffix"
                        >
                          {{ flowSourceTypeParts(overviewSource).suffix }}
                        </span>
                      </span>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('protection.backupsPage.flowSourceDetailConnectivity') }}</span>
                    <span class="hfl-detail-row__value">
                      <el-tag
                        size="small"
                        :type="flowSourceStatusTagType(overviewSource.availability)"
                        :class="{ 'hfl-tag--neutral': flowSourceStatusTag(overviewSource.availability) === 'neutral' }"
                        effect="light"
                      >
                        {{ flowSourceStatusLabel(overviewSource.availability) }}
                      </el-tag>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('protection.backupsPage.flowSourceDetailSourceStatus') }}</span>
                    <span class="hfl-detail-row__value">
                      <el-tag
                        size="small"
                        :type="flowSourceLifecycleStatusTag(overviewSource.status)"
                        effect="light"
                      >
                        {{ flowSourceLifecycleStatusLabel(overviewSource.status) }}
                      </el-tag>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ flowSourceSecondaryInfo(overviewSource).nameLabel }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--mono">{{ flowSourceSecondaryInfo(overviewSource).name }}</span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ flowSourceSecondaryInfo(overviewSource).ipLabel }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--mono">
                      {{ flowSourceSecondaryInfo(overviewSource).ip }}
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('protection.backupsPage.flowSourceDetailRegistered') }}</span>
                    <span class="hfl-detail-row__value">{{ flowSourceRegisteredAt(overviewSource.registeredAt) }}</span>
                  </div>
                </div>
              </section>

              <section class="hfl-detail-section dp-flow-config-section">
                <h4 class="hfl-detail-section__title">
                  {{ t('protection.backupsPage.flowSourceDetailTabConfigs') }}
                </h4>
                <div
                  v-if="currentSourceConfig"
                  class="dp-flow-config-detail-list"
                >
                  <el-alert
                    v-if="currentSourceConfig.status === 'provisioning' || currentSourceConfig.status === 'provision_failed'"
                    :title="currentProvisionAlertTitle"
                    :type="currentSourceConfig.status === 'provision_failed' ? 'error' : 'info'"
                    :closable="false"
                    show-icon
                  >
                    <p class="m-0">
                      {{ currentSourceConfig.provisioning_error_message || t('protection.backupsPage.provisionStatusWaitingDetail') }}
                    </p>
                    <el-button
                      v-if="currentSourceConfig.provisioning_error_code === 'AGENT_UPGRADE_REQUIRED'"
                      plain
                      size="small"
                      class="mt-3 mr-2"
                      @click="openCurrentProvisionAgent"
                    >
                      {{ t('protection.backupsPage.provisionViewAgent') }}
                    </el-button>
                    <el-button
                      v-if="currentSourceConfig.status === 'provision_failed'"
                      type="primary"
                      plain
                      size="small"
                      class="mt-3"
                      :loading="provisionRetrying"
                      @click="retryCurrentConfigProvision"
                    >
                      {{ t('protection.backupsPage.provisionRetry') }}
                    </el-button>
                    <el-button
                      v-if="currentSourceConfig.status === 'provision_failed'"
                      plain
                      size="small"
                      class="mt-3"
                      :loading="provisionDiscarding"
                      @click="discardCurrentFailedConfig"
                    >
                      {{ t('protection.backupsPage.provisionDiscard') }}
                    </el-button>
                  </el-alert>
                  <div class="dp-flow-config-subsections">
                    <section class="hfl-detail-section dp-flow-config-subsection">
                      <h6 class="hfl-detail-section__title dp-flow-config-subsection__title">
                        {{ t('protection.backupsPage.flowSourceDetailConfigSummary') }}
                      </h6>
                      <div class="hfl-detail-grid">
                        <div class="hfl-detail-row">
                          <span class="hfl-detail-row__label">{{ t('protection.backupsPage.flowSourceDetailColSnapshotCount') }}</span>
                          <span
                            class="hfl-detail-row__value dp-flow-config-summary__value tabular-nums"
                            :title="backupSummarySnapshotCountTitle"
                          >
                            {{ backupSummarySnapshotCountText }}
                          </span>
                        </div>
                        <div class="hfl-detail-row">
                          <span class="hfl-detail-row__label">{{ t('protection.backupsPage.flowBackupColLastBackup') }}</span>
                          <span
                            class="hfl-detail-row__value dp-flow-config-summary__value"
                            :class="{ 'hfl-detail-row__empty': !latestSnapshotForConfig(currentSourceConfig.id) }"
                          >
                            {{
                              latestSnapshotForConfig(currentSourceConfig.id)
                                ? formatNullableTime(latestSnapshotForConfig(currentSourceConfig.id)?.finished_at || latestSnapshotForConfig(currentSourceConfig.id)?.started_at || latestSnapshotForConfig(currentSourceConfig.id)?.created_at)
                                : t('protection.backupsPage.flowBackupLastBackupDash')
                            }}
                          </span>
                        </div>
                      </div>
                    </section>

                    <section
                      ref="dirsSectionRef"
                      class="hfl-detail-section dp-flow-config-subsection"
                    >
                      <h6 class="hfl-detail-section__title dp-flow-config-subsection__title">
                        {{ t('protection.backupsPage.flowBackupColBackupDirs') }}
                      </h6>
                      <div class="dp-flow-config-subsection__body">
                        <div
                          v-if="configDirectories(currentSourceConfig).length"
                          class="dp-flow-config-paths dp-flow-config-paths--stack"
                        >
                          <code
                            v-for="dir in configDirectories(currentSourceConfig)"
                            :key="`${currentSourceConfig.id}-${dir.id}`"
                            class="flow-source-list-drawer-path dp-flow-config-path create-recovery-plan-mapping__endpoint"
                            :class="`create-recovery-plan-mapping__endpoint--${backupConfigDirectoryKind(dir)}`"
                          >
                            <component
                              :is="backupConfigDirectoryIcon(dir)"
                              :size="14"
                              class="dp-flow-config-path__icon create-recovery-plan-mapping__icon"
                            />
                            <span class="create-recovery-plan-mapping__text">{{ dir.path }}</span>
                          </code>
                        </div>
                        <span
                          v-else
                          class="hfl-empty-mark"
                        >—</span>
                      </div>
                    </section>

                    <section
                      ref="targetsSectionRef"
                      class="hfl-detail-section dp-flow-config-subsection"
                    >
                      <h6 class="hfl-detail-section__title dp-flow-config-subsection__title">
                        {{ t('protection.backupsPage.flowSourceDetailDisasterTarget') }}
                      </h6>
                      <div class="hfl-detail-grid dp-flow-config-detail-card__grid">
                        <div class="hfl-detail-row">
                          <span class="hfl-detail-row__label">{{ t('protection.backupsPage.flowSourceDetailTargetName') }}</span>
                          <span class="hfl-detail-row__value">{{ configRepositoryName(currentSourceConfig) }}</span>
                        </div>
                        <div class="hfl-detail-row">
                          <span class="hfl-detail-row__label">{{ t('protection.backupsPage.flowSourceDetailTargetType') }}</span>
                          <span class="hfl-detail-row__value">
                            <el-tag
                              size="small"
                              effect="plain"
                            >{{ configRepositoryType(currentSourceConfig) }}</el-tag>
                          </span>
                        </div>
                        <div class="hfl-detail-row">
                          <span class="hfl-detail-row__label">{{ t('protection.backupsPage.flowSourceDetailTargetLocation') }}</span>
                          <span class="hfl-detail-row__value hfl-detail-row__value--mono">{{ configRepositoryLocation(currentSourceConfig) }}</span>
                        </div>
                      </div>
                    </section>

                    <section class="hfl-detail-section dp-flow-config-subsection">
                      <h6 class="hfl-detail-section__title dp-flow-config-subsection__title">
                        {{ t('protection.backupsPage.labelCompressionStrategy') }}
                      </h6>
                      <div class="dp-flow-config-compression">
                        <div class="dp-flow-config-compression__summary">
                          <component
                            :is="currentSourceCompression.icon"
                            :size="15"
                            aria-hidden="true"
                            class="dp-flow-config-compression__icon"
                            :class="`dp-flow-config-compression__icon--${currentSourceCompression.level}`"
                          />
                          <strong class="dp-flow-config-compression__title">
                            {{ currentSourceCompression.title }}
                          </strong>
                        </div>
                        <p class="dp-flow-config-compression__description">
                          {{ currentSourceCompression.description }}
                        </p>
                      </div>
                    </section>

                    <section class="hfl-detail-section dp-flow-config-subsection">
                      <h6 class="hfl-detail-section__title dp-flow-config-subsection__title">
                        {{ t('protection.backupsPage.flowBackupColBoundBackupPolicy') }}
                      </h6>
                      <div
                        v-if="currentSourcePolicy"
                        class="hfl-detail-grid dp-flow-config-detail-card__grid"
                      >
                        <div class="hfl-detail-row hfl-detail-row--full dp-flow-policy-overview__name-row">
                          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldPolicyName') }}</span>
                          <span class="hfl-detail-row__value dp-flow-policy-overview__name-value">
                            <span class="dp-flow-detail-name-with-state dp-flow-policy-overview__name">
                              <span>{{ currentSourcePolicy.name }}</span>
                              <span :class="detailStatePillClass(currentSourcePolicy.is_active)">
                                {{ boolStatusLabel(currentSourcePolicy.is_active) }}
                              </span>
                            </span>
                          </span>
                        </div>
                        <div class="hfl-detail-row hfl-detail-row--full">
                          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.appliedToBackupSourcesLabel') }}</span>
                          <span class="hfl-detail-row__value">{{ policyUsageValue(currentSourcePolicy.related_backup_count) }}</span>
                        </div>
                        <div
                          v-for="row in policyScheduleDetailRows(currentSourcePolicy)"
                          :key="row.label"
                          class="hfl-detail-row hfl-detail-row--full"
                        >
                          <span class="hfl-detail-row__label">{{ row.label }}</span>
                          <span class="hfl-detail-row__value">{{ row.value }}</span>
                        </div>
                        <div class="hfl-detail-row hfl-detail-row--full">
                          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldRetention') }}</span>
                          <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                            <span class="create-policy-detail-popover__retention-box dp-flow-policy-overview__retention-box">
                              <div class="policy-retention-detail-list">
                                <div
                                  v-for="line in policyRetentionDetailLines(currentSourcePolicy)"
                                  :key="`${line.label || ''}${line.text}`"
                                  class="policy-retention-detail-list__line dp-flow-policy-overview__retention-line"
                                >
                                  <span
                                    v-if="line.label"
                                    class="policy-retention-detail-list__label"
                                  >{{ line.label }}</span>
                                  <span class="policy-retention-detail-list__text">{{ line.text }}</span>
                                </div>
                              </div>
                            </span>
                          </span>
                        </div>
                        <div class="hfl-detail-row hfl-detail-row--full">
                          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.sectionAdvancedSettings') }}</span>
                          <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                            <span class="create-policy-detail-popover__advanced-box dp-flow-policy-overview__advanced-box">
                              <span
                                v-for="row in policyAdvancedDetailLines(currentSourcePolicy)"
                                :key="row.label"
                                class="create-policy-detail-popover__advanced-row"
                              >
                                <span>{{ row.label }}</span>
                                <span :class="detailStatePillClass(row.enabled)">{{ boolStatusLabel(row.enabled) }}</span>
                              </span>
                            </span>
                          </span>
                        </div>
                      </div>
                      <div
                        v-else
                        class="dp-flow-config-subsection__empty"
                      >
                        <Unlink :size="14" />
                        {{ t('protection.backupsPage.flowBackupColPolicyNone') }}
                      </div>
                    </section>

                    <section class="hfl-detail-section dp-flow-config-subsection">
                      <h6 class="hfl-detail-section__title dp-flow-config-subsection__title">
                        {{ t('protection.backupsPage.flowBackupColBoundFileFilter') }}
                      </h6>
                      <div
                        v-if="currentSourceFilter"
                        class="hfl-detail-grid dp-flow-config-detail-card__grid"
                      >
                        <div class="hfl-detail-row">
                          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldFilterRuleName') }}</span>
                          <span class="hfl-detail-row__value dp-flow-detail-name-with-state">
                            <span>{{ currentSourceFilter.name }}</span>
                            <span :class="detailStatePillClass(currentSourceFilter.is_active)">
                              {{ boolStatusLabel(currentSourceFilter.is_active) }}
                            </span>
                          </span>
                        </div>
                        <div class="hfl-detail-row">
                          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.appliedToBackupSourcesLabel') }}</span>
                          <span class="hfl-detail-row__value">{{ policyUsageValue(currentSourceFilter.related_backup_count) }}</span>
                        </div>
                        <div
                          v-if="currentSourceFilter.large_file_limit_enabled"
                          class="hfl-detail-row"
                        >
                          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.previewMaxSizeLimit') }}</span>
                          <span class="hfl-detail-row__value">{{ fileFilterMaxSizeLimitValue(currentSourceFilter) }}</span>
                        </div>
                        <div
                          v-if="currentSourceFilter.ignore_cache_directories"
                          class="hfl-detail-row"
                        >
                          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.cacheTitle') }}</span>
                          <span class="hfl-detail-row__value">
                            <span :class="detailStatePillClass(true)">{{ boolStatusLabel(true) }}</span>
                          </span>
                        </div>
                        <div
                          v-if="currentSourceFilter.current_filesystem_only"
                          class="hfl-detail-row"
                        >
                          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fsOnlyTitle') }}</span>
                          <span class="hfl-detail-row__value">
                            <span :class="detailStatePillClass(true)">{{ boolStatusLabel(true) }}</span>
                          </span>
                        </div>
                        <div class="hfl-detail-row hfl-detail-row--full">
                          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.filterRulesPreviewTitle') }}</span>
                          <span class="hfl-detail-row__value">
                            <div class="flow-binding-rules-preview dp-flow-detail-rules-preview">
                              <template v-if="fileFilterCompiledRuleLines(currentSourceFilter).length">
                                <code
                                  v-for="(line, index) in fileFilterCompiledRuleLines(currentSourceFilter)"
                                  :key="`${index}-${line}`"
                                  class="flow-binding-rules-preview__line"
                                >
                                  {{ line }}
                                </code>
                              </template>
                              <p
                                v-else
                                class="flow-binding-rules-preview__empty"
                              >
                                {{ t('protection.policiesPage.filterNoActiveRules') }}
                              </p>
                            </div>
                          </span>
                        </div>
                      </div>
                      <div
                        v-else
                        class="dp-flow-config-subsection__empty"
                      >
                        <Unlink :size="14" />
                        {{ t('protection.backupsPage.flowBackupColPolicyNone') }}
                      </div>
                    </section>

                    <section class="hfl-detail-section dp-flow-config-subsection">
                      <h6 class="hfl-detail-section__title dp-flow-config-subsection__title">
                        {{ t('protection.backupsPage.descRecoveryPlan') }}
                      </h6>
                      <div class="dp-flow-config-subsection__body">
                        <div
                          v-if="currentSourceRecoveryPlanMappings.length"
                          class="dp-flow-restore-plan-card"
                        >
                          <HflPopover
                            trigger="hover"
                            placement="top-start"
                            :width="520"
                            popper-class="create-recovery-plan-tooltip"
                          >
                            <template #reference>
                              <div class="create-recovery-plan-cell create-recovery-plan-cell--review create-recovery-plan-cell--enabled">
                                <div class="create-recovery-plan-cell__status">
                                  <span
                                    class="create-recovery-plan-cell__dot"
                                    aria-hidden="true"
                                  />
                                  <span class="create-recovery-plan-cell__status-label">
                                    {{ recoveryPlanStatusLabel(currentSourceRecoveryPlanMappings) }}
                                  </span>
                                </div>
                                <div class="create-recovery-plan-cell__policy-line">
                                  <div
                                    class="create-recovery-plan-cell__policy"
                                    :class="`create-recovery-plan-cell__policy--${recoveryPlanConflictTone(currentSourceRecoveryPlanMappings[0].plan)}`"
                                  >
                                    <ShieldAlert
                                      v-if="recoveryPlanConflictTone(currentSourceRecoveryPlanMappings[0].plan) === 'overwrite'"
                                      :size="14"
                                      class="create-recovery-plan-cell__policy-icon"
                                    />
                                    <ShieldCheck
                                      v-else
                                      :size="14"
                                      class="create-recovery-plan-cell__policy-icon"
                                    />
                                    <span class="create-recovery-plan-cell__policy-text">
                                      {{ recoveryPlanConflictSummary(currentSourceRecoveryPlanMappings[0].plan) }}
                                    </span>
                                  </div>
                                  <div
                                    class="create-recovery-plan-cell__policy-description"
                                    :class="`create-recovery-plan-cell__policy-description--${recoveryPlanConflictTone(currentSourceRecoveryPlanMappings[0].plan)}`"
                                  >
                                    {{ recoveryPlanConflictDescription(currentSourceRecoveryPlanMappings[0].plan) }}
                                  </div>
                                </div>
                                <div class="create-recovery-plan-cell__mappings">
                                  <div
                                    v-for="mapping in currentSourceRecoveryPlanMappings"
                                    :key="mapping.key"
                                    class="create-recovery-plan-mapping"
                                  >
                                    <span
                                      class="create-recovery-plan-mapping__endpoint"
                                      :class="`create-recovery-plan-mapping__endpoint--${recoveryPlanMappingSourceKind(mapping)}`"
                                      :title="recoveryPlanSourcePathLabel(mapping)"
                                    >
                                      <Camera
                                        v-if="recoveryPlanMappingSourceKind(mapping) === 'snapshot'"
                                        :size="14"
                                        class="create-recovery-plan-mapping__icon"
                                      />
                                      <File
                                        v-else-if="recoveryPlanMappingSourceKind(mapping) === 'file'"
                                        :size="14"
                                        class="create-recovery-plan-mapping__icon"
                                      />
                                      <Folder
                                        v-else
                                        :size="14"
                                        class="create-recovery-plan-mapping__icon"
                                      />
                                      <span
                                        class="create-recovery-plan-mapping__text"
                                        :title="recoveryPlanSourcePathLabel(mapping)"
                                      >{{ recoveryPlanSourcePathLabel(mapping) }}</span>
                                    </span>
                                    <span
                                      class="create-recovery-plan-mapping__arrow"
                                      aria-hidden="true"
                                    >-&gt;</span>
                                    <span
                                      class="create-recovery-plan-mapping__endpoint create-recovery-plan-mapping__endpoint--target"
                                      :title="recoveryPlanTargetSummary(mapping.plan)"
                                    >
                                      <FolderOpen
                                        :size="14"
                                        class="create-recovery-plan-mapping__icon"
                                      />
                                      <span
                                        class="create-recovery-plan-mapping__text"
                                        :title="recoveryPlanTargetSummary(mapping.plan)"
                                      >{{ recoveryPlanTargetSummary(mapping.plan) }}</span>
                                    </span>
                                  </div>
                                </div>
                              </div>
                            </template>
                            <template #default>
                              <div class="create-recovery-plan-tooltip__content">
                                <div
                                  class="create-recovery-plan-tooltip__policy"
                                  :class="`create-recovery-plan-tooltip__policy--${recoveryPlanConflictTone(currentSourceRecoveryPlanMappings[0].plan)}`"
                                >
                                  <ShieldAlert
                                    v-if="recoveryPlanConflictTone(currentSourceRecoveryPlanMappings[0].plan) === 'overwrite'"
                                    :size="14"
                                    class="create-recovery-plan-cell__policy-icon"
                                  />
                                  <ShieldCheck
                                    v-else
                                    :size="14"
                                    class="create-recovery-plan-cell__policy-icon"
                                  />
                                  <span class="create-recovery-plan-cell__policy-text">
                                    {{ recoveryPlanConflictFullLabel(currentSourceRecoveryPlanMappings[0].plan) }}
                                  </span>
                                </div>
                                <div
                                  v-for="mapping in currentSourceRecoveryPlanMappings"
                                  :key="`${mapping.key}-tooltip`"
                                  class="create-recovery-plan-tooltip__mapping create-recovery-plan-mapping"
                                >
                                  <span
                                    class="create-recovery-plan-mapping__endpoint"
                                    :class="`create-recovery-plan-mapping__endpoint--${recoveryPlanMappingSourceKind(mapping)}`"
                                    :title="recoveryPlanSourcePathLabel(mapping)"
                                  >
                                    <Camera
                                      v-if="recoveryPlanMappingSourceKind(mapping) === 'snapshot'"
                                      :size="14"
                                      class="create-recovery-plan-mapping__icon"
                                    />
                                    <File
                                      v-else-if="recoveryPlanMappingSourceKind(mapping) === 'file'"
                                      :size="14"
                                      class="create-recovery-plan-mapping__icon"
                                    />
                                    <Folder
                                      v-else
                                      :size="14"
                                      class="create-recovery-plan-mapping__icon"
                                    />
                                    <span
                                      class="create-recovery-plan-mapping__text"
                                      :title="recoveryPlanSourcePathLabel(mapping)"
                                    >{{ recoveryPlanSourcePathLabel(mapping) }}</span>
                                  </span>
                                  <span
                                    class="create-recovery-plan-mapping__arrow"
                                    aria-hidden="true"
                                  >-&gt;</span>
                                  <span
                                    class="create-recovery-plan-mapping__endpoint create-recovery-plan-mapping__endpoint--target"
                                    :title="recoveryPlanTargetSummary(mapping.plan)"
                                  >
                                    <FolderOpen
                                      :size="14"
                                      class="create-recovery-plan-mapping__icon"
                                    />
                                    <span
                                      class="create-recovery-plan-mapping__text"
                                      :title="recoveryPlanTargetSummary(mapping.plan)"
                                    >{{ recoveryPlanTargetSummary(mapping.plan) }}</span>
                                  </span>
                                </div>
                              </div>
                            </template>
                          </HflPopover>
                        </div>
                        <div
                          v-else
                          class="dp-flow-config-subsection__empty dp-flow-config-subsection__empty--block"
                        >
                          <Unlink :size="14" />
                          {{ t('protection.backupsPage.flowSourceDetailRestorePlanEmpty') }}
                        </div>
                      </div>
                    </section>
                  </div>
                </div>
                <el-empty
                  v-else
                  :description="t('protection.backupsPage.flowSourceDetailConfigsEmpty')"
                  :image-size="72"
                />
              </section>
            </template>
            <div
              v-else-if="sourceDetailLoading"
              class="py-6"
            >
              <el-skeleton
                :rows="8"
                animated
              />
            </div>
            <el-empty
              v-else-if="!sourceDetailError"
              :description="t('protection.backupDetail.notFound')"
              :image-size="72"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane
          :label="t('protection.backupsPage.flowSourceDetailTabSnapshots')"
          name="snapshots"
        >
          <div class="hfl-list-toolbar dp-source-task-toolbar dp-source-record-toolbar">
            <ElInput
              v-model="snapshotFilterId"
              clearable
              class="hfl-list-search dp-source-task-toolbar__search"
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
              style="width: 130px"
              popper-class="dp-source-task-select-popper"
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
              style="width: 150px"
              popper-class="dp-source-task-select-popper"
              @change="onSnapshotTimeModeChange"
            >
              <ElOption
                v-for="option in detailTimeModeOptions"
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
              :shortcuts="sourceTaskDateRangeShortcuts"
              :start-placeholder="t('ops.task.startTime')"
              :end-placeholder="t('ops.task.endTime')"
              popper-class="dp-source-task-select-popper"
              class="dp-source-record-toolbar__range"
            />
            <div class="hfl-list-toolbar__right">
              <ElButton
                class="hfl-refresh-button"
                :title="t('ops.task.btnRefresh')"
                :aria-label="t('ops.task.btnRefresh')"
                :disabled="sourceSnapshotsLoading"
                @click="loadSnapshotsForSource"
              >
                <RefreshCw
                  :size="16"
                  :class="{ 'is-spinning': sourceSnapshotsLoading }"
                />
              </ElButton>
            </div>
          </div>
          <el-alert
            v-if="sourceSnapshotsError"
            :title="sourceSnapshotsError"
            type="error"
            show-icon
            :closable="false"
          />
          <el-table
            v-if="realSourceSnapshots.length || sourceSnapshotsLoading"
            ref="snapshotTableRef"
            v-table-column-resize="'protection.flowBackupSource.snapshots'"
            v-table-overflow-title
            v-loading="sourceSnapshotsLoading"
            :data="pagedSourceSnapshots"
            stripe
            row-key="id"
            :max-height="snapshotTableMaxHeight"
            :expand-row-keys="expandedSnapshotRowKeys"
            :header-cell-style="TABLE_HEADER_STYLE"
            class="hfl-list-table snapshot-points-table"
            @expand-change="onSnapshotExpandChange"
          >
            <el-table-column
              type="expand"
              width="35"
              fixed
            >
              <template #default="{ row }">
                <div class="snapshot-directory-expand-panel">
                  <el-alert
                    v-if="selectedSnapshotId === row.id && snapshotDetailError"
                    :title="snapshotDetailError"
                    type="error"
                    show-icon
                    :closable="false"
                  />
                  <div
                    v-else-if="selectedSnapshotId === row.id && snapshotDetailLoading"
                    class="py-6"
                  >
                    <el-skeleton
                      :rows="3"
                      animated
                    />
                  </div>
                  <template v-else-if="selectedSnapshotId === row.id && selectedSnapshot">
                    <section class="snapshot-efficiency-summary">
                      <dl class="snapshot-efficiency-summary__metrics">
                        <div class="snapshot-efficiency-summary__metric">
                          <dt>
                            <span class="snapshot-efficiency-summary__metric-label">{{ t('protection.backupsPage.snapshotRecoverableData') }}</span>
                            <ElTooltip
                              :content="t('protection.backupsPage.snapshotRecoverableDataHint')"
                              placement="top"
                              teleported
                              append-to="body"
                              :z-index="3600"
                            >
                              <Info
                                :size="13"
                                class="snapshot-efficiency-summary__metric-info"
                                aria-hidden="true"
                              />
                            </ElTooltip>
                          </dt>
                          <dd>{{ fmtBytes(snapshotDisplaySize(selectedSnapshot)) }}</dd>
                        </div>
                        <div class="snapshot-efficiency-summary__metric">
                          <dt>
                            <span class="snapshot-efficiency-summary__metric-label">{{ t('protection.backupsPage.snapshotNewOriginalData') }}</span>
                            <ElTooltip
                              :content="t('protection.backupsPage.snapshotNewOriginalDataHint')"
                              placement="top"
                              teleported
                              append-to="body"
                              :z-index="3600"
                            >
                              <Info
                                :size="13"
                                class="snapshot-efficiency-summary__metric-info"
                                aria-hidden="true"
                              />
                            </ElTooltip>
                          </dt>
                          <dd>{{ fmtReferenceBytes(selectedSnapshot.new_original_content_bytes) }}</dd>
                        </div>
                        <div class="snapshot-efficiency-summary__metric">
                          <dt>
                            <span class="snapshot-efficiency-summary__metric-label">{{ t('protection.backupsPage.snapshotNewStorage') }}</span>
                            <ElTooltip
                              :content="t('protection.backupsPage.snapshotNewStorageHint')"
                              placement="top"
                              teleported
                              append-to="body"
                              :z-index="3600"
                            >
                              <Info
                                :size="13"
                                class="snapshot-efficiency-summary__metric-info"
                                aria-hidden="true"
                              />
                            </ElTooltip>
                          </dt>
                          <dd>{{ fmtReferenceBytes(selectedSnapshot.new_packed_content_bytes) }}</dd>
                        </div>
                        <div class="snapshot-efficiency-summary__metric">
                          <dt>
                            <span class="snapshot-efficiency-summary__metric-label">{{ t('protection.backupsPage.snapshotDataReuse') }}</span>
                            <ElTooltip
                              :content="t('protection.backupsPage.snapshotDataReuseHint')"
                              placement="top"
                              teleported
                              append-to="body"
                              :z-index="3600"
                            >
                              <Info
                                :size="13"
                                class="snapshot-efficiency-summary__metric-info"
                                aria-hidden="true"
                              />
                            </ElTooltip>
                          </dt>
                          <dd>{{ fmtReferencePercent(selectedSnapshot.data_reuse_ratio) }}</dd>
                        </div>
                        <div class="snapshot-efficiency-summary__metric">
                          <dt>
                            <span class="snapshot-efficiency-summary__metric-label">{{ t('protection.backupsPage.snapshotCompressionSavings') }}</span>
                            <ElTooltip
                              :content="t('protection.backupsPage.snapshotCompressionSavingsHint')"
                              placement="top"
                              teleported
                              append-to="body"
                              :z-index="3600"
                            >
                              <Info
                                :size="13"
                                class="snapshot-efficiency-summary__metric-info"
                                aria-hidden="true"
                              />
                            </ElTooltip>
                          </dt>
                          <dd>{{ fmtReferencePercent(selectedSnapshot.compression_savings_ratio) }}</dd>
                        </div>
                        <div class="snapshot-efficiency-summary__metric">
                          <dt>
                            <span class="snapshot-efficiency-summary__metric-label">{{ t('protection.backupsPage.snapshotCombinedReduction') }}</span>
                            <ElTooltip
                              :content="t('protection.backupsPage.snapshotCombinedReductionHint')"
                              placement="top"
                              teleported
                              append-to="body"
                              :z-index="3600"
                            >
                              <Info
                                :size="13"
                                class="snapshot-efficiency-summary__metric-info"
                                aria-hidden="true"
                              />
                            </ElTooltip>
                          </dt>
                          <dd>{{ fmtCombinedReduction(selectedSnapshot) }}</dd>
                        </div>
                      </dl>
                    </section>
                    <el-table
                      v-if="selectedSnapshotDirectories.length"
                      v-table-column-resize="'protection.flowBackupSource.snapshotDirectories'"
                      v-table-overflow-title
                      :data="selectedSnapshotDirectories"
                      :max-height="snapshotTableMaxHeight"
                      :fit="false"
                      stripe
                      :header-cell-style="TABLE_HEADER_STYLE"
                      class="hfl-list-table hfl-list-table--compact snapshot-directory-table"
                    >
                      <el-table-column
                        :label="t('protection.backupDetail.colBackupDir')"
                        width="240"
                      >
                        <template #default="{ row: dir }">
                          <button
                            v-if="canBrowseSnapshotDirectory(dir)"
                            type="button"
                            class="hfl-table-name-link snapshot-directory-path-cell"
                            @click.stop="openSnapshotDirectory(dir)"
                          >
                            <span class="snapshot-directory-path-cell__parent">
                              <component
                                :is="snapshotDirectoryIcon(dir)"
                                :size="15"
                                class="snapshot-directory-path-cell__icon"
                                :class="`snapshot-directory-path-cell__icon--${snapshotDirectoryKind(dir)}`"
                              />
                              <span class="snapshot-directory-path-cell__path hfl-table-cell-mono">{{ dir.source_path }}</span>
                            </span>
                          </button>
                          <span
                            v-else
                            class="snapshot-directory-path-cell snapshot-directory-path-cell--disabled"
                          >
                            <span class="snapshot-directory-path-cell__parent">
                              <component
                                :is="snapshotDirectoryIcon(dir)"
                                :size="15"
                                class="snapshot-directory-path-cell__icon"
                                :class="`snapshot-directory-path-cell__icon--${snapshotDirectoryKind(dir)}`"
                              />
                              <code class="snapshot-directory-path-cell__path flow-source-list-drawer-path hfl-table-cell-mono">{{ dir.source_path }}</code>
                            </span>
                          </span>
                        </template>
                      </el-table-column>
                      <el-table-column
                        :label="t('protection.backupsPage.snapshotBrowserDirectorySnapshotId')"
                        width="180"
                      >
                        <template #default="{ row: dir }">
                          <span
                            class="hfl-table-cell-mono"
                            :class="{ 'hfl-empty-mark': !dir.kopia_snapshot_id }"
                          >{{ dir.kopia_snapshot_id || '—' }}</span>
                        </template>
                      </el-table-column>
                      <el-table-column
                        :label="t('protection.backupsPage.snapshotRecoverableData')"
                        width="140"
                        align="right"
                      >
                        <template #default="{ row: dir }">
                          {{ fmtBytes(dir.size_bytes) }}
                        </template>
                      </el-table-column>
                      <el-table-column
                        :label="t('protection.backupsPage.snapshotBrowserFileDirCount')"
                        width="110"
                        align="right"
                      >
                        <template #default="{ row: dir }">
                          {{ dir.file_count }}/{{ dir.dir_count }}
                        </template>
                      </el-table-column>
                      <el-table-column
                        :label="t('protection.backupDetail.labelStatus')"
                        width="92"
                      >
                        <template #default="{ row: dir }">
                          <el-tag
                            :type="lifecycleStatusTagAttrs(dir.status).type"
                            :class="lifecycleStatusTagAttrs(dir.status).class"
                            size="small"
                          >
                            {{ snapshotStatusLabel(dir.status) }}
                          </el-tag>
                        </template>
                      </el-table-column>
                      <el-table-column
                        :label="t('protection.backupDetail.colError')"
                        width="220"
                      >
                        <template #default="{ row: dir }">
                          <span
                            v-if="dir.error_message"
                            class="snapshot-directory-error"
                          >
                            {{ dir.error_code ? `[${dir.error_code}] ` : '' }}{{ dir.error_message }}
                          </span>
                          <span
                            v-else
                            class="hfl-empty-mark"
                          >{{ t('protection.backupDetail.durationDash') }}</span>
                        </template>
                      </el-table-column>
                      <el-table-column
                        :label="t('protection.sourceResources.colActions')"
                        width="120"
                        fixed="right"
                        align="center"
                        class-name="hfl-table-actions-col"
                        header-class-name="hfl-table-actions-col"
                      >
                        <template #default="{ row: dir }">
                          <div class="snapshot-point-actions">
                            <button
                              type="button"
                              class="snapshot-point-actions__button snapshot-point-actions__button--browse"
                              :title="t('protection.backupsPage.snapshotBrowserBrowse')"
                              :disabled="!canBrowseSnapshotDirectory(dir)"
                              @click.stop="openSnapshotDirectory(dir)"
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
                      </el-table-column>
                    </el-table>
                    <el-empty
                      v-else
                      :description="t('protection.backupsPage.snapshotBrowserEmptyDirectories')"
                      :image-size="56"
                    />
                  </template>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupDetail.colSnapId')"
              width="140"
              fixed
            >
              <template #default="{ row }">
                <button
                  type="button"
                  class="hfl-table-name-link hfl-table-cell-mono hfl-table-name-link--single"
                  @click.stop="toggleSnapshot(row)"
                >
                  {{ row.snapshot_uid || `#${row.id}` }}
                </button>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupDetail.labelStatus')"
              width="92"
            >
              <template #default="{ row }">
                <el-tag
                  :type="lifecycleStatusTagAttrs(row.status).type"
                  :class="lifecycleStatusTagAttrs(row.status).class"
                  size="small"
                >
                  {{ snapshotStatusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupDetail.colSnapStart')"
              width="150"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-time snapshot-point-time"
                  :class="{ 'hfl-empty-mark': !(row.started_at || row.created_at) }"
                  :title="formatNullableTime(row.started_at || row.created_at)"
                >
                  {{ formatNullableTime(row.started_at || row.created_at) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupDetail.colSnapEnd')"
              width="150"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-time snapshot-point-time"
                  :class="{ 'hfl-empty-mark': !row.finished_at }"
                  :title="formatNullableTime(row.finished_at)"
                >
                  {{ formatNullableTime(row.finished_at) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupsPage.snapshotListSize')"
              width="88"
              align="right"
            >
              <template #default="{ row }">
                {{ fmtReferenceBytes(row.new_packed_content_bytes) }}
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupsPage.snapshotRecoverableData')"
              width="105"
              align="right"
            >
              <template #default="{ row }">
                {{ fmtBytes(snapshotDisplaySize(row)) }}
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupsPage.snapshotBrowserFileDirCount')"
              width="110"
              align="right"
            >
              <template #default="{ row }">
                {{ snapshotDisplayFileCount(row) }}/{{ snapshotDisplayDirCount(row) }}
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.sourceResources.colActions')"
              width="171"
              fixed="right"
              align="center"
              class-name="hfl-table-actions-col"
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
                        :title="t('protection.backupsPage.snapshotRecoverAction')"
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
                    :title="t('protection.backupsPage.snapshotViewAction')"
                    @click.stop="toggleSnapshot(row)"
                  >
                    <FolderOpen
                      :size="14"
                      class="snapshot-point-actions__icon"
                      aria-hidden="true"
                    />
                    <span>{{ t('protection.backupsPage.snapshotViewAction') }}</span>
                  </button>
                </div>
              </template>
            </el-table-column>
          </el-table>
          <div
            v-if="sourceSnapshotTotal > 0"
            class="hfl-list-footer"
          >
            <HflPagination
              v-model:current-page="snapshotPagination.page"
              v-model:page-size="snapshotPagination.pageSize"
              class="hfl-list-footer__pagination"
              layout="total, sizes, prev, pager, next"
              :total="sourceSnapshotTotal"
              :page-sizes="DETAIL_PAGE_SIZE_OPTIONS"
              @size-change="snapshotPagination.page = 1"
            />
          </div>
          <Teleport to="body">
            <div
              v-if="fileBrowserDrawerOpen"
              class="dp-snapshot-file-browser-shell"
              @click.self="closeSnapshotFileBrowser"
            >
              <aside class="dp-snapshot-file-browser-panel">
                <header class="dp-snapshot-file-browser-panel__header">
                  <div class="min-w-0 pr-2">
                    <div class="truncate text-base font-semibold text-slate-900">
                      {{ t('protection.backupsPage.snapshotBrowserPreviewTitle') }}
                    </div>
                    <div
                      v-if="selectedSnapshotDirectory"
                      class="truncate text-xs text-slate-500"
                    >
                      {{ selectedSnapshotDirectory.source_path }}
                    </div>
                  </div>
                  <button
                    type="button"
                    class="dp-snapshot-file-browser-panel__close"
                    @click="closeSnapshotFileBrowser"
                  >
                    <X :size="18" />
                  </button>
                </header>
                <div
                  v-if="selectedSnapshotDirectory"
                  class="dp-snapshot-file-browser dp-snapshot-file-browser-panel__body"
                >
                  <template v-if="selectedSnapshotDirectoryIsFile">
                    <div class="dp-snapshot-file-browser__toolbar">
                      <div class="dp-snapshot-file-browser__toolbar-main">
                        <ElButton
                          type="primary"
                          size="small"
                          :loading="downloadingSnapshotFile"
                          :disabled="!selectedSnapshotFileChecked"
                          @click="downloadSelectedSnapshotFile"
                        >
                          <Download
                            :size="14"
                            class="mr-1"
                          />
                          {{ t('protection.backupsPage.snapshotBrowserDownload') }}
                        </ElButton>
                        <div class="min-w-0">
                          <div class="text-sm font-medium text-slate-800">
                            {{ snapshotFileFallbackName(selectedSnapshotDirectory) }}
                          </div>
                          <div class="truncate text-xs text-slate-500">
                            {{ selectedSnapshotDirectory.source_path }}
                          </div>
                        </div>
                      </div>
                      <div class="dp-snapshot-file-browser__toolbar-actions">
                        <span class="dp-snapshot-file-browser__selected">
                          {{ t('protection.backupsPage.snapshotBrowserSelectedCount', { n: selectedSnapshotFileCount }) }}
                        </span>
                        <ElButton
                          v-if="selectedSnapshotFileChecked"
                          size="small"
                          @click="clearSnapshotFileSelection"
                        >
                          {{ t('protection.backupsPage.snapshotBrowserClearSelection') }}
                        </ElButton>
                      </div>
                    </div>

                    <div
                      class="dp-snapshot-file-browser__file-row"
                      :class="{ 'is-selected': selectedSnapshotFileChecked }"
                      role="button"
                      tabindex="0"
                      @click="toggleSnapshotFileSelection"
                      @keydown.enter.prevent="toggleSnapshotFileSelection"
                      @keydown.space.prevent="toggleSnapshotFileSelection"
                    >
                      <ElCheckbox
                        :model-value="selectedSnapshotFileChecked"
                        @change="selectedSnapshotFileChecked = Boolean($event)"
                        @click.stop
                      />
                      <span class="dp-snapshot-file-browser__entry">
                        <File
                          :size="15"
                          class="snapshot-directory-path-cell__icon snapshot-directory-path-cell__icon--file"
                        />
                        <span class="truncate">{{ snapshotFileFallbackName(selectedSnapshotDirectory) }}</span>
                      </span>
                      <span class="dp-snapshot-file-browser__tree-path truncate">{{ selectedSnapshotDirectory.source_path }}</span>
                      <span class="dp-snapshot-file-browser__tree-size">{{ fmtBytes(selectedSnapshotDirectory.size_bytes) }}</span>
                      <span
                        class="dp-snapshot-file-browser__tree-time"
                        :class="{ 'hfl-empty-mark': !selectedSnapshotDirectory.created_at }"
                      >{{ formatNullableTime(selectedSnapshotDirectory.created_at) }}</span>
                    </div>
                  </template>
                  <template v-else>
                    <div class="dp-snapshot-file-browser__toolbar">
                      <div class="dp-snapshot-file-browser__toolbar-main">
                        <ElButton
                          type="primary"
                          size="small"
                          :loading="downloadingSelected"
                          :disabled="!selectedBrowserPathCount"
                          @click="downloadSelectedBrowserPaths"
                        >
                          <Download
                            :size="14"
                            class="mr-1"
                          />
                          {{ t('protection.backupsPage.snapshotBrowserDownload') }}
                        </ElButton>
                        <div class="min-w-0">
                          <div class="text-sm font-medium text-slate-800">
                            {{ selectedSnapshotDirectory.source_path }}
                          </div>
                          <div class="dp-snapshot-file-browser__crumbs">
                            <template
                              v-for="(crumb, index) in browserBreadcrumbs"
                              :key="crumb.path || 'root'"
                            >
                              <button
                                type="button"
                                class="source-more-link"
                                @click="openSnapshotDirectory(selectedSnapshotDirectory, crumb.path)"
                              >
                                {{ crumb.label }}
                              </button>
                              <span
                                v-if="index < browserBreadcrumbs.length - 1"
                                class="text-slate-400"
                              >/</span>
                            </template>
                          </div>
                        </div>
                      </div>
                      <div class="dp-snapshot-file-browser__toolbar-actions">
                        <span class="dp-snapshot-file-browser__selected">
                          {{ t('protection.backupsPage.snapshotBrowserSelectedCount', { n: selectedBrowserPathCount }) }}
                        </span>
                        <ElButton
                          v-if="selectedBrowserPathCount"
                          size="small"
                          @click="clearBrowserSelection"
                        >
                          {{ t('protection.backupsPage.snapshotBrowserClearSelection') }}
                        </ElButton>
                        <ElButton
                          v-if="browserParentPath || browserPath"
                          size="small"
                          @click="openSnapshotDirectory(selectedSnapshotDirectory, browserParentPath)"
                        >
                          <ArrowLeft
                            :size="14"
                            class="mr-1"
                          />
                          {{ t('protection.backupsPage.snapshotBrowserParent') }}
                        </ElButton>
                      </div>
                    </div>

                    <el-alert
                      v-if="browserError"
                      :title="browserError"
                      type="error"
                      show-icon
                      :closable="false"
                    />
                    <el-tree
                      ref="browserTreeRef"
                      :key="`${selectedSnapshotDirectory.id}:${browserPath}:${browserTreeVersion}`"
                      v-loading="browserLoading"
                      node-key="id"
                      show-checkbox
                      check-strictly
                      lazy
                      :load="loadBrowserTreeNode"
                      :props="{ children: 'children', label: 'label', disabled: 'disabled', isLeaf: 'isLeaf' }"
                      class="dp-snapshot-file-browser__tree"
                      empty-text=" "
                      @check-change="onBrowserTreeCheckChange"
                    >
                      <template #default="{ data }">
                        <div class="dp-snapshot-file-browser__tree-row">
                          <span class="dp-snapshot-file-browser__entry">
                            <Folder
                              v-if="data.type === 'dir'"
                              :size="15"
                              class="snapshot-directory-path-cell__icon snapshot-directory-path-cell__icon--dir"
                            />
                            <File
                              v-else
                              :size="15"
                              class="snapshot-directory-path-cell__icon snapshot-directory-path-cell__icon--file"
                            />
                            <span class="truncate">{{ data.name }}</span>
                          </span>
                          <span class="dp-snapshot-file-browser__tree-path truncate">{{ data.path }}</span>
                          <span class="dp-snapshot-file-browser__tree-size">{{ data.type === 'dir' ? '—' : fmtBytes(data.size_bytes) }}</span>
                          <span
                            class="dp-snapshot-file-browser__tree-time"
                            :class="{ 'hfl-empty-mark': !data.modified_at }"
                          >{{ formatNullableTime(data.modified_at) }}</span>
                        </div>
                      </template>
                    </el-tree>
                    <el-empty
                      v-if="!browserLoading && !browserError && !browserTreeEntries.length"
                      :description="t('protection.backupsPage.snapshotBrowserEmpty')"
                      :image-size="48"
                    />
                  </template>
                </div>
              </aside>
            </div>
          </Teleport>
          <el-empty
            v-if="!sourceSnapshotsLoading && !sourceSnapshotsError && !realSourceSnapshots.length"
            :description="t('protection.backupsPage.snapshotBrowserEmpty')"
            :image-size="72"
          />
        </el-tab-pane>

        <el-tab-pane
          :label="t('protection.backupsPage.flowSourceDetailTabRestoreRecords')"
          name="restoreRecords"
        >
          <div class="hfl-list-toolbar dp-source-task-toolbar dp-source-record-toolbar">
            <ElInput
              v-model="restoreRecordFilterQuery"
              clearable
              class="hfl-list-search dp-source-record-toolbar__restore-search"
              :placeholder="t('protection.backupsPage.flowRestoreSearchPlaceholder')"
              @clear="clearRestoreRecordSearch"
            >
              <template #prepend>
                <ElSelect
                  v-model="restoreRecordSearchField"
                  style="width: 120px"
                  popper-class="dp-source-task-select-popper"
                  @change="handleRestoreRecordSearchFieldChange"
                >
                  <ElOption
                    v-for="option in restoreRecordSearchFieldOptions"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </ElSelect>
              </template>
              <template #prefix>
                <Search
                  :size="16"
                  class="text-slate-400"
                />
              </template>
            </ElInput>
            <ElSelect
              v-model="restoreRecordFilterStatus"
              clearable
              :placeholder="t('ops.task.filterStatus')"
              style="width: 130px"
              popper-class="dp-source-task-select-popper"
            >
              <ElOption
                v-for="option in restoreRecordStatusOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </ElSelect>
            <ElSelect
              v-model="restoreRecordFilterSourceMode"
              clearable
              :placeholder="t('protection.backupsPage.flowRestoreConfiguration')"
              style="width: 150px"
              popper-class="dp-source-task-select-popper"
            >
              <ElOption
                v-for="option in restoreRecordSourceModeOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </ElSelect>
            <ElSelect
              v-model="restoreRecordFilterTimeMode"
              :placeholder="t('protection.backupsPage.flowRestoreCreatedTime')"
              style="width: 150px"
              popper-class="dp-source-task-select-popper"
              @change="onRestoreRecordTimeModeChange"
            >
              <ElOption
                v-for="option in detailTimeModeOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </ElSelect>
            <ElDatePicker
              v-if="restoreRecordFilterTimeMode === 'range'"
              v-model="restoreRecordFilterDateRange"
              type="datetimerange"
              format="YYYY-MM-DD HH:mm:ss"
              :default-time="[new Date(2000, 0, 1, 0, 0, 0), new Date(2000, 0, 1, 23, 59, 59)]"
              :shortcuts="sourceTaskDateRangeShortcuts"
              :start-placeholder="t('ops.task.startTime')"
              :end-placeholder="t('ops.task.endTime')"
              popper-class="dp-source-task-select-popper"
              class="dp-source-record-toolbar__range"
            />
            <div class="hfl-list-toolbar__right">
              <ElButton
                class="hfl-refresh-button"
                :title="t('ops.task.btnRefresh')"
                :aria-label="t('ops.task.btnRefresh')"
                :disabled="restoreRecordsLoading"
                @click="loadRestoreRecordsForSource()"
              >
                <RefreshCw
                  :size="16"
                  :class="{ 'is-spinning': restoreRecordsLoading }"
                />
              </ElButton>
            </div>
          </div>
          <el-alert
            v-if="restoreRecordsError"
            :title="t('protection.backupsPage.flowSourceDetailRestoreRecordsLoadFailed', { msg: restoreRecordsError })"
            type="error"
            show-icon
            :closable="false"
          />
          <el-table
            v-if="displayedRestoreRecords.length || restoreRecordsLoading"
            ref="restoreTableRef"
            v-table-column-resize="'protection.flowBackupSource.restoreRecords'"
            v-table-overflow-title
            v-loading="restoreRecordsLoading"
            :data="displayedRestoreRecords"
            :fit="false"
            row-key="id"
            :max-height="restoreTableMaxHeight"
            :expand-row-keys="expandedRestoreRecordRowKeys"
            :header-cell-style="TABLE_HEADER_STYLE"
            stripe
            class="hfl-list-table restore-task-drawer-table restore-records-table"
            @expand-change="onRestoreRecordExpandChange"
          >
            <el-table-column
              type="expand"
              width="35"
              fixed
            >
              <template #default="{ row }">
                <div class="restore-record-expand-panel">
                  <div class="restore-record-time-summary">
                    <div class="restore-record-time-summary__point restore-record-time-summary__point--start">
                      <span
                        class="restore-record-time-summary__marker"
                        aria-hidden="true"
                      >
                        <Clock3 :size="14" />
                      </span>
                      <div class="restore-record-time-summary__copy">
                        <span class="restore-record-time-summary__label">
                          {{ t('protection.backupDetail.colStart') }}
                        </span>
                        <span
                          class="restore-record-time-summary__value"
                          :class="{ 'hfl-empty-mark': !(row.task_summary?.started_at || row.created_at) }"
                        >
                          {{ formatNullableTime(row.task_summary?.started_at || row.created_at) }}
                        </span>
                      </div>
                    </div>

                    <div class="restore-record-time-summary__duration">
                      <span class="restore-record-time-summary__duration-pill">
                        <span class="restore-record-time-summary__duration-label">
                          {{ t('protection.backupsPage.flowSourceDetailDuration') }}
                        </span>
                        <span
                          class="restore-record-time-summary__duration-value"
                          :class="{ 'hfl-empty-mark': restoreRecordDuration(row) === t('protection.backupDetail.durationDash') }"
                        >
                          {{ restoreRecordDuration(row) }}
                        </span>
                      </span>
                    </div>

                    <div
                      class="restore-record-time-summary__point restore-record-time-summary__point--end"
                      :class="{ 'restore-record-time-summary__point--pending': !row.task_summary?.finished_at }"
                    >
                      <span
                        class="restore-record-time-summary__marker"
                        aria-hidden="true"
                      >
                        <Check
                          v-if="row.task_summary?.finished_at"
                          :size="14"
                        />
                        <Clock3
                          v-else
                          :size="14"
                        />
                      </span>
                      <div class="restore-record-time-summary__copy">
                        <span class="restore-record-time-summary__label">
                          {{ t('protection.backupDetail.colEnd') }}
                        </span>
                        <span
                          class="restore-record-time-summary__value"
                          :class="{ 'hfl-empty-mark': !row.task_summary?.finished_at }"
                        >
                          {{ formatNullableTime(row.task_summary?.finished_at) }}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div class="restore-record-runtime-summary">
                    <span class="restore-record-runtime-summary__label">
                      {{ t('protection.backupsPage.flowRestoreRecordRuntimeSummary') }}
                    </span>
                    <div
                      v-if="restoreRecordMetrics(row).length"
                      class="restore-record-runtime-summary__metrics"
                    >
                      <span
                        v-for="metric in restoreRecordMetrics(row)"
                        :key="metric"
                        class="restore-record-runtime-summary__metric"
                      >
                        {{ metric }}
                      </span>
                    </div>
                    <span
                      v-else
                      class="restore-record-runtime-summary__unavailable"
                    >
                      {{ restoreRecordRuntimeLoading(row)
                        ? t('common.loading')
                        : t('protection.backupsPage.flowRestoreRecordMetricsUnavailable') }}
                    </span>
                  </div>
                  <div
                    v-if="row.items?.length"
                    class="dp-flow-restore-plan-card restore-record-structure-card"
                  >
                    <div class="create-recovery-plan-cell create-recovery-plan-cell--review create-recovery-plan-cell--enabled">
                      <div
                        v-if="row.scope === 'snapshot'"
                        class="restore-record-snapshot-tree"
                      >
                        <div class="create-recovery-plan-mapping restore-record-mapping--with-result restore-record-snapshot-tree__parent">
                          <span
                            class="create-recovery-plan-mapping__endpoint create-recovery-plan-mapping__endpoint--snapshot"
                            :title="t('protection.backupsPage.recoveryWholeSnapshot')"
                          >
                            <Camera
                              :size="14"
                              class="create-recovery-plan-mapping__icon"
                            />
                            <span class="create-recovery-plan-mapping__text">
                              {{ t('protection.backupsPage.recoveryWholeSnapshot') }}
                            </span>
                          </span>
                          <span
                            class="create-recovery-plan-mapping__arrow"
                            aria-hidden="true"
                          >-&gt;</span>
                          <span
                            class="create-recovery-plan-mapping__endpoint create-recovery-plan-mapping__endpoint--target"
                            :title="restoreRecordTargetSummary(row)"
                          >
                            <FolderOpen
                              :size="14"
                              class="create-recovery-plan-mapping__icon"
                            />
                            <span class="create-recovery-plan-mapping__text hfl-table-cell-mono">
                              {{ restoreRecordTargetSummary(row) }}
                            </span>
                          </span>
                          <span class="restore-record-mapping__result">
                            <TaskStatusTag
                              v-if="restoreRecordStatus(row)"
                              :status="restoreRecordStatus(row)"
                            />
                            <ElTag
                              v-else
                              type="info"
                              size="small"
                            >
                              {{ t('protection.backupsPage.flowRestoreRecordStatusUnknown') }}
                            </ElTag>
                          </span>
                        </div>

                        <div class="restore-record-snapshot-tree__children">
                          <div
                            v-for="item in row.items"
                            :key="item.id"
                            class="restore-record-structure-entry restore-record-snapshot-tree__child"
                          >
                            <div class="create-recovery-plan-mapping restore-record-mapping--with-result">
                              <span
                                class="create-recovery-plan-mapping__endpoint"
                                :class="`create-recovery-plan-mapping__endpoint--${restoreItemSourceKind(item)}`"
                                :title="item.source_path || '—'"
                              >
                                <File
                                  v-if="restoreItemSourceKind(item) === 'file'"
                                  :size="14"
                                  class="create-recovery-plan-mapping__icon"
                                />
                                <Folder
                                  v-else
                                  :size="14"
                                  class="create-recovery-plan-mapping__icon"
                                />
                                <span
                                  class="create-recovery-plan-mapping__text hfl-table-cell-mono"
                                  :class="{ 'hfl-empty-mark': !item.source_path }"
                                >
                                  {{ item.source_path || '—' }}
                                </span>
                              </span>
                              <span
                                class="create-recovery-plan-mapping__arrow"
                                aria-hidden="true"
                              >-&gt;</span>
                              <span
                                class="create-recovery-plan-mapping__endpoint create-recovery-plan-mapping__endpoint--target"
                                :title="restoreItemTargetSummary(row, item)"
                              >
                                <FolderOpen
                                  :size="14"
                                  class="create-recovery-plan-mapping__icon"
                                />
                                <span class="create-recovery-plan-mapping__text hfl-table-cell-mono">
                                  {{ restoreItemTargetSummary(row, item) }}
                                </span>
                              </span>
                              <span class="restore-record-mapping__result">
                                <TaskStatusTag :status="item.status" />
                              </span>
                            </div>
                            <div
                              v-if="item.error_code || item.error_message"
                              class="restore-record-structure-entry__error"
                            >
                              {{ item.error_code ? `[${item.error_code}] ` : '' }}{{ item.error_message }}
                            </div>
                          </div>
                        </div>
                      </div>

                      <div
                        v-else
                        class="create-recovery-plan-cell__mappings restore-record-flat-mappings"
                      >
                        <div
                          v-for="mapping in restoreRecordPathMappings(row)"
                          :key="mapping.key"
                          class="restore-record-structure-entry"
                        >
                          <div class="create-recovery-plan-mapping restore-record-mapping--with-result">
                            <span
                              class="create-recovery-plan-mapping__endpoint"
                              :class="`create-recovery-plan-mapping__endpoint--${mapping.sourceKind}`"
                              :title="mapping.sourcePath"
                            >
                              <File
                                v-if="mapping.sourceKind === 'file'"
                                :size="14"
                                class="create-recovery-plan-mapping__icon"
                              />
                              <Folder
                                v-else
                                :size="14"
                                class="create-recovery-plan-mapping__icon"
                              />
                              <span class="create-recovery-plan-mapping__text hfl-table-cell-mono">
                                {{ mapping.sourcePath }}
                              </span>
                            </span>
                            <span
                              class="create-recovery-plan-mapping__arrow"
                              aria-hidden="true"
                            >-&gt;</span>
                            <span
                              class="create-recovery-plan-mapping__endpoint create-recovery-plan-mapping__endpoint--target"
                              :title="restoreItemTargetSummary(row, mapping.item)"
                            >
                              <FolderOpen
                                :size="14"
                                class="create-recovery-plan-mapping__icon"
                              />
                              <span class="create-recovery-plan-mapping__text hfl-table-cell-mono">
                                {{ restoreItemTargetSummary(row, mapping.item) }}
                              </span>
                            </span>
                            <span class="restore-record-mapping__result">
                              <TaskStatusTag :status="mapping.item.status" />
                            </span>
                          </div>
                          <div
                            v-if="mapping.item.error_code || mapping.item.error_message"
                            class="restore-record-structure-entry__error"
                          >
                            {{ mapping.item.error_code ? `[${mapping.item.error_code}] ` : '' }}{{ mapping.item.error_message }}
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                  <el-empty
                    v-else
                    :description="t('protection.backupsPage.flowRestoreRecordMappingsEmpty')"
                    :image-size="48"
                  />
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupsPage.flowRestoreRecordColName')"
              width="148"
              fixed
            >
              <template #default="{ row }">
                <button
                  type="button"
                  class="hfl-table-name-link hfl-table-cell-mono hfl-table-name-link--single"
                  :aria-expanded="expandedRestoreRecordRowKeys.includes(row.id)"
                  @click.stop="toggleRestoreRecord(row)"
                >
                  {{ row.restore_uid || `#${row.id}` }}
                </button>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupDetail.colTaskId')"
              width="160"
            >
              <template #default="{ row }">
                <button
                  v-if="row.task_uuid"
                  type="button"
                  class="hfl-table-name-link hfl-table-cell-mono hfl-table-name-link--single"
                  :title="row.task_uuid"
                  @click.stop="openTaskDetailByUuid(row.task_uuid)"
                >
                  {{ row.task_uuid }}
                </button>
                <span
                  v-else
                  class="hfl-empty-mark"
                >{{ t('protection.backupDetail.durationDash') }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupsPage.flowRestoreRecordColStatusProgress')"
              width="148"
            >
              <template #default="{ row }">
                <div class="restore-record-status-progress">
                  <div
                    v-if="shouldShowRestoreRecordProgress(row)"
                    class="restore-record-status-progress__bar"
                  >
                    <ElProgress
                      :percentage="restoreRecordProgressValue(row)"
                      :stroke-width="6"
                      :show-text="false"
                    />
                    <span>{{ restoreRecordProgressText(row) }}</span>
                  </div>
                  <TaskStatusTag
                    v-else-if="restoreRecordStatus(row)"
                    :status="restoreRecordStatus(row)"
                  />
                  <ElTag
                    v-else
                    type="info"
                    size="small"
                  >
                    {{ t('protection.backupsPage.flowRestoreRecordStatusUnknown') }}
                  </ElTag>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupDetail.colSnapId')"
              width="168"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-mono"
                  :title="restoreRecordSnapshotLabel(row)"
                >
                  {{ restoreRecordSnapshotLabel(row) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupsPage.flowRestoreRecordColConflict')"
              width="96"
            >
              <template #default="{ row }">
                <ElTag
                  :type="row.conflict_mode === 'overwrite' ? 'warning' : 'success'"
                  size="small"
                  effect="plain"
                >
                  {{ restoreRecordConflictLabel(row.conflict_mode) }}
                </ElTag>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupsPage.flowRestoreRecordColMode')"
              width="132"
            >
              <template #default="{ row }">
                <ElTag
                  size="small"
                  effect="plain"
                  :title="restoreRecordModeTitle(row)"
                >
                  {{ restoreRecordModeLabel(row) }}
                </ElTag>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupsPage.flowRestoreRecordColCreated')"
              width="150"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-time"
                  :class="{ 'hfl-empty-mark': !row.created_at }"
                >{{ formatNullableTime(row.created_at) }}</span>
              </template>
            </el-table-column>
          </el-table>
          <el-empty
            v-else-if="!restoreRecordsLoading && !restoreRecordsError"
            :description="t('protection.backupsPage.flowSourceDetailRestoreRecordsEmpty')"
            :image-size="72"
          />
          <div
            v-if="displayedRestoreRecords.length || restoreRecordPagination.count > 0"
            class="hfl-list-footer"
          >
            <HflPagination
              v-model:current-page="restoreRecordPagination.page"
              v-model:page-size="restoreRecordPagination.pageSize"
              class="hfl-list-footer__pagination"
              layout="total, sizes, prev, pager, next"
              :total="restoreRecordPagination.count"
              :page-sizes="DETAIL_PAGE_SIZE_OPTIONS"
              @size-change="restoreRecordPagination.page = 1"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane
          :label="t('protection.backupDetail.tabTasks')"
          name="tasks"
        >
          <el-alert
            v-if="sourceTasksError"
            :title="t('protection.backupsPage.flowSourceDetailTasksLoadFailed', { msg: sourceTasksError })"
            type="error"
            show-icon
            :closable="false"
          />
          <div class="hfl-list-toolbar dp-source-task-toolbar">
            <ElInput
              v-model="taskFilterId"
              clearable
              class="hfl-list-search dp-source-task-toolbar__search"
              :placeholder="t('ops.task.phSearch')"
              @clear="clearSourceTaskSearch"
            >
              <template #prepend>
                <ElSelect
                  v-model="taskFilterSearchField"
                  style="width: 90px"
                  popper-class="dp-source-task-select-popper"
                  @change="handleSourceTaskSearchFieldChange"
                >
                  <ElOption
                    v-for="opt in taskSearchFieldOptions"
                    :key="opt.value"
                    :value="opt.value"
                    :label="opt.label"
                  />
                </ElSelect>
              </template>
              <template #prefix>
                <Search
                  :size="16"
                  class="text-slate-400"
                />
              </template>
            </ElInput>
            <ElSelect
              v-model="taskFilterType"
              clearable
              :placeholder="t('ops.task.filterType')"
              style="width: 130px"
              popper-class="dp-source-task-select-popper"
            >
              <ElOption
                v-for="option in taskTypeOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </ElSelect>
            <ElSelect
              v-model="taskFilterStatus"
              clearable
              :placeholder="t('ops.task.filterStatus')"
              style="width: 130px"
              popper-class="dp-source-task-select-popper"
            >
              <ElOption
                v-for="option in taskStatusOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </ElSelect>
            <ElSelect
              v-model="taskFilterTimeMode"
              :placeholder="t('ops.task.filterTime')"
              style="width: 130px"
              popper-class="dp-source-task-select-popper"
              @change="onSourceTaskTimeModeChange"
            >
              <ElOption
                v-for="option in sourceTaskTimeModeOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </ElSelect>
            <div class="hfl-list-toolbar__right">
              <ElBadge
                :value="sourceTaskAdvancedFilterCount"
                :hidden="sourceTaskAdvancedFilterCount === 0"
                class="dp-source-task-filter-badge"
              >
                <ElButton
                  :class="{ 'dp-source-task-filter-button--active': sourceTaskAdvancedFilterCount > 0 }"
                  :title="t('ops.task.advancedFilter')"
                  :aria-label="t('ops.task.advancedFilter')"
                  @click="openSourceTaskAdvancedFilterDrawer"
                >
                  <Filter :size="16" />
                </ElButton>
              </ElBadge>
              <ElButton
                class="hfl-refresh-button"
                :title="t('ops.task.btnRefresh')"
                :aria-label="t('ops.task.btnRefresh')"
                :disabled="sourceTasksLoading"
                @click="loadTasksForSource"
              >
                <RefreshCw
                  :size="16"
                  :class="{ 'is-spinning': sourceTasksLoading }"
                />
              </ElButton>
            </div>
          </div>
          <el-table
            ref="sourceTasksTableRef"
            v-table-column-resize="'protection.flowBackupSource.sourceTasks'"
            v-table-overflow-title
            v-loading="sourceTasksLoading"
            :data="sourceTaskRows"
            stripe
            :max-height="sourceTasksTableMaxHeight"
            class="restore-task-drawer-table dp-source-task-table hfl-list-table"
            :row-class-name="sourceTaskRowClassName"
          >
            <el-table-column
              :label="t('ops.task.colName')"
              width="275"
              fixed
            >
              <template #default="{ row }">
                <ResourceNameSummaryCell
                  :name="taskDetailTitle(row)"
                  :summary="row.task_uuid"
                  kind="task"
                  :show-icon="false"
                  @open="openTaskDetail(row)"
                />
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupDetail.colTaskType')"
              width="205"
            >
              <template #default="{ row }">
                <TaskTypeLabel :type="row.task_type" />
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupDetail.colTaskStatus')"
              width="115"
            >
              <template #default="{ row }">
                <TaskStatusTag :status="row.status" />
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupsPage.flowTaskColProgress')"
              min-width="165"
            >
              <template #default="{ row }">
                <div class="hfl-task-list-progress">
                  <div class="hfl-task-list-progress__track">
                    <div
                      class="hfl-task-list-progress__fill"
                      :class="`hfl-task-list-progress__fill--${row.status}`"
                      :style="{ width: `${progressValue(row)}%` }"
                    />
                  </div>
                  <span class="hfl-task-list-progress__text">{{ progressText(row) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.task.colTrigger')"
              width="105"
            >
              <template #default="{ row }">
                <el-tag
                  type="info"
                  size="small"
                >
                  {{ taskTriggerLabel(row.trigger_type) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupDetail.colCreated')"
              min-width="160"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-time"
                  :class="{ 'hfl-empty-mark': !row.created_at }"
                >{{ formatNullableTime(row.created_at) }}</span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty
                v-if="!sourceTasksLoading && !sourceTasksError"
                :description="t('protection.backupsPage.flowSourceDetailTasksEmpty')"
                :image-size="72"
              />
            </template>
          </el-table>
          <div class="hfl-list-footer">
            <HflPagination
              v-model:current-page="taskPagination.page"
              v-model:page-size="taskPagination.pageSize"
              class="hfl-list-footer__pagination"
              layout="total, sizes, prev, pager, next"
              :total="sourceTasksTotal"
              :page-sizes="DETAIL_PAGE_SIZE_OPTIONS"
              @size-change="taskPagination.page = 1"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </el-drawer>

  <ElDrawer
    v-model="taskAdvancedFilterOpen"
    :title="t('ops.task.advancedFilter')"
    :size="nestedDrawerSize"
    class="dp-source-task-filter-drawer"
    :z-index="3150"
    @closed="onSourceTaskAdvancedFilterClosed"
  >
    <ElForm
      label-position="top"
      class="dp-source-task-filter-form"
    >
      <ElFormItem :label="t('ops.task.filterTime')">
        <ElDatePicker
          v-model="taskAdvancedFilterDraft.dateRange"
          type="datetimerange"
          format="YYYY-MM-DD HH:mm:ss"
          value-format="YYYY-MM-DDTHH:mm:ss"
          :default-time="[new Date(2000, 0, 1, 0, 0, 0), new Date(2000, 0, 1, 23, 59, 59)]"
          :shortcuts="sourceTaskDateRangeShortcuts"
          :start-placeholder="t('ops.task.startTime')"
          :end-placeholder="t('ops.task.endTime')"
          popper-class="dp-source-task-select-popper"
          class="dp-source-task-toolbar__range"
        />
      </ElFormItem>
    </ElForm>
    <template #footer>
      <div class="dp-source-task-filter-drawer__footer">
        <ElButton @click="resetSourceTaskAdvancedFilterDraft">
          {{ t('ops.task.resetFilter') }}
        </ElButton>
        <ElButton
          type="primary"
          @click="applySourceTaskAdvancedFilters"
        >
          {{ t('ops.task.applyFilter') }}
        </ElButton>
      </div>
    </template>
  </ElDrawer>

  <ElDrawer
    v-model="taskDetailOpen"
    class="hfl-task-drawer dp-task-detail-drawer"
    :size="taskDetailDrawerSize"
    :z-index="3200"
    @opened="resetDrawerScroll"
    @closed="onTaskDetailClosed"
  >
    <template #header>
      <div class="dp-task-detail__header-bar">
        <div
          v-if="activeTask"
          class="dp-task-detail__header-summary"
        >
          <div class="dp-task-detail__header-title-row">
            <h2 class="dp-task-detail__header-title">
              {{ taskDetailTitle(activeTask) }}
            </h2>
            <TaskStatusTag :status="activeTask.status" />
          </div>
          <div class="dp-task-detail__header-meta">
            <span>{{ t('protection.backupsPage.flowSourceDetailTaskOwner') }}: {{ source?.name || taskTypeLabel(activeTask.task_type) }}</span>
            <span class="dp-task-detail__header-divider" />
            <span class="dp-task-detail__uuid">
              <span class="dp-task-detail__uuid-text">{{ activeTask.task_uuid }}</span>
              <ElButton
                class="dp-task-detail__copy-button"
                text
                :title="t('protection.backupsPage.flowSourceDetailCopyUuid')"
                @click.stop="copyText(activeTask.task_uuid)"
              >
                <Copy :size="13" />
              </ElButton>
            </span>
          </div>
        </div>
        <h2
          v-else
          class="dp-task-detail__header-title"
        >
          {{ t('protection.backupsPage.backupTaskDrawerTitle') }}
        </h2>
        <div class="dp-task-detail__header-actions">
          <ElButton
            v-if="canCancelBackupTask"
            class="dp-task-detail__cancel-button"
            :loading="backupTaskActionBusy"
            :disabled="backupTaskActionBusy || activeTaskLoading"
            @click="cancelActiveBackupTask"
          >
            <span class="dp-task-detail__cancel-label">
              <CircleStop :size="15" />
              <span>{{ t('ops.task.btnCancel') }}</span>
            </span>
          </ElButton>
          <button
            v-if="activeTask"
            type="button"
            class="hfl-drawer-header-action"
            :title="t('protection.backupsPage.backupTaskDrawerRefresh')"
            :aria-label="t('protection.backupsPage.backupTaskDrawerRefresh')"
            :disabled="activeTaskLoading"
            @click="refreshActiveTask"
          >
            <RefreshCw
              :size="20"
              :class="{ 'is-spinning': activeTaskLoading }"
            />
          </button>
        </div>
      </div>
    </template>

    <div
      v-if="activeTask"
      ref="drawerScrollAnchorRef"
      v-loading="activeTaskLoading"
      class="hfl-task-drawer__body dp-task-detail__body"
    >
      <section class="dp-task-detail__hero">
        <div class="dp-task-detail__hero-section-title">
          {{ t('protection.backupsPage.flowSourceDetailBasicData') }}
        </div>
        <div class="dp-task-detail__hero-grid">
          <div class="dp-task-detail__metric">
            <Globe
              :size="18"
              class="dp-task-detail__metric-icon dp-task-detail__metric-icon--blue"
            />
            <div class="dp-task-detail__metric-copy">
              <span class="dp-task-detail__metric-label">{{ t('protection.backupsPage.flowSourceDetailTypeAndTrigger') }}</span>
              <div class="dp-task-detail__metric-tags">
                <span
                  class="dp-source-task-type-pill"
                  :class="`dp-source-task-type-pill--${activeTask.task_type}`"
                >
                  <span class="dp-source-task-pill-text">{{ taskTypeLabel(activeTask.task_type) }}</span>
                </span>
                <span
                  class="dp-source-task-trigger-pill"
                  :class="`dp-source-task-trigger-pill--${activeTask.trigger_type}`"
                >
                  <span class="dp-source-task-pill-text">{{ taskTriggerLabel(activeTask.trigger_type) }}</span>
                </span>
              </div>
            </div>
          </div>

          <div class="dp-task-detail__metric">
            <RotateCcw
              :size="18"
              class="dp-task-detail__metric-icon dp-task-detail__metric-icon--indigo"
            />
            <div class="dp-task-detail__metric-copy">
              <span class="dp-task-detail__metric-label">{{ t('protection.backupsPage.flowSourceDetailRetryCount') }}</span>
              <span class="dp-task-detail__metric-value">{{ activeTask.retry_count }}</span>
            </div>
          </div>

          <div class="dp-task-detail__metric dp-task-detail__metric--wide">
            <Clock3
              :size="18"
              class="dp-task-detail__metric-icon"
            />
            <div class="dp-task-detail__time-grid">
              <div>
                <span class="dp-task-detail__metric-label">{{ t('protection.backupDetail.colStart') }}</span>
                <span
                  class="dp-task-detail__time-value"
                  :class="{ 'hfl-empty-mark': !(activeTask.started_at || activeTask.created_at) }"
                >{{ formatNullableTime(activeTask.started_at || activeTask.created_at) }}</span>
              </div>
              <div>
                <span class="dp-task-detail__metric-label">{{ t('protection.backupDetail.colEnd') }}</span>
                <span
                  class="dp-task-detail__time-value"
                  :class="{ 'hfl-empty-mark': !activeTask.finished_at }"
                >{{ formatNullableTime(activeTask.finished_at) }}</span>
              </div>
              <div>
                <span class="dp-task-detail__metric-label">{{ t('protection.backupsPage.flowSourceDetailDuration') }}</span>
                <span
                  class="dp-task-detail__time-value dp-task-detail__time-value--strong"
                  :class="{ 'hfl-empty-mark': taskDuration(activeTask) === t('protection.backupDetail.durationDash') }"
                >{{ taskDuration(activeTask) }}</span>
              </div>
            </div>
          </div>
        </div>

        <TaskProgressCell
          v-if="activeTask.status === 'pending' || activeTask.status === 'waiting' || activeTask.status === 'blocked' || activeTask.status === 'running'"
          :progress="progressValue(activeTask)"
          :transfer-progress="activeTransferProgress"
          :failed="false"
        />
        <div
          v-else
          class="dp-task-detail__progress-block"
        >
          <div class="dp-task-detail__progress-head">
            <span>{{ t('protection.backupsPage.flowTaskColProgress') }}</span>
            <span>{{ progressText(activeTask) }}</span>
          </div>
          <div class="dp-task-detail__progress-track">
            <div
              class="dp-task-detail__progress-fill"
              :class="`dp-task-detail__progress-fill--${activeTask.status}`"
              :style="{ width: `${progressValue(activeTask)}%` }"
            />
          </div>
        </div>
      </section>

      <ElAlert
        v-if="['waiting', 'blocked'].includes(activeTask.status) && activeTaskDependencies.length"
        :title="activeTask.status === 'blocked' ? t('ops.task.blockedTitle') : t('ops.task.waitingTitle')"
        type="warning"
        :closable="false"
        show-icon
      >
        <p>{{ activeTask.status === 'blocked' ? t('ops.task.blockedDescription') : t('ops.task.waitingDescription') }}</p>
        <ul>
          <li
            v-for="dependency in activeTaskDependencies"
            :key="dependency.id"
          >
            <span>{{ dependency.detail }}</span>
            <ElButton
              v-if="dependency.blocking_task_uuid"
              link
              type="primary"
              @click="openTaskDetailByUuid(dependency.blocking_task_uuid)"
            >
              {{ t('ops.task.viewBlockingTask') }}
            </ElButton>
          </li>
        </ul>
      </ElAlert>

      <ElTabs
        v-model="activeTaskDetailTab"
        class="hfl-detail-tabs dp-task-detail__tabs"
        @tab-change="onTaskDetailTabChange"
      >
        <ElTabPane name="steps">
          <template #label>
            <span class="hfl-detail-tab-label">
              {{ t('protection.backupsPage.flowSourceDetailSteps') }}
              <span class="hfl-detail-tab-count">{{ activeTask.steps?.length || 0 }}</span>
            </span>
          </template>
          <section class="dp-task-detail__tab-panel">
            <div class="dp-task-detail__steps-head">
              <span>{{ t('protection.backupsPage.flowSourceDetailStepsHint') }}</span>
              <ElButton
                v-if="hasExpandableTaskSteps"
                size="small"
                class="hfl-btn-with-icon"
                @click="toggleAllTaskStepsExpanded"
              >
                {{ hasAnyExpandedTaskStep
                  ? t('protection.backupsPage.flowSourceDetailCollapseAll')
                  : t('protection.backupsPage.flowSourceDetailExpandAll') }}
                <ChevronDown
                  v-if="hasAnyExpandedTaskStep"
                  :size="16"
                  class="hfl-task-step-chevron"
                />
                <ChevronRight
                  v-else
                  :size="16"
                  class="hfl-task-step-chevron"
                />
              </ElButton>
            </div>

            <div
              v-if="stepsWithEvents.length"
              class="dp-task-detail__step-list"
            >
              <div
                v-for="(step, si) in stepsWithEvents"
                :key="step.id"
                class="dp-task-detail__step-item"
                :class="{ 'dp-task-detail__step-item--last': si === stepsWithEvents.length - 1 && unlinkedTaskEvents.length === 0 }"
              >
                <div
                  class="dp-task-detail__step-anchor"
                  :class="timelineIconClass(step.status)"
                >
                  <Check
                    v-if="step.status === 'success'"
                    :size="15"
                  />
                  <X
                    v-else-if="step.status === 'failed' || step.status === 'timeout'"
                    :size="15"
                  />
                  <LoaderCircle
                    v-else-if="step.status === 'running'"
                    :size="15"
                  />
                  <Circle
                    v-else
                    :size="9"
                  />
                </div>

                <article class="dp-task-detail__step-card">
                  <button
                    type="button"
                    class="dp-task-detail__step-card-head"
                    :class="{ 'dp-task-detail__step-card-head--disabled': step.events.length === 0 }"
                    :aria-expanded="step.events.length > 0 && isStepExpanded(step.id)"
                    :aria-disabled="step.events.length === 0"
                    :aria-description="step.events.length === 0 ? t('ops.task.emptyEvents') : undefined"
                    @click="toggleStep(step.id, step.events.length)"
                  >
                    <span class="dp-task-detail__step-title">
                      {{ stepDisplayName(step.step_name, activeTask.task_type) }}
                      <span
                        class="dp-task-detail__step-executed-at"
                        :class="{ 'hfl-empty-mark': !(step.created_at || activeTask.created_at) }"
                      >{{ formatNullableTime(step.created_at || activeTask.created_at) }}</span>
                    </span>
                    <TaskStatusTag :status="step.status" />
                    <span
                      class="dp-task-detail__step-duration"
                      :class="{ 'hfl-empty-mark': stepDuration(si) === t('protection.backupDetail.durationDash') }"
                    >
                      <Clock3 :size="12" />
                      {{ stepDuration(si) }}
                    </span>
                    <ElTooltip
                      v-if="step.events.length === 0"
                      :content="t('ops.task.emptyEvents')"
                      teleported
                      append-to="body"
                      :z-index="3600"
                      placement="top"
                      :show-after="200"
                    >
                      <span class="hfl-task-step-chevron hfl-task-step-chevron--disabled">
                        <ChevronRight
                          :size="16"
                          aria-hidden="true"
                        />
                      </span>
                    </ElTooltip>
                    <ChevronDown
                      v-else-if="isStepExpanded(step.id)"
                      :size="16"
                      class="hfl-task-step-chevron"
                    />
                    <ChevronRight
                      v-else
                      :size="16"
                      class="hfl-task-step-chevron"
                    />
                  </button>

                  <div
                    v-if="step.events.length > 0 && isStepExpanded(step.id)"
                    class="dp-task-detail__event-list"
                  >
                    <div
                      v-for="event in step.events"
                      :key="event.id"
                      class="dp-task-detail__event-row"
                    >
                      <span
                        class="dp-task-detail__event-dot"
                        :class="`dp-task-detail__event-dot--${eventTone(event)}`"
                      >
                        <X
                          v-if="eventTone(event) === 'danger'"
                          :size="9"
                        />
                        <Circle
                          v-else-if="eventTone(event) === 'muted'"
                          :size="7"
                        />
                        <Check
                          v-else
                          :size="9"
                        />
                      </span>
                      <div class="dp-task-detail__event-content">
                        <span
                          class="dp-task-detail__event-msg"
                          :class="eventMessageClass(event)"
                        >{{ eventDisplayMessage(event) }}</span>
                        <span
                          v-if="eventObjectText(event)"
                          class="dp-task-detail__event-object"
                        >
                          <Link2 :size="11" />
                          <span>{{ eventObjectText(event) }}</span>
                        </span>
                        <span
                          v-if="eventErrorText(event)"
                          class="dp-task-detail__event-error"
                        >{{ eventErrorText(event) }}</span>
                        <TaskEventFailureDetails :metadata="event.metadata" />
                      </div>
                      <span
                        class="dp-task-detail__event-time"
                        :class="{ 'hfl-empty-mark': !event.created_at }"
                      >{{ formatNullableTime(event.created_at) }}</span>
                    </div>
                  </div>
                </article>
              </div>

              <div
                v-if="unlinkedTaskEvents.length > 0"
                class="dp-task-detail__step-item dp-task-detail__step-item--last"
              >
                <div class="dp-task-detail__step-anchor dp-task-detail__timeline-icon--muted">
                  <Circle :size="9" />
                </div>
                <article class="dp-task-detail__step-card">
                  <div class="dp-task-detail__step-card-head dp-task-detail__step-card-head--static">
                    <span class="dp-task-detail__step-title">{{ t('protection.backupsPage.flowSourceDetailEvents') }}</span>
                    <span
                      class="dp-task-detail__step-duration"
                      :class="{ 'hfl-empty-mark': taskDuration(activeTask) === t('protection.backupDetail.durationDash') }"
                    >{{ taskDuration(activeTask) }}</span>
                  </div>
                  <div class="dp-task-detail__event-list">
                    <div
                      v-for="event in unlinkedTaskEvents"
                      :key="event.id"
                      class="dp-task-detail__event-row"
                    >
                      <span
                        class="dp-task-detail__event-dot"
                        :class="`dp-task-detail__event-dot--${eventTone(event)}`"
                      >
                        <X
                          v-if="eventTone(event) === 'danger'"
                          :size="9"
                        />
                        <Circle
                          v-else-if="eventTone(event) === 'muted'"
                          :size="7"
                        />
                        <Check
                          v-else
                          :size="9"
                        />
                      </span>
                      <div class="dp-task-detail__event-content">
                        <span
                          class="dp-task-detail__event-msg"
                          :class="eventMessageClass(event)"
                        >{{ eventDisplayMessage(event) }}</span>
                        <span
                          v-if="eventObjectText(event)"
                          class="dp-task-detail__event-object"
                        >
                          <Link2 :size="11" />
                          <span>{{ eventObjectText(event) }}</span>
                        </span>
                        <span
                          v-if="eventErrorText(event)"
                          class="dp-task-detail__event-error"
                        >{{ eventErrorText(event) }}</span>
                        <TaskEventFailureDetails :metadata="event.metadata" />
                      </div>
                      <span class="dp-task-detail__event-time">#{{ event.seq }} · <span :class="{ 'hfl-empty-mark': !event.created_at }">{{ formatNullableTime(event.created_at) }}</span></span>
                    </div>
                  </div>
                </article>
              </div>
            </div>

            <div
              v-else-if="taskDetailEvents.length"
              class="dp-task-detail__event-only"
            >
              <div
                v-for="event in taskDetailEvents"
                :key="event.id"
                class="dp-task-detail__event-row"
              >
                <span
                  class="dp-task-detail__event-dot"
                  :class="`dp-task-detail__event-dot--${eventTone(event)}`"
                >
                  <X
                    v-if="eventTone(event) === 'danger'"
                    :size="9"
                  />
                  <Circle
                    v-else-if="eventTone(event) === 'muted'"
                    :size="7"
                  />
                  <Check
                    v-else
                    :size="9"
                  />
                </span>
                <div class="dp-task-detail__event-content">
                  <span
                    class="dp-task-detail__event-msg"
                    :class="eventMessageClass(event)"
                  >{{ eventDisplayMessage(event) }}</span>
                  <span
                    v-if="eventObjectText(event)"
                    class="dp-task-detail__event-object"
                  >
                    <Link2 :size="11" />
                    <span>{{ eventObjectText(event) }}</span>
                  </span>
                  <span
                    v-if="eventErrorText(event)"
                    class="dp-task-detail__event-error"
                  >{{ eventErrorText(event) }}</span>
                  <TaskEventFailureDetails :metadata="event.metadata" />
                </div>
                <span class="dp-task-detail__event-time">#{{ event.seq }} · <span :class="{ 'hfl-empty-mark': !event.created_at }">{{ formatNullableTime(event.created_at) }}</span></span>
              </div>
            </div>

            <el-empty
              v-else
              :description="t('protection.backupsPage.flowSourceDetailEmptySteps')"
              :image-size="52"
            />
          </section>
        </ElTabPane>

        <ElTabPane name="resources">
          <template #label>
            <span class="hfl-detail-tab-label">
              {{ t('protection.backupsPage.flowSourceDetailResources') }}
              <span class="hfl-detail-tab-count">{{ activeTask.resources?.length || 0 }}</span>
            </span>
          </template>
          <section class="dp-task-detail__tab-panel">
            <div
              v-if="resourceTypeTabs.length"
              class="dp-task-detail__resource-view"
            >
              <div class="dp-task-detail__resource-switcher">
                <button
                  v-for="item in resourceTypeTabs"
                  :key="item.type"
                  type="button"
                  class="dp-task-detail__resource-switch"
                  :class="{ 'dp-task-detail__resource-switch--active': selectedResourceType === item.type }"
                  @click="loadResourceType(item.type)"
                >
                  <Link2 :size="14" />
                  {{ item.label }}
                  <span class="hfl-task-drawer__resource-switch-count">{{ item.count }}</span>
                </button>
              </div>

              <div
                v-if="resourceErrors[selectedResourceType]"
                class="dp-task-detail__resource-error"
              >
                {{ resourceErrors[selectedResourceType] }}
              </div>

              <el-table
                ref="taskResourceTableRef"
                v-table-column-resize="'protection.flowBackupSource.resources'"
                v-table-overflow-title
                v-loading="resourceLoading"
                :data="selectedResourceRows"
                :max-height="taskResourceTableMaxHeight"
                class="hfl-list-table hfl-list-table--compact dp-task-detail__resource-table"
              >
                <el-table-column
                  :label="t('protection.backupsPage.flowSourceDetailResourceId')"
                  width="120"
                >
                  <template #default="{ row }">
                    <span class="hfl-table-cell-mono">{{ row.id }}</span>
                  </template>
                </el-table-column>
                <el-table-column
                  v-if="selectedResourceType === 'backup_source'"
                  :label="t('protection.backupsPage.colBackupSource')"
                  min-width="180"
                >
                  <template #default="{ row }">
                    <FlowSourceSummaryCell
                      v-if="row.flowSource"
                      :row="row.flowSource"
                      :interactive="false"
                    />
                    <span
                      v-else
                      class="hfl-empty-mark"
                    >—</span>
                  </template>
                </el-table-column>
                <el-table-column
                  v-if="selectedResourceType === 'backup_source'"
                  :label="t('protection.backupsPage.colConnectionAddress')"
                  min-width="200"
                >
                  <template #default="{ row }">
                    <FlowSourceConnectionCell
                      v-if="row.flowSource"
                      :row="row.flowSource"
                    />
                    <span
                      v-else
                      class="hfl-empty-mark"
                    >—</span>
                  </template>
                </el-table-column>
                <el-table-column
                  v-if="selectedResourceType !== 'backup_source'"
                  :label="t('protection.backupsPage.flowSourceDetailResourceName')"
                  min-width="180"
                >
                  <template #default="{ row }">
                    <div class="dp-task-detail__resource-name">
                      {{ row.name }}
                    </div>
                    <div class="dp-task-detail__resource-summary">
                      {{ row.summary }}
                    </div>
                  </template>
                </el-table-column>
                <el-table-column
                  v-if="selectedResourceType !== 'backup_source'"
                  :label="t('protection.backupsPage.flowSourceDetailResourceType')"
                  width="130"
                >
                  <template #default="{ row }">
                    <ElTag
                      type="info"
                      size="small"
                      effect="plain"
                    >
                      {{ row.type }}
                    </ElTag>
                  </template>
                </el-table-column>
                <el-table-column
                  :label="selectedResourceType === 'backup_source'
                    ? t('protection.sourceResources.colConnectivity')
                    : t('ops.task.colStatus')"
                  width="120"
                >
                  <template #default="{ row }">
                    <ElTag
                      v-if="selectedResourceType === 'backup_source' && row.availability"
                      :type="flowSourceStatusTagType(row.availability)"
                      :class="{ 'hfl-tag--neutral': flowSourceStatusTag(row.availability) === 'neutral' }"
                      size="small"
                    >
                      {{ flowSourceStatusLabel(row.availability) }}
                    </ElTag>
                    <ElTag
                      v-else-if="selectedResourceType !== 'backup_source' && row.status"
                      v-bind="lifecycleStatusTagAttrs(row.statusValue)"
                      size="small"
                    >
                      {{ row.status }}
                    </ElTag>
                    <span
                      v-else
                      class="hfl-empty-mark"
                    >—</span>
                  </template>
                </el-table-column>
                <el-table-column
                  :label="selectedResourceType === 'backup_source' ? t('protection.sourceResources.colRegisteredAt') : t('ops.task.updatedAt')"
                  width="180"
                >
                  <template #default="{ row }">
                    <span
                      class="hfl-table-cell-time"
                      :class="{ 'hfl-empty-mark': !(selectedResourceType === 'backup_source' ? row.registeredAt : row.updatedAt) }"
                    >{{ formatNullableTime(selectedResourceType === 'backup_source' ? row.registeredAt : row.updatedAt) }}</span>
                  </template>
                </el-table-column>
              </el-table>
            </div>
            <el-empty
              v-else
              :description="t('protection.backupsPage.flowSourceDetailEmptyResources')"
              :image-size="52"
            />
          </section>
        </ElTabPane>
      </ElTabs>
    </div>
  </ElDrawer>
  <ProtectionStopConfirmDialog
    v-if="stopConfirmOpen"
    v-model="stopConfirmOpen"
    :kind="stopConfirmKind"
    :items="stopConfirmItems"
    :loading="backupTaskActionBusy"
    @confirm="stopConfirmDialog.settleConfirm()"
    @cancel="stopConfirmDialog.settleCancel()"
  />
</template>

<style scoped>
.dp-flow-source-detail-drawer__header {
  width: 100%;
  padding-right: 8px;
}

.dp-flow-source-detail-section-title {
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 600;
  color: rgb(30 41 59);
}

.snapshot-points-table {
  container-type: inline-size;
}

.snapshot-directory-expand-panel {
  position: sticky;
  left: 35px;
  box-sizing: border-box;
  width: calc(100cqw - 49px);
  min-width: 0;
  max-width: calc(100cqw - 49px);
  overflow-x: auto;
  margin-left: 35px;
  padding: 8px 0 10px 14px;
  border-left: 2px solid rgb(226 232 240);
  contain: inline-size;
}

.snapshot-directory-table {
  width: 100%;
  min-width: 0;
}

.snapshot-efficiency-summary {
  margin: 0 0 12px;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-light);
}

.snapshot-efficiency-summary__metrics {
  display: grid;
  grid-template-columns: repeat(6, minmax(112px, 1fr));
  gap: 8px;
  margin: 0;
}

.snapshot-efficiency-summary__metric {
  min-width: 0;
  padding: 9px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-bg-color);
}

.snapshot-efficiency-summary__metric dt {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--el-text-color-secondary);
  font-size: 11px;
  line-height: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.snapshot-efficiency-summary__metric-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.snapshot-efficiency-summary__metric-info {
  flex: 0 0 auto;
  color: var(--el-text-color-secondary);
  cursor: help;
}

.snapshot-efficiency-summary__metric dd {
  margin: 3px 0 0;
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  font-weight: 650;
  line-height: 20px;
}

@media (max-width: 1180px) {
  .snapshot-efficiency-summary__metrics {
    grid-template-columns: repeat(3, minmax(112px, 1fr));
  }
}

@media (max-width: 760px) {
  .snapshot-efficiency-summary__metrics {
    grid-template-columns: repeat(2, minmax(112px, 1fr));
  }
}

.hfl-list-table .snapshot-point-time {
  display: block;
  width: 100%;
  overflow: visible;
  color: rgb(71 85 105);
  font-size: 12px;
  line-height: 16px;
  text-overflow: clip;
  white-space: normal;
  word-break: keep-all;
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
  margin: 0;
  padding: 3px 8px;
  border: 1px solid;
  border-radius: 6px;
  background: #fff;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  line-height: 16px;
  letter-spacing: normal;
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

.snapshot-point-actions__button--restore {
  border-color: oklch(87% 0.065 274.039);
  color: oklch(51.1% 0.262 276.966);
}

.snapshot-point-actions__button--restore .snapshot-point-actions__icon {
  color: oklch(58.5% 0.233 277.117);
}

.snapshot-point-actions__button--restore:not(:disabled):hover {
  border-color: oklch(78.5% 0.115 274.713);
  background: oklch(96.2% 0.018 272.314);
}

.snapshot-point-actions__button--browse {
  border-color: oklch(92.9% 0.013 255.508);
  color: oklch(37.2% 0.044 257.287);
}

.snapshot-point-actions__button--browse .snapshot-point-actions__icon {
  color: oklch(55.4% 0.046 257.417);
}

.snapshot-point-actions__button--browse:not(:disabled):hover {
  border-color: oklch(86.9% 0.022 252.894);
  background: oklch(98.4% 0.003 247.858);
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

.snapshot-directory-path-cell {
  display: flex;
  width: 100%;
  min-width: 0;
  flex-direction: column;
  align-items: stretch;
  text-align: left;
}

.snapshot-directory-path-cell__parent {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 7px;
}

.snapshot-directory-path-cell__icon {
  flex: 0 0 auto;
}

.snapshot-directory-path-cell__icon--dir {
  color: #d97706;
}

.snapshot-directory-path-cell__icon--file {
  color: #2563eb;
}

.snapshot-directory-path-cell__path {
  width: 0;
  min-width: 0;
  max-width: 100%;
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.snapshot-directory-path-cell--disabled {
  color: rgb(71 85 105);
}

@media (max-width: 760px) {
  .snapshot-directory-expand-panel {
    left: 21px;
    width: calc(100cqw - 27px);
    max-width: calc(100cqw - 27px);
    margin-left: 21px;
    padding-left: 10px;
  }
}

.dp-flow-source-overview {
  gap: 14px;
}

.dp-flow-source-detail-drawer .hfl-detail-section__title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dp-flow-source-detail-drawer .hfl-detail-section__title::before {
  content: '';
  flex: 0 0 auto;
  width: 4px;
  height: 16px;
  border-radius: 999px;
  background: linear-gradient(
    180deg,
    var(--color-primary) 0%,
    color-mix(in srgb, var(--color-primary) 82%, #000) 100%
  );
}

.dp-flow-source-detail-drawer .dp-flow-config-subsection__title::before {
  content: '';
  flex: 0 0 auto;
  width: 3px;
  height: 12px;
  border-radius: 999px;
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--color-primary) 86%, #fff) 0%,
    color-mix(in srgb, var(--color-primary) 76%, #000) 100%
  );
}

.dp-flow-type-pill {
  display: inline-flex;
  max-width: 100%;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border: 1px solid transparent;
  border-radius: 6px;
  line-height: 1;
  white-space: nowrap;
}

.dp-flow-type-pill__main {
  font-size: 12px;
  font-weight: 700;
}

.dp-flow-type-pill__suffix {
  font-size: 12px;
  font-weight: 500;
  opacity: 0.9;
}

.dp-flow-type-pill--host {
  background: color-mix(in srgb, var(--color-primary) 7%, #fff);
  border-color: color-mix(in srgb, var(--color-primary) 24%, #fff);
  color: color-mix(in srgb, var(--color-primary) 82%, #000);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
}

.dp-flow-type-pill--nas-nfs {
  background: rgba(255, 247, 237, 0.96);
  border-color: rgba(253, 186, 116, 0.8);
  color: rgb(194 65 12);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
}

.dp-flow-type-pill--nas-smb {
  background: rgba(236, 253, 245, 0.96);
  border-color: rgba(110, 231, 183, 0.82);
  color: rgb(5 150 105);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
}

.dp-flow-source-info-value {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.dp-flow-source-info-name {
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}

.dp-flow-config-detail-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border-radius: 0 0 10px 10px;
  background: rgb(248 250 252);
  box-shadow: inset 0 1px 0 rgba(15, 23, 42, 0.03);
  scroll-margin-top: 12px;
}

.dp-flow-config-section {
  border: 0;
  border-radius: 0;
  background: transparent;
}

.dp-flow-config-detail-card {
  overflow: hidden;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: #fff;
}

.dp-flow-config-subsections {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dp-flow-source-detail-drawer .dp-flow-config-subsection__title {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 11px 13px;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
}

.dp-flow-config-compression {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 3px;
  padding: 12px 13px;
}

.dp-flow-config-compression__summary {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.dp-flow-config-compression__icon {
  flex: 0 0 auto;
  margin-top: 1px;
  color: rgb(100 116 139);
}

.dp-flow-config-compression__icon--balanced {
  color: var(--color-primary);
}

.dp-flow-config-compression__icon--high {
  color: rgb(180 83 9);
}

.dp-flow-config-compression__title {
  min-width: 0;
  color: rgb(30 41 59);
  font-size: 13px;
  font-weight: 650;
  line-height: 18px;
}

.dp-flow-config-compression__description {
  width: 100%;
  margin: 0;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
  white-space: normal;
}

.dp-flow-config-subsection__title--with-value {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dp-flow-config-subsection__title--with-value strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
  color: rgb(51 65 85);
}

.dp-flow-config-subsection__body {
  padding: 12px;
}

.dp-flow-config-subsection__empty {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 12px;
  color: rgb(180 83 9);
  font-size: 13px;
}

.dp-flow-config-subsection__empty--block {
  display: flex;
  width: 100%;
  border: 1px dashed rgb(253 186 116);
  border-radius: 8px;
  background: rgb(255 251 235);
}

.dp-flow-config-detail-card__grid {
  border-top: 0;
}

.dp-flow-policy-overview__name {
  min-width: 0;
}

.dp-flow-policy-overview__name-value {
  justify-content: space-between;
  gap: 12px;
}

.dp-flow-policy-overview__retention-box {
  width: 100%;
}

.dp-flow-policy-overview__retention-box .policy-retention-detail-list {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 8px;
}

.dp-flow-policy-overview__retention-box .policy-retention-detail-list__line {
  display: block;
  min-width: 0;
  overflow-wrap: anywhere;
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 400;
  line-height: 1.55;
}

.dp-flow-policy-overview__retention-box .policy-retention-detail-list__line--summary {
  display: block;
  padding-bottom: 0;
  border-bottom: 0;
  font-weight: 400;
}

.dp-flow-policy-overview__retention-box .policy-retention-detail-list__label {
  color: rgb(71 85 105);
  font-weight: 600;
  white-space: nowrap;
}

.dp-flow-policy-overview__retention-box .policy-retention-detail-list__text {
  min-width: 0;
  overflow-wrap: anywhere;
}

.dp-flow-policy-overview__advanced-box {
  width: 100%;
  box-sizing: border-box;
}

.dp-flow-config-summary__value {
  overflow-wrap: anywhere;
}

.dp-flow-detail-name-with-state {
  flex-wrap: wrap;
  gap: 8px;
}

.dp-flow-detail-rules-preview {
  width: 100%;
}

.dp-flow-config-paths {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.dp-flow-config-paths .flow-source-list-drawer-path {
  max-width: 100%;
}

.dp-flow-config-paths--stack {
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
}

.dp-flow-config-paths--stack .flow-source-list-drawer-path {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
}

.dp-flow-config-path__icon {
  flex: 0 0 auto;
  color: var(--color-primary);
}

.dp-flow-config-path span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dp-flow-restore-plan-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: rgb(248 250 252);
  padding: 10px;
}

.create-recovery-plan-cell {
  display: grid;
  gap: 8px;
  min-width: 0;
}

.create-recovery-plan-cell__status,
.create-recovery-plan-cell__policy,
.create-recovery-plan-mapping__endpoint {
  display: inline-flex;
  align-items: center;
  min-width: 0;
}

.create-recovery-plan-cell__status {
  gap: 7px;
  color: rgb(22 101 52);
  font-size: 12px;
  font-weight: 650;
}

.create-recovery-plan-cell__policy-line {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
}

.create-recovery-plan-cell__dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: rgb(34 197 94);
}

.create-recovery-plan-cell__policy {
  width: fit-content;
  max-width: 100%;
  gap: 6px;
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 12px;
  font-weight: 600;
}

.create-recovery-plan-cell__policy-description {
  min-width: 0;
  max-width: 100%;
  font-size: 12px;
  line-height: 1.45;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-recovery-plan-cell__policy-description--skip {
  color: var(--color-success);
}

.create-recovery-plan-cell__policy-description--overwrite {
  color: rgb(180 83 9);
}

.create-recovery-plan-cell__policy--skip {
  color: var(--color-success);
  background: var(--color-success-light);
  border: 1px solid var(--color-success-border);
}

.create-recovery-plan-cell__policy--overwrite {
  color: rgb(180 83 9);
  background: rgb(255 251 235);
  border: 1px solid rgb(253 230 138);
}

.create-recovery-plan-cell__policy-icon,
.create-recovery-plan-mapping__icon {
  flex: 0 0 auto;
}

.create-recovery-plan-cell__policy-text,
.create-recovery-plan-mapping__text {
  display: inline-block;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-recovery-plan-cell--review .create-recovery-plan-cell__policy {
  overflow: visible;
  white-space: normal;
}

.create-recovery-plan-cell--review .create-recovery-plan-cell__policy-text,
.create-recovery-plan-cell--review .create-recovery-plan-mapping__text {
  overflow: visible;
  text-overflow: initial;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: break-word;
}

.create-recovery-plan-cell--review .create-recovery-plan-mapping {
  align-items: start;
}

.create-recovery-plan-cell--review .create-recovery-plan-mapping__endpoint {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: start;
  gap: 5px;
}

.create-recovery-plan-cell--review .create-recovery-plan-mapping__text {
  display: block;
  max-width: none;
}

.create-recovery-plan-cell__mappings {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.create-recovery-plan-mapping {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  min-width: 0;
  border-radius: 6px;
  background: #fff;
  padding: 7px 8px;
  color: rgb(51 65 85);
  font-size: 12px;
  transition:
    background-color 0.16s ease,
    color 0.16s ease;
}

.create-recovery-plan-mapping:hover {
  background: color-mix(in srgb, var(--color-primary) 7%, #ffffff);
}

.create-recovery-plan-mapping--more {
  display: flex;
  justify-content: center;
  border: 1px dashed rgba(148, 163, 184, 0.45);
  background: rgba(248, 250, 252, 0.72);
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 650;
}

.create-recovery-plan-mapping__endpoint {
  gap: 5px;
  max-width: 100%;
}

.create-recovery-plan-mapping__icon {
  color: rgb(71 85 105);
}

.create-recovery-plan-mapping__endpoint--snapshot .create-recovery-plan-mapping__icon {
  color: var(--color-primary);
}

.create-recovery-plan-mapping__endpoint--dir .create-recovery-plan-mapping__icon {
  color: #d97706;
}

.create-recovery-plan-mapping__endpoint--file .create-recovery-plan-mapping__icon {
  color: #2563eb;
}

.create-recovery-plan-mapping__endpoint--target .create-recovery-plan-mapping__icon {
  color: #d97706;
}

.create-recovery-plan-mapping__arrow {
  color: rgb(148 163 184);
  font-size: 11px;
  font-weight: 700;
}

.restore-records-table {
  container-type: inline-size;
}

.restore-record-expand-panel {
  position: sticky;
  left: 35px;
  box-sizing: border-box;
  width: calc(100cqw - 49px);
  min-width: 0;
  max-width: calc(100cqw - 49px);
  overflow-x: hidden;
  margin-left: 35px;
  padding: 12px 16px 14px;
  background: rgb(248 250 252);
  contain: inline-size;
}

.restore-record-time-summary {
  display: grid;
  grid-template-columns: minmax(176px, auto) minmax(140px, 1fr) minmax(176px, auto);
  align-items: center;
  gap: 14px;
  min-width: 0;
  margin: 0 2px 12px;
  padding: 2px 2px 5px;
}

.restore-record-time-summary__point {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

.restore-record-time-summary__point--end {
  justify-content: flex-end;
}

.restore-record-time-summary__marker {
  display: inline-flex;
  flex: 0 0 28px;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid color-mix(in srgb, var(--color-info) 22%, transparent);
  border-radius: 50%;
  background: color-mix(in srgb, var(--color-info) 9%, #fff);
  color: var(--color-info);
}

.restore-record-time-summary__point--end .restore-record-time-summary__marker {
  border-color: color-mix(in srgb, var(--color-success) 24%, transparent);
  background: color-mix(in srgb, var(--color-success) 10%, #fff);
  color: var(--color-success);
}

.restore-record-time-summary__point--pending .restore-record-time-summary__marker {
  border-color: var(--el-border-color);
  background: #fff;
  color: var(--el-text-color-secondary);
}

.restore-record-time-summary__copy {
  min-width: 0;
}

.restore-record-time-summary__label,
.restore-record-time-summary__value {
  display: block;
}

.restore-record-time-summary__label {
  color: var(--el-text-color-secondary);
  font-size: 11px;
  font-weight: 650;
  line-height: 1.2;
}

.restore-record-time-summary__value {
  margin-top: 3px;
  overflow: hidden;
  color: rgb(51 65 85);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  font-weight: 650;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.restore-record-time-summary__duration {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
}

.restore-record-time-summary__duration::before {
  position: absolute;
  right: 0;
  left: 0;
  height: 1px;
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--color-info) 30%, var(--el-border-color)) 0%,
    color-mix(in srgb, var(--color-primary) 28%, var(--el-border-color)) 48%,
    color-mix(in srgb, var(--color-success) 30%, var(--el-border-color)) 100%
  );
  content: '';
}

.restore-record-time-summary__duration-pill {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: baseline;
  gap: 7px;
  max-width: 100%;
  padding: 4px 10px;
  border: 1px solid color-mix(in srgb, var(--color-primary) 18%, var(--el-border-color-lighter));
  border-radius: 999px;
  background: rgb(248 250 252);
  box-shadow: 0 0 0 4px rgb(248 250 252);
  white-space: nowrap;
}

.restore-record-time-summary__duration-label {
  color: var(--el-text-color-secondary);
  font-size: 10px;
  font-weight: 650;
}

.restore-record-time-summary__duration-value {
  overflow: hidden;
  color: var(--color-primary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  font-weight: 750;
  text-overflow: ellipsis;
}

.restore-record-runtime-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  margin: 0 2px 12px;
  padding: 9px 11px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: #fff;
}

.restore-record-runtime-summary__label {
  flex: 0 0 auto;
  color: var(--el-text-color-secondary);
  font-size: 11px;
  font-weight: 700;
}

.restore-record-runtime-summary__metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  min-width: 0;
}

.restore-record-runtime-summary__metric {
  padding: 3px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--color-primary) 8%, #fff);
  color: var(--el-text-color-regular);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.restore-record-runtime-summary__unavailable {
  min-width: 0;
  overflow: hidden;
  color: var(--el-text-color-placeholder);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 760px) {
  .restore-record-expand-panel {
    left: 21px;
    width: calc(100cqw - 27px);
    max-width: calc(100cqw - 27px);
    margin-left: 21px;
    padding-right: 10px;
    padding-left: 10px;
  }

  .restore-record-time-summary {
    grid-template-columns: minmax(0, 1fr);
    gap: 9px;
    padding-left: 3px;
  }

  .restore-record-time-summary__point--end {
    justify-content: flex-start;
  }

  .restore-record-time-summary__duration {
    justify-content: flex-start;
    margin-left: 13px;
    padding-left: 24px;
  }

  .restore-record-time-summary__duration::before {
    top: -10px;
    bottom: -10px;
    left: 0;
    width: 1px;
    height: auto;
    background: linear-gradient(
      180deg,
      color-mix(in srgb, var(--color-info) 30%, var(--el-border-color)) 0%,
      color-mix(in srgb, var(--color-success) 30%, var(--el-border-color)) 100%
    );
  }

  .restore-record-time-summary__duration-pill {
    box-shadow: none;
  }

  .restore-record-runtime-summary {
    align-items: flex-start;
    flex-direction: column;
  }
}

.restore-record-structure-card {
  min-width: 0;
}

.restore-record-mapping--with-result {
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) minmax(112px, auto);
}

.restore-record-expand-panel .create-recovery-plan-mapping {
  align-items: start;
}

.restore-record-expand-panel .create-recovery-plan-mapping__endpoint {
  align-items: flex-start;
}

.restore-record-expand-panel .create-recovery-plan-mapping__icon,
.restore-record-expand-panel .create-recovery-plan-mapping__arrow {
  margin-top: 2px;
}

.restore-record-expand-panel .create-recovery-plan-mapping__text {
  display: block;
  width: 100%;
  overflow: visible;
  line-height: 1.45;
  overflow-wrap: anywhere;
  text-overflow: clip;
  white-space: normal;
  word-break: break-word;
}

.restore-record-mapping__result {
  display: inline-flex;
  align-items: flex-start;
  justify-content: flex-end;
  min-width: 0;
}

.restore-record-snapshot-tree,
.restore-record-snapshot-tree__children,
.restore-record-flat-mappings,
.restore-record-structure-entry {
  display: grid;
  min-width: 0;
}

.restore-record-snapshot-tree,
.restore-record-flat-mappings {
  gap: 6px;
}

.restore-record-snapshot-tree__parent {
  border: 1px solid color-mix(in srgb, var(--color-primary) 24%, var(--el-border-color-lighter));
  background: color-mix(in srgb, var(--color-primary) 5%, #fff);
}

.restore-record-snapshot-tree__children {
  position: relative;
  gap: 6px;
  margin-left: 10px;
  padding-left: 22px;
}

.restore-record-snapshot-tree__children::before {
  position: absolute;
  top: -6px;
  bottom: 20px;
  left: 7px;
  border-left: 1px solid var(--el-border-color);
  content: '';
}

.restore-record-snapshot-tree__child {
  position: relative;
}

.restore-record-snapshot-tree__child::before {
  position: absolute;
  top: 20px;
  left: -15px;
  width: 15px;
  border-top: 1px solid var(--el-border-color);
  content: '';
}

.restore-record-structure-entry {
  gap: 4px;
}

.restore-record-structure-entry__error {
  margin: 0 8px;
  padding: 7px 8px;
  border: 1px solid rgb(254 202 202);
  border-radius: 6px;
  color: rgb(185 28 28);
  background: rgb(254 242 242);
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.restore-record-status-progress {
  display: grid;
  gap: 7px;
  min-width: 0;
}

.restore-record-status-progress__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.restore-record-status-progress__bar :deep(.el-progress) {
  flex: 1;
  min-width: 70px;
}

.restore-record-status-progress__bar span {
  flex: 0 0 auto;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

:global(.create-recovery-plan-tooltip__mapping .create-recovery-plan-mapping__text) {
  overflow: visible;
  text-overflow: initial;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
}

@media (max-width: 760px) {
  .dp-flow-source-info-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.dp-flow-source-detail-tabs :deep(.el-tabs__content) {
  overflow: hidden;
}

.dp-flow-source-detail-drawer__body .hfl-list-footer {
  padding: 10px 0 0;
}

.dp-source-task-toolbar {
  margin-bottom: 10px;
}

.dp-source-task-toolbar__search {
  width: 260px;
}

.dp-source-task-toolbar__range {
  width: 100%;
  max-width: 100%;
}

.dp-source-record-toolbar__restore-search {
  width: 390px;
}

.dp-source-record-toolbar__range {
  width: 360px;
}

@media (max-width: 760px) {
  .dp-source-task-toolbar__search,
  .dp-source-record-toolbar__restore-search,
  .dp-source-record-toolbar__range {
    width: 100%;
  }
}

.dp-source-task-filter-badge {
  margin-right: 4px;
}

.dp-source-task-filter-button--active {
  color: var(--color-info);
  border-color: var(--color-info-border);
  background: var(--color-info-light);
}

:global(.dp-source-task-select-popper.el-popper) {
  z-index: 3600 !important;
}

.dp-source-task-filter-drawer :deep(.el-drawer__body) {
  padding: 18px 20px;
}

.dp-source-task-filter-drawer__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.dp-source-task-table :deep(.dp-source-task-table__row--active > td.el-table__cell) {
  background: var(--color-info-light) !important;
}

.dp-source-task-table :deep(.dp-source-task-table__row--active:hover > td.el-table__cell) {
  background: rgb(219 234 254) !important;
}

/* Keep protection task drawer pills visually synced with ops task list/detail pills. */
.dp-source-task-type-pill,
.dp-source-task-trigger-pill {
  display: inline-grid;
  place-items: center;
  box-sizing: border-box;
  vertical-align: middle;
  max-width: 100%;
  height: 24px;
  padding: 0 8px;
  border: 1px solid rgb(226 232 240);
  border-radius: 4px;
  background: rgb(248 250 252);
  color: rgb(71 85 105);
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  white-space: nowrap;
}

.dp-source-task-pill-text {
  display: block;
  line-height: 12px;
  transform: translateY(1px);
}

.dp-source-task-type-pill--backup {
  border-color: var(--color-info-border);
  background: var(--color-info-light);
  color: var(--color-info);
}

.dp-source-task-type-pill--restore {
  border-color: rgb(196 181 253);
  background: rgb(245 243 255);
  color: rgb(109 40 217);
}

.dp-source-task-trigger-pill {
  border-color: rgb(226 232 240);
  background: rgb(248 250 252);
  color: rgb(71 85 105);
}

.dp-source-task-trigger-pill--manual {
  border-color: var(--color-info-border);
  background: var(--color-info-light);
  color: var(--color-info);
}

.dp-source-task-trigger-pill--system {
  border-color: rgb(216 180 254);
  background: rgb(250 245 255);
  color: rgb(147 51 234);
}

.dp-source-task-trigger-pill--retry {
  border-color: rgb(254 215 170);
  background: rgb(255 247 237);
  color: rgb(234 88 12);
}

.dp-source-task-trigger-pill--api {
  border-color: rgb(153 246 228);
  background: rgb(240 253 250);
  color: rgb(13 148 136);
}

.dp-source-task-trigger-pill--hook {
  border-color: rgb(251 207 232);
  background: rgb(253 242 248);
  color: rgb(219 39 119);
}

.dp-source-task-progress {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
}

.dp-source-task-progress__track {
  height: 9px;
  overflow: hidden;
  border-radius: 999px;
  background-color: rgb(226 232 240);
}

.dp-source-task-progress__fill {
  height: 100%;
  min-width: 4px;
  border-radius: inherit;
  background-color: var(--color-info);
  transition: width 0.35s ease;
}

.dp-source-task-progress__fill--success {
  background-color: var(--color-success);
}

.dp-source-task-progress__fill--failed,
.dp-source-task-progress__fill--timeout {
  background-color: var(--color-error);
}

.dp-source-task-progress__fill--pending,
.dp-source-task-progress__fill--cancelled {
  background-color: rgb(100 116 139);
}

.dp-source-task-progress__text {
  color: var(--color-info);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 11px;
  font-weight: 800;
}

.dp-task-detail__header-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
  min-width: 0;
}

.dp-task-detail__header-summary {
  min-width: 0;
}

.dp-task-detail__header-title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.dp-task-detail__header-title {
  margin: 0;
  color: rgb(15 23 42);
  font-size: 18px;
  font-weight: 800;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

.dp-task-detail__header-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  min-width: 0;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 20px;
}

.dp-task-detail__header-divider {
  align-self: center;
  width: 1px;
  height: 12px;
  background: rgb(203 213 225);
}

.dp-task-detail__uuid {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  color: rgb(71 85 105);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 11px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.dp-task-detail__uuid-text {
  line-height: 21px;
}

.dp-task-detail__copy-button {
  align-self: center;
  width: 20px;
  height: 20px;
  padding: 0;
  color: rgb(100 116 139);
}

.dp-task-detail__header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.dp-task-detail__cancel-button {
  height: 32px;
  padding: 0 11px;
  border-color: var(--color-error-border);
  border-radius: 7px;
  background: var(--color-error-light);
  color: var(--color-error);
  font-size: 12px;
  font-weight: 700;
  box-shadow: none;
}

.dp-task-detail__cancel-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.dp-task-detail__cancel-button:hover:not(:disabled) {
  border-color: var(--color-error);
  background: var(--color-error);
  color: #fff;
}

.dp-task-detail__cancel-button:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--color-error) 42%, transparent);
  outline-offset: 2px;
}

.dp-task-detail__cancel-button:disabled {
  border-color: var(--color-error-border);
  background: var(--color-error-light);
  color: var(--color-error);
  opacity: 0.55;
}

.dp-task-detail__body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 0;
  background: var(--el-bg-color);
}

.dp-task-detail__hero {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
  border: 1px solid rgb(226 232 240);
  border-radius: 12px;
  background: rgb(248 250 252);
  box-shadow: inset 0 1px 1px rgba(15, 23, 42, 0.03);
}

.dp-task-detail__hero-section-title {
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.dp-task-detail__hero-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}

@media (min-width: 720px) {
  .dp-task-detail__hero-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.dp-task-detail__metric {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  min-width: 0;
  padding: 14px;
  border: 1px solid rgb(241 245 249);
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
}

.dp-task-detail__metric--wide {
  grid-column: 1 / -1;
}

.dp-task-detail__metric-icon {
  flex: 0 0 18px;
  margin-top: 2px;
  color: rgb(100 116 139);
}

.dp-task-detail__metric-icon--blue {
  color: rgb(14 165 233);
}

.dp-task-detail__metric-icon--indigo {
  color: rgb(79 70 229);
}

.dp-task-detail__metric-copy {
  min-width: 0;
}

.dp-task-detail__metric-label {
  display: block;
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 650;
}

.dp-task-detail__metric-value {
  display: block;
  margin-top: 4px;
  color: rgb(15 23 42);
  font-size: 14px;
  font-weight: 800;
}

.dp-task-detail__metric-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

.dp-task-detail__time-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  width: 100%;
  min-width: 0;
}

@media (min-width: 720px) {
  .dp-task-detail__time-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

.dp-task-detail__time-value {
  display: block;
  margin-top: 4px;
  min-width: 0;
  color: rgb(51 65 85);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  font-weight: 650;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dp-task-detail__time-value--strong {
  color: var(--color-info);
}

.dp-task-detail__progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 750;
}

.dp-task-detail__progress-head span:last-child {
  color: var(--color-info);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
}

.dp-task-detail__progress-track {
  height: 6px;
  overflow: hidden;
  border-radius: 999px;
  background-color: rgb(226 232 240);
}

.dp-task-detail__progress-fill {
  height: 100%;
  min-width: 4px;
  border-radius: inherit;
  background-color: var(--color-info);
  transition: width 0.35s ease;
}

.dp-task-detail__progress-fill--success {
  background-color: var(--color-success);
}

.dp-task-detail__progress-fill--failed,
.dp-task-detail__progress-fill--timeout {
  background-color: var(--color-error);
}

.dp-task-detail__progress-fill--pending,
.dp-task-detail__progress-fill--cancelled {
  background-color: rgb(100 116 139);
}

.dp-task-detail__tabs {
  min-width: 0;
  margin-top: 4px;
}

.dp-task-detail__tab-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.dp-task-detail__steps-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 750;
}

.dp-task-detail__step-list {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.dp-task-detail__step-item {
  position: relative;
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 12px;
}

.dp-task-detail__step-item::before {
  content: '';
  position: absolute;
  left: 12px;
  top: 34px;
  bottom: -18px;
  width: 0;
  border-left: 2px dashed rgb(226 232 240);
}

.dp-task-detail__step-item--last::before {
  display: none;
}

.dp-task-detail__step-anchor {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  margin-top: 1px;
  border: 2px solid #fff;
  border-radius: 999px;
}

.dp-task-detail__timeline-icon--success {
  border-color: var(--color-success);
  background-color: var(--color-success);
  color: #fff;
}

.dp-task-detail__timeline-icon--danger {
  border-color: var(--color-error);
  background-color: var(--color-error);
  color: #fff;
}

.dp-task-detail__timeline-icon--running {
  border-color: var(--color-info);
  background-color: var(--color-info);
  color: #fff;
}

.dp-task-detail__timeline-icon--running > svg {
  animation: dp-task-step-spin 0.8s linear infinite;
  transform-origin: center center;
  will-change: transform;
}

@keyframes dp-task-step-spin {
  to {
    transform: rotate(360deg);
  }
}

.dp-task-detail__timeline-icon--muted {
  border-color: rgb(100 116 139);
  background-color: rgb(100 116 139);
  color: #fff;
}

.dp-task-detail__timeline-icon--pending {
  background-color: rgb(100 116 139);
  color: #fff;
}

.dp-task-detail__step-card {
  min-width: 0;
  padding: 14px 16px;
  border: 1px solid rgb(226 232 240);
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
}

.dp-task-detail__step-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.dp-task-detail__step-card-head--static {
  cursor: default;
}

.dp-task-detail__step-title {
  flex: 1;
  min-width: 0;
  color: rgb(15 23 42);
  font-size: 14px;
  font-weight: 800;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.dp-task-detail__step-executed-at {
  display: inline-flex;
  margin-left: 8px;
  color: rgb(71 85 105);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 11px;
  font-weight: 700;
}

.dp-task-detail__step-duration {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  padding: 2px 7px;
  border: 1px solid rgb(226 232 240);
  border-radius: 6px;
  background: rgb(248 250 252);
  color: rgb(100 116 139);
  font-size: 10px;
  font-weight: 800;
}

.dp-task-detail__step-duration {
  border-color: rgb(241 245 249);
  color: rgb(100 116 139);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 11px;
}

.dp-task-detail__event-list,
.dp-task-detail__event-only {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid rgb(241 245 249);
}

.dp-task-detail__event-only {
  margin-top: 0;
  padding: 14px;
  border: 1px solid rgb(226 232 240);
  border-radius: 12px;
}

.dp-task-detail__event-row {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr) auto;
  gap: 8px;
  align-items: start;
  min-width: 0;
}

.dp-task-detail__event-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  margin-top: 3px;
  border-radius: 999px;
  background-color: var(--color-success);
  color: #fff;
}

.dp-task-detail__event-dot--danger {
  background-color: var(--color-error);
}

.dp-task-detail__event-dot--warning {
  background-color: var(--color-warning);
}

.dp-task-detail__event-dot--running {
  background-color: var(--color-info);
}

.dp-task-detail__event-dot--muted {
  background-color: rgb(100 116 139);
}

.dp-task-detail__event-msg {
  min-width: 0;
  color: rgb(51 65 85);
  font-size: 12px;
  font-weight: 400;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.dp-task-detail__event-content {
  display: flex;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}

.dp-task-detail__event-object {
  display: inline-flex;
  max-width: 100%;
  align-items: flex-start;
  gap: 4px;
  border: 1px solid rgb(226 232 240);
  border-radius: 6px;
  background: rgb(248 250 252);
  padding: 2px 6px;
  color: rgb(71 85 105);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 11px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.dp-task-detail__event-object svg {
  margin-top: 2px;
  flex: 0 0 auto;
  color: rgb(100 116 139);
}

.dp-task-detail__event-error {
  display: block;
  max-width: 100%;
  color: rgb(185 28 28);
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.dp-task-detail__event-msg--danger {
  color: rgb(185 28 28);
}

.dp-task-detail__event-msg--warning {
  color: rgb(180 83 9);
}

.dp-task-detail__event-msg--muted {
  color: rgb(100 116 139);
}

.dp-task-detail__event-time {
  max-width: 180px;
  overflow: hidden;
  color: rgb(100 116 139);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 11px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dp-task-detail__empty-line {
  padding: 6px 0;
  color: rgb(100 116 139);
  font-size: 13px;
}

.dp-task-detail__resource-view {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.dp-task-detail__resource-switcher {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.dp-task-detail__resource-switch {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 11px;
  border: 1px solid rgb(226 232 240);
  border-radius: 8px;
  background: #fff;
  color: rgb(71 85 105);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: border-color 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}

.dp-task-detail__resource-switch:hover {
  border-color: var(--color-info-border);
  background: var(--color-info-light);
  color: var(--color-info);
}

.dp-task-detail__resource-switch--active {
  border-color: var(--color-info);
  background: var(--color-info-light);
  color: var(--color-info);
}

.dp-task-detail__resource-error {
  padding: 10px 12px;
  border: 1px solid rgb(254 202 202);
  border-radius: 8px;
  background: rgb(254 242 242);
  color: rgb(185 28 28);
  font-size: 12px;
  font-weight: 600;
}

.dp-task-detail__resource-table {
  border-radius: 8px;
  overflow: hidden;
}

.dp-task-detail__resource-name {
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 700;
}

.dp-task-detail__resource-summary {
  margin-top: 2px;
  color: rgb(100 116 139);
  font-size: 11px;
}

.dp-snapshot-file-browser-shell {
  position: fixed;
  inset: 0;
  z-index: 3600;
  pointer-events: auto;
}

.dp-snapshot-file-browser-panel {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(720px, 92vw);
  display: flex;
  flex-direction: column;
  background: #fff;
  box-shadow: -16px 0 36px rgb(15 23 42 / 18%);
  pointer-events: auto;
}

.dp-snapshot-file-browser-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 56px;
  padding: 14px 18px;
  border-bottom: 1px solid rgb(226 232 240);
}

.dp-snapshot-file-browser-panel__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 6px;
  color: rgb(71 85 105);
  background: transparent;
  cursor: pointer;
}

.dp-snapshot-file-browser-panel__close:hover {
  background: rgb(241 245 249);
  color: rgb(15 23 42);
}

.dp-snapshot-file-browser-panel__body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 16px;
}

.dp-snapshot-file-browser {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dp-snapshot-file-browser__toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid rgb(226 232 240);
  border-radius: 8px;
  padding: 10px 12px;
  background: #fff;
}

.dp-snapshot-file-browser__toolbar-main {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  min-width: 0;
}

.dp-snapshot-file-browser__toolbar-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.dp-snapshot-file-browser__selected {
  font-size: 12px;
  color: rgb(100 116 139);
  white-space: nowrap;
}

.dp-snapshot-file-browser__crumbs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-top: 4px;
  font-size: 12px;
}

.dp-snapshot-file-browser__entry {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.dp-snapshot-file-browser__file-row {
  display: grid;
  grid-template-columns: 28px minmax(160px, 1.4fr) minmax(120px, 1fr) 88px 136px;
  align-items: center;
  gap: 10px;
  min-width: 0;
  min-height: 36px;
  padding: 5px 10px;
  border: 1px solid rgb(226 232 240);
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
  font-size: 13px;
}

.dp-snapshot-file-browser__file-row:hover,
.dp-snapshot-file-browser__file-row.is-selected {
  border-color: var(--color-info-border);
  background: var(--color-info-light);
}

.dp-snapshot-file-browser__tree {
  border: 0;
  border-radius: 0;
  padding: 2px 0;
  background: transparent;
}

.dp-snapshot-file-browser__tree :deep(.el-tree-node__content) {
  height: 30px;
}

.dp-snapshot-file-browser__tree-row {
  display: grid;
  grid-template-columns: minmax(160px, 1.4fr) minmax(120px, 1fr) 88px 136px;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-width: 0;
  padding-right: 10px;
  font-size: 13px;
}

.dp-snapshot-file-browser__tree-path,
.dp-snapshot-file-browser__tree-size,
.dp-snapshot-file-browser__tree-time {
  color: rgb(100 116 139);
  font-size: 12px;
}

.dp-snapshot-file-browser__tree-size,
.dp-snapshot-file-browser__tree-time {
  white-space: nowrap;
}

@media (max-width: 760px) {
  .dp-snapshot-file-browser__toolbar {
    flex-direction: column;
  }

  .dp-snapshot-file-browser__toolbar-main,
  .dp-snapshot-file-browser__toolbar-actions {
    width: 100%;
    flex-wrap: wrap;
  }

  .dp-snapshot-file-browser__tree-row {
    grid-template-columns: minmax(120px, 1fr) 72px;
  }

  .dp-snapshot-file-browser__file-row {
    grid-template-columns: 28px minmax(120px, 1fr) 72px;
  }

  .dp-snapshot-file-browser__tree-path,
  .dp-snapshot-file-browser__tree-time,
  .dp-snapshot-file-browser__file-row .dp-snapshot-file-browser__tree-path,
  .dp-snapshot-file-browser__file-row .dp-snapshot-file-browser__tree-time {
    display: none;
  }
}

.dp-task-detail__directories {
  margin-top: 16px;
  padding: 12px 14px;
  border: 1px solid rgb(226 232 240);
  border-radius: 10px;
  background: #fff;
}

.dp-task-detail__directory-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 10px;
}

.dp-task-detail__directory-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgb(248 250 252);
}

.dp-task-detail__directory-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.dp-task-detail__directory-path {
  font-size: 13px;
  font-weight: 600;
  color: rgb(15 23 42);
}

.dp-task-detail__directory-status {
  font-size: 12px;
  color: rgb(100 116 139);
}

.dp-task-detail__directory-error {
  flex: 1 1 100%;
  font-size: 12px;
  color: rgb(220 38 38);
}

/* Match the shared operations task-detail Steps treatment. */
.dp-task-detail__steps-head {
  font-weight: 700;
}

.dp-task-detail__step-list {
  position: relative;
}

.dp-task-detail__step-card {
  border-color: var(--color-border, #e2e8f0);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.dp-task-detail__step-card:hover {
  border-color: rgb(203 213 225);
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.06);
}
</style>
