<script setup lang="ts">
import { computed, defineAsyncComponent, h, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  Plus,
  FolderInput,
  ArrowLeft,
  ArrowRight,
  Database,
  RefreshCw,
  ChevronDown,
  Check,
  CircleOff,
  CirclePlus,
  ChevronsRight,
  CircleStop,
  CloudUpload,
  ArchiveRestore,
  Route,
  Trash2,
  Undo2,
  Unlink,
  ClipboardCheck,
  Archive,
  Camera,
  File,
  Folder,
  FolderTree,
  Filter,
  MoreHorizontal,
  FolderOpen,
  TextCursorInput,
  X,
  ShieldAlert,
  ShieldCheck,
  Scale,
  Info,
  Pencil,
  BrushCleaning,
} from 'lucide-vue-next'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { ElTable, ElTree } from 'element-plus'
import { copyTextToClipboard } from '../../lib/clipboard'
import ModulePage from '../../components/ModulePage.vue'
import HflHelpTip from '../../components/HflHelpTip.vue'
import type { DangerConfirmItem } from '../../components/DangerConfirmDialog.vue'
import WizardSteps from '../../components/WizardSteps.vue'
import AgentPlatformBrandIcon from '../../components/agent-deploy/AgentPlatformBrandIcon.vue'
import { useProtectionSideNav } from '../../composables/useProtectionSideNav'
import {
  backupFlowSourceStepIcon,
  sourceHostIcon,
  sourceNasIcon,
} from '../../lib/sourceTypeIcons'
import FlowSourceSummaryCell from './components/FlowSourceSummaryCell.vue'
import FlowSourceConnectionCell from './components/FlowSourceConnectionCell.vue'
import FlowSourceReadyStatusCell from './components/FlowSourceReadyStatusCell.vue'
import TaskProgressCell from './components/TaskProgressCell.vue'
import TaskTerminalOutcomeCell from './components/TaskTerminalOutcomeCell.vue'
import TaskStatusTag from '../../components/TaskStatusTag.vue'
import TaskDetailDrawer from './components/TaskDetailDrawer.vue'
import { isTransferProgress, type TransferProgress, formatTaskProgressPercent } from '../../lib/kopiaProgress'
import TargetRepositoryDetailCard from './components/TargetRepositoryDetailCard.vue'
import type { TargetRepositoryItem } from './components/TargetRepositoryPicker.vue'
import { useBackupWizardSourcePendingOps } from './composables/useBackupWizardSourcePendingOps'
import { useNodeLifecycleOps } from '../../composables/useNodeLifecycleOps'
import { flowSourceReadyStatus } from '../../lib/flowSourceDisplay'
import { backupSourceLifecycleDisplay } from '../../lib/backupSourceLifecycleDisplay'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import { usePageRequestScope } from '../../composables/usePageRequestScope'
import { useRestoreTargetCatalog } from '../../composables/useRestoreTargetCatalog'
import {
  useProtectionDemoStore,
  type DemoBackup,
  type DemoPathType,
  type DemoSnapshot,
  type DemoSnapshotDir,
} from '../../composables/useProtectionDemoStore'
import { issueEnrollmentInstall, listNodes, updateNode, type EnrollmentOs } from '../../lib/nodeApi'
import {
  createSourceResource,
  getBackupSourcePathInfo,
  listBackupSelectableSources,
  listBackupSourceDirectories,
  updateSourceResource,
  type BackupSelectableAvailability,
  type BackupSelectableTaskStatus,
  type BackupSelectableSearchField,
  type BackupSelectableSource,
  type BackupSourceDeleteResult,
  type BackupSourceDirectoryEntry,
} from '../../lib/sourceApi'
import { buildNasSourceCreatePayload } from '../../lib/nasSourceCreate'
import { defaultNasMountOptions } from '../../lib/nasMountOptions'
import { preflightSourceNasCreate, showNasDraftPreflightGuidance } from '../../lib/nasDraftPreflight'
import { resolveNasSubmitName } from '../../lib/nasSourceNaming'
import {
  selectBackupSourceDirectoryTreeEntries,
  shouldAutoExpandRefreshedDirectory,
} from '../../lib/backupSourceDirectoryTree'
import { restoreDirectoryBrowseSourceId } from '../../lib/restoreDirectoryTarget'
import { useBackupSourcePipeline } from '../../composables/useBackupSourcePipeline'
import { isBackupSelectableId } from '../../composables/useDemoFlowStep2Sources'
import { formatLocalDateTime } from '../../lib/dateTime'
import { lifecycleStatusTagAttrs } from '../../lib/statusTag'
import { nodeEnrollmentOs } from '../../lib/nodeInventoryDisplay'
import {
  getBackupConfig,
  getBackupSourceSnapshot,
  isInvalidCompressionLevelError,
  listBackupSourceSnapshots,
  listBackupConfigs,
  resetBackupConfigs,
  type BackupConfig,
  type BackupConfigDetail,
  type BackupConfigRecoveryPlan,
  type BackupSourceSnapshot,
  type BackupSourceSnapshotDirectory,
  type CompressionLevel,
} from '../../lib/protectionBackupConfigApi'
import {
  listBackupPolicies,
  listFileFilterRules,
  type BackupPolicy,
  type BackupPolicySchedule,
  type BackupPolicyScheduleMode,
  type FileFilterRule,
} from '../../lib/protectionPolicyApi'
import {
  backupPolicyToForm,
  compileFilterIgnorePatterns,
  fileFilterRuleToForm,
  type MessageLocale,
} from '../../lib/protectionPolicyFormModel'
import {
  getStorageRepository,
  listAllStorageRepositories,
  type StorageRepository,
} from '../../lib/storageRepositoryApi'
import { storageRepositoryLocation } from '../../lib/storageRepositoryDisplay'
import { startProtectionBackupTasks, cancelProtectionBackupTask } from '../../lib/protectionBackupTaskApi'
import { cancelProtectionRestoreTask } from '../../lib/protectionRestoreTaskApi'
import {
  type ProtectionStopConfirmItem,
} from '../../lib/protectionStopConfirm'
import { getTask, listTasks, type TaskRow } from '../../lib/taskApi'
import {
  sourceUnregisterPendingKind,
  sourceUnregisterTaskBindings,
  sourceUnregisterTaskOutcome,
} from '../../lib/sourceUnregisterMonitor'
import type { ErrorDetailsPayload } from '../../lib/errors/details'
import {
  notifyUnregisterCleanupWarning,
  notifyUnregisterCleanupWarningBatch,
  notifyUnregisterFailureBatch,
  openUnregisterFailureDetails,
  previousUnregisterFailureDetails,
  unregisterFailureSummaryLine,
  unregisterFailureToErrorDetails,
  warningsForUnregisterSource,
} from '../../lib/unregisterFailureDetails'
import {
  createRestoreRecord,
  browseRestoreSnapshotDirectory,
  getRestoreSnapshotPathInfo,
  listRestoreRecords,
  runRestorePlanBatch,
  type RestoreRecord,
  type RestoreRecordCreatePayload,
  type RestoreEndpointType,
} from '../../lib/restoreApi'
import { apiErrorMessage, apiErrorMessageI18n } from '../../lib/api'
import { toApiError } from '../../lib/errors/normalizer'
import { buildGeneratedNasMountDir, buildGeneratedNasName } from '../../lib/nasMountPath'
import type { ApiNode } from '../../types/node'
import Modal from '../../components/Modal.vue'
import BackupSourceDeleteDialog from '../../components/BackupSourceDeleteDialog.vue'
import BackupSourceResetDialogBody from '../../components/BackupSourceResetDialogBody.vue'
import BackupSourceStep3DeleteDialog from '../../components/BackupSourceStep3DeleteDialog.vue'
import DangerConfirmDialog from '../../components/DangerConfirmDialog.vue'
import ProtectionStopConfirmDialog from '../../components/ProtectionStopConfirmDialog.vue'
import { BACKUP_SOURCE_RESET_CONFIRMATION } from '../../lib/backupSourceResetDialog'
import {
  selectFinishedResetSourceIds,
  selectTerminalFailedResetSourceIds,
} from './resetPipelineRefresh'
import HostAddForm from './components/HostAddForm.vue'
import NasAddForm from './components/NasAddForm.vue'
import type {
  FlowSourceDetailTabInput,
  FlowSourceDetailTaskSubTab,
} from './components/FlowBackupSourceDetailDrawer.vue'
import {
  useFlowSourceAggregate,
  type DemoFlowTask,
  type FlowSourceRow,
} from './composables/useFlowSourceAggregate'
import {
  flowSourceCpuCores,
  flowSourceDiskCountText,
  flowSourceMemoryText,
} from '../../lib/flowSourceDisplay'
import { formatOfflineBackupPlanMessage } from './lib/offlineBackupPlanMessage'

const loadFlowBackupSourceDetailDrawer = () => import('./components/FlowBackupSourceDetailDrawer.vue')
const loadBackupCreateWizard = () => import('./BackupCreateWizard.vue')

let flowBackupSourceDetailDrawerPreload: ReturnType<typeof loadFlowBackupSourceDetailDrawer> | null = null
let backupCreateWizardPreload: ReturnType<typeof loadBackupCreateWizard> | null = null

function preloadFlowBackupSourceDetailDrawer() {
  flowBackupSourceDetailDrawerPreload ||= loadFlowBackupSourceDetailDrawer().catch((err) => {
    flowBackupSourceDetailDrawerPreload = null
    throw err
  })
  return flowBackupSourceDetailDrawerPreload
}

function preloadBackupCreateWizard() {
  backupCreateWizardPreload ||= loadBackupCreateWizard().catch((err) => {
    backupCreateWizardPreload = null
    throw err
  })
  return backupCreateWizardPreload
}

function scheduleInteractivePreload(preload: () => Promise<unknown>) {
  const run = () => {
    void preload().catch(() => undefined)
  }
  if (typeof window === 'undefined') return
  if ('requestIdleCallback' in window) {
    const idleWindow = window as Window & { requestIdleCallback: (cb: () => void) => number }
    idleWindow.requestIdleCallback(run)
    return
  }
  window.setTimeout(run, 250)
}

const FlowBackupSourceDetailDrawer = defineAsyncComponent({
  loader: preloadFlowBackupSourceDetailDrawer,
  suspensible: false,
})
const BackupCreateWizard = defineAsyncComponent({
  loader: preloadBackupCreateWizard,
  suspensible: false,
})

const router = useRouter()
const route = useRoute()
const props = withDefaults(defineProps<{
  standaloneRestore?: boolean
  fixedRestoreSnapshotId?: number
}>(), {
  standaloneRestore: false,
  fixedRestoreSnapshotId: 0,
})
const PROXY_DEPLOY_ROUTE = { path: '/node/nodes/deploy', query: { role: 'proxy' } } as const
const FLOW_RETURN_STEP_STORAGE_KEY = 'protection-flow-return-step'

const isFixedSnapshotRestore = computed(() => props.standaloneRestore)
const fixedRestoreSnapshot = ref<BackupSourceSnapshot | null>(null)
const fixedRestoreInitializing = ref(props.standaloneRestore)
const fixedRestoreInitError = ref('')
const fixedRestoreDirty = ref(false)
const fixedRestoreDirtyTracking = ref(false)
let fixedRestoreLeaveApproved = false

const { t, te, locale } = useI18n()
const sourcePendingOps = useBackupWizardSourcePendingOps({ t })

const lifecycleOps = useNodeLifecycleOps({
  role: 'agent',
  t,
  onRefresh: async () => {
    await Promise.all([
      loadBackupSelectable({ silent: true }),
      refreshPipelineStep2PlusIds(),
      refreshPipelineStep3Ids(),
      refreshBackupConfigs(),
    ])
    if (flowMainStep.value === 1) {
      await refreshFlowStepData(1, { showLoading: false })
    } else if (flowMainStep.value === 2) {
      await refreshFlowStepData(2, { showLoading: false })
    }
  },
  onLifecyclePatch: (patched) => {
    for (const node of patched) {
      if (node.is_deleted) {
        sourcePendingOps.clearByNodeId(node.id)
      }
    }
  },
})
const wizardActiveRemovalNodeIds = computed(() => new Set(
  [...lifecycleOps.running.value, ...lifecycleOps.queued.value]
    .filter((entry) => entry.kind === 'remove')
    .map((entry) => entry.nodeId),
))

function reconcileWizardPendingOps() {
  sourcePendingOps.reconcileWithCatalog(
    new Set([
      ...backupSelectableRows.value,
      ...step2SelectableRows.value,
      ...step3SelectableRows.value,
    ].map((row) => row.id)),
    wizardActiveRemovalNodeIds.value,
  )
}

const store = useProtectionDemoStore()
const pageRequests = usePageRequestScope()

function showApiError(err: unknown, fallback?: string) {
  const message = apiErrorMessage(err, fallback)
  if (!message) return
  ElMessage.error({ message, grouping: true })
}

function showApiErrorI18n(err: unknown, fallback: string) {
  const message = apiErrorMessageI18n(err, t, fallback)
  if (!message) return
  ElMessage.error({ message, grouping: true })
}

const RESTORE_ALREADY_RUNNING_CODE = 'RESTORE.ALREADY_RUNNING'

type RestoreAlreadyRunningMeta = {
  taskUuid: string
  displayName: string
  sourceType: RestoreEndpointType
  sourceRefId: number
}

function restoreAlreadyRunningMeta(err: unknown): RestoreAlreadyRunningMeta | null {
  const apiErr = toApiError(err)
  if (apiErr.errorCode !== RESTORE_ALREADY_RUNNING_CODE) return null
  const meta = apiErr.meta || {}
  const taskUuid = typeof meta.task_uuid === 'string' ? meta.task_uuid : ''
  const sourceType = meta.source_type === 'agent' || meta.source_type === 'nas'
    ? meta.source_type
    : null
  const sourceRefId = Number(meta.source_ref_id)
  if (!taskUuid || !sourceType || !Number.isFinite(sourceRefId) || sourceRefId <= 0) return null
  return {
    taskUuid,
    displayName: typeof meta.display_name === 'string' ? meta.display_name : '',
    sourceType,
    sourceRefId,
  }
}

async function handleRestoreAlreadyRunning(err: unknown): Promise<boolean> {
  const meta = restoreAlreadyRunningMeta(err)
  if (!meta) return false
  const sourceName = displayNameForSource(meta.sourceType, meta.sourceRefId, `${meta.sourceType}:${meta.sourceRefId}`)
  const taskName = meta.displayName || meta.taskUuid
  const items = [
    t('protection.backupsPage.restoreAlreadyRunningMessage', {
      source: sourceName,
      task: taskName,
    }),
    t('protection.backupsPage.restoreAlreadyRunningHint'),
  ]
  try {
    await ElMessageBox.alert(
      h('div', { class: 'reset-backup-config-dialog__warning', role: 'alert' }, [
        h('ol', { class: 'reset-backup-config-dialog__warning-list' }, items.map((item, index) => (
          h('li', { class: 'reset-backup-config-dialog__warning-item' }, [
            h('span', { class: 'reset-backup-config-dialog__warning-index' }, String(index + 1)),
            h('span', { class: 'reset-backup-config-dialog__warning-text' }, item),
          ])
        ))),
      ]),
      t('protection.backupsPage.restoreAlreadyRunningTitle'),
      {
        customClass: 'restore-already-running-dialog',
        showConfirmButton: false,
        showCancelButton: false,
        showClose: true,
        closeOnClickModal: true,
        closeOnPressEscape: true,
      },
    )
  } catch {
    return true
  }
  return true
}

async function runRestoreJobsSequentially(jobs: Array<() => Promise<unknown>>) {
  for (const job of jobs) {
    await job()
  }
}

const {
  pipelineStep2Ids,
  pipelineStep3Ids,
  pipelineStep2Count,
  pipelineStep3Count,
  pipelineReady,
  refreshPipelineStep2PlusIds,
  refreshPipelineStep3Ids,
  setPipelineStep,
  bootstrapPipeline,
} = useBackupSourcePipeline()

const backupConfigSourceIds = ref(new Set<string>())
const backupConfigRows = ref<BackupConfig[]>([])
const backupConfigDetailById = ref(new Map<number, BackupConfigDetail>())
const repositoryById = ref(new Map<number, StorageRepository>())
const backupPolicyById = ref(new Map<number, BackupPolicy>())
const fileFilterById = ref(new Map<number, FileFilterRule>())
const backupSnapshotRows = ref<BackupSourceSnapshot[]>([])
const backupTaskRows = ref<TaskRow[]>([])
const resetTaskRows = ref<TaskRow[]>([])
const restoreTaskRows = ref<TaskRow[]>([])
const restoreRecordRows = ref<RestoreRecord[]>([])
const EMPTY_TASK_ROWS: TaskRow[] = []
const sourceRelatedTaskRows = computed(() => [
  ...backupTaskRows.value,
  ...resetTaskRows.value,
  ...restoreTaskRows.value,
])

const REAL_BACKUP_ID_PREFIX = 'cfg-'

function realBackupId(configId: number) {
  return `${REAL_BACKUP_ID_PREFIX}${configId}`
}

function realConfigIdFromBackupId(backupId: string) {
  if (!backupId.startsWith(REAL_BACKUP_ID_PREFIX)) return 0
  const id = Number(backupId.slice(REAL_BACKUP_ID_PREFIX.length))
  return Number.isFinite(id) ? id : 0
}


function endpointUiId(type: string, refId: number) {
  return `${type}:${refId}`
}

function parseEndpointUiId(id: string): { type: 'agent' | 'nas' | 'proxy'; refId: number } | null {
  const [type, rawRefId] = id.split(':')
  const refId = Number(rawRefId)
  if ((type !== 'agent' && type !== 'nas' && type !== 'proxy') || !Number.isFinite(refId) || refId <= 0) return null
  return { type, refId }
}

async function refreshBackupConfigs(signal?: AbortSignal): Promise<boolean> {
  try {
    const result = await listBackupConfigs({ page: 1, page_size: 200 }, { signal })
    backupConfigRows.value = result.results
    backupConfigSourceIds.value = new Set(
      result.results.map((config) => `${config.source_type}:${config.source_ref_id}`),
    )
    const [details, repositories, policies, filters, snapshots, backupTasks, resetTasks, restoreTasks, restoreRecords] = await Promise.all([
      Promise.all(result.results.map((config) => getBackupConfig(config.id, { signal }))),
      listAllStorageRepositories({ page_size: 10 }, { signal }).catch((e) => {
        if (pageRequests.isAbortError(e)) throw e
        return [] as StorageRepository[]
      }),
      listBackupPolicies({ page: 1, page_size: 500 }, { signal }).catch((e) => {
        if (pageRequests.isAbortError(e)) throw e
        return { count: 0, results: [] as BackupPolicy[] }
      }),
      listFileFilterRules({ page: 1, page_size: 500 }, { signal }).catch((e) => {
        if (pageRequests.isAbortError(e)) throw e
        return { count: 0, results: [] as FileFilterRule[] }
      }),
      listBackupSourceSnapshots({ page: 1, page_size: 500, ordering: '-created_at' }, { signal }).catch((e) => {
        if (pageRequests.isAbortError(e)) throw e
        return { count: 0, results: [] as BackupSourceSnapshot[] }
      }),
      listTasks({ task_type: 'backup', page: 1, page_size: 500 }, { signal }).catch((e) => {
        if (pageRequests.isAbortError(e)) throw e
        return { count: 0, results: [] as TaskRow[] }
      }),
      listTasks({ task_type: 'backup_config_reset', page: 1, page_size: 500 }, { signal }).catch((e) => {
        if (pageRequests.isAbortError(e)) throw e
        return { count: 0, results: [] as TaskRow[] }
      }),
      listTasks({ task_type: 'restore', page: 1, page_size: 500 }, { signal }).catch((e) => {
        if (pageRequests.isAbortError(e)) throw e
        return { count: 0, results: [] as TaskRow[] }
      }),
      listRestoreRecords({ page: 1, page_size: 500 }, { signal }).catch((e) => {
        if (pageRequests.isAbortError(e)) throw e
        return { count: 0, results: [] as RestoreRecord[] }
      }),
    ])
    backupConfigDetailById.value = new Map(details.map((config) => [config.id, config]))
    repositoryById.value = new Map(repositories.map((repo) => [repo.id, repo]))
    backupPolicyById.value = new Map(policies.results.map((policy) => [policy.id, policy]))
    fileFilterById.value = new Map(filters.results.map((rule) => [rule.id, rule]))
    backupSnapshotRows.value = snapshots.results
    backupTaskRows.value = backupTasks.results
    resetTaskRows.value = resetTasks.results
    restoreTaskRows.value = restoreTasks.results
    restoreRecordRows.value = restoreRecords.results
    syncRealBackupConfigsToDemoStore(details, snapshots.results)
    syncRestoreRecordsToFlowTasks(restoreRecords.results, restoreTasks.results)
    return true
  } catch (e) {
    if (pageRequests.isAbortError(e)) throw e
    ElMessage.error({
      message: isInvalidCompressionLevelError(e)
        ? t('protection.backupsPage.compressionInvalidValue')
        : apiErrorMessage(e, t('protection.backupsPage.backupConfigLoadFailed')),
      grouping: true,
    })
    backupConfigRows.value = []
    backupConfigSourceIds.value = new Set()
    backupConfigDetailById.value = new Map()
    backupSnapshotRows.value = []
    backupTaskRows.value = []
    resetTaskRows.value = []
    restoreTaskRows.value = []
    restoreRecordRows.value = []
    return false
  }
}

function displayNameForSource(type: string, refId: number, fallback: string) {
  const row = backupSelectableById.value.get(endpointUiId(type, refId))
  return row?.nodeName || row?.name || fallback || `#${refId}`
}

function ensureEndpointDemoOption(type: string, refId: number, name: string) {
  const id = endpointUiId(type, refId)
  const row = backupSelectableById.value.get(id)
  const label = row?.nodeName || row?.name || name || `#${refId}`
  const hostname = row?.hostname || label
  const status = row?.status || 'online'
  if (type === 'nas') {
    if (!store.getNas(id)) {
      store.addNas({
        id,
        name: label,
        hostname,
        status,
        registeredAt: row?.registeredAt,
      })
    }
    return
  }
  if (!store.getHost(id)) {
    store.addHost({
      id,
      name: label,
      hostname,
      nodeName: label,
      nodeIp: row?.nodeIp || '',
      status,
      registeredAt: row?.registeredAt,
    })
  }
}

function snapshotTime(snapshot: BackupSourceSnapshot) {
  return snapshot.finished_at || snapshot.started_at || snapshot.created_at || new Date().toISOString()
}

function normalizeDemoPathType(value: unknown): DemoPathType {
  const raw = String(value || '').trim().toLowerCase()
  if (raw === 'file') return 'file'
  if (raw === 'directory' || raw === 'dir') return 'directory'
  return 'unknown'
}

function snapshotDirectoryTreeStub(_path: string): DemoSnapshot['treeByPath'][string] {
  return []
}

function syncRealBackupConfigsToDemoStore(
  configs: BackupConfigDetail[],
  snapshots: BackupSourceSnapshot[],
) {
  const snapshotGroups = new Map<number, BackupSourceSnapshot[]>()
  for (const snapshot of snapshots) {
    const configId = Number(snapshot.backup_config_id || 0)
    if (!configId) continue
    snapshotGroups.set(configId, [...(snapshotGroups.get(configId) || []), snapshot])
  }
  const realIds = new Set(configs.map((config) => realBackupId(config.id)))
  const liveSourceIds = new Set(
    configs.map((config) => endpointUiId(config.source_type, Number(config.source_ref_id))),
  )
  store.backups.value = store.backups.value.filter((backup) => {
    if (!backup.id.startsWith(REAL_BACKUP_ID_PREFIX)) return true
    if (!realIds.has(backup.id)) return false
    return backup.sources.some((source) => liveSourceIds.has(source.hostId))
  })
  for (const config of configs) {
    const sourceUiId = endpointUiId(config.source_type, Number(config.source_ref_id))
    ensureEndpointDemoOption(config.source_type, Number(config.source_ref_id), config.name)
    for (const plan of config.recovery_plans || []) {
      if (plan.target_ref_id) {
        ensureEndpointDemoOption(plan.target_type || 'agent', Number(plan.target_ref_id), `#${plan.target_ref_id}`)
      }
    }
    const dirsById = new Map(config.directories.map((dir) => [dir.id, dir]))
    const demoSnapshots = (snapshotGroups.get(config.id) || [])
      .slice()
      .sort((a, b) => new Date(snapshotTime(b)).getTime() - new Date(snapshotTime(a)).getTime())
      .map<DemoSnapshot>((snapshot) => {
        const directories = snapshot.directories?.length
          ? snapshot.directories
          : config.directories.map((dir) => ({
              id: 0,
              backup_config_dir_id: dir.id,
              source_path: dir.path,
              path_type: dir.path_type || 'unknown',
              display_name: dir.display_name,
              repository_id: config.repository_id,
              status: 'available',
              created_at: snapshot.created_at,
              size_bytes: 0,
              file_count: 0,
              dir_count: 0,
            } as BackupSourceSnapshotDirectory))
        const treeByPath: DemoSnapshot['treeByPath'] = {}
        const dirs = directories.map((dir) => {
          const path = dir.source_path || dirsById.get(dir.backup_config_dir_id)?.path || '/'
          const pathType = normalizeDemoPathType(dir.path_type || dirsById.get(dir.backup_config_dir_id)?.path_type)
          treeByPath[path] = snapshotDirectoryTreeStub(path)
          return {
            hostId: sourceUiId,
            hostName: displayNameForSource(config.source_type, Number(config.source_ref_id), snapshot.source_display_name || config.name),
            path,
            pathType,
            sizeBytes: Number(dir.size_bytes || 0),
            fileCount: Number(dir.file_count || 0),
            innerDirCount: Number(dir.dir_count || 0),
          }
        })
        return {
          id: String(snapshot.id),
          startTime: snapshot.started_at || snapshot.created_at,
          endTime: snapshotTime(snapshot),
          status: snapshot.status || 'available',
          sizeBytes: Number(snapshot.total_size_bytes || 0),
          fileCount: Number(snapshot.file_count || 0),
          dirCount: Number(snapshot.dir_count || 0),
          dirs,
          treeByPath,
        }
      })
    const existing = store.getBackup(realBackupId(config.id))
    const row: DemoBackup = {
      id: realBackupId(config.id),
      name: config.name,
      remark: config.remark || '',
      policyId: config.backup_policy_id ? String(config.backup_policy_id) : '',
      globalFilterId: config.file_filter_rule_id ? String(config.file_filter_rule_id) : '',
      compressionLevel: config.compression_level,
      targetId: String(config.repository_id),
      sources: config.directories.length
        ? config.directories.map((dir) => ({ hostId: sourceUiId, path: dir.path, pathType: normalizeDemoPathType(dir.path_type) }))
        : [{ hostId: sourceUiId, path: '/', pathType: 'directory' }],
      status: demoSnapshots.length ? 'completed' : 'idle',
      latestSnapshotAt: demoSnapshots[0]?.endTime || null,
      snapshots: demoSnapshots,
    }
    if (existing) Object.assign(existing, row)
    else store.addBackup(row)
  }
}

function currentBackupConfigDetailsForSnapshotSync(): BackupConfigDetail[] | null {
  if (!backupConfigRows.value.length) return null
  const details: BackupConfigDetail[] = []
  for (const config of backupConfigRows.value) {
    const detail = backupConfigDetailById.value.get(config.id)
    if (!detail) return null
    details.push(detail)
  }
  return details
}

function syncCurrentBackupSnapshotsToDemoStore(snapshots: BackupSourceSnapshot[]) {
  const details = currentBackupConfigDetailsForSnapshotSync()
  if (!details) return
  syncRealBackupConfigsToDemoStore(details, snapshots)
}

function restoreRecordTaskRow(record: RestoreRecord, tasks: TaskRow[]) {
  return tasks.find((task) => task.task_uuid === record.task_uuid || task.id === record.task_id)
}

function restoreRecordFlowStatus(record: RestoreRecord, task: TaskRow | undefined): DemoFlowTaskStatus {
  const status = String(task?.status || '').toLowerCase()
  if (['success', 'completed', 'done'].includes(status)) return 'completed'
  if (status === 'cancelled') return 'failed'
  if (['failed', 'timeout'].includes(status)) return 'failed'
  const itemStatuses = record.items.map((item) => String(item.status || '').toLowerCase())
  if (itemStatuses.length && itemStatuses.every((status) => status === 'success')) return 'completed'
  if (itemStatuses.some((status) => status === 'cancelled') && !itemStatuses.some((status) => status === 'running' || status === 'pending')) {
    return 'failed'
  }
  if (itemStatuses.some((status) => status === 'failed')) return 'failed'
  return 'running'
}

function restoreRecordToFlowTask(record: RestoreRecord, tasks: TaskRow[] = restoreTaskRows.value): DemoFlowTask {
  const task = restoreRecordTaskRow(record, tasks)
  const configId = Number(record.backup_config_id || 0)
  const backupId = configId ? realBackupId(configId) : undefined
  const backupName = backupId ? store.getBackup(backupId)?.name : ''
  const progress = Number(task?.progress ?? 0)
  const status = restoreRecordFlowStatus(record, task)
  return {
    id: `restore-${record.id}`,
    title: task?.display_name || `${t('protection.backupsPage.flowTaskKindRestore')} · ${backupName || record.restore_uid}`,
    kind: 'restore',
    status,
    progress: status === 'completed' ? 100 : Number.isFinite(progress) ? Math.max(6, progress) : 6,
    phaseIndex: status === 'completed' ? 4 : status === 'failed' ? 3 : 1,
    startedAt: task?.started_at || record.created_at,
    endedAt: task?.finished_at || (status === 'completed' || status === 'failed' ? record.updated_at : null),
    backupId,
  }
}

function syncRestoreRecordsToFlowTasks(records: RestoreRecord[], tasks: TaskRow[]) {
  const realRestoreIds = new Set(records.map((record) => `restore-${record.id}`))
  restoreFlowTasks.value = restoreFlowTasks.value.filter((task) =>
    !task.id.startsWith('restore-') || realRestoreIds.has(task.id),
  )
  for (const record of records.slice().sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())) {
    const row = restoreRecordToFlowTask(record, tasks)
    const existing = restoreFlowTasks.value.find((item) => item.id === row.id)
    if (existing) Object.assign(existing, row)
    else restoreFlowTasks.value.unshift(row)
  }
}

function restoreRecordsForSource(sourceId: string) {
  const endpoint = parseEndpointUiId(sourceId)
  if (!endpoint) return []
  const configIds = new Set(sourceBackupConfigIds(sourceId))
  return restoreRecordRows.value.filter((record) => {
    if (endpointUiId(record.source_type, record.source_ref_id) === sourceId) return true
    const configId = Number(record.backup_config_id || 0)
    return configId > 0 && configIds.has(configId)
  })
}

function restoreRecordIsRunning(record: RestoreRecord) {
  return restoreRecordFlowStatus(record, restoreRecordTaskRow(record, restoreTaskRows.value)) === 'running'
}

async function refreshRestoreRecords(signal?: AbortSignal) {
  const records = await listRestoreRecords({ page: 1, page_size: 500 }, { signal })
  restoreRecordRows.value = records.results
  syncRestoreRecordsToFlowTasks(records.results, restoreTaskRows.value)
}

async function refreshStep3RuntimeRows(signal?: AbortSignal) {
  const ids = step3SelectableRows.value.map((row) => row.id).filter(Boolean)
  if (!ids.length) return
  const list = await listBackupSelectableSources({
    ids: ids.join(','),
    expand: 'runtime',
  }, signal ? { signal } : undefined)
  const runtimeById = new Map(list.results.map((item) => [item.id, item.runtime]))
  step3SelectableRows.value = step3SelectableRows.value.map((row) => {
    const runtime = runtimeById.get(row.id)
    return runtime ? { ...row, runtime } : row
  })
  const rows = step3SelectableRows.value
  backupSnapshotRows.value = expandedSnapshots(rows)
  for (const row of rows) {
    const snapshot = recordValue(row.runtime).latest_snapshot as BackupSourceSnapshot | undefined
    if (snapshot?.id) {
      recoverySnapshotDetails.value.delete(Number(snapshot.id))
    }
  }
  syncCurrentBackupSnapshotsToDemoStore(backupSnapshotRows.value)
  backupTaskRows.value = expandedTasks(rows, 'backup')
  restoreTaskRows.value = expandedTasks(rows, 'restore')
}

function normalizeSourceIdList(ids: string[]) {
  const seen = new Set<string>()
  return ids.filter((id) => {
    if (!id || seen.has(id)) return false
    seen.add(id)
    return true
  })
}

function sameIdList(a: string[], b: string[]) {
  return a.length === b.length && a.every((id, index) => id === b[index])
}

const initialFlowMainStep = flowStepFromRouteQuery(route.query.step) ?? consumeStoredFlowReturnStep() ?? 0
const flowMainStep = ref<0 | 1 | 2>(initialFlowMainStep)
const taskSearchQuery = ref('')
const debouncedTaskSearchQuery = ref('')
const step3SearchField = ref<BackupSelectableSearchField>('source_name')
const step3SourceType = ref<'host' | 'nas' | ''>('')
const step3SourceStatus = ref<'' | 'active' | 'error' | 'inactive' | 'remove_failed' | 'removing'>('')
const step3Availability = ref<BackupSelectableAvailability | ''>('')
const step3BackupTaskStatus = ref<BackupSelectableTaskStatus | ''>('')
const step3RestoreTaskStatus = ref<BackupSelectableTaskStatus | ''>('')
const step3AdvancedSourceName = ref('')
const step3AdvancedHostname = ref('')
const step3AdvancedIp = ref('')
const step3BackupPolicyId = ref<number | ''>('')
const step3FileFilterRuleId = ref<number | ''>('')
const step3RepositoryId = ref<number | ''>('')
const draftStep3SourceType = ref<'host' | 'nas' | ''>('')
const draftStep3SourceStatus = ref<'' | 'active' | 'error' | 'inactive' | 'remove_failed' | 'removing'>('')
const draftStep3Availability = ref<BackupSelectableAvailability | ''>('')
const draftStep3BackupTaskStatus = ref<BackupSelectableTaskStatus | ''>('')
const draftStep3RestoreTaskStatus = ref<BackupSelectableTaskStatus | ''>('')
const draftStep3AdvancedSourceName = ref('')
const draftStep3AdvancedHostname = ref('')
const draftStep3AdvancedIp = ref('')
const draftStep3BackupPolicyId = ref<number | ''>('')
const draftStep3FileFilterRuleId = ref<number | ''>('')
const draftStep3RepositoryId = ref<number | ''>('')
const TASK_SEARCH_DELAY_MS = 300
let taskSearchDebounceTimer: ReturnType<typeof setTimeout> | null = null

const REC_WIZARD_DRAWER_Z_INDEX = 2250

const REC_ENTRY_PREVIEW_MAX = 3
const FLOW_DETAIL_POPOVER_HIDE_AFTER_MS = 350

const TABLE_HEADER_STYLE: Record<string, string> = {
  background: 'rgba(248, 250, 252, 0.96)',
  color: 'rgb(71 85 105)',
  fontWeight: '600',
  whiteSpace: 'nowrap',
}

const FLOW_PICK_TABLE_COL_MIN = {
  source: 168,
  connection: 118,
  cpu: 79,
  memory: 90,
  diskCount: 87,
} as const

const FLOW_START_BACKUP_TABLE_COL_MIN = {
  connection: 220,
  backupDirs: 260,
  compression: 190,
  targetRepo: 280,
  binding: 210,
} as const

const flowStep0Pager = reactive({ page: 1, pageSize: 10 })
const flowStep1Pager = reactive({ page: 1, pageSize: 10 })
const flowStep2Pager = reactive({ page: 1, pageSize: 10 })
type FlowSourceTypeFilter = 'host' | 'nas' | 'nas_smb' | 'nas_nfs'
type FlowSourceStatusFilter = 'online' | 'offline'
type FlowBindingFilter = 'bound' | 'unbound'
type FlowBackupTaskStatusFilter = 'running' | 'completed' | 'failed' | 'idle'
type FlowRestoreTaskFilter = 'running' | 'none'
type FlowRepoHealthFilter = 'online' | 'offline' | 'unknown'
type FlowLastBackupMode = 'all' | 'never' | 'days24' | 'days7' | 'days30' | 'custom'
type FlowHeaderFilterKey =
  | 'sourceType'
  | 'sourceStatus'
  | 'backupTaskStatus'
  | 'repoHealth'
  | 'policyBinding'
  | 'fileFilterBinding'
type FlowHeaderFilterOption = { text: string; value: string }

const flowAdvancedFilterOpen = ref(false)
const flowFilterSourceTypes = ref<FlowSourceTypeFilter[]>([])
const flowFilterSourceStatuses = ref<FlowSourceStatusFilter[]>([])
const flowFilterPolicyBinding = ref<FlowBindingFilter[]>([])
const flowFilterFileFilterBinding = ref<FlowBindingFilter[]>([])
const flowFilterBackupTaskStatuses = ref<FlowBackupTaskStatusFilter[]>([])
const flowFilterRestoreTasks = ref<FlowRestoreTaskFilter[]>([])
const flowFilterRepoHealth = ref<FlowRepoHealthFilter[]>([])
const flowFilterNodeQuery = ref('')
const flowFilterTargetQuery = ref('')
const flowFilterDirectoryQuery = ref('')
const flowFilterLastBackupMode = ref<FlowLastBackupMode>('all')
const flowFilterLastBackupRange = ref<[Date, Date] | null>(null)
const flowHeaderFilterOpen = reactive<Record<FlowHeaderFilterKey, boolean>>({
  sourceType: false,
  sourceStatus: false,
  backupTaskStatus: false,
  repoHealth: false,
  policyBinding: false,
  fileFilterBinding: false,
})
const flowHeaderFilterSearch = reactive<Record<FlowHeaderFilterKey, string>>({
  sourceType: '',
  sourceStatus: '',
  backupTaskStatus: '',
  repoHealth: '',
  policyBinding: '',
  fileFilterBinding: '',
})

function flowStepFromRouteQuery(value: unknown): 0 | 1 | 2 | null {
  const normalized = Array.isArray(value) ? value[0] : value
  if (normalized === 'backup-config' || normalized === '1') return 1
  if (normalized === 'start-backup' || normalized === '2') return 2
  if (normalized === 'source' || normalized === '0') return 0
  return null
}

function flowStepQueryValue(step: 0 | 1 | 2) {
  if (step === 1) return 'backup-config'
  if (step === 2) return 'start-backup'
  return 'source'
}

function consumeStoredFlowReturnStep(): 0 | 1 | 2 | null {
  if (typeof sessionStorage === 'undefined') return null
  const raw = sessionStorage.getItem(FLOW_RETURN_STEP_STORAGE_KEY)
  sessionStorage.removeItem(FLOW_RETURN_STEP_STORAGE_KEY)
  return flowStepFromRouteQuery(raw)
}

function clearStoredFlowReturnStep() {
  if (typeof sessionStorage === 'undefined') return
  sessionStorage.removeItem(FLOW_RETURN_STEP_STORAGE_KEY)
}

function applyFlowStep(step: 0 | 1 | 2 | null) {
  if (step === null) return
  if (step === flowMainStep.value) return
  if (step === 2) {
    enterStartBackupStep({ syncRoute: false })
    return
  }
  flowMainStep.value = step
}

function syncFlowStepRoute(step: 0 | 1 | 2, mode: 'push' | 'replace' = 'push') {
  if (route.path !== '/protection/backups') return
  const nextStep = flowStepQueryValue(step)
  if (route.query.step === nextStep) return
  const query = { ...route.query, step: nextStep }
  const location = { path: route.path, query }
  if (mode === 'replace') {
    void router.replace(location)
  } else {
    void router.push(location)
  }
}

function applyRequestedFlowStep(value: unknown) {
  const requestedStep = flowStepFromRouteQuery(value)
  applyFlowStep(requestedStep)
  if (requestedStep !== null) {
    clearStoredFlowReturnStep()
  }
  return requestedStep !== null
}

const flowToolbarRef = ref<HTMLElement | null>(null)
const flowTableZoneRef = ref<HTMLElement | null>(null)
const flowTableMaxHeight = ref(400)
const FLOW_TABLE_FOOTER_RESERVE = 64

let flowTableZoneObserver: ResizeObserver | null = null
let flowTableResizeFrame = 0

function updateFlowTableMaxHeight() {
  const zone = flowTableZoneRef.value
  if (!zone) return

  const pageBody = zone.closest('.page-body') as HTMLElement | null
  const zoneTop = zone.getBoundingClientRect().top
  const pageBottom = pageBody?.getBoundingClientRect().bottom ?? window.visualViewport?.height ?? window.innerHeight
  const visibleFooter = zone
    ? Array.from(zone.querySelectorAll<HTMLElement>('.hfl-list-footer'))
      .find((footer) => footer.offsetParent !== null)
    : null
  const footerHeight = visibleFooter?.offsetHeight || FLOW_TABLE_FOOTER_RESERVE
  const nextHeight = Math.max(280, Math.floor(pageBottom - zoneTop - footerHeight))

  if (flowTableMaxHeight.value !== nextHeight) {
    flowTableMaxHeight.value = nextHeight
  }
}

function scheduleFlowTableMaxHeightUpdate() {
  if (flowTableResizeFrame) window.cancelAnimationFrame(flowTableResizeFrame)
  flowTableResizeFrame = window.requestAnimationFrame(() => {
    flowTableResizeFrame = 0
    updateFlowTableMaxHeight()
  })
}

function layoutFlowTables() {
  nextTick(() => {
    sourceTableRef.value?.doLayout?.()
    step2TableRef.value?.doLayout?.()
    step3TableRef.value?.doLayout?.()
  })
}

function paginateFlowList<T>(list: T[], pager: { page: number; pageSize: number }) {
  const start = (pager.page - 1) * pager.pageSize
  return list.slice(start, start + pager.pageSize)
}

function clampFlowPagerPage(pager: { page: number; pageSize: number }, total: number) {
  const maxPage = Math.max(1, Math.ceil(total / pager.pageSize) || 1)
  if (pager.page > maxPage) pager.page = maxPage
  if (pager.page < 1) pager.page = 1
}
const moreActionsOpen = ref(false)

const selectedSourceIds = ref<string[]>([])
const sourceTableRef = ref<InstanceType<typeof ElTable> | null>(null)

const backupSelectableRows = ref<FlowSourceRow[]>([])
const backupSelectableCount = ref(0)
const backupSelectableLoading = ref(initialFlowMainStep === 0)
const step2SelectableRows = ref<FlowSourceRow[]>([])
const step2SelectableCount = ref(0)
const step3SelectableRows = ref<FlowSourceRow[]>([])
const step3SelectableCount = ref(0)
const flowRefreshing = ref(false)
const flowBootstrapping = ref(false)
const flowStepDataLoading = reactive({
  1: initialFlowMainStep === 1,
  2: initialFlowMainStep === 2,
})
const backupSelectableById = ref(new Map<string, FlowSourceRow>())

function mapBackupSelectableToFlowRow(item: BackupSelectableSource): FlowSourceRow {
  return {
    id: item.id,
    name: item.name,
    hostname: item.hostname || item.name,
    nodeName: item.node_name || item.name,
    nodeIp: item.node_ip || '',
    status: item.status,
    availability: item.availability === 'online' ? 'online' : 'offline',
    registeredAt: item.registered_at || '',
    type: item.type,
    protocol: item.protocol === 'smb' ? 'smb' : item.protocol === 'nfs' ? 'nfs' : undefined,
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
    backup_configs: item.backup_configs,
    policies: item.policies,
    filters: item.filters,
    runtime: item.runtime,
  }
}

function rememberSelectableRows(rows: FlowSourceRow[]) {
  const next = new Map(backupSelectableById.value)
  for (const row of rows) next.set(row.id, row)
  backupSelectableById.value = next
}

const STEP3_EXPAND = 'backup_configs,policies,runtime'
const PREVIEW_REPOSITORY_LOCATION_KEY = '__preview_location'

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {}
}

const BACKUP_POLICY_SCHEDULE_MODES = new Set<string>([
  'interval',
  'daily',
  'weekly',
  'monthly',
  'advanced',
])

function isBackupPolicyScheduleMode(value: string): value is BackupPolicyScheduleMode {
  return BACKUP_POLICY_SCHEDULE_MODES.has(value)
}

function backupPolicyScheduleMode(value: unknown): BackupPolicyScheduleMode | undefined {
  const mode = String(value || '')
  return isBackupPolicyScheduleMode(mode) ? mode : undefined
}

function scheduleNumberList(value: unknown, min: number, max: number): number[] | undefined {
  if (!Array.isArray(value)) return undefined
  return value
    .map(Number)
    .filter((item) => Number.isInteger(item) && item >= min && item <= max)
}

function expandedPolicySchedule(value: Record<string, unknown>): BackupPolicySchedule {
  const mode = backupPolicyScheduleMode(value.mode)
  const weekdays = scheduleNumberList(value.weekdays, 1, 7)
  const monthDays = scheduleNumberList(value.month_days, 1, 31)
  return {
    enabled: value.enabled !== false,
    cron_expr: String(value.cron_expr || ''),
    ...(mode ? { mode } : {}),
    ...(typeof value.timezone === 'string' ? { timezone: value.timezone } : {}),
    ...(typeof value.starts_at === 'string' || value.starts_at === null
      ? { starts_at: value.starts_at }
      : {}),
    ...(value.interval_unit === 'minute' || value.interval_unit === 'hour' || value.interval_unit === 'day'
      ? { interval_unit: value.interval_unit }
      : {}),
    ...(Number.isFinite(Number(value.interval_value))
      ? { interval_value: Number(value.interval_value) }
      : {}),
    ...(typeof value.time === 'string' ? { time: value.time } : {}),
    ...(weekdays ? { weekdays } : {}),
    ...(monthDays ? { month_days: monthDays } : {}),
    ...(typeof value.month_end === 'boolean' ? { month_end: value.month_end } : {}),
  }
}

function isRepositoryDetailComplete(repo: StorageRepository | undefined) {
  return Boolean(repo && String(repo.repo_type || '').trim())
}

function repositoryDisplayLocation(repo: StorageRepository | undefined) {
  if (!repo) return ''
  const location = storageRepositoryLocation(repo)
  if (location) return location
  const previewLocation = repo.config?.[PREVIEW_REPOSITORY_LOCATION_KEY]
  return typeof previewLocation === 'string' ? previewLocation.trim() : ''
}

function expandedBackupConfigs(rows: FlowSourceRow[]): BackupConfigDetail[] {
  return rows.flatMap((row) => (row.backup_configs?.configs || []) as BackupConfigDetail[])
}

function expandedSnapshots(rows: FlowSourceRow[]): BackupSourceSnapshot[] {
  const snapshots = new Map<number, BackupSourceSnapshot>()
  for (const row of rows) {
    const runtime = recordValue(row.runtime)
    for (const raw of [runtime.latest_snapshot, runtime.latest_restorable_snapshot]) {
      if (!raw || typeof raw !== 'object') continue
      const snapshot = raw as BackupSourceSnapshot
      const id = Number(snapshot.id || 0)
      if (!id || snapshots.has(id)) continue
      snapshots.set(id, snapshot)
    }
  }
  return Array.from(snapshots.values())
}

function expandedTasks(rows: FlowSourceRow[], taskType: 'backup' | 'restore'): TaskRow[] {
  return rows
    .map((row) => recordValue(recordValue(row.runtime)[taskType]).latest_task)
    .filter((task): task is TaskRow => Boolean(task && typeof task === 'object'))
}

function expandedRepositories(rows: FlowSourceRow[]): StorageRepository[] {
  const repos = new Map<number, StorageRepository>()
  for (const row of rows) {
    for (const raw of row.backup_configs?.repos_preview || []) {
      const repo = recordValue(raw)
      const id = Number(repo.repository_id || 0)
      if (!id || repos.has(id)) continue
      repos.set(id, {
        id,
        organization_id: 0,
        name: String(repo.name || `#${id}`),
        repo_type: '',
        status: String(repo.status || ''),
        health: String(repo.health || ''),
        config: {
          [PREVIEW_REPOSITORY_LOCATION_KEY]: String(repo.location || ''),
        },
        capacity_bytes: Number(repo.capacity_bytes || 0),
        estimated_usage_bytes: Number(repo.used_bytes || 0),
        last_checked_at: typeof repo.last_checked_at === 'string' ? repo.last_checked_at : null,
      })
    }
  }
  return Array.from(repos.values())
}

function expandedPolicies(rows: FlowSourceRow[]): BackupPolicy[] {
  const policies = new Map<number, BackupPolicy>()
  for (const row of rows) {
    for (const raw of row.policies?.items || []) {
      const item = recordValue(raw)
      const id = Number(item.id || 0)
      if (!id || policies.has(id)) continue
      const retention = recordValue(item.retention)
      const throttling = recordValue(item.throttling)
      const errorHandling = recordValue(item.error_handling)
      const schedule = recordValue(item.schedule)
      policies.set(id, {
        id,
        name: String(item.name || `#${id}`),
        is_active: item.is_active !== false,
        schedule: expandedPolicySchedule(schedule),
        retention: {
          enabled: retention.enabled !== false,
          recent_points: Number(retention.recent_points || 0),
          hourly_enabled: Boolean(retention.hourly_enabled),
          hourly_hours: Number(retention.hourly_hours || 0),
          daily_enabled: Boolean(retention.daily_enabled),
          daily_days: Number(retention.daily_days || 0),
          weekly_enabled: Boolean(retention.weekly_enabled),
          weekly_weeks: Number(retention.weekly_weeks || 0),
          monthly_enabled: Boolean(retention.monthly_enabled),
          monthly_months: Number(retention.monthly_months || 0),
          annual_enabled: Boolean(retention.annual_enabled),
          annual_years: Number(retention.annual_years || 0),
        },
        throttling: {
          enabled: Boolean(throttling.enabled),
          unlimited: throttling.unlimited !== false,
          rate_mbps: Number(throttling.rate_mbps || 0),
        },
        error_handling: {
          enabled: Boolean(errorHandling.enabled),
          ignore_directory_read_errors: Boolean(errorHandling.ignore_directory_read_errors),
          ignore_file_read_errors: Boolean(errorHandling.ignore_file_read_errors),
          ignore_unknown_entries: Boolean(errorHandling.ignore_unknown_entries),
        },
        schedule_summary: String(item.schedule_summary || ''),
        retention_summary: String(item.retention_summary || ''),
        related_backup_count: Number(item.related_backup_count || 0),
        created_at: String(item.created_at || ''),
        updated_at: String(item.updated_at || ''),
      })
    }
  }
  return Array.from(policies.values())
}

function expandedFilters(rows: FlowSourceRow[]): FileFilterRule[] {
  const filters = new Map<number, FileFilterRule>()
  for (const row of rows) {
    for (const raw of row.filters?.items || []) {
      const item = recordValue(raw)
      const id = Number(item.id || 0)
      if (!id || filters.has(id)) continue
      filters.set(id, {
        id,
        name: String(item.name || `#${id}`),
        is_active: item.is_active !== false,
        ignore_patterns: String(item.ignore_patterns || ''),
        large_file_limit_enabled: Boolean(item.large_file_limit_enabled),
        large_file_bytes_max: Number(item.large_file_bytes_max || 0),
        ignore_cache_directories: item.ignore_cache_directories !== false,
        current_filesystem_only: Boolean(item.current_filesystem_only),
        summary: String(item.summary || ''),
        related_backup_count: Number(item.related_backup_count || 0),
        created_at: String(item.created_at || ''),
        updated_at: String(item.updated_at || ''),
      })
    }
  }
  return Array.from(filters.values())
}

function syncExpandedStep3Rows(rows: FlowSourceRow[]) {
  const configs = expandedBackupConfigs(rows)
  backupConfigRows.value = configs
  backupConfigSourceIds.value = new Set(configs.map((config) => `${config.source_type}:${config.source_ref_id}`))
  backupConfigDetailById.value = new Map(configs.map((config) => [config.id, config]))
  const repositories = new Map(repositoryById.value)
  for (const repo of expandedRepositories(rows)) {
    const existing = repositories.get(repo.id)
    const previewLocation = repo.config?.[PREVIEW_REPOSITORY_LOCATION_KEY]
    if (isRepositoryDetailComplete(existing)) {
      if (typeof previewLocation === 'string' && previewLocation.trim()) {
        repositories.set(repo.id, {
          ...existing,
          config: {
            ...(existing.config || {}),
            [PREVIEW_REPOSITORY_LOCATION_KEY]: previewLocation,
          },
        })
      }
    } else {
      repositories.set(repo.id, repo)
    }
  }
  repositoryById.value = repositories
  const policies = new Map(backupPolicyById.value)
  for (const policy of expandedPolicies(rows)) {
    if (!policies.has(policy.id)) policies.set(policy.id, policy)
  }
  backupPolicyById.value = policies
  const filters = new Map(fileFilterById.value)
  for (const rule of expandedFilters(rows)) {
    if (!filters.has(rule.id)) filters.set(rule.id, rule)
  }
  fileFilterById.value = filters
  backupSnapshotRows.value = expandedSnapshots(rows)
  backupTaskRows.value = expandedTasks(rows, 'backup')
  restoreTaskRows.value = expandedTasks(rows, 'restore')
  if (configs.length) syncRealBackupConfigsToDemoStore(configs, backupSnapshotRows.value)
  return configs
}

async function ensureRepositoryDetailsForConfigs(
  configs: Array<BackupConfig | BackupConfigDetail>,
  signal?: AbortSignal,
) {
  const ids = Array.from(new Set(
    configs
      .map((config) => Number(config.repository_id || 0))
      .filter((id) => id > 0 && !isRepositoryDetailComplete(repositoryById.value.get(id))),
  ))
  if (!ids.length) return

  const repos = await Promise.all(ids.map(async (id) => {
    try {
      return await getStorageRepository(id, signal ? { signal } : undefined)
    } catch (e) {
      if (pageRequests.isAbortError(e)) throw e
      return null
    }
  }))

  const next = new Map(repositoryById.value)
  for (const repo of repos) {
    if (repo) next.set(repo.id, repo)
  }
  repositoryById.value = next
}

function backupSelectableRequestParams() {
  return {
    page: flowStep0Pager.page,
    pageSize: flowStep0Pager.pageSize,
    search: debouncedTaskSearchQuery.value.trim(),
    searchField: step3SearchField.value,
    availability: step3Availability.value,
    sourceName: step3AdvancedSourceName.value,
    sourceHostname: step3AdvancedHostname.value,
    sourceIp: step3AdvancedIp.value,
    sourceType: step3SourceType.value,
    sourceStatus: step3SourceStatus.value,
  }
}

function backupSelectableRequestKey(params = backupSelectableRequestParams()) {
  return JSON.stringify(params)
}

const backupSelectableRequests = new Map<string, Promise<void>>()
let latestBackupSelectableRequestKey = ''
const flowStepDataLoadingCounts = { 1: 0, 2: 0 }
function flowStepScope(step: 0 | 1 | 2) {
  return `flow-step-${step}`
}

function cancelFlowStepRequests(step: 0 | 1 | 2) {
  pageRequests.abortScope(flowStepScope(step))
}

function cancelOtherFlowStepRequests(activeStep: 0 | 1 | 2) {
  cancelFlowStepRequests(activeStep === 0 ? 1 : 0)
  cancelFlowStepRequests(activeStep === 2 ? 1 : 2)
}

function setFlowStepDataLoading(step: 1 | 2, loading: boolean) {
  flowStepDataLoadingCounts[step] += loading ? 1 : -1
  flowStepDataLoadingCounts[step] = Math.max(0, flowStepDataLoadingCounts[step])
  flowStepDataLoading[step] = flowStepDataLoadingCounts[step] > 0
}

async function loadBackupSelectable(options: { silent?: boolean } = {}) {
  const params = backupSelectableRequestParams()
  const key = backupSelectableRequestKey(params)
  latestBackupSelectableRequestKey = key
  const running = backupSelectableRequests.get(key)
  if (running) return running

  const scope = flowStepScope(0)
  const signal = pageRequests.nextSignal(scope)
  const silent = options.silent === true
  if (!silent) backupSelectableLoading.value = true
  const request = (async () => {
    try {
      const list = await listBackupSelectableSources({
        page: params.page,
        page_size: params.pageSize,
        search: params.search || undefined,
        search_field: params.searchField,
        source_name: step3AdvancedSourceName.value.trim() || undefined,
        source_hostname: step3AdvancedHostname.value.trim() || undefined,
        source_ip: step3AdvancedIp.value.trim() || undefined,
        type: step3SourceType.value || undefined,
        source_status: step3SourceStatus.value || undefined,
        availability: params.availability || undefined,
        step: 1,
      }, { signal })
      if (latestBackupSelectableRequestKey !== key) return
      backupSelectableRows.value = list.results.map(mapBackupSelectableToFlowRow)
      backupSelectableCount.value = list.count
      rememberSelectableRows(backupSelectableRows.value)
      reconcileWizardPendingOps()
    } catch (e) {
      if (latestBackupSelectableRequestKey !== key) return
      if (pageRequests.isAbortError(e)) return
      backupSelectableRows.value = []
      backupSelectableCount.value = 0
      showApiError(e)
    } finally {
      pageRequests.releaseSignal(scope, signal)
      backupSelectableRequests.delete(key)
      if (latestBackupSelectableRequestKey === key && !silent) {
        backupSelectableLoading.value = false
      }
    }
  })()
  backupSelectableRequests.set(key, request)
  return request
}

async function loadStep2Selectable(options: { signal?: AbortSignal } = {}) {
  const signal = options.signal
  const list = await listBackupSelectableSources({
    page: flowStep1Pager.page,
    page_size: flowStep1Pager.pageSize,
    search: debouncedTaskSearchQuery.value.trim() || undefined,
    search_field: step3SearchField.value,
    source_name: step3AdvancedSourceName.value.trim() || undefined,
    source_hostname: step3AdvancedHostname.value.trim() || undefined,
    source_ip: step3AdvancedIp.value.trim() || undefined,
    type: step3SourceType.value || undefined,
    source_status: step3SourceStatus.value || undefined,
    availability: step3Availability.value || undefined,
    step: 2,
  }, signal ? { signal } : undefined)
  const rows = list.results.map(mapBackupSelectableToFlowRow)
  step2SelectableRows.value = rows
  step2SelectableCount.value = list.count
  rememberSelectableRows(rows)
}

async function loadStep3Selectable(options: { signal?: AbortSignal } = {}) {
  const signal = options.signal
  const list = await listBackupSelectableSources({
    page: flowStep2Pager.page,
    page_size: flowStep2Pager.pageSize,
    search: debouncedTaskSearchQuery.value.trim() || undefined,
    search_field: step3SearchField.value,
    source_name: step3AdvancedSourceName.value.trim() || undefined,
    source_hostname: step3AdvancedHostname.value.trim() || undefined,
    source_ip: step3AdvancedIp.value.trim() || undefined,
    type: step3SourceType.value || undefined,
    source_status: step3SourceStatus.value || undefined,
    availability: step3Availability.value || undefined,
    backup_task_status: step3BackupTaskStatus.value || undefined,
    restore_task_status: step3RestoreTaskStatus.value || undefined,
    backup_policy_id: step3BackupPolicyId.value || undefined,
    file_filter_rule_id: step3FileFilterRuleId.value || undefined,
    repository_id: step3RepositoryId.value || undefined,
    step: 3,
    expand: STEP3_EXPAND,
  }, signal ? { signal } : undefined)
  const rows = list.results.map(mapBackupSelectableToFlowRow)
  step3SelectableRows.value = rows
  step3SelectableCount.value = list.count
  rememberSelectableRows(rows)
  const configs = syncExpandedStep3Rows(rows)
  await ensureRepositoryDetailsForConfigs(configs, signal)
}

async function refreshStep3State(signal?: AbortSignal) {
  if (flowMainStep.value === 2) {
    await loadStep3Selectable({ signal })
    // Step 3 task links navigate through restore records, which are not part
    // of the selectable-source runtime expansion.
    await refreshBackupConfigs(signal)
    return
  }
  await Promise.all([
    refreshBackupConfigs(signal),
    refreshPipelineStep3Ids(signal),
  ])
}

async function refreshBackupSourcePoolCount(signal?: AbortSignal) {
  const list = await listBackupSelectableSources({
    page: 1,
    page_size: 1,
    step: 1,
  }, { signal })
  backupSelectableCount.value = list.count
}

function syncWizardCountsFromPipeline() {
  step2SelectableCount.value = pipelineStep2Count.value
  step3SelectableCount.value = pipelineStep3Count.value
}

function syncStep2WizardCountFromPipeline() {
  step2SelectableCount.value = pipelineStep2Count.value
}

async function refreshSelectableCatalog(ids: string[], signal?: AbortSignal) {
  const realIds = normalizeSourceIdList(ids.filter(isBackupSelectableId))
  if (!realIds.length) return
  try {
    const list = await listBackupSelectableSources({ ids: realIds.join(',') }, { signal })
    rememberSelectableRows(list.results.map(mapBackupSelectableToFlowRow))
  } catch (e) {
    if (pageRequests.isAbortError(e)) throw e
    /* best-effort for step2/step3 display */
  }
}

async function refreshFlowStepData(
  step: 0 | 1 | 2 = flowMainStep.value,
  options: { showLoading?: boolean } = {},
) {
  const showLoading = options.showLoading ?? true
  if (step === 0) {
    await loadBackupSelectable({ silent: !showLoading })
    return
  }

  const scope = flowStepScope(step)
  const signal = pageRequests.nextSignal(scope)
  if (showLoading) setFlowStepDataLoading(step, true)
  try {
    if (step === 1) {
      // Refresh pipeline ids so stale step-3 caches cannot hide step=2 rows.
      // Keep loadStep2Selectable's filtered count for the table pager / search.
      await refreshPipelineStep2PlusIds(signal)
      if (!pageRequests.isCurrentSignal(scope, signal)) return
      await loadStep2Selectable({ signal })
      return
    }

    await loadStep3Selectable({ signal })
    // Step 3 task links navigate through restore records, which are not part
    // of the selectable-source runtime expansion.
    await refreshBackupConfigs(signal)
  } catch (e) {
    if (!pageRequests.isAbortError(e)) {
      showApiError(e)
    }
  } finally {
    pageRequests.releaseSignal(scope, signal)
    if (showLoading) setFlowStepDataLoading(step, false)
  }
}

function flowRowFromSourceId(id: string): FlowSourceRow | null {
  return backupSelectableById.value.get(id) ?? null
}

function offlineFlowSourcesFromIds(ids: string[]) {
  return ids
    .map((id) => flowRowFromSourceId(id))
    .filter((row): row is FlowSourceRow => row != null && !flowSourceCanOperate(row))
}

const step1Selection = ref<string[]>([])
const step2TableRef = ref<InstanceType<typeof ElTable> | null>(null)
const flowAdvancingToBackupConfig = ref(false)
let syncingSourceSelection = false
let syncingStep2Selection = false


const step2PipelineSourceIds = computed(() => pipelineStep2Ids.value)
const step3PipelineSourceIds = computed(() => pipelineStep3Ids.value)

const step2AvailableSourceIds = computed(() => step2PipelineSourceIds.value)
const step3AvailableSourceIds = computed(() => step3PipelineSourceIds.value)


const step2ToolbarActionsEnabled = computed(() => step1Selection.value.length > 0)

const step2SourceList = computed(() => {
  if (flowMainStep.value === 1) return step2SelectableRows.value
  return step2AvailableSourceIds.value
    .map((id) => flowRowFromSourceId(id))
    .filter((row): row is FlowSourceRow => row != null)
})

function sourceHasBackupConfig(sourceId: string) {
  return step3PipelineSourceIds.value.includes(sourceId) || backupConfigSourceIds.value.has(sourceId)
}

const step2PendingSourceList = computed(() => {
  // On the Backup Configuration step the table is backed by step=2 API rows.
  // Do not hide those rows via stale step-3 pipeline / backup-config caches
  // (Reset Backup Config moves the source to step 2 before the caches catch up).
  if (flowMainStep.value === 1) return step2SourceList.value
  return step2SourceList.value.filter((row) => !sourceHasBackupConfig(row.id))
})

const step3ConfiguredSourceIds = computed(() =>
  normalizeSourceIdList([
    ...step3AvailableSourceIds.value,
    ...Array.from(backupConfigSourceIds.value),
  ]),
)

const step2ConfiguredSourceList = computed(() =>
  step3ConfiguredSourceIds.value
    .map((id) => flowRowFromSourceId(id))
    .filter((row): row is FlowSourceRow => row != null),
)

const step3FocusSourceIds = ref<string[] | null>(null)

function sourceRegisteredAtTimestamp(row: FlowSourceRow) {
  const time = new Date(row.registeredAt || '').getTime()
  return Number.isFinite(time) ? time : 0
}

function sortSourcesByRegisteredAtDesc(rows: FlowSourceRow[]) {
  return rows.slice().sort((a, b) => {
    const delta = sourceRegisteredAtTimestamp(b) - sourceRegisteredAtTimestamp(a)
    return delta !== 0 ? delta : a.id.localeCompare(b.id)
  })
}

const step3SourceList = computed(() => {
  if (flowMainStep.value === 2) return step3SelectableRows.value
  const configured = step2ConfiguredSourceList.value
  if (step3FocusSourceIds.value === null) return sortSourcesByRegisteredAtDesc(configured)
  const focus = new Set(step3FocusSourceIds.value)
  return sortSourcesByRegisteredAtDesc(configured.filter((row) => focus.has(row.id)))
})

const step3ReadyCount = computed(() => step3SelectableCount.value)

const step2PendingCount = computed(() => step2SelectableCount.value)

const step2AllSourcesConfigured = computed(() => step2PendingCount.value === 0 && step3ReadyCount.value > 0)

const step3TableRef = ref<InstanceType<typeof ElTable> | null>(null)
const step3SourceSelection = ref<FlowSourceRow[]>([])
let syncingStep3Selection = false

const flowSourceDetailOpen = ref(false)
const flowSourceDetailMounted = ref(false)
const flowSourceDetailDrawerRef = ref<{ openTaskDetailByUuid: (taskUuid?: string | null) => Promise<void> } | null>(null)
const activeFlowSource = ref<FlowSourceRow | null>(null)
const flowSourceDetailTab = ref<FlowSourceDetailTabInput>('overview')
const flowSourceDetailTaskSubTab = ref<FlowSourceDetailTaskSubTab>('history')
const flowSourceDetailScrollTo = ref<'dirs' | 'targets' | null>(null)
const flowSourceDetailTaskUuid = ref('')
const flowSourceDetailRestoreRecordId = ref<number | null>(null)
const flowSourceDetailRestoreRecordTaskUuid = ref('')
const backupTaskDetailOpen = ref(false)
const backupTaskDetailUuid = ref('')
const flowSourceDetailRows = computed(() => {
  const rows = Array.from(backupSelectableById.value.values())
  const active = activeFlowSource.value
  if (active && !rows.some((row) => row.id === active.id)) rows.push(active)
  return rows
})
const { drawerSize, updateDrawerWidth } = useResponsiveDrawerWidth()
const { drawerSize: nestedDrawerSize, updateDrawerWidth: updateNestedDrawerWidth } = useResponsiveDrawerWidth(2)

function openFlowSourceDetail(
  row: FlowSourceRow,
  opts?: {
    tab?: FlowSourceDetailTabInput
    taskSubTab?: FlowSourceDetailTaskSubTab
    scrollTo?: 'dirs' | 'targets'
    taskUuid?: string
    restoreRecordId?: number
    restoreRecordTaskUuid?: string
  },
) {
  void preloadFlowBackupSourceDetailDrawer().catch(() => undefined)
  flowSourceDetailMounted.value = true
  activeFlowSource.value = row
  const tab = opts?.tab ?? 'overview'
  if (tab === 'executions' || tab === 'restore') {
    flowSourceDetailTab.value = 'tasks'
    flowSourceDetailTaskSubTab.value = tab
  } else {
    flowSourceDetailTab.value = tab
    flowSourceDetailTaskSubTab.value = tab === 'tasks' ? (opts?.taskSubTab ?? 'history') : 'history'
  }
  flowSourceDetailScrollTo.value = opts?.scrollTo ?? null
  flowSourceDetailTaskUuid.value = opts?.taskUuid ?? ''
  flowSourceDetailRestoreRecordId.value = opts?.restoreRecordId ?? null
  flowSourceDetailRestoreRecordTaskUuid.value = opts?.restoreRecordTaskUuid ?? ''
  flowSourceDetailOpen.value = true
  nextTick(() => {
    requestAnimationFrame(() => updateDrawerWidth())
  })
}

function onFlowSourceCellClick(row: FlowSourceRow, column: { columnKey?: string | null }) {
  if (column.columnKey !== 'backup-source') return
  openFlowSourceDetail(row)
}

function onFlowSourceDetailClosed() {
  flowSourceDetailTab.value = 'overview'
  flowSourceDetailTaskSubTab.value = 'history'
  flowSourceDetailScrollTo.value = null
  flowSourceDetailTaskUuid.value = ''
  flowSourceDetailRestoreRecordId.value = null
  flowSourceDetailRestoreRecordTaskUuid.value = ''
}

watch(backupTaskDetailOpen, (open) => {
  if (!open) backupTaskDetailUuid.value = ''
})

function startBackupForSource(sourceId: string) {
  const row =
    activeFlowSource.value?.id === sourceId
      ? activeFlowSource.value
      : flowSourceRowById(sourceId) ?? flowRowFromSourceId(sourceId)
  if (!row) return
  step3SourceSelection.value = [row]
  startSelectedBackupTasks()
}

function openRecoveryWithBackupIds(backupIds: string[]) {
  if (!backupIds.length) {
    ElMessage.warning({ message: t('protection.backupsPage.msgPickBackupForRecovery'), grouping: true })
    return
  }
  void restoreTargetCatalog.ensureByIds(backupIds.map(backupSourceHostId).filter(Boolean))
  invalidateRecoverySnapshotLists(backupIds)
  recOpen.value = true
  ensureRecoveryNodes()
  recEntryStage.value = 'chooser'
  recEntryMode.value = 'plan'
  recStep.value = 0
  recBackupIds.value = backupIds
  recBackupId.value = backupIds[0] || ''
  recSnapshotMap.value = Object.fromEntries(
    backupIds
      .map((id) => [id, latestRecoverableBackupSnapshot(store.getBackup(id))?.id || ''])
      .filter(([, snapshotId]) => !!snapshotId),
  )
  recoverySnapshotDetails.value = new Map()
  recoverySnapshotBrowseRevision.value = 0
  recSnapshotId.value = recSnapshotMap.value[recBackupId.value] || ''
  recDirKeys.value = []
  recHostDirNameMap.value = {}
  recConflictPolicyMap.value = {}
  recDestActiveBackupId.value = backupIds[0] || ''
  recDestCheckedBackupIds.value = []
  recOriginalHostLockedBackupId.value = ''
  recDestEntriesByHost.value = buildInitialRecoveryDestEntriesByHost(backupIds)
  recDirSelectionsByHost.value = buildInitialRecoveryDirSelectionsByHost(backupIds)
  recExpandedRecDirHostIds.value = []
  recDirStepInitialized.value = false
  recExpandedRecConfirmHostIds.value = []
  recBatchHostDirPrefix.value = ''
}

function openRecoveryForSource(sourceId: string) {
  const backupIds = backupIdsForSource(sourceId)
  const row =
    activeFlowSource.value?.id === sourceId
      ? activeFlowSource.value
      : flowSourceRowById(sourceId) ?? flowRowFromSourceId(sourceId)
  if (row) step3SourceSelection.value = [row]
  openRecoveryWithBackupIds(backupIds)
}

async function openSnapshotRestore(payload: { snapshotId: number }) {
  await router.push({
    name: 'protection-snapshot-restore',
    params: { snapshotId: String(payload.snapshotId) },
  })
}

function onFlowSourceDetailViewAllRestore(sourceId: string) {
  const row =
    activeFlowSource.value?.id === sourceId
      ? activeFlowSource.value
      : flowSourceRowById(sourceId) ?? flowRowFromSourceId(sourceId)
  if (row) openRestoreTaskStatusDrawer(row)
}

function syncSelectionTable(
  tableRef: InstanceType<typeof ElTable> | null,
  rows: Array<{ id: string }>,
  selectedIds: string[],
  syncingFlag: 'source' | 'step2' | 'step3',
) {
  if (!tableRef) return
  const selected = new Set(selectedIds)
  if (syncingFlag === 'source') syncingSourceSelection = true
  else if (syncingFlag === 'step2') syncingStep2Selection = true
  else syncingStep3Selection = true
  tableRef.clearSelection()
  rows.forEach((row) => {
    if (selected.has(row.id)) tableRef.toggleRowSelection(row, true)
  })
  if (syncingFlag === 'source') syncingSourceSelection = false
  else if (syncingFlag === 'step2') syncingStep2Selection = false
  else syncingStep3Selection = false
}

function syncSourceTableSelection() {
  syncSelectionTable(sourceTableRef.value, backupSelectableRows.value, selectedSourceIds.value, 'source')
}

function syncStep2TableSelection() {
  syncSelectionTable(step2TableRef.value, step2PendingSourceList.value, step1Selection.value, 'step2')
}






function flowSourceRegisteredAt(value?: string) {
  return formatLocalDateTime(value, '—')
}

function flowSourceAvailabilityLabel(value?: FlowSourceRow['availability']) {
  return value === 'online'
    ? t('protection.sourceResources.nodeStatusOnline')
    : t('protection.sourceResources.nodeStatusOffline')
}

function flowSourceAvailabilityTagType(value?: FlowSourceRow['availability']): 'success' | 'danger' {
  return value === 'online' ? 'success' : 'danger'
}

const FLOW_SOURCE_BLOCKING_STATUSES = new Set([
  'upgrading', 'restarting', 'verifying', 'verification_pending',
  'removing', 'cleaning_up', 'probing',
])

function flowSourceCanOperate(row: FlowSourceRow) {
  return row.availability === 'online' && !FLOW_SOURCE_BLOCKING_STATUSES.has(row.status)
}

function onSourceSelectionChange(rows: Array<{ id: string }>) {
  if (syncingSourceSelection) return
  selectedSourceIds.value = rows.map((row) => row.id)
}

function onStep2SelectionChange(rows: Array<{ id: string }>) {
  if (syncingStep2Selection) return
  step1Selection.value = rows.map((row) => row.id)
}

async function syncStep2SourcesFromSelection(picked: string[]) {
  rememberSelectableRows(
    backupSelectableRows.value.filter((row) => picked.includes(row.id)),
  )
  if (picked.length) {
    await setPipelineStep(picked, 2)
    syncWizardCountsFromPipeline()
    await refreshBackupSourcePoolCount()
  }
  selectedSourceIds.value = []
  step1Selection.value = []
}

async function enterBackupConfigStep(requireSelection = false) {
  if (flowAdvancingToBackupConfig.value) return
  const picked = selectedSourceIds.value.filter(isBackupSelectableId)
  if (requireSelection && flowMainStep.value === 0 && picked.length === 0) {
    ElMessage.warning({ message: t('protection.backupsPage.msgSelectSourceFirst'), grouping: true })
    return
  }

  flowAdvancingToBackupConfig.value = true
  try {
    if (requireSelection && flowMainStep.value === 0) {
      await syncStep2SourcesFromSelection(picked)
    }
    flowMainStep.value = 1
    syncFlowStepRoute(1)
  } catch (err) {
    showApiError(err)
  } finally {
    flowAdvancingToBackupConfig.value = false
  }
}

function onGoToStep2() {
  void enterBackupConfigStep(true)
}

async function onGoToCreateBackup() {
  if (step1Selection.value.length === 0) {
    ElMessage.warning({ message: t('protection.backupsPage.msgSelectSourceFirst'), grouping: true })
    return
  }
  if (setupDrOpening.value || setupDrCreateOpen.value) return
  const offlineRows = offlineFlowSourcesFromIds(step1Selection.value)
  if (offlineRows.length > 0) {
    ElMessage.warning({ message: formatOfflineBackupPlanMessage(offlineRows, t), grouping: true })
    return
  }
  void preloadBackupCreateWizard().catch(() => undefined)
  setupDrCreateSources.value = [...step1Selection.value]
  setupDrEditConfigIds.value = []
  setupDrEditSection.value = undefined
  setupDrOpening.value = true
  setupDrCreateOpen.value = true
}

function enterSourceStep() {
  flowMainStep.value = 0
  syncFlowStepRoute(0)
}

function enterStartBackupStep(opts?: { requireReady?: boolean; focusIds?: string[] | null; syncRoute?: boolean; refresh?: boolean }) {
  if (opts?.focusIds !== undefined && opts.focusIds !== null) {
    const focusIds = opts.focusIds.filter((id) => sourceHasBackupConfig(id))
    step3FocusSourceIds.value = focusIds.length > 0 ? focusIds : null
  } else {
    step3FocusSourceIds.value = null
  }

  const alreadyOnStep3 = flowMainStep.value === 2
  flowMainStep.value = 2
  if (opts?.syncRoute !== false) syncFlowStepRoute(2)
  if (alreadyOnStep3 && !flowBootstrapping.value && opts?.refresh !== false) {
    void refreshFlowStepData(2)
  }
}

const lastCreatedSourceIds = ref<string[]>([])
const setupDrCreateOpen = ref(false)
const setupDrOpening = ref(false)
const setupDrCreateSources = ref<string[]>([])
type SetupDrEditSection = 'paths' | 'policy' | 'recovery-plan'
const setupDrEditConfigIds = ref<number[]>([])
const setupDrEditSection = ref<SetupDrEditSection | undefined>()

function closeCreate() {
  setupDrCreateOpen.value = false
  setupDrOpening.value = false
  setupDrCreateSources.value = []
  setupDrEditConfigIds.value = []
  setupDrEditSection.value = undefined
}

async function loadStep3SelectableWithPageClamp(signal?: AbortSignal) {
  await loadStep3Selectable({ signal })
  const maxPage = Math.max(1, Math.ceil(step3SelectableCount.value / flowStep2Pager.pageSize) || 1)
  if (flowStep2Pager.page <= maxPage) return
  flowStep2Pager.page = maxPage
  await loadStep3Selectable({ signal })
}

function latestStep3SourceRow(sourceId: string) {
  return step3SelectableRows.value.find((row) => row.id === sourceId)
    ?? backupSelectableById.value.get(sourceId)
    ?? null
}

async function refreshStep3AfterMoreAction(options: {
  focusIds?: string[]
  preserveSelection?: boolean
  closeMissingDetail?: boolean
} = {}) {
  const scope = flowStepScope(2)
  const signal = pageRequests.nextSignal(scope)
  const refreshSeq = ++step3ActionRefreshSeq
  const activeSourceId = activeFlowSource.value?.id || ''
  const selectedSourceIds = options.preserveSelection === false
    ? []
    : step3SourceSelection.value.map((row) => row.id)
  const showLoading = flowMainStep.value === 2
  step3ActionRefreshInFlight = true
  if (showLoading) setFlowStepDataLoading(2, true)
  try {
    await refreshPipelineStep2PlusIds(signal)
    if (!pageRequests.isCurrentSignal(scope, signal)) return []
    await loadStep3SelectableWithPageClamp(signal)
    if (!pageRequests.isCurrentSignal(scope, signal)) return []
    await refreshBackupConfigs(signal)
    if (!pageRequests.isCurrentSignal(scope, signal)) return []

    if (activeSourceId) {
      const latestActiveSource = latestStep3SourceRow(activeSourceId)
      if (latestActiveSource && sourceHasBackupConfig(activeSourceId)) {
        activeFlowSource.value = latestActiveSource
      } else if (options.closeMissingDetail !== false && flowSourceDetailOpen.value) {
        flowSourceDetailOpen.value = false
        onFlowSourceDetailClosed()
      }
    }

    if (options.preserveSelection === false) {
      step3SourceSelection.value = []
    } else {
      step3SourceSelection.value = selectedSourceIds
        .map((id) => latestStep3SourceRow(id))
        .filter((row): row is FlowSourceRow => Boolean(row && sourceHasBackupConfig(row.id)))
    }
    await nextTick()
    syncStep3TableSelection()
    layoutFlowTables()
    const requestedFocusIds = normalizeSourceIdList(options.focusIds || [])
    return requestedFocusIds.filter((id) => sourceHasBackupConfig(id))
  } catch (err) {
    if (pageRequests.isAbortError(err)) return []
    throw err
  } finally {
    pageRequests.releaseSignal(scope, signal)
    if (showLoading) setFlowStepDataLoading(2, false)
    if (refreshSeq === step3ActionRefreshSeq) step3ActionRefreshInFlight = false
  }
}

async function finishCreateAndGoToStep3(sourceIds?: string[]) {
  const requestedFocusIds = sourceIds ?? lastCreatedSourceIds.value
  closeCreate()
  const focusIds = await refreshStep3AfterMoreAction({ focusIds: requestedFocusIds })
  if (focusIds.length > 0) {
    const idSet = new Set(focusIds)
    step1Selection.value = step1Selection.value.filter((id) => !idSet.has(id))
  }
  nextTick(() => {
    if (focusIds.length > 0) {
      enterStartBackupStep({ focusIds, refresh: false })
      return
    }
    enterStartBackupStep({ refresh: false })
  })
}


const addSourceOpen = ref(false)
const addSourceShellRef = ref<HTMLElement | null>(null)
type AddSourceType = 'hostFileSystem' | 'nas'
const addSourceType = ref<AddSourceType>('hostFileSystem')
const proxyNodes = ref<ApiNode[]>([])
const proxyNodesRefreshing = ref(false)
const recoveryNodeRows = ref<ApiNode[]>([])
const recoveryNodeLoading = ref(false)
const recoveryNodeLoaded = ref(false)
const restoreTargetCatalog = useRestoreTargetCatalog()
const recoveryTargetHostRows = computed(() => restoreTargetCatalog.rows.value.map(mapBackupSelectableToFlowRow))
const recoveryTargetHostLoading = restoreTargetCatalog.loading
const recoveryTargetHostLoadingMore = restoreTargetCatalog.loadingMore
const recoveryTargetHostError = restoreTargetCatalog.error
watch(restoreTargetCatalog.allRecords, (records) => {
  rememberSelectableRows(records.map(mapBackupSelectableToFlowRow))
}, { flush: 'sync' })
const inlineEditorMode = computed<'recovery' | null>(() => {
  if (recOpen.value) return 'recovery'
  return null
})

async function loadProxyNodes() {
  const signal = pageRequests.nextSignal('proxy-node-options')
  try {
    proxyNodes.value = await listNodes({ role: 'proxy' }, { signal })
  } catch (e) {
    if (pageRequests.isAbortError(e)) return
    proxyNodes.value = []
  } finally {
    pageRequests.releaseSignal('proxy-node-options', signal)
  }
}

async function loadRecoveryNodes() {
  const signal = pageRequests.nextSignal('recovery-node-options')
  recoveryNodeLoading.value = true
  try {
    recoveryNodeRows.value = (await listNodes(undefined, { signal })).filter((node) => node.role === 'agent' || node.role === 'proxy')
    recoveryNodeLoaded.value = true
  } catch (e) {
    if (pageRequests.isAbortError(e)) return
    recoveryNodeRows.value = []
    recoveryNodeLoaded.value = true
  } finally {
    pageRequests.releaseSignal('recovery-node-options', signal)
    if (!signal.aborted) recoveryNodeLoading.value = false
  }
}

function ensureRecoveryNodes() {
  if (recoveryNodeLoaded.value || recoveryNodeLoading.value) return
  void loadRecoveryNodes()
}

async function loadRecoveryTargetHostOptions(opts: { reset?: boolean } = {}) {
  if (opts.reset === true) await restoreTargetCatalog.reset()
  else await restoreTargetCatalog.loadMore()
  rememberSelectableRows(restoreTargetCatalog.allRecords.value.map(mapBackupSelectableToFlowRow))
}

function searchRecoveryTargetHostOptions(query: string) {
  restoreTargetCatalog.setSearch(query)
}

function onRecoveryTargetNodeSelectVisible(visible: boolean) {
  if (!visible) return
  if (!recoveryTargetHostRows.value.length) void loadRecoveryTargetHostOptions({ reset: true })
}

function onRecoveryTargetNodePopupScroll(event: Event | { scrollTop?: number; clientHeight?: number; scrollHeight?: number }) {
  const target = (event instanceof Event ? event.target : event) as HTMLElement | null
  if (!target) return
  const remaining = target.scrollHeight - target.scrollTop - target.clientHeight
  if (remaining <= 48) void restoreTargetCatalog.loadMore()
}

async function refreshProxyNodesManually() {
  proxyNodesRefreshing.value = true
  try {
    await loadProxyNodes()
    ElMessage.success({ message: t('protection.sourceResources.proxyRefreshSuccess'), grouping: true })
  } finally {
    proxyNodesRefreshing.value = false
  }
}

const deploySelectedOs = ref<EnrollmentOs>('linux')
const deployScript = ref('')
const deployScriptLoading = ref(false)
const deployScriptCache: Partial<Record<EnrollmentOs, string>> = {}
let deployGeneration = 0




function clearDeployScriptCache() {
  delete deployScriptCache.linux
  delete deployScriptCache.windows
  delete deployScriptCache.macos
}

async function refreshDeployScript(generation: number, os: EnrollmentOs) {
  const cached = deployScriptCache[os]
  if (cached) {
    if (generation === deployGeneration && deploySelectedOs.value === os) {
      deployScript.value = cached
      deployScriptLoading.value = false
    }
    return
  }

  deployScriptLoading.value = true
  try {
    const { command } = await issueEnrollmentInstall({
      role: 'agent',
      os,
      note: 'deploy:agent:source-host',
    })
    if (generation !== deployGeneration) return
    deployScriptCache[os] = command
    if (deploySelectedOs.value !== os) return
    deployScript.value = command
  } catch (e) {
    if (generation === deployGeneration && deploySelectedOs.value === os) {
      showApiError(e, t('nodesDeploy.scriptLoadFailed'))
    }
  } finally {
    if (generation === deployGeneration && deploySelectedOs.value === os) {
      deployScriptLoading.value = false
    }
  }
}

function startDeploySession() {
  const generation = ++deployGeneration
  const os = deploySelectedOs.value
  const cached = deployScriptCache[os]
  if (cached) {
    deployScript.value = cached
    deployScriptLoading.value = false
    return
  }
  void refreshDeployScript(generation, os)
}

function resetNasForm() {
  nasProtocol.value = 'smb'
  nasBindNodeId.value = undefined
  nasBindNodeError.value = ''
  nasName.value = ''
  nasNameTouched.value = false
  nasDir.value = ''
  nasDirTouched.value = false
  nasSmbServer.value = ''
  nasSmbShare.value = ''
  nasSmbUsername.value = ''
  nasSmbPassword.value = ''
  nasSmbDomain.value = ''
  nasNfsHost.value = ''
  nasNfsExport.value = ''
  nasNfsOptions.value = defaultNasMountOptions('smb')
}

function onAddBackupSource() {
  addSourceType.value = 'hostFileSystem'
  deploySelectedOs.value = 'linux'
  clearDeployScriptCache()
  deployScript.value = ''
  deployScriptLoading.value = false
  resetNasForm()
  addSourceOpen.value = true
  void loadProxyNodes()
  startDeploySession()
  void nextTick(() => addSourceShellRef.value?.focus())
}

function closeAddSource() {
  addSourceOpen.value = false
}

async function refreshAfterAddSourceClose() {
  try {
    await Promise.all([
      loadBackupSelectable(),
      refreshPipelineStep2PlusIds(),
      refreshPipelineStep3Ids(),
      refreshBackupConfigs(),
    ])
    await refreshSelectableCatalog([
      ...step2AvailableSourceIds.value,
      ...step3ConfiguredSourceIds.value,
    ])
    await nextTick()
    if (flowMainStep.value === 0) {
      syncSourceTableSelection()
    } else if (flowMainStep.value === 1) {
      syncStep2TableSelection()
    } else {
      syncStep3TableSelection()
    }
    layoutFlowTables()
  } catch (err) {
    showApiError(err)
  }
}

const nasBusy = ref(false)
type NasProtocol = 'smb' | 'nfs'
const nasProtocol = ref<NasProtocol>('smb')
const nasBindNodeId = ref<number | undefined>(undefined)
const nasBindNodeError = ref('')
const nasBindSectionRef = ref<HTMLElement | null>(null)
const nasName = ref('')
const nasNameTouched = ref(false)
const nasDir = ref('')
const nasDirTouched = ref(false)
/* SMB */
const nasSmbServer = ref('')
const nasSmbShare = ref('')
const nasSmbUsername = ref('')
const nasSmbPassword = ref('')
const nasSmbDomain = ref('')
/* NFS */
const nasNfsHost = ref('')
const nasNfsExport = ref('')
const nasNfsOptions = ref(defaultNasMountOptions('smb'))

const generatedNasDir = computed(() =>
  buildGeneratedNasMountDir({
    protocol: nasProtocol.value,
    smbServer: nasSmbServer.value,
    smbShare: nasSmbShare.value,
    nfsHost: nasNfsHost.value,
    nfsExport: nasNfsExport.value,
  }),
)

const generatedNasName = computed(() =>
  buildGeneratedNasName({
    protocol: nasProtocol.value,
    smbServer: nasSmbServer.value,
    smbShare: nasSmbShare.value,
    nfsHost: nasNfsHost.value,
    nfsExport: nasNfsExport.value,
  }),
)

function clearNasBindNodeError() {
  nasBindNodeError.value = ''
}

function validateNasBindNode(): boolean {
  clearNasBindNodeError()
  if (proxyNodes.value.length === 0) {
    nasBindNodeError.value = t('protection.sourceResources.nasNoProxy')
    ElMessage.warning({ message: t('protection.sourceResources.nasNoProxy'), grouping: true })
    nasBindSectionRef.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    return false
  }
  if (nasBindNodeId.value == null) {
    nasBindNodeError.value = t('protection.sourceResources.errNasProxyRequired')
    ElMessage.warning({ message: t('protection.sourceResources.errNasProxyRequired'), grouping: true })
    nasBindSectionRef.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    return false
  }
  return true
}

type NasWizardStep = 0 | 1 | 2

function validateNasStep(step: NasWizardStep): boolean {
  if (step === 0) {
    if (!nasProtocol.value) {
      ElMessage.warning({ message: t('repositoriesPage.errProtocol'), grouping: true })
      return false
    }
    if (nasProtocol.value === 'smb') {
      if (!nasSmbServer.value.trim()) { ElMessage.warning({ message: t('addNasRepo.errSmbHost'), grouping: true }); return false }
      if (!nasSmbShare.value.trim()) { ElMessage.warning({ message: t('addNasRepo.errSmbShare'), grouping: true }); return false }
      if (!nasSmbUsername.value.trim()) { ElMessage.warning({ message: t('repositoriesPage.errSmbUsername'), grouping: true }); return false }
      if (!nasSmbPassword.value.trim()) { ElMessage.warning({ message: t('repositoriesPage.errSmbPassword'), grouping: true }); return false }
    } else if (nasProtocol.value === 'nfs') {
      if (!nasNfsHost.value.trim()) { ElMessage.warning({ message: t('repositoriesPage.errNfsHost'), grouping: true }); return false }
      if (!nasNfsExport.value.trim()) { ElMessage.warning({ message: t('repositoriesPage.errNfsExport'), grouping: true }); return false }
    }
    return true
  }
  if (step === 1) {
    return validateNasBindNode()
  }
  if (step === 2) {
    if (!nasName.value.trim()) { ElMessage.warning({ message: t('repositoriesPage.errName'), grouping: true }); return false }
    if (!nasDir.value.trim()) { ElMessage.warning({ message: t('repositoriesPage.errRepoDir'), grouping: true }); return false }
    return true
  }
  return false
}

function validateNasForm(): boolean {
  return validateNasStep(0) && validateNasStep(1) && validateNasStep(2)
}

async function nasSubmit() {
  nasName.value = resolveNasSubmitName(nasName.value, generatedNasName.value, nasNameTouched.value)
  if (!nasDir.value.trim() || !nasDirTouched.value) {
    nasDir.value = generatedNasDir.value
  }
  if (!validateNasForm()) return
  nasBusy.value = true
  try {
    const payload = buildNasSourceCreatePayload({
      name: nasName.value.trim(),
      protocol: nasProtocol.value,
      mountPath: nasDir.value.trim(),
      boundNodeId: nasBindNodeId.value ?? null,
      smb: nasProtocol.value === 'smb'
        ? {
            server: nasSmbServer.value,
            share: nasSmbShare.value,
            username: nasSmbUsername.value,
            password: nasSmbPassword.value,
            domain: nasSmbDomain.value,
            options: nasNfsOptions.value,
          }
        : undefined,
      nfs: nasProtocol.value === 'nfs'
        ? {
            server: nasNfsHost.value,
            exportPath: nasNfsExport.value,
            options: nasNfsOptions.value,
          }
        : undefined,
    })
    await preflightSourceNasCreate(payload)
    await createSourceResource(payload)
    ElMessage.success({ message: t('protection.sourceResources.nasCreated'), grouping: true })
    addSourceOpen.value = false
  } catch (e) {
    const proxyName = proxyNodes.value.find((node) => node.id === nasBindNodeId.value)?.name || ''
    if (await showNasDraftPreflightGuidance(e, t, proxyName)) return
    ElMessage.error({
      message: apiErrorMessage(e, t('protection.sourceResources.nasCreateFailed')),
      grouping: true,
    })
  } finally {
    nasBusy.value = false
  }
}

watch(nasBindNodeId, () => {
  clearNasBindNodeError()
})

watch(nasProtocol, (protocol) => {
  nasSmbServer.value = ''
  nasSmbShare.value = ''
  nasSmbUsername.value = ''
  nasSmbPassword.value = ''
  nasSmbDomain.value = ''
  nasNfsHost.value = ''
  nasNfsExport.value = ''
  nasNfsOptions.value = defaultNasMountOptions(protocol)
  nasDirTouched.value = false
  nasNameTouched.value = false
})

watch(generatedNasDir, (value) => {
  if (!nasDirTouched.value || !nasDir.value.trim()) {
    nasDir.value = value
  }
})

watch(generatedNasName, (value) => {
  if (!nasNameTouched.value || !nasName.value.trim()) {
    nasName.value = value
  }
})

watch(addSourceOpen, (open, wasOpen) => {
  if (typeof document === 'undefined') return
  document.body.style.overflow = open ? 'hidden' : ''
  if (open) {
    void nextTick(() => addSourceShellRef.value?.focus())
    return
  }
  if (wasOpen) {
    void refreshAfterAddSourceClose()
  }
})

watch(addSourceType, (type) => {
  if (!addSourceOpen.value) return
  if (type === 'hostFileSystem') {
    startDeploySession()
    return
  }
  resetNasForm()
})

watch(deploySelectedOs, (os) => {
  if (!addSourceOpen.value || addSourceType.value !== 'hostFileSystem') return
  const cached = deployScriptCache[os]
  if (cached) {
    deployScript.value = cached
    deployScriptLoading.value = false
    return
  }
  const generation = ++deployGeneration
  void refreshDeployScript(generation, os)
})

watch(step2PipelineSourceIds, () => {
  if (!flowBootstrapping.value && flowMainStep.value === 0 && pipelineReady.value) void loadBackupSelectable()
})

watch(selectedSourceIds, () => {
  nextTick(() => syncSourceTableSelection())
})

watch(step1Selection, () => {
  nextTick(() => syncStep2TableSelection())
})

watch(backupSelectableRows, (list) => {
  const visibleIds = new Set(list.map((row) => row.id))
  const nextSelectedIds = selectedSourceIds.value.filter((id) => visibleIds.has(id))
  if (!sameIdList(nextSelectedIds, selectedSourceIds.value)) {
    selectedSourceIds.value = nextSelectedIds
    return
  }
  nextTick(() => syncSourceTableSelection())
}, { flush: 'post' })

watch(step2PendingSourceList, (list) => {
  const visibleIds = new Set([
    ...list.map((row) => row.id),
    ...step2AvailableSourceIds.value,
  ])
  const nextSelectedIds = step1Selection.value.filter((id) => visibleIds.has(id))
  if (!sameIdList(nextSelectedIds, step1Selection.value)) {
    step1Selection.value = nextSelectedIds
    return
  }
  nextTick(() => syncStep2TableSelection())
}, { flush: 'post' })

watch(step3SourceList, (list) => {
  const visibleIds = new Set(list.map((row) => row.id))
  const nextSelected = step3SourceSelection.value.filter((row) => visibleIds.has(row.id))
  if (nextSelected.length !== step3SourceSelection.value.length) {
    step3SourceSelection.value = nextSelected
    return
  }
  nextTick(() => syncStep3TableSelection())
}, { flush: 'post' })

watch(flowMainStep, (step) => {
  cancelOtherFlowStepRequests(step)
  if (step === 1) scheduleInteractivePreload(preloadBackupCreateWizard)
  if (step === 2) scheduleInteractivePreload(preloadFlowBackupSourceDetailDrawer)
  nextTick(() => {
    updateFlowTableMaxHeight()
    if (!flowBootstrapping.value) {
      void refreshFlowStepData(step)
    }
    if (step === 0) syncSourceTableSelection()
    if (step === 1) syncStep2TableSelection()
    if (step === 2) syncStep3TableSelection()
    layoutFlowTables()
  })
})

watch(
  () => route.query.step,
  (step) => {
    applyRequestedFlowStep(step)
  },
)

onMounted(async () => {
  lifecycleOps.restorePersisted()
  scheduleInteractivePreload(preloadFlowBackupSourceDetailDrawer)
  flowBootstrapping.value = true
  try {
    await bootstrapPipeline()
    syncWizardCountsFromPipeline()
    if (isFixedSnapshotRestore.value) {
      await refreshBackupConfigs()
      await initializeFixedSnapshotRestore()
    } else {
      await refreshBackupSourcePoolCount()
      if (!applyRequestedFlowStep(route.query.step)) {
        const storedStep = consumeStoredFlowReturnStep()
        applyFlowStep(storedStep)
        if (storedStep !== null) syncFlowStepRoute(storedStep, 'replace')
      }
      await refreshFlowStepData(flowMainStep.value)
      if (flowMainStep.value === 1) scheduleInteractivePreload(preloadBackupCreateWizard)
      if (flowMainStep.value === 2) scheduleInteractivePreload(preloadFlowBackupSourceDetailDrawer)
    }
  } catch (err) {
    if (!pageRequests.isAbortError(err)) throw err
  } finally {
    flowBootstrapping.value = false
  }
  resumePendingUnregisterMonitors()
  nextTick(() => {
    updateFlowTableMaxHeight()
    const toolbar = flowToolbarRef.value
    if (typeof ResizeObserver !== 'undefined' && toolbar) {
      flowTableZoneObserver = new ResizeObserver(() => {
        scheduleFlowTableMaxHeightUpdate()
      })
      flowTableZoneObserver.observe(toolbar)
    }
    window.addEventListener('resize', scheduleFlowTableMaxHeightUpdate)
    window.addEventListener('resize', updateDrawerWidth)
    layoutFlowTables()
  })
})

onUnmounted(() => {
  stopStep3AutoRefresh()
  stopUnregisterTaskPolls()
  cancelFlowStepRequests(0)
  cancelFlowStepRequests(1)
  cancelFlowStepRequests(2)
  if (taskSearchDebounceTimer) {
    clearTimeout(taskSearchDebounceTimer)
    taskSearchDebounceTimer = null
  }
  flowTableZoneObserver?.disconnect()
  flowTableZoneObserver = null
  window.removeEventListener('resize', scheduleFlowTableMaxHeightUpdate)
  window.removeEventListener('resize', updateDrawerWidth)
  if (flowTableResizeFrame) {
    window.cancelAnimationFrame(flowTableResizeFrame)
    flowTableResizeFrame = 0
  }
  if (typeof document !== 'undefined') document.body.style.overflow = ''
})

watch(flowTableMaxHeight, () => layoutFlowTables())

function openProxyDeploy() {
  const { href } = router.resolve(PROXY_DEPLOY_ROUTE)
  window.open(href, '_blank', 'noopener,noreferrer')
}

async function copyDeployScript(text?: string) {
  const script = text || deployScriptCache[deploySelectedOs.value] || deployScript.value
  if (!script) {
    ElMessage.warning({ message: t('nodesDeploy.scriptNotReady'), grouping: true })
    return
  }
  try {
    await copyTextToClipboard(script)
    ElMessage.success({ message: t('nodesDeploy.copied'), grouping: true })
  } catch {
    ElMessage.error({ message: t('nodesDeploy.copyFailed'), grouping: true })
  }
}

type DemoFlowTaskStatus = 'running' | 'completed' | 'failed'

const backupFlowTasks = ref<DemoFlowTask[]>([])
const restoreFlowTasks = ref<DemoFlowTask[]>([])

const {
  aggregateForSource,
  backupsForSource,
  backupIdsForSource,
  flowTaskStatusLabel,
} = useFlowSourceAggregate(backupFlowTasks, restoreFlowTasks)

const restoreTaskDrawerOpen = ref(false)
const restoreDrawerSourceId = ref<string | null>(null)
const restoreTaskPager = reactive({ page: 1, pageSize: 5 })

const drawerSourceRow = computed(() => {
  const id = restoreDrawerSourceId.value
  return id ? flowSourceRowById(id) ?? flowRowFromSourceId(id) : null
})

const restoreDrawerSourceName = computed(() => drawerSourceRow.value?.name ?? t('protection.backupDetail.durationDash'))





function sourceBackupConfigs(sourceId: string) {
  const [sourceType, rawRefId] = sourceId.split(':')
  const sourceRefId = Number(rawRefId)
  return backupConfigRows.value
    .filter((config) => config.source_type === sourceType && Number(config.source_ref_id) === sourceRefId)
    .map((config) => backupConfigDetailById.value.get(config.id) ?? config)
}

function sourceBackupConfigIds(sourceId: string) {
  return sourceBackupConfigs(sourceId).map((config) => config.id)
}

function compressionLevelLabel(level: CompressionLevel) {
  if (level === 'none') return t('protection.backupsPage.compressionNoneTitle')
  if (level === 'high') return t('protection.backupsPage.compressionHighTitle')
  return t('protection.backupsPage.compressionBalancedTitle')
}

function compressionLevelIcon(level: CompressionLevel) {
  if (level === 'none') return CircleOff
  if (level === 'high') return Archive
  return Scale
}

function compressionLevelTooltip(level: CompressionLevel) {
  if (level === 'none') return t('protection.backupsPage.compressionNoneTooltip')
  if (level === 'high') return t('protection.backupsPage.compressionHighTooltip')
  return t('protection.backupsPage.compressionBalancedTooltip')
}

function sourceCompressionLevel(sourceId: string) {
  return sourceBackupConfigs(sourceId)[0]?.compression_level
}

function sourceCompressionLabel(sourceId: string) {
  const level = sourceCompressionLevel(sourceId)
  return level ? compressionLevelLabel(level) : ''
}

function startBackupTaskPayloadForSources(sources: FlowSourceRow[]) {
  return {
    source_ids: sources.map((source) => source.id),
    trigger_type: 'manual' as const,
  }
}

type BackupConfigEditSection = 'paths' | 'policy' | 'recovery'

const backupConfigEditPickOpen = ref(false)
const backupConfigEditPickCandidates = ref<Array<BackupConfig | BackupConfigDetail>>([])
const backupConfigEditPickSection = ref<BackupConfigEditSection>('paths')

function backupConfigEditSectionStep(section: BackupConfigEditSection) {
  if (section === 'policy') return 'policy'
  if (section === 'recovery') return 'recovery-plan'
  return 'paths'
}

function openBackupConfigEdit(configs: Array<BackupConfig | BackupConfigDetail>, section: BackupConfigEditSection) {
  if (setupDrOpening.value || setupDrCreateOpen.value) return
  const ids = configs
    .map((config) => Number(config.id))
    .filter((id) => Number.isFinite(id) && id > 0)
  if (!ids.length) return
  void preloadBackupCreateWizard().catch(() => undefined)
  setupDrCreateSources.value = []
  setupDrEditConfigIds.value = [...new Set(ids)]
  setupDrEditSection.value = backupConfigEditSectionStep(section)
  setupDrOpening.value = true
  setupDrCreateOpen.value = true
}

function openBackupConfigEditFromStep3(section: BackupConfigEditSection) {
  const sources = step3SourceSelection.value
  if (!sources.length) {
    ElMessage.warning({ message: t('protection.backupsPage.msgSelectConfiguredSourceForStep3'), grouping: true })
    return
  }
  if (sources.some((source) => sourceResetRunning(source.id))) {
    ElMessage.warning({ message: t('protection.backupsPage.msgResetBackupConfigRunning'), grouping: true })
    return
  }
  const configs = sources.flatMap((source) => sourceBackupConfigs(source.id))
  if (!configs.length) {
    ElMessage.warning({ message: t('protection.backupsPage.msgSourceNoBackupConfig'), grouping: true })
    return
  }
  openBackupConfigEdit(configs, section)
}

function onBackupConfigEditPickSelected(config: BackupConfig | BackupConfigDetail) {
  backupConfigEditPickOpen.value = false
  openBackupConfigEdit([config], backupConfigEditPickSection.value)
}

async function openConfigureRestorePlanFromMissingRow(row: { backupId: string }) {
  const configId = realConfigIdFromBackupId(row.backupId)
  if (!configId) {
    ElMessage.warning({ message: t('protection.backupsPage.msgSourceNoBackupConfig'), grouping: true })
    return
  }
  let config: BackupConfig | BackupConfigDetail | undefined = backupConfigDetailById.value.get(configId)
  if (!config) {
    try {
      config = await getBackupConfig(configId)
      backupConfigDetailById.value.set(config.id, config)
    } catch (e) {
      showApiError(e)
      return
    }
  }
  await closeRecoveryWizard()
  if (recOpen.value) return
  await nextTick()
  openBackupConfigEdit([config], 'recovery')
}

function taskPayload(task: TaskRow): Record<string, unknown> {
  return task.request_payload && typeof task.request_payload === 'object'
    ? task.request_payload as Record<string, unknown>
    : {}
}

function tasksForBackupConfigIds(configIds: number[]) {
  const idSet = new Set(configIds)
  return backupTaskRows.value.filter((task) => {
    const backupConfigId = Number(taskPayload(task).backup_config_id || 0)
    return backupConfigId > 0 && idSet.has(backupConfigId)
  })
}

function snapshotsForBackupConfigIds(configIds: number[]) {
  const idSet = new Set(configIds)
  return backupSnapshotRows.value.filter((snapshot) => idSet.has(Number(snapshot.backup_config_id || 0)))
}

function runtimeForSource(sourceId: string) {
  return recordValue(flowSourceRowById(sourceId)?.runtime)
}

function runtimeSection(sourceId: string, key: 'backup' | 'restore') {
  return recordValue(runtimeForSource(sourceId)[key])
}

function runtimeBool(value: unknown) {
  return value === true
}

function runtimeNumber(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) ? number : 0
}

function runtimeFailed(runtime: Record<string, unknown>) {
  const latestStatus = String(recordValue(runtime.latest_task).status || '').toLowerCase()
  if (latestStatus) return latestStatus === 'failed' || latestStatus === 'timeout'
  if (runtimeBool(runtime.running)) return false
  return runtimeBool(runtime.failed)
}

function latestSnapshotTimeForSource(sourceId: string) {
  const runtimeSnapshot = recordValue(runtimeForSource(sourceId).latest_snapshot)
  const runtimeTime = String(runtimeSnapshot.finished_at || runtimeSnapshot.started_at || runtimeSnapshot.created_at || '')
  if (runtimeTime) return runtimeTime
  const snapshots = snapshotsForBackupConfigIds(sourceBackupConfigIds(sourceId))
  let latest = ''
  for (const snapshot of snapshots) {
    const raw = snapshot.finished_at || snapshot.started_at || snapshot.created_at
    if (!raw) continue
    if (!latest || new Date(raw).getTime() > new Date(latest).getTime()) latest = raw
  }
  return latest
}

function withExecutionState(
  transfer: TransferProgress | null,
  executionState?: string | null,
): TransferProgress | null {
  if (!transfer) return null
  const state = String(executionState || '').trim()
  if (!state) return transfer
  return { ...transfer, execution_state: state }
}

function sourceBackupRuntime(sourceId: string) {
  const runtime = runtimeSection(sourceId, 'backup')
  const executionState = String(runtime.execution_state || '').trim() || null
  const transferProgress = withExecutionState(
    isTransferProgress(runtime.transfer_progress)
      ? (runtime.transfer_progress as TransferProgress)
      : null,
    executionState,
  )
  if (Object.keys(runtime).length) {
    return {
      running: runtimeBool(runtime.running),
      failed: runtimeFailed(runtime),
      progress: runtimeNumber(runtime.progress),
      transferProgress,
      executionState,
    }
  }
  const tasks = tasksForBackupConfigIds(sourceBackupConfigIds(sourceId))
  const running = tasks.filter((task) => task.status === 'running' || task.status === 'pending')
  if (running.length) {
    const progress = Math.max(...running.map((task) => Number(task.progress || 0)))
    const primary = running[0]
    const fromTask = isTransferProgress(primary.transfer_progress) ? primary.transfer_progress : null
    const fromPayload = primary.result_payload && typeof primary.result_payload === 'object' && !Array.isArray(primary.result_payload)
      ? (primary.result_payload as Record<string, unknown>).transfer_progress
      : null
    const transferFromTask = fromTask || (isTransferProgress(fromPayload) ? fromPayload : null)
    return {
      running: true,
      failed: false,
      progress: Math.min(100, Math.max(0, Number.isFinite(progress) ? progress : 0)),
      transferProgress: transferFromTask ?? {
        label_key: 'protection.taskProgress.backup.estimating',
        phase: 'estimating',
        show_metrics: false,
      },
    }
  }
  return {
    running: false,
    failed: tasks.some((task) => task.status === 'failed' || task.status === 'timeout'),
    progress: tasks.length && tasks.every((task) => task.status === 'success') ? 100 : 0,
    transferProgress: null,
  }
}

function sourceResetConfigs(sourceId: string) {
  return sourceBackupConfigs(sourceId).filter((config) => {
    const status = String(config.status || '').toLowerCase()
    return status === 'resetting' || status === 'reset_failed' || Boolean(config.reset_task_uuid)
  })
}

function taskIsActive(task?: TaskRow | null) {
  return task?.status === 'pending' || task?.status === 'running'
}

function resetTaskForSource(sourceId: string) {
  const endpoint = parseEndpointUiId(sourceId)
  const uuids = sourceResetConfigs(sourceId)
    .map((config) => String(config.reset_task_uuid || ''))
    .filter(Boolean)
  const uuidMatch = resetTaskRows.value.find((task) => uuids.includes(task.task_uuid))
  if (uuidMatch) return uuidMatch
  if (!endpoint) return null
  return resetTaskRows.value.find((task) => {
    const payload = taskPayload(task)
    return String(payload.source_type || '') === endpoint.type
      && Number(payload.source_ref_id || 0) === endpoint.refId
  }) ?? null
}

function sourceResetState(sourceId: string): 'resetting' | 'reset_failed' | '' {
  const task = resetTaskForSource(sourceId)
  if (taskIsActive(task)) return 'resetting'
  const configs = sourceResetConfigs(sourceId)
  if (configs.some((config) => String(config.status || '').toLowerCase() === 'resetting')) return 'resetting'
  if (configs.some((config) => String(config.status || '').toLowerCase() === 'reset_failed')) return 'reset_failed'
  if (task?.status === 'failed' || task?.status === 'timeout') return 'reset_failed'
  return ''
}

function sourceResetRunning(sourceId: string) {
  return sourceResetState(sourceId) === 'resetting'
}

function sourceResetProgress(sourceId: string) {
  const progress = Number(resetTaskForSource(sourceId)?.progress ?? 0)
  return Number.isFinite(progress) ? Math.max(0, Math.min(100, Math.round(progress))) : 0
}

function sourceResetStatusLabel(sourceId: string) {
  const state = sourceResetState(sourceId)
  if (state === 'resetting') return t('protection.backupsPage.resetStatusResetting')
  if (state === 'reset_failed') return t('protection.backupsPage.resetStatusFailed')
  return t('protection.backupsPage.resetStatusConfigured')
}

function openResetTaskDetail(row: FlowSourceRow) {
  const task = resetTaskForSource(row.id)
  if (!task?.task_uuid) return
  openFlowSourceDetail(row, {
    tab: 'tasks',
    taskSubTab: 'executions',
    taskUuid: task.task_uuid,
  })
}

function latestBackupTaskForSource(sourceId: string) {
  const tasks = tasksForBackupConfigIds(sourceBackupConfigIds(sourceId))
  const active = tasks
    .filter(taskIsActive)
    .sort((a, b) => taskSortTime(b) - taskSortTime(a))[0]
  if (active) return active

  return tasks.slice().sort((a, b) => taskSortTime(b) - taskSortTime(a))[0] || null
}

function latestBackupTaskUuidForSource(sourceId: string) {
  return latestBackupTaskForSource(sourceId)?.task_uuid || String(latestSnapshotForSource(sourceId)?.task_uuid || '')
}

function latestRestoreRecordForSource(sourceId: string) {
  const records = restoreRecordsForSource(sourceId)
  const sortByLatestActivity = (a: RestoreRecord, b: RestoreRecord) => {
    const aTask = restoreRecordTaskRow(a, restoreTaskRows.value)
    const bTask = restoreRecordTaskRow(b, restoreTaskRows.value)
    const aTime = taskSortTime(aTask || ({ updated_at: a.updated_at, created_at: a.created_at } as TaskRow))
    const bTime = taskSortTime(bTask || ({ updated_at: b.updated_at, created_at: b.created_at } as TaskRow))
    return bTime - aTime
  }
  const active = records.filter((record) => {
    const task = restoreRecordTaskRow(record, restoreTaskRows.value)
    return taskIsActive(task) || restoreRecordIsRunning(record)
  }).sort(sortByLatestActivity)[0]
  return active || records.slice().sort(sortByLatestActivity)[0] || null
}

function latestRestoreTaskForSource(sourceId: string) {
  const runtimeTask = recordValue(runtimeSection(sourceId, 'restore').latest_task)
  const record = latestRestoreRecordForSource(sourceId)
  const recordTask = record ? restoreRecordTaskRow(record, restoreTaskRows.value) : null
  if (!runtimeTask.task_uuid) return recordTask || null
  if (!recordTask) return runtimeTask as TaskRow
  return taskSortTime(runtimeTask as TaskRow) >= taskSortTime(recordTask)
    ? runtimeTask as TaskRow
    : recordTask
}

function sourceRestoreRuntime(sourceId: string) {
  const runtime = runtimeSection(sourceId, 'restore')
  const executionState = String(runtime.execution_state || '').trim() || null
  const transferProgress = withExecutionState(
    isTransferProgress(runtime.transfer_progress)
      ? (runtime.transfer_progress as TransferProgress)
      : null,
    executionState,
  )
  const runningRecord = latestRestoreRecordForSource(sourceId)
  if (runningRecord && restoreRecordIsRunning(runningRecord)) {
    const task = restoreRecordTaskRow(runningRecord, restoreTaskRows.value)
    const taskProgress = Number(task?.progress)
    const fromTask = isTransferProgress(task?.transfer_progress) ? task.transfer_progress : null
    const fromPayload = task?.result_payload && typeof task.result_payload === 'object' && !Array.isArray(task.result_payload)
      ? (task.result_payload as Record<string, unknown>).transfer_progress
      : null
    const transferFromTask = fromTask || (isTransferProgress(fromPayload) ? fromPayload : null)
    return {
      running: true,
      failed: false,
      progress: Math.min(100, Math.max(0, Number.isFinite(taskProgress) ? taskProgress : runtimeNumber(runtime.progress))),
      transferProgress: transferFromTask || transferProgress || {
        label_key: 'protection.taskProgress.restore.estimating',
        phase: 'estimating',
        show_metrics: false,
      },
      executionState,
    }
  }
  if (Object.keys(runtime).length) {
    return {
      running: runtimeBool(runtime.running),
      failed: runtimeFailed(runtime),
      progress: runtimeNumber(runtime.progress),
      transferProgress,
      executionState,
    }
  }
  const latest = latestRestoreRecordForSource(sourceId)
  const failed = latest ? restoreRecordFlowStatus(latest, restoreRecordTaskRow(latest, restoreTaskRows.value)) === 'failed' : false
  return { running: false, failed, progress: 0, transferProgress: null }
}

type SourceStopKind = 'backup' | 'restore'
type SourceStopOptimistic = { kind: SourceStopKind; phase: 'stopping'; until: number }

const SOURCE_STOP_STOPPING_MS = 15000
const sourceStopOptimistic = ref<Record<string, SourceStopOptimistic>>({})

function sourceStopKey(sourceId: string, kind: SourceStopKind) {
  return `${kind}:${sourceId}`
}

function readSourceStopOptimistic(sourceId: string, kind: SourceStopKind) {
  const row = sourceStopOptimistic.value[sourceStopKey(sourceId, kind)]
  if (!row) return null
  if (Date.now() > row.until) {
    const next = { ...sourceStopOptimistic.value }
    delete next[sourceStopKey(sourceId, kind)]
    sourceStopOptimistic.value = next
    return null
  }
  return row
}

function markSourceStopPhase(sourceId: string, kind: SourceStopKind) {
  sourceStopOptimistic.value = {
    ...sourceStopOptimistic.value,
    [sourceStopKey(sourceId, kind)]: {
      kind,
      phase: 'stopping',
      until: Date.now() + SOURCE_STOP_STOPPING_MS,
    },
  }
}

function clearSourceStopPhase(sourceId: string, kind: SourceStopKind) {
  const key = sourceStopKey(sourceId, kind)
  if (!sourceStopOptimistic.value[key]) return
  const next = { ...sourceStopOptimistic.value }
  delete next[key]
  sourceStopOptimistic.value = next
}

function runtimeStopping(sourceId: string, kind: SourceStopKind) {
  const runtime = runtimeSection(sourceId, kind)
  if (runtimeBool(runtime.stopping)) return true
  return readSourceStopOptimistic(sourceId, kind)?.phase === 'stopping'
}

function syncSourceStopOptimisticFromRuntime(sourceId: string, kind: SourceStopKind) {
  const runtime = runtimeSection(sourceId, kind)
  if (runtimeBool(runtime.running) || runtimeBool(runtime.cancelled)) {
    clearSourceStopPhase(sourceId, kind)
    return
  }
  if (runtimeBool(runtime.stopping)) {
    markSourceStopPhase(sourceId, kind)
  }
}

function runningBackupTaskForSource(sourceId: string) {
  return tasksForBackupConfigIds(sourceBackupConfigIds(sourceId))
    .find((task) => task.status === 'running' || task.status === 'pending') ?? null
}

function runningRestoreTaskForSource(sourceId: string) {
  for (const record of restoreRecordsForSource(sourceId)) {
    if (!restoreRecordIsRunning(record)) continue
    const task = restoreRecordTaskRow(record, restoreTaskRows.value)
    if (task?.task_uuid) return task
    if (record.task_uuid) return { task_uuid: record.task_uuid } as TaskRow
  }
  return null
}

function sourceBackupCellPhase(sourceId: string): 'running' | 'stopping' | 'terminal' {
  if (sourceBackupRuntime(sourceId).running) return 'running'
  if (runtimeStopping(sourceId, 'backup')) return 'stopping'
  return 'terminal'
}

function sourceRestoreCellPhase(sourceId: string): 'running' | 'stopping' | 'terminal' {
  if (sourceRestoreRuntime(sourceId).running) return 'running'
  if (runtimeStopping(sourceId, 'restore')) return 'stopping'
  return 'terminal'
}

function configRepositoryParts(config: BackupConfig | BackupConfigDetail) {
  const repo = repositoryById.value.get(config.repository_id)
  if (!repo) return { name: `#${config.repository_id}`, location: '' }
  return { name: repo.name, location: repositoryDisplayLocation(repo) }
}

function sourceConfigDirRows(sourceId: string) {
  return sourceBackupConfigs(sourceId).flatMap((config) => {
    const directories = 'directories' in config ? config.directories : []
    return directories.map((dir) => ({
      configId: config.id,
      configName: config.name,
      path: dir.path,
    }))
  })
}

function sourceConfigTargetRows(sourceId: string) {
  return sourceBackupConfigs(sourceId).map((config) => {
    const repo = configRepositoryParts(config)
    const repository = repositoryById.value.get(config.repository_id)
    const repoType = String(repository?.repo_type || '').toUpperCase()
    return {
      id: `${config.id}:${config.repository_id}`,
      name: repo.name,
      location: repo.location,
      repoType,
      status: repository?.health === 'offline' || repository?.status === 'create_failed'
        ? 'offline'
        : repository?.health === 'online'
          ? 'online'
          : 'warning',
      health: repository?.health || '',
      nasProtocol: repository?.nas_protocol ?? null,
      bindNodeType: repository?.bind_node_type ?? null,
      bindNodeId: repository?.bind_node_id ?? null,
      bindNodeName: repository?.bind_node_display_name ?? null,
      bindNodeIp: repository?.bind_node_ip ?? null,
      s3Endpoint: (repository?.config?.endpoint as string | undefined) ?? null,
      s3Region: (repository?.config?.region as string | undefined) ?? null,
      s3Bucket: repository?.s3_bucket ?? null,
      nasServerAddress: (repository?.config?.server_address as string | undefined) ?? null,
      nasSharePath: (repository?.config?.share_path as string | undefined) ?? null,
      proxyNodeDir: (repository?.config?.proxy_node_dir as string | undefined) ?? null,
      usedBytes: Number(repository?.estimated_usage_bytes || 0),
      capacityBytes: Number(repository?.capacity_bytes || 0),
      lastCheckedAt: repository?.last_checked_at || '',
    } satisfies TargetRepositoryItem & { lastCheckedAt: string }
  })
}

function latestSnapshotForSource(sourceId: string) {
  const runtimeSnapshot = runtimeForSource(sourceId).latest_snapshot
  if (runtimeSnapshot && typeof runtimeSnapshot === 'object') return runtimeSnapshot as BackupSourceSnapshot
  return snapshotsForBackupConfigIds(sourceBackupConfigIds(sourceId)).reduce<BackupSourceSnapshot | null>((latest, snapshot) => {
    if (!latest) return snapshot
    return new Date(snapshotTime(snapshot)).getTime() > new Date(snapshotTime(latest)).getTime() ? snapshot : latest
  }, null)
}

function snapshotStatusLabel(status?: string) {
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

function taskSortTime(task: TaskRow) {
  return new Date(task.updated_at || task.finished_at || task.started_at || task.created_at || '').getTime() || 0
}

function flowBindingStateLabel(active: boolean) {
  return active ? t('protection.policiesPage.statusOn') : t('protection.policiesPage.statusOff')
}

function flowBindingStatePillClass(active: boolean) {
  return ['hfl-state-pill', active ? 'hfl-state-pill--enabled' : 'hfl-state-pill--disabled']
}

function flowBindingStatusDotClass(active: boolean) {
  return ['flow-binding-status-dot', active ? 'flow-binding-status-dot--enabled' : 'flow-binding-status-dot--disabled']
}

function flowPolicyUsageValue(count: number) {
  const n = Number(count) || 0
  if (locale.value === 'en' && n === 1) return '1 Backup Source'
  return t('protection.policiesPage.appliedToBackupSourcesCount', { n })
}

type FlowPolicyRetentionDetailLine = {
  label?: string
  text: string
}

function flowPolicyScheduleValue(policy: BackupPolicy | null | undefined) {
  if (!policy) return t('protection.backupDetail.durationDash')
  if (policy.schedule?.enabled === false) return t('protection.backupsPage.policyConfigNotConfigured')
  return policy.schedule_summary || policy.schedule?.cron_expr || t('protection.backupsPage.policyConfigNotConfigured')
}

function flowPolicyDetailRows(policy: BackupPolicy | null | undefined) {
  if (!policy) return []
  return [
    { label: t('protection.policiesPage.fieldSchedule'), value: flowPolicyScheduleValue(policy) },
    { label: t('protection.policiesPage.appliedToBackupSourcesLabel'), value: flowPolicyUsageValue(policy.related_backup_count) },
  ]
}

function flowPolicyRetentionDetailLines(policy: BackupPolicy | null | undefined): FlowPolicyRetentionDetailLine[] {
  if (!policy) return [{ text: t('protection.backupsPage.policyConfigNotConfigured') }]
  const f = backupPolicyToForm(policy)
  if (!f.sectionRetentionEnabled) {
    return [{ text: t('protection.backupsPage.policyConfigNotConfigured') }]
  }
  const hasRetentionValues =
    Number(f.retentionRecentPoints) > 0 ||
    f.retentionHourlyEnabled ||
    f.retentionDailyEnabled ||
    f.retentionWeeklyEnabled ||
    f.retentionMonthlyEnabled ||
    f.retentionAnnualEnabled
  if (!hasRetentionValues && policy.retention_summary) {
    return [{ text: policy.retention_summary }]
  }

  if (messageLocale.value === 'en') {
    const latestSuffix = Number(f.retentionRecentPoints) === 1 ? 'point' : 'points'
    const lines: FlowPolicyRetentionDetailLine[] = [{ text: `Keep latest ${f.retentionRecentPoints} restore ${latestSuffix}.` }]
    if (f.retentionHourlyEnabled) {
      lines.push({ label: 'Hourly:', text: `Keep one restore point per hour for ${f.retentionHourlyHours} hour(s).` })
    }
    if (f.retentionDailyEnabled) {
      lines.push({ label: 'Daily:', text: `Keep one restore point per day for ${f.retentionDailyDays} day(s).` })
    }
    if (f.retentionMonthlyEnabled) {
      lines.push({ label: 'Monthly:', text: `Keep one restore point per month for ${f.retentionMonthlyMonths} month(s).` })
    }
    return lines
  }

  const lines: FlowPolicyRetentionDetailLine[] = [{ text: `Keep the latest ${f.retentionRecentPoints} restore points.` }]
  if (f.retentionHourlyEnabled) {
    lines.push({ label: 'Hourly:', text: `Keep one restore point per hour for ${f.retentionHourlyHours} hours.` })
  }
  if (f.retentionDailyEnabled) {
    lines.push({ label: 'Daily:', text: `Keep one restore point per day for ${f.retentionDailyDays} days.` })
  }
  if (f.retentionMonthlyEnabled) {
    lines.push({ label: 'Monthly:', text: `Keep one restore point per month for ${f.retentionMonthlyMonths} months.` })
  }
  return lines
}

function flowFilterCompiledRuleLines(rule: FileFilterRule | null | undefined) {
  if (!rule) return []
  return compileFilterIgnorePatterns(fileFilterFormView(rule))
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
}

function flowFilterMaxSizeLimitValue(rule: FileFilterRule) {
  return `>${rule.large_file_limit_enabled ? fmtBytes(Number(rule.large_file_bytes_max) || 0) : t('protection.policiesPage.previewNoLimit')}`
}

function flowFilterHoverRows(rule: FileFilterRule | null | undefined) {
  if (!rule) return []
  const rows: Array<{ label: string, value: string, enabled?: boolean }> = [
    { label: t('protection.policiesPage.appliedToBackupSourcesLabel'), value: flowPolicyUsageValue(rule.related_backup_count) },
  ]
  if (rule.large_file_limit_enabled) {
    rows.push({ label: t('protection.policiesPage.previewMaxSizeLimit'), value: flowFilterMaxSizeLimitValue(rule) })
  }
  if (rule.ignore_cache_directories) {
    rows.push({ label: t('protection.policiesPage.cacheTitle'), value: flowBindingStateLabel(true), enabled: true })
  }
  if (rule.current_filesystem_only) {
    rows.push({ label: t('protection.policiesPage.fsOnlyTitle'), value: flowBindingStateLabel(true), enabled: true })
  }
  return rows
}

function sourceConfigPolicyRows(sourceId: string) {
  return sourceBackupConfigs(sourceId).map((config) => {
    const policy = config.backup_policy_id ? (backupPolicyById.value.get(config.backup_policy_id) ?? null) : null
    return {
      id: `${config.id}:${config.backup_policy_id ?? 'none'}`,
      bound: Boolean(config.backup_policy_id),
      isActive: policy?.is_active !== false,
      name: policy?.name ?? (config.backup_policy_id ? `#${config.backup_policy_id}` : t('protection.backupsPage.flowBackupColPolicyNone')),
      detailRows: flowPolicyDetailRows(policy),
      retentionLines: flowPolicyRetentionDetailLines(policy),
    }
  })
}

function fileFilterFormView(rule: FileFilterRule) {
  return fileFilterRuleToForm(rule)
}

function sourceConfigFilterRows(sourceId: string) {
  return sourceBackupConfigs(sourceId).map((config) => {
    const rule = config.file_filter_rule_id ? (fileFilterById.value.get(config.file_filter_rule_id) ?? null) : null
    return {
      id: `${config.id}:${config.file_filter_rule_id ?? 'none'}`,
      bound: Boolean(config.file_filter_rule_id),
      isActive: rule?.is_active !== false,
      name: rule?.name ?? (config.file_filter_rule_id ? `#${config.file_filter_rule_id}` : t('protection.backupsPage.flowBackupColPolicyNone')),
      usage: flowPolicyUsageValue(rule?.related_backup_count ?? 0),
      hoverRows: flowFilterHoverRows(rule),
      compiledRules: flowFilterCompiledRuleLines(rule),
    }
  })
}

function sourceBoundPolicyRows(sourceId: string) {
  return sourceConfigPolicyRows(sourceId).filter((policy) => policy.bound)
}

function sourceBoundFilterRows(sourceId: string) {
  return sourceConfigFilterRows(sourceId).filter((filter) => filter.bound)
}


function sourcePoliciesLabel(sourceId: string) {
  const policies = sourceBoundPolicyRows(sourceId)
  if (!policies.length) return null
  if (policies.length === 1) return policies[0].name
  return t('protection.backupsPage.flowSourcePolicyCount', { n: policies.length })
}

function sourceFiltersLabel(sourceId: string) {
  const filters = sourceBoundFilterRows(sourceId)
  if (!filters.length) return null
  if (filters.length === 1) return filters[0].name
  return t('protection.backupsPage.flowSourceFilterCount', { n: filters.length })
}

const FLOW_SOURCE_CELL_PREVIEW = 2
const FLOW_SOURCE_DIR_CELL_PREVIEW = 2

function sourceDirsPreview(sourceId: string) {
  return sourceConfigDirRows(sourceId).slice(0, FLOW_SOURCE_DIR_CELL_PREVIEW)
}

function sourceDirsOverflowCount(sourceId: string) {
  const total = sourceConfigDirRows(sourceId).length
  return total > FLOW_SOURCE_DIR_CELL_PREVIEW ? total - FLOW_SOURCE_DIR_CELL_PREVIEW : 0
}

function sourceTargetsPreview(sourceId: string) {
  return sourceConfigTargetRows(sourceId).slice(0, FLOW_SOURCE_CELL_PREVIEW)
}

function sourceTargetsOverflowCount(sourceId: string) {
  const total = sourceConfigTargetRows(sourceId).length
  return total > FLOW_SOURCE_CELL_PREVIEW ? total - FLOW_SOURCE_CELL_PREVIEW : 0
}

function flowTargetTone(target: TargetRepositoryItem | null | undefined) {
  if (!target) return 'unassigned'
  if (target.status === 'online') return 'online'
  if (target.status === 'warning') return 'warning'
  if (target.status === 'offline') return 'offline'
  return 'unknown'
}

function flowSourceRowById(sourceId: string) {
  return (
    step3SourceList.value.find((row) => row.id === sourceId) ??
    step2SourceList.value.find((row) => row.id === sourceId) ??
    backupSelectableRows.value.find((row) => row.id === sourceId) ??
    flowRowFromSourceId(sourceId)
  )
}

function restoreRunningCountForSource(sourceId: string) {
  const runtime = runtimeSection(sourceId, 'restore')
  if (Object.keys(runtime).length) return runtimeBool(runtime.running) ? Math.max(1, runtimeNumber(runtime.running_count)) : 0
  return restoreRecordsForSource(sourceId).filter(restoreRecordIsRunning).length
}

function openTaskDetail(taskUuid?: string | null) {
  const uuid = String(taskUuid || '').trim()
  if (!uuid) return
  backupTaskDetailUuid.value = uuid
  backupTaskDetailOpen.value = true
  nextTick(() => {
    requestAnimationFrame(() => updateDrawerWidth())
  })
}

function openLatestBackupTask(row: FlowSourceRow) {
  openTaskDetail(latestBackupTaskUuidForSource(row.id))
}

function openLatestRestoreTask(row: FlowSourceRow) {
  const record = latestRestoreRecordForSource(row.id)
  if (!record) return
  openFlowSourceDetail(row, {
    tab: 'restoreRecords',
    restoreRecordId: record.id,
    restoreRecordTaskUuid: record.task_uuid,
  })
}

function openRestoreTaskStatusDrawer(row: FlowSourceRow) {
  restoreDrawerSourceId.value = row.id
  restoreTaskPager.page = 1
  restoreTaskDrawerOpen.value = true
  nextTick(() => {
    requestAnimationFrame(() => updateDrawerWidth())
  })
  const scope = 'restore-task-drawer'
  const signal = pageRequests.nextSignal(scope)
  void refreshRestoreRecords(signal)
    .catch((e) => {
      showApiError(e)
    })
    .finally(() => pageRequests.releaseSignal(scope, signal))
}

function openFlowSourceListDrawer(row: FlowSourceRow, kind: 'dirs' | 'targets') {
  openFlowSourceDetail(row, { tab: 'overview', scrollTo: kind })
}

function onRestoreTaskDrawerClosed() {
  pageRequests.abortScope('restore-task-drawer')
  restoreDrawerSourceId.value = null
}



function restorePhaseLabel(idx: number) {
  const keys = [
    'protection.backupsPage.flowTaskPhaseRestorePrepare',
    'protection.backupsPage.flowTaskPhaseRestoreLayout',
    'protection.backupsPage.flowTaskPhaseRestoreCopy',
    'protection.backupsPage.flowTaskPhaseRestoreVerify',
    'protection.backupsPage.flowTaskPhaseRestoreDone',
  ] as const
  return t(keys[Math.min(Math.max(idx, 0), keys.length - 1)])
}


const flowSourceTypeFilterOptions = computed(() => [
  { text: t('protection.backupsPage.sourceTypeHost'), value: 'host' },
  { text: t('protection.backupsPage.sourceTypeNas'), value: 'nas' },
  { text: t('protection.backupsPage.sourceTypeNasSmb'), value: 'nas_smb' },
  { text: t('protection.backupsPage.sourceTypeNasNfs'), value: 'nas_nfs' },
])

const flowSourceStatusFilterOptions = computed(() => [
  { text: t('protection.backupsPage.nodeStatusOnline'), value: 'online' },
  { text: t('protection.backupsPage.nodeStatusOffline'), value: 'offline' },
])

const flowBindingFilterOptions = computed(() => [
  { text: t('protection.backupsPage.flowFilterBound'), value: 'bound' },
  { text: t('protection.backupsPage.flowFilterUnbound'), value: 'unbound' },
])



const flowRepoHealthFilterOptions = computed(() => [
  { text: t('protection.backupsPage.targetStatusOnline'), value: 'online' },
  { text: t('protection.backupsPage.targetStatusOffline'), value: 'offline' },
  { text: t('protection.backupsPage.targetStatusUnknown'), value: 'unknown' },
])


const step3SearchFieldOptions = computed(() => [
  { label: t('protection.backupsPage.step3SearchSourceName'), value: 'source_name' as const },
  { label: t('protection.backupsPage.step3SearchHostname'), value: 'source_hostname' as const },
  { label: t('protection.backupsPage.step3SearchIp'), value: 'source_ip' as const },
])

const step3SourceTypeOptions = computed(() => [
  { label: t('protection.backupsPage.sourceTypeHost'), value: 'host' as const },
  { label: t('protection.backupsPage.sourceTypeNas'), value: 'nas' as const },
])

const step3SourceStatusOptions = computed(() => [
  { label: t('protection.backupsPage.step3StatusActive'), value: 'active' },
  { label: t('protection.backupsPage.step3StatusInactive'), value: 'inactive' },
  { label: t('protection.backupsPage.step3StatusError'), value: 'error' },
  { label: t('protection.backupsPage.step3StatusRemoving'), value: 'removing' },
  { label: t('protection.backupsPage.step3StatusRemoveFailed'), value: 'remove_failed' },
])

const step3AvailabilityOptions = computed(() => [
  { label: t('protection.backupsPage.step3StatusOnline'), value: 'online' as const },
  { label: t('protection.backupsPage.step3StatusOffline'), value: 'offline' as const },
])

const step3BackupTaskStatusOptions = computed(() => [
  { label: t('protection.backupsPage.step3BackupSuccess'), value: 'success' as const },
  { label: t('protection.backupsPage.step3BackupFailed'), value: 'failed' as const },
  { label: t('protection.backupsPage.step3BackupRunning'), value: 'running' as const },
  { label: t('protection.backupsPage.step3TaskNoTask'), value: 'none' as const },
])

const step3RestoreTaskStatusOptions = computed(() => [
  { label: t('protection.backupsPage.step3RestoreSuccess'), value: 'success' as const },
  { label: t('protection.backupsPage.step3RestoreFailed'), value: 'failed' as const },
  { label: t('protection.backupsPage.step3RestoreRunning'), value: 'running' as const },
  { label: t('protection.backupsPage.step3TaskNoTask'), value: 'none' as const },
])

const step3BackupPolicyOptions = computed(() =>
  [...backupPolicyById.value.values()].sort((a, b) => a.name.localeCompare(b.name)),
)
const step3FileFilterOptions = computed(() =>
  [...fileFilterById.value.values()].sort((a, b) => a.name.localeCompare(b.name)),
)
const step3RepositoryOptions = computed(() =>
  [...repositoryById.value.values()].sort((a, b) => a.name.localeCompare(b.name)),
)

const flowActiveFilterCount = computed(() => {
  if (flowMainStep.value === 2) {
    return [
      step3SourceType.value,
      step3SourceStatus.value,
      step3Availability.value,
      step3BackupTaskStatus.value,
      step3RestoreTaskStatus.value,
      step3AdvancedSourceName.value.trim(),
      step3AdvancedHostname.value.trim(),
      step3AdvancedIp.value.trim(),
      step3BackupPolicyId.value,
      step3FileFilterRuleId.value,
      step3RepositoryId.value,
    ].filter(Boolean).length
  }
  let count = 0
  count += [
    step3SourceType.value,
    step3SourceStatus.value,
    step3AdvancedSourceName.value.trim(),
    step3AdvancedHostname.value.trim(),
    step3AdvancedIp.value.trim(),
    step3Availability.value,
  ].filter(Boolean).length
  const buckets = [
    flowFilterSourceTypes.value,
    flowFilterSourceStatuses.value,
    flowFilterPolicyBinding.value,
    flowFilterFileFilterBinding.value,
    flowFilterBackupTaskStatuses.value,
    flowFilterRestoreTasks.value,
    flowFilterRepoHealth.value,
  ]
  count += buckets.filter((list) => list.length > 0).length
  if (flowFilterNodeQuery.value.trim()) count += 1
  if (flowFilterTargetQuery.value.trim()) count += 1
  if (flowFilterDirectoryQuery.value.trim()) count += 1
  if (flowFilterLastBackupMode.value !== 'all') count += 1
  return count
})

function visibleFlowHeaderFilterOptions(options: FlowHeaderFilterOption[], key: FlowHeaderFilterKey) {
  const query = flowHeaderFilterSearch[key].trim().toLowerCase()
  if (!query) return options
  return options.filter((option) => option.text.toLowerCase().includes(query))
}

function hasFlowHeaderFilterValue(key: FlowHeaderFilterKey) {
  if (key === 'sourceType') return flowFilterSourceTypes.value.length > 0
  if (key === 'sourceStatus') return flowFilterSourceStatuses.value.length > 0
  if (key === 'backupTaskStatus') return flowFilterBackupTaskStatuses.value.length > 0
  if (key === 'repoHealth') return flowFilterRepoHealth.value.length > 0
  if (key === 'policyBinding') return flowFilterPolicyBinding.value.length > 0
  return flowFilterFileFilterBinding.value.length > 0
}

function clearFlowHeaderFilter(key: FlowHeaderFilterKey) {
  if (key === 'sourceType') flowFilterSourceTypes.value = []
  else if (key === 'sourceStatus') flowFilterSourceStatuses.value = []
  else if (key === 'backupTaskStatus') flowFilterBackupTaskStatuses.value = []
  else if (key === 'repoHealth') flowFilterRepoHealth.value = []
  else if (key === 'policyBinding') flowFilterPolicyBinding.value = []
  else flowFilterFileFilterBinding.value = []
  flowHeaderFilterSearch[key] = ''
}

function closeFlowHeaderFilter(key: FlowHeaderFilterKey) {
  flowHeaderFilterOpen[key] = false
}

function flowSourceTypeMatches(row: FlowSourceRow, values: FlowSourceTypeFilter[]) {
  if (!values.length) return true
  return values.some((value) => {
    if (value === 'host') return row.type === 'host'
    if (value === 'nas') return row.type === 'nas'
    if (value === 'nas_smb') return row.type === 'nas' && row.protocol === 'smb'
    return row.type === 'nas' && row.protocol === 'nfs'
  })
}

function flowBindingMatches(bound: boolean, filters: FlowBindingFilter[]) {
  if (!filters.length) return true
  return filters.includes(bound ? 'bound' : 'unbound')
}

function normalizeRepositoryHealth(value?: string): FlowRepoHealthFilter {
  const normalized = String(value || '').toLowerCase()
  if (normalized === 'online') return 'online'
  if (normalized === 'offline') return 'offline'
  return 'unknown'
}

function sourceHasPolicyBinding(sourceId: string) {
  const rows = sourceConfigPolicyRows(sourceId)
  return rows.length > 0 && rows.some((policy) => policy.bound)
}

function sourceHasFileFilterBinding(sourceId: string) {
  const rows = sourceConfigFilterRows(sourceId)
  return rows.length > 0 && rows.some((filter) => filter.bound)
}

function sourceRepoHealthMatches(sourceId: string, filters: FlowRepoHealthFilter[]) {
  if (!filters.length) return true
  const targets = sourceConfigTargetRows(sourceId)
  if (!targets.length) return filters.includes('unknown')
  return targets.some((target) => filters.includes(normalizeRepositoryHealth(target.health)))
}

function sourceLastBackupMatches(sourceId: string) {
  const mode = flowFilterLastBackupMode.value
  if (mode === 'all') return true
  const latest = latestSnapshotTimeForSource(sourceId) || aggregateForSource(sourceId).lastBackupAt
  if (mode === 'never') return !latest
  if (!latest) return false
  const timestamp = new Date(latest).getTime()
  if (!Number.isFinite(timestamp)) return false
  const now = Date.now()
  if (mode === 'days24') return timestamp >= now - 24 * 60 * 60 * 1000
  if (mode === 'days7') return timestamp >= now - 7 * 24 * 60 * 60 * 1000
  if (mode === 'days30') return timestamp >= now - 30 * 24 * 60 * 60 * 1000
  const [start, end] = flowFilterLastBackupRange.value || []
  if (!start || !end) return true
  const startTime = new Date(start).setHours(0, 0, 0, 0)
  const endTime = new Date(end).setHours(23, 59, 59, 999)
  return timestamp >= startTime && timestamp <= endTime
}

function flowCommonFiltersMatch(row: FlowSourceRow) {
  const nodeQuery = flowFilterNodeQuery.value.trim().toLowerCase()
  return (
    flowSourceTypeMatches(row, flowFilterSourceTypes.value) &&
    (!flowFilterSourceStatuses.value.length || flowFilterSourceStatuses.value.includes(row.status)) &&
    (!nodeQuery || [row.nodeName, row.nodeIp, row.hostname].some((value) => String(value || '').toLowerCase().includes(nodeQuery)))
  )
}

function flowTaskSearchMatches(row: FlowSourceRow, query: string) {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return [
    row.name,
    row.hostname,
    row.nodeName,
    row.nodeIp,
    ...sourceBackupConfigs(row.id).map((config) => config.name),
    ...sourceConfigDirRows(row.id).map((dir) => dir.path),
    ...sourceConfigTargetRows(row.id).map((target) => target.name),
    ...sourceConfigPolicyRows(row.id).map((policy) => policy.name),
    ...sourceConfigFilterRows(row.id).map((filter) => filter.name),
  ].some((value) => String(value).toLowerCase().includes(q))
}

function applyTaskSearchImmediately() {
  if (taskSearchDebounceTimer) {
    clearTimeout(taskSearchDebounceTimer)
    taskSearchDebounceTimer = null
  }
  const next = taskSearchQuery.value
  if (debouncedTaskSearchQuery.value !== next) {
    debouncedTaskSearchQuery.value = next
    return
  }
  flowStep1Pager.page = 1
  flowStep2Pager.page = 1
  if (flowMainStep.value === 0) {
    flowStep0Pager.page = 1
    void loadBackupSelectable()
  } else if (flowMainStep.value === 1) {
    void refreshFlowStepData(1)
  }
}

function onStep3SearchFieldChange() {
  const hasSearchCondition = taskSearchQuery.value.trim() !== '' || debouncedTaskSearchQuery.value.trim() !== ''
  const hasAppliedSearch = debouncedTaskSearchQuery.value.trim() !== ''
  if (taskSearchDebounceTimer) {
    clearTimeout(taskSearchDebounceTimer)
    taskSearchDebounceTimer = null
  }
  taskSearchQuery.value = ''
  flowStep0Pager.page = 1
  flowStep1Pager.page = 1
  flowStep2Pager.page = 1
  if (!hasSearchCondition) return
  if (hasAppliedSearch) {
    // The debounced-search watcher performs the single refresh for an
    // already-applied query after it is cleared.
    debouncedTaskSearchQuery.value = ''
    return
  }
  // The user changed type before the debounce elapsed; reload once with the
  // cleared query rather than waiting for a stale timer.
  if (flowMainStep.value === 0) void loadBackupSelectable()
  else if (flowMainStep.value === 1) void refreshFlowStepData(1)
  else void refreshFlowStepData(2)
}

function flowStep3FiltersMatch(row: FlowSourceRow) {
  const targetQuery = flowFilterTargetQuery.value.trim().toLowerCase()
  const directoryQuery = flowFilterDirectoryQuery.value.trim().toLowerCase()
  const aggregate = aggregateForSource(row.id)
  const runtime = sourceBackupRuntime(row.id)
  const taskStatus: FlowBackupTaskStatusFilter = runtime.running
    ? 'running'
    : runtime.failed || aggregate.taskStatus === 'failed'
      ? 'failed'
      : aggregate.taskStatus === 'completed'
        ? 'completed'
        : 'idle'
  return (
    flowCommonFiltersMatch(row) &&
    (!flowFilterBackupTaskStatuses.value.length || flowFilterBackupTaskStatuses.value.includes(taskStatus)) &&
    (!flowFilterRestoreTasks.value.length || flowFilterRestoreTasks.value.includes(restoreRunningCountForSource(row.id) > 0 ? 'running' : 'none')) &&
    flowBindingMatches(sourceHasPolicyBinding(row.id), flowFilterPolicyBinding.value) &&
    flowBindingMatches(sourceHasFileFilterBinding(row.id), flowFilterFileFilterBinding.value) &&
    sourceRepoHealthMatches(row.id, flowFilterRepoHealth.value) &&
    sourceLastBackupMatches(row.id) &&
    (!targetQuery || sourceConfigTargetRows(row.id).some((target) =>
      [target.name, target.location].some((value) => String(value || '').toLowerCase().includes(targetQuery)),
    )) &&
    (!directoryQuery || sourceConfigDirRows(row.id).some((dir) => dir.path.toLowerCase().includes(directoryQuery)))
  )
}


function syncStep3AdvancedFilterDrafts() {
  draftStep3SourceType.value = step3SourceType.value
  draftStep3SourceStatus.value = step3SourceStatus.value
  draftStep3Availability.value = step3Availability.value
  draftStep3BackupTaskStatus.value = step3BackupTaskStatus.value
  draftStep3RestoreTaskStatus.value = step3RestoreTaskStatus.value
  draftStep3AdvancedSourceName.value = step3AdvancedSourceName.value
  draftStep3AdvancedHostname.value = step3AdvancedHostname.value
  draftStep3AdvancedIp.value = step3AdvancedIp.value
  draftStep3BackupPolicyId.value = step3BackupPolicyId.value
  draftStep3FileFilterRuleId.value = step3FileFilterRuleId.value
  draftStep3RepositoryId.value = step3RepositoryId.value
}

function resetStep3AdvancedFilterDrafts() {
  draftStep3SourceType.value = ''
  draftStep3SourceStatus.value = ''
  draftStep3Availability.value = ''
  draftStep3BackupTaskStatus.value = ''
  draftStep3RestoreTaskStatus.value = ''
  draftStep3AdvancedSourceName.value = ''
  draftStep3AdvancedHostname.value = ''
  draftStep3AdvancedIp.value = ''
  draftStep3BackupPolicyId.value = ''
  draftStep3FileFilterRuleId.value = ''
  draftStep3RepositoryId.value = ''
}

function clearStep3AdvancedFilterDraft(field: 'sourceType' | 'sourceName' | 'hostname' | 'ip' | 'sourceStatus' | 'backupTaskStatus' | 'restoreTaskStatus' | 'availability' | 'backupPolicy' | 'fileFilter' | 'repository') {
  if (field === 'sourceType') draftStep3SourceType.value = ''
  else if (field === 'sourceName') draftStep3AdvancedSourceName.value = ''
  else if (field === 'hostname') draftStep3AdvancedHostname.value = ''
  else if (field === 'ip') draftStep3AdvancedIp.value = ''
  else if (field === 'sourceStatus') draftStep3SourceStatus.value = ''
  else if (field === 'backupTaskStatus') draftStep3BackupTaskStatus.value = ''
  else if (field === 'restoreTaskStatus') draftStep3RestoreTaskStatus.value = ''
  else if (field === 'availability') draftStep3Availability.value = ''
  else if (field === 'backupPolicy') draftStep3BackupPolicyId.value = ''
  else if (field === 'fileFilter') draftStep3FileFilterRuleId.value = ''
  else draftStep3RepositoryId.value = ''
}

function openAdvancedFilters() {
  syncStep3AdvancedFilterDrafts()
  flowAdvancedFilterOpen.value = true
}

function cancelAdvancedFilters() {
  syncStep3AdvancedFilterDrafts()
  flowAdvancedFilterOpen.value = false
}

function applyAdvancedFilters() {
  step3SourceType.value = draftStep3SourceType.value
  step3SourceStatus.value = draftStep3SourceStatus.value
  step3Availability.value = draftStep3Availability.value
  step3BackupTaskStatus.value = draftStep3BackupTaskStatus.value
  step3RestoreTaskStatus.value = draftStep3RestoreTaskStatus.value
  step3AdvancedSourceName.value = draftStep3AdvancedSourceName.value
  step3AdvancedHostname.value = draftStep3AdvancedHostname.value
  step3AdvancedIp.value = draftStep3AdvancedIp.value
  step3BackupPolicyId.value = draftStep3BackupPolicyId.value
  step3FileFilterRuleId.value = draftStep3FileFilterRuleId.value
  step3RepositoryId.value = draftStep3RepositoryId.value
  flowAdvancedFilterOpen.value = false
}

const filteredBackupSelectableRows = computed(() =>
  sourcePendingOps.injectPendingRows(
    backupSelectableRows.value.filter(flowCommonFiltersMatch),
    { activeRemovalNodeIds: wizardActiveRemovalNodeIds.value },
  ),
)
const filteredStep2SourceList = computed(() => {
  const q = debouncedTaskSearchQuery.value
  return sourcePendingOps.injectPendingRows(
    step2PendingSourceList.value.filter((row) => flowCommonFiltersMatch(row) && flowTaskSearchMatches(row, q)),
    { activeRemovalNodeIds: wizardActiveRemovalNodeIds.value },
  )
})
const flowStep0DisplayTotal = computed(() => flowActiveFilterCount.value > 0 ? filteredBackupSelectableRows.value.length : backupSelectableCount.value)

const filteredStep3SourceList = computed(() => {
  if (flowMainStep.value === 2) {
    return sourcePendingOps.injectPendingRows(
      step3SourceList.value,
      { activeRemovalNodeIds: wizardActiveRemovalNodeIds.value },
    )
  }
  const q = debouncedTaskSearchQuery.value
  return sourcePendingOps.injectPendingRows(
    step3SourceList.value
      .filter(flowStep3FiltersMatch)
      .filter((row) => flowTaskSearchMatches(row, q)),
    { activeRemovalNodeIds: wizardActiveRemovalNodeIds.value },
  )
})

const paginatedStep2SourceList = computed(() =>
  flowMainStep.value === 1
    ? filteredStep2SourceList.value
    : paginateFlowList(filteredStep2SourceList.value, flowStep1Pager),
)
const paginatedStep3SourceList = computed(() =>
  flowMainStep.value === 2
    ? filteredStep3SourceList.value
    : paginateFlowList(filteredStep3SourceList.value, flowStep2Pager),
)

watch(backupSelectableCount, (total) => clampFlowPagerPage(flowStep0Pager, total))
watch(filteredStep2SourceList, (list) => {
  if (flowMainStep.value === 1) return
  clampFlowPagerPage(flowStep1Pager, list.length)
})
watch(filteredStep3SourceList, (list) => {
  if (flowMainStep.value === 2) return
  clampFlowPagerPage(flowStep2Pager, list.length)
})
watch(taskSearchQuery, () => {
  if (taskSearchDebounceTimer) clearTimeout(taskSearchDebounceTimer)
  taskSearchDebounceTimer = setTimeout(() => {
    debouncedTaskSearchQuery.value = taskSearchQuery.value
    taskSearchDebounceTimer = null
  }, TASK_SEARCH_DELAY_MS)
})
watch(debouncedTaskSearchQuery, () => {
  flowStep1Pager.page = 1
  flowStep2Pager.page = 1
  if (flowMainStep.value === 0) {
    flowStep0Pager.page = 1
    void loadBackupSelectable()
  } else if (flowMainStep.value === 1) {
    void refreshFlowStepData(1)
  } else if (flowMainStep.value === 2) {
    void refreshFlowStepData(2)
  }
})
watch(
  () => flowFilterBackupTaskStatuses.value.join(','),
  () => {
    flowStep2Pager.page = 1
  },
)
watch(
  () => [
    step3SourceType.value,
    step3SourceStatus.value,
    step3Availability.value,
    step3BackupTaskStatus.value,
    step3RestoreTaskStatus.value,
    step3AdvancedSourceName.value,
    step3AdvancedHostname.value,
    step3AdvancedIp.value,
    step3BackupPolicyId.value,
    step3FileFilterRuleId.value,
    step3RepositoryId.value,
  ],
  () => {
    if (flowMainStep.value === 0) {
      flowStep0Pager.page = 1
      void loadBackupSelectable()
    } else if (flowMainStep.value === 1) {
      flowStep1Pager.page = 1
      void refreshFlowStepData(1)
    } else {
      flowStep2Pager.page = 1
      void refreshFlowStepData(2)
    }
  },
)
watch(
  () => flowStep0Pager.pageSize,
  () => {
    clampFlowPagerPage(flowStep0Pager, backupSelectableCount.value)
    void loadBackupSelectable()
  },
)
watch(
  () => flowStep0Pager.page,
  () => {
    if (flowMainStep.value === 0) void loadBackupSelectable()
  },
)
watch(
  () => flowStep1Pager.pageSize,
  () => {
    if (flowMainStep.value === 1) {
      flowStep1Pager.page = 1
      void refreshFlowStepData(1)
      return
    }
    clampFlowPagerPage(flowStep1Pager, step2PendingSourceList.value.length)
  },
)
watch(
  () => flowStep1Pager.page,
  () => {
    if (flowMainStep.value === 1) void refreshFlowStepData(1)
  },
)
watch(
  () => flowStep2Pager.pageSize,
  () => {
    if (flowMainStep.value === 2) {
      flowStep2Pager.page = 1
      void refreshFlowStepData(2)
      return
    }
    clampFlowPagerPage(flowStep2Pager, filteredStep3SourceList.value.length)
  },
)
watch(
  () => flowStep2Pager.page,
  () => {
    if (flowMainStep.value === 2) void refreshFlowStepData(2)
  },
)

const STEP3_REFRESH_IDLE_MS = 30000
const STEP3_REFRESH_ACTIVE_MS = 2000
let step3RefreshTimer: number | null = null
let step3RefreshInFlight = false
let step3RefreshIntervalMs = STEP3_REFRESH_IDLE_MS
let step3ActionRefreshInFlight = false
let step3ActionRefreshSeq = 0

function hasRunningStep3Tasks() {
  return step3SourceList.value.some((row) =>
    sourceBackupRuntime(row.id).running
    || sourceResetRunning(row.id)
    || restoreRunningCountForSource(row.id) > 0
    || runtimeStopping(row.id, 'backup')
    || runtimeStopping(row.id, 'restore'),
  )
}

function stopStep3AutoRefresh() {
  if (step3RefreshTimer !== null) {
    window.clearInterval(step3RefreshTimer)
    step3RefreshTimer = null
  }
}

function ensureStep3AutoRefreshInterval() {
  const nextInterval = hasRunningStep3Tasks() ? STEP3_REFRESH_ACTIVE_MS : STEP3_REFRESH_IDLE_MS
  if (step3RefreshTimer !== null && step3RefreshIntervalMs === nextInterval) return
  stopStep3AutoRefresh()
  step3RefreshIntervalMs = nextInterval
  if (flowMainStep.value !== 2) return
  step3RefreshTimer = window.setInterval(() => {
    void refreshStep3SourceList()
  }, step3RefreshIntervalMs)
}

async function refreshStep3SourceList() {
  if (flowMainStep.value !== 2 || step3RefreshInFlight || step3ActionRefreshInFlight) return
  const scope = flowStepScope(2)
  const signal = pageRequests.nextSignal(scope)
  const resetTrackedIds = collectResetTrackedIds()
  step3RefreshInFlight = true
  try {
    await refreshStep3RuntimeRows(signal)
    if (!resetTrackedIds.length) return
    const configsLoaded = await refreshBackupConfigs(signal)
    if (!pageRequests.isCurrentSignal(scope, signal)) return
    // Soft-failure clears config/task rows; do not treat empty reset state as success.
    if (!configsLoaded) return
    await loadStep3Selectable({ signal })
    if (!pageRequests.isCurrentSignal(scope, signal)) return
    const finishedIds = selectFinishedResetSourceIds(
      resetTrackedIds,
      (id) => sourceResetState(id),
    )
    const failedIds = selectTerminalFailedResetSourceIds(
      resetTrackedIds,
      (id) => sourceResetState(id),
    )
    // Drop terminal failures immediately; keep finished ids tracked until the
    // pipeline refresh succeeds so an abort/failure can retry next poll.
    untrackResetPipelineSources(failedIds)
    if (!finishedIds.length) return
    await refreshPipelineStep2PlusIds(signal)
    if (!pageRequests.isCurrentSignal(scope, signal)) return
    syncStep2WizardCountFromPipeline()
    untrackResetPipelineSources(finishedIds)
  } catch {
    // Auto-refresh is best-effort; keep the last known runtime state and retry
    // on the next interval without creating an unhandled promise rejection.
  } finally {
    pageRequests.releaseSignal(scope, signal)
    step3RefreshInFlight = false
    for (const row of step3SourceList.value) {
      syncSourceStopOptimisticFromRuntime(row.id, 'backup')
      syncSourceStopOptimisticFromRuntime(row.id, 'restore')
    }
    ensureStep3AutoRefreshInterval()
  }
}

function syncStep3AutoRefresh() {
  if (flowMainStep.value === 2) {
    ensureStep3AutoRefreshInterval()
  } else {
    stopStep3AutoRefresh()
  }
}

watch(flowMainStep, syncStep3AutoRefresh)
watch([step3SourceList, backupTaskRows, resetTaskRows, restoreRecordRows], syncStep3AutoRefresh)


const drawerFilteredRestoreTasks = computed(() => {
  const sourceId = restoreDrawerSourceId.value
  if (!sourceId) return []
  return restoreRecordsForSource(sourceId)
    .map((record) => restoreRecordToFlowTask(record))
    .slice()
    .sort((a, b) => new Date(b.startedAt).getTime() - new Date(a.startedAt).getTime())
})

const drawerRunningRestoreTasks = computed(() => drawerFilteredRestoreTasks.value.filter((row) => row.status === 'running'))
const drawerHistoryRestoreTasks = computed(() => drawerFilteredRestoreTasks.value.filter((row) => row.status !== 'running'))
const drawerPagedHistoryRestoreTasks = computed(() => {
  const start = (restoreTaskPager.page - 1) * restoreTaskPager.pageSize
  return drawerHistoryRestoreTasks.value.slice(start, start + restoreTaskPager.pageSize)
})

watch(drawerHistoryRestoreTasks, (rows) => {
  const maxPage = Math.max(1, Math.ceil(rows.length / restoreTaskPager.pageSize))
  if (restoreTaskPager.page > maxPage) restoreTaskPager.page = maxPage
})

function restoreRecordForTaskRow(row: DemoFlowTask) {
  const match = /^restore-(\d+)$/.exec(row.id)
  const recordId = match ? Number(match[1]) : 0
  if (!recordId) return null
  return restoreRecordRows.value.find((record) => record.id === recordId) ?? null
}

function openRestoreTaskDetail(row: DemoFlowTask) {
  const record = restoreRecordForTaskRow(row)
  const source = drawerSourceRow.value
  if (!source || !record) {
    ElMessage.warning({ message: t('protection.backupsPage.flowRecordPendingDetailHint'), grouping: true })
    return
  }
  restoreTaskDrawerOpen.value = false
  openFlowSourceDetail(source, {
    tab: 'restoreRecords',
    restoreRecordId: record.id,
    restoreRecordTaskUuid: record.task_uuid,
  })
}

async function refreshTaskLists() {
  if (flowMainStep.value === 2 && step3ActionRefreshInFlight) return
  flowRefreshing.value = true
  try {
    await refreshFlowStepData()
  } catch (err) {
    showApiError(err)
  } finally {
    flowRefreshing.value = false
  }
}

function syncStep3TableSelection() {
  syncSelectionTable(step3TableRef.value, step3SourceList.value, step3SourceSelection.value.map((row) => row.id), 'step3')
}

async function clearStep3TableSelection() {
  step3SourceSelection.value = []
  // `reserve-selection` retains row keys independently from our reactive
  // selection state. Clear both now and after Vue applies the state change so
  // a concurrent list refresh cannot restore stale checked rows.
  step3TableRef.value?.clearSelection()
  await nextTick()
  step3TableRef.value?.clearSelection()
}

function onStep3SelectionChange(rows: FlowSourceRow[]) {
  if (syncingStep3Selection) return
  step3SourceSelection.value = rows
}

type DisplayNameEditRow = {
  source: FlowSourceRow
  originalName: string
  name: string
  error: string
}

const DISPLAY_NAME_EDIT_BATCH_SIZE = 5
const displayNameEditOpen = ref(false)
const displayNameEditSubmitting = ref(false)
const displayNameEditRows = ref<DisplayNameEditRow[]>([])

function selectedFlowRows(ids: string[]) {
  return ids
    .map((id) => backupSelectableById.value.get(id))
    .filter((row): row is FlowSourceRow => row != null)
}

function displayNameEditMaxLength(row: DisplayNameEditRow) {
  return row.source.type === 'host' ? 200 : 255
}

function displayNameEditError(row: DisplayNameEditRow) {
  if (!row.name.trim()) return t('protection.backupsPage.displayNameRequired')
  if (row.name.trim().length > displayNameEditMaxLength(row)) {
    return t('protection.backupsPage.displayNameTooLong', { n: displayNameEditMaxLength(row) })
  }
  return row.error
}

const displayNameEditCanSubmit = computed(() => {
  if (displayNameEditSubmitting.value || displayNameEditRows.value.length === 0) return false
  if (displayNameEditRows.value.some((row) => Boolean(displayNameEditError(row)))) return false
  return displayNameEditRows.value.some((row) => row.name.trim() !== row.originalName)
})

function sourceCanEditDisplayName(row: FlowSourceRow | undefined) {
  return Boolean(row && flowSourceRowSelectable(row) && !sourceResetRunning(row.id))
}

const step1DisplayNameEditEnabled = computed(() =>
  !displayNameEditSubmitting.value
  && selectedSourceIds.value.length > 0
  && selectedSourceIds.value.every((id) => sourceCanEditDisplayName(backupSelectableById.value.get(id))),
)

const step2DisplayNameEditEnabled = computed(() =>
  !displayNameEditSubmitting.value
  && step1Selection.value.length > 0
  && step1Selection.value.every((id) => sourceCanEditDisplayName(backupSelectableById.value.get(id))),
)

const step3DisplayNameEditEnabled = computed(() =>
  !displayNameEditSubmitting.value
  && step3SourceSelection.value.length > 0
  && step3SourceSelection.value.every((row) => sourceCanEditDisplayName(row)),
)

function openDisplayNameEditor(sources: FlowSourceRow[]) {
  if (!sources.length || displayNameEditSubmitting.value) return
  displayNameEditRows.value = sources.map((source) => ({
    source,
    originalName: source.name.trim(),
    name: source.name,
    error: '',
  }))
  displayNameEditOpen.value = true
}

function openStep1DisplayNameEditor() {
  if (!step1DisplayNameEditEnabled.value) return
  openDisplayNameEditor(selectedFlowRows(selectedSourceIds.value))
}

function openStep2DisplayNameEditor() {
  if (!step2DisplayNameEditEnabled.value) return
  openDisplayNameEditor(selectedFlowRows(step1Selection.value))
}

function openStep3DisplayNameEditor() {
  if (!step3DisplayNameEditEnabled.value) return
  openDisplayNameEditor(step3SourceSelection.value)
}

function closeDisplayNameEditor() {
  if (displayNameEditSubmitting.value) return
  displayNameEditOpen.value = false
  displayNameEditRows.value = []
}

function onDisplayNameInput(row: DisplayNameEditRow) {
  row.error = ''
}

async function updateBackupSourceDisplayName(row: DisplayNameEditRow) {
  const parsed = parseEndpointUiId(row.source.id)
  if (!parsed) throw new Error(t('protection.backupsPage.displayNameUpdateFailed'))
  const name = row.name.trim()
  if (row.source.type === 'host' && parsed.type === 'agent') {
    await updateNode(parsed.refId, { name })
    return
  }
  if (row.source.type === 'nas' && parsed.type === 'nas') {
    await updateSourceResource(parsed.refId, { name })
    return
  }
  throw new Error(t('protection.backupsPage.displayNameUpdateFailed'))
}

async function refreshAfterDisplayNameEdit(step: 0 | 1 | 2, sourceIds: string[]) {
  if (step === 2) {
    await refreshStep3AfterMoreAction({ focusIds: sourceIds })
    return
  }
  await refreshFlowStepData(step)
  await nextTick()
  if (step === 0) syncSourceTableSelection()
  else syncStep2TableSelection()
}

async function submitDisplayNameEdit() {
  if (!displayNameEditCanSubmit.value) return
  const candidates = displayNameEditRows.value.filter((row) => row.name.trim() !== row.originalName)
  const step = flowMainStep.value
  const succeeded = new Set<string>()

  displayNameEditSubmitting.value = true
  for (const row of candidates) row.error = ''
  try {
    for (let index = 0; index < candidates.length; index += DISPLAY_NAME_EDIT_BATCH_SIZE) {
      const batch = candidates.slice(index, index + DISPLAY_NAME_EDIT_BATCH_SIZE)
      const results = await Promise.allSettled(batch.map((row) => updateBackupSourceDisplayName(row)))
      results.forEach((result, resultIndex) => {
        const row = batch[resultIndex]
        if (!row) return
        if (result.status === 'fulfilled') {
          succeeded.add(row.source.id)
        } else {
          row.error = apiErrorMessageI18n(
            result.reason,
            t,
            t('protection.backupsPage.displayNameUpdateFailed'),
          )
        }
      })
    }

    if (succeeded.size > 0) {
      await refreshAfterDisplayNameEdit(step, Array.from(succeeded))
    }

    const failed = candidates.filter((row) => !succeeded.has(row.source.id))
    if (failed.length === 0) {
      displayNameEditOpen.value = false
      displayNameEditRows.value = []
      ElMessage.success({
        message: t('protection.backupsPage.displayNameUpdateSuccess', { n: succeeded.size }),
        grouping: true,
      })
      return
    }

    displayNameEditRows.value = failed
    if (succeeded.size > 0) {
      ElMessage.warning({
        message: t('protection.backupsPage.displayNameUpdatePartial', {
          done: succeeded.size,
          failed: failed.length,
        }),
        grouping: true,
      })
    } else {
      ElMessage.error({
        message: t('protection.backupsPage.displayNameUpdateAllFailed'),
        grouping: true,
      })
    }
  } finally {
    displayNameEditSubmitting.value = false
  }
}

const step3SourceActionsEnabled = computed(() => {
  if (!step3SourceSelection.value.length) return false
  if (step3SourceSelection.value.some((row) => sourceResetRunning(row.id))) return false
  return step3SourceSelection.value.some((row) => flowSourceCanOperate(row))
})
const step3LifecycleActionsEnabled = computed(() => {
  if (!step3SourceSelection.value.length) return false
  if (step3SourceSelection.value.some((row) => sourceResetRunning(row.id))) return false
  if (step3SourceSelection.value.some((row) => sourcePendingOps.isPending(row.id))) return false
  if (step3SourceSelection.value.some((row) => {
    const node = flowRowToApiNode(row)
    return node != null && lifecycleOps.isNodeBusy(node)
  })) return false
  return true
})
const step3SelectionEditable = step3LifecycleActionsEnabled
function sourceHasRunningBackupOrRestore(sourceId: string) {
  return sourceBackupRuntime(sourceId).running || sourceRestoreRuntime(sourceId).running
}

function selectionHasRunningBackupOrRestore(sourceIds: string[]) {
  return sourceIds.some(sourceHasRunningBackupOrRestore)
}

const step1UnregisterEnabled = computed(() => {
  if (selectedSourceIds.value.length === 0) return false
  return !selectionHasRunningBackupOrRestore(selectedSourceIds.value)
})

const step2UnregisterEnabled = computed(() => {
  if (step1Selection.value.length === 0) return false
  return !selectionHasRunningBackupOrRestore(step1Selection.value)
})

const step3UnregisterEnabled = computed(() => {
  if (!step3SelectionEditable.value) return false
  return !step3SourceSelection.value.some((row) => sourceHasRunningBackupOrRestore(row.id))
})
const step3CanStopBackup = computed(() =>
  step3SourceSelection.value.some((row) => sourceBackupRuntime(row.id).running),
)
const step3CanStopRestore = computed(() =>
  step3SourceSelection.value.some((row) => sourceRestoreRuntime(row.id).running),
)
const step3StopActionBusy = ref(false)
const selectedRecoverableSourceRows = computed(() =>
  step3SourceSelection.value.filter((row) => !sourceResetRunning(row.id) && sourceHasRecoverableSnapshot(row.id)),
)
const recoveryToolbarEnabled = computed(() => {
  if (selectedRecoverableSourceRows.value.length === 0) return false
  return !selectedRecoverableSourceRows.value.some((row) =>
    sourceRestoreRuntime(row.id).running || runtimeStopping(row.id, 'restore'),
  )
})
const startBackupSubmitting = ref(false)
const recoveryOpening = ref(false)
const stopConfirmOpen = ref(false)
const stopConfirmKind = ref<'backup' | 'restore'>('backup')
const stopConfirmItems = ref<ProtectionStopConfirmItem[]>([])

function step3StopBackupConfirmItems(): ProtectionStopConfirmItem[] {
  return step3SourceSelection.value
    .filter((row) => sourceBackupRuntime(row.id).running)
    .map((row) => ({
      name: row.name || row.hostname || row.id,
      description: formatTaskProgressPercent(sourceBackupRuntime(row.id).progress),
      hint: row.nodeIp || row.hostname || undefined,
    }))
}

function step3StopRestoreConfirmItems(): ProtectionStopConfirmItem[] {
  return step3SourceSelection.value
    .filter((row) => sourceRestoreRuntime(row.id).running)
    .map((row) => {
      const record = restoreRecordsForSource(row.id).find(restoreRecordIsRunning)
      return {
        name: row.name || row.hostname || row.id,
        hint: record?.target_path || undefined,
        description: formatTaskProgressPercent(sourceRestoreRuntime(row.id).progress),
      }
    })
}

function openStopBackupConfirmDialog() {
  if (step3StopActionBusy.value || !step3CanStopBackup.value) {
    if (!step3CanStopBackup.value) {
      ElMessage.info({ message: t('protection.backupsPage.msgStopBackupNoneRunning'), grouping: true })
    }
    return
  }
  stopConfirmKind.value = 'backup'
  stopConfirmItems.value = step3StopBackupConfirmItems()
  stopConfirmOpen.value = true
}

function openStopRestoreConfirmDialog() {
  if (step3StopActionBusy.value || !step3CanStopRestore.value) {
    if (!step3CanStopRestore.value) {
      ElMessage.info({ message: t('protection.backupsPage.msgStopRestoreNoneRunning'), grouping: true })
    }
    return
  }
  stopConfirmKind.value = 'restore'
  stopConfirmItems.value = step3StopRestoreConfirmItems()
  stopConfirmOpen.value = true
}

async function onConfirmStopDialog() {
  stopConfirmOpen.value = false
  if (stopConfirmKind.value === 'backup') {
    await stopSelectedBackupTasks()
    return
  }
  await stopSelectedRestoreTasks()
}

async function stopSelectedBackupTasks() {
  if (step3StopActionBusy.value || !step3CanStopBackup.value) {
    if (!step3CanStopBackup.value) {
      ElMessage.info({ message: t('protection.backupsPage.msgStopBackupNoneRunning'), grouping: true })
    }
    return
  }
  step3StopActionBusy.value = true
  let stopped = 0
  try {
    const targets = step3SourceSelection.value.filter((row) => sourceBackupRuntime(row.id).running)
    for (const row of targets) {
      const task = runningBackupTaskForSource(row.id)
      if (!task?.task_uuid) continue
      markSourceStopPhase(row.id, 'backup')
      await cancelProtectionBackupTask(task.task_uuid)
      stopped += 1
    }
    await refreshStep3AfterMoreAction()
    for (const row of targets) syncSourceStopOptimisticFromRuntime(row.id, 'backup')
    if (stopped > 0) {
      ElMessage.success({ message: t('protection.backupsPage.msgStopBackupRequested', { n: stopped }), grouping: true })
    }
  } catch (err) {
    showApiError(err, 'Failed to stop backup task')
  } finally {
    step3StopActionBusy.value = false
  }
}

async function stopSelectedRestoreTasks() {
  if (step3StopActionBusy.value || !step3CanStopRestore.value) {
    if (!step3CanStopRestore.value) {
      ElMessage.info({ message: t('protection.backupsPage.msgStopRestoreNoneRunning'), grouping: true })
    }
    return
  }
  step3StopActionBusy.value = true
  let stopped = 0
  try {
    const targets = step3SourceSelection.value.filter((row) => sourceRestoreRuntime(row.id).running)
    for (const row of targets) {
      const task = runningRestoreTaskForSource(row.id)
      if (!task?.task_uuid) continue
      markSourceStopPhase(row.id, 'restore')
      await cancelProtectionRestoreTask(task.task_uuid)
      stopped += 1
    }
    await refreshStep3AfterMoreAction()
    for (const row of targets) syncSourceStopOptimisticFromRuntime(row.id, 'restore')
    if (stopped > 0) {
      ElMessage.success({ message: t('protection.backupsPage.msgStopRestoreRequested', { n: stopped }), grouping: true })
    }
  } catch (err) {
    showApiError(err, 'Failed to stop restore task')
  } finally {
    step3StopActionBusy.value = false
  }
}

const restoreTaskSelection = ref<DemoFlowTask[]>([])
const deleteRestoreTasksDialogOpen = ref(false)
const pendingDeleteRestoreTasks = ref<DemoFlowTask[]>([])

const deleteRestoreTaskItems = computed<DangerConfirmItem[]>(() =>
  pendingDeleteRestoreTasks.value.map((row) => ({
    key: row.id,
    name: row.title,
    status: {
      label: flowTaskStatusLabel(row.status),
      tone: row.status === 'completed' ? 'success' : row.status === 'failed' ? 'danger' : 'info',
    },
    description: fmtLocalTime(row.startedAt),
  })),
)

function onBackupTaskSelection(rows: FlowSourceRow[]) {
  onStep3SelectionChange(rows)
}



async function startSelectedBackupTasks() {
  const sources = step3SourceSelection.value
  if (!sources.length) {
    ElMessage.warning({ message: t('protection.backupsPage.msgSelectSourcesToStart'), grouping: true })
    return
  }
  const offlineRows = offlineFlowSourcesFromIds(sources.map((source) => source.id))
  if (offlineRows.length > 0) {
    ElMessage.warning(
      formatOfflineBackupPlanMessage(offlineRows, t, {
        hostOfflineKey: 'protection.backupsPage.msgStartBackupHostOffline',
        nasOfflineKey: 'protection.backupsPage.msgStartBackupNasOffline',
      }),
    )
    return
  }
  if (startBackupSubmitting.value) {
    return
  }
  startBackupSubmitting.value = true
  try {
    await refreshStep3State()
    const runnableSources = sources.filter((source) => sourceHasBackupConfig(source.id))
    if (!runnableSources.length) {
      ElMessage.warning({ message: t('protection.backupsPage.msgSourceNoBackupConfig'), grouping: true })
      return
    }
    const result = await startProtectionBackupTasks(startBackupTaskPayloadForSources(runnableSources))
    await refreshStep3State()
    if (result.created_count > 0) {
      ElMessage.success({
        message: t('protection.backupsPage.msgStartBackupIncrementalHint'),
        grouping: true,
      })
      return
    }
    if (result.results.some((item) => item.status === 'conflict')) {
      ElMessage.info({ message: t('protection.backupsPage.msgStartBackupAllRunning'), grouping: true })
      return
    }
    const failed = result.results.find((item) => item.status === 'failed' || item.status === 'skipped')
    if (failed?.message) {
      ElMessage.warning({ message: failed.message, grouping: true })
      return
    }
    ElMessage.warning({ message: t('protection.backupsPage.msgSourceNoBackupConfig'), grouping: true })
  } catch (err) {
    showApiError(err, 'Failed to start backup task')
  } finally {
    startBackupSubmitting.value = false
  }
}

function flowRowsForSourceIds(ids: string[]): FlowSourceRow[] {
  return ids
    .map((id) => backupSelectableById.value.get(id) || flowRowFromSourceId(id))
    .filter((row): row is FlowSourceRow => row != null)
}

function flowRowToApiNode(row: FlowSourceRow): ApiNode | null {
  if (row.type !== 'host') return null
  const parsed = parseEndpointUiId(row.id)
  if (!parsed || parsed.type !== 'agent') return null
  return {
    id: parsed.refId,
    organization: 0,
    name: row.name,
    role: 'agent',
    status: row.status,
    availability: row.availability,
    routable: row.availability === 'online',
    version: '',
    is_deleted: false,
    ip_address: row.nodeIp || null,
  }
}

type FlowSourceDisplayStatus = {
  label: string
  tag: 'success' | 'warning' | 'danger' | 'info' | 'neutral'
  spinning?: boolean
  clickable?: boolean
}

type BackupSourceDeleteDisplayRow = {
  id: string
  name: string
  type?: string
  sourceType?: FlowSourceRow['type']
  platform?: FlowSourceRow['platform']
  protocol?: FlowSourceRow['protocol']
  statusLabel?: string
  statusTag?: 'success' | 'warning' | 'danger' | 'info' | 'neutral'
  registeredAt?: string
  snapshotCount?: string | number
}

function resolveFlowSourceDisplayStatus(row: FlowSourceRow): FlowSourceDisplayStatus {
  const pendingLabel = sourcePendingOps.rowPendingLabel(row.id)
  if (pendingLabel) {
    const op = sourcePendingOps.getOp(row.id)
    return {
      label: pendingLabel,
      tag: op?.kind === 'delete_failed' ? 'danger' : 'warning',
      spinning: sourcePendingOps.rowPendingSpinning(row.id),
      clickable: op?.kind === 'delete_failed' || op?.kind === 'delete_waiting' || op?.kind === 'delete_blocked',
    }
  }

  const hostNode = flowRowToApiNode(row)
  if (hostNode) {
    const lifecycle = backupSourceLifecycleDisplay(lifecycleOps.resolveDisplayStatus(hostNode))
    return {
      label: te(lifecycle.labelKey) ? t(lifecycle.labelKey) : lifecycle.labelKey,
      tag: lifecycle.tagType,
      spinning: lifecycle.spinning,
    }
  }

  const ready = flowSourceReadyStatus(row, {
    registered: t('protection.sourceResources.lifecycleRegistered'),
  })
  return { label: ready.label, tag: ready.tag }
}

function flowSourceDeleteDisplayRow(row: FlowSourceRow, options: { includeSnapshots?: boolean } = {}): BackupSourceDeleteDisplayRow {
  const status = resolveFlowSourceDisplayStatus(row)
  return {
    id: row.id,
    name: row.name,
    type: row.type === 'host'
      ? t('protection.backupsPage.sourceTypeHost')
      : t('protection.backupsPage.sourceTypeNas'),
    sourceType: row.type,
    platform: row.platform,
    protocol: row.protocol,
    statusLabel: status.label,
    statusTag: status.tag,
    registeredAt: flowSourceRegisteredAt(row.registeredAt),
    snapshotCount: options.includeSnapshots ? sourceResetSnapshotCountText(row.id) : undefined,
  }
}

function flowSourceRowSelectable(row: FlowSourceRow) {
  if (!sourcePendingOps.isRowSelectable(row.id)) return false
  const hostNode = flowRowToApiNode(row)
  if (hostNode && lifecycleOps.isNodeBusy(hostNode)) return false
  return true
}

function openSourcePendingFailureDetails(sourceId: string) {
  const op = sourcePendingOps.getOp(sourceId)
  if (op?.kind !== 'delete_failed') {
    if (op?.taskUuid) openTaskDetail(op.taskUuid)
    return
  }
  if (op.failureDetails) {
    openUnregisterFailureDetails(op.failureDetails)
    return
  }
  const row = flowRowsForSourceIds([sourceId])[0]
  openUnregisterFailureDetails(unregisterFailureToErrorDetails({
    t,
    sourceId,
    sourceName: row?.name || sourceId,
    fallbackMessage: op.errorMessage || t('protection.backupsPage.msgDeleteSourceFailed'),
  }))
}

const backupSourceDeleteDialogOpen = ref(false)
const backupSourceStep3DeleteDialogOpen = ref(false)
const backupSourceDeleteIds = ref<string[]>([])
const backupSourceDeleteRows = ref<BackupSourceDeleteDisplayRow[]>([])
const backupSourceDeleteShowSnapshots = ref(false)
const backupSourceDeletePreviousFailure = computed(() =>
  previousUnregisterFailureDetails(
    t,
    backupSourceDeleteIds.value.flatMap((sourceId) => {
      const op = sourcePendingOps.getOp(sourceId)
      return op?.kind === 'delete_failed' && op.failureDetails ? [op.failureDetails] : []
    }),
  ),
)
const UNREGISTER_TASK_POLL_MS = 2000
const UNREGISTER_BLOCKED_TASK_POLL_MS = 30_000
const UNREGISTER_TASK_POLL_TIMEOUT_MS = 15 * 60 * 1000
const unregisterTaskPollTimers = new Set<number>()
let unregisterTaskPollingStopped = false

function scheduleUnregisterTaskPoll(callback: () => void) {
  if (unregisterTaskPollingStopped) return
  const timer = window.setTimeout(() => {
    unregisterTaskPollTimers.delete(timer)
    if (unregisterTaskPollingStopped) return
    callback()
  }, UNREGISTER_TASK_POLL_MS)
  unregisterTaskPollTimers.add(timer)
}

function stopUnregisterTaskPolls() {
  unregisterTaskPollingStopped = true
  for (const timer of unregisterTaskPollTimers) window.clearTimeout(timer)
  unregisterTaskPollTimers.clear()
}

function monitorPendingUnregister(
  sourceIds: string[],
  taskUuids: string[],
  monitorStartedAt = Date.now(),
) {
  const pairs = taskUuids
    .map((taskUuid, index) => ({ taskUuid, sourceId: sourceIds[index] || '' }))
    .filter((item) => item.taskUuid && item.sourceId)
  if (!pairs.length) return
  if (pairs.length > 1) {
    pairs.forEach((pair) => {
      monitorPendingUnregister([pair.sourceId], [pair.taskUuid], monitorStartedAt)
    })
    return
  }
  const startedAt = monitorStartedAt

  const poll = async () => {
    if (unregisterTaskPollingStopped) return
    let tasks: TaskRow[]
    try {
      tasks = await Promise.all(pairs.map(({ taskUuid }) => getTask(taskUuid)))
    } catch {
      if (unregisterTaskPollingStopped) return
      if (Date.now() - startedAt < UNREGISTER_TASK_POLL_TIMEOUT_MS) {
        scheduleUnregisterTaskPoll(() => { void poll() })
        return
      }
      const timeoutNotices = sourceIds.map((sourceId) => {
        const sourceName = flowRowsForSourceIds([sourceId])[0]?.name || sourceId
        const details = unregisterFailureToErrorDetails({
          t,
          sourceId,
          sourceName,
          fallbackMessage: t('protection.backupsPage.msgDeleteSourceFailed'),
        })
        sourcePendingOps.mark(
          [sourceId],
          {
            kind: 'delete_failed',
            errorMessage: unregisterFailureSummaryLine(details),
            failureDetails: details,
          },
          flowRowsForSourceIds([sourceId]),
        )
        return { sourceId, sourceName, details }
      })
      notifyUnregisterFailureBatch({
        t,
        items: timeoutNotices,
        dedupeKey: `unregister-timeout-batch:${sourceIds.join(',')}`,
      })
      return
    }

    if (unregisterTaskPollingStopped) return

    const terminalStatuses = new Set(['success', 'failed', 'cancelled', 'timeout'])
    const nonTerminalTasks = tasks.filter(
      (task) => !terminalStatuses.has(String(task.status || '').toLowerCase()),
    )
    if (nonTerminalTasks.length) {
      pairs.forEach((pair, index) => {
        const task = tasks[index]
        sourcePendingOps.mark(
          [pair.sourceId],
          {
            kind: sourceUnregisterPendingKind(task.status),
            taskId: task.id,
            taskUuid: task.task_uuid,
            startedAt,
          },
          flowRowsForSourceIds([pair.sourceId]),
        )
      })
      const allBlocked = nonTerminalTasks.every(
        (task) => String(task.status || '').toLowerCase() === 'blocked',
      )
      const delay = allBlocked ? UNREGISTER_BLOCKED_TASK_POLL_MS : UNREGISTER_TASK_POLL_MS
      const timer = window.setTimeout(() => {
        unregisterTaskPollTimers.delete(timer)
        void poll()
      }, delay)
      unregisterTaskPollTimers.add(timer)
      return
    }

    const outcomes = tasks.map(sourceUnregisterTaskOutcome)
    const cancelledSourceIds = pairs.flatMap((pair, index) =>
      outcomes[index]?.status === 'cancelled' ? [pair.sourceId] : [],
    )
    const failedSourceIds = pairs.flatMap((pair, index) =>
      outcomes[index]?.success || outcomes[index]?.status === 'cancelled'
        ? []
        : [pair.sourceId],
    )
    const pendingRemovals = outcomes.flatMap((outcome) => outcome.pendingRemovals)
    const pendingRemovalIds = new Set(pendingRemovals.map((item) => item.source_id))
    const completedSourceIds = sourceIds.filter((id) => !failedSourceIds.includes(id))
    const cleanupNotices: Array<{ sourceId: string; sourceName: string; details: ErrorDetailsPayload }> = []
    completedSourceIds
      .filter((id) => !pendingRemovalIds.has(id))
      .forEach((sourceId) => {
        const index = pairs.findIndex((pair) => pair.sourceId === sourceId)
        const outcome = outcomes[index]
        if (!outcome?.partialSuccess) return
        const sourceName = flowRowsForSourceIds([sourceId])[0]?.name || sourceId
        cleanupNotices.push({
          sourceId,
          sourceName,
          details: unregisterFailureToErrorDetails({
            t,
            sourceId,
            sourceName,
            task: tasks[index],
            outcome,
            partialSuccess: true,
          }),
        })
      })
    sourcePendingOps.clear([
      ...completedSourceIds.filter((id) => !pendingRemovalIds.has(id)),
      ...cancelledSourceIds,
    ])
    sourcePendingOps.transitionToRemoving(pendingRemovals)
    const failureNotices: Array<{ sourceId: string; sourceName: string; details: ErrorDetailsPayload }> = []
    if (failedSourceIds.length) {
      failedSourceIds.forEach((sourceId) => {
        const index = pairs.findIndex((pair) => pair.sourceId === sourceId)
        const outcome = outcomes[index]
        const task = tasks[index]
        const sourceName = flowRowsForSourceIds([sourceId])[0]?.name || sourceId
        const details = unregisterFailureToErrorDetails({
          t,
          sourceId,
          sourceName,
          task,
          outcome,
        })
        sourcePendingOps.mark(
          [sourceId],
          {
            kind: 'delete_failed',
            errorMessage: unregisterFailureSummaryLine(details),
            failureDetails: details,
          },
          flowRowsForSourceIds([sourceId]),
        )
        failureNotices.push({ sourceId, sourceName, details })
      })
    }
    notifyUnregisterFailureBatch({
      t,
      items: failureNotices,
      dedupeKey: `unregister-failure-batch:${failureNotices.map((item) => item.sourceId).join(',')}`,
    })
    notifyUnregisterCleanupWarningBatch({
      t,
      items: cleanupNotices,
      dedupeKey: `unregister-cleanup-warning-batch:${cleanupNotices.map((item) => item.sourceId).join(',')}`,
    })
    try {
      await Promise.all([
        loadBackupSelectable({ silent: true }),
        refreshStep3AfterMoreAction({ preserveSelection: false }),
      ])
    } catch (err) {
      if (!pageRequests.isAbortError(err)) showApiError(err)
    }
  }

  void poll()
}

function resumePendingUnregisterMonitors() {
  for (const { sourceId, taskUuid, startedAt } of sourcePendingOps.pendingDeleteTasks()) {
    monitorPendingUnregister([sourceId], [taskUuid], startedAt || Date.now())
  }
}

function affectedBackupIdsForSources(sourceIds: string[]): Set<string> {
  const ids = new Set<string>()
  for (const sourceId of sourceIds) {
    for (const backupId of backupIdsForSource(sourceId)) {
      ids.add(backupId)
    }
    for (const configId of sourceBackupConfigIds(sourceId)) {
      ids.add(realBackupId(configId))
    }
  }
  return ids
}

function clearFlowTasksForSources(sourceIds: string[]) {
  if (!sourceIds.length) return
  const affectedBackupIds = affectedBackupIdsForSources(sourceIds)
  if (!affectedBackupIds.size) return
  backupFlowTasks.value = backupFlowTasks.value.filter(
    (task) => !task.backupId || !affectedBackupIds.has(task.backupId),
  )
  restoreFlowTasks.value = restoreFlowTasks.value.filter(
    (task) => !task.backupId || !affectedBackupIds.has(task.backupId),
  )
}

function clearLocalStateAfterDelete(idSet: Set<string>) {
  const sourceIds = Array.from(idSet)
  clearFlowTasksForSources(sourceIds)
  store.removeSources(sourceIds)
  if (activeFlowSource.value && idSet.has(activeFlowSource.value.id)) {
    flowSourceDetailOpen.value = false
    onFlowSourceDetailClosed()
  }
  if (restoreDrawerSourceId.value && idSet.has(restoreDrawerSourceId.value)) {
    restoreTaskDrawerOpen.value = false
    restoreDrawerSourceId.value = null
  }
  step3SourceSelection.value = step3SourceSelection.value.filter((row) => !idSet.has(row.id))
  selectedSourceIds.value = selectedSourceIds.value.filter((id) => !idSet.has(id))
  step1Selection.value = step1Selection.value.filter((id) => !idSet.has(id))
}

function openBackupSourceDeleteDialog(
  ids: string[],
  options: { rows?: FlowSourceRow[]; showSnapshots?: boolean } = {},
) {
  const realIds = ids.filter(isBackupSelectableId)
  if (!realIds.length) {
    ElMessage.warning({ message: t('protection.backupsPage.msgSelectSourcesToDelete'), grouping: true })
    return
  }
  const sourceRows = options.rows || flowRowsForSourceIds(realIds)
  backupSourceDeleteIds.value = realIds
  backupSourceDeleteShowSnapshots.value = Boolean(options.showSnapshots)
  backupSourceDeleteRows.value = sourceRows.map((row) =>
    flowSourceDeleteDisplayRow(row, { includeSnapshots: backupSourceDeleteShowSnapshots.value }),
  )
  backupSourceDeleteDialogOpen.value = true
}

function openBackupSourceStep3DeleteDialog(ids: string[], rows: FlowSourceRow[] = []) {
  const realIds = ids.filter(isBackupSelectableId)
  if (!realIds.length) {
    ElMessage.warning({ message: t('protection.backupsPage.msgSelectSourcesToDelete'), grouping: true })
    return
  }
  const sourceRows = rows.length ? rows : flowRowsForSourceIds(realIds)
  backupSourceDeleteIds.value = realIds
  backupSourceDeleteShowSnapshots.value = true
  backupSourceDeleteRows.value = sourceRows.map((row) =>
    flowSourceDeleteDisplayRow(row, { includeSnapshots: true }),
  )
  backupSourceStep3DeleteDialogOpen.value = true
}

const resetBackupFromStep3Submitting = ref(false)
const resetBackupConfigDialogOpen = ref(false)
const resetBackupConfigConfirmText = ref('')
const pendingResetSources = ref<FlowSourceRow[]>([])
/** Reset source ids queued this session — survives step3 pagination so completion can refresh pipeline. */
const pendingResetPipelineSourceIds = ref<string[]>([])
const resetSnapshotCountBySourceId = ref(new Map<string, number>())
const resetSnapshotCountLoading = ref(false)
let resetSnapshotCountRequestSeq = 0

function trackResetPipelineSources(ids: string[]) {
  pendingResetPipelineSourceIds.value = normalizeSourceIdList([
    ...pendingResetPipelineSourceIds.value,
    ...ids,
  ])
}

function untrackResetPipelineSources(ids: string[]) {
  if (!ids.length) return
  const done = new Set(ids)
  pendingResetPipelineSourceIds.value = pendingResetPipelineSourceIds.value.filter((id) => !done.has(id))
}

function collectResetTrackedIds() {
  return normalizeSourceIdList([
    ...pendingResetPipelineSourceIds.value,
    ...step3SourceList.value
      .filter((row) => Boolean(sourceResetState(row.id)))
      .map((row) => row.id),
  ])
}

const resetBackupConfigDisplayRows = computed(() =>
  pendingResetSources.value.map((row) =>
    flowSourceDeleteDisplayRow(row, { includeSnapshots: true }),
  ),
)

function sourceResetSnapshotCountFallback(sourceId: string) {
  return snapshotsForBackupConfigIds(sourceBackupConfigIds(sourceId)).length
}

function sourceResetSnapshotCountText(sourceId: string) {
  const count = resetSnapshotCountBySourceId.value.get(sourceId)
  if (typeof count === 'number') return String(count)
  return resetSnapshotCountLoading.value ? '—' : String(sourceResetSnapshotCountFallback(sourceId))
}

async function loadResetSnapshotCounts(sources: FlowSourceRow[]) {
  const seq = ++resetSnapshotCountRequestSeq
  resetSnapshotCountBySourceId.value = new Map()
  if (!sources.length) {
    resetSnapshotCountLoading.value = false
    return
  }
  resetSnapshotCountLoading.value = true
  try {
    const entries = await Promise.all(sources.map(async (source) => {
      const configIds = sourceBackupConfigIds(source.id)
      if (!configIds.length) return [source.id, 0] as const
      const counts = await Promise.all(configIds.map(async (backupConfigId) => {
        const result = await listBackupSourceSnapshots({
          backup_config_id: backupConfigId,
          page: 1,
          page_size: 1,
        })
        return result.count
      }))
      return [source.id, counts.reduce((sum, count) => sum + count, 0)] as const
    }))
    if (seq !== resetSnapshotCountRequestSeq) return
    resetSnapshotCountBySourceId.value = new Map(entries)
  } catch {
    if (seq !== resetSnapshotCountRequestSeq) return
    resetSnapshotCountBySourceId.value = new Map(
      sources.map((source) => [source.id, sourceResetSnapshotCountFallback(source.id)]),
    )
  } finally {
    if (seq === resetSnapshotCountRequestSeq) resetSnapshotCountLoading.value = false
  }
}

function clearResetBackupConfigDialogState() {
  resetBackupConfigConfirmText.value = ''
  resetSnapshotCountRequestSeq += 1
  resetSnapshotCountBySourceId.value = new Map()
  resetSnapshotCountLoading.value = false
}

function resetSelectedBackupConfigurations() {
  const sources = step3SourceSelection.value
  if (!sources.length) {
    ElMessage.warning({ message: t('protection.backupsPage.msgSelectSourcesToRevert'), grouping: true })
    return
  }
  if (sources.some((source) => sourceResetRunning(source.id))) {
    ElMessage.warning({ message: t('protection.backupsPage.msgResetBackupConfigRunning'), grouping: true })
    return
  }
  pendingResetSources.value = [...sources]
  void loadResetSnapshotCounts(pendingResetSources.value)
  resetBackupConfigConfirmText.value = ''
  resetBackupConfigDialogOpen.value = true
}

async function confirmResetBackupConfiguration() {
  const sources = pendingResetSources.value
  if (!sources.length || resetBackupConfigConfirmText.value !== BACKUP_SOURCE_RESET_CONFIRMATION) return
  const sourceIds = new Set(sources.map((source) => source.id))
  resetBackupFromStep3Submitting.value = true
  try {
    const sourceIdList = sources.map((source) => source.id).filter(isBackupSelectableId)
    const result = await resetBackupConfigs(sourceIdList, BACKUP_SOURCE_RESET_CONFIRMATION)
    trackResetPipelineSources(sourceIdList)

    if (activeFlowSource.value && sourceIds.has(activeFlowSource.value.id)) {
      flowSourceDetailOpen.value = false
      onFlowSourceDetailClosed()
    }
    if (restoreDrawerSourceId.value && sourceIds.has(restoreDrawerSourceId.value)) {
      restoreTaskDrawerOpen.value = false
      restoreDrawerSourceId.value = null
    }

    await clearStep3TableSelection()
    await refreshStep3AfterMoreAction({ preserveSelection: false })
    resetBackupConfigDialogOpen.value = false
    pendingResetSources.value = []
    resetBackupConfigConfirmText.value = ''
    ElMessage.success({ message: t('protection.backupsPage.msgResetBackupConfigQueued', { n: result.created_count }), grouping: true })
  } catch (err) {
    showApiError(err, 'Failed to reset backup configuration')
  } finally {
    resetBackupFromStep3Submitting.value = false
  }
}

async function onBackupSourcesDeleted(payload: {
  result: string
  warnings: Array<Record<string, unknown>>
  pending_removals?: Array<{ source_id: string; node_id: number }>
  task_id?: number
  task_uuid?: string
  task_ids?: number[]
  task_uuids?: string[]
  tasks?: Array<{ source_id: string; task_id: number; task_uuid: string }>
  rejected?: BackupSourceDeleteResult['rejected']
  accepted?: boolean
}) {
  const idSet = new Set(backupSourceDeleteIds.value)
  const deletedRows = flowRowsForSourceIds([...idSet])
  const rejectedBySourceId = new Map(
    (payload.rejected || []).map((item) => [item.source_id, item]),
  )
  const acceptedIdSet = new Set(
    [...idSet].filter((sourceId) => !rejectedBySourceId.has(sourceId)),
  )
  const fromStep3 = backupSourceStep3DeleteDialogOpen.value || flowMainStep.value === 2
  if (
    (payload.accepted && payload.result === 'pending')
    || ['failed', 'partial_failure'].includes(payload.result)
  ) {
    try {
      if (fromStep3) {
        await clearStep3TableSelection()
        await Promise.all([
          loadBackupSelectable({ silent: true }),
          refreshStep3AfterMoreAction({ preserveSelection: false }),
        ])
      } else {
        await Promise.all([
          refreshPipelineStep2PlusIds(),
          refreshPipelineStep3Ids(),
          refreshBackupConfigs(),
        ])
        if (flowMainStep.value === 0) void loadBackupSelectable()
      }
      const sourceIds = Array.from(idSet)
      const bindings = sourceUnregisterTaskBindings(sourceIds, payload)
      const monitoredSourceIds = bindings.map((binding) => binding.sourceId)
      const monitoredTaskUuids = bindings.map((binding) => binding.taskUuid)
      const bySourceId = new Map(bindings.map((binding) => [binding.sourceId, binding]))
      const rejectedBySourceId = new Map(
        (payload.rejected || []).map((item) => [item.source_id, item]),
      )
      const unboundNotices: Array<{ sourceId: string; sourceName: string; details: ErrorDetailsPayload }> = []
      sourceIds.forEach((sourceId) => {
        const binding = bySourceId.get(sourceId)
        if (binding) {
          sourcePendingOps.mark(
            [sourceId],
            {
              kind: sourceUnregisterPendingKind(binding.status),
              taskId: binding.taskId,
              taskUuid: binding.taskUuid,
              startedAt: Date.now(),
            },
            flowRowsForSourceIds([sourceId]),
          )
          return
        }
        const sourceName = flowRowsForSourceIds([sourceId])[0]?.name || sourceId
        const rejection = rejectedBySourceId.get(sourceId)
        const details = unregisterFailureToErrorDetails({
          t,
          sourceId,
          sourceName,
          apiError: rejection
            ? { message: t('protection.backupsPage.msgDeleteSourceFailed'), reasons: rejection.reasons }
            : undefined,
          fallbackMessage: t('protection.backupsPage.msgDeleteSourceFailed'),
        })
        sourcePendingOps.mark(
          [sourceId],
          {
            kind: 'delete_failed',
            errorMessage: unregisterFailureSummaryLine(details),
            failureDetails: details,
          },
          flowRowsForSourceIds([sourceId]),
        )
        unboundNotices.push({ sourceId, sourceName, details })
      })
      notifyUnregisterFailureBatch({
        t,
        items: unboundNotices,
        dedupeKey: `unregister-unbound-batch:${unboundNotices.map((item) => item.sourceId).join(',')}`,
      })
      monitorPendingUnregister(monitoredSourceIds, monitoredTaskUuids)
      if (payload.result === 'pending') {
        ElMessage.info({ message: t('protection.backupsPage.msgDeleteSourcePending'), grouping: true })
      }
    } catch (err) {
      sourcePendingOps.clear(Array.from(idSet))
      showApiError(err, t('protection.backupsPage.msgDeleteSourceFailed'))
    } finally {
      backupSourceDeleteIds.value = []
      backupSourceDeleteRows.value = []
      backupSourceDeleteShowSnapshots.value = false
    }
    return
  }
  try {
    clearLocalStateAfterDelete(acceptedIdSet)
    const rejectedNotices = [...rejectedBySourceId].map(([sourceId, rejection]) => {
      const sourceName = deletedRows.find((row) => row.id === sourceId)?.name || sourceId
      const details = unregisterFailureToErrorDetails({
        t,
        sourceId,
        sourceName,
        apiError: {
          message: t('protection.backupsPage.msgDeleteSourceFailed'),
          reasons: rejection.reasons,
        },
      })
      sourcePendingOps.mark(
        [sourceId],
        {
          kind: 'delete_failed',
          errorMessage: unregisterFailureSummaryLine(details),
          failureDetails: details,
        },
        flowRowsForSourceIds([sourceId]),
      )
      return { sourceId, sourceName, details }
    })
    notifyUnregisterFailureBatch({
      t,
      items: rejectedNotices,
      dedupeKey: `unregister-rejected-batch:${rejectedNotices.map((item) => item.sourceId).join(',')}`,
    })
    if (fromStep3) {
      await clearStep3TableSelection()
      await Promise.all([
        loadBackupSelectable({ silent: !!payload.pending_removals?.length }),
        refreshStep3AfterMoreAction({ preserveSelection: false }),
      ])
    } else {
      await Promise.all([
        loadBackupSelectable({ silent: !!payload.pending_removals?.length }),
        refreshPipelineStep2PlusIds(),
        refreshPipelineStep3Ids(),
        refreshBackupConfigs(),
      ])
    }
    if (payload.pending_removals?.length) {
      const removalNodes: ApiNode[] = payload.pending_removals.map((item) => {
        const row = deletedRows.find((entry) => entry.id === item.source_id)
        const fromRow = row ? flowRowToApiNode(row) : null
        if (fromRow) return fromRow
        return {
          id: item.node_id,
          organization: 0,
          name: row?.name || `#${item.node_id}`,
          role: 'agent',
          status: 'offline',
          routable: false,
          version: '',
          is_deleted: false,
          ip_address: row?.nodeIp || null,
        }
      })
      lifecycleOps.trackPendingRemovals(
        payload.pending_removals.map((row) => ({ nodeId: row.node_id })),
        removalNodes,
      )
      sourcePendingOps.transitionToRemoving(payload.pending_removals)
      ElMessage.info({ message: t('protection.backupsPage.msgDeleteSourcePending'), grouping: true })
    } else {
      sourcePendingOps.clear(Array.from(acceptedIdSet))
      if (payload.result === 'partial_success' && payload.warnings.length) {
        const sourceIds = Array.from(acceptedIdSet)
        const items = sourceIds.map((sourceId) => {
          const sourceName = deletedRows.find((row) => row.id === sourceId)?.name || sourceId
          return {
            sourceId,
            sourceName,
            details: unregisterFailureToErrorDetails({
              t,
              sourceId,
              sourceName,
              partialSuccess: true,
              warnings: warningsForUnregisterSource(payload.warnings, sourceId),
            }),
          }
        })
        if (!items.length) {
          notifyUnregisterCleanupWarning({
            t,
            warnings: payload.warnings,
            dedupeKey: 'unregister-sync-cleanup-warning:bulk',
          })
        } else {
          notifyUnregisterCleanupWarningBatch({
            t,
            items,
            dedupeKey: `unregister-sync-cleanup-warning:${sourceIds.join(',')}`,
          })
        }
      } else if (!rejectedNotices.length) {
        ElMessage.success({ message: t('protection.backupsPage.msgDeleteSourceSuccess'), grouping: true })
      }
    }
  } catch (err) {
    sourcePendingOps.clear(Array.from(idSet))
    showApiError(err, t('protection.backupsPage.msgDeleteSourceFailed'))
    await loadBackupSelectable()
  } finally {
    backupSourceDeleteIds.value = []
    backupSourceDeleteRows.value = []
    backupSourceDeleteShowSnapshots.value = false
  }
}

function deleteSelectedSourcesFromStep1() {
  openBackupSourceDeleteDialog(selectedSourceIds.value)
}

function deleteSelectedSourcesFromStep2() {
  openBackupSourceDeleteDialog(step1Selection.value)
}

function deleteSelectedSourcesFromStep3() {
  openBackupSourceStep3DeleteDialog(
    step3SourceSelection.value.map((row) => row.id),
    step3SourceSelection.value,
  )
}

function revertSelectedSourcesFromStep3() {
  resetSelectedBackupConfigurations()
}


function confirmDeleteRestoreTasks() {
  const rows = pendingDeleteRestoreTasks.value
  if (!rows.length) return
  const ids = new Set(rows.map((r) => r.id))
  restoreFlowTasks.value = restoreFlowTasks.value.filter((r) => !ids.has(r.id))
  restoreTaskSelection.value = []
  pendingDeleteRestoreTasks.value = []
  deleteRestoreTasksDialogOpen.value = false
  ElMessage.success({ message: t('protection.backupsPage.msgDeletedTasksDemo'), grouping: true })
}

const editOpen = ref(false)
const editRow = ref<DemoBackup | null>(null)
const editName = ref('')
const editRemark = ref('')
const editPolicy = ref('')
const editGlobalFilter = ref('')

function openEdit(b: DemoBackup) {
  editRow.value = b
  editName.value = b.name
  editRemark.value = b.remark
  editPolicy.value = b.policyId
  editGlobalFilter.value = b.globalFilterId ?? ''
  editOpen.value = true
}

const backupPickOpen = ref(false)
const backupPickCandidates = ref<DemoBackup[]>([])

function onBackupPickSelected(backup: DemoBackup) {
  backupPickOpen.value = false
  openEdit(backup)
}






function saveEdit() {
  if (!editRow.value) return
  const id = editRow.value.id
  const nextName = editName.value.trim() || editRow.value.name
  store.updateBackup(id, {
    name: nextName,
    remark: editRemark.value.trim(),
    policyId: editPolicy.value,
    globalFilterId: editGlobalFilter.value,
  })
  for (const row of backupFlowTasks.value) {
    if (row.backupId === id) row.title = `${t('protection.backupsPage.flowTaskKindBackupCreate')} · ${nextName}`
  }
  for (const row of restoreFlowTasks.value) {
    if (row.backupId === id) row.title = `${t('protection.backupsPage.flowTaskKindRestore')} · ${nextName}`
  }
  editOpen.value = false
  ElMessage.success({ message: t('protection.backupsPage.msgSaveEditDemo'), grouping: true })
}



const messageLocale = computed<MessageLocale>(() => 'en')
const protectionMenus = useProtectionSideNav()

const recOpen = ref(false)
const recEntryStage = ref<'chooser' | 'wizard'>('chooser')
const recEntryMode = ref<'plan' | 'manual'>('plan')
const recStep = ref(0)
const recSubmitting = ref(false)
const recBackupIds = ref<string[]>([])
const recSnapshotMap = ref<Record<string, string>>({})
const recoverySnapshotDetails = ref(new Map<number, BackupSourceSnapshot>())
const RECOVERY_SNAPSHOT_PAGE_SIZE = 200
const RECOVERY_SNAPSHOT_LOAD_MORE_VALUE = '__load_more_snapshots__'

type RecoverySnapshotListState = {
  page: number
  pageSize: number
  count: number
  items: BackupSourceSnapshot[]
  loading: boolean
  loadingMore: boolean
  loaded: boolean
  error: string
}

type RecoverySnapshotOption = {
  id: string
  time: string
  status: string
  sizeBytes: number
  fileCount: number
  dirCount: number
  dirs: DemoSnapshotDir[]
  directories: BackupSourceSnapshotDirectory[]
  raw?: BackupSourceSnapshot
}

type SnapshotCompatibility = {
  compatible: boolean
  reason: string
}

const recoverySnapshotLists = reactive<Record<string, RecoverySnapshotListState>>({})
const recoveryPlanSnapshotMap = ref<Record<string, string>>({})
const recoverySnapshotBrowseRevision = ref(0)
const recBackupId = ref('')
const recSnapshotId = ref('')
const recDirKeys = ref<string[]>([])
const recSelectTreeRef = ref<InstanceType<typeof ElTree>>()
const recHostDirNameMap = ref<Record<string, string>>({})
type RecoveryConflictPolicy = '' | 'skip' | 'overwrite'
type SelectedRecoveryConflictPolicy = Exclude<RecoveryConflictPolicy, ''>
const recConflictPolicyMap = ref<Record<string, RecoveryConflictPolicy>>({})
const recDestDrawerOpen = ref(false)
const recDestDrawerHostId = ref('')
const recDestDrawerEntryId = ref('')
const recDestDrawerDraft = ref<RecoveryDestinationConfig | null>(null)
const recDirDrawerOpen = ref(false)
const recDirDrawerHostId = ref('')
const recDirDrawerEntryId = ref('')
const recDirDrawerDraftKeys = ref<string[]>([])
const recDirDrawerTreeRef = ref<InstanceType<typeof ElTree> | null>(null)
const recDirSelectionsByHost = ref<Record<string, RecoveryDirSelectionEntry[]>>({})
const recExpandedRecDirHostIds = ref<string[]>([])
const recRecoveryDirSourceTableRef = ref<InstanceType<typeof ElTable> | null>(null)
const recDirStepInitialized = ref(false)
const recSnapshotRangePickerVisible = reactive<Record<string, boolean>>({})
const recRecoveryTargetDirPickerVisible = reactive<Record<string, boolean>>({})
const dismissedRecoveryPathErrors = reactive<Record<string, string>>({})
const recSnapshotRangePathValidating = reactive<Record<string, boolean>>({})
const recRecoveryTargetDirPathValidating = reactive<Record<string, boolean>>({})
const recRecoveryTargetDirTreeLoading = reactive<Record<string, number>>({})
const recoveryDirectoryTreeRefs = new Map<string, InstanceType<typeof ElTree>>()
const refreshingRecoveryDirectoryByKey = reactive<Record<string, boolean>>({})
const recoveryDirectoryExpansionRevisionByKey = new Map<string, number>()
const recDestActiveBackupId = ref('')
const recDestCheckedBackupIds = ref<string[]>([])
const recOriginalHostLockedBackupId = ref('')
const recDestEntriesByHost = ref<Record<string, RecoveryDestinationEntry[]>>({})
const recBatchHostDirPrefix = ref('')

watch(
  [recStep, recDestEntriesByHost, recDirSelectionsByHost, recConflictPolicyMap],
  () => {
    if (isFixedSnapshotRestore.value && fixedRestoreDirtyTracking.value) fixedRestoreDirty.value = true
  },
  { deep: true },
)

const recWizardSteps = computed(() => [
  {
    title: t('protection.backupsPage.recStepBackupAndSnapshot'),
    hint: t('protection.backupsPage.recWizardHintBackupAndSnapshot'),
    icon: Archive,
  },
  {
    title: t('protection.backupsPage.recStepDest'),
    hint: t('protection.backupsPage.recWizardHintDest'),
    icon: FolderInput,
  },
  {
    title: t('protection.backupsPage.recStepDirs'),
    hint: t('protection.backupsPage.recWizardHintDirs'),
    icon: FolderTree,
  },
  {
    title: t('protection.backupsPage.recStepConfirm'),
    hint: t('protection.backupsPage.recWizardHintConfirm'),
    icon: ClipboardCheck,
  },
])

const recWizardStepItems = computed(() =>
  recWizardSteps.value.map((step, index) => ({
    step: index,
    label: step.title,
    icon: step.icon,
  })).slice(isFixedSnapshotRestore.value ? 1 : 0),
)

function isRecStepDone(step: number) {
  return recStep.value > step
}

async function confirmDiscardFixedRestore() {
  if (!fixedRestoreDirty.value) return true
  try {
    await ElMessageBox.confirm(
      t('protection.backupsPage.snapshotRestoreDiscardMessage'),
      t('protection.backupsPage.snapshotRestoreDiscardTitle'),
      {
        confirmButtonText: t('protection.backupsPage.snapshotRestoreDiscardConfirm'),
        cancelButtonText: t('common.cancel'),
        type: 'warning',
      },
    )
    return true
  } catch {
    return false
  }
}

async function leaveFixedRestore() {
  fixedRestoreLeaveApproved = true
  fixedRestoreDirtyTracking.value = false
  fixedRestoreDirty.value = false
  recOpen.value = false
  await router.replace({ path: '/protection/backups', query: { step: 'start-backup' } })
}

async function closeRecoveryWizard() {
  if (!isFixedSnapshotRestore.value) {
    recOpen.value = false
    return
  }
  if (!(await confirmDiscardFixedRestore())) return
  await leaveFixedRestore()
}

onBeforeRouteLeave(async () => {
  if (!isFixedSnapshotRestore.value || fixedRestoreLeaveApproved || !fixedRestoreDirty.value) return true
  const confirmed = await confirmDiscardFixedRestore()
  if (confirmed) fixedRestoreLeaveApproved = true
  return confirmed
})

async function initializeFixedSnapshotRestore() {
  if (!isFixedSnapshotRestore.value) return
  fixedRestoreInitializing.value = true
  fixedRestoreInitError.value = ''
  fixedRestoreDirty.value = false
  fixedRestoreDirtyTracking.value = false
  recOpen.value = false
  try {
    if (props.fixedRestoreSnapshotId <= 0) {
      throw new Error(t('protection.backupsPage.snapshotRestoreNotFound'))
    }
    const detail = await getBackupSourceSnapshot(props.fixedRestoreSnapshotId)
    if (!hasRestorableSnapshotDirectory(detail)) {
      throw new Error(t('protection.backupsPage.snapshotRestoreUnavailable'))
    }
    fixedRestoreSnapshot.value = detail
    const sourceId = endpointUiId(detail.source_type, Number(detail.source_ref_id))
    await restoreTargetCatalog.ensureByIds([sourceId])
    rememberSelectableRows(restoreTargetCatalog.allRecords.value.map(mapBackupSelectableToFlowRow))

    let config = backupConfigDetailById.value.get(Number(detail.backup_config_id))
    if (!config) {
      config = await getBackupConfig(Number(detail.backup_config_id))
      backupConfigDetailById.value.set(config.id, config)
      backupConfigRows.value = [...backupConfigRows.value.filter((item) => item.id !== config.id), config]
    }

    backupSnapshotRows.value = mergeSnapshotListItems(backupSnapshotRows.value, [detail])
    syncRealBackupConfigsToDemoStore([...backupConfigDetailById.value.values()], backupSnapshotRows.value)
    const backupId = realBackupId(config.id)
    openRecoveryWithBackupIds([backupId])
    mergeRecoverySnapshotDetail(detail)
    const snapshotState = recoverySnapshotListState(`${detail.source_type}:${detail.source_ref_id}`)
    snapshotState.items = mergeSnapshotListItems(snapshotState.items, [detail])
    snapshotState.count = Math.max(snapshotState.count, snapshotState.items.length)
    snapshotState.loaded = true
    recEntryMode.value = 'manual'
    recSnapshotMap.value = { [backupId]: String(detail.id) }
    recSnapshotId.value = String(detail.id)
    recStep.value = 1
    const row = flowRowFromSourceId(sourceId)
    if (row) step3SourceSelection.value = [row]
    await nextTick()
    fixedRestoreDirtyTracking.value = true
  } catch (error) {
    fixedRestoreSnapshot.value = null
    fixedRestoreInitError.value = apiErrorMessage(
      error,
      t('protection.backupsPage.snapshotRestoreNotFound'),
    )
  } finally {
    fixedRestoreInitializing.value = false
  }
}

type RecoveryDirNode = {
  id: string
  label: string
  backupId?: string
  backupName?: string
  snapshotId?: string
  snapshotTime?: string
  hostId: string
  hostName?: string
  path: string
  pathType?: DemoPathType
  isCustom?: boolean
  children?: RecoveryDirNode[]
}

type RecoveryPlanSourceDir = {
  hostId: string
  hostName: string
  path: string
  pathType?: DemoPathType
  scope?: 'snapshot' | 'directory'
}

type RecoveryPlanMapping = {
  id: string
  restorePlanId: number
  sourceDir: RecoveryPlanSourceDir
  destHostId: string
  targetType?: RestoreEndpointType
  targetRefId?: number
  restoreDir: string
  destResourceName: string
  destHostName: string
  destHostIp: string
  customSubdir: string
  conflictPolicy: SelectedRecoveryConflictPolicy
}

type RecoveryPlanSummary = {
  id: string
  restorePlanId?: number
  restorePlanIds: number[]
  backupConfigId?: number
  sourceType: RestoreEndpointType
  sourceRefId: number
  name: string
  backupId: string
  backupName: string
  snapshotId: string
  snapshotTime: string
  sourceResourceName: string
  sourceHostName: string
  sourceHostIp: string
  sourceDirIds: string[]
  sourceDirs: RecoveryPlanSourceDir[]
  destHostId: string
  targetType?: RestoreEndpointType
  targetRefId?: number
  restoreDir: string
  destResourceName: string
  destHostName: string
  destHostIp: string
  customSubdir: string
  conflictPolicy: SelectedRecoveryConflictPolicy
  mappings: RecoveryPlanMapping[]
}

type RecoveryDestinationConfig = {
  hostId: string
  dirKey: string
  currentNodeKey: string
  customSubdir: string
  expandedKeys: string[]
}

type RecoveryDestinationEntry = RecoveryDestinationConfig & {
  id: string
}

type RecoveryDirSelectionEntry = {
  id: string
  hostId: string
  path: string
  hostName: string
  scope?: 'snapshot' | 'directory'
  sourceKind?: 'snapshot' | 'dir' | 'file'
  targetPath?: string
  sourceSnapshotDirectoryId?: number
  sourcePathValidation?: 'valid' | 'pending' | 'invalid'
  sourcePathError?: string
  targetPathValidation?: 'valid' | 'pending' | 'invalid'
  targetPathError?: string
}

type RecoverySnapshotPickerNode = {
  id: string
  label: string
  path: string
  type: 'snapshot' | 'dir' | 'file'
  directoryId?: number
  browsePath?: string
  sourceRootPath?: string
  isLeaf: boolean
}

type RecoveryTargetDirPickerNode = {
  id: string
  label: string
  path: string
  isLeaf: boolean
}

type RecoveryDirectoryPickerNode = RecoverySnapshotPickerNode | RecoveryTargetDirPickerNode

function recoveryDirectoryTreeKey(hostId: string, entryId: string, kind: 'snapshot' | 'target') {
  return `${recoveryDirEntryPickerKey(hostId, entryId)}:${kind}`
}

function recoveryDirectoryNodeRefreshKey(treeKey: string, nodeKey: string) {
  return `${treeKey}:${nodeKey}`
}

function setRecoveryDirectoryTreeRef(treeKey: string, el: unknown) {
  if (el) {
    recoveryDirectoryTreeRefs.set(treeKey, el as InstanceType<typeof ElTree>)
  } else {
    recoveryDirectoryTreeRefs.delete(treeKey)
  }
}

function isRecoveryDirectoryRefreshing(treeKey: string, nodeKey: string) {
  return Boolean(refreshingRecoveryDirectoryByKey[recoveryDirectoryNodeRefreshKey(treeKey, nodeKey)])
}

function onRecoveryDirectoryExpansionChange(treeKey: string, nodeKey: string) {
  if (!nodeKey) return
  const refreshKey = recoveryDirectoryNodeRefreshKey(treeKey, nodeKey)
  recoveryDirectoryExpansionRevisionByKey.set(
    refreshKey,
    (recoveryDirectoryExpansionRevisionByKey.get(refreshKey) ?? 0) + 1,
  )
}

async function refreshRecoveryDirectoryNode(
  treeKey: string,
  nodeKey: string,
  data: RecoveryDirectoryPickerNode,
  loadChildren: () => Promise<RecoveryDirectoryPickerNode[]>,
) {
  const tree = recoveryDirectoryTreeRefs.get(treeKey)
  if (!tree || !nodeKey) return
  const refreshKey = recoveryDirectoryNodeRefreshKey(treeKey, nodeKey)
  if (refreshingRecoveryDirectoryByKey[refreshKey]) return
  const sourceNode = tree.getNode(nodeKey) as unknown as { expanded?: boolean } | null
  if (!sourceNode) return
  const wasExpanded = Boolean(sourceNode.expanded)
  const expansionRevisionAtStart = recoveryDirectoryExpansionRevisionByKey.get(refreshKey) ?? 0

  refreshingRecoveryDirectoryByKey[refreshKey] = true
  try {
    const children = await loadChildren()
    if (recoveryDirectoryTreeRefs.get(treeKey) !== tree) return
    tree.updateKeyChildren(nodeKey, children)
    const refreshedNode = tree.getNode(nodeKey) as unknown as {
      loaded?: boolean
      collapse?: () => void
      expand?: () => void
      updateLeafState?: () => void
    } | null
    if (!refreshedNode) return
    refreshedNode.loaded = true
    refreshedNode.updateLeafState?.()
    await nextTick()
    const hasChildren = children.length > 0
    if (!hasChildren) {
      refreshedNode.collapse?.()
      ElMessage.info({
        message: t('protection.backupsPage.dirTreeRefreshEmpty', { path: data.path }),
        grouping: true,
      })
      return
    }
    const expansionRevisionAfterRefresh = recoveryDirectoryExpansionRevisionByKey.get(refreshKey) ?? 0
    if (shouldAutoExpandRefreshedDirectory({
      wasExpanded,
      hasChildren,
      expansionRevisionAtStart,
      expansionRevisionAfterRefresh,
    })) {
      refreshedNode.expand?.()
    }
    ElMessage.success({
      message: t('protection.backupsPage.dirTreeRefreshSuccess', { path: data.path }),
      grouping: true,
    })
  } catch (e) {
    showApiErrorI18n(e, t('protection.backupsPage.dirTreeLoadFailed'))
  } finally {
    delete refreshingRecoveryDirectoryByKey[refreshKey]
  }
}

type RecoveryTaskDraft = {
  backupId: string
  backupName: string
  snapshotId: string
  snapshotTime: string
  dirs: RecoveryDirSelectionEntry[]
  conflictPolicy: SelectedRecoveryConflictPolicy
  destHostId: string
  destHostName: string
  destPath: string
}

function latestBackupSnapshot(backup: DemoBackup | undefined): DemoSnapshot | undefined {
  if (!backup?.snapshots.length) return undefined
  return backup.snapshots.reduce<DemoSnapshot | undefined>((latest, snap) => {
    if (!latest) return snap
    return new Date(snap.endTime).getTime() > new Date(latest.endTime).getTime() ? snap : latest
  }, undefined)
}

function isRecoverableSnapshot(snapshot: DemoSnapshot | undefined | null) {
  const status = String(snapshot?.status || 'available').toLowerCase()
  return status === 'available' || status === 'partial'
}

function hasRestorableSnapshotDirectory(snapshot: BackupSourceSnapshot | undefined | null) {
  const status = String(snapshot?.status || '').toLowerCase()
  if (status !== 'available' && status !== 'partial') return false
  return (snapshot.directories || []).some((dir) =>
    dir.id > 0
    && dir.status === 'available'
    && Boolean(String(dir.kopia_snapshot_id || '').trim()),
  )
}

function sourceHasRecoverableSnapshot(sourceId: string) {
  const runtime = runtimeForSource(sourceId)
  if (Object.prototype.hasOwnProperty.call(runtime, 'has_restorable_snapshot')) {
    return runtimeBool(runtime.has_restorable_snapshot)
  }
  const configIds = sourceBackupConfigIds(sourceId)
  if (snapshotsForBackupConfigIds(configIds).some((snapshot) => isRecoverableSnapshot(snapshot))) return true
  return backupsForSource(sourceId).some((backup) => backup.snapshots.some(isRecoverableSnapshot))
}

function latestRecoverableBackupSnapshot(backup: DemoBackup | undefined): DemoSnapshot | undefined {
  if (!backup?.snapshots.length) return undefined
  return backup.snapshots
    .filter(isRecoverableSnapshot)
    .reduce<DemoSnapshot | undefined>((latest, snap) => {
      if (!latest) return snap
      return new Date(snap.endTime).getTime() > new Date(latest.endTime).getTime() ? snap : latest
    }, undefined)
}

function sourceDisplayNames(rows: FlowSourceRow[]) {
  const names = rows.map((row) => row.name || row.nodeName || row.hostname || row.id)
  return names.slice(0, 5).join(', ') + (names.length > 5 ? `, +${names.length - 5}` : '')
}

function uniqueBackupIds(ids: string[]) {
  const seen = new Set<string>()
  return ids.filter((id) => {
    if (!id || seen.has(id) || !store.getBackup(id)) return false
    seen.add(id)
    return true
  })
}

async function openRecovery() {
  if (recoveryOpening.value) return
  if (!step3SourceSelection.value.length) {
    ElMessage.warning({ message: t('protection.backupsPage.msgSelectSourcesToRecover'), grouping: true })
    return
  }
  if (step3SourceSelection.value.some((row) => sourceRestoreRuntime(row.id).running || runtimeStopping(row.id, 'restore'))) {
    ElMessage.warning({ message: t('protection.backupsPage.msgRestoreAlreadyRunning'), grouping: true })
    return
  }
  if (!step3SourceSelection.value.some((row) => sourceHasRecoverableSnapshot(row.id))) {
    ElMessage.warning({ message: t('protection.backupsPage.msgSelectedSourcesNoBackupForRecovery'), grouping: true })
    return
  }
  recoveryOpening.value = true
  try {
    const invalidRows: FlowSourceRow[] = []
    const recoverableBackupIds: string[] = []
    for (const row of step3SourceSelection.value) {
      const recoverableConfigIds = new Set(
        snapshotsForBackupConfigIds(sourceBackupConfigIds(row.id))
          .filter((snapshot) => isRecoverableSnapshot(snapshot))
          .map((snapshot) => Number(snapshot.backup_config_id || 0))
          .filter((id) => id > 0),
      )
      const backupIds = backupsForSource(row.id)
        .filter((backup) =>
          recoverableConfigIds.has(realConfigIdFromBackupId(backup.id))
          || backup.snapshots.some(isRecoverableSnapshot),
        )
        .map((backup) => backup.id)
      if (backupIds.length) recoverableBackupIds.push(...backupIds)
      else invalidRows.push(row)
    }
    if (invalidRows.length) {
      ElMessage.warning({
message: t('protection.backupsPage.msgSelectedSourcesNoRestorableSnapshot', {
        names: sourceDisplayNames(invalidRows),
      }),
grouping: true,
})
      return
    }
    const backupIds = uniqueBackupIds(recoverableBackupIds)
    openRecoveryWithBackupIds(backupIds)
  } catch (e) {
    showApiError(e)
  } finally {
    recoveryOpening.value = false
  }
}

function currentRecBackup() {
  return store.getBackup(recBackupId.value)
}



function destKeyForPath(hostId: string, path: string) {
  return hostId && path ? `${hostId}::${path}` : ''
}

function decodeDestPath(config: RecoveryDestinationConfig | undefined) {
  if (!config?.dirKey) return ''
  if (config.dirKey.startsWith('custom::')) {
    const prefix = `custom::${config.hostId}::`
    return config.dirKey.startsWith(prefix) ? config.dirKey.slice(prefix.length) : config.dirKey.split('::').slice(2).join('::')
  }
  const prefix = `${config.hostId}::`
  return config.dirKey.startsWith(prefix) ? config.dirKey.slice(prefix.length) : config.dirKey.split('::').slice(1).join('::')
}


function activeRecoveryDestinationBackupId() {
  if (recBackupIds.value.includes(recDestActiveBackupId.value)) return recDestActiveBackupId.value
  return recBackupIds.value[0] || recBackupId.value || ''
}

function emptyRecoveryDestinationConfig(): RecoveryDestinationConfig {
  return {
    hostId: '',
    dirKey: '',
    currentNodeKey: '',
    customSubdir: '',
    expandedKeys: [],
  }
}

function buildInitialRecoveryDestEntriesByHost(backupIds: string[]) {
  const map: Record<string, RecoveryDestinationEntry[]> = {}
  for (const backupId of backupIds) {
    const backup = store.getBackup(backupId)
    const hostId = backup?.sources[0]?.hostId || ''
    if (!hostId) continue
    if (!(hostId in map)) map[hostId] = []
  }
  return map
}

function isRecoveryDestinationEntryConfigured(entry: RecoveryDestinationConfig) {
  return Boolean(entry.hostId)
}

function recoveryDestEntriesForHost(hostId: string): RecoveryDestinationEntry[] {
  return recDestEntriesByHost.value[hostId] || []
}

function recoveryDestConfiguredEntryForHost(hostId: string): RecoveryDestinationEntry | null {
  return recoveryDestEntriesForHost(hostId).find(isRecoveryDestinationEntryConfigured) ?? null
}

function setRecoveryDestEntriesForHost(hostId: string, entries: RecoveryDestinationEntry[]) {
  const configured = entries.find(isRecoveryDestinationEntryConfigured)
  const placeholder = entries.find((entry) => !isRecoveryDestinationEntryConfigured(entry))
  const next = configured ? [configured] : placeholder ? [placeholder] : []
  recDestEntriesByHost.value = { ...recDestEntriesByHost.value, [hostId]: next }
}


function findRecoveryDestEntry(hostId: string, entryId: string) {
  return recoveryDestEntriesForHost(hostId).find((entry) => entry.id === entryId)
}

function recoveryDestConfigForBackup(backupId: string): RecoveryDestinationConfig {
  const id = backupId || activeRecoveryDestinationBackupId()
  const backup = store.getBackup(id)
  const hostId = backup?.sources[0]?.hostId || ''
  const entry = recoveryDestEntriesForHost(hostId).find(isRecoveryDestinationEntryConfigured)
  if (entry) return entry
  return emptyRecoveryDestinationConfig()
}

function updateRecoveryDestEntry(hostId: string, entryId: string, patch: Partial<RecoveryDestinationConfig>) {
  if (!hostId || !entryId) return
  const list = recoveryDestEntriesForHost(hostId)
  const current = list.find((entry) => entry.id === entryId)
  if (!current) return
  recDestEntriesByHost.value = {
    ...recDestEntriesByHost.value,
    [hostId]: list.map((entry) =>
      entry.id === entryId
        ? {
            ...entry,
            ...patch,
            expandedKeys: patch.expandedKeys ? [...patch.expandedKeys] : [...entry.expandedKeys],
          }
        : entry,
    ),
  }
}



function isRecDestDrawerEditingEntry(hostId: string, entryId: string) {
  return (
    recDestDrawerOpen.value
    && recDestDrawerDraft.value !== null
    && recDestDrawerHostId.value === hostId
    && recDestDrawerEntryId.value === entryId
  )
}

function activeRecoveryDestConfig(): RecoveryDestinationConfig {
  const hostId = recDestDrawerHostId.value || backupSourceHostId(activeRecoveryDestinationBackupId())
  const entryId = recDestDrawerEntryId.value || recoveryDestEntriesForHost(hostId)[0]?.id || ''
  if (isRecDestDrawerEditingEntry(hostId, entryId) && recDestDrawerDraft.value) {
    return recDestDrawerDraft.value
  }
  const entry = findRecoveryDestEntry(hostId, entryId)
  if (entry) return entry
  return recoveryDestConfigForBackup(activeRecoveryDestinationBackupId())
}

function patchActiveRecoveryDestConfig(patch: Partial<RecoveryDestinationConfig>) {
  const hostId = recDestDrawerHostId.value || backupSourceHostId(activeRecoveryDestinationBackupId())
  const entryId = recDestDrawerEntryId.value || recoveryDestEntriesForHost(hostId)[0]?.id || ''
  if (!hostId || !entryId) return
  if (isRecDestDrawerEditingEntry(hostId, entryId) && recDestDrawerDraft.value) {
    const current = recDestDrawerDraft.value
    recDestDrawerDraft.value = {
      ...current,
      ...patch,
      expandedKeys: patch.expandedKeys ? [...patch.expandedKeys] : [...current.expandedKeys],
    }
    return
  }
  updateRecoveryDestEntry(hostId, entryId, patch)
}

function backupSourceHostId(backupId: string) {
  return store.getBackup(backupId)?.sources[0]?.hostId || ''
}

const recDestHostId = computed({
  get: () => activeRecoveryDestConfig().hostId,
  set: (hostId: string) => {
    patchActiveRecoveryDestConfig({
      hostId,
      dirKey: '',
      currentNodeKey: '',
      expandedKeys: [],
    })
  },
})

const recDestDirKey = computed({
  get: () => activeRecoveryDestConfig().dirKey,
  set: (dirKey: string) => patchActiveRecoveryDestConfig({ dirKey }),
})


const recDestCurrentNodeKey = computed({
  get: () => activeRecoveryDestConfig().currentNodeKey,
  set: (currentNodeKey: string) => patchActiveRecoveryDestConfig({ currentNodeKey }),
})


const recDestExpandedKeys = computed({
  get: () => activeRecoveryDestConfig().expandedKeys,
  set: (expandedKeys: string[]) => patchActiveRecoveryDestConfig({ expandedKeys }),
})

function syncRecoveryDestinationConfigs() {
  const ids = recBackupIds.value
  const next: Record<string, RecoveryDestinationEntry[]> = { ...recDestEntriesByHost.value }
  for (const group of recBackupSourceGroups.value) {
    if (!next[group.hostId]) next[group.hostId] = []
    else {
      next[group.hostId] = next[group.hostId].map((entry) => ({
        ...entry,
        expandedKeys: [...entry.expandedKeys],
      }))
    }
  }
  for (const hostId of Object.keys(next)) {
    if (!recBackupSourceGroups.value.some((group) => group.hostId === hostId)) {
      delete next[hostId]
    }
  }
  for (const group of recBackupSourceGroups.value) {
    next[group.hostId] = (next[group.hostId] || []).slice(0, 1)
  }
  recDestEntriesByHost.value = next
  recDestCheckedBackupIds.value = recDestCheckedBackupIds.value.filter((backupId) => ids.includes(backupId))
  if (!ids.includes(recDestActiveBackupId.value)) recDestActiveBackupId.value = ids[0] || ''
  if (!ids.includes(recOriginalHostLockedBackupId.value)) recOriginalHostLockedBackupId.value = ''
}

type RecoveryPlanSourceContext = {
  snapshot: DemoSnapshot
  rootDirs: Array<{ hostId: string; hostName: string; path: string; pathType?: DemoPathType }>
  sourceHostId: string
  sourceResourceName: string
  sourceHostName: string
  sourceHostIp: string
}

function buildRecoveryPlanSourceContext(backup: DemoBackup): RecoveryPlanSourceContext | null {
  const snapshotId = recSnapshotMap.value[backup.id]
  const snapshot = backup.snapshots.find((s) => s.id === snapshotId) ?? latestBackupSnapshot(backup)
  if (!backup || !snapshot) return null
  const configId = realConfigIdFromBackupId(backup.id)
  const configDirTypeByPath = new Map(
    (configId ? backupConfigDetailById.value.get(configId)?.directories || [] : [])
      .map((dir) => [normalizeComparablePath(dir.path), normalizeDemoPathType(dir.path_type)] as const),
  )
  const sourcePathType = (path: string, fallback?: DemoPathType) =>
    normalizeDemoPathType(fallback) !== 'unknown'
      ? normalizeDemoPathType(fallback)
      : configDirTypeByPath.get(normalizeComparablePath(path)) || 'directory'
  const rootDirs = snapshot.dirs.length
    ? snapshot.dirs.map((dir) => ({
        hostId: dir.hostId,
        hostName: dir.hostName,
        path: dir.path,
        pathType: sourcePathType(dir.path, dir.pathType),
      }))
    : backup.sources.map((source) => ({
        hostId: source.hostId,
        hostName: store.getNodeName(source.hostId),
        path: source.path,
        pathType: sourcePathType(source.path, source.pathType),
      }))
  if (!rootDirs.length) return null
  const sourceHostId = rootDirs[0].hostId
  const sourceEndpoint = recoveryEndpointDisplay(sourceHostId, rootDirs[0]?.hostName || store.getNodeName(sourceHostId))
  const sourceResource = recoveryResourceDisplay(sourceHostId, sourceEndpoint.name)
  return {
    snapshot,
    rootDirs,
    sourceHostId,
    sourceResourceName: sourceResource.name,
    sourceHostName: sourceEndpoint.name,
    sourceHostIp: sourceResource.ip,
  }
}

function restorePlanSourceDir(
  config: BackupConfigDetail,
  restorePlan: BackupConfigRecoveryPlan,
  context: RecoveryPlanSourceContext,
): RecoveryPlanSourceDir {
  if (restorePlan.scope === 'snapshot') {
    return {
      hostId: context.sourceHostId,
      hostName: context.sourceHostName,
      path: t('protection.backupsPage.recoveryWholeSnapshot'),
      pathType: 'directory' as DemoPathType,
      scope: 'snapshot',
    }
  }
  const sourceHostId = endpointUiId(
    restorePlan.source_type || config.source_type || 'agent',
    Number(restorePlan.source_ref_id || config.source_ref_id),
  )
  const sourceEndpoint = recoveryEndpointDisplay(sourceHostId, context.sourceHostName)
  const configDir = config.directories.find((dir) => dir.id === restorePlan.backup_config_dir_id)
    ?? config.directories.find((dir) => normalizeComparablePath(dir.path) === normalizeComparablePath(restorePlan.source_path))
    ?? config.directories.find((dir) => isSameOrAncestorPath(dir.path, restorePlan.source_path))
  const snapshotDir = context.rootDirs.find((dir) => normalizeComparablePath(dir.path) === normalizeComparablePath(restorePlan.source_path))
    ?? context.rootDirs.find((dir) => isSameOrAncestorPath(dir.path, restorePlan.source_path))
  const pathType = normalizeDemoPathType(configDir?.path_type || snapshotDir?.pathType)
  return {
    hostId: sourceHostId,
    hostName: sourceEndpoint.name,
    path: restorePlan.source_path,
    pathType: pathType === 'unknown' ? 'directory' : pathType,
    scope: 'directory',
  }
}

function buildRecoveryPlanMapping(
  restorePlan: BackupConfigRecoveryPlan,
  config: BackupConfigDetail,
  context: RecoveryPlanSourceContext,
): RecoveryPlanMapping | null {
  if (!restorePlan?.id || (restorePlan.scope !== 'snapshot' && !restorePlan.source_path)) return null
  const sourceDir = restorePlanSourceDir(config, restorePlan, context)
  const destHost =
    store.getHost(sourceDir.hostId) ??
    store.getNas(sourceDir.hostId) ??
    [...store.hosts.value, ...store.nas.value].find((host) => host.id !== sourceDir.hostId) ??
    store.hosts.value[0] ??
    store.nas.value[0]
  if (!destHost) return null
  const destHostId = restorePlan.target_ref_id
    ? endpointUiId(restorePlan.target_type || 'agent', Number(restorePlan.target_ref_id))
    : destHost.id
  const destEndpoint = recoveryEndpointDisplay(destHostId, destHost.name)
  const destResource = recoveryResourceDisplay(destHostId, destEndpoint.name)
  return {
    id: `rpm-${restorePlan.id}`,
    restorePlanId: restorePlan.id,
    sourceDir,
    destHostId,
    targetType: (restorePlan.target_type || 'agent') as RestoreEndpointType,
    targetRefId: Number(restorePlan.target_ref_id) || undefined,
    restoreDir: restorePlan.restore_dir || '',
    destResourceName: destResource.name,
    destHostName: destEndpoint.name,
    destHostIp: destResource.ip,
    customSubdir: restorePlan.restore_dir || '',
    conflictPolicy: restorePlan.conflict_mode === 'overwrite' ? 'overwrite' : 'skip',
  }
}

function buildRecoveryPlanSummary(
  backup: DemoBackup,
  config: BackupConfigDetail,
  context: RecoveryPlanSourceContext,
  mappings: RecoveryPlanMapping[],
): RecoveryPlanSummary | null {
  const configId = realConfigIdFromBackupId(backup.id)
  if (!configId || !mappings.length) return null
  const firstMapping = mappings[0]
  const sourceResource = recoveryResourceDisplay(context.sourceHostId, context.sourceHostName)
  const sourceDirs = mappings.map((mapping) => mapping.sourceDir)
  return {
    id: `rp-group-${configId}`,
    restorePlanId: firstMapping.restorePlanId,
    restorePlanIds: mappings.map((mapping) => mapping.restorePlanId),
    backupConfigId: config.id,
    sourceType: (config.source_type || 'agent') as RestoreEndpointType,
    sourceRefId: Number(config.source_ref_id),
    name: `${t('protection.backupsPage.defaultRecoveryPlanName')} · ${context.sourceHostName}`,
    backupId: backup.id,
    backupName: backup.name,
    snapshotId: context.snapshot.id,
    snapshotTime: context.snapshot.endTime,
    sourceResourceName: sourceResource.name,
    sourceHostName: context.sourceHostName,
    sourceHostIp: sourceResource.ip,
    sourceDirIds: sourceDirs.map((dir) => `${backup.id}::${dir.hostId}::${dir.path}`),
    sourceDirs,
    destHostId: firstMapping.destHostId,
    targetType: firstMapping.targetType,
    targetRefId: firstMapping.targetRefId,
    restoreDir: firstMapping.restoreDir,
    destResourceName: firstMapping.destResourceName,
    destHostName: firstMapping.destHostName,
    destHostIp: firstMapping.destHostIp,
    customSubdir: firstMapping.customSubdir,
    conflictPolicy: firstMapping.conflictPolicy,
    mappings,
  }
}

type RecoveryPlanTableRow = {
  rowKey: string
  state: 'configured' | 'missing'
  plan: RecoveryPlanSummary | null
  backupId: string
  backupName: string
  snapshotId: string
  snapshotTime: string
  sourceResourceName: string
  sourceHostName: string
  sourceHostIp: string
}

function buildRecoveryPlanTableRowsForBackup(backup: DemoBackup): RecoveryPlanTableRow[] {
  const context = buildRecoveryPlanSourceContext(backup)
  if (!context) return []
  const configId = realConfigIdFromBackupId(backup.id)
  const config = configId ? backupConfigDetailById.value.get(configId) : null
  const mappings = (config?.recovery_plans || [])
    .filter((plan) => plan.enabled !== false)
    .map((plan) => buildRecoveryPlanMapping(plan, config as BackupConfigDetail, context))
    .filter(Boolean) as RecoveryPlanMapping[]
  const plan = config ? buildRecoveryPlanSummary(backup, config, context, mappings) : null
  if (plan) {
    return [{
      rowKey: plan.id,
      state: 'configured',
      plan,
      backupId: backup.id,
      backupName: backup.name,
      snapshotId: context.snapshot.id,
      snapshotTime: context.snapshot.endTime,
      sourceResourceName: plan.sourceResourceName,
      sourceHostName: plan.sourceHostName,
      sourceHostIp: plan.sourceHostIp,
    }]
  }
  return [{
    rowKey: `missing-rp-${backup.id}`,
    state: 'missing',
    plan: null,
    backupId: backup.id,
    backupName: backup.name,
    snapshotId: context.snapshot.id,
    snapshotTime: context.snapshot.endTime,
    sourceResourceName: context.sourceResourceName,
    sourceHostName: context.sourceHostName,
    sourceHostIp: context.sourceHostIp,
  }]
}

const recoveryPlanTableRows = computed(() => {
  return recBackupIds.value
    .map((backupId) => store.getBackup(backupId))
    .filter(Boolean)
    .flatMap((backup) => buildRecoveryPlanTableRowsForBackup(backup as DemoBackup))
})

function recoveryPlanSnapshotKey(plan: RecoveryPlanSummary) {
  return plan.id
}

function recoveryPlanSelectedSnapshotId(plan: RecoveryPlanSummary) {
  return recoveryPlanSnapshotMap.value[recoveryPlanSnapshotKey(plan)]
    || defaultCompatibleSnapshotIdForPlan(plan)
    || plan.snapshotId
    || ''
}

function recoveryPlanWithSelectedSnapshot(plan: RecoveryPlanSummary): RecoveryPlanSummary {
  const snapshotId = recoveryPlanSelectedSnapshotId(plan)
  const snapshot = recoverySnapshotById(backupSourceHostId(plan.backupId), snapshotId)
  return {
    ...plan,
    snapshotId,
    snapshotTime: snapshot?.time || plan.snapshotTime,
  }
}

async function ensureRecoveryPlanSnapshotDefault(plan: RecoveryPlanSummary) {
  const hostId = backupSourceHostId(plan.backupId)
  await loadRecoverySnapshotsForSource(hostId)
  const key = recoveryPlanSnapshotKey(plan)
  const selectedId = recoveryPlanSnapshotMap.value[key]
  const selected = selectedId ? recoverySnapshotById(hostId, selectedId) : null
  if (selected && restorePlanSnapshotCompatibility(plan, selected).compatible) return
  const nextId = defaultCompatibleSnapshotIdForPlan(plan)
  if (nextId) {
    recoveryPlanSnapshotMap.value = { ...recoveryPlanSnapshotMap.value, [key]: nextId }
  } else if (selectedId) {
    const next = { ...recoveryPlanSnapshotMap.value }
    delete next[key]
    recoveryPlanSnapshotMap.value = next
  }
}

async function ensureRecoveryPlanSnapshotDefaults() {
  const plans = recoveryPlanTableRows.value
    .filter((row) => row.state === 'configured' && row.plan)
    .map((row) => row.plan as RecoveryPlanSummary)
  await Promise.all(plans.map((plan) => ensureRecoveryPlanSnapshotDefault(plan)))
}

async function onRecoveryPlanSnapshotVisible(plan: RecoveryPlanSummary, visible: boolean) {
  if (!visible) return
  try {
    await ensureRecoveryPlanSnapshotDefault(plan)
  } catch (e) {
    showApiError(e)
  }
}

function updateRecoveryPlanSnapshot(plan: RecoveryPlanSummary, value: string | number) {
  const nextValue = String(value || '')
  const hostId = backupSourceHostId(plan.backupId)
  if (nextValue === RECOVERY_SNAPSHOT_LOAD_MORE_VALUE) {
    void loadRecoverySnapshotsForSource(hostId, { loadMore: true })
      .then(() => ensureRecoveryPlanSnapshotDefault(plan))
      .catch((e) => showApiError(e))
    return
  }
  const snapshot = recoverySnapshotById(hostId, nextValue)
  if (snapshot && !restorePlanSnapshotCompatibility(plan, snapshot).compatible) return
  recoveryPlanSnapshotMap.value = {
    ...recoveryPlanSnapshotMap.value,
    [recoveryPlanSnapshotKey(plan)]: nextValue,
  }
}

const selectedRecoveryPlans = computed<RecoveryPlanSummary[]>(() =>
  recoveryPlanTableRows.value
    .filter((row) => row.state === 'configured' && row.plan)
    .map((row) => recoveryPlanWithSelectedSnapshot(row.plan as RecoveryPlanSummary)),
)

const missingRecoveryPlanRows = computed(() =>
  recoveryPlanTableRows.value.filter((row) => row.state === 'missing'),
)

const partialRecoveryConfirmOpen = ref(false)
const pendingRecoveryPlanConfirmPlans = ref<RecoveryPlanSummary[]>([])
const pendingRecoveryPlanConfirmMissingRows = ref<RecoveryPlanTableRow[]>([])

const partialRecoveryConfirmItems = computed<DangerConfirmItem[]>(() =>
  pendingRecoveryPlanConfirmMissingRows.value.map((row) => ({
    key: row.rowKey,
    name: row.sourceResourceName || row.backupName,
    status: { label: t('protection.backupsPage.recoveryPlanMissingTitle'), tone: 'warning' },
    description: row.sourceHostIp || row.snapshotTime || row.backupName,
  })),
)

const recSelectedBackupRows = computed(() =>
  recBackupIds.value
    .map((backupId) => store.getBackup(backupId))
    .filter(Boolean) as DemoBackup[],
)

type RecBackupSourceGroup = {
  hostId: string
  hostName: string
  backups: DemoBackup[]
}

const recBackupSourceGroups = computed(() => {
  const groups = new Map<string, RecBackupSourceGroup>()
  for (const backup of recSelectedBackupRows.value) {
    const hostId = backup.sources[0]?.hostId || ''
    const hostName = store.getNodeName(hostId)
    if (!groups.has(hostId)) groups.set(hostId, { hostId, hostName, backups: [] })
    groups.get(hostId)!.backups.push(backup)
  }
  return [...groups.values()]
})

type RecBackupSnapshotHostRow = {
  rowKey: string
  hostId: string
  hostName: string
  sourceSummary: RecoverySourceSummary
}

type RecoverySourceSummary = {
  sourceType: 'host' | 'nas'
  displayName: string
  ipLine: string
  typeLabel: string
  typeTagType: 'primary' | 'warning'
  platform: EnrollmentOs | null
}

function recoverySourceSummaryFromRow(
  source: FlowSourceRow | null,
  hostId: string,
  fallbackName = '',
): RecoverySourceSummary {
  const parsed = parseEndpointUiId(hostId)
  const sourceType = source?.type ?? (parsed?.type === 'nas' ? 'nas' : 'host')
  const displayName = source?.name || fallbackName || store.getNodeName(hostId)
  if (sourceType === 'nas') {
    const proxyNode = recoverySourceProxyNode(source)
    return {
      sourceType,
      displayName,
      ipLine: proxyNode ? nodeIpLabel(proxyNode) : source?.nodeIp || source?.hostname || '—',
      typeLabel: t('protection.backupsPage.sourceTypeNas'),
      typeTagType: 'warning',
      platform: proxyNode ? nodeEnrollmentOs(proxyNode) : null,
    }
  }
  const node = parsed?.type === 'agent'
    ? recoveryNodeRows.value.find((item) => item.id === parsed.refId && item.role === 'agent')
    : null
  return {
    sourceType,
    displayName,
    ipLine: source?.nodeIp || source?.hostname || (node ? nodeIpLabel(node) : '—'),
    typeLabel: t('protection.backupsPage.sourceTypeHost'),
    typeTagType: 'primary',
    platform: source?.platform ?? (node ? nodeEnrollmentOs(node) : null),
  }
}

function recoverySourceProxyNode(source: FlowSourceRow | null) {
  if (!source || source.type !== 'nas') return null
  const boundNodeId = source.boundNodeId ?? source.bound_node_id ?? null
  const numericId = Number(boundNodeId)
  if (!Number.isFinite(numericId) || numericId <= 0) return null
  return recoveryNodeRows.value.find((node) => node.id === numericId && node.role === 'proxy') ?? null
}

function recoverySourceSummary(hostId: string, fallbackName = ''): RecoverySourceSummary {
  const source = flowRowFromSourceId(hostId)
  return recoverySourceSummaryFromRow(source, hostId, fallbackName)
}

const recBackupSnapshotHostRows = computed<RecBackupSnapshotHostRow[]>(() =>
  recBackupSourceGroups.value.map((group) => ({
    rowKey: group.hostId,
    hostId: group.hostId,
    hostName: group.hostName,
    sourceSummary: recoverySourceSummary(group.hostId, group.hostName),
  })),
)

type RecRecoveryDestSourceRow = {
  hostId: string
  hostName: string
  sourceSummary: RecoverySourceSummary
  snapshotLine: string
  configuredEntry: RecoveryDestinationEntry | null
}

type RecoveryTargetNodeOption = {
  value: string
  label: string
  ipLabel: string
  typeLabel: string
  group: 'original' | 'node'
  isOriginalPrimary?: boolean
  sourceType?: 'host' | 'nas'
  sourceSummary: RecoverySourceSummary
  node?: ApiNode
  source?: FlowSourceRow
  selectable: boolean
  unavailableReason: 'offline' | 'busy' | null
}

const recRecoveryDestStepReady = computed(() => {
  if (!recBackupSourceGroups.value.length) return false
  return recBackupSourceGroups.value.every((group) => {
    const targetId = recoveryDestConfiguredEntryForHost(group.hostId)?.hostId || ''
    return Boolean(targetId && restoreTargetCatalog.isSelectable(targetId))
  })
})

const recRecoveryDestSourceRows = computed<RecRecoveryDestSourceRow[]>(() =>
  recBackupSourceGroups.value.map((group) => ({
    hostId: group.hostId,
    hostName: group.hostName,
    sourceSummary: recoverySourceSummary(group.hostId, group.hostName),
    snapshotLine: recGroupSnapshotDisplayLine(group.hostId),
    configuredEntry: recoveryDestConfiguredEntryForHost(group.hostId),
  })),
)

function nodeIpLabel(node: ApiNode | undefined) {
  return node?.ip_address || node?.metadata?.ip_address?.toString() || '—'
}


function recoveryTargetNodeLabel(targetHostId: string, backupSourceHostId?: string) {
  const source = backupSelectableById.value.get(targetHostId) ?? recoveryOriginalSourceById(targetHostId)
  if (source) {
    const name = source.nodeName || source.name
    if (backupSourceHostId && targetHostId === backupSourceHostId) {
      return `${t('protection.backupsPage.recoveryOriginalNodePrefix')} ${name}`
    }
    const ip = source.nodeIp || source.hostname || ''
    return ip ? `${name} (${ip})` : name
  }
  const parsed = parseEndpointUiId(targetHostId)
  const node = parsed ? recoveryNodeRows.value.find((item) => item.id === parsed.refId) : undefined
  if (!node) return store.getNodeName(targetHostId)
  return `${node.name} (${nodeIpLabel(node)})`
}

function recoveryTargetSummaryForHostId(targetHostId: string, fallbackName = ''): RecoverySourceSummary {
  const source = backupSelectableById.value.get(targetHostId) ?? recoveryOriginalSourceById(targetHostId) ?? null
  return recoverySourceSummaryFromRow(source, targetHostId, source?.name || fallbackName)
}

function recoveryTargetSummaryForEntry(entry: RecoveryDestinationEntry | null): RecoverySourceSummary | null {
  return entry ? recoveryTargetSummaryForHostId(entry.hostId) : null
}

function recoveryOriginalSourceById(id: string) {
  return recoveryOriginalSourceRows().find((source) => source.id === id)
}

function recoveryOriginalSourceRows(): FlowSourceRow[] {
  const rows = new Map<string, FlowSourceRow>()
  for (const row of backupSelectableById.value.values()) {
    if (!flowSourceCanOperate(row)) continue
    if (row.pipeline_step && row.pipeline_step !== 3) continue
    rows.set(row.id, row)
  }
  return [...rows.values()].sort((a, b) => a.name.localeCompare(b.name, treeSortLocale()))
}

function recoverySourceTargetOption(source: FlowSourceRow, sourceHostId: string): RecoveryTargetNodeOption {
  const sourceSummary = recoverySourceSummaryFromRow(source, source.id, source.name)
  return {
    value: source.id,
    label: sourceSummary.displayName,
    ipLabel: sourceSummary.ipLine,
    typeLabel: sourceSummary.typeLabel,
    group: 'original',
    isOriginalPrimary: source.id === sourceHostId,
    sourceType: source.type,
    sourceSummary,
    source,
    selectable: restoreTargetCatalog.isSelectable(source.id),
    unavailableReason: source.availability !== 'online' ? 'offline' : restoreTargetCatalog.isSelectable(source.id) ? null : 'busy',
  }
}

function recoveryOriginalTargetOptions(sourceHostId: string): RecoveryTargetNodeOption[] {
  const source = backupSelectableById.value.get(sourceHostId) ?? recoveryOriginalSourceById(sourceHostId)
  if (!source || !restoreTargetCatalog.isSelectable(sourceHostId)) return []
  return [recoverySourceTargetOption(source, sourceHostId)]
}

function recoveryTargetNodeOptionsForSource(sourceHostId: string): RecoveryTargetNodeOption[] {
  const currentTargetId = recoveryTargetNodeModelForSource(sourceHostId)
  return restoreTargetCatalog.options([currentTargetId])
    .map(({ source }) => recoverySourceTargetOption(mapBackupSelectableToFlowRow(source), sourceHostId))
}


function recoveryTargetNodeModelForSource(hostId: string) {
  return recoveryDestConfiguredEntryForHost(hostId)?.hostId || ''
}

function recoveryTargetNodeSelectModelForSource(hostId: string) {
  const current = recoveryTargetNodeModelForSource(hostId)
  return current === hostId ? '' : current
}

function recoverySourceHostButtonOption(hostId: string) {
  return recoveryOriginalTargetOptions(hostId)[0] || null
}

function isRecoverySourceHostSelected(hostId: string) {
  return recoveryTargetNodeModelForSource(hostId) === hostId
}

function toggleRecoverySourceTargetForSource(hostId: string) {
  setRecoveryTargetNodeForSource(hostId, isRecoverySourceHostSelected(hostId) ? '' : hostId)
}

function setRecoveryDirTargetPathForSource(hostId: string, targetPath: string) {
  const entries = recoveryDirEntriesForHost(hostId)
  if (!entries.length) return
  recDirSelectionsByHost.value = {
    ...recDirSelectionsByHost.value,
    [hostId]: entries.map((entry) => ({
      ...entry,
      targetPath,
      targetPathValidation: targetPath ? 'valid' : undefined,
    })),
  }
  syncRecDirKeysFromSelections()
}

function setRecoveryTargetNodeForSource(hostId: string, optionValue: string | number) {
  if (!optionValue) {
    setRecoveryDestEntriesForHost(hostId, [])
    setRecoveryDirTargetPathForSource(hostId, '')
    return
  }
  const value = String(optionValue)
  if (!restoreTargetCatalog.isSelectable(value)) return
  const parsed = parseEndpointUiId(value)
  if (!parsed && !backupSelectableById.value.get(value)) return
  const entry: RecoveryDestinationEntry = {
    id: `rde-${hostId}-${value.replace(/[^a-zA-Z0-9_-]/g, '-')}`,
    hostId: value,
    dirKey: '',
    currentNodeKey: '',
    customSubdir: '',
    expandedKeys: [],
  }
  setRecoveryDestEntriesForHost(hostId, [entry])
  setRecoveryDirTargetPathForSource(hostId, '')
}

function targetNodeForSource(hostId: string) {
  const parsed = parseEndpointUiId(recoveryDestConfiguredEntryForHost(hostId)?.hostId || '')
  if (!parsed) return null
  if (parsed.type === 'nas') {
    const source = backupSelectableById.value.get(endpointUiId('nas', parsed.refId))
    const boundNodeId = source?.bound_node_id
    return boundNodeId ? recoveryNodeRows.value.find((node) => node.id === boundNodeId && node.role === 'proxy') || null : null
  }
  return recoveryNodeRows.value.find((node) => node.id === parsed.refId) || null
}

function targetNodeSourceIdForSource(hostId: string) {
  const parsed = parseEndpointUiId(recoveryDestConfiguredEntryForHost(hostId)?.hostId || '')
  if (parsed) return restoreDirectoryBrowseSourceId(endpointUiId(parsed.type, parsed.refId))
  return restoreDirectoryBrowseSourceId(recoveryDestConfiguredEntryForHost(hostId)?.hostId || '')
}

function hasRecoverySnapshotDirectoryDetail(detail: BackupSourceSnapshot | undefined) {
  if (!Array.isArray(detail?.directories) || !detail.directories.length) return false
  const snapshotStatus = String(detail.status || '').toLowerCase()
  if (snapshotStatus === 'available' || snapshotStatus === 'partial' || snapshotStatus === 'failed') {
    return detail.directories.every((dir) => {
      const status = String(dir.status || '').toLowerCase()
      return status === 'available' || status === 'failed' || status === 'cancelled' || status === 'deleted'
    })
  }
  return true
}

function mergeRecoverySnapshotDetail(detail: BackupSourceSnapshot) {
  const snapshotId = Number(detail.id)
  if (!Number.isFinite(snapshotId) || snapshotId <= 0) return
  recoverySnapshotDetails.value.set(snapshotId, detail)
  const index = backupSnapshotRows.value.findIndex((row) => Number(row.id) === snapshotId)
  if (index >= 0) {
    backupSnapshotRows.value[index] = { ...backupSnapshotRows.value[index], ...detail }
  } else {
    backupSnapshotRows.value.push(detail)
  }
  recoverySnapshotBrowseRevision.value += 1
}

async function ensureRecoverySnapshotDetails(snapshotIds: number[]) {
  const uniqueIds = [...new Set(snapshotIds.filter((id) => Number.isFinite(id) && id > 0))]
  const missing = uniqueIds.filter((snapshotId) => {
    const cached = recoverySnapshotDetails.value.get(snapshotId)
    if (hasRecoverySnapshotDirectoryDetail(cached)) return false
    const listed = backupSnapshotRows.value.find((row) => Number(row.id) === snapshotId)
    if (hasRecoverySnapshotDirectoryDetail(listed)) {
      mergeRecoverySnapshotDetail(listed!)
      return false
    }
    return true
  })
  if (!missing.length) return
  const details = await Promise.all(missing.map((snapshotId) => getBackupSourceSnapshot(snapshotId)))
  details.forEach((detail) => mergeRecoverySnapshotDetail(detail))
}

async function refreshRecoverySnapshotDetails(snapshotIds: number[]) {
  const uniqueIds = [...new Set(snapshotIds.filter((id) => Number.isFinite(id) && id > 0))]
  if (!uniqueIds.length) return
  const details = await Promise.all(uniqueIds.map((snapshotId) => getBackupSourceSnapshot(snapshotId)))
  details.forEach((detail) => mergeRecoverySnapshotDetail(detail))
}

function availableDirectoriesForSnapshot(snapshotId: number) {
  const detail = recoverySnapshotDetails.value.get(snapshotId)
    ?? backupSnapshotRows.value.find((row) => Number(row.id) === snapshotId)
  return (detail?.directories || []).filter((dir) => dir.id && dir.status === 'available')
}

function recoverySnapshotSourceKey(hostId: string) {
  const parsed = parseEndpointUiId(hostId)
  if (!parsed || (parsed.type !== 'agent' && parsed.type !== 'nas')) return ''
  return `${parsed.type}:${parsed.refId}`
}

function invalidateRecoverySnapshotLists(backupIds: string[]) {
  const sourceKeys = new Set(
    backupIds
      .map((backupId) => recoverySnapshotSourceKey(backupSourceHostId(backupId)))
      .filter(Boolean),
  )
  for (const sourceKey of sourceKeys) delete recoverySnapshotLists[sourceKey]
  recoveryPlanSnapshotMap.value = {}
}

function recoverySnapshotListState(sourceKey: string): RecoverySnapshotListState {
  if (!recoverySnapshotLists[sourceKey]) {
    recoverySnapshotLists[sourceKey] = {
      page: 0,
      pageSize: RECOVERY_SNAPSHOT_PAGE_SIZE,
      count: 0,
      items: [],
      loading: false,
      loadingMore: false,
      loaded: false,
      error: '',
    }
  }
  return recoverySnapshotLists[sourceKey]
}

function recoverySnapshotListStateForHost(hostId: string) {
  return recoverySnapshotListState(recoverySnapshotSourceKey(hostId))
}

function mergeSnapshotListItems(existing: BackupSourceSnapshot[], incoming: BackupSourceSnapshot[]) {
  const rows = new Map<number, BackupSourceSnapshot>()
  for (const item of existing) rows.set(Number(item.id), item)
  for (const item of incoming) rows.set(Number(item.id), item)
  return [...rows.values()].sort((a, b) =>
    new Date(snapshotTime(b)).getTime() - new Date(snapshotTime(a)).getTime(),
  )
}

async function loadRecoverySnapshotsForSource(hostId: string, opts: { reset?: boolean; loadMore?: boolean } = {}) {
  const parsed = parseEndpointUiId(hostId)
  if (!parsed || (parsed.type !== 'agent' && parsed.type !== 'nas')) return
  const sourceKey = recoverySnapshotSourceKey(hostId)
  const state = recoverySnapshotListState(sourceKey)
  if (state.loading || state.loadingMore) return
  const reset = opts.reset || !state.loaded
  const nextPage = reset ? 1 : state.page + 1
  if (!reset && state.items.length >= state.count && state.count > 0) return
  state.error = ''
  if (opts.loadMore) state.loadingMore = true
  else state.loading = true
  try {
    const result = await listBackupSourceSnapshots({
      source_type: parsed.type,
      source_ref_id: parsed.refId,
      status: 'available,partial',
      ordering: '-finished_at',
      page: nextPage,
      page_size: state.pageSize,
      include_directory_snapshots: 1,
    })
    state.page = nextPage
    state.count = result.count
    state.items = reset ? result.results : mergeSnapshotListItems(state.items, result.results)
    state.loaded = true
    for (const snapshot of result.results) {
      if (Array.isArray(snapshot.directories)) mergeRecoverySnapshotDetail(snapshot)
    }
  } catch (e) {
    state.error = apiErrorMessage(e, t('protection.backupsPage.snapshotLoadFailed'))
    throw e
  } finally {
    state.loading = false
    state.loadingMore = false
  }
}

function sourceSnapshotFallbackOptions(hostId: string): RecoverySnapshotOption[] {
  const seen = new Set<string>()
  const options: RecoverySnapshotOption[] = []
  for (const backup of recBackupsForSource(hostId)) {
    for (const snapshot of backup.snapshots) {
      if (!isRecoverableSnapshot(snapshot) || seen.has(snapshot.id)) continue
      seen.add(snapshot.id)
      options.push({
        id: snapshot.id,
        time: snapshot.endTime,
        status: snapshot.status || 'available',
        sizeBytes: Number(snapshot.sizeBytes || 0),
        fileCount: Number(snapshot.fileCount || 0),
        dirCount: Number(snapshot.dirCount || 0),
        dirs: snapshot.dirs,
        directories: [],
      })
    }
  }
  return options.sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime())
}

function recoverySnapshotOption(snapshot: BackupSourceSnapshot): RecoverySnapshotOption {
  const sourceHostId = endpointUiId(snapshot.source_type, Number(snapshot.source_ref_id))
  return {
    id: String(snapshot.id),
    time: snapshotTime(snapshot),
    status: snapshot.status || 'available',
    sizeBytes: Number(snapshot.total_size_bytes || 0),
    fileCount: Number(snapshot.file_count || 0),
    dirCount: Number(snapshot.dir_count || 0),
    dirs: (snapshot.directories || []).map((dir) => ({
      hostId: sourceHostId,
      hostName: displayNameForSource(snapshot.source_type, Number(snapshot.source_ref_id), snapshot.source_display_name || ''),
      path: dir.source_path,
      pathType: normalizeDemoPathType(dir.path_type),
      sizeBytes: Number(dir.size_bytes || 0),
      fileCount: Number(dir.file_count || 0),
      innerDirCount: Number(dir.dir_count || 0),
    })),
    directories: snapshot.directories || [],
    raw: snapshot,
  }
}

function recoverySnapshotOptionsForSource(hostId: string): RecoverySnapshotOption[] {
  const sourceKey = recoverySnapshotSourceKey(hostId)
  const state = sourceKey ? recoverySnapshotListState(sourceKey) : null
  if (state?.items.length) return state.items.map(recoverySnapshotOption)
  return sourceSnapshotFallbackOptions(hostId)
}

function recoverySnapshotById(hostId: string, snapshotId: string) {
  return recoverySnapshotOptionsForSource(hostId).find((snapshot) => snapshot.id === snapshotId) || null
}

function recoverySnapshotHasMore(hostId: string) {
  const state = recoverySnapshotListStateForHost(hostId)
  return state.loaded && state.items.length < state.count
}

function recoverySnapshotLoadMoreLabel(hostId: string) {
  const state = recoverySnapshotListStateForHost(hostId)
  const loaded = state.items.length
  const total = state.count || loaded
  return state.loadingMore
    ? t('common.loading')
    : t('protection.backupsPage.snapshotLoadMore', { loaded, total })
}

function usableSnapshotDirectories(snapshot: RecoverySnapshotOption | BackupSourceSnapshot | null | undefined) {
  const directories = 'directories' in (snapshot || {})
    ? (snapshot as RecoverySnapshotOption | BackupSourceSnapshot).directories || []
    : []
  return directories.filter((dir) =>
    dir.id > 0
    && dir.status === 'available'
    && Boolean(String(dir.kopia_snapshot_id || '').trim()),
  )
}

function restorePlanSnapshotCompatibility(plan: RecoveryPlanSummary, snapshot: RecoverySnapshotOption): SnapshotCompatibility {
  const status = String(snapshot.status || '').toLowerCase()
  if (status !== 'available' && status !== 'partial') {
    return { compatible: false, reason: t('protection.backupsPage.snapshotReasonStatusUnavailable') }
  }
  const directories = usableSnapshotDirectories(snapshot)
  if (!directories.length) {
    return { compatible: false, reason: t('protection.backupsPage.snapshotReasonNoUsableDirectories') }
  }
  for (const sourceDir of plan.sourceDirs) {
    if (sourceDir.scope === 'snapshot') continue
    const path = String(sourceDir.path || '').trim()
    const matched = directories.find((dir) => path && dir.source_path && isSameOrAncestorPath(dir.source_path, path))
    if (!matched) {
      return {
        compatible: false,
        reason: t('protection.backupsPage.snapshotReasonMissingBackupPath', { path }),
      }
    }
  }
  return { compatible: true, reason: '' }
}

function defaultSnapshotIdForSource(hostId: string) {
  return recoverySnapshotOptionsForSource(hostId)[0]?.id || ''
}

function defaultCompatibleSnapshotIdForPlan(plan: RecoveryPlanSummary) {
  return recoverySnapshotOptionsForSource(backupSourceHostId(plan.backupId))
    .find((snapshot) => restorePlanSnapshotCompatibility(plan, snapshot).compatible)?.id || ''
}

function selectedSnapshotForSource(hostId: string) {
  const backup = recBackupsForSource(hostId)[0]
  if (!backup) return null
  const snapshotId = recSnapshotMap.value[backup.id]
  return recoverySnapshotById(hostId, snapshotId)
    ?? recoverySnapshotOptionsForSource(hostId)[0]
    ?? null
}

function selectedSnapshotNumericIdForSource(hostId: string) {
  const backup = recBackupsForSource(hostId)[0]
  const snapshotId = backup ? recSnapshotMap.value[backup.id] || selectedSnapshotForSource(hostId)?.id || '' : ''
  const numeric = Number(snapshotId)
  return Number.isFinite(numeric) && numeric > 0 ? numeric : 0
}

function availableSnapshotDirectoriesForSource(hostId: string) {
  const snapshotId = selectedSnapshotNumericIdForSource(hostId)
  if (snapshotId > 0) {
    const dirs = availableDirectoriesForSnapshot(snapshotId)
    if (dirs.length) return dirs
  }
  return []
}

function recoveryDirEntryRangeModel(entry: RecoveryDirSelectionEntry) {
  return entry.scope === 'snapshot' ? '__snapshot__' : entry.path
}

function recoverySnapshotRangeInputIcon(entry: RecoveryDirSelectionEntry) {
  if (entry.sourcePathValidation !== 'valid') return TextCursorInput
  if (entry.scope === 'snapshot' || entry.sourceKind === 'snapshot') return Camera
  return entry.sourceKind === 'file' ? File : FolderOpen
}

function recoverySnapshotRangeInputIconClass(entry: RecoveryDirSelectionEntry) {
  if (entry.sourcePathValidation !== 'valid') return ''
  if (entry.scope === 'snapshot' || entry.sourceKind === 'snapshot') return 'create-dir-row__icon--snapshot'
  return entry.sourceKind === 'file' ? 'create-dir-row__icon--file' : 'create-dir-row__icon--folder'
}

function recoveryTargetDirInputIcon(entry: RecoveryDirSelectionEntry) {
  return entry.targetPathValidation === 'valid' && entry.targetPath ? FolderOpen : TextCursorInput
}

function recoveryTargetDirInputIconClass(entry: RecoveryDirSelectionEntry) {
  return entry.targetPathValidation === 'valid' && entry.targetPath ? 'create-dir-row__icon--folder' : ''
}

function isRecoverySnapshotRangeInputInvalid(entry: RecoveryDirSelectionEntry) {
  return entry.sourcePathValidation === 'invalid'
}

function isRecoveryTargetDirInputInvalid(entry: RecoveryDirSelectionEntry) {
  return entry.targetPathValidation === 'invalid'
}

function recoverySnapshotRangeInputError(entry: RecoveryDirSelectionEntry) {
  if (!isRecoverySnapshotRangeInputInvalid(entry)) return ''
  if (entry.sourcePathError) return entry.sourcePathError
  if (!entry.path && entry.scope !== 'snapshot') return t('protection.backupsPage.msgManualPathRequired')
  return t('protection.backupsPage.msgRestoreScopeVerifyFailed')
}

function recoveryTargetDirInputError(entry: RecoveryDirSelectionEntry) {
  if (!isRecoveryTargetDirInputInvalid(entry)) return ''
  if (entry.targetPathError) return entry.targetPathError
  if (!entry.targetPath) return t('protection.backupsPage.msgRestoreDirectoryRequired')
  return t('protection.backupsPage.msgRestoreDirectoryVerifyFailed')
}

function recoveryPathErrorNoticeKey(hostId: string, entry: RecoveryDirSelectionEntry, kind: 'source' | 'target') {
  return `${recoveryDirEntryPickerKey(hostId, entry.id)}:${kind}`
}

function isRecoverySnapshotRangeErrorVisible(hostId: string, entry: RecoveryDirSelectionEntry) {
  const message = recoverySnapshotRangeInputError(entry)
  return Boolean(message) && dismissedRecoveryPathErrors[recoveryPathErrorNoticeKey(hostId, entry, 'source')] !== message
}

function isRecoveryTargetDirErrorVisible(hostId: string, entry: RecoveryDirSelectionEntry) {
  const message = recoveryTargetDirInputError(entry)
  return Boolean(message) && dismissedRecoveryPathErrors[recoveryPathErrorNoticeKey(hostId, entry, 'target')] !== message
}

function dismissRecoveryPathError(hostId: string, entry: RecoveryDirSelectionEntry, kind: 'source' | 'target') {
  const message = kind === 'source' ? recoverySnapshotRangeInputError(entry) : recoveryTargetDirInputError(entry)
  if (message) dismissedRecoveryPathErrors[recoveryPathErrorNoticeKey(hostId, entry, kind)] = message
}

function restoreRecoveryPathErrorNotice(hostId: string, entry: RecoveryDirSelectionEntry, kind: 'source' | 'target') {
  delete dismissedRecoveryPathErrors[recoveryPathErrorNoticeKey(hostId, entry, kind)]
}

function recoverySnapshotPickerRootNodes(hostId: string): RecoverySnapshotPickerNode[] {
  const nodes: RecoverySnapshotPickerNode[] = [
    {
      id: `${hostId}:snapshot`,
      label: t('protection.backupsPage.recoveryWholeSnapshot'),
      path: '',
      type: 'snapshot',
      isLeaf: true,
    },
  ]
  for (const dir of availableSnapshotDirectoriesForSource(hostId)) {
    if (!dir.source_path) continue
    const sourceType = normalizeDemoPathType(dir.path_type) === 'file' ? 'file' : 'dir'
    nodes.push({
      id: `${hostId}:${sourceType}:${dir.id || dir.source_path}`,
      label: dir.source_path,
      path: dir.source_path,
      type: sourceType,
      directoryId: dir.id || undefined,
      browsePath: '',
      sourceRootPath: dir.source_path,
      isLeaf: sourceType === 'file' || !dir.id,
    })
  }
  return nodes
}

function recoverySnapshotPickerNodeId(hostId: string, directoryId: number | undefined, path: string, type: string) {
  return `${hostId}:${directoryId || 0}:${type}:${path || '__root__'}`
}

function mapRecoverySnapshotPickerChildren(
  hostId: string,
  data: RecoverySnapshotPickerNode,
  entries: Awaited<ReturnType<typeof browseRestoreSnapshotDirectory>>['entries'],
): RecoverySnapshotPickerNode[] {
  const sep: '/' | '\\' = isWindowsPath(data.sourceRootPath || data.path) ? '\\' : '/'
  return entries.map((entry) => {
    const isDir = entry.type === 'dir'
    const relativePath = (entry.path ?? '').replace(/^\/+/, '')
    const fullPath = relativePath
      ? joinPathBySep(data.sourceRootPath || data.path, relativePath, sep)
      : data.sourceRootPath || data.path
    return {
      id: recoverySnapshotPickerNodeId(hostId, data.directoryId, fullPath, isDir ? 'dir' : 'file'),
      label: entry.name || basenamePath(fullPath),
      path: fullPath,
      type: isDir ? 'dir' : 'file',
      directoryId: data.directoryId,
      browsePath: relativePath,
      sourceRootPath: data.sourceRootPath || data.path,
      isLeaf: !isDir,
    }
  })
}

async function loadRecoverySnapshotPickerNode(hostId: string, node: unknown, resolve: (nodes: RecoverySnapshotPickerNode[]) => void) {
  const treeNode = node as { level?: number; data?: RecoverySnapshotPickerNode }
  if (!treeNode.level) {
    const snapshotId = selectedSnapshotNumericIdForSource(hostId)
    if (snapshotId > 0) {
      try {
        await ensureRecoverySnapshotDetails([snapshotId])
      } catch (e) {
        showApiError(e)
      }
    }
    resolve(recoverySnapshotPickerRootNodes(hostId))
    return
  }
  const data = treeNode.data
  if (!data?.directoryId || data.type !== 'dir') {
    resolve([])
    return
  }
  const targetNode = targetNodeForSource(hostId)
  if (!targetNode) {
    resolve([])
    return
  }
  try {
    const result = await browseRestoreSnapshotDirectory(data.directoryId, {
      target_node_id: targetNode.id,
      path: data.browsePath || '',
      limit: 500,
    })
    resolve(mapRecoverySnapshotPickerChildren(hostId, data, result.entries))
  } catch (e) {
    showApiError(e)
    resolve([])
  }
}

function refreshRecoverySnapshotDirectory(
  hostId: string,
  entry: RecoveryDirSelectionEntry,
  data: RecoverySnapshotPickerNode,
) {
  if (!data.directoryId || data.type !== 'dir') return
  const targetNode = targetNodeForSource(hostId)
  if (!targetNode) return
  const treeKey = recoveryDirectoryTreeKey(hostId, entry.id, 'snapshot')
  return refreshRecoveryDirectoryNode(treeKey, data.id, data, async () => {
    const result = await browseRestoreSnapshotDirectory(data.directoryId!, {
      target_node_id: targetNode.id,
      path: data.browsePath || '',
      limit: 500,
    }, { cache: 'no-store' })
    return mapRecoverySnapshotPickerChildren(hostId, data, result.entries)
  })
}

function setRecoveryDirEntrySnapshotRange(
  hostId: string,
  entryId: string,
  value: string,
  sourceKind: RecoveryDirSelectionEntry['sourceKind'] = value === '__snapshot__' ? 'snapshot' : 'dir',
) {
  delete dismissedRecoveryPathErrors[`${recoveryDirEntryPickerKey(hostId, entryId)}:source`]
  if (value === '__snapshot__') {
    updateRecoveryDirEntry(hostId, entryId, {
      scope: 'snapshot',
      path: '',
      sourceKind: 'snapshot',
      sourceSnapshotDirectoryId: undefined,
      sourcePathValidation: 'valid',
      sourcePathError: '',
    })
    return
  }
  const dir = availableSnapshotDirectoriesForSource(hostId).find((item) => item.source_path === value)
  updateRecoveryDirEntry(hostId, entryId, {
    scope: 'directory',
    path: value,
    hostName: store.getNodeName(hostId),
    sourceKind,
    sourceSnapshotDirectoryId: dir?.id || undefined,
    sourcePathValidation: 'valid',
    sourcePathError: '',
  })
}

function recoveryDirEntryPickerKey(hostId: string, entryId: string) {
  return `${hostId}:${entryId}`
}

function isRecoverySnapshotRangeValidating(hostId: string, entry: RecoveryDirSelectionEntry) {
  return Boolean(recSnapshotRangePathValidating[recoveryDirEntryPickerKey(hostId, entry.id)])
}

function isRecoveryTargetDirValidating(hostId: string, entry: RecoveryDirSelectionEntry) {
  return Boolean(recRecoveryTargetDirPathValidating[recoveryDirEntryPickerKey(hostId, entry.id)])
}

function setRecoveryTargetDirTreeLoading(key: string, loading: boolean) {
  if (!key) return
  const current = recRecoveryTargetDirTreeLoading[key] || 0
  if (loading) {
    recRecoveryTargetDirTreeLoading[key] = current + 1
    return
  }
  const next = Math.max(0, current - 1)
  if (next === 0) delete recRecoveryTargetDirTreeLoading[key]
  else recRecoveryTargetDirTreeLoading[key] = next
}

function isRecoveryTargetDirTreeLoading(hostId: string, entry: RecoveryDirSelectionEntry) {
  return Boolean(recRecoveryTargetDirTreeLoading[recoveryDirEntryPickerKey(hostId, entry.id)])
}

function updateRecoverySnapshotRangeInput(hostId: string, entry: RecoveryDirSelectionEntry, value: string) {
  restoreRecoveryPathErrorNotice(hostId, entry, 'source')
  const nextValue = String(value || '').trim()
  if (nextValue === t('protection.backupsPage.recoveryWholeSnapshot')) {
    setRecoveryDirEntrySnapshotRange(hostId, entry.id, '__snapshot__', 'snapshot')
    return
  }
  updateRecoveryDirEntry(hostId, entry.id, {
    scope: 'directory',
    path: nextValue,
    hostName: store.getNodeName(hostId),
    sourceKind: undefined,
    sourceSnapshotDirectoryId: undefined,
    sourcePathValidation: nextValue ? 'pending' : 'invalid',
    sourcePathError: nextValue ? '' : t('protection.backupsPage.msgManualPathRequired'),
  })
}

function findSnapshotDirectoryForAbsolutePath(hostId: string, path: string) {
  return [...availableSnapshotDirectoriesForSource(hostId)]
    .filter((directory) => directory.id && directory.source_path && isSameOrAncestorPath(directory.source_path, path))
    .sort((a, b) => b.source_path.length - a.source_path.length)[0] || null
}

async function validateRecoverySnapshotRangeInput(hostId: string, entry: RecoveryDirSelectionEntry) {
  const rawPath = recoverySnapshotRangeDisplayForEntry(hostId, entry).trim()
  if (!rawPath || rawPath === t('protection.backupsPage.recoveryWholeSnapshot')) {
    if (rawPath === t('protection.backupsPage.recoveryWholeSnapshot')) {
      setRecoveryDirEntrySnapshotRange(hostId, entry.id, '__snapshot__', 'snapshot')
      return
    }
    const message = t('protection.backupsPage.msgManualPathRequired')
    updateRecoveryDirEntry(hostId, entry.id, { sourcePathValidation: 'invalid', sourcePathError: message })
    ElMessage.warning({ message, grouping: true })
    return
  }
  if (!isAbsoluteSourcePath(rawPath)) {
    const message = t('protection.backupsPage.msgManualPathAbsolute')
    updateRecoveryDirEntry(hostId, entry.id, { sourcePathValidation: 'invalid', sourcePathError: message })
    ElMessage.warning({ message, grouping: true })
    return
  }
  try {
    await ensureRecoverySnapshotBrowseOptions(hostId)
  } catch (e) {
    showApiError(e)
    return
  }
  const directory = findSnapshotDirectoryForAbsolutePath(hostId, rawPath)
  if (!directory?.id) {
    const message = t('protection.backupsPage.msgRestoreScopeOutsideSnapshot')
    updateRecoveryDirEntry(hostId, entry.id, { sourcePathValidation: 'invalid', sourcePathError: message })
    ElMessage.warning({ message, grouping: true })
    return
  }
  const targetNode = targetNodeForSource(hostId)
  if (!targetNode) {
    const message = t('protection.backupsPage.msgConfigureRecoveryDest')
    updateRecoveryDirEntry(hostId, entry.id, { sourcePathValidation: 'invalid', sourcePathError: message })
    ElMessage.warning({ message, grouping: true })
    return
  }
  const relativePath = relativeSnapshotPath(directory.source_path, rawPath)
  const key = recoveryDirEntryPickerKey(hostId, entry.id)
  recSnapshotRangePathValidating[key] = true
  try {
    const pathInfo = relativePath
      ? await getRestoreSnapshotPathInfo(directory.id, {
          target_node_id: targetNode.id,
          path: relativePath,
        })
      : { path: '', is_dir: true }
    updateRecoveryDirEntry(hostId, entry.id, {
      scope: 'directory',
      path: rawPath,
      hostName: store.getNodeName(hostId),
      sourceKind: pathInfo.is_dir ? 'dir' : 'file',
      sourceSnapshotDirectoryId: directory.id,
      sourcePathValidation: 'valid',
      sourcePathError: '',
    })
    recSnapshotRangePickerVisible[key] = false
  } catch (e) {
    const message = apiErrorMessageI18n(e, t, t('protection.backupsPage.msgRestoreScopeVerifyFailed'))
    updateRecoveryDirEntry(hostId, entry.id, { sourcePathValidation: 'invalid', sourcePathError: message })
    ElMessage.error({ message, grouping: true })
  } finally {
    recSnapshotRangePathValidating[key] = false
  }
}

function recoverySnapshotRangeDisplayForEntry(hostId: string, entry: RecoveryDirSelectionEntry) {
  const value = recoveryDirEntryRangeModel(entry)
  if (value === '__snapshot__') return t('protection.backupsPage.recoveryWholeSnapshot')
  return entry.path || ''
}

async function ensureRecoverySnapshotBrowseOptions(hostId: string) {
  const snapshotId = selectedSnapshotNumericIdForSource(hostId)
  if (snapshotId > 0) {
    await ensureRecoverySnapshotDetails([snapshotId])
  }
}

function setRecoverySnapshotRangePickerVisible(hostId: string, entry: RecoveryDirSelectionEntry, visible: boolean) {
  const key = recoveryDirEntryPickerKey(hostId, entry.id)
  recSnapshotRangePickerVisible[key] = visible
  if (visible) {
    void ensureRecoverySnapshotBrowseOptions(hostId).catch((e) => {
      showApiError(e)
    })
  }
}

function isRecoverySnapshotRangePickerVisible(hostId: string, entry: RecoveryDirSelectionEntry) {
  return Boolean(recSnapshotRangePickerVisible[recoveryDirEntryPickerKey(hostId, entry.id)])
}

function pickRecoverySnapshotRange(hostId: string, entry: RecoveryDirSelectionEntry, node: RecoverySnapshotPickerNode) {
  setRecoveryDirEntrySnapshotRange(
    hostId,
    entry.id,
    node.type === 'snapshot' ? '__snapshot__' : node.path,
    node.type === 'snapshot' ? 'snapshot' : node.type === 'file' ? 'file' : 'dir',
  )
  recSnapshotRangePickerVisible[recoveryDirEntryPickerKey(hostId, entry.id)] = false
}

function setRecoveryTargetDirPickerVisible(hostId: string, entry: RecoveryDirSelectionEntry, visible: boolean) {
  const key = recoveryDirEntryPickerKey(hostId, entry.id)
  recRecoveryTargetDirPickerVisible[key] = visible
}

function isRecoveryTargetDirPickerVisible(hostId: string, entry: RecoveryDirSelectionEntry) {
  return Boolean(recRecoveryTargetDirPickerVisible[recoveryDirEntryPickerKey(hostId, entry.id)])
}

function pickRecoveryTargetDir(hostId: string, entry: RecoveryDirSelectionEntry, path: string) {
  restoreRecoveryPathErrorNotice(hostId, entry, 'target')
  updateRecoveryDirEntry(hostId, entry.id, { targetPath: path, targetPathValidation: 'valid', targetPathError: '' })
  recRecoveryTargetDirPickerVisible[recoveryDirEntryPickerKey(hostId, entry.id)] = false
}

function updateRecoveryTargetDirInput(hostId: string, entry: RecoveryDirSelectionEntry, value: string) {
  restoreRecoveryPathErrorNotice(hostId, entry, 'target')
  const nextValue = String(value || '').trim()
  updateRecoveryDirEntry(hostId, entry.id, {
    targetPath: nextValue,
    targetPathValidation: nextValue ? 'pending' : 'invalid',
    targetPathError: nextValue ? '' : t('protection.backupsPage.msgRestoreDirectoryRequired'),
  })
}

async function validateRecoveryTargetDirInput(hostId: string, entry: RecoveryDirSelectionEntry) {
  const path = String(entry.targetPath || '').trim()
  if (!path) {
    const message = t('protection.backupsPage.msgRestoreDirectoryRequired')
    updateRecoveryDirEntry(hostId, entry.id, { targetPathValidation: 'invalid', targetPathError: message })
    ElMessage.warning({ message, grouping: true })
    return
  }
  if (!isAbsoluteSourcePath(path)) {
    const message = t('protection.backupsPage.msgManualPathAbsolute')
    updateRecoveryDirEntry(hostId, entry.id, { targetPathValidation: 'invalid', targetPathError: message })
    ElMessage.warning({ message, grouping: true })
    return
  }
  const sourceId = targetNodeSourceIdForSource(hostId)
  if (!sourceId) {
    const message = t('protection.backupsPage.msgConfigureRecoveryDest')
    updateRecoveryDirEntry(hostId, entry.id, { targetPathValidation: 'invalid', targetPathError: message })
    ElMessage.warning({ message, grouping: true })
    return
  }
  const key = recoveryDirEntryPickerKey(hostId, entry.id)
  recRecoveryTargetDirPathValidating[key] = true
  try {
    const pathInfo = await getBackupSourcePathInfo({
      source_id: sourceId,
      path,
      timeout: 10,
    })
    if (pathInfo.is_dir === false) {
      const message = t('protection.backupsPage.msgRestoreDirectoryMustBeDirectory')
      updateRecoveryDirEntry(hostId, entry.id, { targetPathValidation: 'invalid', targetPathError: message })
      ElMessage.warning({ message, grouping: true })
      return
    }
    updateRecoveryDirEntry(hostId, entry.id, {
      targetPath: pathInfo.path || path,
      targetPathValidation: 'valid',
      targetPathError: '',
    })
    recRecoveryTargetDirPickerVisible[key] = false
  } catch (e) {
    const message = apiErrorMessageI18n(e, t, t('protection.backupsPage.msgRestoreDirectoryVerifyFailed'))
    updateRecoveryDirEntry(hostId, entry.id, { targetPathValidation: 'invalid', targetPathError: message })
    ElMessage.error({ message, grouping: true })
  } finally {
    recRecoveryTargetDirPathValidating[key] = false
  }
}

function recoveryTargetDirectoryBrowsePath(hostId: string, node: { level?: number; data?: RecoveryTargetDirPickerNode }) {
  return node.level ? node.data?.path || '' : ''
}

async function fetchRecoveryTargetDirectoryChildren(
  sourceId: string,
  parentPath: string,
  forceRefresh = false,
): Promise<RecoveryTargetDirPickerNode[]> {
  const result = await listBackupSourceDirectories({
    source_id: sourceId,
    path: parentPath || undefined,
    limit: 500,
  }, forceRefresh ? { cache: 'no-store' } : undefined)
  const targetSource = backupSelectableById.value.get(sourceId) ?? recoveryOriginalSourceById(sourceId)
  const rows = selectBackupSourceDirectoryTreeEntries({
    source: targetSource,
    parentPath,
    result,
  })
    .filter((entry) => entry?.is_dir !== false) as BackupSourceDirectoryEntry[]
  return rows.map((entry) => ({
    id: `${sourceId}:${entry.path}`,
    label: entry.label || basenamePath(entry.path) || entry.path,
    path: entry.path,
    isLeaf: Boolean(entry.isLeaf),
  }))
}

async function loadRecoveryTargetDirPickerNode(hostId: string, entry: RecoveryDirSelectionEntry, node: unknown, resolve: (nodes: RecoveryTargetDirPickerNode[]) => void) {
  const sourceId = targetNodeSourceIdForSource(hostId)
  if (!sourceId) {
    resolve([])
    return
  }
  const treeNode = node as { level?: number; data?: RecoveryTargetDirPickerNode }
  const parentPath = recoveryTargetDirectoryBrowsePath(hostId, treeNode)
  const loadingKey = recoveryDirEntryPickerKey(hostId, entry.id)
  setRecoveryTargetDirTreeLoading(loadingKey, true)
  try {
    resolve(await fetchRecoveryTargetDirectoryChildren(sourceId, parentPath))
  } catch (e) {
    showApiErrorI18n(e, t('protection.backupsPage.dirTreeLoadFailed'))
    resolve([])
  } finally {
    setRecoveryTargetDirTreeLoading(loadingKey, false)
  }
}

function refreshRecoveryTargetDirectory(
  hostId: string,
  entry: RecoveryDirSelectionEntry,
  data: RecoveryTargetDirPickerNode,
) {
  const sourceId = targetNodeSourceIdForSource(hostId)
  if (!sourceId) return
  const treeKey = recoveryDirectoryTreeKey(hostId, entry.id, 'target')
  return refreshRecoveryDirectoryNode(
    treeKey,
    data.path,
    data,
    () => fetchRecoveryTargetDirectoryChildren(sourceId, data.path, true),
  )
}


type RecRecoveryDirSourceRow = {
  hostId: string
  hostName: string
  sourceSummary: RecoverySourceSummary
  destSummary: RecoverySourceSummary | null
  snapshotLine: string
  entries: RecoveryDirSelectionEntry[]
  configuredEntries: RecoveryDirSelectionEntry[]
  previewEntries: RecoveryDirSelectionEntry[]
  hiddenDirCount: number
  destConfiguredEntry: RecoveryDestinationEntry | null
}

const recRecoveryDirStepReady = computed(() => {
  if (!recBackupSourceGroups.value.length) return false
  return recBackupSourceGroups.value.every((group) =>
    recoveryDirEntriesForHost(group.hostId).some(isRecoveryDirEntryConfigured)
    && Boolean(recConflictPolicyForSource(group.hostId)),
  )
})

const recRecoveryDirSourceRows = computed<RecRecoveryDirSourceRow[]>(() =>
  recBackupSourceGroups.value.map((group) => {
    const entries = recoveryDirEntriesForHost(group.hostId)
    const configuredEntries = entries.filter(isRecoveryDirEntryConfigured)
    return {
      hostId: group.hostId,
      hostName: group.hostName,
      sourceSummary: recoverySourceSummary(group.hostId, group.hostName),
      destSummary: recoveryTargetSummaryForEntry(recoveryDestConfiguredEntryForHost(group.hostId)),
      snapshotLine: recGroupSnapshotDisplayLine(group.hostId),
      entries,
      configuredEntries,
      previewEntries: configuredEntries.slice(0, REC_ENTRY_PREVIEW_MAX),
      hiddenDirCount: Math.max(configuredEntries.length - REC_ENTRY_PREVIEW_MAX, 0),
      destConfiguredEntry: recoveryDestConfiguredEntryForHost(group.hostId),
    }
  }),
)

function isRecRecoveryDirRowExpanded(hostId: string) {
  return recExpandedRecDirHostIds.value.includes(hostId)
}

function onRecRecoveryDirExpandChange(_row: RecRecoveryDirSourceRow, expandedRows: RecRecoveryDirSourceRow[]) {
  const visibleIds = new Set(recRecoveryDirSourceRows.value.map((row) => row.hostId))
  const hiddenExpandedIds = recExpandedRecDirHostIds.value.filter((id) => !visibleIds.has(id))
  recExpandedRecDirHostIds.value = [...hiddenExpandedIds, ...expandedRows.map((row) => row.hostId)]
}

function toggleRecRecoveryDirRow(row: RecRecoveryDirSourceRow) {
  recRecoveryDirSourceTableRef.value?.toggleRowExpansion(row, !isRecRecoveryDirRowExpanded(row.hostId))
}

function recoveryDestinationPathForEntry(entry: RecoveryDestinationConfig | undefined) {
  return decodeDestPath(entry) || ''
}





function recBackupsForSource(hostId: string) {
  return recBackupSourceGroups.value.find((group) => group.hostId === hostId)?.backups ?? []
}

function recGroupSnapshotId(hostId: string) {
  const backups = recBackupsForSource(hostId)
  if (!backups.length) return ''
  const snapshotIds = backups.map((backup) => {
    const selected = recoverySnapshotById(hostId, recSnapshotMap.value[backup.id])
    return selected?.id || defaultSnapshotIdForSource(hostId)
  })
  const first = snapshotIds.find(Boolean) || ''
  if (first && snapshotIds.every((id) => !id || id === first)) return first
  return first
}

function recGroupSnapshotDisplayLine(hostId: string) {
  const snapshotId = recGroupSnapshotId(hostId)
  if (!snapshotId) return '—'
  const snapshot = recoverySnapshotById(hostId, snapshotId)
  return snapshot ? fmtLocalTime(snapshot.time) : '—'
}

function setRecoverySnapshotForSource(hostId: string, snapshotId: string) {
  for (const backup of recBackupsForSource(hostId)) {
    setRecoverySnapshot(backup.id, snapshotId)
  }
  recDirSelectionsByHost.value = { ...recDirSelectionsByHost.value, [hostId]: [] }
  recDirStepInitialized.value = false
  syncRecDirKeysFromSelections()
  if (recStep.value >= 2) {
    if (recStep.value === 2) ensureInitialRecoveryDirStepRows()
    void preloadRecoverySnapshotDirectoriesForDirStep().catch((e) => {
      showApiError(e)
    })
  }
}

async function onRecoverySnapshotSelectVisible(hostId: string, visible: boolean) {
  if (!visible) return
  try {
    await loadRecoverySnapshotsForSource(hostId)
    const current = recGroupSnapshotId(hostId)
    if (!current) setRecoverySnapshotForSource(hostId, defaultSnapshotIdForSource(hostId))
  } catch (e) {
    showApiError(e)
  }
}

function updateRecoverySnapshotSelectValue(hostId: string, value: string | number) {
  const next = String(value || '')
  if (next === RECOVERY_SNAPSHOT_LOAD_MORE_VALUE) {
    void loadRecoverySnapshotsForSource(hostId, { loadMore: true }).catch((e) => showApiError(e))
    return
  }
  setRecoverySnapshotForSource(hostId, next)
}

async function ensureManualRecoverySnapshotDefaults() {
  await Promise.all(recBackupSourceGroups.value.map(async (group) => {
    await loadRecoverySnapshotsForSource(group.hostId)
    if (!recGroupSnapshotId(group.hostId)) {
      setRecoverySnapshotForSource(group.hostId, defaultSnapshotIdForSource(group.hostId))
    }
  }))
}

function setRecoverySnapshot(backupId: string, snapshotId: string) {
  recSnapshotMap.value = { ...recSnapshotMap.value, [backupId]: snapshotId }
  if (backupId === recBackupId.value) recSnapshotId.value = snapshotId
}

function ensureRecoverableRecoverySnapshots() {
  const next = { ...recSnapshotMap.value }
  let changed = false
  for (const backupId of recBackupIds.value) {
    const backup = store.getBackup(backupId)
    if (!backup) continue
    const hostId = backupSourceHostId(backupId)
    const current = recoverySnapshotById(hostId, next[backupId])
    if (current) continue
    const fallbackId = defaultSnapshotIdForSource(hostId) || latestRecoverableBackupSnapshot(backup)?.id || ''
    if (!fallbackId) {
      if (next[backupId]) {
        delete next[backupId]
        changed = true
      }
      continue
    }
    next[backupId] = fallbackId
    changed = true
  }
  if (changed) recSnapshotMap.value = next
  const active = recBackupId.value ? next[recBackupId.value] : ''
  if (active) recSnapshotId.value = active
}

function buildInitialRecoveryDirSelectionsByHost(backupIds: string[]) {
  const map: Record<string, RecoveryDirSelectionEntry[]> = {}
  for (const backupId of backupIds) {
    const backup = store.getBackup(backupId)
    const hostId = backup?.sources[0]?.hostId || ''
    if (!hostId) continue
    if (!(hostId in map)) map[hostId] = []
  }
  return map
}

function recoveryDirEntriesForHost(hostId: string) {
  return recDirSelectionsByHost.value[hostId] || []
}


function isRecoveryDirEntryConfigured(entry: RecoveryDirSelectionEntry) {
  const sourceValid = entry.scope === 'snapshot' || (
    Boolean(entry.path)
    && entry.sourcePathValidation !== 'pending'
    && entry.sourcePathValidation !== 'invalid'
  )
  const targetValid = Boolean(entry.targetPath)
    && entry.targetPathValidation !== 'pending'
    && entry.targetPathValidation !== 'invalid'
  return Boolean(sourceValid && targetValid)
}

function updateRecoveryDirEntry(hostId: string, entryId: string, patch: Partial<RecoveryDirSelectionEntry>) {
  if (!hostId || !entryId) return
  recDirSelectionsByHost.value = {
    ...recDirSelectionsByHost.value,
    [hostId]: recoveryDirEntriesForHost(hostId).map((entry) =>
      entry.id === entryId ? { ...entry, ...patch } : entry,
    ),
  }
  syncRecDirKeysFromSelections()
}

function syncRecDirKeysFromSelections() {
  const next: string[] = []
  for (const group of recBackupSourceGroups.value) {
    for (const backup of group.backups) {
      const snapshotId = recSnapshotMap.value[backup.id]
      const snap = backup.snapshots.find((s) => s.id === snapshotId) ?? latestBackupSnapshot(backup)
      for (const entry of recoveryDirEntriesForHost(group.hostId)) {
        if (!isRecoveryDirEntryConfigured(entry)) continue
        if (entry.scope === 'snapshot') continue
        const snapDir = snap?.dirs.find((dir) => dir.hostId === entry.hostId && dir.path === entry.path)
        next.push(
          dirKey(backup.id, {
            hostId: entry.hostId,
            path: entry.path,
            hostName: entry.hostName || snapDir?.hostName || store.getNodeName(entry.hostId),
            sizeBytes: snapDir?.sizeBytes ?? 0,
            fileCount: snapDir?.fileCount ?? 0,
            innerDirCount: snapDir?.innerDirCount ?? 0,
          }),
        )
      }
    }
  }
  recDirKeys.value = next
}

function syncRecoveryDirSelections() {
  const next: Record<string, RecoveryDirSelectionEntry[]> = { ...recDirSelectionsByHost.value }
  for (const group of recBackupSourceGroups.value) {
    if (!next[group.hostId]) next[group.hostId] = []
  }
  for (const hostId of Object.keys(next)) {
    if (!recBackupSourceGroups.value.some((group) => group.hostId === hostId)) {
      delete next[hostId]
    }
  }
  recDirSelectionsByHost.value = next
  recExpandedRecDirHostIds.value = recExpandedRecDirHostIds.value.filter((hostId) =>
    recBackupSourceGroups.value.some((group) => group.hostId === hostId),
  )
  syncRecDirKeysFromSelections()
}

function newRecoveryDirEntryForSource(sourceHostId: string): RecoveryDirSelectionEntry | null {
  const backup = recBackupsForSource(sourceHostId)[0]
  if (!backup) return null
  const snap = selectedSnapshotForSource(sourceHostId)
  const targetPath = recoveryDestinationPathForEntry(recoveryDestConfiguredEntryForHost(sourceHostId) ?? undefined)
  if (snap?.dirs.length) {
    const dir = snap.dirs.find((item) => item.hostId === sourceHostId) || snap.dirs[0]
    return {
      id: `rds-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      hostId: dir.hostId,
      path: '',
      hostName: dir.hostName || store.getNodeName(dir.hostId),
      scope: 'snapshot',
      sourceKind: 'snapshot',
      targetPath,
      sourcePathValidation: 'valid',
      targetPathValidation: targetPath ? 'valid' : undefined,
    }
  }
  const source = backup.sources.find((item) => item.hostId === sourceHostId) || backup.sources[0]
  if (!source?.path) return null
  return {
    id: `rds-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    hostId: source.hostId,
    path: '',
    hostName: store.getNodeName(source.hostId),
    scope: 'snapshot',
    sourceKind: 'snapshot',
    targetPath,
    sourcePathValidation: 'valid',
    targetPathValidation: targetPath ? 'valid' : undefined,
  }
}

function addRecoveryDirEntry(hostId: string) {
  const entry = newRecoveryDirEntryForSource(hostId)
  if (!entry) {
    ElMessage.warning({ message: t('protection.backupsPage.msgSnapshotNoDirsAvailable'), grouping: true })
    return false
  }
  recDirSelectionsByHost.value = {
    ...recDirSelectionsByHost.value,
    [hostId]: [...recoveryDirEntriesForHost(hostId), entry],
  }
  syncRecDirKeysFromSelections()
  return true
}

function addRecoveryDirEntryAndExpand(row: RecRecoveryDirSourceRow) {
  if (!addRecoveryDirEntry(row.hostId)) return
  recExpandedRecDirHostIds.value = [...new Set([...recExpandedRecDirHostIds.value, row.hostId])]
  nextTick(() => {
    recRecoveryDirSourceTableRef.value?.toggleRowExpansion(row, true)
  })
}

function recoverySnapshotIdsForDirStep() {
  return recBackupIds.value
    .map((backupId) => Number(recSnapshotMap.value[backupId]))
    .filter((id) => Number.isFinite(id) && id > 0)
}

function preloadRecoverySnapshotDirectoriesForDirStep() {
  const snapshotIds = recoverySnapshotIdsForDirStep()
  if (!snapshotIds.length) return Promise.resolve()
  return ensureRecoverySnapshotDetails(snapshotIds)
}

function ensureInitialRecoveryDirStepRows() {
  if (recDirStepInitialized.value) return
  const next: Record<string, RecoveryDirSelectionEntry[]> = { ...recDirSelectionsByHost.value }
  for (const row of recRecoveryDirSourceRows.value) {
    if (next[row.hostId]?.length) continue
    const entry = newRecoveryDirEntryForSource(row.hostId)
    if (entry) next[row.hostId] = [entry]
  }
  recDirSelectionsByHost.value = next
  recExpandedRecDirHostIds.value = recRecoveryDirSourceRows.value.map((row) => row.hostId)
  recDirStepInitialized.value = true
  syncRecDirKeysFromSelections()
  void preloadRecoverySnapshotDirectoriesForDirStep().catch((e) => {
    showApiError(e)
  })
}

function removeRecoveryDirEntry(hostId: string, entryId: string) {
  recDirSelectionsByHost.value = {
    ...recDirSelectionsByHost.value,
    [hostId]: recoveryDirEntriesForHost(hostId).filter((entry) => entry.id !== entryId),
  }
  syncRecDirKeysFromSelections()
  if (recDirDrawerOpen.value && recDirDrawerHostId.value === hostId && recDirDrawerEntryId.value === entryId) {
    closeRecDirDrawer()
  }
}


function recoveryMappingSourceKind(entry: RecoveryDirSelectionEntry): NonNullable<RecoveryDirSelectionEntry['sourceKind']> {
  if (entry.scope === 'snapshot') return 'snapshot'
  return entry.sourceKind === 'file' ? 'file' : 'dir'
}

function recoveryMappingSourceLabel(entry: RecoveryDirSelectionEntry) {
  if (entry.scope === 'snapshot') return t('protection.backupsPage.recoveryWholeSnapshot')
  const hostLabel = entry.hostName || store.getNodeName(entry.hostId)
  return entry.path ? `${hostLabel} · ${entry.path}` : '—'
}

function recoveryMappingTargetLabel(entry: RecoveryDirSelectionEntry) {
  return entry.targetPath || '—'
}

function recoveryMappingTotalLabel(count: number) {
  return count === 1
    ? t('protection.backupsPage.recoveryMappingTotalOne')
    : t('protection.backupsPage.recoveryMappingTotalMany', { n: count })
}



function recConflictPolicyForSource(hostId: string): RecoveryConflictPolicy {
  const backupId = recBackupsForSource(hostId)[0]?.id || ''
  return recConflictPolicyMap.value[backupId] || ''
}

function setRecConflictPolicyForSource(hostId: string, policy: RecoveryConflictPolicy) {
  const next = { ...recConflictPolicyMap.value }
  for (const backup of recBackupsForSource(hostId)) {
    next[backup.id] = policy
  }
  recConflictPolicyMap.value = next
}

watch(recOpen, (open) => {
  if (typeof document === 'undefined') return
  if (addSourceOpen.value) return
  document.body.style.overflow = open ? 'hidden' : ''
})

watch([recOpen, recBackupId], () => {
  const b = currentRecBackup()
  if (!b) return
  const first = latestRecoverableBackupSnapshot(b)
  if (first && !recSnapshotMap.value[b.id]) {
    recSnapshotMap.value = { ...recSnapshotMap.value, [b.id]: first.id }
  }
  if (first && !recSnapshotId.value) recSnapshotId.value = recSnapshotMap.value[b.id] || first.id
})

watch(recBackupIds, () => {
  syncRecoveryDestinationConfigs()
  syncRecoveryDirSelections()
  ensureRecoverableRecoverySnapshots()
  const next = { ...recConflictPolicyMap.value }
  recConflictPolicyMap.value = Object.fromEntries(
    Object.entries(next).filter(([backupId]) => recBackupIds.value.includes(backupId)),
  ) as Record<string, RecoveryConflictPolicy>
})

watch([recOpen, recEntryMode, recStep], ([open, mode, step]) => {
  if (!open || mode !== 'manual' || step !== 1) return
  if (!recoveryTargetHostRows.value.length) void loadRecoveryTargetHostOptions({ reset: true })
})


function dirKey(backupId: string, d: DemoSnapshotDir) {
  return `${backupId}::${d.hostId}::${d.path}`
}

function joinPath(parent: string, name: string) {
  const p = parent === '/' ? '' : parent.replace(/\/+$/, '')
  return `${p}/${name}`.replace(/\/+/g, '/')
}

function basenamePath(path: string) {
  const parts = path.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] || path
}

function isWindowsPath(path: string) {
  return /^[a-zA-Z]:($|[\\/])/.test(path)
}

function isAbsoluteSourcePath(path: string) {
  return path.startsWith('/')
    || /^[A-Za-z]:[\\/]/.test(path)
    || path.startsWith('\\\\')
}

function splitPathWithRoot(path: string): { root: string; segments: string[]; sep: '/' | '\\' } {
  if (isWindowsPath(path)) {
    const drive = `${path.slice(0, 2)}\\`
    const rest = path.slice(2).replace(/^[\\/]+/, '')
    const segments = rest ? rest.split(/[\\/]+/).filter(Boolean) : []
    return { root: drive, segments, sep: '\\' }
  }
  const cleaned = path.startsWith('/') ? path.slice(1) : path
  const segments = cleaned ? cleaned.split('/').filter(Boolean) : []
  return { root: '/', segments, sep: '/' }
}

function joinPathBySep(parent: string, name: string, sep: '/' | '\\') {
  if (sep === '\\') {
    const p = parent.endsWith('\\') ? parent.slice(0, -1) : parent
    return `${p}\\${name}`.replace(/\\+/g, '\\')
  }
  return joinPath(parent, name)
}

function normalizeComparablePath(path: string) {
  return path.replace(/\\/g, '/').replace(/\/+$/, '')
}

function fmtLocalTime(raw: string) {
  return formatLocalDateTime(raw, '—')
}

function fmtBytes(n: number) {
  if (!n || n <= 0) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let i = 0
  let v = n
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v.toFixed(i >= 2 ? 1 : 0)} ${u[i]}`
}

const treeSortLocale = () => String(locale.value || 'en')

function buildSubDirNodes(
  backup: DemoBackup,
  snapshot: DemoSnapshot,
  hostId: string,
  hostName: string,
  parentPath: string,
  nodes: DemoSnapshot['treeByPath'][string] | undefined,
): RecoveryDirNode[] {
  if (!nodes?.length) return []
  return nodes
    .filter((n) => n.type === 'dir')
    .map((n) => {
      const path = joinPath(parentPath, n.name)
      return {
        id: `${backup.id}::${hostId}::${path}`,
        label: n.name,
        backupId: backup.id,
        backupName: backup.name,
        snapshotId: snapshot.id,
        snapshotTime: snapshot.endTime,
        hostId,
        hostName,
        path,
        pathType: 'directory',
        children: buildSubDirNodes(backup, snapshot, hostId, hostName, path, n.children),
      }
    })
}

function isSameOrAncestorPath(ancestorPath: string, childPath: string) {
  const ancestor = normalizeComparablePath(ancestorPath)
  const child = normalizeComparablePath(childPath)
  if (ancestor === child) return true
  if (/^[A-Za-z]:/.test(ancestor) || /^[A-Za-z]:/.test(child)) {
    const a = ancestor.toLowerCase()
    const c = child.toLowerCase()
    const prefix = a.endsWith('/') ? a : `${a}/`
    return c.startsWith(prefix)
  }
  const prefix = ancestor.endsWith('/') ? ancestor : `${ancestor}/`
  return child.startsWith(prefix)
}

function relativeSnapshotPath(sourceRootPath: string, selectedPath: string) {
  const root = normalizeComparablePath(sourceRootPath)
  const selected = normalizeComparablePath(selectedPath)
  if (root === selected) return ''
  const prefix = root.endsWith('/') ? root : `${root}/`
  if (/^[A-Za-z]:/.test(root) || /^[A-Za-z]:/.test(selected)) {
    return selected.toLowerCase().startsWith(prefix.toLowerCase())
      ? selected.slice(prefix.length).replace(/^\/+/, '')
      : selected.replace(/^\/+/, '')
  }
  return selected.startsWith(prefix) ? selected.slice(prefix.length).replace(/^\/+/, '') : selected.replace(/^\/+/, '')
}

const recDirTreeData = computed<RecoveryDirNode[]>(() => {
  const roots: RecoveryDirNode[] = []
  for (const backupId of recBackupIds.value.length ? recBackupIds.value : recBackupId.value ? [recBackupId.value] : []) {
    const backup = store.getBackup(backupId)
    if (!backup) continue
    const snapshotId = recSnapshotMap.value[backupId]
    const snap = backup.snapshots.find((s) => s.id === snapshotId) ?? latestBackupSnapshot(backup)
    if (!snap) continue
    roots.push(
      ...snap.dirs.map((d) => ({
        id: dirKey(backup.id, d),
        label: `${d.hostName} · ${basenamePath(d.path)}`,
        backupId: backup.id,
        backupName: backup.name,
        snapshotId: snap.id,
        snapshotTime: snap.endTime,
        hostId: d.hostId,
        hostName: d.hostName,
        path: d.path,
        pathType: normalizeDemoPathType(d.pathType) === 'file' ? 'file' : 'directory',
        children: buildSubDirNodes(backup, snap, d.hostId, d.hostName, d.path, snap.treeByPath[d.path]),
      })),
    )
  }
  return roots
})

const recDirNodeMap = computed(() => {
  const map = new Map<string, RecoveryDirNode>()
  const walk = (nodes: RecoveryDirNode[]) => {
    for (const n of nodes) {
      map.set(n.id, n)
      if (n.children?.length) walk(n.children)
    }
  }
  walk(recDirTreeData.value)
  return map
})

const recSelectedDirNodes = computed(() => recDirKeys.value.map((k) => recDirNodeMap.value.get(k)).filter(Boolean) as RecoveryDirNode[])

function recDirTreeDataForBackup(backupId: string) {
  return recDirTreeData.value.filter((node) => node.backupId === backupId)
}





function recDirTreeDataForSource(hostId: string) {
  const backup = recBackupsForSource(hostId)[0]
  if (!backup) return []
  return recDirTreeDataForBackup(backup.id)
}

function recoveryDirNodeSourceKind(node: RecoveryDirNode) {
  return normalizeDemoPathType(node.pathType) === 'file' ? 'file' : 'dir'
}

function recoveryDirNodeIcon(node: RecoveryDirNode) {
  return recoveryDirNodeSourceKind(node) === 'file' ? File : Folder
}




function recoveryPlanRunStatusTone(_plan: RecoveryPlanSummary) {
  return 'enabled'
}

function recoveryPlanRunStatusLabel(plan: RecoveryPlanSummary) {
  if (plan.sourceDirs.some((dir) => dir.scope === 'snapshot')) {
    return t('protection.backupsPage.recoveryWholeSnapshot')
  }
  return t('protection.backupsPage.recoveryPlanEnabledPathCount', { n: plan.sourceDirs.length })
}

function recoveryPlanRunSourceKind(dir: Pick<RecoveryPlanSourceDir, 'pathType' | 'scope'>) {
  if (dir.scope === 'snapshot') return 'snapshot'
  return normalizeDemoPathType(dir.pathType) === 'file' ? 'file' : 'dir'
}

function recoveryPlanRunSourceIcon(dir: Pick<RecoveryPlanSourceDir, 'pathType' | 'scope'>) {
  const kind = recoveryPlanRunSourceKind(dir)
  if (kind === 'snapshot') return Camera
  return kind === 'file' ? File : Folder
}

function recoveryPlanRunConflictSummary(plan: RecoveryPlanSummary) {
  return plan.conflictPolicy === 'overwrite'
    ? t('protection.backupsPage.createRecoveryConflictOverwriteFull')
    : t('protection.backupsPage.createRecoveryConflictSkipFull')
}

function recoveryConflictPolicySummary(policy: SelectedRecoveryConflictPolicy) {
  return policy === 'overwrite'
    ? t('protection.backupsPage.createRecoveryConflictOverwriteFull')
    : t('protection.backupsPage.createRecoveryConflictSkipFull')
}

function recoveryPlanRunSourcePathLabel(dir: Pick<RecoveryPlanSourceDir, 'path' | 'scope'>) {
  if (dir.scope === 'snapshot') return t('protection.backupsPage.recoveryWholeSnapshot')
  return dir.path || '—'
}

function recoveryPlanMappingTargetSummary(mapping: RecoveryPlanMapping) {
  const targetName = mapping.destResourceName || mapping.destHostName || mapping.destHostId || '—'
  const targetLabel = mapping.destHostIp ? `${targetName} ${mapping.destHostIp}` : targetName
  return `${mapping.customSubdir || '—'} (${targetLabel})`
}

function recoveryEndpointDisplay(endpointId: string, fallbackName = '') {
  const host = store.getHost(endpointId)
  if (host) {
    return {
      name: host.name || fallbackName || endpointId,
      ip: host.nodeIp || host.hostname || t('protection.backupDetail.durationDash'),
    }
  }
  const nas = store.getNas(endpointId)
  if (nas) {
    return {
      name: nas.name || fallbackName || endpointId,
      ip: nas.hostname || nas.proxyNodeIp || t('protection.backupDetail.durationDash'),
    }
  }
  const row = flowRowFromSourceId(endpointId)
  return {
    name: row?.name || row?.nodeName || fallbackName || store.getNodeName(endpointId),
    ip: row?.nodeIp || row?.hostname || t('protection.backupDetail.durationDash'),
  }
}

function recoveryResourceDisplay(endpointId: string, fallbackName = '') {
  const endpoint = recoveryEndpointDisplay(endpointId, fallbackName)
  const row = flowRowFromSourceId(endpointId)
  return {
    name: row?.name || fallbackName || endpoint.name,
    ip: row?.nodeIp || row?.hostname || endpoint.ip,
  }
}


const recDestinationHostOptions = computed(() =>
  [...store.hosts.value, ...store.nas.value].map((h) => ({
    hostId: h.id,
    hostName: h.name,
    hostname: h.hostname,
  })),
)

function ensureHostFolderNames() {
  const next = { ...recHostDirNameMap.value }
  const hostIds = new Set(recSelectedDirNodes.value.map((d) => d.hostId))
  hostIds.forEach((hostId) => {
    if (!next[hostId]) {
      next[hostId] = store.getNodeName(hostId)
    }
  })
  recHostDirNameMap.value = next
}

watch(recSelectedDirNodes, () => {
  ensureHostFolderNames()
})

watch(recDirKeys, (keys) => {
  recSelectTreeRef.value?.setCheckedKeys(keys)
})

watch(recStep, (step) => {
  if (step === 2) {
    ensureInitialRecoveryDirStepRows()
    void preloadRecoverySnapshotDirectoriesForDirStep().catch((e) => {
      showApiError(e)
    })
  }
  if (step === 3) {
    expandAllRecRecoveryConfirmRows()
  }
})

watch(recDestinationHostOptions, (list) => {
  if (!list.length) {
    recDestHostId.value = ''
    return
  }
  if (!list.some((x) => x.hostId === recDestHostId.value)) {
    recDestHostId.value = list[0].hostId
  }
})

function cloneRecoveryDirNode(node: RecoveryDirNode): RecoveryDirNode {
  return {
    ...node,
    children: node.children?.map(cloneRecoveryDirNode) ?? [],
  }
}

const recDestTreeData = ref<RecoveryDirNode[]>([])

function rebuildRecDestTree() {
  const hostId = recDestHostId.value
  if (!hostId) {
    recDestTreeData.value = []
    recDestDirKey.value = ''
    recDestCurrentNodeKey.value = ''
    recDestExpandedKeys.value = []
    return
  }
  const roots: RecoveryDirNode[] = []
  const map = new Map<string, RecoveryDirNode>()
  const ensureNode = (nodeHostId: string, path: string, label: string, parent: RecoveryDirNode | null) => {
    const id = `${nodeHostId}::${path}`
    let node = map.get(id)
    if (!node) {
      node = { id, label, hostId: nodeHostId, path, children: [] }
      map.set(id, node)
      if (parent) {
        parent.children = parent.children || []
        if (!parent.children.some((x) => x.id === id)) parent.children.push(node)
      } else if (!roots.some((x) => x.id === id)) {
        roots.push(node)
      }
    }
    return node
  }
  const appendSubDirs = (
    parent: RecoveryDirNode,
    nodeHostId: string,
    parentPath: string,
    sep: '/' | '\\',
    nodes: DemoSnapshot['treeByPath'][string] | undefined,
  ) => {
    if (!nodes?.length) return
    for (const n of nodes) {
      if (n.type !== 'dir') continue
      const childPath = joinPathBySep(parentPath, n.name, sep)
      const childNode = ensureNode(nodeHostId, childPath, n.name, parent)
      appendSubDirs(childNode, nodeHostId, childPath, sep, n.children)
    }
  }
  const snapshotsForHost = (recBackupIds.value.length ? recBackupIds.value : recBackupId.value ? [recBackupId.value] : [])
    .filter((backupId) => backupId === activeRecoveryDestinationBackupId())
    .map((backupId) => {
      const backup = store.getBackup(backupId)
      if (!backup) return null
      const snapshotId = recSnapshotMap.value[backupId]
      return backup.snapshots.find((s) => s.id === snapshotId) ?? latestBackupSnapshot(backup) ?? null
    })
    .filter(Boolean) as DemoSnapshot[]
  const hostDirs = snapshotsForHost.flatMap((snap) => snap.dirs.filter((d) => d.hostId === hostId).map((dir) => ({ dir, snap })))
  for (const { dir: d, snap } of hostDirs) {
    const parsed = splitPathWithRoot(d.path)
    let parent: RecoveryDirNode | null = null
    let curPath = parsed.root
    parent = ensureNode(hostId, curPath, parsed.root, null)
    for (const seg of parsed.segments) {
      curPath = joinPathBySep(curPath, seg, parsed.sep)
      parent = ensureNode(hostId, curPath, seg, parent)
    }
    if (parent) {
      appendSubDirs(parent, hostId, curPath, parsed.sep, snap.treeByPath[d.path])
    }
  }
  recDestTreeData.value = roots.map(cloneRecoveryDirNode)
  const finalMap = new Map<string, RecoveryDirNode>()
  const walk = (nodes: RecoveryDirNode[]) => {
    for (const n of nodes) {
      finalMap.set(n.id, n)
      if (n.children?.length) {
        n.children.sort((a, b) => a.label.localeCompare(b.label, treeSortLocale()))
        walk(n.children)
      }
    }
  }
  walk(recDestTreeData.value)
  recDestTreeData.value.sort((a, b) => a.label.localeCompare(b.label, treeSortLocale()))
  recDestExpandedKeys.value = recDestTreeData.value.map((x) => x.id)
  if (recDestDirKey.value && !finalMap.has(recDestDirKey.value) && !recDestDirKey.value.startsWith('custom::')) {
    recDestDirKey.value = recDestTreeData.value[0]?.id ?? recDestDirKey.value
  }
  if (!finalMap.has(recDestCurrentNodeKey.value)) {
    recDestCurrentNodeKey.value = ''
  }
}

watch(
  () => [
    activeRecoveryDestinationBackupId(),
    recDestHostId.value,
    recSnapshotMap.value[activeRecoveryDestinationBackupId()],
    recBackupIds.value.join('|'),
  ],
  () => {
    rebuildRecDestTree()
  },
  { deep: true },
)






function recoveryTreePathLabel(node: RecoveryDirNode) {
  return node.hostName ? `${node.hostName} · ${node.path}` : node.path
}



function onRecDestDrawerOpened() {
  nextTick(() => {
    requestAnimationFrame(() => updateNestedDrawerWidth())
  })
}






function onRecDirDrawerOpened() {
  onRecDestDrawerOpened()
  nextTick(() => {
    recDirDrawerTreeRef.value?.setCheckedKeys(recDirDrawerDraftKeys.value, false)
  })
}

function syncRecDirDrawerTreeCheckedKeys() {
  nextTick(() => {
    recDirDrawerTreeRef.value?.setCheckedKeys(recDirDrawerDraftKeys.value, false)
  })
}


function closeRecDirDrawer() {
  recDirDrawerOpen.value = false
}

function onRecDirDrawerClosed() {
  recDirDrawerHostId.value = ''
  recDirDrawerEntryId.value = ''
  recDirDrawerDraftKeys.value = []
}

function onRecDirDrawerTreeCheckChange(hostId: string, data: RecoveryDirNode, checked: boolean) {
  const repBackup = recBackupsForSource(hostId)[0]
  if (!repBackup) return
  if (data.backupId && data.backupId !== repBackup.id) return
  if (!checked) {
    recDirDrawerDraftKeys.value = recDirDrawerDraftKeys.value.filter((id) => id !== data.id)
  } else {
    recDirDrawerDraftKeys.value = [data.id]
  }
  syncRecDirDrawerTreeCheckedKeys()
}

function saveRecDirDrawer() {
  const hostId = recDirDrawerHostId.value
  const entryId = recDirDrawerEntryId.value
  const node = recDirNodeMap.value.get(recDirDrawerDraftKeys.value[0] || '')
  if (!hostId || !entryId || !node?.path) {
    ElMessage.warning({ message: t('protection.backupsPage.msgPickAtLeastOneDir'), grouping: true })
    return
  }
  updateRecoveryDirEntry(hostId, entryId, {
    hostId: node.hostId,
    path: node.path,
    hostName: node.hostName || store.getNodeName(node.hostId),
  })
  closeRecDirDrawer()
}

function cancelRecDirDrawer() {
  closeRecDirDrawer()
}


function startManualRecoveryWizard() {
  recEntryMode.value = 'manual'
  recStep.value = 0
  ensureRecoverableRecoverySnapshots()
  void ensureManualRecoverySnapshotDefaults().catch((e) => showApiError(e))
}

function onRecoveryEntryModeChange(mode: string | number | boolean | undefined) {
  recStep.value = 0
  if (mode === 'manual') {
    ensureRecoverableRecoverySnapshots()
    ensureInitialRecoveryDirStepRows()
    void ensureManualRecoverySnapshotDefaults().catch((e) => showApiError(e))
    void loadRecoveryTargetHostOptions({ reset: true })
  }
}


function applyRecoveryPlans(plans: RecoveryPlanSummary[]) {
  if (!plans.length) return
  void restoreTargetCatalog.ensureByIds(plans.map((plan) => plan.destHostId).filter(Boolean))
  recBackupIds.value = uniqueBackupIds(plans.map((plan) => plan.backupId))
  recBackupId.value = plans[0].backupId
  recSnapshotMap.value = Object.fromEntries(plans.map((plan) => [plan.backupId, plan.snapshotId]))
  recSnapshotId.value = plans[0].snapshotId
  recDestActiveBackupId.value = plans[0].backupId
  recDestEntriesByHost.value = {}
  for (const plan of plans) {
    const backup = store.getBackup(plan.backupId)
    const sourceHostId = backup?.sources[0]?.hostId || ''
    if (!sourceHostId) continue
    const path = plan.customSubdir || ''
    const dirKey = destKeyForPath(plan.destHostId, path)
    const entry: RecoveryDestinationEntry = {
      id: `rde-plan-${plan.backupId}`,
      hostId: plan.destHostId,
      dirKey,
      currentNodeKey: dirKey,
      expandedKeys: dirKey ? [dirKey] : [],
      customSubdir: plan.customSubdir || '',
    }
    recDestEntriesByHost.value = {
      ...recDestEntriesByHost.value,
      [sourceHostId]: [entry],
    }
  }
  recHostDirNameMap.value = Object.fromEntries(
    plans.flatMap((plan) => plan.sourceDirs.map((dir) => [dir.hostId, dir.hostName] as const)),
  )
  recConflictPolicyMap.value = Object.fromEntries(plans.map((plan) => [plan.backupId, plan.conflictPolicy as RecoveryConflictPolicy]))
  recDirSelectionsByHost.value = {}
  for (const plan of plans) {
    const backup = store.getBackup(plan.backupId)
    const sourceHostId = backup?.sources[0]?.hostId || ''
    if (!sourceHostId) continue
    const existing = recDirSelectionsByHost.value[sourceHostId] || []
    const seen = new Set(existing.map((entry) => `${entry.scope || 'directory'}::${entry.hostId}::${entry.path}`))
    for (const dir of plan.sourceDirs) {
      const scope = dir.scope === 'snapshot' ? 'snapshot' : 'directory'
      const token = `${scope}::${dir.hostId}::${dir.path}`
      if (seen.has(token)) continue
      seen.add(token)
      existing.push({
        id: `rds-plan-${plan.backupId}-${scope}-${dir.path || 'snapshot'}`,
        hostId: dir.hostId,
        path: scope === 'snapshot' ? '' : dir.path,
        hostName: dir.hostName,
        scope,
        sourceKind: scope === 'snapshot' ? 'snapshot' : recoveryPlanRunSourceKind(dir),
        targetPath: recoveryDestinationPathForEntry(recoveryDestConfiguredEntryForHost(sourceHostId) ?? undefined),
        sourcePathValidation: 'valid',
        targetPathValidation: 'valid',
      })
    }
    recDirSelectionsByHost.value = { ...recDirSelectionsByHost.value, [sourceHostId]: existing }
  }
  syncRecDirKeysFromSelections()
}

function recoveryMissingPlanHostSummary(rows: RecoveryPlanTableRow[]) {
  const names = [...new Set(rows.map((row) => row.sourceResourceName || row.sourceHostName || row.backupName))]
  const visible = names.slice(0, 5).join(', ')
  if (names.length <= 5) return visible
  return `${visible}, +${names.length - 5}`
}

async function confirmRecoveryEntry() {
  if (recEntryMode.value === 'manual') {
    startManualRecoveryWizard()
    return
  }
  const plans = selectedRecoveryPlans.value
  if (!plans.length) {
    ElMessage.warning({ message: t('protection.backupsPage.msgRecoveryPlanUnavailable'), grouping: true })
    return
  }
  const missingRows = missingRecoveryPlanRows.value
  if (missingRows.length) {
    pendingRecoveryPlanConfirmPlans.value = [...plans]
    pendingRecoveryPlanConfirmMissingRows.value = [...missingRows]
    partialRecoveryConfirmOpen.value = true
    return
  }
  applyRecoveryPlans(plans)
  runRecovery('plan')
}

function confirmPartialRecoveryPlans() {
  const plans = pendingRecoveryPlanConfirmPlans.value
  if (!plans.length) return
  partialRecoveryConfirmOpen.value = false
  pendingRecoveryPlanConfirmPlans.value = []
  pendingRecoveryPlanConfirmMissingRows.value = []
  applyRecoveryPlans(plans)
  runRecovery('plan')
}




async function nextRec() {
  if (recStep.value === 0) {
    try {
      await ensureManualRecoverySnapshotDefaults()
    } catch (e) {
      showApiError(e)
      return
    }
    if (!recBackupIds.value.length) {
      ElMessage.warning({ message: t('protection.backupsPage.msgPickBackupForRecovery'), grouping: true })
      return
    }
    if (recBackupIds.value.some((backupId) => !recSnapshotMap.value[backupId])) {
      ElMessage.warning({ message: t('protection.backupsPage.msgPickSnapshotForRecovery'), grouping: true })
      return
    }
  }
  if (recStep.value === 1) {
    if (!recRecoveryDestStepReady.value) {
      ElMessage.warning({ message: t('protection.backupsPage.msgConfigureRecoveryDest'), grouping: true })
      return
    }
  }
  if (recStep.value === 2) {
    if (!recDirTreeData.value.length) {
      ElMessage.warning({ message: t('protection.backupsPage.msgSnapshotNoDirsAvailable'), grouping: true })
      return
    }
    if (!recRecoveryDirStepReady.value) {
      if (recBackupSourceGroups.value.some((group) => !recoveryDirEntriesForHost(group.hostId).some(isRecoveryDirEntryConfigured))) {
        ElMessage.warning({ message: t('protection.backupsPage.msgConfigureRecoveryDirs'), grouping: true })
      } else {
        ElMessage.warning({ message: t('protection.backupsPage.msgSelectRecoveryConflictPolicy'), grouping: true })
      }
      return
    }
  }
  if (recStep.value < 3) {
    recStep.value += 1
    if (recStep.value === 3) {
      expandAllRecRecoveryConfirmRows()
    }
    if (recStep.value === 2) {
      ensureInitialRecoveryDirStepRows()
      try {
        await preloadRecoverySnapshotDirectoriesForDirStep()
      } catch (e) {
        showApiError(e)
        recStep.value -= 1
      }
    }
  }
}

function prevRec() {
  if (recStep.value > 0) recStep.value -= 1
}

function recoveryTaskDraftFromEntry(
  backupId: string,
  backupName: string,
  snapshotId: string,
  snapshotTime: string,
  entry: RecoveryDestinationEntry,
  dirs: RecoveryDirSelectionEntry[],
): RecoveryTaskDraft {
  return {
    backupId,
    backupName,
    snapshotId,
    snapshotTime,
    conflictPolicy: (recConflictPolicyMap.value[backupId] || 'skip') as SelectedRecoveryConflictPolicy,
    destHostId: entry.hostId,
    destHostName: recoveryTargetNodeLabel(entry.hostId, backupSourceHostId(backupId)),
    destPath: dirs.find((dir) => dir.targetPath)?.targetPath || recoveryDestinationPathForEntry(entry),
    dirs,
  }
}

function recoveryTaskDrafts(mode: 'plan' | 'manual'): RecoveryTaskDraft[] {
  if (mode === 'plan') {
    return selectedRecoveryPlans.value.flatMap((plan) => {
      const sourceHostId = backupSourceHostId(plan.backupId)
      const destEntry = recoveryDestConfiguredEntryForHost(sourceHostId)
      if (!destEntry) return []
      const dirs = plan.sourceDirs.map((dir) => ({
        id: `${plan.backupId}::${dir.scope || 'directory'}::${dir.hostId}::${dir.path || 'snapshot'}`,
        hostId: dir.hostId,
        hostName: dir.hostName,
        path: dir.scope === 'snapshot' ? '' : dir.path,
        scope: dir.scope === 'snapshot' ? 'snapshot' as const : 'directory' as const,
        sourceKind: recoveryPlanRunSourceKind(dir),
        targetPath: recoveryDestinationPathForEntry(destEntry),
      }))
      return [
        recoveryTaskDraftFromEntry(
          plan.backupId,
          plan.backupName,
          plan.snapshotId,
          plan.snapshotTime,
          destEntry,
          dirs,
        ),
      ]
    })
  }

  const drafts: RecoveryTaskDraft[] = []
  for (const backupId of recBackupIds.value) {
    const backup = store.getBackup(backupId)
    if (!backup) continue
    const sourceHostId = backupSourceHostId(backupId)
    const destEntry = recoveryDestConfiguredEntryForHost(sourceHostId)
    const dirs = recoveryDirEntriesForHost(sourceHostId).filter(isRecoveryDirEntryConfigured)
    if (!dirs.length || !destEntry) continue
    const snapshotId = recSnapshotMap.value[backupId] || ''
    const snapshot = backup.snapshots.find((s) => s.id === snapshotId)
    drafts.push(
      recoveryTaskDraftFromEntry(
        backupId,
        backup.name,
        snapshotId,
        snapshot?.endTime || '',
        destEntry,
        dirs,
      ),
    )
  }
  return drafts
}

const recManualRecoveryDrafts = computed(() => recoveryTaskDrafts('manual'))

type RecRecoveryConfirmSourceRow = {
  hostId: string
  hostName: string
  sourceSummary: RecoverySourceSummary
  destSummary: RecoverySourceSummary | null
  sourceDisplayName: string
  sourceIp: string
  snapshotLine: string
  destDisplayName: string
  destIp: string
  destConfiguredEntry: RecoveryDestinationEntry | null
  dirConfiguredEntries: RecoveryDirSelectionEntry[]
  conflictPolicy: SelectedRecoveryConflictPolicy
  taskDrafts: RecoveryTaskDraft[]
}

const recExpandedRecConfirmHostIds = ref<string[]>([])
const recRecoveryConfirmTableRef = ref<InstanceType<typeof ElTable> | null>(null)

function recoveryConfirmDraftsForSource(hostId: string) {
  return recManualRecoveryDrafts.value.filter((draft) => backupSourceHostId(draft.backupId) === hostId)
}

function recoveryConfirmSourceDisplay(hostId: string, fallbackName = '') {
  const row = flowRowFromSourceId(hostId)
  return {
    name: row?.name || fallbackName || store.getNodeName(hostId),
    ip: row?.nodeIp || row?.hostname || t('protection.backupDetail.durationDash'),
  }
}

function recoveryConfirmDestinationDisplay(entry: RecoveryDestinationEntry | null) {
  if (!entry) {
    return {
      name: '',
      ip: '',
    }
  }
  const source = recoveryOriginalSourceById(entry.hostId)
  if (source) {
    return {
      name: source.name,
      ip: source.nodeIp || source.hostname || t('protection.backupDetail.durationDash'),
    }
  }
  const parsed = parseEndpointUiId(entry.hostId)
  const node = parsed ? recoveryNodeRows.value.find((item) => item.id === parsed.refId) : undefined
  if (node) {
    return {
      name: node.name,
      ip: nodeIpLabel(node),
    }
  }
  const row = flowRowFromSourceId(entry.hostId)
  return {
    name: row?.name || store.getNodeName(entry.hostId),
    ip: row?.nodeIp || row?.hostname || t('protection.backupDetail.durationDash'),
  }
}

const recRecoveryConfirmSourceRows = computed<RecRecoveryConfirmSourceRow[]>(() =>
  recBackupSourceGroups.value.map((group) => {
    const dirConfiguredEntries = recoveryDirEntriesForHost(group.hostId).filter(isRecoveryDirEntryConfigured)
    const destConfiguredEntry = recoveryDestConfiguredEntryForHost(group.hostId)
    const sourceDisplay = recoveryConfirmSourceDisplay(group.hostId, group.hostName)
    const destDisplay = recoveryConfirmDestinationDisplay(destConfiguredEntry)
    return {
      hostId: group.hostId,
      hostName: group.hostName,
      sourceSummary: recoverySourceSummary(group.hostId, group.hostName),
      destSummary: recoveryTargetSummaryForEntry(destConfiguredEntry),
      sourceDisplayName: sourceDisplay.name,
      sourceIp: sourceDisplay.ip,
      snapshotLine: recGroupSnapshotDisplayLine(group.hostId),
      destDisplayName: destDisplay.name,
      destIp: destDisplay.ip,
      destConfiguredEntry,
      dirConfiguredEntries,
      conflictPolicy: (recConflictPolicyForSource(group.hostId) || 'skip') as SelectedRecoveryConflictPolicy,
      taskDrafts: recoveryConfirmDraftsForSource(group.hostId),
    }
  }),
)

function expandAllRecRecoveryConfirmRows() {
  recExpandedRecConfirmHostIds.value = recRecoveryConfirmSourceRows.value.map((row) => row.hostId)
  void nextTick(() => recRecoveryConfirmTableRef.value?.doLayout?.())
}

function onRecRecoveryConfirmExpandChange(_row: RecRecoveryConfirmSourceRow, expandedRows: RecRecoveryConfirmSourceRow[]) {
  const visibleIds = new Set(recRecoveryConfirmSourceRows.value.map((row) => row.hostId))
  const hiddenExpandedIds = recExpandedRecConfirmHostIds.value.filter((id) => !visibleIds.has(id))
  recExpandedRecConfirmHostIds.value = [...hiddenExpandedIds, ...expandedRows.map((row) => row.hostId)]
}

function toggleRecRecoveryConfirmRow(row: RecRecoveryConfirmSourceRow) {
  const nextExpanded = !recExpandedRecConfirmHostIds.value.includes(row.hostId)
  recRecoveryConfirmTableRef.value?.toggleRowExpansion(row, nextExpanded)
}

function restoreSnapshotDirectoryForPath(snapshotId: number, path: string): {
  directory: BackupSourceSnapshotDirectory
  selectedPaths: string[]
} | null {
  const directories = [...availableDirectoriesForSnapshot(snapshotId)]
    .sort((a, b) => b.source_path.length - a.source_path.length)
  for (const directory of directories) {
    if (!isSameOrAncestorPath(directory.source_path, path)) continue
    return {
      directory,
      selectedPaths: normalizeComparablePath(directory.source_path) === normalizeComparablePath(path)
        ? []
        : [relativeSnapshotPath(directory.source_path, path)],
    }
  }
  return null
}

function buildManualRestorePayload(draft: RecoveryTaskDraft): RestoreRecordCreatePayload | null {
  const configId = realConfigIdFromBackupId(draft.backupId)
  const config = configId ? backupConfigDetailById.value.get(configId) : null
  const snapshotId = Number(draft.snapshotId)
  const sourceEndpoint = config
    ? { type: config.source_type, refId: Number(config.source_ref_id) }
    : parseEndpointUiId(backupSourceHostId(draft.backupId))
  const targetEndpoint = parseEndpointUiId(draft.destHostId)
  if (!sourceEndpoint || !targetEndpoint || !Number.isFinite(snapshotId) || snapshotId <= 0) return null
  const availableDirectories = availableDirectoriesForSnapshot(snapshotId)
  if (!availableDirectories.length) return null
  const items = draft.dirs.flatMap((dir) => {
    const targetPath = dir.targetPath || draft.destPath
    if (!targetPath) return [null]
    if (dir.scope === 'snapshot') {
      return availableDirectories.map((directory) => ({
        source_snapshot_directory_id: directory.id,
        selected_paths: [],
        target_path: targetPath,
        conflict_mode: draft.conflictPolicy,
      }))
    }
    const expanded = restoreSnapshotDirectoryForPath(snapshotId, dir.path)
    if (!expanded) return [null]
    return [{
      source_snapshot_directory_id: expanded.directory.id,
      selected_paths: expanded.selectedPaths,
      target_path: targetPath,
      conflict_mode: draft.conflictPolicy,
    }]
  })
  if (!items.length || items.some((item) => item === null)) return null
  const firstTargetPath = (items[0] as NonNullable<(typeof items)[number]> | undefined)?.target_path || draft.destPath || ''
  if (!firstTargetPath) return null
  const wholeSnapshot = draft.dirs.every((dir) => dir.scope === 'snapshot')
  return {
    source_snapshot_id: snapshotId,
    source_type: sourceEndpoint.type as 'agent' | 'nas',
    source_ref_id: sourceEndpoint.refId,
    target_type: targetEndpoint.type === 'nas' ? 'nas' : 'agent',
    target_ref_id: targetEndpoint.refId,
    target_path: firstTargetPath,
    scope: wholeSnapshot ? 'snapshot' : 'paths',
    conflict_mode: draft.conflictPolicy,
    items: items as RestoreRecordCreatePayload['items'],
    idempotency_key: `restore-${draft.backupId}-${Date.now()}`,
  }
}

function manualRecoveryDraftProblemNames(drafts: RecoveryTaskDraft[]) {
  const names = new Map<string, string>()
  for (const draft of drafts) {
    const snapshotId = Number(draft.snapshotId)
    const detail = Number.isFinite(snapshotId) && snapshotId > 0
      ? recoverySnapshotDetails.value.get(snapshotId)
      : undefined
    if (hasRestorableSnapshotDirectory(detail)) continue
    const sourceId = backupSourceHostId(draft.backupId)
    const row = flowRowFromSourceId(sourceId)
    names.set(sourceId || draft.backupId, row?.name || row?.nodeName || draft.backupName)
  }
  return [...names.values()].slice(0, 5).join(', ') + (names.size > 5 ? `, +${names.size - 5}` : '')
}

async function runRecovery(mode: 'plan' | 'manual' = 'manual') {
  if (recSubmitting.value) return
  recSubmitting.value = true
  try {
    if (mode === 'plan') {
      try {
        await ensureRecoveryPlanSnapshotDefaults()
      } catch (e) {
        showApiError(e)
        return
      }
      const plans = selectedRecoveryPlans.value
      if (plans.some((plan) => !(plan.restoreDir || plan.customSubdir))) {
        ElMessage.warning({ message: t('protection.backupsPage.msgRestoreDirectoryRequired'), grouping: true })
        return
      }
      const runnablePlans = plans.filter((plan) =>
        plan.backupConfigId
        && plan.targetRefId
        && Number(plan.snapshotId) > 0
        && Boolean(
          recoverySnapshotById(backupSourceHostId(plan.backupId), plan.snapshotId)
          && restorePlanSnapshotCompatibility(
            plan,
            recoverySnapshotById(backupSourceHostId(plan.backupId), plan.snapshotId) as RecoverySnapshotOption,
          ).compatible,
        ),
      )
      if (!runnablePlans.length || runnablePlans.length !== plans.length) {
        ElMessage.warning({ message: t('protection.backupsPage.msgPickSnapshotForRecovery'), grouping: true })
        return
      }
      await runRestoreJobsSequentially(
        runnablePlans.map((plan) => () => runRestorePlanBatch({
          backup_config_id: Number(plan.backupConfigId),
          target_type: (plan.targetType || 'agent') as RestoreEndpointType,
          target_ref_id: Number(plan.targetRefId),
          restore_dir: plan.restoreDir || plan.customSubdir,
          conflict_mode: plan.conflictPolicy,
          source_snapshot_id: Number(plan.snapshotId),
          idempotency_key: `restore-plan-${plan.backupConfigId}-${plan.snapshotId}-${Date.now()}`,
        })),
      )
    } else {
      const drafts = recoveryTaskDrafts(mode)
      if (!drafts.length) {
        ElMessage.warning({ message: t('protection.backupsPage.msgPickAtLeastOneDir'), grouping: true })
        return
      }
      const snapshotIds = [...new Set(
        drafts
          .map((draft) => Number(draft.snapshotId))
          .filter((id) => Number.isFinite(id) && id > 0),
      )]
      await refreshRecoverySnapshotDetails(snapshotIds)
      const problemNames = manualRecoveryDraftProblemNames(drafts)
      if (problemNames) {
        ElMessage.warning({
          message: t('protection.backupsPage.msgSelectedSourcesNoRestorableSnapshot', {
            names: problemNames,
          }),
          grouping: true,
        })
        return
      }
      const payloads = drafts.map(buildManualRestorePayload)
      if (payloads.some((payload) => payload === null)) {
        ElMessage.warning({ message: t('protection.backupsPage.msgSnapshotNoDirsAvailable'), grouping: true })
        return
      }
      await runRestoreJobsSequentially(
        (payloads as RestoreRecordCreatePayload[]).map((payload) => () => createRestoreRecord(payload)),
      )
    }
    if (isFixedSnapshotRestore.value) {
      fixedRestoreDirty.value = false
      ElMessage.success({ message: t('protection.backupsPage.msgRecoverySubmitted'), grouping: true })
      await leaveFixedRestore()
      return
    }
    recOpen.value = false
    await refreshBackupConfigs()
    ElMessage.success(
      mode === 'plan'
        ? t('protection.backupsPage.msgRecoveryPlanSubmitted')
        : t('protection.backupsPage.msgRecoverySubmitted'),
    )
  } catch (e) {
    if (await handleRestoreAlreadyRunning(e)) return
    showApiError(e)
  } finally {
    recSubmitting.value = false
  }
}

</script>

<template>
  <ModulePage
    :title="t('protection.moduleTitle')"
    :menus="protectionMenus"
    :body-flush="inlineEditorMode === null"
  >
    <template v-if="inlineEditorMode === null">
    <div class="protection-flow-workspace">
    <div class="dp-flow-steps-row flex flex-col gap-2 xl:flex-row xl:items-stretch shrink-0">
      <button
        type="button"
        class="dp-flow-card text-left flex-1"
        :class="{ 'dp-flow-card--active': flowMainStep === 0 }"
        :aria-pressed="flowMainStep === 0"
        @click="enterSourceStep"
      >
        <div class="dp-flow-card__mark" aria-hidden="true">
          <span class="dp-flow-card__index">01</span>
          <span class="dp-flow-card__icon">
            <component :is="backupFlowSourceStepIcon" :size="20" stroke-width="1.7" />
          </span>
        </div>
        <div class="dp-flow-card__body min-w-0 flex-1">
          <div class="dp-flow-card__header">
            <div class="dp-flow-card__heading">
              <span class="dp-flow-card__title">{{ t('protection.backupsPage.flowStepSourceTitle') }}</span>
            </div>
            <span class="dp-flow-card__pill" :class="{ 'dp-flow-card__pill--idle': flowMainStep !== 0 }">
              <Check v-if="flowMainStep === 0" :size="12" stroke-width="3" />
              {{ flowMainStep === 0
                ? t('protection.backupsPage.flowStepBadgeCurrent')
                : t('protection.backupsPage.flowStepBadgeSwitch') }}
            </span>
          </div>
          <p class="dp-flow-card__desc">{{ t('protection.backupsPage.flowStepSourceDesc') }}</p>
          <div class="dp-flow-card__meta">
            <span class="dp-flow-card__meta-dot" aria-hidden="true" />
            <span class="dp-flow-card__meta-text">
              {{ t('protection.backupsPage.flowStepSourceMetric', { n: backupSelectableCount }) }}
            </span>
          </div>
        </div>
      </button>

      <div class="dp-flow-steps-connector" aria-hidden="true">
        <ChevronsRight :size="22" stroke-width="2" class="dp-flow-steps-connector__icon" />
      </div>

      <button
        type="button"
        class="dp-flow-card text-left flex-1"
        :class="{ 'dp-flow-card--active': flowMainStep === 1 }"
        :aria-pressed="flowMainStep === 1"
        @click="enterBackupConfigStep()"
      >
        <div class="dp-flow-card__mark" aria-hidden="true">
          <span class="dp-flow-card__index">02</span>
          <span class="dp-flow-card__icon">
            <Database :size="20" stroke-width="1.7" />
          </span>
        </div>
        <div class="dp-flow-card__body min-w-0 flex-1">
          <div class="dp-flow-card__header">
            <div class="dp-flow-card__heading">
              <span class="dp-flow-card__title">{{ t('protection.backupsPage.flowStepBackupTitle') }}</span>
            </div>
            <span class="dp-flow-card__pill" :class="{ 'dp-flow-card__pill--idle': flowMainStep !== 1 }">
              <Check v-if="flowMainStep === 1" :size="12" stroke-width="3" />
              {{ flowMainStep === 1
                ? t('protection.backupsPage.flowStepBadgeCurrent')
                : t('protection.backupsPage.flowStepBadgeSwitch') }}
            </span>
          </div>
          <p class="dp-flow-card__desc">{{ t('protection.backupsPage.flowStepBackupDesc') }}</p>
          <div class="dp-flow-card__meta">
            <span class="dp-flow-card__meta-dot" aria-hidden="true" />
            <span class="dp-flow-card__meta-text">
              {{ t('protection.backupsPage.flowStepBackupMetricPending', { n: step2PendingCount }) }}
            </span>
          </div>
        </div>
      </button>

      <div class="dp-flow-steps-connector" aria-hidden="true">
        <ChevronsRight :size="22" stroke-width="2" class="dp-flow-steps-connector__icon" />
      </div>

      <button
        type="button"
        class="dp-flow-card text-left flex-1"
        :class="{ 'dp-flow-card--active': flowMainStep === 2 }"
        :aria-pressed="flowMainStep === 2"
        @click="enterStartBackupStep({ requireReady: true })"
      >
        <div class="dp-flow-card__mark" aria-hidden="true">
          <span class="dp-flow-card__index">03</span>
          <span class="dp-flow-card__icon">
            <CloudUpload :size="20" stroke-width="1.7" />
          </span>
        </div>
        <div class="dp-flow-card__body min-w-0 flex-1">
          <div class="dp-flow-card__header">
            <div class="dp-flow-card__heading">
              <span class="dp-flow-card__title">{{ t('protection.backupsPage.flowStepRestoreTitle') }}</span>
            </div>
            <span class="dp-flow-card__pill" :class="{ 'dp-flow-card__pill--idle': flowMainStep !== 2 }">
              <Check v-if="flowMainStep === 2" :size="12" stroke-width="3" />
              {{ flowMainStep === 2
                ? t('protection.backupsPage.flowStepBadgeCurrent')
                : t('protection.backupsPage.flowStepBadgeSwitch') }}
            </span>
          </div>
          <p class="dp-flow-card__desc">{{ t('protection.backupsPage.flowStepRestoreDesc') }}</p>
          <div class="dp-flow-card__meta">
            <span class="dp-flow-card__meta-dot" aria-hidden="true" />
            <span class="dp-flow-card__meta-text">
              {{ t('protection.backupsPage.flowStepRestoreMetric', { n: step3SelectableCount }) }}
            </span>
          </div>
        </div>
      </button>
    </div>

    <div class="hfl-list-shell protection-flow-list-shell">
      <div class="hfl-list-panel protection-flow-list-panel">
    <div
      ref="flowToolbarRef"
      class="hfl-list-toolbar hfl-list-toolbar--mobile-primary-utility protection-flow-toolbar"
    >
      <div class="hfl-list-toolbar__primary">
          <template v-if="flowMainStep === 0">
            <ElButton
              type="primary"
              :title="t('protection.backupsPage.btnAddBackupSourceDesc')"
              @click="onAddBackupSource"
            >
              <Plus :size="16" />
              {{ t('protection.backupsPage.btnAddBackupSource') }}
            </ElButton>
            <ElButton
              type="primary"
              :disabled="selectedSourceIds.length === 0 || flowAdvancingToBackupConfig"
              :loading="flowAdvancingToBackupConfig"
              @click="onGoToStep2"
            >
              <ArrowRight :size="16" class="shrink-0" />
              {{ t('protection.backupsPage.btnNext') }}
            </ElButton>
            <ElDropdown
              trigger="click"
              popper-class="hfl-actions-dropdown"
              @visible-change="moreActionsOpen = $event"
            >
              <ElButton>
                {{ t('protection.backupsPage.flowMoreActions') }}
                <ChevronDown
                  :size="16"
                  class="hfl-list-more__chev"
                  :class="{ 'hfl-list-more__chev--open': moreActionsOpen }"
                />
              </ElButton>
              <template #dropdown>
                <ElDropdownMenu>
                  <ElDropdownItem
                    :disabled="!step1DisplayNameEditEnabled"
                    @click="openStep1DisplayNameEditor"
                  >
                    <span class="el-dropdown-menu__item-content">
                      <Pencil :size="14" class="shrink-0" />
                      <span>{{ t('protection.backupsPage.flowActionEditDisplayName') }}</span>
                    </span>
                  </ElDropdownItem>
                  <ElDropdownItem
                    divided
                    class="el-dropdown-menu__item--danger"
                    :disabled="!step1UnregisterEnabled"
                    @click="deleteSelectedSourcesFromStep1"
                  >
                    <span class="hfl-dropdown-item-row hfl-dropdown-item-row--danger">
                      <Trash2 :size="14" class="shrink-0" />
                      <span>{{ t('protection.backupsPage.flowActionDelete') }}</span>
                    </span>
                  </ElDropdownItem>
                </ElDropdownMenu>
              </template>
            </ElDropdown>
          </template>
          <template v-else-if="flowMainStep === 1">
            <ElButton
              type="primary"
              class="hfl-btn-with-icon"
              :disabled="!step2ToolbarActionsEnabled || setupDrOpening"
              :loading="setupDrOpening"
              @click="onGoToCreateBackup"
            >
              <Database :size="16" class="shrink-0" />
              {{ t('protection.backupsPage.btnCreateBackup') }}
            </ElButton>
            <ElDropdown
              trigger="click"
              popper-class="hfl-actions-dropdown"
              @visible-change="moreActionsOpen = $event"
            >
              <ElButton>
                {{ t('protection.backupsPage.flowMoreActions') }}
                <ChevronDown
                  :size="16"
                  class="hfl-list-more__chev"
                  :class="{ 'hfl-list-more__chev--open': moreActionsOpen }"
                />
              </ElButton>
              <template #dropdown>
                <ElDropdownMenu>
                  <ElDropdownItem
                    :disabled="!step2DisplayNameEditEnabled"
                    @click="openStep2DisplayNameEditor"
                  >
                    <span class="el-dropdown-menu__item-content">
                      <Pencil :size="14" class="shrink-0" />
                      <span>{{ t('protection.backupsPage.flowActionEditDisplayName') }}</span>
                    </span>
                  </ElDropdownItem>
                  <ElDropdownItem
                    divided
                    class="el-dropdown-menu__item--danger"
                    :disabled="!step2UnregisterEnabled"
                    @click="deleteSelectedSourcesFromStep2"
                  >
                    <span class="hfl-dropdown-item-row hfl-dropdown-item-row--danger">
                      <Trash2 :size="14" class="shrink-0" />
                      <span>{{ t('protection.backupsPage.flowActionDelete') }}</span>
                    </span>
                  </ElDropdownItem>
                </ElDropdownMenu>
              </template>
            </ElDropdown>
          </template>
          <template v-else>
            <ElTooltip
              :content="t('protection.backupsPage.btnStartBackupCloudHint')"
              placement="bottom"
              :show-after="300"
              :hide-after="0"
            >
              <span class="dp-flow-step3-action-tooltip">
                <ElButton
                  type="primary"
                  plain
                  class="hfl-btn-with-icon dp-flow-step3-action-btn shrink-0"
                  :disabled="!step3SourceActionsEnabled || startBackupSubmitting || step3StopActionBusy"
                  :loading="startBackupSubmitting"
                  @click="startSelectedBackupTasks"
                >
                  <CloudUpload :size="16" class="shrink-0" />
                  {{ t('protection.backupsPage.btnStartBackup') }}
                </ElButton>
              </span>
            </ElTooltip>
            <ElButton
              type="primary"
              class="hfl-btn-with-icon dp-flow-step3-action-btn shrink-0"
              :disabled="!recoveryToolbarEnabled || recoveryOpening || step3StopActionBusy"
              :loading="recoveryOpening"
              @click="openRecovery"
            >
              <ArchiveRestore :size="16" class="shrink-0" />
              {{ t('protection.backupsPage.btnRecover') }}
            </ElButton>
            <ElDropdown
              trigger="click"
              popper-class="hfl-actions-dropdown"
              @visible-change="moreActionsOpen = $event"
            >
              <ElButton>
                {{ t('protection.backupsPage.flowMoreActions') }}
                <ChevronDown
                  :size="16"
                  class="hfl-list-more__chev"
                  :class="{ 'hfl-list-more__chev--open': moreActionsOpen }"
                />
              </ElButton>
              <template #dropdown>
                <ElDropdownMenu>
                  <ElDropdownItem
                    :disabled="!step3DisplayNameEditEnabled"
                    @click="openStep3DisplayNameEditor"
                  >
                    <span class="el-dropdown-menu__item-content">
                      <Pencil :size="14" class="shrink-0" />
                      <span>{{ t('protection.backupsPage.flowActionEditDisplayName') }}</span>
                    </span>
                  </ElDropdownItem>
                  <ElDropdownItem
                    divided
                    :disabled="!step3SelectionEditable"
                    @click="openBackupConfigEditFromStep3('paths')"
                  >
                    <span class="el-dropdown-menu__item-content">
                      <FolderTree :size="14" class="shrink-0" />
                      <span>{{ t('protection.backupsPage.flowActionEditBackupPaths') }}</span>
                    </span>
                  </ElDropdownItem>
                  <ElDropdownItem
                    :disabled="!step3SelectionEditable"
                    @click="openBackupConfigEditFromStep3('policy')"
                  >
                    <span class="el-dropdown-menu__item-content">
                      <ClipboardCheck :size="14" class="shrink-0" />
                      <span>{{ t('protection.backupsPage.flowActionEditBackupPolicy') }}</span>
                    </span>
                  </ElDropdownItem>
                  <ElDropdownItem
                    :disabled="!step3SelectionEditable"
                    @click="openBackupConfigEditFromStep3('recovery')"
                  >
                    <span class="el-dropdown-menu__item-content">
                      <Route :size="14" class="shrink-0" />
                      <span>{{ t('protection.backupsPage.flowActionEditRestorePlan') }}</span>
                    </span>
                  </ElDropdownItem>
                  <ElDropdownItem
                    divided
                    :disabled="!step3CanStopBackup || step3StopActionBusy"
                    @click="openStopBackupConfirmDialog"
                  >
                    <span class="el-dropdown-menu__item-content">
                      <CircleStop :size="14" class="shrink-0" />
                      <span>{{ t('protection.backupsPage.flowActionStopBackup') }}</span>
                    </span>
                  </ElDropdownItem>
                  <ElDropdownItem
                    :disabled="!step3CanStopRestore || step3StopActionBusy"
                    @click="openStopRestoreConfirmDialog"
                  >
                    <span class="el-dropdown-menu__item-content">
                      <CircleStop :size="14" class="shrink-0" />
                      <span>{{ t('protection.backupsPage.flowActionStopRestore') }}</span>
                    </span>
                  </ElDropdownItem>
                  <ElDropdownItem
                    divided
                    :disabled="!step3SelectionEditable"
                    @click="revertSelectedSourcesFromStep3"
                  >
                    <span class="el-dropdown-menu__item-content">
                      <Undo2 :size="14" class="shrink-0" />
                      <span>{{ t('protection.backupsPage.actionResetBackupConfig') }}</span>
                    </span>
                  </ElDropdownItem>
                  <ElDropdownItem
                    class="el-dropdown-menu__item--danger"
                    :disabled="!step3UnregisterEnabled"
                    @click="deleteSelectedSourcesFromStep3"
                  >
                    <span class="hfl-dropdown-item-row hfl-dropdown-item-row--danger">
                      <Trash2 :size="14" class="shrink-0" />
                      <span>{{ t('protection.backupsPage.flowActionDelete') }}</span>
                    </span>
                  </ElDropdownItem>
                </ElDropdownMenu>
              </template>
            </ElDropdown>
          </template>
      </div>
        <div class="hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split">
          <ElInput
            v-model="taskSearchQuery"
            clearable
            size="small"
            :placeholder="t('protection.backupsPage.step3SearchPlaceholder')"
            class="hfl-list-search hfl-list-search-group"
            @keyup.enter="applyTaskSearchImmediately"
            @clear="applyTaskSearchImmediately"
          >
            <template #prepend>
              <ElSelect v-model="step3SearchField" @change="onStep3SearchFieldChange">
                <ElOption
                  v-for="item in step3SearchFieldOptions"
                  :key="item.value"
                  :label="item.label"
                  :value="item.value"
                />
              </ElSelect>
            </template>
          </ElInput>
          <ElSelect
            v-if="flowMainStep === 2"
            v-model="step3BackupTaskStatus"
            clearable
            size="small"
            :placeholder="t('protection.backupsPage.step3BackupTask')"
            style="width: 130px"
          >
            <ElOption
              v-for="item in step3BackupTaskStatusOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </ElSelect>
          <ElSelect
            v-if="flowMainStep === 2"
            v-model="step3RestoreTaskStatus"
            clearable
            size="small"
            :placeholder="t('protection.backupsPage.step3RestoreTask')"
            style="width: 150px"
          >
            <ElOption
              v-for="item in step3RestoreTaskStatusOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </ElSelect>
          <ElSelect
            v-if="flowMainStep !== 2"
            v-model="step3Availability"
            clearable
            size="small"
            :placeholder="t('protection.backupsPage.step3Availability')"
            style="width: 130px"
          >
            <ElOption
              v-for="item in step3AvailabilityOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </ElSelect>
          <ElSelect
            v-if="flowMainStep === 2"
            v-model="step3Availability"
            clearable
            size="small"
            :placeholder="t('protection.backupsPage.step3Availability')"
            style="width: 130px"
          >
            <ElOption
              v-for="item in step3AvailabilityOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </ElSelect>
          <div class="hfl-list-toolbar__utility">
            <ElButton
              :title="t('protection.backupsPage.flowFilterAdvanced')"
              class="hfl-filter-button"
              :class="{ 'hfl-filter-button--active': flowActiveFilterCount > 0 }"
              :aria-label="t('protection.backupsPage.flowFilterAdvanced')"
              @click="openAdvancedFilters"
            >
              <ElBadge v-if="flowActiveFilterCount > 0" :value="flowActiveFilterCount" :max="9" class="hfl-filter-badge">
                <Filter :size="16" />
              </ElBadge>
              <Filter v-else :size="16" />
            </ElButton>
            <ElButton
              class="hfl-refresh-button"
              :title="t('protection.backupsPage.flowActionRefreshList')"
              :aria-label="t('protection.backupsPage.flowActionRefreshList')"
              :disabled="flowRefreshing"
              @click="refreshTaskLists"
            >
              <RefreshCw :size="16" :class="{ 'is-spinning': flowRefreshing }" />
            </ElButton>
          </div>
        </div>
    </div>

    <div ref="flowTableZoneRef" class="protection-flow-table-zone">
    <div v-if="flowMainStep === 0" class="protection-flow-panel protection-flow-panel--fill">
      <div class="protection-flow-table-block">
      <el-table
          v-table-overflow-title
        v-table-header-scroll-sync
        v-table-column-resize="'protection.dataProtection.sources'"
        v-loading="backupSelectableLoading"
        class="hfl-list-table source-table--flow-pick"
        :data="filteredBackupSelectableRows"
        stripe
        row-key="id"
        :max-height="flowTableMaxHeight"
        :header-cell-style="TABLE_HEADER_STYLE"
        ref="sourceTableRef"
        @selection-change="onSourceSelectionChange"
        @cell-click="onFlowSourceCellClick"
      >
        <el-table-column type="selection" width="35" fixed="left" reserve-selection :selectable="flowSourceRowSelectable" />
        <el-table-column
          fixed="left"
          column-key="backup-source"
          :label="t('protection.backupsPage.colBackupSource')"
          :min-width="FLOW_PICK_TABLE_COL_MIN.source"
        >
          <template #default="{ row }">
            <FlowSourceSummaryCell
              :row="row"
              :interactive="false"
              externally-interactive
            />
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupsPage.colConnectionAddress')"
          :min-width="FLOW_PICK_TABLE_COL_MIN.connection"
        >
          <template #default="{ row }">
            <FlowSourceConnectionCell :row="row" />
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.sourceResources.colLifecycleStatus')"
          width="168"
          align="center"
          header-align="center"
        >
          <template #header>
            <span class="flow-filterable-header">
              <span>{{ t('protection.sourceResources.colLifecycleStatus') }}</span>
              <HflPopover v-if="flowMainStep !== 2" v-model:visible="flowHeaderFilterOpen.sourceStatus" trigger="click" placement="bottom" :width="210" popper-class="flow-header-filter-popper">
                <template #reference>
                  <button type="button" class="flow-header-filter-trigger" :class="{ 'flow-header-filter-trigger--active': hasFlowHeaderFilterValue('sourceStatus') }" @click.stop>
                    <Filter :size="14" />
                  </button>
                </template>
                <div class="flow-header-filter-panel">
                  <ElInput v-model="flowHeaderFilterSearch.sourceStatus" size="small" clearable :placeholder="t('protection.backupsPage.flowFilterSearchPlaceholder')" />
                  <ElCheckboxGroup v-model="flowFilterSourceStatuses" class="flow-header-filter-options">
                    <ElCheckbox v-for="item in visibleFlowHeaderFilterOptions(flowSourceStatusFilterOptions, 'sourceStatus')" :key="item.value" :value="item.value">
                      {{ item.text }}
                    </ElCheckbox>
                  </ElCheckboxGroup>
                  <div v-if="visibleFlowHeaderFilterOptions(flowSourceStatusFilterOptions, 'sourceStatus').length === 0" class="flow-header-filter-empty">
                    {{ t('protection.backupsPage.flowFilterNoOptions') }}
                  </div>
                  <div class="flow-header-filter-actions">
                    <ElButton text size="small" @click="clearFlowHeaderFilter('sourceStatus')">{{ t('protection.backupsPage.flowFilterReset') }}</ElButton>
                    <ElButton text size="small" type="primary" @click="closeFlowHeaderFilter('sourceStatus')">{{ t('protection.backupsPage.flowFilterApply') }}</ElButton>
                  </div>
                </div>
              </HflPopover>
            </span>
          </template>
          <template #default="{ row }">
            <FlowSourceReadyStatusCell
              v-bind="resolveFlowSourceDisplayStatus(row)"
              neutral-as-danger
              @click="openSourcePendingFailureDetails(row.id)"
            />
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.sourceResources.colCpu')"
          :min-width="FLOW_PICK_TABLE_COL_MIN.cpu"
        >
          <template #default="{ row }">
            <span>{{
              flowSourceCpuCores(row) != null
                ? t('protection.sourceResources.cpuCoresValue', { n: flowSourceCpuCores(row) })
                : '—'
            }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.sourceResources.colMemory')"
          :min-width="FLOW_PICK_TABLE_COL_MIN.memory"
        >
          <template #default="{ row }">
            <span>{{ flowSourceMemoryText(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.sourceResources.colDiskCount')"
          :min-width="FLOW_PICK_TABLE_COL_MIN.diskCount"
        >
          <template #default="{ row }">
            <span>{{ flowSourceDiskCountText(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.sourceResources.colConnectivity')"
          width="110"
          align="center"
          header-align="center"
        >
          <template #default="{ row }">
            <div class="hfl-table-no-tooltip">
              <ElTag :type="flowSourceAvailabilityTagType(row.availability)" size="small">
                {{ flowSourceAvailabilityLabel(row.availability) }}
              </ElTag>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="t('protection.sourceResources.colRegisteredAt')" width="154">
          <template #default="{ row }">
            <span class="hfl-table-cell-time" :class="{ 'hfl-empty-mark': !row.registeredAt }">{{ flowSourceRegisteredAt(row.registeredAt) }}</span>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :image-size="72">
            <template #description>
              <p>
                {{ t('protection.backupsPage.flowTaskEmptySourcePrefix') }}
                <strong>{{ t('protection.backupsPage.flowTaskEmptySourceAction') }}</strong>
                {{ t('protection.backupsPage.flowTaskEmptySourceSuffix') }}
              </p>
            </template>
          </el-empty>
        </template>
      </el-table>
      <div class="hfl-list-footer">
        <span v-if="selectedSourceIds.length > 0" class="hfl-list-footer__selected">
          {{ t('protection.sourceResources.selectedCount', { n: selectedSourceIds.length }) }}
        </span>
        <HflPagination
          class="hfl-list-footer__pagination"
          v-model:current-page="flowStep0Pager.page"
          v-model:page-size="flowStep0Pager.pageSize"
          :total="flowStep0DisplayTotal"
        />
      </div>
      </div>
    </div>

    <div v-if="flowMainStep === 1" class="protection-flow-panel protection-flow-panel--fill">
      <div class="protection-flow-table-block">
      <el-table
          v-table-overflow-title
        v-table-header-scroll-sync
        v-table-column-resize="'protection.dataProtection.configurations'"
        v-loading="flowStepDataLoading[1]"
        class="hfl-list-table source-table--flow-pick"
        :data="paginatedStep2SourceList"
        stripe
        row-key="id"
        :max-height="flowTableMaxHeight"
        :header-cell-style="TABLE_HEADER_STYLE"
        ref="step2TableRef"
        @selection-change="onStep2SelectionChange"
        @cell-click="onFlowSourceCellClick"
      >
        <el-table-column type="selection" width="35" fixed="left" reserve-selection :selectable="flowSourceRowSelectable" />
        <el-table-column
          fixed="left"
          column-key="backup-source"
          :label="t('protection.backupsPage.colBackupSource')"
          :min-width="FLOW_PICK_TABLE_COL_MIN.source"
        >
          <template #default="{ row }">
            <FlowSourceSummaryCell
              :row="row"
              :interactive="false"
              externally-interactive
            />
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupsPage.colConnectionAddress')"
          :min-width="FLOW_PICK_TABLE_COL_MIN.connection"
        >
          <template #default="{ row }">
            <FlowSourceConnectionCell :row="row" />
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.sourceResources.colLifecycleStatus')"
          width="168"
          align="center"
          header-align="center"
        >
          <template #header>
            <span class="flow-filterable-header">
              <span>{{ t('protection.sourceResources.colLifecycleStatus') }}</span>
              <HflPopover v-if="flowMainStep !== 2" v-model:visible="flowHeaderFilterOpen.sourceStatus" trigger="click" placement="bottom" :width="210" popper-class="flow-header-filter-popper">
                <template #reference>
                  <button type="button" class="flow-header-filter-trigger" :class="{ 'flow-header-filter-trigger--active': hasFlowHeaderFilterValue('sourceStatus') }" @click.stop>
                    <Filter :size="14" />
                  </button>
                </template>
                <div class="flow-header-filter-panel">
                  <ElInput v-model="flowHeaderFilterSearch.sourceStatus" size="small" clearable :placeholder="t('protection.backupsPage.flowFilterSearchPlaceholder')" />
                  <ElCheckboxGroup v-model="flowFilterSourceStatuses" class="flow-header-filter-options">
                    <ElCheckbox v-for="item in visibleFlowHeaderFilterOptions(flowSourceStatusFilterOptions, 'sourceStatus')" :key="item.value" :value="item.value">
                      {{ item.text }}
                    </ElCheckbox>
                  </ElCheckboxGroup>
                  <div v-if="visibleFlowHeaderFilterOptions(flowSourceStatusFilterOptions, 'sourceStatus').length === 0" class="flow-header-filter-empty">
                    {{ t('protection.backupsPage.flowFilterNoOptions') }}
                  </div>
                  <div class="flow-header-filter-actions">
                    <ElButton text size="small" @click="clearFlowHeaderFilter('sourceStatus')">{{ t('protection.backupsPage.flowFilterReset') }}</ElButton>
                    <ElButton text size="small" type="primary" @click="closeFlowHeaderFilter('sourceStatus')">{{ t('protection.backupsPage.flowFilterApply') }}</ElButton>
                  </div>
                </div>
              </HflPopover>
            </span>
          </template>
          <template #default="{ row }">
            <FlowSourceReadyStatusCell
              v-bind="resolveFlowSourceDisplayStatus(row)"
              neutral-as-danger
              @click="openSourcePendingFailureDetails(row.id)"
            />
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.sourceResources.colCpu')"
          :min-width="FLOW_PICK_TABLE_COL_MIN.cpu"
        >
          <template #default="{ row }">
            <span>{{
              flowSourceCpuCores(row) != null
                ? t('protection.sourceResources.cpuCoresValue', { n: flowSourceCpuCores(row) })
                : '—'
            }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.sourceResources.colMemory')"
          :min-width="FLOW_PICK_TABLE_COL_MIN.memory"
        >
          <template #default="{ row }">
            <span>{{ flowSourceMemoryText(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.sourceResources.colDiskCount')"
          :min-width="FLOW_PICK_TABLE_COL_MIN.diskCount"
        >
          <template #default="{ row }">
            <span>{{ flowSourceDiskCountText(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.sourceResources.colConnectivity')"
          width="110"
          align="center"
          header-align="center"
        >
          <template #default="{ row }">
            <div class="hfl-table-no-tooltip">
              <ElTag :type="flowSourceAvailabilityTagType(row.availability)" size="small">
                {{ flowSourceAvailabilityLabel(row.availability) }}
              </ElTag>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="t('protection.sourceResources.colRegisteredAt')" width="154">
          <template #default="{ row }">
            <span class="hfl-table-cell-time" :class="{ 'hfl-empty-mark': !row.registeredAt }">{{ flowSourceRegisteredAt(row.registeredAt) }}</span>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :image-size="72">
            <template #description>
              <p v-if="step2AllSourcesConfigured">
                {{ t('protection.backupsPage.flowStep2EmptyAllConfigured') }}
              </p>
              <p v-else>
                {{ t('protection.backupsPage.flowTaskEmptyBackupConfigPrefix') }}
                <strong>{{ t('protection.backupsPage.flowTaskEmptyBackupConfigStep') }}</strong>
                {{ t('protection.backupsPage.flowTaskEmptyBackupConfigSuffix') }}
              </p>
            </template>
          </el-empty>
        </template>
      </el-table>
      <div class="hfl-list-footer">
        <span v-if="step1Selection.length > 0" class="hfl-list-footer__selected">
          {{ t('protection.sourceResources.selectedCount', { n: step1Selection.length }) }}
        </span>
        <HflPagination
          class="hfl-list-footer__pagination"
          v-model:current-page="flowStep1Pager.page"
          v-model:page-size="flowStep1Pager.pageSize"
          :total="flowMainStep === 1 ? step2SelectableCount : filteredStep2SourceList.length"
        />
      </div>
      </div>
    </div>

    <div v-if="flowMainStep === 2" class="protection-flow-panel protection-flow-panel--fill">
      <div class="protection-flow-table-block">
      <el-table
          v-table-overflow-title
        v-table-header-scroll-sync
        v-table-column-resize="'protection.dataProtection.startBackup'"
        v-loading="flowStepDataLoading[2]"
        class="hfl-list-table"
        :data="paginatedStep3SourceList"
        stripe
        row-key="id"
        :max-height="flowTableMaxHeight"
        :header-cell-style="TABLE_HEADER_STYLE"
        ref="step3TableRef"
        @selection-change="onBackupTaskSelection"
        @cell-click="onFlowSourceCellClick"
      >
        <el-table-column type="selection" width="35" fixed="left" reserve-selection :selectable="flowSourceRowSelectable" />
        <el-table-column
          fixed="left"
          column-key="backup-source"
          :label="t('protection.backupsPage.colBackupSource')"
          :min-width="FLOW_PICK_TABLE_COL_MIN.source"
        >
          <template #header>
            <span class="flow-filterable-header">
              <span>{{ t('protection.backupsPage.colBackupSource') }}</span>
              <HflPopover v-if="flowMainStep !== 2" v-model:visible="flowHeaderFilterOpen.sourceType" trigger="click" placement="bottom" :width="224" popper-class="flow-header-filter-popper">
                <template #reference>
                  <button type="button" class="flow-header-filter-trigger" :class="{ 'flow-header-filter-trigger--active': hasFlowHeaderFilterValue('sourceType') }" @click.stop>
                    <Filter :size="14" />
                  </button>
                </template>
                <div class="flow-header-filter-panel">
                  <ElInput v-model="flowHeaderFilterSearch.sourceType" size="small" clearable :placeholder="t('protection.backupsPage.flowFilterSearchPlaceholder')" />
                  <ElCheckboxGroup v-model="flowFilterSourceTypes" class="flow-header-filter-options">
                    <ElCheckbox v-for="item in visibleFlowHeaderFilterOptions(flowSourceTypeFilterOptions, 'sourceType')" :key="item.value" :value="item.value">
                      {{ item.text }}
                    </ElCheckbox>
                  </ElCheckboxGroup>
                  <div v-if="visibleFlowHeaderFilterOptions(flowSourceTypeFilterOptions, 'sourceType').length === 0" class="flow-header-filter-empty">
                    {{ t('protection.backupsPage.flowFilterNoOptions') }}
                  </div>
                  <div class="flow-header-filter-actions">
                    <ElButton text size="small" @click="clearFlowHeaderFilter('sourceType')">{{ t('protection.backupsPage.flowFilterReset') }}</ElButton>
                    <ElButton text size="small" type="primary" @click="closeFlowHeaderFilter('sourceType')">{{ t('protection.backupsPage.flowFilterApply') }}</ElButton>
                  </div>
                </div>
              </HflPopover>
            </span>
          </template>
          <template #default="{ row }">
            <FlowSourceSummaryCell
              :row="row"
              :interactive="false"
              externally-interactive
            />
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupsPage.colConnectionAddress')"
          :min-width="FLOW_START_BACKUP_TABLE_COL_MIN.connection"
        >
          <template #default="{ row }">
            <FlowSourceConnectionCell :row="row" />
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.sourceResources.colLifecycleStatus')"
          width="168"
          align="center"
          header-align="center"
        >
          <template #header>
            <span class="flow-filterable-header">
              <span>{{ t('protection.sourceResources.colLifecycleStatus') }}</span>
              <HflPopover v-if="flowMainStep !== 2" v-model:visible="flowHeaderFilterOpen.sourceStatus" trigger="click" placement="bottom" :width="210" popper-class="flow-header-filter-popper">
                <template #reference>
                  <button type="button" class="flow-header-filter-trigger" :class="{ 'flow-header-filter-trigger--active': hasFlowHeaderFilterValue('sourceStatus') }" @click.stop>
                    <Filter :size="14" />
                  </button>
                </template>
                <div class="flow-header-filter-panel">
                  <ElInput v-model="flowHeaderFilterSearch.sourceStatus" size="small" clearable :placeholder="t('protection.backupsPage.flowFilterSearchPlaceholder')" />
                  <ElCheckboxGroup v-model="flowFilterSourceStatuses" class="flow-header-filter-options">
                    <ElCheckbox v-for="item in visibleFlowHeaderFilterOptions(flowSourceStatusFilterOptions, 'sourceStatus')" :key="item.value" :value="item.value">
                      {{ item.text }}
                    </ElCheckbox>
                  </ElCheckboxGroup>
                  <div v-if="visibleFlowHeaderFilterOptions(flowSourceStatusFilterOptions, 'sourceStatus').length === 0" class="flow-header-filter-empty">
                    {{ t('protection.backupsPage.flowFilterNoOptions') }}
                  </div>
                  <div class="flow-header-filter-actions">
                    <ElButton text size="small" @click="clearFlowHeaderFilter('sourceStatus')">{{ t('protection.backupsPage.flowFilterReset') }}</ElButton>
                    <ElButton text size="small" type="primary" @click="closeFlowHeaderFilter('sourceStatus')">{{ t('protection.backupsPage.flowFilterApply') }}</ElButton>
                  </div>
                </div>
              </HflPopover>
            </span>
          </template>
          <template #default="{ row }">
            <button
              v-if="sourceResetState(row.id)"
              type="button"
              class="reset-status-cell"
              :class="`reset-status-cell--${sourceResetState(row.id)}`"
              :title="t('protection.backupsPage.resetStatusClickHint')"
              @click.stop="openResetTaskDetail(row)"
            >
              <span class="reset-status-cell__label">{{ sourceResetStatusLabel(row.id) }}</span>
              <span v-if="sourceResetState(row.id) === 'resetting'" class="reset-status-cell__progress">
                <span class="reset-status-cell__track">
                  <span class="reset-status-cell__fill" :style="{ width: `${sourceResetProgress(row.id)}%` }" />
                </span>
                <span class="reset-status-cell__percent">{{ sourceResetProgress(row.id) }}%</span>
              </span>
            </button>
            <FlowSourceReadyStatusCell
              v-else
              v-bind="resolveFlowSourceDisplayStatus(row)"
              neutral-as-danger
              @click="openSourcePendingFailureDetails(row.id)"
            />
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupsPage.flowBackupColBackupDirs')"
          :min-width="FLOW_START_BACKUP_TABLE_COL_MIN.backupDirs"
        >
          <template #default="{ row }">
            <div v-if="sourceConfigDirRows(row.id).length" class="table-stack-list">
              <HflPopover
                placement="right-start"
                trigger="hover"
                :hide-after="0"
                :width="420"
                append-to-body
              >
                <template #reference>
                  <div class="table-stack-list flow-dir-preview-list">
                    <div
                      v-for="(dir, index) in sourceDirsPreview(row.id)"
                      :key="`${row.id}-${dir.configId}-${dir.path}`"
                      class="source-path-text flow-dir-preview-list__row"
                      :class="{ 'flow-dir-preview-list__row--has-more': index === 0 && sourceDirsOverflowCount(row.id) > 0 }"
                    >
                      <span class="flow-dir-preview-list__path">{{ dir.path }}</span>
                      <span v-if="index === 0 && sourceDirsOverflowCount(row.id) > 0" class="flow-dir-preview-list__more" aria-hidden="true">
                        <MoreHorizontal :size="16" />
                      </span>
                    </div>
                  </div>
                </template>
                <ul class="m-0 list-none p-0 text-sm text-slate-800 space-y-2">
                  <li v-for="dir in sourceConfigDirRows(row.id)" :key="`all-${row.id}-${dir.configId}-${dir.path}`">
                    <code class="flow-source-list-drawer-path">{{ dir.path }}</code>
                  </li>
                </ul>
              </HflPopover>
            </div>
            <span v-else class="hfl-empty-mark">{{ t('protection.backupDetail.durationDash') }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('protection.backupsPage.flowBackupColCurrentTaskStatus')" min-width="228">
          <template #default="{ row }">
            <button
              type="button"
              class="backup-task-trigger"
              :disabled="!latestBackupTaskUuidForSource(row.id)"
              :title="t('protection.backupsPage.backupTaskStatusClickHint')"
              @click.stop="openLatestBackupTask(row)"
            >
              <TaskProgressCell
                v-if="sourceBackupCellPhase(row.id) === 'running'"
                :failed="sourceBackupRuntime(row.id).failed"
                :progress="sourceBackupRuntime(row.id).progress"
                :transfer-progress="sourceBackupRuntime(row.id).transferProgress"
              />
              <TaskProgressCell
                v-else-if="sourceBackupCellPhase(row.id) === 'stopping'"
                :progress="sourceBackupRuntime(row.id).progress"
                :transfer-progress="sourceBackupRuntime(row.id).transferProgress"
                stopping
              />
              <TaskTerminalOutcomeCell
                v-else
                :task="latestBackupTaskForSource(row.id)"
                :fallback="latestSnapshotForSource(row.id)"
              />
            </button>
          </template>
        </el-table-column>
        <el-table-column :label="t('protection.backupsPage.flowBackupColRestoreTaskStatus')" min-width="228">
          <template #default="{ row }">
            <button
              type="button"
              class="backup-task-trigger"
              :disabled="!latestRestoreTaskForSource(row.id)?.task_uuid && !latestRestoreRecordForSource(row.id)?.task_uuid"
              :title="t('protection.backupsPage.restoreTaskStatusClickHint')"
              @click.stop="openLatestRestoreTask(row)"
            >
              <TaskProgressCell
                v-if="sourceRestoreCellPhase(row.id) === 'running'"
                :failed="sourceRestoreRuntime(row.id).failed"
                :progress="sourceRestoreRuntime(row.id).progress"
                :transfer-progress="sourceRestoreRuntime(row.id).transferProgress"
              />
              <TaskProgressCell
                v-else-if="sourceRestoreCellPhase(row.id) === 'stopping'"
                :progress="sourceRestoreRuntime(row.id).progress"
                :transfer-progress="sourceRestoreRuntime(row.id).transferProgress"
                stopping
              />
              <TaskTerminalOutcomeCell
                v-else
                :task="latestRestoreTaskForSource(row.id)"
                :fallback="latestRestoreRecordForSource(row.id)?.task_summary"
              />
            </button>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.sourceResources.colConnectivity')"
          width="110"
          align="center"
          header-align="center"
        >
          <template #default="{ row }">
            <div class="hfl-table-no-tooltip">
              <ElTag :type="flowSourceAvailabilityTagType(row.availability)" size="small">
                {{ flowSourceAvailabilityLabel(row.availability) }}
              </ElTag>
            </div>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupsPage.flowBackupColTargetRepo')"
          :min-width="FLOW_START_BACKUP_TABLE_COL_MIN.targetRepo"
        >
          <template #header>
            <span class="flow-filterable-header">
              <span>{{ t('protection.backupsPage.flowBackupColTargetRepo') }}</span>
              <HflPopover v-if="flowMainStep !== 2" v-model:visible="flowHeaderFilterOpen.repoHealth" trigger="click" placement="bottom" :width="224" popper-class="flow-header-filter-popper">
                <template #reference>
                  <button type="button" class="flow-header-filter-trigger" :class="{ 'flow-header-filter-trigger--active': hasFlowHeaderFilterValue('repoHealth') }" @click.stop>
                    <Filter :size="14" />
                  </button>
                </template>
                <div class="flow-header-filter-panel">
                  <ElInput v-model="flowHeaderFilterSearch.repoHealth" size="small" clearable :placeholder="t('protection.backupsPage.flowFilterSearchPlaceholder')" />
                  <ElCheckboxGroup v-model="flowFilterRepoHealth" class="flow-header-filter-options">
                    <ElCheckbox v-for="item in visibleFlowHeaderFilterOptions(flowRepoHealthFilterOptions, 'repoHealth')" :key="item.value" :value="item.value">
                      {{ item.text }}
                    </ElCheckbox>
                  </ElCheckboxGroup>
                  <div v-if="visibleFlowHeaderFilterOptions(flowRepoHealthFilterOptions, 'repoHealth').length === 0" class="flow-header-filter-empty">
                    {{ t('protection.backupsPage.flowFilterNoOptions') }}
                  </div>
                  <div class="flow-header-filter-actions">
                    <ElButton text size="small" @click="clearFlowHeaderFilter('repoHealth')">{{ t('protection.backupsPage.flowFilterReset') }}</ElButton>
                    <ElButton text size="small" type="primary" @click="closeFlowHeaderFilter('repoHealth')">{{ t('protection.backupsPage.flowFilterApply') }}</ElButton>
                  </div>
                </div>
              </HflPopover>
            </span>
          </template>
          <template #default="{ row }">
            <div v-if="sourceConfigTargetRows(row.id).length" class="table-stack-list">
              <HflPopover
                v-for="target in sourceTargetsPreview(row.id)"
                :key="`${row.id}-target-${target.id}`"
                class="table-stack-cell flow-target-repository-popover"
                placement="top-start"
                trigger="hover"
                :hide-after="FLOW_DETAIL_POPOVER_HIDE_AFTER_MS"
                :width="420"
                :show-after="300"
                append-to-body
              >
                <template #reference>
                  <div
                    class="wizard-target-repository-cell hfl-table-no-tooltip"
                    :class="`wizard-target-repository-cell--${flowTargetTone(target)}`"
                    :title="target.location ? `${target.name}\n${target.location}` : target.name"
                  >
                    <span class="wizard-target-repository-cell__dot" aria-hidden="true"></span>
                    <div class="wizard-target-repository-cell__body">
                      <div class="wizard-target-repository-cell__name">{{ target.name }}</div>
                      <div
                        v-if="target.location"
                        class="wizard-target-repository-cell__location hfl-table-cell-mono"
                      >
                        {{ target.location }}
                      </div>
                    </div>
                  </div>
                </template>
                <TargetRepositoryDetailCard :target="target" :hide-capacity="true" />
              </HflPopover>
              <button
                v-if="sourceTargetsOverflowCount(row.id) > 0"
                type="button"
                class="source-more-link"
                :title="t('protection.backupsPage.flowSourceTargetDrawerOpenHint')"
                @click.stop="openFlowSourceListDrawer(row, 'targets')"
              >
                {{ t('protection.backupsPage.flowSourceTargetOverflow', { n: sourceTargetsOverflowCount(row.id) }) }}
              </button>
            </div>
            <span v-else class="hfl-empty-mark">{{ t('protection.backupDetail.durationDash') }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupsPage.labelCompressionStrategy')"
          :min-width="FLOW_START_BACKUP_TABLE_COL_MIN.compression"
        >
          <template #default="{ row }">
            <HflPopover
              v-if="sourceCompressionLabel(row.id)"
              trigger="hover"
              placement="bottom-start"
              :width="288"
              :fallback-placements="['bottom-start', 'bottom-end']"
              popper-class="flow-compression-popper"
            >
              <template #reference>
                <span class="flow-compression-cell hfl-table-no-tooltip">
                  <component
                    :is="compressionLevelIcon(sourceCompressionLevel(row.id) ?? 'balanced')"
                    :size="15"
                    aria-hidden="true"
                    class="flow-compression-cell__icon"
                    :class="`flow-compression-cell__icon--${sourceCompressionLevel(row.id) ?? 'balanced'}`"
                  />
                  <span class="flow-compression-cell__label">{{ sourceCompressionLabel(row.id) }}</span>
                </span>
              </template>
              <div class="flow-compression-popover">
                <div class="flow-compression-popover__title">
                  <component
                    :is="compressionLevelIcon(sourceCompressionLevel(row.id) ?? 'balanced')"
                    :size="17"
                    aria-hidden="true"
                    class="flow-compression-popover__icon"
                    :class="`flow-compression-popover__icon--${sourceCompressionLevel(row.id) ?? 'balanced'}`"
                  />
                  <span>{{ sourceCompressionLabel(row.id) }}</span>
                </div>
                <div class="flow-compression-popover__body">
                  {{ compressionLevelTooltip(sourceCompressionLevel(row.id) ?? 'balanced') }}
                </div>
              </div>
            </HflPopover>
            <span v-else class="hfl-empty-mark">{{ t('protection.backupDetail.durationDash') }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupsPage.flowBackupColBoundBackupPolicy')"
          :min-width="FLOW_START_BACKUP_TABLE_COL_MIN.binding"
        >
          <template #header>
            <span class="flow-filterable-header">
              <span>{{ t('protection.backupsPage.flowBackupColBoundBackupPolicy') }}</span>
              <HflPopover v-if="flowMainStep !== 2" v-model:visible="flowHeaderFilterOpen.policyBinding" trigger="click" placement="bottom" :width="210" popper-class="flow-header-filter-popper">
                <template #reference>
                  <button type="button" class="flow-header-filter-trigger" :class="{ 'flow-header-filter-trigger--active': hasFlowHeaderFilterValue('policyBinding') }" @click.stop>
                    <Filter :size="14" />
                  </button>
                </template>
                <div class="flow-header-filter-panel">
                  <ElInput v-model="flowHeaderFilterSearch.policyBinding" size="small" clearable :placeholder="t('protection.backupsPage.flowFilterSearchPlaceholder')" />
                  <ElCheckboxGroup v-model="flowFilterPolicyBinding" class="flow-header-filter-options">
                    <ElCheckbox v-for="item in visibleFlowHeaderFilterOptions(flowBindingFilterOptions, 'policyBinding')" :key="item.value" :value="item.value">
                      {{ item.text }}
                    </ElCheckbox>
                  </ElCheckboxGroup>
                  <div v-if="visibleFlowHeaderFilterOptions(flowBindingFilterOptions, 'policyBinding').length === 0" class="flow-header-filter-empty">
                    {{ t('protection.backupsPage.flowFilterNoOptions') }}
                  </div>
                  <div class="flow-header-filter-actions">
                    <ElButton text size="small" @click="clearFlowHeaderFilter('policyBinding')">{{ t('protection.backupsPage.flowFilterReset') }}</ElButton>
                    <ElButton text size="small" type="primary" @click="closeFlowHeaderFilter('policyBinding')">{{ t('protection.backupsPage.flowFilterApply') }}</ElButton>
                  </div>
                </div>
              </HflPopover>
            </span>
          </template>
          <template #default="{ row }">
            <template v-if="sourcePoliciesLabel(row.id)">
              <HflPopover placement="right-start" trigger="hover" :hide-after="FLOW_DETAIL_POPOVER_HIDE_AFTER_MS" :width="420" append-to-body popper-class="create-policy-option-popper flow-binding-detail-popper">
                <template #reference>
                  <span class="flow-binding-list hfl-table-no-tooltip">
                    <span
                      v-for="policy in sourceBoundPolicyRows(row.id)"
                      :key="policy.id"
                      class="flow-binding-list-item"
                    >
                      <span :class="flowBindingStatusDotClass(policy.isActive)" aria-hidden="true" />
                      <span class="flow-binding-list-item__name">{{ policy.name }}</span>
                    </span>
                  </span>
                </template>
                <div class="create-confirm-binding-popover-stack">
                  <div
                    v-for="policy in sourceBoundPolicyRows(row.id)"
                    :key="policy.id"
                    class="create-policy-detail-popover"
                  >
                    <div class="create-policy-detail-popover__head">
                      <div class="create-policy-detail-popover__title">{{ policy.name }}</div>
                      <span :class="flowBindingStatePillClass(policy.isActive)">
                        {{ flowBindingStateLabel(policy.isActive) }}
                      </span>
                    </div>
                    <div class="create-policy-detail-popover__sections">
                      <section
                        v-for="detailRow in policy.detailRows"
                        :key="detailRow.label"
                        class="create-policy-detail-popover__section create-policy-detail-popover__section--line"
                      >
                        <span class="create-policy-detail-popover__section-title">{{ detailRow.label }}:</span>
                        <span class="create-policy-detail-popover__value">{{ detailRow.value }}</span>
                      </section>
                      <section class="create-policy-detail-popover__section">
                        <div class="create-policy-detail-popover__section-title">
                          {{ t('protection.policiesPage.fieldRetention') }}:
                        </div>
                        <div class="create-policy-detail-popover__retention-box">
                          <div class="policy-retention-detail-list">
                            <div
                              v-for="line in policy.retentionLines"
                              :key="`${line.label || ''}${line.text}`"
                              class="policy-retention-detail-list__line"
                              :class="{ 'policy-retention-detail-list__line--summary': !line.label }"
                            >
                              <span v-if="line.label" class="policy-retention-detail-list__label">{{ line.label }}</span>
                              <span class="policy-retention-detail-list__text">{{ line.text }}</span>
                            </div>
                          </div>
                        </div>
                      </section>
                    </div>
                  </div>
                </div>
              </HflPopover>
            </template>
            <span v-else class="flow-binding-empty">
              <Unlink :size="14" class="shrink-0" stroke-width="2.2" aria-hidden="true" />
              {{ t('protection.backupsPage.flowBackupColPolicyNone') }}
            </span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupsPage.flowBackupColBoundFileFilter')"
          :min-width="FLOW_START_BACKUP_TABLE_COL_MIN.binding"
        >
          <template #header>
            <span class="flow-filterable-header">
              <span>{{ t('protection.backupsPage.flowBackupColBoundFileFilter') }}</span>
              <HflPopover v-if="flowMainStep !== 2" v-model:visible="flowHeaderFilterOpen.fileFilterBinding" trigger="click" placement="bottom" :width="210" popper-class="flow-header-filter-popper">
                <template #reference>
                  <button type="button" class="flow-header-filter-trigger" :class="{ 'flow-header-filter-trigger--active': hasFlowHeaderFilterValue('fileFilterBinding') }" @click.stop>
                    <Filter :size="14" />
                  </button>
                </template>
                <div class="flow-header-filter-panel">
                  <ElInput v-model="flowHeaderFilterSearch.fileFilterBinding" size="small" clearable :placeholder="t('protection.backupsPage.flowFilterSearchPlaceholder')" />
                  <ElCheckboxGroup v-model="flowFilterFileFilterBinding" class="flow-header-filter-options">
                    <ElCheckbox v-for="item in visibleFlowHeaderFilterOptions(flowBindingFilterOptions, 'fileFilterBinding')" :key="item.value" :value="item.value">
                      {{ item.text }}
                    </ElCheckbox>
                  </ElCheckboxGroup>
                  <div v-if="visibleFlowHeaderFilterOptions(flowBindingFilterOptions, 'fileFilterBinding').length === 0" class="flow-header-filter-empty">
                    {{ t('protection.backupsPage.flowFilterNoOptions') }}
                  </div>
                  <div class="flow-header-filter-actions">
                    <ElButton text size="small" @click="clearFlowHeaderFilter('fileFilterBinding')">{{ t('protection.backupsPage.flowFilterReset') }}</ElButton>
                    <ElButton text size="small" type="primary" @click="closeFlowHeaderFilter('fileFilterBinding')">{{ t('protection.backupsPage.flowFilterApply') }}</ElButton>
                  </div>
                </div>
              </HflPopover>
            </span>
          </template>
          <template #default="{ row }">
            <template v-if="sourceFiltersLabel(row.id)">
              <HflPopover placement="right-start" trigger="hover" :hide-after="FLOW_DETAIL_POPOVER_HIDE_AFTER_MS" :width="460" append-to-body popper-class="create-policy-option-popper flow-binding-detail-popper">
                <template #reference>
                  <span class="flow-binding-list hfl-table-no-tooltip">
                    <span
                      v-for="filter in sourceBoundFilterRows(row.id)"
                      :key="filter.id"
                      class="flow-binding-list-item"
                    >
                      <span :class="flowBindingStatusDotClass(filter.isActive)" aria-hidden="true" />
                      <span class="flow-binding-list-item__name">{{ filter.name }}</span>
                    </span>
                  </span>
                </template>
                <div class="create-confirm-binding-popover-stack">
                  <div
                    v-for="filter in sourceBoundFilterRows(row.id)"
                    :key="filter.id"
                    class="create-policy-detail-popover"
                  >
                    <div class="create-policy-detail-popover__head">
                      <div class="create-policy-detail-popover__title">{{ filter.name }}</div>
                      <span :class="flowBindingStatePillClass(filter.isActive)">
                        {{ flowBindingStateLabel(filter.isActive) }}
                      </span>
                    </div>
                    <div class="create-policy-detail-popover__sections">
                      <section
                        v-for="detailRow in filter.hoverRows"
                        :key="detailRow.label"
                        class="create-policy-detail-popover__section create-policy-detail-popover__section--filter-line"
                      >
                        <span class="create-policy-detail-popover__section-title">{{ detailRow.label }}:</span>
                        <span
                          v-if="detailRow.enabled"
                          :class="flowBindingStatePillClass(true)"
                        >
                          {{ detailRow.value }}
                        </span>
                        <span v-else class="create-policy-detail-popover__value">{{ detailRow.value }}</span>
                      </section>
                      <section class="create-policy-detail-popover__section">
                        <div class="create-policy-detail-popover__section-title">
                          {{ t('protection.policiesPage.filterRulesPreviewTitle') }}:
                        </div>
                        <div class="create-filter-rules-preview">
                          <template v-if="filter.compiledRules.length">
                            <code
                              v-for="(line, index) in filter.compiledRules"
                              :key="`${index}-${line}`"
                              class="create-filter-rules-preview__line"
                            >
                              {{ line }}
                            </code>
                          </template>
                          <p v-else class="create-filter-rules-preview__empty">
                            {{ t('protection.policiesPage.filterNoActiveRules') }}
                          </p>
                        </div>
                      </section>
                    </div>
                  </div>
                </div>
              </HflPopover>
            </template>
            <span v-else class="flow-binding-empty">
              <Unlink :size="14" class="shrink-0" stroke-width="2.2" aria-hidden="true" />
              {{ t('protection.backupsPage.flowBackupColPolicyNone') }}
            </span>
          </template>
        </el-table-column>
        <el-table-column :label="t('protection.sourceResources.colRegisteredAt')" width="184">
          <template #default="{ row }">
            <span class="hfl-table-cell-time" :class="{ 'hfl-empty-mark': !row.registeredAt }">{{ flowSourceRegisteredAt(row.registeredAt) }}</span>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :image-size="72">
            <template #description>
              <p>
                {{ t('protection.backupsPage.flowTaskEmptyStep3SourcePrefix') }}
                <strong>{{ t('protection.backupsPage.flowTaskEmptyStep3SourceStep') }}</strong>
                {{ t('protection.backupsPage.flowTaskEmptyStep3SourceSuffix') }}
              </p>
            </template>
          </el-empty>
        </template>
      </el-table>
      <div class="hfl-list-footer">
        <span v-if="step3SourceSelection.length > 0" class="hfl-list-footer__selected">
          {{ t('protection.sourceResources.selectedCount', { n: step3SourceSelection.length }) }}
        </span>
        <HflPagination
          class="hfl-list-footer__pagination"
          v-model:current-page="flowStep2Pager.page"
          v-model:page-size="flowStep2Pager.pageSize"
          :total="step3SelectableCount"
        />
      </div>
      </div>
    </div>

    </div>
      </div>
    </div>

    <el-drawer
      v-model="flowAdvancedFilterOpen"
      direction="rtl"
      :size="nestedDrawerSize"
      destroy-on-close
      class="dp-flow-filter-drawer"
    >
      <template #header>
        <div class="flex w-full min-w-0 items-center justify-between gap-3 pr-1">
          <span class="truncate text-base font-semibold text-slate-900">{{ t('protection.backupsPage.flowFilterAdvanced') }}</span>
        </div>
      </template>
      <div class="flow-filter-drawer">
        <div class="flow-filter-drawer__body scrollbar">
          <section class="flow-filter-section">
              <h3>{{ t('protection.backupsPage.flowFilterSectionSource') }}</h3>
              <ElAlert type="warning" :closable="false" show-icon class="flow-filter-nas-proxy-alert">
                {{ t('protection.backupsPage.step3NasProxyHelp') }}
              </ElAlert>
              <ElForm class="flow-filter-form">
                <ElFormItem :label="t('protection.backupsPage.flowFilterSourceType')">
                  <div class="flow-filter-control">
                    <ElSelect v-model="draftStep3SourceType" clearable>
                      <ElOption v-for="item in step3SourceTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
                    </ElSelect>
                    <ElButton class="flow-filter-control__clear" :title="t('protection.backupsPage.flowFilterReset')" :aria-label="t('protection.backupsPage.flowFilterReset')" :disabled="!draftStep3SourceType" @click="clearStep3AdvancedFilterDraft('sourceType')">
                      <BrushCleaning :size="15" />
                    </ElButton>
                  </div>
                </ElFormItem>
                <ElFormItem :label="t('protection.backupsPage.step3SearchSourceName')">
                  <div class="flow-filter-control"><ElInput v-model="draftStep3AdvancedSourceName" clearable /><ElButton class="flow-filter-control__clear" :title="t('protection.backupsPage.flowFilterReset')" :aria-label="t('protection.backupsPage.flowFilterReset')" :disabled="!draftStep3AdvancedSourceName" @click="clearStep3AdvancedFilterDraft('sourceName')"><BrushCleaning :size="15" /></ElButton></div>
                </ElFormItem>
                <ElFormItem :label="t('protection.backupsPage.step3SearchHostname')">
                  <div class="flow-filter-control"><ElInput v-model="draftStep3AdvancedHostname" clearable /><ElButton class="flow-filter-control__clear" :title="t('protection.backupsPage.flowFilterReset')" :aria-label="t('protection.backupsPage.flowFilterReset')" :disabled="!draftStep3AdvancedHostname" @click="clearStep3AdvancedFilterDraft('hostname')"><BrushCleaning :size="15" /></ElButton></div>
                </ElFormItem>
                <ElFormItem :label="t('protection.backupsPage.step3SearchIp')">
                  <div class="flow-filter-control"><ElInput v-model="draftStep3AdvancedIp" clearable /><ElButton class="flow-filter-control__clear" :title="t('protection.backupsPage.flowFilterReset')" :aria-label="t('protection.backupsPage.flowFilterReset')" :disabled="!draftStep3AdvancedIp" @click="clearStep3AdvancedFilterDraft('ip')"><BrushCleaning :size="15" /></ElButton></div>
                </ElFormItem>
                <ElFormItem :label="t('protection.backupsPage.step3SourceStatus')">
                  <div class="flow-filter-control"><ElSelect v-model="draftStep3SourceStatus" clearable><ElOption v-for="item in step3SourceStatusOptions" :key="item.value" :label="item.label" :value="item.value" /></ElSelect><ElButton class="flow-filter-control__clear" :title="t('protection.backupsPage.flowFilterReset')" :aria-label="t('protection.backupsPage.flowFilterReset')" :disabled="!draftStep3SourceStatus" @click="clearStep3AdvancedFilterDraft('sourceStatus')"><BrushCleaning :size="15" /></ElButton></div>
                </ElFormItem>
                <ElFormItem :label="t('protection.backupsPage.step3Availability')">
                  <div class="flow-filter-control"><ElSelect v-model="draftStep3Availability" clearable><ElOption v-for="item in step3AvailabilityOptions" :key="item.value" :label="item.label" :value="item.value" /></ElSelect><ElButton class="flow-filter-control__clear" :title="t('protection.backupsPage.flowFilterReset')" :aria-label="t('protection.backupsPage.flowFilterReset')" :disabled="!draftStep3Availability" @click="clearStep3AdvancedFilterDraft('availability')"><BrushCleaning :size="15" /></ElButton></div>
                </ElFormItem>
            </ElForm>
          </section>
          <template v-if="flowMainStep === 2">
            <section class="flow-filter-section flow-filter-section--divided">
              <h3>{{ t('protection.backupsPage.flowFilterSectionBackup') }}</h3>
              <ElForm class="flow-filter-form">
                <ElFormItem :label="t('protection.backupsPage.step3BackupTask')">
                  <div class="flow-filter-control">
                    <ElSelect v-model="draftStep3BackupTaskStatus" clearable>
                      <ElOption v-for="item in step3BackupTaskStatusOptions" :key="item.value" :label="item.label" :value="item.value" />
                    </ElSelect>
                    <ElButton class="flow-filter-control__clear" :title="t('protection.backupsPage.flowFilterReset')" :aria-label="t('protection.backupsPage.flowFilterReset')" :disabled="!draftStep3BackupTaskStatus" @click="clearStep3AdvancedFilterDraft('backupTaskStatus')">
                      <BrushCleaning :size="15" />
                    </ElButton>
                  </div>
                </ElFormItem>
                <ElFormItem :label="t('protection.backupsPage.step3RestoreTask')">
                  <div class="flow-filter-control">
                    <ElSelect v-model="draftStep3RestoreTaskStatus" clearable>
                      <ElOption v-for="item in step3RestoreTaskStatusOptions" :key="item.value" :label="item.label" :value="item.value" />
                    </ElSelect>
                    <ElButton class="flow-filter-control__clear" :title="t('protection.backupsPage.flowFilterReset')" :aria-label="t('protection.backupsPage.flowFilterReset')" :disabled="!draftStep3RestoreTaskStatus" @click="clearStep3AdvancedFilterDraft('restoreTaskStatus')">
                      <BrushCleaning :size="15" />
                    </ElButton>
                  </div>
                </ElFormItem>
              </ElForm>
            </section>
            <section class="flow-filter-section flow-filter-section--divided">
              <h3>{{ t('protection.backupsPage.flowFilterSectionTarget') }}</h3>
              <ElForm class="flow-filter-form">
                <ElFormItem :label="t('protection.backupsPage.flowFilterPolicyBinding')">
                  <div class="flow-filter-control">
                    <ElSelect v-model="draftStep3BackupPolicyId" clearable filterable>
                      <ElOption v-for="item in step3BackupPolicyOptions" :key="item.id" :label="item.name" :value="item.id" />
                    </ElSelect>
                    <ElButton class="flow-filter-control__clear" :title="t('protection.backupsPage.flowFilterReset')" :aria-label="t('protection.backupsPage.flowFilterReset')" :disabled="!draftStep3BackupPolicyId" @click="clearStep3AdvancedFilterDraft('backupPolicy')">
                      <BrushCleaning :size="15" />
                    </ElButton>
                  </div>
                </ElFormItem>
                <ElFormItem :label="t('protection.backupsPage.flowFilterFileFilterBinding')">
                  <div class="flow-filter-control">
                    <ElSelect v-model="draftStep3FileFilterRuleId" clearable filterable>
                      <ElOption v-for="item in step3FileFilterOptions" :key="item.id" :label="item.name" :value="item.id" />
                    </ElSelect>
                    <ElButton class="flow-filter-control__clear" :title="t('protection.backupsPage.flowFilterReset')" :aria-label="t('protection.backupsPage.flowFilterReset')" :disabled="!draftStep3FileFilterRuleId" @click="clearStep3AdvancedFilterDraft('fileFilter')">
                      <BrushCleaning :size="15" />
                    </ElButton>
                  </div>
                </ElFormItem>
                <ElFormItem :label="t('protection.backupsPage.flowFilterTargetRepo')">
                  <div class="flow-filter-control">
                    <ElSelect v-model="draftStep3RepositoryId" clearable filterable>
                      <ElOption v-for="item in step3RepositoryOptions" :key="item.id" :label="item.name" :value="item.id" />
                    </ElSelect>
                    <ElButton class="flow-filter-control__clear" :title="t('protection.backupsPage.flowFilterReset')" :aria-label="t('protection.backupsPage.flowFilterReset')" :disabled="!draftStep3RepositoryId" @click="clearStep3AdvancedFilterDraft('repository')">
                      <BrushCleaning :size="15" />
                    </ElButton>
                  </div>
                </ElFormItem>
              </ElForm>
            </section>
          </template>
        </div>
      </div>
      <template #footer>
        <div class="flow-filter-drawer__footer">
          <ElButton @click="resetStep3AdvancedFilterDrafts">
            {{ t('protection.backupsPage.flowFilterReset') }}
          </ElButton>
          <ElButton @click="cancelAdvancedFilters">
            {{ t('protection.backupsPage.flowFilterCancel') }}
          </ElButton>
          <ElButton type="primary" @click="applyAdvancedFilters">
            {{ t('protection.backupsPage.flowFilterApply') }}
          </ElButton>
        </div>
      </template>
    </el-drawer>

    <ElDialog
      v-model="resetBackupConfigDialogOpen"
      :title="t('protection.backupsPage.titleResetBackupConfig')"
      class="hfl-flow-action-dialog hfl-flow-action-dialog--delete"
      align-center
      destroy-on-close
      :close-on-click-modal="!resetBackupFromStep3Submitting"
      :close-on-press-escape="!resetBackupFromStep3Submitting"
      @closed="clearResetBackupConfigDialogState"
    >
      <BackupSourceResetDialogBody
        v-model:confirm-text="resetBackupConfigConfirmText"
        :sources="resetBackupConfigDisplayRows"
        :loading="resetBackupFromStep3Submitting"
        @confirm="confirmResetBackupConfiguration"
      />
      <template #footer>
        <ElButton
          :disabled="resetBackupFromStep3Submitting"
          @click="resetBackupConfigDialogOpen = false"
        >
          {{ t('common.cancel') }}
        </ElButton>
        <ElButton
          type="danger"
          :disabled="resetBackupConfigConfirmText !== BACKUP_SOURCE_RESET_CONFIRMATION"
          :loading="resetBackupFromStep3Submitting"
          @click="confirmResetBackupConfiguration"
        >
          {{ t('protection.backupsPage.btnConfirmReset') }}
        </ElButton>
      </template>
    </ElDialog>

    <ElDialog
      v-model="displayNameEditOpen"
      :title="t('protection.backupsPage.displayNameDialogTitle')"
      class="hfl-flow-action-dialog hfl-flow-action-dialog--form display-name-edit-dialog"
      align-center
      destroy-on-close
      :show-close="!displayNameEditSubmitting"
      :close-on-click-modal="!displayNameEditSubmitting"
      :close-on-press-escape="!displayNameEditSubmitting"
    >
      <p class="display-name-edit-dialog__lead">
        {{ t('protection.backupsPage.displayNameDialogLead') }}
      </p>
      <ElTable
        v-table-overflow-title
        :data="displayNameEditRows"
        size="small"
        class="hfl-flow-action-dialog__table hfl-table display-name-edit-dialog__table"
        max-height="360"
      >
        <ElTableColumn
          :label="t('protection.backupsPage.colBackupSource')"
          min-width="240"
        >
          <template #default="{ row }">
            <FlowSourceSummaryCell
              :row="row.source"
              :interactive="false"
            />
          </template>
        </ElTableColumn>
        <ElTableColumn
          :label="t('protection.backupsPage.displayNameColumn')"
          min-width="280"
        >
          <template #default="{ row }">
            <ElFormItem
              class="display-name-edit-dialog__field"
              :error="displayNameEditError(row)"
            >
              <ElInput
                v-model="row.name"
                :maxlength="displayNameEditMaxLength(row)"
                :placeholder="t('protection.backupsPage.displayNamePlaceholder')"
                show-word-limit
                :disabled="displayNameEditSubmitting"
                @input="onDisplayNameInput(row)"
                @keyup.enter="submitDisplayNameEdit"
              />
            </ElFormItem>
          </template>
        </ElTableColumn>
      </ElTable>
      <template #footer>
        <ElButton
          :disabled="displayNameEditSubmitting"
          @click="closeDisplayNameEditor"
        >
          {{ t('common.cancel') }}
        </ElButton>
        <ElButton
          type="primary"
          :disabled="!displayNameEditCanSubmit"
          :loading="displayNameEditSubmitting"
          @click="submitDisplayNameEdit"
        >
          {{ t('common.save') }}
        </ElButton>
      </template>
    </ElDialog>

    <FlowBackupSourceDetailDrawer
      v-if="flowSourceDetailMounted"
      ref="flowSourceDetailDrawerRef"
      v-model="flowSourceDetailOpen"
      :drawer-size="drawerSize"
      :source="activeFlowSource"
      :source-rows="flowSourceDetailRows"
      :initial-tab="flowSourceDetailTab"
      :initial-task-sub-tab="flowSourceDetailTaskSubTab"
      :initial-task-uuid="flowSourceDetailTaskUuid"
      :initial-restore-record-id="flowSourceDetailRestoreRecordId"
      :initial-restore-record-task-uuid="flowSourceDetailRestoreRecordTaskUuid"
      :scroll-to="flowSourceDetailScrollTo"
      :backup-flow-tasks="backupFlowTasks"
      :restore-flow-tasks="restoreFlowTasks"
      :backup-configs="backupConfigRows"
      :backup-config-details="backupConfigDetailById"
      :repositories="repositoryById"
      :backup-policies="backupPolicyById"
      :file-filters="fileFilterById"
      :backup-snapshots="backupSnapshotRows"
      :backup-tasks="flowSourceDetailOpen ? sourceRelatedTaskRows : EMPTY_TASK_ROWS"
      @closed="onFlowSourceDetailClosed"
      @start-backup="startBackupForSource"
      @recover="openRecoveryForSource"
      @restore-snapshot="openSnapshotRestore"
      @view-all-restore="onFlowSourceDetailViewAllRestore"
    />

    <TaskDetailDrawer
      v-model="backupTaskDetailOpen"
      :task-uuid="backupTaskDetailUuid"
      :drawer-size="drawerSize"
    />

    <el-drawer
      v-model="restoreTaskDrawerOpen"
      direction="rtl"
      :size="drawerSize"
      destroy-on-close
      class="dp-restore-task-drawer"
      @closed="onRestoreTaskDrawerClosed"
    >
      <template #header>
        <div class="flex w-full min-w-0 flex-col gap-1 pr-1">
          <span class="truncate text-base font-semibold text-slate-900">{{ t('protection.backupsPage.restoreTaskDrawerTitle') }}</span>
          <span class="truncate text-xs text-slate-500">
            {{ restoreDrawerSourceName }}
          </span>
        </div>
      </template>

      <div class="space-y-4">
        <section v-if="drawerRunningRestoreTasks.length > 0" class="restore-task-section">
          <div class="restore-task-section__head">
            <span>{{ t('protection.backupsPage.restoreTaskRunningSection') }}</span>
            <el-tag size="small" type="warning" effect="plain">
              {{ t('protection.backupsPage.restoreTaskRunningCount', { n: drawerRunningRestoreTasks.length }) }}
            </el-tag>
          </div>
          <el-table
          v-table-overflow-title
            :data="drawerRunningRestoreTasks"
            stripe
            class="hfl-list-table restore-task-drawer-table"
          >
            <el-table-column :label="t('protection.backupsPage.flowRestoreRecordColName')" min-width="180">
              <template #default="{ row }">
                <button type="button" class="hfl-table-name-link restore-task-record-link" @click.stop="openRestoreTaskDetail(row)">
                  {{ row.title }}
                </button>
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.backupsPage.flowTaskColPhase')" min-width="130">
              <template #default="{ row }">{{ restorePhaseLabel(row.phaseIndex) }}</template>
            </el-table-column>
            <el-table-column :label="t('protection.backupsPage.flowTaskColProgress')" min-width="130">
              <template #default="{ row }">
                <el-progress :percentage="row.progress" :stroke-width="7" />
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.backupsPage.flowRestoreRecordColCreated')" min-width="150">
              <template #default="{ row }">{{ fmtLocalTime(row.startedAt) }}</template>
            </el-table-column>
          </el-table>
        </section>

        <section class="restore-task-section">
          <div class="restore-task-section__head">
            <span>{{ t('protection.backupsPage.restoreTaskHistorySection') }}</span>
            <el-tag size="small" effect="plain">
              {{ t('protection.backupsPage.restoreTaskTotalCount', { n: drawerHistoryRestoreTasks.length }) }}
            </el-tag>
          </div>
          <el-table
          v-table-overflow-title
            :data="drawerPagedHistoryRestoreTasks"
            stripe
            class="hfl-list-table restore-task-drawer-table"
          >
            <el-table-column :label="t('protection.backupsPage.flowRestoreRecordColName')" min-width="180">
              <template #default="{ row }">
                <button type="button" class="hfl-table-name-link restore-task-record-link" @click.stop="openRestoreTaskDetail(row)">
                  {{ row.title }}
                </button>
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.backupsPage.flowTaskColStatus')" width="92">
              <template #default="{ row }">
                <TaskStatusTag :status="row.status" />
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.backupsPage.flowTaskColProgress')" width="120">
              <template #default="{ row }">
                <el-progress :percentage="row.progress" :status="row.status === 'failed' ? 'exception' : undefined" :stroke-width="7" />
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.backupsPage.flowRestoreRecordColCreated')" min-width="150">
              <template #default="{ row }">
                <span class="hfl-table-cell-time" :class="{ 'hfl-empty-mark': !row.startedAt }">{{ fmtLocalTime(row.startedAt) }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.backupsPage.flowRestoreRecordColFinished')" min-width="150">
              <template #default="{ row }">
                <span class="hfl-table-cell-time" :class="{ 'hfl-empty-mark': !row.endedAt }">{{ row.endedAt ? fmtLocalTime(row.endedAt) : t('protection.backupDetail.durationDash') }}</span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty :description="t('protection.backupsPage.flowTaskEmptyRestore')" :image-size="64" />
            </template>
          </el-table>
          <div class="hfl-list-footer">
            <HflPagination
              v-model:current-page="restoreTaskPager.page"
              v-model:page-size="restoreTaskPager.pageSize"
              class="hfl-list-footer__pagination"
              :page-sizes="[5, 10, 20, 50]"
              :total="drawerHistoryRestoreTasks.length"
              layout="total, sizes, prev, pager, next"
              small
            />
          </div>
        </section>
      </div>
    </el-drawer>

    </div>
    </template>

    <el-dialog
      v-model="backupConfigEditPickOpen"
      :title="t('protection.backupsPage.backupPickTitle')"
      width="420px"
      destroy-on-close
    >
      <p class="text-sm text-slate-600 mb-3">{{ t('protection.backupsPage.backupPickLead') }}</p>
      <div class="space-y-2">
        <button
          v-for="config in backupConfigEditPickCandidates"
          :key="config.id"
          type="button"
          class="w-full rounded-md border border-slate-200 px-3 py-2 text-left text-sm hover:border-primary hover:bg-slate-50 transition"
          @click="onBackupConfigEditPickSelected(config)"
        >
          <span class="font-medium text-slate-800">{{ config.name }}</span>
          <span class="block text-xs text-slate-500 mt-0.5 truncate">
            {{ 'directories' in config ? config.directories.map((dir) => dir.path).join(' / ') : t('protection.backupsPage.flowSourceDirCount', { n: config.directory_count }) }}
          </span>
        </button>
      </div>
    </el-dialog>

    <el-dialog
      v-model="backupPickOpen"
      :title="t('protection.backupsPage.backupPickTitle')"
      width="420px"
      destroy-on-close
    >
      <p class="text-sm text-slate-600 mb-3">{{ t('protection.backupsPage.backupPickLead') }}</p>
      <div class="space-y-2">
        <button
          v-for="backup in backupPickCandidates"
          :key="backup.id"
          type="button"
          class="w-full rounded-md border border-slate-200 px-3 py-2 text-left text-sm hover:border-primary hover:bg-slate-50 transition"
          @click="onBackupPickSelected(backup)"
        >
          <span class="font-medium text-slate-800">{{ backup.name }}</span>
          <span class="block text-xs text-slate-500 mt-0.5 truncate" :class="{ 'hfl-empty-mark': !backup.sources[0]?.path }">{{ backup.sources[0]?.path || '—' }}</span>
        </button>
      </div>
    </el-dialog>

    <Modal v-if="editOpen" :open="editOpen" :title="t('protection.backupsPage.modalEditTitle')" variant="page" @close="editOpen = false">
      <ElForm v-if="editRow" label-width="88px">
        <ElFormItem :label="t('protection.backupsPage.labelBackupName')">
          <ElInput v-model="editName" />
        </ElFormItem>
        <ElFormItem :label="t('protection.backupsPage.labelRemark')">
          <ElInput v-model="editRemark" type="textarea" :rows="2" />
        </ElFormItem>
        <ElFormItem :label="t('protection.backupsPage.descPolicy')">
          <el-select v-model="editPolicy" class="w-full" clearable :placeholder="t('protection.backupsPage.phNoPolicy')">
            <el-option :label="t('protection.backupsPage.optNoPolicy')" value="" />
            <el-option v-for="pol in store.policies" :key="pol.id" :label="pol.name" :value="pol.id" />
          </el-select>
        </ElFormItem>
        <ElFormItem :label="t('protection.backupsPage.descFileFilter')">
          <el-select v-model="editGlobalFilter" class="w-full" clearable :placeholder="t('protection.backupsPage.phNoGlobalFilter')">
            <el-option :label="t('protection.backupsPage.optNoGlobalFilter')" value="" />
            <el-option v-for="gf in store.globalFilters" :key="gf.id" :label="gf.name" :value="gf.id" />
          </el-select>
        </ElFormItem>
      </ElForm>
      <div class="mt-4 flex justify-end gap-2">
        <ElButton @click="editOpen = false">{{ t('protection.backupsPage.btnCancel') }}</ElButton>
        <ElButton type="primary" @click="saveEdit">{{ t('protection.backupsPage.btnSave') }}</ElButton>
      </div>
    </Modal>

    <Teleport to="body">
      <div
        v-if="isFixedSnapshotRestore && !recOpen"
        class="fullscreen-form-fullscreen fullscreen-form-animated create-backup-fullscreen create-restore-fullscreen"
        role="dialog"
        aria-modal="true"
      >
        <div class="fullscreen-form-page create-backup-page">
          <div class="fullscreen-form-header">
            <button type="button" class="fullscreen-form-header__back" @click="closeRecoveryWizard">
              <ArrowLeft class="fullscreen-form-header__back-icon" :size="18" />
            </button>
            <div class="fullscreen-form-header__content">
              <h1 class="fullscreen-form-header__title">{{ t('protection.backupsPage.snapshotRestoreTitle') }}</h1>
            </div>
          </div>
          <main class="fixed-restore-route-state">
            <el-skeleton v-if="fixedRestoreInitializing" :rows="6" animated />
            <el-result
              v-else
              icon="error"
              :title="t('protection.backupsPage.snapshotRestoreUnavailableTitle')"
              :sub-title="fixedRestoreInitError"
            >
              <template #extra>
                <ElButton type="primary" @click="closeRecoveryWizard">
                  {{ t('protection.backupsPage.snapshotRestoreBack') }}
                </ElButton>
              </template>
            </el-result>
          </main>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div
        v-if="recOpen"
        class="fullscreen-form-fullscreen fullscreen-form-animated create-backup-fullscreen create-restore-fullscreen"
        :class="{ 'create-restore-fullscreen--manual': recEntryMode === 'manual' }"
        role="dialog"
        aria-modal="true"
      >
        <div class="fullscreen-form-page create-backup-page">
          <div class="fullscreen-form-header">
            <button type="button" class="fullscreen-form-header__back" @click="closeRecoveryWizard">
              <ArrowLeft class="fullscreen-form-header__back-icon" :size="18" />
            </button>
            <div class="fullscreen-form-header__content">
              <h1 class="fullscreen-form-header__title">
                {{ isFixedSnapshotRestore
                  ? `${t('protection.backupsPage.snapshotRestoreTitle')} · ${fixedRestoreSnapshot?.snapshot_uid || `#${fixedRestoreSnapshot?.id}`}`
                  : t('protection.backupsPage.modalCreateRestoreTitle') }}
              </h1>
              <p v-if="recEntryStage === 'chooser' && !isFixedSnapshotRestore" class="fullscreen-form-header__desc">
                {{ t('protection.backupsPage.recoveryEntryLead') }}
              </p>
            </div>
          </div>

          <div class="fullscreen-form-layout create-backup-layout create-backup-layout--steps">
            <WizardSteps
              v-if="recEntryStage === 'wizard'"
              as="aside"
              class="create-backup-steps"
              :steps="recWizardStepItems"
              :current-step="recStep"
              :is-done="isRecStepDone"
              :clickable="false"
              :aria-label="t('protection.backupsPage.createRestoreWizardAria')"
            />

            <main class="fullscreen-form-main create-backup-main">
              <div class="create-backup-step-body dp-process-page dp-restore-wizard-body">
        <template v-if="recEntryStage === 'chooser'">
          <div class="recovery-entry-panel space-y-4">
            <ElRadioGroup v-if="!isFixedSnapshotRestore" v-model="recEntryMode" class="recovery-entry-options" @change="onRecoveryEntryModeChange">
              <ElRadio value="plan" border class="recovery-entry-card !mr-0">
                <span class="recovery-entry-card__icon recovery-entry-card__icon--plan" aria-hidden="true">
                  <ArchiveRestore :size="20" />
                </span>
                <div class="recovery-entry-card__body">
                  <div class="recovery-entry-card__title">{{ t('protection.backupsPage.recoveryEntryPlanTitle') }}</div>
                  <p class="recovery-entry-card__desc">{{ t('protection.backupsPage.recoveryEntryPlanDesc') }}</p>
                </div>
              </ElRadio>
              <ElRadio value="manual" border class="recovery-entry-card !mr-0">
                <span class="recovery-entry-card__icon recovery-entry-card__icon--manual" aria-hidden="true">
                  <CirclePlus :size="20" />
                </span>
                <div class="recovery-entry-card__body">
                  <div class="recovery-entry-card__title">{{ t('protection.backupsPage.recoveryEntryManualTitle') }}</div>
                  <p class="recovery-entry-card__desc">{{ t('protection.backupsPage.recoveryEntryManualDesc') }}</p>
                </div>
              </ElRadio>
            </ElRadioGroup>

            <div
              v-if="!isFixedSnapshotRestore && recEntryMode === 'plan' && recoveryPlanTableRows.length"
              class="rounded-[var(--radius-card)] border border-[var(--color-border,#e2e8f0)] bg-[var(--color-card-bg,#fff)] p-4"
            >
              <p class="m-0 mb-3 text-xs text-slate-500">{{ t('protection.backupsPage.recoveryPlanPreviewHint') }}</p>
              <el-table
          v-table-overflow-title
                :data="recoveryPlanTableRows"
                :header-cell-style="TABLE_HEADER_STYLE"
                stripe
                class="recovery-plan-preview-table"
                row-key="rowKey"
              >
                <el-table-column
                  :label="t('protection.backupsPage.colBackupSourceAndSnapshot')"
                  min-width="220"
                >
                  <template #default="{ row }">
                    <div class="recovery-plan-preview-table__source-cell">
                      <span
                        class="recovery-plan-preview-table__source"
                      >
                        {{ row.sourceResourceName }}
                      </span>
                      <span
                        class="recovery-plan-preview-table__endpoint-ip"
                      >
                        {{ row.sourceHostIp }}
                      </span>
                      <span class="recovery-plan-preview-table__source-snapshot text-xs text-slate-500">
                        {{ t('protection.backupsPage.descSnapshot') }}: {{ fmtLocalTime(row.snapshotTime) }}
                      </span>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('protection.backupsPage.descRecoveryPlan')"
                  min-width="520"
                >
                  <template #default="{ row }">
                    <HflPopover
                      v-if="row.state === 'configured' && row.plan"
                      trigger="hover"
                      placement="top-start"
                      :width="640"
                      popper-class="create-recovery-plan-tooltip create-recovery-plan-tooltip--wide"
                    >
                      <template #reference>
                        <div
                          class="create-recovery-plan-cell create-recovery-plan-cell--review"
                          :class="`create-recovery-plan-cell--${recoveryPlanRunStatusTone(row.plan)}`"
                        >
                          <div class="create-recovery-plan-cell__status">
                            <span class="create-recovery-plan-cell__dot" aria-hidden="true" />
                            <span class="create-recovery-plan-cell__status-label">
                              {{ recoveryPlanRunStatusLabel(row.plan) }}
                            </span>
                          </div>
                          <div
                            class="create-recovery-plan-cell__policy"
                            :class="`create-recovery-plan-cell__policy--${row.plan.conflictPolicy}`"
                          >
                            <ShieldAlert
                              v-if="row.plan.conflictPolicy === 'overwrite'"
                              :size="14"
                              class="create-recovery-plan-cell__policy-icon"
                            />
                            <ShieldCheck v-else :size="14" class="create-recovery-plan-cell__policy-icon" />
                            <span class="create-recovery-plan-cell__policy-text">
                              {{ recoveryPlanRunConflictSummary(row.plan) }}
                            </span>
                          </div>
                          <div class="create-recovery-plan-cell__mappings">
                            <div
                              v-for="mapping in row.plan.mappings"
                              :key="mapping.id"
                              class="create-recovery-plan-mapping"
                            >
                              <span
                                class="create-recovery-plan-mapping__endpoint"
                                :class="`create-recovery-plan-mapping__endpoint--${recoveryPlanRunSourceKind(mapping.sourceDir)}`"
                                :title="recoveryPlanRunSourcePathLabel(mapping.sourceDir)"
                              >
                                <component :is="recoveryPlanRunSourceIcon(mapping.sourceDir)" :size="14" class="create-recovery-plan-mapping__icon" />
                                <span class="create-recovery-plan-mapping__text" :title="recoveryPlanRunSourcePathLabel(mapping.sourceDir)">{{ recoveryPlanRunSourcePathLabel(mapping.sourceDir) }}</span>
                              </span>
                              <span class="create-recovery-plan-mapping__arrow" aria-hidden="true">-&gt;</span>
                              <span
                                class="create-recovery-plan-mapping__endpoint create-recovery-plan-mapping__endpoint--target"
                                :title="recoveryPlanMappingTargetSummary(mapping)"
                              >
                                <FolderOpen :size="14" class="create-recovery-plan-mapping__icon" />
                                <span class="create-recovery-plan-mapping__text" :title="recoveryPlanMappingTargetSummary(mapping)">{{ recoveryPlanMappingTargetSummary(mapping) }}</span>
                              </span>
                            </div>
                          </div>
                        </div>
                      </template>
                      <template #default>
                        <div class="create-recovery-plan-tooltip__content">
                          <div
                            class="create-recovery-plan-tooltip__policy"
                            :class="`create-recovery-plan-tooltip__policy--${row.plan.conflictPolicy}`"
                          >
                            <ShieldAlert
                              v-if="row.plan.conflictPolicy === 'overwrite'"
                              :size="14"
                              class="create-recovery-plan-cell__policy-icon"
                            />
                            <ShieldCheck v-else :size="14" class="create-recovery-plan-cell__policy-icon" />
                            <span class="create-recovery-plan-cell__policy-text">
                              {{ recoveryPlanRunConflictSummary(row.plan) }}
                            </span>
                          </div>
                          <div
                            v-for="mapping in row.plan.mappings"
                            :key="`${mapping.id}-tooltip`"
                            class="create-recovery-plan-tooltip__mapping create-recovery-plan-mapping"
                          >
                            <span
                              class="create-recovery-plan-mapping__endpoint"
                              :class="`create-recovery-plan-mapping__endpoint--${recoveryPlanRunSourceKind(mapping.sourceDir)}`"
                              :title="recoveryPlanRunSourcePathLabel(mapping.sourceDir)"
                            >
                              <component :is="recoveryPlanRunSourceIcon(mapping.sourceDir)" :size="14" class="create-recovery-plan-mapping__icon" />
                              <span class="create-recovery-plan-mapping__text" :title="recoveryPlanRunSourcePathLabel(mapping.sourceDir)">{{ recoveryPlanRunSourcePathLabel(mapping.sourceDir) }}</span>
                            </span>
                            <span class="create-recovery-plan-mapping__arrow" aria-hidden="true">-&gt;</span>
                            <span
                              class="create-recovery-plan-mapping__endpoint create-recovery-plan-mapping__endpoint--target"
                              :title="recoveryPlanMappingTargetSummary(mapping)"
                            >
                              <FolderOpen :size="14" class="create-recovery-plan-mapping__icon" />
                              <span class="create-recovery-plan-mapping__text" :title="recoveryPlanMappingTargetSummary(mapping)">{{ recoveryPlanMappingTargetSummary(mapping) }}</span>
                            </span>
                          </div>
                        </div>
                      </template>
                    </HflPopover>
                    <div
                      v-else
                      class="create-recovery-plan-cell create-recovery-plan-cell--review create-recovery-plan-cell--pending recovery-plan-missing-cell"
                    >
                      <div class="create-recovery-plan-cell__status">
                        <span class="create-recovery-plan-cell__dot" aria-hidden="true" />
                        <span class="create-recovery-plan-cell__status-label">
                          {{ t('protection.backupsPage.recoveryPlanMissingTitle') }}
                        </span>
                      </div>
                      <div class="create-recovery-plan-cell__meta">
                        {{ t('protection.backupsPage.recoveryPlanMissingDesc') }}
                      </div>
                      <ElButton
                        text
                        type="primary"
                        size="small"
                        class="hfl-btn-with-icon recovery-plan-missing-cell__action"
                        @click="openConfigureRestorePlanFromMissingRow(row)"
                      >
                        <Route :size="14" class="shrink-0" />
                        <span>{{ t('protection.backupsPage.flowActionConfigureRecoveryPlan') }}</span>
                      </ElButton>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('protection.backupsPage.descSnapshot')"
                  min-width="300"
                >
                  <template #default="{ row }">
                    <el-select
                      v-if="row.state === 'configured' && row.plan"
                      :model-value="recoveryPlanSelectedSnapshotId(row.plan)"
                      :placeholder="t('protection.backupsPage.phPickSnapshot')"
                      class="w-full recovery-plan-preview-table__snapshot-select"
                      :loading="recoverySnapshotListStateForHost(backupSourceHostId(row.plan.backupId)).loading"
                      @visible-change="(visible) => onRecoveryPlanSnapshotVisible(row.plan, visible)"
                      @update:model-value="(value) => updateRecoveryPlanSnapshot(row.plan, value)"
                    >
                      <el-option
                        v-for="s in recoverySnapshotOptionsForSource(backupSourceHostId(row.plan.backupId))"
                        :key="s.id"
                        :label="`${fmtLocalTime(s.time)} · ${fmtBytes(s.sizeBytes)}`"
                        :value="s.id"
                        :disabled="!restorePlanSnapshotCompatibility(row.plan, s).compatible"
                      >
                        <ElTooltip
                          :disabled="restorePlanSnapshotCompatibility(row.plan, s).compatible"
                          :content="restorePlanSnapshotCompatibility(row.plan, s).reason"
                          placement="bottom-start"
                          :show-after="300"
                        >
                          <div class="recovery-snapshot-option" :class="{ 'is-disabled': !restorePlanSnapshotCompatibility(row.plan, s).compatible }">
                            <span class="recovery-snapshot-option__label">
                              {{ fmtLocalTime(s.time) }} · {{ fmtBytes(s.sizeBytes) }}
                            </span>
                            <el-tag
                              size="small"
                              :type="lifecycleStatusTagAttrs(s.status).type"
                              :class="lifecycleStatusTagAttrs(s.status).class"
                              effect="plain"
                            >
                              {{ snapshotStatusLabel(s.status) }}
                            </el-tag>
                            <Info
                              v-if="!restorePlanSnapshotCompatibility(row.plan, s).compatible"
                              :size="14"
                              class="recovery-snapshot-option__info"
                            />
                          </div>
                        </ElTooltip>
                      </el-option>
                      <el-option
                        v-if="recoverySnapshotHasMore(backupSourceHostId(row.plan.backupId))"
                        :key="`${row.rowKey}-snapshot-load-more`"
                        :value="RECOVERY_SNAPSHOT_LOAD_MORE_VALUE"
                        :label="recoverySnapshotLoadMoreLabel(backupSourceHostId(row.plan.backupId))"
                      >
                        <button
                          type="button"
                          class="recovery-snapshot-load-more"
                          @mousedown.prevent
                          @click.stop="updateRecoveryPlanSnapshot(row.plan, RECOVERY_SNAPSHOT_LOAD_MORE_VALUE)"
                        >
                          {{ recoverySnapshotLoadMoreLabel(backupSourceHostId(row.plan.backupId)) }}
                        </button>
                      </el-option>
                    </el-select>
                    <span v-else class="hfl-empty-mark">—</span>
                  </template>
                </el-table-column>
              </el-table>
            </div>
            <div
              v-else-if="recEntryMode === 'plan'"
              class="recovery-plan-empty-state"
            >
              {{ t('protection.backupsPage.msgRecoveryPlanUnavailable') }}
            </div>

            <div v-if="recEntryMode === 'manual'" class="create-backup-layout create-backup-layout--steps recovery-manual-inline-layout">
              <WizardSteps
                as="aside"
                class="create-backup-steps recovery-manual-inline-layout__steps"
                :steps="recWizardStepItems"
                :current-step="recStep"
                :is-done="isRecStepDone"
                :clickable="false"
                :aria-label="t('protection.backupsPage.createRestoreWizardAria')"
              />
              <div class="fullscreen-form-main create-backup-main recovery-manual-inline-layout__main">
                <div class="create-backup-step-body recovery-manual-inline-layout__content">

        <div v-show="recStep === 0" class="dp-wizard-pane">
          <p class="text-sm text-slate-500 mb-3">{{ t('protection.backupsPage.recStepBackupAndSnapshotLead') }}</p>
          <div class="recovery-manual-table-shell">
            <el-table
          v-table-overflow-title
              :data="recBackupSnapshotHostRows"
              :header-cell-style="TABLE_HEADER_STYLE"
              max-height="calc(var(--app-viewport-height) - 390px)"
              stripe
              class="recovery-plan-preview-table recovery-manual-table"
              row-key="rowKey"
            >
              <el-table-column
                :label="t('protection.backupsPage.colBackupSource')"
                min-width="240"
              >
                <template #default="{ row }">
                  <div class="recovery-source-summary">
                    <div class="recovery-source-summary__head">
                      <span class="recovery-source-summary__name">
                        {{ row.sourceSummary.displayName }}
                      </span>
                      <el-tag
                        size="small"
                        effect="plain"
                        class="recovery-source-summary__type"
                        :type="row.sourceSummary.typeTagType"
                      >
                        {{ row.sourceSummary.typeLabel }}
                      </el-tag>
                    </div>
                    <div class="recovery-source-summary__meta">
                      <span class="recovery-source-summary__ip">
                        {{ row.sourceSummary.ipLine }}
                      </span>
                      <span class="recovery-source-summary__platform">
                        <AgentPlatformBrandIcon
                          v-if="row.sourceSummary.platform"
                          :os="row.sourceSummary.platform"
                        />
                        <component
                          :is="row.sourceSummary.sourceType === 'nas' ? sourceNasIcon : sourceHostIcon"
                          v-else
                          :size="16"
                        />
                      </span>
                    </div>
                  </div>
                </template>
              </el-table-column>
              <el-table-column
                :label="t('protection.backupsPage.descSnapshot')"
                min-width="280"
              >
                <template #default="{ row }">
                  <el-select
                    :model-value="recGroupSnapshotId(row.hostId)"
                    :placeholder="t('protection.backupsPage.phPickSnapshot')"
                    class="w-full recovery-plan-preview-table__snapshot-select"
                    :loading="recoverySnapshotListStateForHost(row.hostId).loading"
                    @visible-change="(visible) => onRecoverySnapshotSelectVisible(row.hostId, visible)"
                    @update:model-value="(value) => updateRecoverySnapshotSelectValue(row.hostId, value)"
                  >
                    <el-option
                      v-for="s in recoverySnapshotOptionsForSource(row.hostId)"
                      :key="s.id"
                      :label="`${fmtLocalTime(s.time)} · ${fmtBytes(s.sizeBytes)}`"
                      :value="s.id"
                    >
                      <div class="recovery-snapshot-option">
                        <span class="recovery-snapshot-option__label">
                          {{ fmtLocalTime(s.time) }} · {{ fmtBytes(s.sizeBytes) }}
                        </span>
                        <el-tag
                          size="small"
                          :type="lifecycleStatusTagAttrs(s.status).type"
                          :class="lifecycleStatusTagAttrs(s.status).class"
                          effect="plain"
                        >
                          {{ snapshotStatusLabel(s.status) }}
                        </el-tag>
                      </div>
                    </el-option>
                    <el-option
                      v-if="recoverySnapshotHasMore(row.hostId)"
                      :key="`${row.rowKey}-snapshot-load-more`"
                      :value="RECOVERY_SNAPSHOT_LOAD_MORE_VALUE"
                      :label="recoverySnapshotLoadMoreLabel(row.hostId)"
                    >
                      <button
                        type="button"
                        class="recovery-snapshot-load-more"
                        @mousedown.prevent
                        @click.stop="updateRecoverySnapshotSelectValue(row.hostId, RECOVERY_SNAPSHOT_LOAD_MORE_VALUE)"
                      >
                        {{ recoverySnapshotLoadMoreLabel(row.hostId) }}
                      </button>
                    </el-option>
                  </el-select>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>

        <div v-show="recStep === 1" class="dp-wizard-pane">
          <p class="text-sm text-slate-500 mb-3">{{ t('protection.backupsPage.recStep3Lead') }}</p>
          <div class="recovery-manual-table-shell">
            <el-table
          v-table-overflow-title
              :data="recRecoveryDestSourceRows"
              row-key="hostId"
              max-height="calc(var(--app-viewport-height) - 390px)"
              stripe
              :header-cell-style="TABLE_HEADER_STYLE"
              class="hfl-list-table recovery-manual-table"
            >
              <el-table-column :label="t('protection.backupsPage.colBackupSourceSnapshot')" min-width="260">
                <template #default="{ row }">
                  <div class="recovery-source-summary recovery-source-summary--with-snapshot">
                    <div class="recovery-source-summary__head">
                      <span class="recovery-source-summary__name">
                        {{ row.sourceSummary.displayName }}
                      </span>
                      <el-tag
                        size="small"
                        effect="plain"
                        class="recovery-source-summary__type"
                        :type="row.sourceSummary.typeTagType"
                      >
                        {{ row.sourceSummary.typeLabel }}
                      </el-tag>
                    </div>
                    <div class="recovery-source-summary__meta">
                      <span class="recovery-source-summary__ip">
                        {{ row.sourceSummary.ipLine }}
                      </span>
                      <span class="recovery-source-summary__platform">
                        <AgentPlatformBrandIcon
                          v-if="row.sourceSummary.platform"
                          :os="row.sourceSummary.platform"
                        />
                        <component
                          :is="row.sourceSummary.sourceType === 'nas' ? sourceNasIcon : sourceHostIcon"
                          v-else
                          :size="16"
                        />
                      </span>
                    </div>
                    <div class="recovery-source-summary__snapshot">
                      Snapshot: {{ row.snapshotLine }}
                    </div>
                  </div>
                </template>
              </el-table-column>
              <el-table-column :label="t('protection.backupsPage.recStepDest')" min-width="360">
                <template #default="{ row }">
                  <div class="recovery-target-host-control">
                    <ElTooltip
                      :disabled="Boolean(recoverySourceHostButtonOption(row.hostId))"
                      :content="t('protection.backupsPage.recoverySourceHostUnavailable')"
                      placement="top"
                    >
                      <span class="recovery-target-host-control__source-wrap">
                        <ElButton
                          class="hfl-btn-with-icon recovery-target-host-control__source"
                          :class="{ 'is-selected': isRecoverySourceHostSelected(row.hostId) }"
                          :disabled="!recoverySourceHostButtonOption(row.hostId)"
                          @click="toggleRecoverySourceTargetForSource(row.hostId)"
                        >
                          <Check v-if="isRecoverySourceHostSelected(row.hostId)" :size="14" />
                          <component
                            :is="row.sourceSummary.sourceType === 'nas' ? sourceNasIcon : sourceHostIcon"
                            v-else
                            :size="14"
                          />
                          <span>{{ t('protection.backupsPage.recoveryUseSourceHost') }}</span>
                        </ElButton>
                      </span>
                    </ElTooltip>
                    <div
                      v-if="isRecoverySourceHostSelected(row.hostId)"
                      class="recovery-target-host-control__readonly"
                    >
                      <div class="recovery-source-summary">
                        <div class="recovery-source-summary__head">
                          <span class="recovery-source-summary__name">
                            {{ row.sourceSummary.displayName }}
                          </span>
                          <el-tag
                            size="small"
                            effect="plain"
                            class="recovery-source-summary__type"
                            :type="row.sourceSummary.typeTagType"
                          >
                            {{ row.sourceSummary.typeLabel }}
                          </el-tag>
                        </div>
                        <div class="recovery-source-summary__meta">
                          <span class="recovery-source-summary__ip">
                            {{ row.sourceSummary.ipLine }}
                          </span>
                          <span class="recovery-source-summary__platform">
                            <AgentPlatformBrandIcon
                              v-if="row.sourceSummary.platform"
                              :os="row.sourceSummary.platform"
                            />
                            <component
                              :is="row.sourceSummary.sourceType === 'nas' ? sourceNasIcon : sourceHostIcon"
                              v-else
                              :size="16"
                            />
                          </span>
                        </div>
                      </div>
                    </div>
                    <el-select
                      v-else
                      :model-value="recoveryTargetNodeSelectModelForSource(row.hostId)"
                      :placeholder="t('protection.backupsPage.phPickOtherRecoveryHost')"
                      class="recovery-target-host-control__select"
                      filterable
                      remote
                      reserve-keyword
                      clearable
                      :remote-method="searchRecoveryTargetHostOptions"
                      :loading="recoveryTargetHostLoading || recoveryTargetHostLoadingMore"
                      popper-class="recovery-target-node-select-popper"
                      @visible-change="onRecoveryTargetNodeSelectVisible"
                      @popup-scroll="onRecoveryTargetNodePopupScroll"
                      @update:model-value="(value) => setRecoveryTargetNodeForSource(row.hostId, value)"
                    >
                      <el-option
                        v-for="option in recoveryTargetNodeOptionsForSource(row.hostId)"
                        :key="option.value"
                        :label="option.label"
                        :value="option.value"
                        :disabled="!option.selectable"
                      >
                        <div class="recovery-target-node-option">
                          <div class="recovery-target-node-option__main">
                            <span class="recovery-target-node-option__label" :title="option.sourceSummary.displayName">
                              {{ option.sourceSummary.displayName }}
                            </span>
                            <el-tag
                              size="small"
                              effect="plain"
                              class="recovery-source-summary__type"
                              :type="option.sourceSummary.typeTagType"
                            >
                              {{ option.sourceSummary.typeLabel }}
                            </el-tag>
                          </div>
                          <div class="recovery-source-summary__meta">
                            <span class="recovery-target-node-option__meta" :title="option.sourceSummary.ipLine">
                              {{ option.sourceSummary.ipLine }}
                            </span>
                            <span class="recovery-source-summary__platform">
                              <AgentPlatformBrandIcon
                                v-if="option.sourceSummary.platform"
                                :os="option.sourceSummary.platform"
                              />
                              <component
                                :is="option.sourceSummary.sourceType === 'nas' ? sourceNasIcon : sourceHostIcon"
                                v-else
                                :size="16"
                              />
                            </span>
                          </div>
                        </div>
                      </el-option>
                      <el-option
                        v-if="recoveryTargetHostLoadingMore"
                        key="recovery-target-loading-more"
                        disabled
                        value="__loading_more__"
                        :label="t('common.loading')"
                      >
                        <span class="recovery-target-node-option__loading">{{ t('common.loading') }}</span>
                      </el-option>
                      <el-option
                        v-else-if="recoveryTargetHostError"
                        key="recovery-target-load-more-error"
                        disabled
                        value="__load_more_error__"
                        :label="t('protection.backupsPage.recoveryTargetLoadFailed')"
                      >
                        <ElButton link type="primary" @click.stop="restoreTargetCatalog.retry">
                          {{ t('common.retry') }}
                        </ElButton>
                      </el-option>
                      <template #empty>
                        <div class="py-3 text-center text-xs text-slate-500">
                          <span>{{ recoveryTargetHostError ? t('protection.backupsPage.recoveryTargetLoadFailed') : t('protection.backupsPage.recoveryTargetEmpty') }}</span>
                          <ElButton v-if="recoveryTargetHostError" link type="primary" @click.stop="restoreTargetCatalog.retry">
                            {{ t('common.retry') }}
                          </ElButton>
                        </div>
                      </template>
                    </el-select>
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>

        <div v-show="recStep === 2" class="dp-wizard-pane">
          <p class="text-sm text-slate-500 mb-3">{{ t('protection.backupsPage.recStep2Lead') }}</p>
          <div class="recovery-manual-table-shell">
            <el-table
          v-table-overflow-title
              ref="recRecoveryDirSourceTableRef"
              :data="recRecoveryDirSourceRows"
              row-key="hostId"
              max-height="calc(var(--app-viewport-height) - 390px)"
              stripe
              :expand-row-keys="recExpandedRecDirHostIds"
              :header-cell-style="TABLE_HEADER_STYLE"
              class="hfl-list-table recovery-dest-config-table recovery-dir-config-table recovery-manual-table"
              @expand-change="onRecRecoveryDirExpandChange"
            >
              <el-table-column type="expand" width="35" class-name="recovery-dest-config-expand-column">
                <template #default="{ row: sourceRow }">
                  <div class="recovery-dest-config-detail">
                    <div class="create-recovery-dir-plan-list recovery-dir-selection-list">
                      <div class="create-recovery-dir-plan-labels recovery-dir-selection-labels" aria-hidden="true">
                        <span class="create-recovery-required-label">
                          {{ t('protection.backupsPage.colBackupSnapshotDirectory') }}
                          <span class="create-recovery-required-mark">*</span>
                          <HflHelpTip
                            :content="t('protection.backupsPage.createRecoveryPathInputHint')"
                            :size="13"
                            :aria-label="t('protection.backupsPage.colBackupSnapshotDirectory')"
                          />
                        </span>
                        <span class="create-recovery-required-label">
                          {{ t('protection.backupsPage.colRecoveryTargetDirectory') }}
                          <span class="create-recovery-required-mark">*</span>
                          <HflHelpTip
                            :content="t('protection.backupsPage.createRecoveryPathInputHint')"
                            :size="13"
                            :aria-label="t('protection.backupsPage.colRecoveryTargetDirectory')"
                          />
                        </span>
                        <span>{{ t('protection.backupsPage.colActions') }}</span>
                      </div>
                      <div
                        v-for="entry in sourceRow.entries"
                        :key="entry.id"
                        class="create-recovery-dir-plan-row recovery-dir-selection-row"
                      >
                        <div class="create-recovery-dir-plan-field" :data-label="t('protection.backupsPage.colBackupSnapshotDirectory')">
                          <HflPopover
                            :visible="isRecoverySnapshotRangePickerVisible(sourceRow.hostId, entry)"
                            trigger="manual"
                            placement="bottom-start"
                            :width="460"
                            popper-class="create-recovery-path-popover"
                            @update:visible="(visible) => setRecoverySnapshotRangePickerVisible(sourceRow.hostId, entry, Boolean(visible))"
                          >
                            <template #reference>
                              <div class="create-recovery-path-input">
                                <el-input
                                  :model-value="recoverySnapshotRangeDisplayForEntry(sourceRow.hostId, entry)"
                                  clearable
                                  :placeholder="t('protection.backupsPage.phSelectOrEnterRestoreScope')"
                                  :class="{
                                    'create-recovery-path-input--pending': entry.sourcePathValidation === 'pending',
                                    'create-recovery-path-input--invalid': isRecoverySnapshotRangeInputInvalid(entry),
                                  }"
                                  :aria-describedby="isRecoverySnapshotRangeErrorVisible(sourceRow.hostId, entry) ? `recovery-snapshot-range-error-${sourceRow.hostId}-${entry.id}` : undefined"
                                  @click.stop
                                  @update:model-value="(value) => updateRecoverySnapshotRangeInput(sourceRow.hostId, entry, String(value))"
                                  @blur="entry.sourcePathValidation === 'pending' && validateRecoverySnapshotRangeInput(sourceRow.hostId, entry)"
                                  @keydown.enter.prevent="validateRecoverySnapshotRangeInput(sourceRow.hostId, entry)"
                                >
                                  <template v-if="isRecoverySnapshotRangeValidating(sourceRow.hostId, entry)" #suffix>
                                    <span class="create-recovery-path-input__checking">{{ t('common.loading') }}</span>
                                  </template>
                                  <template #prefix>
                                    <component
                                      :is="recoverySnapshotRangeInputIcon(entry)"
                                      :size="14"
                                      class="create-recovery-path-input__type-icon"
                                      :class="recoverySnapshotRangeInputIconClass(entry)"
                                    />
                                  </template>
                                  <template #append>
                                    <ElButton
                                      class="create-recovery-path-input__btn"
                                      :aria-label="t('protection.backupsPage.btnBrowsePaths')"
                                      @click.stop="setRecoverySnapshotRangePickerVisible(sourceRow.hostId, entry, true)"
                                    >
                                      <FolderOpen :size="16" />
                                    </ElButton>
                                  </template>
                                </el-input>
                                <p
                                  v-if="isRecoverySnapshotRangeErrorVisible(sourceRow.hostId, entry)"
                                  :id="`recovery-snapshot-range-error-${sourceRow.hostId}-${entry.id}`"
                                  class="create-recovery-path-input__error"
                                  role="alert"
                                >
                                  <span>{{ recoverySnapshotRangeInputError(entry) }}</span>
                                  <button
                                    type="button"
                                    class="create-recovery-path-input__error-close"
                                    :aria-label="t('protection.backupsPage.btnClose')"
                                    @click.stop="dismissRecoveryPathError(sourceRow.hostId, entry, 'source')"
                                  >
                                    <X :size="13" />
                                  </button>
                                </p>
                              </div>
                            </template>
                            <div class="create-recovery-tree-popover hfl-dir-tree-shell">
                              <el-tree
                                :ref="(el) => setRecoveryDirectoryTreeRef(recoveryDirectoryTreeKey(sourceRow.hostId, entry.id, 'snapshot'), el)"
                                :key="`restore-snapshot-picker-${sourceRow.hostId}-${entry.id}-${selectedSnapshotNumericIdForSource(sourceRow.hostId)}-${recoverySnapshotBrowseRevision}`"
                                class="source-dir-tree create-recovery-popover-tree hfl-dir-tree hfl-dir-tree--tall"
                                node-key="id"
                                lazy
                                highlight-current
                                :load="(node, resolve) => loadRecoverySnapshotPickerNode(sourceRow.hostId, node, resolve)"
                                :props="{ label: 'label', children: 'children', isLeaf: 'isLeaf' }"
                                @node-click="(data) => pickRecoverySnapshotRange(sourceRow.hostId, entry, data)"
                                @node-collapse="(data) => onRecoveryDirectoryExpansionChange(recoveryDirectoryTreeKey(sourceRow.hostId, entry.id, 'snapshot'), data.id)"
                                @node-expand="(data) => onRecoveryDirectoryExpansionChange(recoveryDirectoryTreeKey(sourceRow.hostId, entry.id, 'snapshot'), data.id)"
                              >
                                <template #default="{ data }">
                                  <div class="create-tree-node-content hfl-dir-tree-node">
                                    <Camera
                                      v-if="data.type === 'snapshot'"
                                      :size="15"
                                      class="create-tree-node-content__icon hfl-dir-tree-node__icon create-dir-row__icon--snapshot"
                                    />
                                    <FolderOpen
                                      v-else-if="data.type === 'dir'"
                                      :size="15"
                                      class="create-tree-node-content__icon hfl-dir-tree-node__icon create-dir-row__icon--folder"
                                    />
                                    <File
                                      v-else
                                      :size="15"
                                      class="create-tree-node-content__icon hfl-dir-tree-node__icon create-dir-row__icon--file"
                                    />
                                    <div class="create-tree-node-content__text hfl-dir-tree-node__text">
                                      <span class="create-tree-node-content__label hfl-dir-tree-node__label">{{ data.label }}</span>
                                      <span v-if="data.path" class="create-tree-node-content__path hfl-dir-tree-node__path">{{ data.path }}</span>
                                    </div>
                                    <button
                                      v-if="data.type === 'dir'"
                                      type="button"
                                      class="hfl-dir-tree-node__refresh"
                                      :class="{ 'is-refreshing': isRecoveryDirectoryRefreshing(recoveryDirectoryTreeKey(sourceRow.hostId, entry.id, 'snapshot'), data.id) }"
                                      :disabled="isRecoveryDirectoryRefreshing(recoveryDirectoryTreeKey(sourceRow.hostId, entry.id, 'snapshot'), data.id)"
                                      :aria-label="t('protection.backupsPage.ariaRefreshDirectory', { path: data.path })"
                                      :title="t('protection.backupsPage.ariaRefreshDirectory', { path: data.path })"
                                      @click.stop="refreshRecoverySnapshotDirectory(sourceRow.hostId, entry, data)"
                                    >
                                      <RefreshCw
                                        :size="14"
                                        :class="{ 'is-spinning': isRecoveryDirectoryRefreshing(recoveryDirectoryTreeKey(sourceRow.hostId, entry.id, 'snapshot'), data.id) }"
                                      />
                                    </button>
                                  </div>
                                </template>
                              </el-tree>
                            </div>
                          </HflPopover>
                        </div>
                        <div class="create-recovery-dir-plan-field" :data-label="t('protection.backupsPage.colRecoveryTargetDirectory')">
                          <HflPopover
                            :visible="isRecoveryTargetDirPickerVisible(sourceRow.hostId, entry)"
                            trigger="manual"
                            placement="bottom-start"
                            :width="360"
                            popper-class="create-recovery-path-popover"
                            @update:visible="(visible) => setRecoveryTargetDirPickerVisible(sourceRow.hostId, entry, Boolean(visible))"
                          >
                            <template #reference>
                              <div class="create-recovery-path-input">
                                <el-input
                                  :model-value="entry.targetPath || ''"
                                  :placeholder="t('protection.backupsPage.phSelectOrEnterRestoreDirectory')"
                                  :class="{
                                    'create-recovery-path-input--pending': entry.targetPathValidation === 'pending',
                                    'create-recovery-path-input--invalid': isRecoveryTargetDirInputInvalid(entry),
                                  }"
                                  :aria-describedby="isRecoveryTargetDirErrorVisible(sourceRow.hostId, entry) ? `recovery-target-dir-error-${sourceRow.hostId}-${entry.id}` : undefined"
                                  @click.stop
                                  @update:model-value="(value) => updateRecoveryTargetDirInput(sourceRow.hostId, entry, String(value))"
                                  @blur="entry.targetPathValidation === 'pending' && validateRecoveryTargetDirInput(sourceRow.hostId, entry)"
                                  @keydown.enter.prevent="validateRecoveryTargetDirInput(sourceRow.hostId, entry)"
                                >
                                  <template v-if="isRecoveryTargetDirValidating(sourceRow.hostId, entry)" #suffix>
                                    <span class="create-recovery-path-input__checking">{{ t('common.loading') }}</span>
                                  </template>
                                  <template #prefix>
                                    <component
                                      :is="recoveryTargetDirInputIcon(entry)"
                                      :size="14"
                                      class="create-recovery-path-input__type-icon"
                                      :class="recoveryTargetDirInputIconClass(entry)"
                                    />
                                  </template>
                                  <template #append>
                                    <ElButton
                                      class="create-recovery-path-input__btn"
                                      :aria-label="t('protection.backupsPage.btnBrowsePaths')"
                                      @click.stop="setRecoveryTargetDirPickerVisible(sourceRow.hostId, entry, !isRecoveryTargetDirPickerVisible(sourceRow.hostId, entry))"
                                    >
                                      <FolderOpen :size="16" />
                                    </ElButton>
                                  </template>
                                </el-input>
                                <p
                                  v-if="isRecoveryTargetDirErrorVisible(sourceRow.hostId, entry)"
                                  :id="`recovery-target-dir-error-${sourceRow.hostId}-${entry.id}`"
                                  class="create-recovery-path-input__error"
                                  role="alert"
                                >
                                  <span>{{ recoveryTargetDirInputError(entry) }}</span>
                                  <button
                                    type="button"
                                    class="create-recovery-path-input__error-close"
                                    :aria-label="t('protection.backupsPage.btnClose')"
                                    @click.stop="dismissRecoveryPathError(sourceRow.hostId, entry, 'target')"
                                  >
                                    <X :size="13" />
                                  </button>
                                </p>
                              </div>
                            </template>
                            <div class="create-recovery-tree-popover hfl-dir-tree-shell">
                              <el-tree
                                :ref="(el) => setRecoveryDirectoryTreeRef(recoveryDirectoryTreeKey(sourceRow.hostId, entry.id, 'target'), el)"
                                :key="`restore-target-dir-picker-${sourceRow.hostId}-${entry.id}-${targetNodeSourceIdForSource(sourceRow.hostId)}`"
                                v-loading="isRecoveryTargetDirTreeLoading(sourceRow.hostId, entry)"
                                class="source-dir-tree create-recovery-popover-tree hfl-dir-tree hfl-dir-tree--tall"
                                node-key="path"
                                lazy
                                :expand-on-click-node="false"
                                :load="(node, resolve) => loadRecoveryTargetDirPickerNode(sourceRow.hostId, entry, node, resolve)"
                                :props="{ label: 'label', children: 'children', isLeaf: 'isLeaf' }"
                                :current-node-key="entry.targetPath"
                                highlight-current
                                @node-click="(data) => pickRecoveryTargetDir(sourceRow.hostId, entry, data.path)"
                                @node-collapse="(data) => onRecoveryDirectoryExpansionChange(recoveryDirectoryTreeKey(sourceRow.hostId, entry.id, 'target'), data.path)"
                                @node-expand="(data) => onRecoveryDirectoryExpansionChange(recoveryDirectoryTreeKey(sourceRow.hostId, entry.id, 'target'), data.path)"
                              >
                                <template #default="{ data }">
                                  <div class="create-tree-node-content hfl-dir-tree-node">
                                    <FolderOpen :size="15" class="create-tree-node-content__icon hfl-dir-tree-node__icon create-dir-row__icon--folder" />
                                    <div class="create-tree-node-content__text hfl-dir-tree-node__text">
                                      <span class="create-tree-node-content__label hfl-dir-tree-node__label">{{ data.label }}</span>
                                      <span class="create-tree-node-content__path hfl-dir-tree-node__path">{{ data.path }}</span>
                                    </div>
                                    <button
                                      type="button"
                                      class="hfl-dir-tree-node__refresh"
                                      :class="{ 'is-refreshing': isRecoveryDirectoryRefreshing(recoveryDirectoryTreeKey(sourceRow.hostId, entry.id, 'target'), data.path) }"
                                      :disabled="isRecoveryDirectoryRefreshing(recoveryDirectoryTreeKey(sourceRow.hostId, entry.id, 'target'), data.path)"
                                      :aria-label="t('protection.backupsPage.ariaRefreshDirectory', { path: data.path })"
                                      :title="t('protection.backupsPage.ariaRefreshDirectory', { path: data.path })"
                                      @click.stop="refreshRecoveryTargetDirectory(sourceRow.hostId, entry, data)"
                                    >
                                      <RefreshCw
                                        :size="14"
                                        :class="{ 'is-spinning': isRecoveryDirectoryRefreshing(recoveryDirectoryTreeKey(sourceRow.hostId, entry.id, 'target'), data.path) }"
                                      />
                                    </button>
                                  </div>
                                </template>
                              </el-tree>
                            </div>
                          </HflPopover>
                        </div>
                        <div class="create-recovery-dir-plan-field create-recovery-dir-plan-actions" :data-label="t('protection.backupsPage.colActions')">
                          <ElButton
                            text
                            circle
                            type="danger"
                            size="small"
                            :title="t('protection.backupsPage.ariaRemove')"
                            @click="removeRecoveryDirEntry(sourceRow.hostId, entry.id)"
                          >
                            <Trash2 :size="14" />
                          </ElButton>
                        </div>
                      </div>
                      <div class="recovery-dir-selection-add-wrap">
                        <button
                          type="button"
                          class="recovery-dir-selection-add-row"
                          @click="addRecoveryDirEntryAndExpand(sourceRow)"
                        >
                          <CirclePlus :size="16" />
                          <span>{{ t('protection.backupsPage.btnAddRecoveryDir') }}</span>
                        </button>
                      </div>
                    </div>
                  </div>
                </template>
              </el-table-column>
              <el-table-column :label="t('protection.backupsPage.colBackupSnapshot')" min-width="220">
                <template #default="{ row }">
                  <button
                    type="button"
                    class="recovery-dir-source-toggle"
                    @click="toggleRecRecoveryDirRow(row)"
                  >
                    <span class="recovery-source-summary recovery-source-summary--with-snapshot">
                      <span class="recovery-source-summary__head">
                        <span class="recovery-source-summary__name">
                          {{ row.sourceSummary.displayName }}
                        </span>
                        <el-tag
                          size="small"
                          effect="plain"
                          class="recovery-source-summary__type"
                          :type="row.sourceSummary.typeTagType"
                        >
                          {{ row.sourceSummary.typeLabel }}
                        </el-tag>
                      </span>
                      <span class="recovery-source-summary__meta">
                        <span class="recovery-source-summary__ip">
                          {{ row.sourceSummary.ipLine }}
                        </span>
                        <span class="recovery-source-summary__platform">
                          <AgentPlatformBrandIcon
                            v-if="row.sourceSummary.platform"
                            :os="row.sourceSummary.platform"
                          />
                          <component
                            :is="row.sourceSummary.sourceType === 'nas' ? sourceNasIcon : sourceHostIcon"
                            v-else
                            :size="16"
                          />
                        </span>
                      </span>
                      <span class="recovery-source-summary__snapshot">
                        Snapshot: {{ row.snapshotLine }}
                      </span>
                    </span>
                  </button>
                </template>
              </el-table-column>
              <el-table-column :label="t('protection.backupsPage.recStepDest')" min-width="210">
                <template #default="{ row }">
                  <div v-if="row.destSummary" class="recovery-source-summary">
                    <div class="recovery-source-summary__head">
                      <span class="recovery-source-summary__name">
                        {{ row.destSummary.displayName }}
                      </span>
                      <el-tag
                        size="small"
                        effect="plain"
                        class="recovery-source-summary__type"
                        :type="row.destSummary.typeTagType"
                      >
                        {{ row.destSummary.typeLabel }}
                      </el-tag>
                    </div>
                    <div class="recovery-source-summary__meta">
                      <span class="recovery-source-summary__ip">
                        {{ row.destSummary.ipLine }}
                      </span>
                      <span class="recovery-source-summary__platform">
                        <AgentPlatformBrandIcon
                          v-if="row.destSummary.platform"
                          :os="row.destSummary.platform"
                        />
                        <component
                          :is="row.destSummary.sourceType === 'nas' ? sourceNasIcon : sourceHostIcon"
                          v-else
                          :size="16"
                        />
                      </span>
                    </div>
                  </div>
                  <div v-else class="create-source-dir-preview-empty">
                    {{ t('protection.backupsPage.recoveryDestEmptyCompact') }}
                  </div>
                </template>
              </el-table-column>
              <el-table-column min-width="220">
                <template #header>
                  <span class="create-recovery-required-label">
                    {{ t('protection.backupsPage.recoveryConflictPolicyCol') }}
                    <span class="create-recovery-required-mark">*</span>
                  </span>
                </template>
                <template #default="{ row }">
                  <div class="recovery-conflict-policy-cell">
                    <el-select
                      :model-value="recConflictPolicyForSource(row.hostId)"
                      size="small"
                      class="w-full recovery-conflict-policy-select"
                      :class="{
                        'recovery-conflict-policy-select--invalid': !recConflictPolicyForSource(row.hostId),
                        'recovery-conflict-policy-select--skip': recConflictPolicyForSource(row.hostId) === 'skip',
                        'recovery-conflict-policy-select--overwrite': recConflictPolicyForSource(row.hostId) === 'overwrite',
                      }"
                      :placeholder="t('protection.backupsPage.fileConflictPolicyPlaceholder')"
                      @update:model-value="(value) => setRecConflictPolicyForSource(row.hostId, value as RecoveryConflictPolicy)"
                    >
                      <el-option :label="t('protection.backupsPage.recoveryConflictSkip')" value="skip">
                        <div class="recovery-conflict-policy-option recovery-conflict-policy-option--skip">
                          <ShieldCheck :size="14" />
                          <span>{{ t('protection.backupsPage.createRecoveryConflictSkipFull') }}</span>
                        </div>
                      </el-option>
                      <el-option :label="t('protection.backupsPage.recoveryConflictOverwrite')" value="overwrite">
                        <div class="recovery-conflict-policy-option recovery-conflict-policy-option--overwrite">
                          <ShieldAlert :size="14" />
                          <span>{{ t('protection.backupsPage.createRecoveryConflictOverwriteFull') }}</span>
                        </div>
                      </el-option>
                    </el-select>
                  </div>
                </template>
              </el-table-column>
              <el-table-column :label="t('protection.backupsPage.colRecoveryDirectoryMapping')" min-width="300">
                <template #default="{ row }">
                  <ElTooltip
                    v-if="row.configuredEntries.length"
                    placement="top-start"
                    :show-after="300"
                    popper-class="recovery-entry-preview-tooltip"
                  >
                    <template #content>
                      <div class="recovery-entry-preview-tooltip__list">
                        <div
                          v-for="entry in row.configuredEntries"
                          :key="entry.id"
                          class="recovery-mapping-line recovery-entry-preview-tooltip__line"
                        >
                          <span
                            class="recovery-mapping-line__endpoint"
                            :class="`recovery-mapping-line__endpoint--${recoveryMappingSourceKind(entry)}`"
                          >
                            <Archive
                              v-if="recoveryMappingSourceKind(entry) === 'snapshot'"
                              :size="14"
                              class="recovery-mapping-line__icon"
                            />
                            <File
                              v-else-if="recoveryMappingSourceKind(entry) === 'file'"
                              :size="14"
                              class="recovery-mapping-line__icon"
                            />
                            <Folder v-else :size="14" class="recovery-mapping-line__icon" />
                            <span class="recovery-mapping-line__text">{{ recoveryMappingSourceLabel(entry) }}</span>
                          </span>
                          <span class="recovery-mapping-line__arrow" aria-hidden="true">-&gt;</span>
                          <span class="recovery-mapping-line__endpoint recovery-mapping-line__endpoint--target">
                            <FolderOpen :size="14" class="recovery-mapping-line__icon" />
                            <span class="recovery-mapping-line__text">{{ recoveryMappingTargetLabel(entry) }}</span>
                          </span>
                        </div>
                      </div>
                    </template>
                    <div class="create-source-dir-preview recovery-entry-preview-trigger">
                      <div class="create-source-dir-preview__count">
                        {{ recoveryMappingTotalLabel(row.configuredEntries.length) }}
                      </div>
                      <div
                        v-for="entry in row.previewEntries"
                        :key="entry.id"
                        class="create-source-dir-preview__item recovery-mapping-line recovery-mapping-line--preview"
                      >
                        <span
                          class="recovery-mapping-line__endpoint"
                          :class="`recovery-mapping-line__endpoint--${recoveryMappingSourceKind(entry)}`"
                        >
                          <Archive
                            v-if="recoveryMappingSourceKind(entry) === 'snapshot'"
                            :size="14"
                            class="recovery-mapping-line__icon"
                          />
                          <File
                            v-else-if="recoveryMappingSourceKind(entry) === 'file'"
                            :size="14"
                            class="recovery-mapping-line__icon"
                          />
                          <Folder v-else :size="14" class="recovery-mapping-line__icon" />
                          <span class="recovery-mapping-line__text">{{ recoveryMappingSourceLabel(entry) }}</span>
                        </span>
                        <span class="recovery-mapping-line__arrow" aria-hidden="true">-&gt;</span>
                        <span class="recovery-mapping-line__endpoint recovery-mapping-line__endpoint--target">
                          <FolderOpen :size="14" class="recovery-mapping-line__icon" />
                          <span class="recovery-mapping-line__text">{{ recoveryMappingTargetLabel(entry) }}</span>
                        </span>
                      </div>
                      <div v-if="row.hiddenDirCount > 0" class="create-source-dir-preview__more">
                        {{ t('protection.backupsPage.moreRecoveryDirs', { n: row.hiddenDirCount }) }}
                      </div>
                    </div>
                  </ElTooltip>
                  <div v-else class="create-source-dir-preview-empty">
                    {{ t('protection.backupsPage.recoveryDirEmptyCompact') }}
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <el-drawer
            v-model="recDirDrawerOpen"
            direction="rtl"
            destroy-on-close
            append-to-body
            :modal="true"
            :z-index="REC_WIZARD_DRAWER_Z_INDEX"
            :size="nestedDrawerSize"
            class="dp-rec-dest-drawer dp-rec-dir-drawer"
            @opened="onRecDirDrawerOpened"
            @closed="onRecDirDrawerClosed"
          >
            <template #header>
              <span class="dp-rec-dest-drawer__title">
                {{ store.getNodeName(recDirDrawerHostId) || t('protection.backupsPage.recDirSelectionCol') }}
              </span>
            </template>

            <div class="dp-rec-dest-drawer__body">
              <div class="dp-rec-dest-drawer__content">
                <p class="m-0 mb-1 text-xs text-slate-500">
                  {{ t('protection.backupsPage.recDirsSnapshotHint', { time: recGroupSnapshotDisplayLine(recDirDrawerHostId) }) }}
                </p>
                <p class="m-0 mb-3 text-xs text-slate-500">{{ t('protection.backupsPage.recDirDrawerHint') }}</p>
                <div
                  v-if="!recDirTreeDataForSource(recDirDrawerHostId).length"
                  class="dp-create-tree-shell__empty hfl-dir-tree-empty"
                >
                  {{ t('protection.backupsPage.recSourceTreeEmpty') }}
                </div>
                <el-tree
                  v-else
                  ref="recDirDrawerTreeRef"
                  :key="`rec-dir-drawer-${recDirDrawerHostId}-${recDirDrawerEntryId}`"
                  class="source-dir-tree rec-select-tree recovery-dir-picker__tree hfl-dir-tree hfl-dir-tree--tall dp-rec-dir-drawer__tree"
                  :data="recDirTreeDataForSource(recDirDrawerHostId)"
                  node-key="id"
                  show-checkbox
                  check-strictly
                  default-expand-all
                  :check-on-click-node="true"
                  :expand-on-click-node="false"
                  :props="{ label: 'label', children: 'children' }"
                  :default-checked-keys="recDirDrawerDraftKeys"
                  @check-change="(data, checked) => onRecDirDrawerTreeCheckChange(recDirDrawerHostId, data, checked)"
                >
                  <template #default="{ data }">
                    <div class="tree-node-content hfl-dir-tree-node">
                      <component
                        :is="recoveryDirNodeIcon(data)"
                        :size="15"
                        class="tree-node-content__icon hfl-dir-tree-node__icon"
                        :class="`create-dir-row__icon--${recoveryDirNodeSourceKind(data) === 'file' ? 'file' : 'folder'}`"
                      />
                      <div class="tree-node-content__text hfl-dir-tree-node__text">
                        <span class="tree-node-content__label hfl-dir-tree-node__label">{{ data.label }}</span>
                        <span class="tree-node-content__path hfl-dir-tree-node__path">{{ recoveryTreePathLabel(data) }}</span>
                      </div>
                    </div>
                  </template>
                </el-tree>
              </div>

            </div>
            <template #footer>
              <div class="el-drawer__footer-actions">
                <ElButton @click="cancelRecDirDrawer">
                  {{ t('protection.backupsPage.btnCancel') }}
                </ElButton>
                <ElButton type="primary" @click="saveRecDirDrawer">
                  {{ t('protection.backupsPage.btnEdit') }}
                </ElButton>
              </div>
            </template>
          </el-drawer>
        </div>

        <div v-show="recStep === 3" class="dp-wizard-pane">
          <p class="text-sm text-slate-600 mb-3">{{ t('protection.backupsPage.recStep4Lead') }}</p>
          <div class="recovery-manual-table-shell">
            <el-table
          v-table-overflow-title
              ref="recRecoveryConfirmTableRef"
              :data="recRecoveryConfirmSourceRows"
              row-key="hostId"
              max-height="calc(var(--app-viewport-height) - 390px)"
              stripe
              :expand-row-keys="recExpandedRecConfirmHostIds"
              :header-cell-style="TABLE_HEADER_STYLE"
              class="hfl-list-table recovery-confirm-source-table recovery-manual-table"
              @expand-change="onRecRecoveryConfirmExpandChange"
            >
              <el-table-column type="expand" width="35" class-name="recovery-dest-config-expand-column">
                <template #default="{ row: sourceRow }">
                  <div class="recovery-confirm-expand">
                    <div
                      v-for="draft in sourceRow.taskDrafts"
                      :key="`${draft.backupId}-${draft.destHostId}-${draft.destPath}`"
                      class="recovery-confirm-mapping-group"
                    >
                      <div class="recovery-confirm-mapping-list">
                        <div
                          v-for="entry in draft.dirs"
                          :key="`${draft.backupId}-${entry.id}`"
                          class="recovery-mapping-line recovery-confirm-mapping-line"
                          :title="`${recoveryMappingSourceLabel(entry)} -> ${recoveryMappingTargetLabel(entry)}`"
                        >
                          <span
                            class="recovery-mapping-line__endpoint"
                            :class="`recovery-mapping-line__endpoint--${recoveryMappingSourceKind(entry)}`"
                          >
                            <Archive
                              v-if="recoveryMappingSourceKind(entry) === 'snapshot'"
                              :size="14"
                              class="recovery-mapping-line__icon"
                            />
                            <File
                              v-else-if="recoveryMappingSourceKind(entry) === 'file'"
                              :size="14"
                              class="recovery-mapping-line__icon"
                            />
                            <Folder v-else :size="14" class="recovery-mapping-line__icon" />
                            <span class="recovery-mapping-line__text">{{ recoveryMappingSourceLabel(entry) }}</span>
                          </span>
                          <span class="recovery-mapping-line__arrow" aria-hidden="true">-&gt;</span>
                          <span class="recovery-mapping-line__endpoint recovery-mapping-line__endpoint--target">
                            <FolderOpen :size="14" class="recovery-mapping-line__icon" />
                            <span class="recovery-mapping-line__text">{{ recoveryMappingTargetLabel(entry) }}</span>
                          </span>
                        </div>
                      </div>
                    </div>
                    <div v-if="!sourceRow.taskDrafts.length" class="text-sm text-slate-400">
                      {{ t('protection.backupsPage.recoveryDirEmptyCompact') }}
                    </div>
                  </div>
                </template>
              </el-table-column>
              <el-table-column :label="t('protection.backupsPage.colBackupSnapshot')" min-width="220">
                <template #default="{ row }">
                  <button
                    type="button"
                    class="recovery-dir-source-toggle"
                    @click="toggleRecRecoveryConfirmRow(row)"
                  >
                    <span class="recovery-source-summary recovery-source-summary--with-snapshot">
                      <span class="recovery-source-summary__head">
                        <span class="recovery-source-summary__name">
                          {{ row.sourceSummary.displayName }}
                        </span>
                        <el-tag
                          size="small"
                          effect="plain"
                          class="recovery-source-summary__type"
                          :type="row.sourceSummary.typeTagType"
                        >
                          {{ row.sourceSummary.typeLabel }}
                        </el-tag>
                      </span>
                      <span class="recovery-source-summary__meta">
                        <span class="recovery-source-summary__ip">
                          {{ row.sourceSummary.ipLine }}
                        </span>
                        <span class="recovery-source-summary__platform">
                          <AgentPlatformBrandIcon
                            v-if="row.sourceSummary.platform"
                            :os="row.sourceSummary.platform"
                          />
                          <component
                            :is="row.sourceSummary.sourceType === 'nas' ? sourceNasIcon : sourceHostIcon"
                            v-else
                            :size="16"
                          />
                        </span>
                      </span>
                      <span class="recovery-source-summary__snapshot">
                        Snapshot: {{ row.snapshotLine }}
                      </span>
                    </span>
                  </button>
                </template>
              </el-table-column>
              <el-table-column :label="t('protection.backupsPage.recStepDest')" min-width="216">
                <template #default="{ row }">
                  <div v-if="row.destSummary" class="recovery-source-summary">
                    <div class="recovery-source-summary__head">
                      <span class="recovery-source-summary__name">
                        {{ row.destSummary.displayName }}
                      </span>
                      <el-tag
                        size="small"
                        effect="plain"
                        class="recovery-source-summary__type"
                        :type="row.destSummary.typeTagType"
                      >
                        {{ row.destSummary.typeLabel }}
                      </el-tag>
                    </div>
                    <div class="recovery-source-summary__meta">
                      <span class="recovery-source-summary__ip">
                        {{ row.destSummary.ipLine }}
                      </span>
                      <span class="recovery-source-summary__platform">
                        <AgentPlatformBrandIcon
                          v-if="row.destSummary.platform"
                          :os="row.destSummary.platform"
                        />
                        <component
                          :is="row.destSummary.sourceType === 'nas' ? sourceNasIcon : sourceHostIcon"
                          v-else
                          :size="16"
                        />
                      </span>
                    </div>
                  </div>
                  <div v-else class="create-source-dir-preview-empty">
                    {{ t('protection.backupsPage.recoveryDestEmptyCompact') }}
                  </div>
                </template>
              </el-table-column>
              <el-table-column :label="t('protection.backupsPage.recoveryConflictPolicyCol')" min-width="254">
                <template #default="{ row }">
                  <ElTooltip
                    placement="top-start"
                    :content="recoveryConflictPolicySummary(row.conflictPolicy)"
                    :show-after="300"
                  >
                    <span
                      class="create-recovery-plan-cell__policy recovery-confirm-policy-cell"
                      :class="`create-recovery-plan-cell__policy--${row.conflictPolicy}`"
                    >
                      <ShieldAlert
                        v-if="row.conflictPolicy === 'overwrite'"
                        :size="14"
                        class="create-recovery-plan-cell__policy-icon"
                      />
                      <ShieldCheck v-else :size="14" class="create-recovery-plan-cell__policy-icon" />
                      <span class="create-recovery-plan-cell__policy-text">
                        {{ recoveryConflictPolicySummary(row.conflictPolicy) }}
                      </span>
                    </span>
                  </ElTooltip>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>
                </div>
              </div>
            </div>
          </div>
        </template>
              </div>
            </main>
          </div>

          <div class="fullscreen-form-footer create-backup-footer">
            <div class="create-backup-footer__inner">
              <div class="create-backup-footer__actions">
                <template v-if="recEntryStage === 'chooser' && recEntryMode === 'plan'">
                  <ElButton class="hfl-btn-with-icon" :disabled="recSubmitting" @click="closeRecoveryWizard">
                    <X :size="14" />
                    <span>{{ t('protection.backupsPage.btnCancel') }}</span>
                  </ElButton>
                  <ElButton
                    type="primary"
                    class="hfl-btn-with-icon"
                    :disabled="!selectedRecoveryPlans.length"
                    :loading="recSubmitting"
                    @click="confirmRecoveryEntry"
                  >
                    <Check :size="14" />
                    <span>{{ t('protection.backupsPage.btnRunRecoveryPlan') }}</span>
                  </ElButton>
                </template>
                <template v-else>
                  <ElButton class="hfl-btn-with-icon" :disabled="recSubmitting" @click="closeRecoveryWizard">
                    <X :size="14" />
                    <span>{{ t('protection.backupsPage.btnCancel') }}</span>
                  </ElButton>
                  <ElButton
                    v-if="recStep > (isFixedSnapshotRestore ? 1 : 0)"
                    class="hfl-btn-with-icon"
                    :disabled="recSubmitting"
                    @click="prevRec"
                  >
                    <ArrowLeft :size="14" />
                    <span>{{ t('protection.backupsPage.btnPrev') }}</span>
                  </ElButton>
                  <ElButton
                    v-else-if="recEntryStage === 'wizard'"
                    class="hfl-btn-with-icon"
                    :disabled="recSubmitting"
                    @click="recEntryStage = 'chooser'"
                  >
                    <ArrowLeft :size="14" />
                    <span>{{ t('protection.backupsPage.btnBackToRecoveryChoice') }}</span>
                  </ElButton>
                  <ElButton
                    v-if="recStep < 3"
                    type="primary"
                    class="hfl-btn-with-icon"
                    :disabled="(recStep === 1 && !recRecoveryDestStepReady) || (recStep === 2 && !recRecoveryDirStepReady)"
                    @click="nextRec"
                  >
                    <span>{{ t('protection.backupsPage.btnNext') }}</span>
                    <ArrowRight :size="14" />
                  </ElButton>
                  <ElButton v-else type="primary" class="hfl-btn-with-icon" :loading="recSubmitting" @click="runRecovery">
                    <Check :size="14" />
                    <span>{{ t('protection.backupsPage.btnConfirmRecovery') }}</span>
                  </ElButton>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div
        v-if="addSourceOpen"
        ref="addSourceShellRef"
        class="fullscreen-form-fullscreen fullscreen-form-animated resource-add-fullscreen source-deploy-fullscreen add-source-fullscreen"
        :class="{ 'agent-deploy-fullscreen': addSourceType === 'hostFileSystem' }"
        role="dialog"
        aria-modal="true"
        tabindex="-1"
        @keydown.escape.prevent="closeAddSource"
      >
        <div class="fullscreen-form-page source-deploy-page">
          <header class="fullscreen-form-header">
            <button type="button" class="fullscreen-form-header__back" @click="closeAddSource">
              <ArrowLeft class="fullscreen-form-header__back-icon" :size="18" />
            </button>
            <div class="fullscreen-form-header__content">
              <h1 class="fullscreen-form-header__title">{{ t('protection.sourceResources.addSourcePageTitle') }}</h1>
              <p class="fullscreen-form-header__desc">{{ t('protection.sourceResources.addSourcePageDesc') }}</p>
            </div>
          </header>

          <div class="fullscreen-form-layout">
            <div class="fullscreen-form-main">
              <div class="fullscreen-form-card add-source-switcher-card">
                <section class="fullscreen-form-section">
                  <div class="add-source-type-grid">
                    <button
                      type="button"
                      class="add-source-type-card"
                      :class="{ 'is-active': addSourceType === 'hostFileSystem' }"
                      :aria-pressed="addSourceType === 'hostFileSystem'"
                      @click="addSourceType = 'hostFileSystem'"
                    >
                      <span class="add-source-type-card__inner">
                        <component :is="sourceHostIcon" :size="24" />
                        <span class="add-source-type-card__text">
                          <span class="font-semibold">{{ t('protection.sourceResources.addSourceTypeHostFile') }}</span>
                          <span class="text-xs text-[var(--color-text-tertiary)]">{{ t('protection.sourceResources.deployAgentHint') }}</span>
                        </span>
                      </span>
                    </button>
                    <button
                      type="button"
                      class="add-source-type-card"
                      :class="{ 'is-active': addSourceType === 'nas' }"
                      :aria-pressed="addSourceType === 'nas'"
                      @click="addSourceType = 'nas'"
                    >
                      <span class="add-source-type-card__inner">
                        <component :is="sourceNasIcon" :size="24" />
                        <span class="add-source-type-card__text">
                          <span class="font-semibold">{{ t('protection.sourceResources.addSourceTypeNas') }}</span>
                          <span class="text-xs text-[var(--color-text-tertiary)]">{{ t('protection.sourceResources.addSourceTypeNasHint') }}</span>
                        </span>
                      </span>
                    </button>
                  </div>
                </section>
              </div>

              <template v-if="addSourceType === 'hostFileSystem'">
                <HostAddForm
                  :os="deploySelectedOs"
                  @update:os="deploySelectedOs = $event"
                  @copy="copyDeployScript"
                />
                <div class="fullscreen-form-footer fullscreen-form-action-footer">
                  <ElButton @click="closeAddSource">
                    {{ t('common.back') }}
                  </ElButton>
                </div>
              </template>
              <template v-else>
                <NasAddForm
                  :protocol="nasProtocol"
                  :smb-server="nasSmbServer"
                  :smb-share="nasSmbShare"
                  :smb-username="nasSmbUsername"
                  :smb-password="nasSmbPassword"
                  :smb-domain="nasSmbDomain"
                  :nfs-host="nasNfsHost"
                  :nfs-export="nasNfsExport"
                  :nfs-options="nasNfsOptions"
                  :bind-node-id="nasBindNodeId"
                  :bind-node-error="nasBindNodeError"
                  :name="nasName"
                  :generated-name="generatedNasName"
                  :dir="nasDir"
                  :generated-dir="generatedNasDir"
                  :proxy-nodes="proxyNodes"
                  :proxy-nodes-refreshing="proxyNodesRefreshing"
                  :busy="nasBusy"
                  @update:protocol="nasProtocol = $event"
                  @update:smb-server="nasSmbServer = $event"
                  @update:smb-share="nasSmbShare = $event"
                  @update:smb-username="nasSmbUsername = $event"
                  @update:smb-password="nasSmbPassword = $event"
                  @update:smb-domain="nasSmbDomain = $event"
                  @update:nfs-host="nasNfsHost = $event"
                  @update:nfs-export="nasNfsExport = $event"
                  @update:nfs-options="nasNfsOptions = $event"
                  @update:bind-node-id="nasBindNodeId = $event"
                  @update:name="nasName = $event"
                  @name-touched="nasNameTouched = true"
                  @update:dir="nasDir = $event"
                  @dir-touched="nasDirTouched = true"
                  @clear-bind-node-error="clearNasBindNodeError"
                  @refresh-proxy-nodes="refreshProxyNodesManually"
                  @open-proxy-deploy="openProxyDeploy"
                  @cancel="closeAddSource"
                  @submit="nasSubmit"
                />
              </template>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
    <BackupSourceDeleteDialog
      v-if="backupSourceDeleteDialogOpen"
      v-model="backupSourceDeleteDialogOpen"
      :source-ids="backupSourceDeleteIds"
      :sources="backupSourceDeleteRows"
      :show-snapshots="backupSourceDeleteShowSnapshots"
      :previous-failure-details="backupSourceDeletePreviousFailure"
      @deleted="onBackupSourcesDeleted"
    />
    <BackupSourceStep3DeleteDialog
      v-if="backupSourceStep3DeleteDialogOpen"
      v-model="backupSourceStep3DeleteDialogOpen"
      :source-ids="backupSourceDeleteIds"
      :sources="backupSourceDeleteRows"
      :previous-failure-details="backupSourceDeletePreviousFailure"
      @deleted="onBackupSourcesDeleted"
    />
    <DangerConfirmDialog
      v-if="deleteRestoreTasksDialogOpen"
      v-model="deleteRestoreTasksDialogOpen"
      :title="t('protection.backupsPage.titleDeleteTasks')"
      :message="t('protection.backupsPage.msgDeleteRestoreTasksConfirm', { n: pendingDeleteRestoreTasks.length })"
      :items="deleteRestoreTaskItems"
      :items-heading="t('protection.backupsPage.flowBackupColRestoreTaskStatus')"
      :item-name-label="t('protection.backupsPage.flowRestoreRecordColName')"
      :item-status-label="t('protection.backupsPage.flowTaskColStatus')"
      :item-details-label="t('protection.backupDetail.labelStart')"
      :cancel-text="t('common.cancel')"
      :confirm-text="t('protection.backupsPage.btnConfirmDelete')"
      level="high"
      width="640px"
      @confirm="confirmDeleteRestoreTasks"
      @cancel="deleteRestoreTasksDialogOpen = false"
    />
    <ProtectionStopConfirmDialog
      v-if="stopConfirmOpen"
      v-model="stopConfirmOpen"
      :kind="stopConfirmKind"
      :items="stopConfirmItems"
      :loading="step3StopActionBusy"
      @confirm="onConfirmStopDialog"
      @cancel="stopConfirmOpen = false"
    />
    <DangerConfirmDialog
      v-if="partialRecoveryConfirmOpen"
      v-model="partialRecoveryConfirmOpen"
      :title="t('protection.backupsPage.recoveryPlanPartialConfirmTitle')"
      :message="t('protection.backupsPage.recoveryPlanPartialConfirmMessage', {
        configured: pendingRecoveryPlanConfirmPlans.length,
        missing: pendingRecoveryPlanConfirmMissingRows.length,
        hosts: recoveryMissingPlanHostSummary(pendingRecoveryPlanConfirmMissingRows),
      })"
      :items="partialRecoveryConfirmItems"
      :items-heading="t('protection.backupsPage.recoveryPlanMissingTitle')"
      :item-name-label="t('protection.backupsPage.colBackupSource')"
      :item-status-label="t('protection.backupsPage.colStatus')"
      :item-details-label="t('protection.backupsPage.flowSourceDetailHostname')"
      :cancel-text="t('common.cancel')"
      :confirm-text="t('protection.backupsPage.btnRunRecoveryPlan')"
      level="low"
      width="640px"
      @confirm="confirmPartialRecoveryPlans"
      @cancel="partialRecoveryConfirmOpen = false"
    />
    <BackupCreateWizard
      v-if="setupDrCreateOpen"
      embedded
      :initial-sources="setupDrCreateSources"
      :initial-edit-config-ids="setupDrEditConfigIds"
      :initial-edit-section="setupDrEditSection"
      @ready="setupDrOpening = false"
      @closed="closeCreate"
      @completed="finishCreateAndGoToStep3"
    />
    <Teleport to="body">
      <div
        v-if="setupDrOpening"
        class="fullscreen-form-fullscreen setup-dr-opening-skeleton"
        role="status"
        :aria-label="t('protection.backupsPage.loadingCreateWizard')"
      >
        <div class="fullscreen-form-page setup-dr-opening-skeleton__page" aria-hidden="true">
          <header class="fullscreen-form-header setup-dr-opening-skeleton__header">
            <span class="setup-dr-opening-skeleton__back"></span>
            <div class="fullscreen-form-header__content setup-dr-opening-skeleton__title">
              <span></span>
              <i></i>
            </div>
          </header>
          <div class="fullscreen-form-layout create-backup-layout create-backup-layout--steps setup-dr-opening-skeleton__layout">
            <aside class="create-backup-steps setup-dr-opening-skeleton__steps">
              <span v-for="idx in 5" :key="`setup-dr-opening-step-${idx}`">
                <i></i>
                <b></b>
              </span>
            </aside>
            <main class="fullscreen-form-main create-backup-main setup-dr-opening-skeleton__main">
              <section class="setup-dr-opening-skeleton__wizard-panel">
                <div class="setup-dr-opening-skeleton__lead">
                  <span></span>
                  <i></i>
                </div>
                <div class="setup-dr-opening-skeleton__panel-toolbar">
                  <span></span>
                  <i></i>
                </div>
                <div class="setup-dr-opening-skeleton__table">
                  <div class="setup-dr-opening-skeleton__table-head">
                    <span></span>
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
                  <div class="setup-dr-opening-skeleton__table-row">
                    <span></span>
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
                  <div class="setup-dr-opening-skeleton__expanded">
                    <section class="setup-dr-opening-skeleton__browse">
                      <span></span>
                      <div class="setup-dr-opening-skeleton__input-row">
                        <i></i>
                        <b></b>
                      </div>
                      <em></em>
                      <div class="setup-dr-opening-skeleton__tree-box">
                        <small></small>
                        <small></small>
                        <small></small>
                      </div>
                    </section>
                    <section class="setup-dr-opening-skeleton__selected">
                      <span></span>
                      <div class="setup-dr-opening-skeleton__selected-box">
                        <i></i>
                        <i></i>
                      </div>
                    </section>
                  </div>
                </div>
              </section>
            </main>
          </div>
          <footer class="setup-dr-opening-skeleton__footer">
            <span></span>
            <i></i>
          </footer>
        </div>
      </div>
    </Teleport>
  </ModulePage>
</template>

<style src="../../styles/fullscreen-form-shell.css"></style>
<style src="../../styles/resource-add.css"></style>
<style src="../../styles/source-deploy-ui.css"></style>
<style src="../../styles/agent-install-wizard.css"></style>
<style scoped>
.display-name-edit-dialog__lead {
  margin: 0 0 12px;
  color: var(--el-text-color-secondary, #64748b);
  font-size: 13px;
  line-height: 20px;
}

.display-name-edit-dialog__table :deep(.el-table__cell) {
  vertical-align: top;
}

.display-name-edit-dialog__field {
  margin-bottom: 0;
}

.display-name-edit-dialog__field :deep(.el-form-item__content) {
  display: block;
}

.display-name-edit-dialog__field :deep(.el-form-item__error) {
  position: static;
  padding-top: 4px;
  line-height: 16px;
}

.protection-flow-list-shell {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.protection-flow-list-panel {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
}

.protection-flow-table-block {
  display: flex;
  flex: 0 1 auto;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
}

.protection-flow-table-block :deep(.el-table__empty-block) {
  background: #fff;
}

.protection-flow-table-block :deep(.el-table-column--selection .cell) {
  justify-content: center;
  text-align: center;
}

.wizard-target-repository-cell {
  display: grid;
  min-width: 0;
  max-width: 100%;
  grid-template-columns: 8px minmax(0, 1fr);
  align-items: start;
  column-gap: 8px;
}

.wizard-target-repository-cell__dot {
  width: 6px;
  height: 6px;
  margin-top: 6px;
  border-radius: 999px;
  background: rgb(148 163 184);
}

.wizard-target-repository-cell__body {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.wizard-target-repository-cell__name {
  min-width: 0;
  overflow: hidden;
  color: rgb(30 41 59);
  font-size: 13px;
  font-weight: 650;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wizard-target-repository-cell__location {
  min-width: 0;
  overflow: hidden;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 17px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wizard-target-repository-cell--online .wizard-target-repository-cell__dot {
  background: var(--color-success);
}

.wizard-target-repository-cell--warning .wizard-target-repository-cell__dot {
  background: var(--color-warning);
}

.wizard-target-repository-cell--offline .wizard-target-repository-cell__dot {
  background: var(--color-error);
}

.flow-filterable-header {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
}

.flow-header-filter-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 4px;
  padding: 0;
  color: rgb(148 163 184);
  background: transparent;
  cursor: pointer;
  transition:
    color 0.16s ease,
    background 0.16s ease;
}

.flow-header-filter-trigger:hover,
.flow-header-filter-trigger--active {
  color: rgb(79 70 229);
  background: rgba(238, 242, 255, 0.95);
}

.setup-dr-opening-skeleton {
  background: var(--content-bg, #f4f4f7);
}

.setup-dr-opening-skeleton__page {
  min-height: var(--app-viewport-height);
}

.setup-dr-opening-skeleton__header {
  align-items: center;
}

.setup-dr-opening-skeleton__back,
.setup-dr-opening-skeleton__title span,
.setup-dr-opening-skeleton__title i,
.setup-dr-opening-skeleton__steps span,
.setup-dr-opening-skeleton__wizard-panel,
.setup-dr-opening-skeleton__lead span,
.setup-dr-opening-skeleton__lead i,
.setup-dr-opening-skeleton__panel-toolbar span,
.setup-dr-opening-skeleton__panel-toolbar i,
.setup-dr-opening-skeleton__table-head span,
.setup-dr-opening-skeleton__table-row span,
.setup-dr-opening-skeleton__browse,
.setup-dr-opening-skeleton__selected,
.setup-dr-opening-skeleton__footer span,
.setup-dr-opening-skeleton__footer i {
  position: relative;
  overflow: hidden;
  background: var(--color-card-bg, #fff);
}

.setup-dr-opening-skeleton__back::after,
.setup-dr-opening-skeleton__title span::after,
.setup-dr-opening-skeleton__title i::after,
.setup-dr-opening-skeleton__steps span::after,
.setup-dr-opening-skeleton__wizard-panel::after,
.setup-dr-opening-skeleton__lead span::after,
.setup-dr-opening-skeleton__lead i::after,
.setup-dr-opening-skeleton__panel-toolbar span::after,
.setup-dr-opening-skeleton__panel-toolbar i::after,
.setup-dr-opening-skeleton__table-head span::after,
.setup-dr-opening-skeleton__table-row span::after,
.setup-dr-opening-skeleton__browse::after,
.setup-dr-opening-skeleton__selected::after,
.setup-dr-opening-skeleton__footer span::after,
.setup-dr-opening-skeleton__footer i::after {
  content: '';
  position: absolute;
  inset: 0;
  transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, rgba(109, 94, 246, 0.10), transparent);
  animation: setup-dr-opening-skeleton-shimmer 1.15s ease-in-out infinite;
}

.setup-dr-opening-skeleton__back {
  display: block;
  width: 38px;
  height: 38px;
  flex: 0 0 auto;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
}

.setup-dr-opening-skeleton__title {
  display: grid;
  gap: 10px;
  min-width: 0;
}

.setup-dr-opening-skeleton__title span,
.setup-dr-opening-skeleton__title i,
.setup-dr-opening-skeleton__lead span,
.setup-dr-opening-skeleton__lead i,
.setup-dr-opening-skeleton__panel-toolbar span,
.setup-dr-opening-skeleton__panel-toolbar i,
.setup-dr-opening-skeleton__table-head span,
.setup-dr-opening-skeleton__table-row span,
.setup-dr-opening-skeleton__browse span,
.setup-dr-opening-skeleton__browse i,
.setup-dr-opening-skeleton__browse b,
.setup-dr-opening-skeleton__browse em,
.setup-dr-opening-skeleton__browse small,
.setup-dr-opening-skeleton__selected span,
.setup-dr-opening-skeleton__selected i,
.setup-dr-opening-skeleton__steps i,
.setup-dr-opening-skeleton__steps b {
  display: block;
  border-radius: 999px;
  background: var(--color-grey-2, #f5f5f7);
}

.setup-dr-opening-skeleton__title span {
  width: min(320px, 72%);
  height: 24px;
}

.setup-dr-opening-skeleton__title i {
  width: min(560px, 92%);
  height: 12px;
}

.setup-dr-opening-skeleton__layout {
  min-height: 0;
}

.setup-dr-opening-skeleton__steps {
  display: grid;
  align-content: start;
  gap: 10px;
}

.setup-dr-opening-skeleton__steps span {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  min-height: 48px;
  padding: 10px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
}

.setup-dr-opening-skeleton__steps i {
  width: 30px;
  height: 30px;
  border-radius: 999px;
}

.setup-dr-opening-skeleton__steps b {
  width: 78%;
  height: 12px;
}

.setup-dr-opening-skeleton__main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  padding-right: 6px;
}

.setup-dr-opening-skeleton__wizard-panel {
  display: grid;
  flex: 1 1 auto;
  grid-template-rows: auto auto minmax(0, 1fr);
  gap: 14px;
  min-height: 0;
  padding: 22px 20px 18px;
  border: 1px solid rgba(109, 94, 246, 0.78);
  border-radius: 8px;
  background: var(--color-card-bg, #fff);
  box-shadow: 0 18px 40px rgba(30, 41, 59, 0.06);
}

.setup-dr-opening-skeleton__wizard-panel::after {
  pointer-events: none;
}

.setup-dr-opening-skeleton__lead {
  display: grid;
  gap: 10px;
  width: min(920px, 100%);
}

.setup-dr-opening-skeleton__lead span {
  width: 100%;
  height: 14px;
}

.setup-dr-opening-skeleton__lead i {
  width: 58%;
  height: 12px;
}

.setup-dr-opening-skeleton__panel-toolbar {
  display: flex;
  justify-content: flex-end;
}

.setup-dr-opening-skeleton__panel-toolbar span {
  width: min(300px, 28%);
  height: 34px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
}

.setup-dr-opening-skeleton__panel-toolbar i {
  display: none;
}

.setup-dr-opening-skeleton__table {
  display: grid;
  grid-template-rows: 47px 83px minmax(0, 1fr);
  min-height: 0;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}

.setup-dr-opening-skeleton__table-head,
.setup-dr-opening-skeleton__table-row {
  display: grid;
  grid-template-columns: 44px 1.35fr 0.6fr 1fr 2fr 0.6fr;
  align-items: center;
  gap: 14px;
  padding: 0 14px;
}

.setup-dr-opening-skeleton__table-head {
  background: #eef2f7;
}

.setup-dr-opening-skeleton__table-row {
  border-bottom: 1px solid var(--color-border-light, #f2f2f6);
  background: #fbfbfc;
}

.setup-dr-opening-skeleton__table-head span,
.setup-dr-opening-skeleton__table-row span {
  height: 12px;
}

.setup-dr-opening-skeleton__table-head span:nth-child(1),
.setup-dr-opening-skeleton__table-row span:nth-child(1) {
  width: 14px;
}

.setup-dr-opening-skeleton__table-head span:nth-child(2),
.setup-dr-opening-skeleton__table-row span:nth-child(2) {
  width: 72%;
}

.setup-dr-opening-skeleton__table-head span:nth-child(3),
.setup-dr-opening-skeleton__table-row span:nth-child(3) {
  width: 58%;
}

.setup-dr-opening-skeleton__table-head span:nth-child(4),
.setup-dr-opening-skeleton__table-row span:nth-child(4) {
  width: 64%;
}

.setup-dr-opening-skeleton__table-head span:nth-child(5),
.setup-dr-opening-skeleton__table-row span:nth-child(5) {
  width: 82%;
}

.setup-dr-opening-skeleton__table-head span:nth-child(6),
.setup-dr-opening-skeleton__table-row span:nth-child(6) {
  width: 62%;
}

.setup-dr-opening-skeleton__expanded {
  display: grid;
  grid-template-columns: minmax(0, 1.65fr) minmax(320px, 1fr);
  gap: 14px;
  min-height: 0;
  padding: 16px;
  background: #fbfbfc;
}

.setup-dr-opening-skeleton__browse,
.setup-dr-opening-skeleton__selected {
  display: grid;
  align-content: start;
  gap: 14px;
  min-height: 0;
  padding: 14px 12px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
  background: var(--color-card-bg, #fff);
}

.setup-dr-opening-skeleton__browse span,
.setup-dr-opening-skeleton__selected span {
  width: 140px;
  height: 14px;
}

.setup-dr-opening-skeleton__input-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 54px;
  gap: 8px;
  align-items: center;
}

.setup-dr-opening-skeleton__input-row i,
.setup-dr-opening-skeleton__input-row b {
  height: 34px;
  border-radius: 8px;
}

.setup-dr-opening-skeleton__browse em {
  width: 160px;
  height: 14px;
}

.setup-dr-opening-skeleton__tree-box,
.setup-dr-opening-skeleton__selected-box {
  display: grid;
  align-content: center;
  justify-items: center;
  gap: 10px;
  min-height: 124px;
  border: 1px dashed rgba(203, 213, 225, 0.9);
  border-radius: 8px;
}

.setup-dr-opening-skeleton__tree-box small,
.setup-dr-opening-skeleton__selected-box i {
  width: min(220px, 70%);
  height: 12px;
}

.setup-dr-opening-skeleton__tree-box small:nth-child(2),
.setup-dr-opening-skeleton__selected-box i:nth-child(2) {
  width: min(160px, 54%);
}

.setup-dr-opening-skeleton__tree-box small:nth-child(3) {
  width: min(112px, 42%);
}

.setup-dr-opening-skeleton__footer {
  position: fixed;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 2100;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  height: 60px;
  padding: 0 32px;
  border-top: 1px solid transparent;
  background: rgba(43, 45, 54, 0.98);
  box-shadow: 0 -8px 24px rgba(0, 0, 0, 0.12);
  backdrop-filter: blur(12px);
}

.setup-dr-opening-skeleton__footer span,
.setup-dr-opening-skeleton__footer i {
  display: block;
  height: 34px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
}

.setup-dr-opening-skeleton__footer span {
  width: 86px;
}

.setup-dr-opening-skeleton__footer i {
  width: 118px;
}

html[data-theme='light'] .setup-dr-opening-skeleton__footer {
  border-top-color: #e5e6eb;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 -4px 16px rgba(29, 33, 41, 0.06);
}

html[data-theme='dark'] .setup-dr-opening-skeleton__footer {
  border-top-color: #2a2b35;
  background: rgba(22, 22, 29, 0.96);
  box-shadow: 0 -8px 24px rgba(0, 0, 0, 0.28);
}

@keyframes setup-dr-opening-skeleton-shimmer {
  100% {
    transform: translateX(100%);
  }
}

:global(.flow-header-filter-popper) {
  padding: 0 !important;
  border: 1px solid rgba(226, 232, 240, 0.95) !important;
  border-radius: 8px !important;
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.14) !important;
}

:global(.flow-header-filter-panel) {
  padding: 10px;
}

:global(.flow-header-filter-options) {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 180px;
  overflow: auto;
  padding: 8px 0;
}

:global(.flow-header-filter-options .el-checkbox) {
  width: 100%;
  height: 28px;
  margin-right: 0;
  padding: 0 4px;
  border-radius: 5px;
}

:global(.flow-header-filter-options .el-checkbox:hover) {
  background: rgb(248 250 252);
}

:global(.flow-header-filter-options .el-checkbox .el-checkbox__label) {
  font-size: 12px;
}

:global(.flow-header-filter-empty) {
  display: flex;
  justify-content: center;
  padding: 14px 4px;
  color: rgb(148 163 184);
  font-size: 0.8125rem;
}

:global(.flow-header-filter-actions) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid rgba(226, 232, 240, 0.9);
  padding-top: 8px;
}

.dp-flow-filter-drawer :deep(.el-drawer__body) {
  background: var(--el-bg-color, #fff);
}

.flow-filter-drawer {
  min-height: 100%;
}

.flow-filter-drawer__body {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 0;
}

.flow-filter-section {
  padding: 0;
}

.flow-filter-section--divided {
  border-top: 1px solid #e5e6eb;
  padding-top: 24px;
}

.flow-filter-section h3 {
  margin: 0 0 12px;
  color: rgb(51 65 85);
  font-size: 0.875rem;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.flow-filter-nas-proxy-alert {
  width: 100%;
  margin: 0 0 12px;
  border-color: var(--color-warning-border) !important;
  border-radius: 6px !important;
  background: var(--color-warning-light) !important;
}

.flow-filter-nas-proxy-alert :deep(.el-alert__content) {
  min-width: 0;
}

.flow-filter-nas-proxy-alert :deep(.el-alert__title),
.flow-filter-nas-proxy-alert :deep(.el-alert__description) {
  color: var(--color-warning);
  font-size: 13px;
  line-height: 1.55;
}

.flow-filter-nas-proxy-alert :deep(.el-alert__icon) {
  color: var(--color-warning);
}

.flow-filter-form {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
}

.flow-filter-form :deep(.el-form-item) {
  display: grid;
  grid-template-columns: minmax(108px, 30%) minmax(0, 1fr);
  align-items: center;
  margin-bottom: 0;
}

.flow-filter-form :deep(.el-form-item__label) {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  height: auto;
  min-height: 32px;
  padding: 0 8px 0 0;
  line-height: 1.35;
  text-align: left;
}

.flow-filter-form :deep(.el-form-item__content) {
  min-width: 0;
}

.flow-filter-form :deep(.el-input),
.flow-filter-form :deep(.el-select),
.flow-filter-form :deep(.el-date-editor) {
  width: 100%;
}

.flow-filter-control {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.flow-filter-control :deep(.el-input),
.flow-filter-control :deep(.el-select) {
  flex: 1 1 auto;
  min-width: 0;
}

.flow-filter-control :deep(.el-input__wrapper),
.flow-filter-control :deep(.el-select__wrapper) {
  background: rgb(248 250 252);
  box-shadow: 0 0 0 1px rgb(226 232 240) inset;
}

.flow-filter-control :deep(.el-input__wrapper.is-focus),
.flow-filter-control :deep(.el-select__wrapper.is-focused) {
  background: #fff;
}

.flow-filter-control__clear {
  flex: 0 0 32px;
  width: 32px;
  height: 32px;
  padding: 0;
  color: rgb(100 116 139);
  border-color: rgb(203 213 225);
  background: rgb(248 250 252);
}

.flow-filter-control__clear:not(:disabled):hover {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
}

.flow-filter-drawer__footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  width: 100%;
}

@media (max-width: 639.98px) {
  .flow-filter-form :deep(.el-form-item) {
    grid-template-columns: minmax(0, 1fr);
    gap: 4px;
  }

  .flow-filter-form :deep(.el-form-item__label) {
    min-height: auto;
    padding-right: 0;
  }
}

.source-table--flow-pick :deep(.el-table__body),
.source-table--flow-pick :deep(.el-table__header) {
  width: 100% !important;
}

.dp-create-tree-shell {
  border: 1px solid rgba(203, 213, 225, 0.9);
  border-radius: 10px;
  padding: 12px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.92) 0%, rgba(248, 250, 252, 0.96) 100%);
}

.dp-create-source-layout {
  --dp-create-source-pane-height: 520px;
}

.dp-create-source-pane {
  display: flex;
  flex: 1 1 0;
  flex-direction: column;
  height: var(--dp-create-source-pane-height);
  min-height: var(--dp-create-source-pane-height);
  max-height: var(--dp-create-source-pane-height);
  overflow: hidden;
}

.dp-create-source-window {
  flex: 1 1 auto;
  height: auto;
  min-height: 0;
}

.recovery-entry-panel {
  padding-top: 4px;
  overflow: visible;
}

.recovery-entry-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(260px, 1fr));
  gap: 12px;
  width: 100%;
  overflow: visible;
}

.recovery-entry-card {
  position: relative;
  display: flex !important;
  align-items: center !important;
  height: auto !important;
  min-height: 92px;
  padding: 12px 14px !important;
  border-radius: 8px !important;
  border-color: rgba(203, 213, 225, 0.95) !important;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(248, 250, 252, 0.92) 100%);
  overflow: hidden;
  isolation: isolate;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.72),
    0 1px 2px rgba(15, 23, 42, 0.04);
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    background 0.18s ease,
    transform 0.18s ease;
}

.recovery-entry-card::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background: linear-gradient(125deg, transparent 58%, rgba(255, 255, 255, 0.2) 73%, transparent 88%);
  opacity: 0;
  transition: opacity 0.22s ease;
}

.recovery-entry-card::after {
  content: '';
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: 0;
  z-index: 1;
  height: 3px;
  border-radius: 999px 999px 0 0;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--color-primary) 12%, transparent), transparent);
  opacity: 0;
  transition: opacity 0.18s ease, background 0.18s ease;
}

.recovery-entry-card :deep(.el-radio__input) {
  display: none;
}

.recovery-entry-card :deep(.el-radio__label) {
  position: relative;
  z-index: 2;
  display: flex;
  flex: 1 1 auto;
  min-height: 100%;
  min-width: 0;
  align-items: center;
  gap: 11px;
  padding-left: 0;
  white-space: normal;
}

.recovery-entry-card__icon {
  display: inline-flex;
  width: 36px;
  height: 36px;
  flex: 0 0 36px;
  align-items: center;
  justify-content: center;
  align-self: center;
  border: 1px solid color-mix(in srgb, rgb(20 184 166) 28%, rgb(203 213 225));
  border-radius: 8px;
  background: color-mix(in srgb, rgb(20 184 166) 8%, white);
  color: rgb(13 148 136);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
  transition:
    background-color 0.18s ease,
    border-color 0.18s ease,
    color 0.18s ease,
    transform 0.18s ease;
}

.recovery-entry-card__icon--manual {
  border-color: color-mix(in srgb, rgb(20 184 166) 28%, rgb(203 213 225));
  background: color-mix(in srgb, rgb(20 184 166) 8%, white);
  color: rgb(13 148 136);
}

.recovery-entry-card:hover {
  border-color: color-mix(in srgb, var(--color-primary) 38%, transparent) !important;
  background: rgba(255, 255, 255, 0.96);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.82),
    0 8px 16px color-mix(in srgb, var(--color-primary) 8%, transparent);
  transform: translateY(-1px);
}

.recovery-entry-card:hover::after {
  opacity: 1;
}

.recovery-entry-card:hover .recovery-entry-card__icon {
  border-color: color-mix(in srgb, rgb(20 184 166) 40%, rgb(203 213 225));
  background: color-mix(in srgb, rgb(20 184 166) 12%, white);
  transform: translateY(-1px);
}

.recovery-entry-card.is-checked {
  border-color: var(--color-primary) !important;
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--color-primary) 14%, white) 0%,
    color-mix(in srgb, var(--color-primary) 7%, white) 100%
  );
  box-shadow:
    0 0 0 1px color-mix(in srgb, var(--color-primary) 8%, transparent) inset,
    0 8px 18px color-mix(in srgb, var(--color-primary) 12%, transparent);
}

.recovery-entry-card.is-checked::before,
.recovery-entry-card.is-checked::after {
  opacity: 1;
}

.recovery-entry-card.is-checked .recovery-entry-card__icon {
  border-color: color-mix(in srgb, var(--color-primary) 44%, rgb(203 213 225));
  background: var(--color-primary);
  color: #fff;
}

.recovery-entry-card__body {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 5px;
}

.recovery-entry-card__title {
  font-size: 14px;
  font-weight: 700;
  line-height: 1.35;
  color: rgb(15 23 42);
}

.recovery-entry-card__desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.38;
  color: rgb(100 116 139);
}

.recovery-entry-card.is-checked .recovery-entry-card__desc {
  color: rgb(71 85 105);
}

.recovery-plan-preview-table {
  width: 100%;
  border-radius: var(--radius-card, 10px);
  overflow: hidden;
}

.recovery-plan-empty-state {
  display: flex;
  min-height: 96px;
  align-items: center;
  justify-content: center;
  padding: 18px;
  border: 1px dashed rgba(203, 213, 225, 0.95);
  border-radius: var(--radius-card, 10px);
  background: rgba(248, 250, 252, 0.78);
  color: rgb(100 116 139);
  font-size: 13px;
  font-weight: 600;
  text-align: center;
}

.recovery-plan-missing-cell {
  align-items: flex-start;
  gap: 6px;
}

.recovery-plan-missing-cell__action {
  margin-left: -7px;
  height: auto;
  min-height: 24px;
  padding: 0 7px;
  font-weight: 650;
}

.recovery-manual-inline-layout {
  min-width: 0;
  padding-top: 0;
  overflow: visible;
}

:deep(.recovery-manual-inline-layout__steps.wizard-steps) {
  width: 214px;
  padding-top: 8px;
}

.recovery-manual-inline-layout__steps :deep(.wizard-steps__label) {
  font-size: 13px;
}

.recovery-manual-inline-layout__steps :deep(.wizard-steps__connector) {
  height: 22px;
  min-height: 22px;
  flex-basis: 22px;
}

.recovery-manual-inline-layout__content {
  display: flex;
  min-height: 0;
  min-width: 0;
  flex: 1 1 auto;
  flex-direction: column;
  overflow: hidden;
}

.recovery-manual-inline-layout__main {
  --create-backup-primary: var(--color-primary);
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--create-backup-primary) 55%, transparent);
  border-radius: 8px;
  background: rgb(255 255 255);
  box-shadow:
    inset 3px 0 0 color-mix(in srgb, var(--create-backup-primary) 85%, transparent),
    0 8px 20px rgba(15, 23, 42, 0.04);
}

.recovery-manual-table-shell {
  flex: 1 1 auto;
  max-width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.recovery-manual-table {
  width: 100%;
}

.recovery-manual-inline-layout__content > .dp-wizard-pane {
  display: flex;
  min-height: 0;
  flex: 1 1 auto;
  flex-direction: column;
  overflow: hidden;
}

.recovery-manual-table :deep(.el-table__inner-wrapper::before) {
  display: none;
}

.recovery-snapshot-option {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.recovery-snapshot-option.is-disabled {
  color: #94a3b8;
}

.recovery-snapshot-option__label {
  min-width: 0;
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recovery-snapshot-option__info {
  flex: 0 0 auto;
  color: #64748b;
}

.recovery-snapshot-load-more {
  display: flex;
  width: 100%;
  min-height: 30px;
  align-items: center;
  justify-content: center;
  border: 0;
  background: transparent;
  color: #2563eb;
  font-size: 13px;
  cursor: pointer;
}

.recovery-snapshot-load-more:hover {
  color: #1d4ed8;
}

.recovery-plan-preview-table :deep(.el-table__cell) {
  vertical-align: middle;
}

.recovery-plan-preview-table :deep(.el-table__cell .cell) {
  display: flex;
  align-items: center;
  min-height: 100%;
}

.recovery-plan-preview-table :deep(.el-table__cell.is-right .cell) {
  justify-content: flex-end;
}

.recovery-plan-preview-table__snapshot-select {
  width: 100%;
}

.recovery-plan-mapping-list {
  display: flex;
  width: 100%;
  min-width: 0;
  flex-direction: column;
  gap: 7px;
}

.recovery-plan-mapping-list__name {
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 650;
  line-height: 18px;
}

.recovery-plan-mapping-line {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) max-content;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: rgb(51 65 85);
  font-size: 12px;
  line-height: 18px;
}

.recovery-plan-mapping-line__path {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recovery-plan-mapping-line__path--target {
  color: var(--color-primary);
  font-weight: 600;
}

.recovery-plan-mapping-line__arrow {
  color: rgb(148 163 184);
  font-size: 12px;
  font-weight: 700;
}

.recovery-plan-mapping-line__policy {
  justify-self: end;
  color: rgb(71 85 105);
  font-weight: 600;
  white-space: nowrap;
}

.create-recovery-plan-cell {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 5px;
  cursor: default;
}

.create-recovery-plan-cell__status {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
}

.create-recovery-plan-cell__dot {
  width: 7px;
  height: 7px;
  flex: 0 0 7px;
  border-radius: 999px;
  background: rgb(148 163 184);
}

.create-recovery-plan-cell--enabled .create-recovery-plan-cell__dot {
  background: var(--color-success);
}

.create-recovery-plan-cell--pending .create-recovery-plan-cell__dot {
  background: var(--color-warning);
}

.create-recovery-plan-cell__status-label {
  min-width: 0;
  overflow: hidden;
  color: rgb(30 41 59);
  font-size: 13px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-recovery-plan-cell--disabled .create-recovery-plan-cell__status-label {
  color: rgb(71 85 105);
}

.create-recovery-plan-cell__primary,
.create-recovery-plan-cell__policy,
.create-recovery-plan-cell__meta {
  min-width: 0;
  overflow: hidden;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recovery-plan-missing-cell .create-recovery-plan-cell__meta {
  overflow: visible;
  text-overflow: unset;
  white-space: normal;
}

.create-recovery-plan-cell__policy {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: rgb(51 65 85);
  font-weight: 650;
}

.create-recovery-plan-cell__policy--skip {
  color: rgb(22 101 52);
}

.create-recovery-plan-cell__policy--overwrite {
  color: rgb(180 83 9);
}

.create-recovery-plan-cell__policy--pending {
  color: var(--el-color-danger);
}

.create-recovery-plan-cell__policy-icon {
  flex: 0 0 auto;
}

.create-recovery-plan-cell__policy-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-recovery-plan-cell__mappings {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.create-recovery-plan-mapping {
  display: grid;
  min-width: 0;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
  gap: 6px;
  color: rgb(71 85 105);
  font-size: 12px;
  line-height: 1.45;
}

.create-recovery-plan-mapping--more {
  display: flex;
  justify-content: center;
  border: 1px dashed rgba(148, 163, 184, 0.45);
  border-radius: 7px;
  background: rgba(248, 250, 252, 0.72);
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 650;
}

.create-recovery-plan-mapping__endpoint {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 5px;
}

.create-recovery-plan-mapping__icon {
  flex: 0 0 auto;
  color: rgb(71 85 105);
}

.create-recovery-plan-mapping__endpoint--snapshot .create-recovery-plan-mapping__icon {
  color: var(--color-primary);
}

.create-recovery-plan-mapping__endpoint--dir .create-recovery-plan-mapping__icon,
.create-recovery-plan-mapping__endpoint--target .create-recovery-plan-mapping__icon {
  color: #d97706;
}

.create-recovery-plan-mapping__endpoint--file .create-recovery-plan-mapping__icon {
  color: #2563eb;
}

.create-recovery-plan-mapping__text {
  display: inline-block;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-recovery-plan-mapping__arrow {
  color: rgb(148 163 184);
  font-size: 11px;
  font-weight: 700;
}

.create-recovery-plan-mapping__pending {
  grid-column: 1 / -1;
  min-width: 0;
  overflow: hidden;
  color: rgb(180 83 9);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-recovery-plan-cell--review .create-recovery-plan-cell__policy {
  overflow: visible;
  white-space: normal;
}

.create-recovery-plan-cell--review .create-recovery-plan-cell__policy-text,
.create-recovery-plan-cell--review .create-recovery-plan-mapping__text,
.create-recovery-plan-cell--review .create-recovery-plan-mapping__pending {
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

.create-recovery-plan-cell--review {
  gap: 8px;
}

.create-recovery-plan-cell--review .create-recovery-plan-mapping {
  gap: 8px;
  padding: 6px 0;
  border-top: 1px solid rgba(226, 232, 240, 0.86);
}

:global(.create-recovery-plan-tooltip) {
  max-width: min(560px, calc(100vw - 48px));
}

:global(.create-recovery-plan-tooltip--wide) {
  max-width: min(680px, calc(100vw - 48px));
}

:global(.create-recovery-plan-tooltip__content) {
  display: flex;
  max-width: 100%;
  flex-direction: column;
  gap: 8px;
}

:global(.create-recovery-plan-tooltip__policy) {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 5px;
  color: rgb(51 65 85);
  font-size: 12px;
  font-weight: 700;
  line-height: 1.45;
}

:global(.create-recovery-plan-tooltip__policy--skip) {
  color: rgb(22 101 52);
}

:global(.create-recovery-plan-tooltip__policy--overwrite) {
  color: rgb(180 83 9);
}

:global(.create-recovery-plan-tooltip__policy--pending) {
  color: var(--el-color-danger);
}

:global(.create-recovery-plan-tooltip__mapping .create-recovery-plan-mapping__text) {
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.recovery-dest-preview-table__dest-cell {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.recovery-dest-preview-table__dest-text {
  min-width: 0;
  flex: 1;
}

.recovery-dest-preview-table__edit {
  padding-left: 4px;
  padding-right: 4px;
}

.recovery-dest-config-table {
  width: 100%;
}

.recovery-dest-config-table :deep(.el-table__header .cell) {
  white-space: nowrap;
}

.recovery-dest-config-table :deep(.el-table__expanded-cell),
.recovery-confirm-source-table :deep(.el-table__expanded-cell) {
  padding: 16px;
  background: rgba(248, 250, 252, 0.76);
}

.recovery-dest-config-table :deep(.recovery-dest-config-expand-column),
.recovery-confirm-source-table :deep(.recovery-dest-config-expand-column) {
  text-align: center;
}

.recovery-dest-config-table :deep(.recovery-dest-config-expand-column .cell),
.recovery-confirm-source-table :deep(.recovery-dest-config-expand-column .cell) {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}

.recovery-dest-config-table :deep(.el-table__expand-icon),
.recovery-confirm-source-table :deep(.el-table__expand-icon) {
  width: 32px;
  height: 32px;
  color: var(--el-text-color-secondary);
}

.recovery-dest-config-table :deep(.el-table__expand-icon .el-icon),
.recovery-confirm-source-table :deep(.el-table__expand-icon .el-icon) {
  font-size: 18px;
}

.recovery-dest-config-table :deep(.el-table__expand-icon:hover),
.recovery-confirm-source-table :deep(.el-table__expand-icon:hover) {
  color: var(--el-color-primary);
}

.recovery-dir-source-toggle {
  display: flex;
  width: 100%;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  border: 0;
  padding: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.recovery-dir-source-toggle:hover .recovery-plan-preview-table__source {
  color: var(--el-color-primary);
}

.recovery-dir-source-toggle:hover .recovery-source-summary__name,
.recovery-dir-source-toggle:focus-visible .recovery-source-summary__name {
  color: var(--el-color-primary);
}

.recovery-dir-source-toggle:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--color-primary) 42%, transparent);
  outline-offset: 3px;
  border-radius: 8px;
}

.recovery-dest-config-detail {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  margin-left: 22px;
  padding: 12px 14px 14px 28px;
  border-left: 4px solid color-mix(in srgb, var(--color-info) 68%, var(--color-info-border));
  border-radius: 0 8px 8px 0;
  background: linear-gradient(180deg, rgba(241, 245, 249, 0.72), rgba(248, 250, 252, 0.86));
}

.recovery-dest-config-detail__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.recovery-dest-nested-table {
  width: 100%;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  overflow: hidden;
}

.recovery-dest-nested-table__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  justify-content: flex-end;
}

.recovery-dest-nested-table :deep(.el-table__cell .cell) {
  display: block;
}

.recovery-dir-selection-labels,
.recovery-dir-selection-row,
.recovery-dir-selection-add-wrap {
  display: grid;
  grid-template-columns:
    minmax(180px, 1fr)
    minmax(200px, 1.08fr)
    72px !important;
  column-gap: 10px;
  row-gap: 8px;
}

.recovery-dir-selection-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 8px 22px 0;
}

.recovery-dir-selection-labels {
  align-items: center;
  border-bottom: 1px solid rgba(226, 232, 240, 0.95);
  padding: 0 12px 6px;
  background: transparent;
  color: rgb(71 85 105);
  font-size: 12px;
  font-weight: 700;
}

.create-recovery-required-label {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 3px;
}

.create-recovery-required-mark {
  color: var(--color-error) !important;
  font-weight: 800;
  line-height: 1;
}

.recovery-dir-selection-row {
  align-items: start;
  padding: 4px 0;
}

.recovery-dir-selection-row .create-recovery-dir-plan-field::before {
  display: none !important;
  content: none !important;
}

.recovery-dir-selection-row .create-recovery-path-input {
  width: 100%;
  min-width: 0;
  border-radius: 8px;
}

.recovery-dir-selection-row .create-recovery-path-input :deep(.el-input__wrapper),
.recovery-dir-selection-row .create-recovery-path-input :deep(.el-input-group__append) {
  background: #ffffff;
}

.recovery-dir-selection-add-wrap {
  align-items: center;
  padding-top: 2px;
}

.recovery-dir-selection-add-row {
  display: flex;
  grid-column: 1 / span 2;
  width: 70%;
  max-width: 100%;
  min-height: 32px;
  align-items: center;
  justify-self: center;
  justify-content: center;
  gap: 8px;
  margin: 0;
  border: 1px dashed rgba(148, 163, 184, 0.8);
  border-radius: 8px;
  background: rgba(248, 250, 252, 0.72);
  color: var(--color-primary);
  font-size: 13px;
  font-weight: 600;
}

.recovery-dir-selection-add-row:hover {
  border-color: var(--color-primary);
  background: rgba(239, 246, 255, 0.82);
}

.recovery-target-host-control {
  display: flex;
  width: 100%;
  min-width: 0;
  align-items: center;
  gap: 8px;
}

.recovery-target-host-control__source-wrap {
  display: inline-flex;
  flex: 0 0 auto;
}

.recovery-target-host-control__source {
  min-width: 168px;
  justify-content: center;
}

.recovery-target-host-control__source.is-selected {
  border-color: color-mix(in srgb, var(--color-primary) 72%, transparent);
  background: color-mix(in srgb, var(--color-primary) 14%, white);
  color: var(--color-primary);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--color-primary) 16%, transparent);
}

.recovery-target-host-control__select {
  min-width: 0;
  flex: 1 1 auto;
}

.recovery-target-host-control__readonly {
  min-width: 0;
  flex: 1 1 auto;
  padding: 5px 0;
}

.recovery-conflict-policy-select :deep(.el-select__wrapper) {
  min-height: 34px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--color-primary) 7%, #ffffff);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--color-primary) 34%, rgb(203 213 225)) inset;
}

.recovery-conflict-policy-cell {
  display: flex;
  min-width: 0;
  flex-direction: column;
  justify-content: center;
  min-height: 42px;
}

.recovery-conflict-policy-select :deep(.el-select__wrapper.is-focused) {
  box-shadow: 0 0 0 1px var(--color-primary) inset;
}

.recovery-conflict-policy-select--invalid :deep(.el-select__wrapper) {
  background: var(--color-error-light);
  box-shadow: 0 0 0 1px var(--color-error) inset;
}

.recovery-conflict-policy-select--skip :deep(.el-select__placeholder),
.recovery-conflict-policy-select--skip :deep(.el-select__placeholder span),
.recovery-conflict-policy-select--skip :deep(.el-select__selected-item),
.recovery-conflict-policy-select--skip :deep(.el-select__selected-item span) {
  color: rgb(22 101 52);
}

.recovery-conflict-policy-select--overwrite :deep(.el-select__placeholder),
.recovery-conflict-policy-select--overwrite :deep(.el-select__placeholder span),
.recovery-conflict-policy-select--overwrite :deep(.el-select__selected-item),
.recovery-conflict-policy-select--overwrite :deep(.el-select__selected-item span) {
  color: rgb(180 83 9);
}

.recovery-conflict-policy-option {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
  color: rgb(51 65 85);
  font-size: 12px;
  font-weight: 700;
}

.recovery-conflict-policy-option span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recovery-conflict-policy-option--skip {
  color: rgb(22 101 52);
}

.recovery-conflict-policy-option--overwrite {
  color: rgb(180 83 9);
}

.recovery-target-node-option {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
  padding: 5px 0;
  line-height: 1.3;
}

.recovery-target-node-option__main {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.recovery-target-node-option__label {
  min-width: 0;
  overflow: hidden;
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recovery-target-node-option__meta {
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 16px;
  white-space: normal;
  word-break: break-all;
}

.recovery-target-node-option__loading {
  display: flex;
  justify-content: center;
  width: 100%;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 32px;
}

:global(.recovery-target-node-select-popper) {
  min-width: 480px !important;
}

:global(.recovery-target-node-select-popper .el-select-dropdown__item) {
  height: auto;
  min-height: 52px;
  padding-top: 6px;
  padding-bottom: 6px;
  line-height: normal;
}

:global(.recovery-target-node-select-popper .el-select-dropdown__wrap) {
  max-height: 320px;
}

.recovery-dir-picker-options {
  display: grid;
  max-height: 320px;
  min-width: 0;
  gap: 6px;
  overflow-y: auto;
}

.recovery-dir-picker-option {
  display: flex;
  min-width: 0;
  width: 100%;
  align-items: center;
  gap: 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  padding: 8px 10px;
  color: rgb(51 65 85);
  font-size: 13px;
  line-height: 18px;
  text-align: left;
}

.recovery-dir-picker-option:hover,
.recovery-dir-picker-option.is-active {
  border-color: rgba(59, 130, 246, 0.24);
  background: rgba(239, 246, 255, 0.9);
  color: rgb(30 64 175);
}

.recovery-dir-picker-option__icon {
  flex: 0 0 auto;
}

.recovery-dir-picker-option span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recovery-dir-picker-empty {
  padding: 10px;
  color: rgb(100 116 139);
  font-size: 13px;
  line-height: 18px;
}

.create-source-dir-preview {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.create-source-dir-preview-empty {
  color: var(--el-text-color-placeholder);
  font-size: 12px;
}

.create-source-dir-preview__item {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  gap: 6px;
  color: var(--el-text-color-regular);
  font-size: 13px;
  line-height: 20px;
}

.create-source-dir-preview__count {
  color: var(--el-text-color-primary);
  font-size: 13px;
  font-weight: 600;
  line-height: 20px;
}

.create-source-dir-preview__icon {
  flex: 0 0 auto;
  color: var(--el-text-color-secondary);
}

.create-source-dir-preview__path {
  min-width: 0;
  overflow: visible;
  text-overflow: initial;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: break-word;
}

.create-source-dir-preview__more {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 18px;
}

.recovery-mapping-line {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
  border-radius: 7px;
  color: rgb(51 65 85);
  font-size: 12px;
  line-height: 18px;
  transition:
    background-color 0.16s ease,
    color 0.16s ease;
}

.recovery-mapping-line--preview {
  padding: 3px 4px;
  margin-inline: -4px;
}

.recovery-mapping-line--preview:hover,
.recovery-entry-preview-tooltip__line:hover {
  background: color-mix(in srgb, var(--color-primary) 7%, #ffffff);
}

.recovery-mapping-line__endpoint {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 5px;
}

.recovery-mapping-line__endpoint--snapshot {
  color: var(--color-primary);
}

.recovery-mapping-line__endpoint--dir {
  color: #d97706;
}

.recovery-mapping-line__endpoint--file {
  color: #2563eb;
}

.recovery-mapping-line__endpoint--target {
  color: #d97706;
}

.recovery-mapping-line__icon {
  flex: 0 0 auto;
}

.create-dir-row__icon--folder {
  color: #d97706;
}

.create-dir-row__icon--file {
  color: #2563eb;
}

.recovery-mapping-line__text {
  min-width: 0;
  overflow: hidden;
  color: rgb(51 65 85);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recovery-mapping-line__arrow {
  flex: 0 0 auto;
  color: rgb(148 163 184);
  font-size: 11px;
  font-weight: 700;
}

.create-source-expand-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.create-source-expand-btn__icon {
  transition: transform 0.18s ease;
}

.create-source-expand-btn__icon--open {
  transform: rotate(180deg);
}

.recovery-entry-preview-trigger {
  width: 100%;
  min-width: 0;
  cursor: default;
}

.recovery-entry-preview-tooltip__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: min(520px, 72vw);
}

.recovery-entry-preview-tooltip__line {
  padding: 4px 6px;
}

.dp-rec-dest-drawer__tree {
  min-height: 280px;
  max-height: min(52vh, 420px);
}

.recovery-plan-preview-table__source-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
  width: 100%;
}

.recovery-source-summary {
  display: flex;
  width: 100%;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
}

.recovery-source-summary__head {
  display: flex;
  width: 100%;
  min-width: 0;
  align-items: center;
  gap: 8px;
}

.recovery-source-summary__platform {
  display: inline-flex;
  width: 20px;
  height: 20px;
  flex: 0 0 20px;
  align-items: center;
  justify-content: center;
  color: rgb(71 85 105);
}

.recovery-source-summary__platform :deep(.agent-platform-brand-icon) {
  width: 18px;
  height: 18px;
}

.recovery-source-summary__name {
  display: block;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 650;
  line-height: 20px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recovery-source-summary__type {
  min-height: 20px;
  flex: 0 0 auto;
  line-height: 18px;
}

.recovery-source-summary__meta {
  display: flex;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.recovery-source-summary__ip,
.recovery-source-summary__snapshot {
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recovery-source-summary__snapshot {
  color: rgb(71 85 105);
}

.recovery-dir-source-toggle .recovery-source-summary {
  text-align: left;
}

.recovery-plan-preview-table__source-snapshot {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.25rem;
  white-space: nowrap;
}

.recovery-plan-preview-table :deep(tbody tr td:first-child .cell) {
  align-items: flex-start;
}

.recovery-plan-preview-table__source {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 0.875rem;
  font-weight: 600;
  color: rgb(15 23 42);
  white-space: nowrap;
}

.recovery-plan-preview-table__endpoint-ip {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--color-primary);
  font-size: 0.8125rem;
  font-weight: 600;
  line-height: 1.25rem;
  white-space: nowrap;
}

.recovery-plan-preview-table__paths {
  padding-left: 1rem;
}

.create-recovery-plan-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.create-recovery-plan-card {
  border: 1px solid rgba(203, 213, 225, 0.9);
  border-radius: 12px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(248, 250, 252, 0.98) 100%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.72),
    0 8px 20px rgba(15, 23, 42, 0.04);
}

.create-recovery-plan-card__header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid rgba(226, 232, 240, 0.95);
}

.create-recovery-plan-card__body {
  display: flex;
  flex-direction: column;
  gap: 0;
  padding: 16px;
}

.create-recovery-plan-section {
  padding: 16px 0;
  border-top: 1px solid rgba(226, 232, 240, 0.95);
}

.create-recovery-plan-section:first-child {
  padding-top: 0;
  border-top: 0;
}

.create-recovery-plan-section:last-child {
  padding-bottom: 0;
}

.create-recovery-plan-field {
  min-width: 0;
}

.create-recovery-plan-field__label {
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 650;
  color: rgb(71 85 105);
}

.create-recovery-scope-options {
  grid-template-columns: repeat(2, minmax(220px, 1fr));
}

.create-recovery-plan-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 16px;
}

.create-recovery-destination-grid {
  display: grid;
  grid-template-columns: minmax(260px, 0.82fr) minmax(320px, 1.18fr);
  gap: 14px 16px;
  align-items: start;
}

.create-recovery-target-host-stack {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 10px;
}

.create-recovery-original-host-btn {
  align-self: flex-start;
  min-width: 96px;
  border-radius: 8px;
}

.create-recovery-original-host-btn.is-active {
  box-shadow: 0 3px 8px rgba(37, 99, 235, 0.18);
}

.create-recovery-target-dir-field .create-recovery-path-input {
  margin-top: 42px;
}

.create-recovery-path-input {
  position: relative;
  width: 100%;
  min-width: 0;
}

.create-recovery-path-input :deep(.el-input__wrapper) {
  border-top-right-radius: 0;
  border-bottom-right-radius: 0;
}

.create-recovery-path-input :deep(.el-input-group__append) {
  display: inline-flex;
  width: 40px;
  min-width: 40px;
  min-height: 32px;
  align-items: stretch;
  justify-content: stretch;
  overflow: hidden;
  padding: 0;
  border-radius: 0 var(--el-border-radius-base) var(--el-border-radius-base) 0;
  background: #fff;
  box-shadow: 0 0 0 1px var(--el-border-color) inset;
}

.create-recovery-path-input--pending :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px rgba(245, 158, 11, 0.78) inset;
}

.create-recovery-path-input--invalid :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}

.create-recovery-path-input--invalid :deep(.el-input-group__append) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}

.create-recovery-path-input__checking {
  font-size: 11px;
  color: rgb(100 116 139);
  white-space: nowrap;
}

.create-recovery-path-input__type-icon {
  color: rgb(100 116 139);
}

.create-recovery-path-input__type-icon.create-dir-row__icon--snapshot {
  color: var(--color-primary);
}

.create-recovery-path-input__type-icon.create-dir-row__icon--folder {
  color: #d97706;
}

.create-recovery-path-input__type-icon.create-dir-row__icon--file {
  color: #2563eb;
}

.create-recovery-path-input :deep(.create-recovery-path-input__btn) {
  display: inline-flex;
  width: 40px;
  min-width: 40px;
  height: 32px;
  min-height: 32px;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 0;
  background: linear-gradient(180deg, rgba(239, 246, 255, 0.96), rgba(219, 234, 254, 0.86));
  color: var(--color-primary);
  padding: 0;
  box-shadow: none;
}

.create-recovery-path-input :deep(.create-recovery-path-input__btn:hover),
.create-recovery-path-input :deep(.create-recovery-path-input__btn:focus-visible) {
  background: linear-gradient(180deg, rgba(219, 234, 254, 1), rgba(191, 219, 254, 0.96));
  color: var(--color-primary);
}

.create-recovery-path-input :deep(.create-recovery-path-input__btn:active) {
  background: rgba(191, 219, 254, 1);
}

.create-recovery-path-input__error {
  position: absolute;
  z-index: 3;
  top: calc(100% + 4px);
  left: 0;
  display: flex;
  min-width: 100%;
  max-width: min(360px, calc(100vw - 48px));
  align-items: flex-start;
  justify-content: space-between;
  gap: 6px;
  margin: 0;
  padding: 4px 8px;
  border: 1px solid color-mix(in srgb, var(--el-color-danger) 38%, white);
  border-radius: 4px;
  background: #fff7f7;
  box-shadow: 0 3px 8px rgba(127, 29, 29, 0.12);
  color: var(--el-color-danger);
  font-size: 12px;
  line-height: 16px;
  overflow-wrap: anywhere;
}

.create-recovery-path-input__error-close {
  display: inline-flex;
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  align-items: center;
  justify-content: center;
  margin: 0;
  padding: 0;
  border: 0;
  border-radius: 3px;
  background: transparent;
  color: currentcolor;
  cursor: pointer;
}

.create-recovery-path-input__error-close:hover,
.create-recovery-path-input__error-close:focus-visible {
  background: rgba(220, 38, 38, 0.12);
  outline: 0;
}

.create-recovery-path-input__error > span {
  min-width: 0;
}

.create-recovery-tree-popover {
  max-height: none;
  overflow: visible;
  padding: 4px;
  border: 0;
  background: transparent;
}

.create-recovery-popover-tree {
  min-width: 100%;
}

.create-recovery-conflict-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(180px, 240px));
  gap: 12px;
}

.create-recovery-conflict-option {
  width: 100%;
  height: 40px !important;
  border-radius: 8px !important;
  display: inline-flex !important;
  align-items: center;
}

:global(.create-recovery-path-popover.el-popper) {
  padding: 10px 10px 10px 12px;
  border-radius: 10px;
}

.create-confirm-section {
  padding: 16px 0;
  border-top: 1px solid rgba(226, 232, 240, 0.95);
}

.create-confirm-section--batch {
  padding-top: 0;
  border-top: 0;
}

.create-confirm-section__head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px 14px;
  margin-bottom: 12px;
}

.create-confirm-section__title {
  margin: 0;
  font-size: 14px;
  font-weight: 650;
  color: rgb(15 23 42);
}

.create-confirm-section__desc {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.55;
  color: rgb(100 116 139);
}

.create-confirm-batch-row {
  display: grid;
  grid-template-columns: minmax(240px, 1fr) auto;
  gap: 10px;
  align-items: center;
}

.create-confirm-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.create-confirm-card {
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.96);
  padding: 14px;
}

.create-confirm-card__name {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
}

.create-confirm-card__index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 999px;
  background: var(--color-info-light);
  border: 1px solid var(--color-info-border);
  color: var(--color-info);
  font-size: 12px;
  font-weight: 700;
  font-feature-settings: 'tnum' 1;
}

.create-confirm-card__meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 14px;
  margin: 0;
}

.create-confirm-card__meta div {
  min-width: 0;
}

.create-confirm-card__meta dt {
  margin: 0 0 4px;
  font-size: 12px;
  font-weight: 650;
  color: rgb(100 116 139);
}

.create-confirm-card__meta dd {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: rgb(30 41 59);
  word-break: break-word;
}

.dp-create-tree-shell--fill {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  flex-direction: column;
}

.dp-create-source-pane--left .dp-create-tree-shell {
  flex: 1 1 auto;
  min-height: 0;
}

.dp-create-source-pane .dp-create-source-window {
  flex: 1 1 auto;
  min-height: 0;
}

.dp-create-tree-title-hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.55;
  color: rgb(100 116 139);
}

.dp-create-tree-shell__warn {
  margin: 0 0 10px;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid rgba(245, 158, 11, 0.22);
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.94) 0%, rgba(254, 243, 199, 0.78) 100%);
  font-size: 13px;
  color: rgb(180 83 9);
}

.dp-create-tree-shell__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 120px;
  padding: 16px;
  color: rgb(100 116 139);
  font-size: 13px;
  background: rgba(248, 250, 252, 0.88);
  border-radius: 8px;
  border: 1px dashed rgba(148, 163, 184, 0.35);
}

.source-dir-tree {
  max-height: 260px;
  min-height: 0;
  overflow-y: auto;
  overflow-x: auto;
  border: 0;
  border-radius: 0;
  padding: 2px 0;
  background: transparent;
  box-shadow: none;
}

.source-dir-tree.hfl-dir-tree--fill {
  flex: 1 1 auto;
  max-height: none;
}

.source-dir-tree.hfl-dir-tree--tall {
  max-height: min(48vh, 360px);
}

.dp-create-source-pane--left .source-dir-tree {
  flex: 1 1 auto;
  min-height: 0;
  max-height: none;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  padding: 0;
}

.dp-added-dir-shell {
  display: flex;
  min-height: 0;
  flex-direction: column;
}

.dp-added-dir-empty {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  align-items: center;
  justify-content: center;
}

.dp-added-dir-list {
  flex: 1 1 auto;
  min-height: 0;
  max-height: none;
  overflow-y: auto;
}


@media (max-width: 1023px) {
  .dp-create-source-layout {
    --dp-create-source-pane-height: 420px;
  }
}

@media (max-width: 767.98px) {
  .recovery-entry-options {
    grid-template-columns: 1fr;
  }

  :deep(.recovery-manual-inline-layout__steps.wizard-steps) {
    width: 100%;
  }
}


.source-dir-tree :deep(.el-tree-node__content) {
  height: auto;
  min-height: 30px;
  padding-top: 1px;
  padding-bottom: 1px;
  border-radius: 4px;
}

.source-dir-tree :deep(.el-tree-node__content:hover) {
  background: rgba(226, 232, 240, 0.5);
}

.source-dir-tree :deep(.el-tree-node.is-current > .el-tree-node__content) {
  background: linear-gradient(180deg, rgba(219, 234, 254, 0.88) 0%, rgba(191, 219, 254, 0.72) 100%);
}

.source-dir-tree :deep(.el-checkbox) {
  margin-right: 6px;
}

.create-tree-node-content {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-width: 0;
}

.create-tree-node-content__icon {
  flex-shrink: 0;
  color: #d97706;
}

.create-tree-node-content__icon.create-dir-row__icon--snapshot,
.tree-node-content__icon.create-dir-row__icon--snapshot {
  color: var(--color-primary);
}

.create-tree-node-content__icon.create-dir-row__icon--folder,
.tree-node-content__icon.create-dir-row__icon--folder {
  color: #d97706;
}

.create-tree-node-content__icon.create-dir-row__icon--file,
.tree-node-content__icon.create-dir-row__icon--file {
  color: #2563eb;
}

.create-tree-node-content__text {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  flex-direction: column;
}

.create-tree-node-content__label {
  font-size: 13px;
  line-height: 17px;
  color: rgb(30 41 59);
}

.create-tree-node-content__path {
  font-size: 12px;
  line-height: 15px;
  color: rgb(100 116 139);
  word-break: break-word;
}

.dp-added-dir-path {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 8px;
  min-width: 0;
}

.dp-added-dir-path__icon {
  flex-shrink: 0;
  margin-top: 2px;
  color: #d97706;
}

.dp-added-dir-path__text {
  min-width: 0;
  word-break: break-all;
  font-size: 12px;
  line-height: 1.45;
  color: rgb(51 65 85);
}

.target-host-group-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.target-host-group {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 14px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(248, 250, 252, 0.96) 100%);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}

.target-host-group__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.target-host-group__title-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
}

.target-host-group__title {
  font-size: 15px;
  font-weight: 600;
  color: rgb(15 23 42);
}

.target-batch-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  border-radius: 12px;
  background: rgba(241, 245, 249, 0.75);
  border: 1px solid rgba(226, 232, 240, 0.95);
}

.target-batch-panel__lead {
  font-size: 13px;
  font-weight: 600;
  color: rgb(51 65 85);
}

.target-batch-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.target-batch-panel__header-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.dp-create-action-btn {
  font-weight: 600;
  box-shadow: 0 6px 14px rgba(37, 99, 235, 0.14);
}

.dp-create-action-btn:hover,
.dp-create-action-btn:focus-visible {
  box-shadow: 0 8px 18px rgba(37, 99, 235, 0.18);
}

.target-picker-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(170px, 220px) auto;
  gap: 10px;
  align-items: start;
}

.target-picker-grid--row {
  grid-template-columns: minmax(0, 1fr) minmax(170px, 220px);
}

.target-picker-grid__search,
.target-picker-grid__type,
.target-picker-grid__target {
  width: 100%;
}

.target-dir-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.target-dir-card {
  display: grid;
  grid-template-columns: minmax(0, 0.92fr) minmax(0, 1.4fr);
  gap: 16px;
  padding: 14px 16px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 12px;
  background: #fff;
}

.target-dir-card__source,
.target-dir-card__picker {
  min-width: 0;
}

.target-dir-card__source {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.target-dir-card__source-body {
  min-width: 0;
}

.target-dir-card__path {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}

.target-assignment-card__meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
}

.target-assignment-card__meta-type {
  font-size: 12px;
  font-weight: 600;
  color: rgb(71 85 105);
}

.target-assignment-card__meta-location {
  font-size: 12px;
  line-height: 1.45;
  color: rgb(100 116 139);
  word-break: break-all;
}

.target-option {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 2px 0;
}

.target-option__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.target-option__name {
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  color: rgb(30 41 59);
}

.target-option__location {
  font-size: 12px;
  line-height: 1.4;
  color: rgb(100 116 139);
  word-break: break-all;
}

.policy-dir-config-head .target-batch-panel__header {
  align-items: center;
}

.policy-dir-card__source {
  align-items: center;
}

.policy-dir-card__picker {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(240px, 1.2fr);
  gap: 10px;
}

.policy-dir-card__picker .target-assignment-card__meta {
  grid-column: 1 / -1;
}

.policy-batch-grid {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(240px, 1fr) auto;
  gap: 12px;
  align-items: end;
  margin-top: 12px;
}

.policy-select-field {
  min-width: 0;
}

.policy-select-field__label {
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 650;
  color: rgb(71 85 105);
}

.policy-name-hover {
  cursor: default;
  border-bottom: 1px dotted rgb(203 213 225);
}

:global(.el-dialog.dp-add-target-dialog .el-dialog__body),
:global(.dp-add-target-dialog .el-dialog__body) {
  padding-top: 12px;
}

:global(.el-dialog.dp-add-target-dialog.create-policy-dialog),
:global(.dp-add-target-dialog.create-policy-dialog .el-dialog) {
  display: flex;
  flex-direction: column;
  max-width: calc(100vw - 32px);
  max-height: 84vh;
  margin-top: 8vh !important;
}

:global(.el-dialog.dp-add-target-dialog.create-policy-dialog .el-dialog__body),
:global(.dp-add-target-dialog.create-policy-dialog .el-dialog__body) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 22px;
}

:global(.el-dialog.dp-add-target-dialog.create-policy-dialog .el-dialog__footer),
:global(.dp-add-target-dialog.create-policy-dialog .el-dialog__footer) {
  flex: 0 0 auto;
  border-top: 1px solid rgba(226, 232, 240, 0.95);
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 -8px 18px rgba(15, 23, 42, 0.05);
}

.create-policy-dialog .policy-dialog-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.create-policy-dialog .create-kind-block {
  margin-bottom: 16px;
}

.create-policy-dialog .create-kind-block__label {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 650;
  color: rgb(71 85 105);
}

.create-policy-dialog .create-kind-block__desc {
  margin: 8px 0 0;
  font-size: 12px;
  color: rgb(100 116 139);
}

.create-policy-dialog .create-kind-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.create-policy-dialog .create-kind-card {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
  padding: 14px;
  border-radius: 10px;
  border: 1px solid rgba(203, 213, 225, 0.95);
  background: rgba(255, 255, 255, 0.92);
  text-align: left;
}

.create-policy-dialog .create-kind-card.is-active {
  border-color: rgb(29 78 216);
  background: linear-gradient(180deg, rgba(219, 234, 254, 0.88) 0%, rgba(239, 246, 255, 0.92) 100%);
}

.create-policy-dialog .create-kind-card__indicator {
  width: 10px;
  height: 10px;
  margin-top: 5px;
  flex-shrink: 0;
  border-radius: 999px;
  border: 2px solid rgba(148, 163, 184, 0.9);
}

.create-policy-dialog .create-kind-card.is-active .create-kind-card__indicator {
  border-color: rgb(29 78 216);
  background: rgb(29 78 216);
}

.create-policy-dialog .create-kind-card__body {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.create-policy-dialog .create-kind-card__name {
  font-size: 14px;
  font-weight: 650;
  color: rgb(30 41 59);
}

.create-policy-dialog .create-kind-card__sub {
  font-size: 12px;
  line-height: 1.5;
  color: rgb(100 116 139);
}

.create-policy-dialog .policy-dialog-card {
  padding: 18px 20px;
  border: 1px solid rgba(203, 213, 225, 0.9);
  border-radius: 12px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(248, 250, 252, 0.98) 100%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.72),
    0 8px 20px rgba(15, 23, 42, 0.04);
}

.create-policy-dialog .policy-dialog-card__title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 16px;
  font-size: 15px;
  font-weight: 650;
  color: rgb(15 23 42);
}

.create-policy-dialog .policy-dialog-card__title::before {
  content: '';
  width: 4px;
  height: 16px;
  border-radius: 999px;
  background: linear-gradient(180deg, #3b82f6 0%, #1d4ed8 100%);
}

.create-policy-dialog .policy-section-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.create-policy-dialog .policy-section-head__title {
  font-size: 14px;
  font-weight: 650;
  color: rgb(15 23 42);
}

.create-policy-dialog .policy-section-off-hint {
  margin: 0;
  font-size: 13px;
  color: rgb(100 116 139);
}

.create-policy-dialog .policy-section-nested {
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  background: rgba(248, 250, 252, 0.78);
}

.create-policy-dialog .policy-dialog-form--backup :deep(.el-form-item__label) {
  font-weight: 650;
  color: rgb(30 41 59);
}

.create-policy-dialog .cron-row {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}

.create-policy-dialog .cron-row__label {
  padding-top: 7px;
  font-size: 13px;
  font-weight: 650;
  color: rgb(51 65 85);
}

.create-policy-dialog .cron-row__required {
  margin-right: 3px;
  color: rgb(239 68 68);
}

.create-policy-dialog .cron-row__error {
  margin: 4px 0 0;
  font-size: 12px;
  color: rgb(239 68 68);
}

.create-policy-dialog .compression-radio-group {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.create-policy-dialog .compression-radio-group :deep(.el-radio) {
  height: auto;
  min-height: 54px;
  align-items: flex-start;
  margin-right: 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: #fff;
  border: 1px solid rgba(226, 232, 240, 0.95);
}

.create-policy-dialog .compression-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
  white-space: normal;
}

.create-policy-dialog .compression-option__title {
  font-size: 13px;
  font-weight: 650;
  color: rgb(30 41 59);
}

.create-policy-dialog .compression-option__desc {
  font-size: 12px;
  line-height: 1.45;
  color: rgb(100 116 139);
}

.create-policy-dialog .retention-tier-row,
.create-policy-dialog .toggle-row,
.create-policy-dialog .toggle-row__details {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.create-policy-dialog .retention-tier-label,
.create-policy-dialog .retention-tier-unit {
  font-size: 13px;
  color: rgb(71 85 105);
}

.create-policy-dialog .error-policy-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.create-policy-dialog .error-policy-list > li {
  display: flex;
  gap: 10px;
  padding: 12px 0;
  border-bottom: 1px solid rgba(226, 232, 240, 0.95);
}

.create-policy-dialog .error-policy-list > li.is-last {
  border-bottom: 0;
}

.create-policy-dialog .error-policy-list__title,
.create-policy-dialog .toggle-row__title {
  font-size: 13px;
  font-weight: 650;
  color: rgb(30 41 59);
}

.create-policy-dialog .error-policy-list__desc,
.create-policy-dialog .toggle-row__sub {
  margin: 2px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: rgb(100 116 139);
}

.repository-add-dialog :deep(.el-dialog) {
  max-width: calc(100vw - 32px);
  max-height: calc(var(--app-viewport-height) - 48px);
  display: flex;
  flex-direction: column;
}

.repository-add-dialog :deep(.el-dialog__body) {
  flex: 1 1 auto;
  min-height: 0;
  max-height: min(72vh, 780px);
  overflow-y: auto;
  padding: 16px 24px 20px;
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.9) 0%, rgba(255, 255, 255, 1) 100%);
}

.repository-add-dialog :deep(.el-dialog__footer) {
  flex-shrink: 0;
  padding: 14px 24px;
  border-top: 1px solid rgba(226, 232, 240, 0.95);
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 -8px 18px rgba(15, 23, 42, 0.05);
}

.dp-add-target-form :deep(.el-form-item__label) {
  font-weight: 600;
  color: rgb(51 65 85);
}

.add-target-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 14px;
}

.repository-add-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.repository-add-switcher {
  margin: 0;
}

.add-source-type-grid.repository-add-type-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.repository-add-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.repository-add-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 18px;
  align-items: start;
}

.repository-add-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.repository-add-steps {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
}

.repository-add-step {
  display: flex;
  align-items: center;
  gap: 10px;
  opacity: 0.58;
}

.repository-add-step--active {
  opacity: 1;
}

.repository-add-step--done {
  opacity: 1;
}

.repository-add-step__num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 650;
  color: rgb(100 116 139);
  background: rgb(248 250 252);
  border: 1px solid rgba(203, 213, 225, 0.95);
}

.repository-add-step--active .repository-add-step__num,
.repository-add-step--done .repository-add-step__num {
  color: #fff;
  background: linear-gradient(180deg, rgb(37 99 235) 0%, rgb(29 78 216) 100%);
  border-color: rgb(29 78 216);
}

.repository-add-step__label {
  font-size: 14px;
  font-weight: 600;
  color: rgb(30 41 59);
  white-space: nowrap;
}

.repository-add-step__connector {
  flex: 1;
  max-width: 72px;
  height: 2px;
  border-radius: 999px;
  background: rgba(203, 213, 225, 0.9);
  transition: background 0.2s ease;
}

.repository-add-step__connector.is-on {
  background: rgb(37 99 235);
}

.repository-add-card {
  border: 1px solid rgba(203, 213, 225, 0.9);
  border-radius: 12px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(248, 250, 252, 0.98) 100%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.72),
    0 8px 20px rgba(15, 23, 42, 0.04);
}

.repository-add-section {
  padding: 18px 20px 20px;
}

.repository-add-section__title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 16px;
  font-size: 14px;
  font-weight: 650;
  color: rgb(15 23 42);
}

.repository-add-section__indicator {
  width: 4px;
  height: 16px;
  border-radius: 999px;
  background: linear-gradient(180deg, #3b82f6 0%, #1d4ed8 100%);
}

.repository-add-section__subtitle {
  margin: 18px 0 12px;
  padding-top: 16px;
  border-top: 1px solid rgba(226, 232, 240, 0.95);
  font-size: 14px;
  font-weight: 650;
  color: rgb(30 41 59);
}

.repository-platform-grid,
.repository-region-grid {
  display: grid;
  gap: 10px;
}

.repository-platform-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.repository-region-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.repository-platform-btn,
.repository-region-btn {
  min-width: 0;
  border: 1px solid rgba(203, 213, 225, 0.95);
  border-radius: 10px;
  background: #fff;
  color: rgb(30 41 59);
  transition: all 0.18s ease;
}

.repository-platform-btn {
  padding: 12px 14px;
  font-size: 13px;
  font-weight: 650;
}

.repository-region-btn {
  display: flex;
  min-height: 56px;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 3px;
  padding: 10px 12px;
  text-align: left;
}

.repository-platform-btn:hover,
.repository-region-btn:hover {
  border-color: rgba(59, 130, 246, 0.38);
  box-shadow: 0 8px 16px rgba(59, 130, 246, 0.08);
}

.repository-platform-btn.is-active,
.repository-region-btn.is-active {
  border-color: rgb(29 78 216);
  background: linear-gradient(180deg, rgba(219, 234, 254, 0.88) 0%, rgba(239, 246, 255, 0.92) 100%);
}

.repository-region-btn__label {
  font-size: 13px;
  font-weight: 650;
}

.repository-region-btn__code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  color: rgb(100 116 139);
}

.repository-add-warning {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 14px;
  padding: 10px 12px;
  border-radius: 10px;
  color: rgb(30 64 175);
  background: rgba(239, 246, 255, 0.92);
  border: 1px solid rgba(147, 197, 253, 0.35);
  font-size: 13px;
  line-height: 1.55;
}

.repository-add-form {
  max-width: 680px;
}

.repository-add-form--grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
  max-width: none;
}

.repository-add-form :deep(.el-form-item__label) {
  font-weight: 650;
  color: rgb(30 41 59);
}

.repository-add-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.repository-segmented {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
  padding: 4px;
  border-radius: 10px;
  background: rgb(241 245 249);
  border: 1px solid rgba(226, 232, 240, 0.95);
}

.repository-segmented__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 5px 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: rgb(71 85 105);
  font-size: 12px;
  font-weight: 650;
}

.repository-segmented__btn.is-active {
  color: rgb(30 64 175);
  background: #fff;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.08);
}

.repository-dir-selector {
  width: 100%;
}

.repository-dir-tree-shell {
  min-height: 178px;
  max-height: none;
  overflow: visible;
  padding: 6px;
  border-radius: 8px;
  border: 1px solid rgba(203, 213, 225, 0.9);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.92) 0%, rgba(248, 250, 252, 0.96) 100%);
}

.repository-dir-tree-shell__empty {
  display: flex;
  min-height: 120px;
  align-items: center;
  justify-content: center;
  padding: 16px;
  color: rgb(100 116 139);
  font-size: 13px;
  background: rgba(248, 250, 252, 0.88);
  border-radius: 6px;
  border: 1px dashed rgba(148, 163, 184, 0.35);
}

.repository-dir-tree {
  max-height: 260px;
  min-height: 0;
  overflow-y: auto;
  overflow-x: auto;
  border: 0;
  border-radius: 0;
  padding: 2px 0;
  background: transparent;
  box-shadow: none;
}

.repository-tree-node {
  display: flex;
  width: 100%;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.repository-tree-node__icon {
  flex-shrink: 0;
  color: #d97706;
}

.repository-tree-node__text {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  flex-direction: column;
}

.repository-tree-node__label {
  font-size: 13px;
  font-weight: 400;
  line-height: 17px;
  color: rgb(30 41 59);
}

.repository-tree-node__path {
  font-size: 12px;
  line-height: 15px;
  color: rgb(100 116 139);
  word-break: break-word;
}

.repository-add-form--quota {
  display: flex;
  gap: 16px;
  max-width: none;
}

.repository-add-form__grow {
  flex: 1 1 280px;
}

.repository-quota-panel {
  flex: 1 1 280px;
  min-width: 0;
  padding: 12px 14px;
  border-radius: 10px;
  background: rgb(248 250 252);
  border: 1px solid rgba(226, 232, 240, 0.95);
}

.repository-quota-panel__threshold {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  font-size: 12px;
  color: rgb(71 85 105);
}

.repository-quota-panel__input {
  width: 88px;
}

.repository-add-preview {
  position: sticky;
  top: 0;
  overflow: hidden;
  border-radius: 12px;
  border: 1px solid rgba(203, 213, 225, 0.9);
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 10px 22px rgba(15, 23, 42, 0.06);
}

.repository-add-preview__head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  background: linear-gradient(135deg, rgba(239, 246, 255, 0.96) 0%, rgba(236, 253, 245, 0.9) 100%);
  border-bottom: 1px solid rgba(226, 232, 240, 0.95);
}

.repository-add-preview__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  flex-shrink: 0;
  border-radius: 12px;
  color: var(--color-info);
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid var(--color-info-border);
}

.repository-add-preview__title-wrap {
  min-width: 0;
}

.repository-add-preview__title {
  overflow: hidden;
  margin: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 15px;
  font-weight: 650;
  color: rgb(15 23 42);
}

.repository-add-preview__type {
  margin: 3px 0 0;
  font-size: 12px;
  color: rgb(100 116 139);
}

.repository-add-preview__body {
  padding: 14px 16px 16px;
}

.repository-add-preview__section + .repository-add-preview__section {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid rgba(226, 232, 240, 0.95);
}

.repository-add-preview__section-title {
  margin: 0 0 10px;
  font-size: 12px;
  font-weight: 650;
  color: rgb(71 85 105);
}

.repository-add-preview__row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid rgba(241, 245, 249, 0.95);
  font-size: 12px;
}

.repository-add-preview__row:last-child {
  border-bottom: 0;
}

.repository-add-preview__row span {
  flex-shrink: 0;
  color: rgb(100 116 139);
}

.repository-add-preview__row strong {
  min-width: 0;
  text-align: right;
  font-weight: 650;
  color: rgb(30 41 59);
  word-break: break-all;
}

.repository-add-preview__mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.repository-add-preview__badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  justify-content: flex-end;
  color: rgb(100 116 139) !important;
}

.repository-add-preview__badge.is-on {
  color: var(--color-success-text) !important;
}

.repository-add-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}

.repository-add-footer__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.repository-add-footer__actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

@media (max-width: 767.98px) {
  .target-picker-grid,
  .target-picker-grid--row,
  .target-dir-card,
  .create-recovery-plan-grid {
    grid-template-columns: 1fr;
  }

  .recovery-dir-selection-labels {
    display: grid;
  }

  .recovery-dir-selection-row,
  .recovery-dir-selection-add-wrap {
    grid-template-columns:
      minmax(140px, 1fr)
      minmax(160px, 1.08fr)
      72px !important;
  }

  .target-batch-panel__header {
    align-items: flex-start;
    flex-direction: column;
  }

  .target-batch-panel__header-actions {
    justify-content: flex-start;
  }

  .target-batch-panel__header-actions,
  .add-target-form-grid {
    width: 100%;
    grid-template-columns: 1fr;
  }

  .repository-add-type-grid,
  .repository-add-layout,
  .repository-platform-grid,
  .repository-region-grid,
  .repository-add-form--grid {
    grid-template-columns: 1fr;
  }

  .repository-add-form--quota {
    flex-direction: column;
  }

  .repository-add-preview {
    position: static;
  }

  .create-recovery-scope-options {
    grid-template-columns: 1fr;
  }

  .create-recovery-destination-grid,
  .create-recovery-conflict-options {
    grid-template-columns: 1fr;
  }

  .create-recovery-target-dir-field .create-recovery-path-input {
    margin-top: 0;
  }

  .create-confirm-batch-row,
  .create-confirm-card__meta {
    grid-template-columns: 1fr;
  }

  .policy-dir-card__picker,
  .policy-batch-grid,
  .create-policy-dialog .compression-radio-group,
  .create-policy-dialog .cron-row,
  .create-policy-dialog .create-kind-grid {
    grid-template-columns: 1fr;
  }
}

.policy-step-search {
  width: 100%;
  max-width: 360px;
}

.policy-pick-group {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  text-align: left;
  width: 100%;
  gap: 0.25rem;
}

.policy-pick-group :deep(.el-radio) {
  margin-right: 0;
  height: auto;
  align-self: stretch;
  white-space: normal;
  line-height: 1.45;
}

.policy-pick-item {
  padding: 6px 0;
}

.policy-pick-radio :deep(.el-radio__label) {
  width: 100%;
  padding-left: 8px;
}

.policy-detail-dl dd {
  word-break: break-word;
}

.create-backup-fullscreen .create-backup-layout--steps {
  display: flex;
  flex-direction: column;
  gap: 24px;
  min-height: 0;
}

@media (min-width: 1024px) {
  .create-backup-fullscreen .create-backup-layout--steps {
    flex-direction: row;
    align-items: flex-start;
  }
}

.create-backup-main {
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
}

.create-restore-fullscreen > .fullscreen-form-page {
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.create-restore-fullscreen > .fullscreen-form-page > .fullscreen-form-layout {
  flex: 0 0 auto;
  overflow: visible;
}

.create-restore-fullscreen > .fullscreen-form-page > .fullscreen-form-layout > .create-backup-main {
  flex: 1 1 auto;
  height: auto;
  max-height: none;
  overflow: visible;
}

.fixed-restore-route-state {
  width: min(760px, calc(100% - 48px));
  margin: 72px auto;
  padding: 32px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: var(--radius-card, 12px);
  background: #fff;
}

.create-restore-fullscreen--manual > .fullscreen-form-page {
  overflow: hidden;
}

.create-restore-fullscreen--manual > .fullscreen-form-page > .fullscreen-form-layout {
  flex: 1 1 auto;
  overflow: hidden;
}

.create-restore-fullscreen--manual > .fullscreen-form-page > .fullscreen-form-layout > .create-backup-main,
.create-restore-fullscreen--manual .dp-restore-wizard-body,
.create-restore-fullscreen--manual .recovery-entry-panel {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}

.create-restore-fullscreen--manual .recovery-entry-panel {
  flex-direction: column;
}

.create-restore-fullscreen--manual .recovery-entry-options {
  flex: 0 0 auto;
}

.create-restore-fullscreen--manual .recovery-manual-inline-layout {
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}

.create-restore-fullscreen--manual .recovery-manual-inline-layout__main {
  flex: 1 1 auto;
  min-height: 0;
  max-height: 100%;
  overflow-y: auto;
  overscroll-behavior: contain;
}

@media (min-width: 1024px) {
  .create-restore-fullscreen > .fullscreen-form-page > .fullscreen-form-layout {
    align-items: stretch;
  }

  .create-restore-fullscreen--manual .recovery-manual-inline-layout {
    align-items: stretch;
  }
}

.create-backup-step-body {
  display: flex;
  flex-direction: column;
  gap: 0;
  min-height: 0;
}

.create-backup-footer__inner,
.create-backup-footer__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  width: 100%;
}

.create-backup-footer__actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.dp-restore-wizard-body {
  display: flex;
  flex-direction: column;
  width: 100%;
  min-height: 0;
}

.dp-wizard-pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.dp-wizard-scroll-card {
  display: flex;
  flex-direction: column;
  max-height: clamp(280px, 58vh, 520px);
  overflow: hidden;
}

.dp-wizard-scroll-card > .rec-select-tree,
.dp-wizard-scroll-card > .rec-dest-tree {
  flex: 1 1 auto;
  min-height: 0;
}

.rec-select-tree,
.rec-dest-tree {
  flex: 1 1 auto;
  min-height: 0;
  max-height: none;
}

.recovery-dest-record {
  display: flex;
  width: 100%;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid rgba(203, 213, 225, 0.95);
  border-radius: var(--radius-card);
  background: rgba(255, 255, 255, 0.9);
  text-align: left;
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    background 0.18s ease,
    box-shadow 0.18s ease;
}

.recovery-dest-record:hover,
.recovery-dest-record.is-active {
  border-color: rgba(61, 126, 200, 0.7);
  background: #fff;
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
}

.recovery-dest-record__main,
.recovery-dest-record__dest {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.recovery-dest-record__name {
  overflow: hidden;
  color: rgb(30 41 59);
  font-size: 13px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recovery-dest-record__source,
.recovery-dest-record__dest {
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 1.35;
}

.recovery-dest-table {
  border-radius: var(--radius-card);
  overflow: hidden;
}

.recovery-dest-table :deep(.el-table__row) {
  cursor: pointer;
}

.recovery-dest-table :deep(.recovery-dest-table-row--active td.el-table__cell) {
  background: rgba(219, 234, 254, 0.72) !important;
}

.recovery-dir-config-table {
  border-radius: var(--radius-card);
  overflow: hidden;
}

.recovery-dir-config-table :deep(.el-table__body-wrapper) {
  padding-bottom: 28px;
}

.recovery-dir-config-table :deep(.el-scrollbar__wrap) {
  padding-bottom: 28px;
}

.recovery-dir-picker {
  max-height: 520px;
  overflow: hidden;
}

.recovery-dir-picker__tree {
  max-height: 360px;
  overflow-y: auto;
}

.recovery-confirm-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.recovery-confirm-card {
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: var(--radius-card);
  background: var(--color-card-bg, #fff);
  overflow: hidden;
}

.recovery-confirm-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid rgba(226, 232, 240, 0.95);
  background: rgb(248 250 252);
}

.recovery-confirm-card__body {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  gap: 12px;
  padding: 14px;
}

.recovery-confirm-section {
  min-width: 0;
}

.recovery-confirm-section--tree {
  grid-column: 1 / -1;
  padding-top: 12px;
  border-top: 1px solid rgba(226, 232, 240, 0.95);
}

.recovery-confirm-section__title {
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 650;
  color: rgb(71 85 105);
}

.recovery-confirm-dir,
.recovery-confirm-dest {
  display: flex;
  min-width: 0;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
}

.recovery-confirm-dir span,
.recovery-confirm-dest span {
  flex-shrink: 0;
  color: rgb(100 116 139);
}

.recovery-confirm-source-table {
  width: 100%;
}

.recovery-confirm-expand {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-left: 22px;
  padding: 8px 0 8px 22px;
  border-left: 3px solid color-mix(in srgb, var(--color-primary) 50%, rgb(203 213 225));
}

.recovery-confirm-expand__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.recovery-confirm-policy-cell {
  display: inline-flex;
  width: 100%;
  max-width: 100%;
  flex-wrap: nowrap;
  align-items: center;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(248, 250, 252, 0.86);
  white-space: nowrap;
}

.recovery-confirm-policy-cell .create-recovery-plan-cell__policy-icon {
  flex: 0 0 auto;
}

.recovery-confirm-policy-cell .create-recovery-plan-cell__policy-text {
  min-width: 0;
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recovery-confirm-mapping-group {
  position: relative;
  min-width: 0;
}

.recovery-confirm-mapping-group::before {
  position: absolute;
  top: 16px;
  left: -22px;
  width: 14px;
  border-top: 1px solid rgba(148, 163, 184, 0.7);
  content: '';
}

.recovery-confirm-mapping-list {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.recovery-confirm-mapping-line {
  padding: 5px 8px;
  border: 1px solid rgba(226, 232, 240, 0.9);
  background: rgba(248, 250, 252, 0.72);
}

@media (max-width: 767.98px) {
  .recovery-confirm-expand {
    margin-left: 10px;
    padding-left: 16px;
  }
}

.recovery-confirm-dir strong,
.recovery-confirm-dest strong {
  min-width: 0;
  color: rgb(30 41 59);
  font-weight: 600;
  word-break: break-all;
}

.policy-step .policy-pick-group {
  max-height: clamp(180px, 32vh, 280px);
  overflow-y: auto;
  padding-right: 4px;
}

.dp-flow-step3-action-tooltip {
  display: inline-flex;
}

.dp-flow-step3-action-btn.el-button {
  min-width: 6.5rem;
}

.dp-flow-steps-row {
  --dp-hbr-primary: var(--color-primary, var(--el-color-primary, #2563eb));
  --dp-hbr-primary-deep: color-mix(in srgb, var(--dp-hbr-primary) 88%, #000);
  --dp-hbr-accent: color-mix(in srgb, var(--dp-hbr-primary) 72%, #64748b);
  --dp-hbr-primary-soft: color-mix(in srgb, var(--dp-hbr-primary) 8%, transparent);
  --dp-hbr-primary-tint: color-mix(in srgb, var(--dp-hbr-primary) 7%, #fff);
  --dp-hbr-primary-tint-strong: color-mix(in srgb, var(--dp-hbr-primary) 12%, #fff);
  --dp-hbr-border: rgba(203, 213, 225, 0.9);
  --dp-hbr-muted: rgb(100 116 139);
  position: relative;
  padding: 1px;
  border-radius: 8px;
  isolation: isolate;
}

.dp-flow-card {
  position: relative;
  display: flex;
  align-items: stretch;
  gap: 12px;
  min-height: 112px;
  padding: 14px 14px 13px;
  border-radius: 14px;
  border: 1px solid rgba(203, 213, 225, 0.82);
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.98), rgba(248, 250, 252, 0.9)),
    linear-gradient(90deg, color-mix(in srgb, var(--dp-hbr-primary) 5%, transparent), transparent 45%, color-mix(in srgb, var(--dp-hbr-accent) 5%, transparent));
  box-shadow:
    0 1px 2px rgba(15, 23, 42, 0.04),
    inset 0 1px 0 rgba(255, 255, 255, 0.92);
  cursor: pointer;
  overflow: hidden;
  isolation: isolate;
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    background 0.18s ease,
    transform 0.18s ease;
}

.dp-flow-card::before,
.dp-flow-card::after {
  content: '';
  position: absolute;
  z-index: 1;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.18s ease;
}

.dp-flow-card::before {
  top: 8px;
  right: 8px;
  width: 34px;
  height: 18px;
  border-top: 1px solid color-mix(in srgb, var(--dp-hbr-accent) 42%, transparent);
  border-right: 1px solid color-mix(in srgb, var(--dp-hbr-accent) 42%, transparent);
  border-radius: 0 6px 0 0;
}

.dp-flow-card::after {
  left: 14px;
  right: 14px;
  bottom: 0;
  height: 2px;
  border-radius: 2px 2px 0 0;
  /* background: linear-gradient(90deg, transparent, var(--dp-hbr-primary), var(--dp-hbr-accent), transparent); */
}

.dp-flow-card > * {
  position: relative;
  z-index: 2;
}

.dp-flow-card__mark {
  position: relative;
  flex: 0 0 56px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  align-self: stretch;
  padding: 2px 10px 0 0;
  border-right: 1px solid rgba(226, 232, 240, 0.86);
  transition: border-color 0.18s ease;
}

.dp-flow-card__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(241, 245, 249, 0.88));
  color: rgb(51 65 85);
  border: 1px solid rgba(203, 213, 225, 0.9);
  box-shadow:
    0 1px 2px rgba(15, 23, 42, 0.04),
    inset 0 1px 0 rgba(255, 255, 255, 0.86);
  transition:
    background 0.18s ease,
    color 0.18s ease,
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    transform 0.18s ease;
}

.dp-flow-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 7px;
}

.dp-flow-card__body {
  display: flex;
  flex-direction: column;
}

.dp-flow-card__heading {
  display: flex;
  align-items: baseline;
  min-width: 0;
}

.dp-flow-card__index {
  flex: none;
  color: rgb(148 163 184);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.02em;
  transition: color 0.18s ease;
}

.dp-flow-card__title {
  min-width: 0;
  color: rgb(15 23 42);
  font-size: 0.95rem;
  font-weight: 650;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dp-flow-card__desc {
  margin: 0;
  color: var(--dp-hbr-muted);
  font-size: 0.8rem;
  line-height: 1.45;
  margin-bottom: 7px;
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  line-clamp: 3;
}

.dp-flow-card__pill {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 20px;
  padding: 2px 7px 2px 6px;
  border-radius: 6px;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0;
  color: var(--dp-hbr-primary-deep);
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid color-mix(in srgb, var(--dp-hbr-primary) 28%, transparent);
  box-shadow: none;
  transition:
    color 0.18s ease,
    background 0.18s ease,
    border-color 0.18s ease,
    box-shadow 0.18s ease;
}

.dp-flow-card__pill--idle {
  color: rgb(100 116 139);
  background: rgba(248, 250, 252, 0.74);
  border-color: rgba(226, 232, 240, 0.84);
  box-shadow: none;
  font-weight: 500;
}

.dp-flow-card__meta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  margin-top: auto;
  min-height: 24px;
  padding: 3px 8px 3px 7px;
  border-radius: 6px;
  background: rgba(248, 250, 252, 0.76);
  border: 1px solid rgba(226, 232, 240, 0.86);
  font-size: 12px;
  color: rgb(71 85 105);
  font-variant-numeric: tabular-nums;
  transition:
    background 0.18s ease,
    border-color 0.18s ease,
    color 0.18s ease;
}

.dp-flow-card__meta-dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--dp-hbr-accent) 55%, rgb(148 163 184));
  transition: background 0.18s ease;
}

.dp-flow-card__meta-text {
  line-height: 1.2;
}

.dp-flow-card:not(.dp-flow-card--active):hover {
  border-color: color-mix(in srgb, var(--dp-hbr-accent) 36%, rgba(203, 213, 225, 0.86));
  background: rgba(255, 255, 255, 0.96);
  box-shadow:
    0 8px 18px rgba(15, 23, 42, 0.065),
    inset 0 1px 0 rgba(255, 255, 255, 0.9);
  transform: translateY(-1px);
}

.dp-flow-card:not(.dp-flow-card--active):hover::before,
.dp-flow-card:not(.dp-flow-card--active):hover::after {
  opacity: 1;
}

.dp-flow-card:not(.dp-flow-card--active):hover .dp-flow-card__mark {
  border-color: color-mix(in srgb, var(--dp-hbr-accent) 24%, #fff);
}

.dp-flow-card:not(.dp-flow-card--active):hover .dp-flow-card__index {
  color: var(--dp-hbr-primary-deep);
}

.dp-flow-card:not(.dp-flow-card--active):hover .dp-flow-card__icon {
  color: var(--dp-hbr-primary-deep);
  border-color: color-mix(in srgb, var(--dp-hbr-accent) 44%, transparent);
  background: linear-gradient(180deg, #fff, color-mix(in srgb, var(--dp-hbr-accent) 8%, #fff));
  box-shadow:
    0 5px 12px color-mix(in srgb, var(--dp-hbr-accent) 12%, transparent),
    inset 0 1px 0 rgba(255, 255, 255, 0.9);
  transform: translateY(-1px);
}

.dp-flow-card:not(.dp-flow-card--active):hover .dp-flow-card__pill--idle {
  color: var(--dp-hbr-primary-deep);
  background: color-mix(in srgb, var(--dp-hbr-accent) 7%, #fff);
  border-color: color-mix(in srgb, var(--dp-hbr-accent) 26%, transparent);
}

.dp-flow-card--active {
  border-color: color-mix(in srgb, var(--dp-hbr-primary) 62%, var(--dp-hbr-accent));
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--dp-hbr-primary) 10%, #fff), rgba(255, 255, 255, 0.98) 42%, color-mix(in srgb, var(--dp-hbr-accent) 8%, #fff)),
    repeating-linear-gradient(90deg, rgba(148, 163, 184, 0.08) 0 1px, transparent 1px 18px);
  box-shadow:
    0 0 0 1px color-mix(in srgb, var(--dp-hbr-accent) 20%, transparent) inset,
    0 10px 22px color-mix(in srgb, var(--dp-hbr-primary) 12%, transparent),
    0 2px 4px rgba(15, 23, 42, 0.045);
}

.dp-flow-card--active::before,
.dp-flow-card--active::after {
  opacity: 1;
}

.dp-flow-card--active .dp-flow-card__title {
  color: rgb(15 23 42);
}

.dp-flow-card--active .dp-flow-card__desc {
  color: rgb(71 85 105);
}

.dp-flow-card--active .dp-flow-card__icon {
  background:
    linear-gradient(135deg, var(--dp-hbr-primary), color-mix(in srgb, var(--dp-hbr-accent) 58%, var(--dp-hbr-primary)));
  color: #fff;
  border-color: color-mix(in srgb, var(--dp-hbr-accent) 36%, var(--dp-hbr-primary));
  box-shadow:
    0 7px 16px color-mix(in srgb, var(--dp-hbr-accent) 18%, transparent),
    inset 0 1px 0 rgba(255, 255, 255, 0.22);
}

.dp-flow-card--active .dp-flow-card__mark {
  border-color: color-mix(in srgb, var(--dp-hbr-accent) 24%, #fff);
}

.dp-flow-card--active .dp-flow-card__index {
  color: var(--dp-hbr-primary-deep);
}

.dp-flow-card--active .dp-flow-card__meta {
  background: rgba(255, 255, 255, 0.84);
  border-color: color-mix(in srgb, var(--dp-hbr-accent) 28%, #fff);
  color: var(--dp-hbr-primary-deep);
}

.dp-flow-card--active .dp-flow-card__meta-dot {
  background: var(--dp-hbr-accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--dp-hbr-accent) 16%, transparent);
}

.dp-flow-card--active .dp-flow-card__pill {
  background:
    linear-gradient(135deg, var(--dp-hbr-primary), color-mix(in srgb, var(--dp-hbr-accent) 52%, var(--dp-hbr-primary)));
  border-color: color-mix(in srgb, var(--dp-hbr-accent) 34%, var(--dp-hbr-primary));
  color: #fff;
  box-shadow: 0 5px 12px color-mix(in srgb, var(--dp-hbr-accent) 18%, transparent);
}

.dp-flow-card--active:hover {
  border-color: color-mix(in srgb, var(--dp-hbr-primary) 62%, var(--dp-hbr-accent));
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--dp-hbr-primary) 12%, #fff), rgba(255, 255, 255, 1) 42%, color-mix(in srgb, var(--dp-hbr-accent) 10%, #fff));
  box-shadow:
    0 0 0 1px color-mix(in srgb, var(--dp-hbr-accent) 20%, transparent) inset,
    0 10px 22px color-mix(in srgb, var(--dp-hbr-primary) 13%, transparent);
}

.dp-flow-card:focus-visible {
  outline: 3px solid color-mix(in srgb, var(--dp-hbr-primary) 28%, transparent);
  outline-offset: 2px;
}

.dp-flow-steps-connector {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  flex-shrink: 0;
  padding: 0;
  color: rgb(148 163 184);
}

.dp-flow-steps-connector::before,
.dp-flow-steps-connector::after {
  content: '';
  width: 10px;
  height: 2px;
  border-radius: 2px;
  opacity: 0.75;
}

.dp-flow-steps-connector::before {
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--dp-hbr-accent) 36%, rgb(148 163 184)));
}

.dp-flow-steps-connector::after {
  background: linear-gradient(90deg, color-mix(in srgb, var(--dp-hbr-primary) 42%, var(--dp-hbr-accent)), transparent);
}

.dp-flow-steps-connector__icon {
  width: 18px;
  height: 18px;
  margin: 0 3px;
  color: color-mix(in srgb, var(--dp-hbr-primary) 62%, var(--dp-hbr-accent));
  opacity: 0.9;
  filter: none;
  animation: dp-chev-shift 2.8s ease-in-out infinite;
}

@keyframes dp-chev-shift {
  0%, 100% {
    transform: translateX(0);
  }
  50% {
    transform: translateX(2px);
  }
}

@media (min-width: 1280px) {
  .dp-flow-steps-connector {
    align-self: center;
  }
}

@media (min-width: 768px) and (max-width: 1279.98px) {
  .dp-flow-steps-row {
    flex-direction: row;
    align-items: stretch;
  }

  .dp-flow-card {
    min-width: 0;
    min-height: 104px;
    gap: 9px;
    padding: 12px 11px 11px;
  }

  .dp-flow-card__mark {
    flex-basis: 44px;
    gap: 6px;
    padding-right: 8px;
  }

  .dp-flow-card__icon {
    width: 32px;
    height: 32px;
  }

  .dp-flow-card__header {
    align-items: flex-start;
    gap: 6px;
  }

  .dp-flow-card__desc {
    -webkit-line-clamp: 2;
    line-clamp: 2;
  }

  .dp-flow-steps-connector {
    width: 24px;
  }
}

@media (max-width: 767.98px) {
  .dp-flow-steps-row {
    gap: 0;
  }

  .dp-flow-card {
    min-height: 76px;
    align-items: center;
    gap: 10px;
    padding: 9px 11px;
    border-radius: 10px;
  }

  .dp-flow-card__mark {
    flex-basis: 44px;
    justify-content: center;
    gap: 4px;
    padding: 0 9px 0 0;
  }

  .dp-flow-card__icon {
    width: 30px;
    height: 30px;
  }

  .dp-flow-card__body {
    align-self: stretch;
    justify-content: center;
  }

  .dp-flow-card__header {
    gap: 6px;
    margin-bottom: 5px;
  }

  .dp-flow-card__title {
    font-size: 0.875rem;
  }

  .dp-flow-card__desc {
    display: none;
  }

  .dp-flow-card__pill {
    min-height: 20px;
    padding: 2px 6px;
  }

  .dp-flow-card__meta {
    min-height: 20px;
    padding: 2px 7px 2px 6px;
    font-size: 11.5px;
  }

  .dp-flow-card__meta-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .dp-flow-steps-connector {
    width: 100%;
    height: 20px;
  }

  .dp-flow-steps-connector::before,
  .dp-flow-steps-connector::after {
    width: 2px;
    height: 4px;
  }

  .dp-flow-steps-connector::before {
    background: linear-gradient(180deg, transparent, color-mix(in srgb, var(--dp-hbr-accent) 36%, rgb(148 163 184)));
  }

  .dp-flow-steps-connector::after {
    background: linear-gradient(180deg, color-mix(in srgb, var(--dp-hbr-primary) 42%, var(--dp-hbr-accent)), transparent);
  }

  .dp-flow-steps-connector__icon {
    width: 14px;
    height: 14px;
    animation-name: dp-chev-shift-v;
  }

  @keyframes dp-chev-shift-v {
    0%, 100% {
      transform: rotate(90deg) translateX(0);
    }
    50% {
      transform: rotate(90deg) translateX(2px);
    }
  }
}

@media (max-width: 639.98px) {
  .dp-flow-card__pill {
    font-size: 10px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .dp-flow-steps-connector__icon {
    animation: none;
  }
}

.protection-flow-progress {
  max-width: 100%;
}

.protection-flow-progress :deep(.el-progress-bar__outer) {
  background-color: rgb(226 232 240);
}

.protection-flow-progress :deep(.el-progress-bar__inner) {
  background-color: var(--color-info);
}

.protection-flow-progress.is-exception :deep(.el-progress-bar__inner) {
  background-color: var(--color-error);
}

.protection-flow-progress.is-success :deep(.el-progress-bar__inner) {
  background-color: var(--color-success);
}

.flow-source-list-drawer-table :deep(.el-table__cell) {
  vertical-align: middle;
}

.flow-source-list-drawer-path {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
  font-size: 12px;
  color: rgb(30 41 59);
  word-break: break-all;
}

.dp-flow-source-detail {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr);
  gap: 0.5rem 0.75rem;
  margin: 0;
}

.dp-flow-source-detail dt {
  color: var(--el-text-color-secondary);
  font-size: 0.85rem;
}

.dp-flow-source-detail dd {
  min-width: 0;
  margin: 0;
  color: rgb(15 23 42);
  overflow-wrap: anywhere;
}

.backup-task-trigger {
  display: block;
  width: 100%;
  min-width: 0;
  padding: 3px 6px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: background 0.18s ease;
}

.backup-task-trigger:hover {
  background: rgb(248 250 252);
}

.backup-task-trigger:disabled {
  cursor: not-allowed;
  opacity: 0.48;
}

.flow-dir-preview-list__row {
  display: inline-flex;
  width: fit-content;
  max-width: 100%;
  align-items: center;
}

.flow-dir-preview-list__row--has-more {
  max-width: calc(100% - 26px);
}

.flow-dir-preview-list__path {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.flow-dir-preview-list__more {
  flex: 0 0 auto;
  display: inline-flex;
  width: 18px;
  height: 18px;
  margin-left: 8px;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: rgb(241 245 249);
  color: rgb(100 116 139);
  box-shadow: 0 0 0 1px rgba(226, 232, 240, 0.9);
  pointer-events: none;
}

.restore-task-section {
  border-radius: var(--radius-card);
  border: 1px solid rgba(226, 232, 240, 0.95);
  background: #fff;
  overflow: hidden;
}

.restore-task-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px 14px;
  border-bottom: 1px solid rgba(226, 232, 240, 0.95);
  font-size: 13px;
  font-weight: 650;
  color: rgb(30 41 59);
}

.restore-task-drawer-table {
  width: 100%;
}

.restore-task-record-link {
  max-width: 100%;
  text-align: left;
}

.dp-process-page :deep(.el-descriptions) {
  border-radius: 10px;
  overflow: hidden;
}

.dp-process-page :deep(.el-descriptions__body) {
  background: rgba(255, 255, 255, 0.94);
}

.dp-process-page :deep(.el-descriptions__label) {
  font-weight: 600;
  color: rgb(71 85 105);
}

.dp-process-page :deep(.el-descriptions__content) {
  color: rgb(30 41 59);
}

.add-source-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
}
.add-source-type-card__inner {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-width: 0;
  color: #334155;
}
.add-source-type-card__inner > svg {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  padding: 7px;
  border-radius: 8px;
  background: rgba(69, 125, 176, 0.1);
  color: #457AB0;
  transition: all 0.18s ease;
}
.add-source-type-card:hover .add-source-type-card__inner > svg,
.add-source-type-card.is-active .add-source-type-card__inner > svg {
  background: rgba(69, 125, 176, 0.16);
  color: var(--color-primary, #457AB0);
}
.add-source-type-card__text {
  display: flex;
  min-width: 0;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
}
.add-source-layout {
  flex: 1 1 auto;
  display: flex;
  width: 100%;
  min-width: 0;
  min-height: 0;
}
.add-source-layout--host {
  margin-top: 0;
}
.add-source-dialog-scroll {
  overflow: hidden;
}
.add-source-layout .fullscreen-form-layout {
  flex: 1 1 auto;
  height: 100%;
  min-height: 0;
}
.add-source-main {
  width: 100%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.add-source-main .policy-dialog-card + .policy-dialog-card {
  margin-top: 0;
}
.source-underline-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: 0 18px;
  background: transparent;
  border-bottom: none;
}
.source-underline-tabs :deep(.el-tabs__nav-wrap::after) {
  display: none;
}
.source-underline-tabs :deep(.el-tabs__nav) {
  padding-left: 0;
}
.source-underline-tabs :deep(.el-tabs__item) {
  height: 48px;
  line-height: 48px;
  padding: 0 24px;
  font-size: 14px;
  font-weight: 400;
  color: rgb(71 85 105);
  border: none;
  transition: color var(--transition-fast, 150ms ease);
}
.source-underline-tabs :deep(.el-tabs__item:hover) {
  color: rgb(30 41 59);
}
.source-underline-tabs :deep(.el-tabs__item.is-active) {
  color: rgb(29 78 216);
  font-weight: 500;
}
.source-underline-tabs :deep(.el-tabs__active-bar) {
  height: 3px;
  border-radius: 2px 2px 0 0;
  background: linear-gradient(180deg, #3b82f6 0%, #1d4ed8 100%);
}
.source-underline-tabs :deep(.el-tabs__content) {
  display: none;
}
.add-nas-layout {
  display: flex;
  flex-direction: column;
  gap: 0;
}
.add-nas-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
}
.add-nas-main > .add-nas-card {
  margin-bottom: 16px;
}
.add-nas-main > .add-nas-card:last-child {
  margin-bottom: 0;
}
.add-nas-protocol-tabs {
  margin-bottom: 8px;
}
.add-nas-protocol-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: 0;
  background: transparent;
  border-bottom: none;
}
.add-nas-protocol-tabs :deep(.el-tabs__item) {
  height: 44px;
  line-height: 44px;
  padding: 0 20px 0 0;
}
.add-nas-protocol-tabs :deep(.el-tabs__item:last-child) {
  padding-right: 0;
}
.add-nas-card {
  border: 1px solid rgba(203, 213, 225, 0.9);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.96) 0%, rgba(248, 250, 252, 0.96) 100%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.7),
    0 8px 20px rgba(15, 23, 42, 0.04);
}
@keyframes source-refresh-spin {
  to {
    transform: rotate(360deg);
  }
}
.source-kind-name {
  display: flex;
  align-items: center;
  gap: 10px;
}
.source-hero-card {
  margin-bottom: 14px;
}
.source-hero {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}
.source-hero__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  flex-shrink: 0;
  border-radius: 14px;
  background: linear-gradient(180deg, #eaf3ff 0%, #dbeafe 100%);
  color: #2563eb;
  border: 1px solid rgba(37, 99, 235, 0.14);
}
.source-hero__icon--nas {
  background: linear-gradient(180deg, #eefbf6 0%, #dff7ec 100%);
  color: #0f766e;
  border-color: rgba(15, 118, 110, 0.14);
}
.source-hero__body {
  min-width: 0;
  flex: 1 1 auto;
}
.source-hero__title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--el-text-color-primary, #0f172a);
}
.source-hero__desc {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--el-text-color-secondary, #64748b);
}
.source-hero__meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 16px;
}
.source-hero__meta-item {
  min-width: 0;
  padding: 12px 14px;
  border-radius: var(--radius-card, 10px);
  background: var(--el-fill-color-light, #f8fafc);
  border: 1px solid var(--el-border-color-lighter, #e5e7eb);
}
.source-hero__meta-label {
  display: block;
  margin-bottom: 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary, #64748b);
}
.source-hero__meta-value {
  display: block;
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary, #0f172a);
}
.source-config-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(220px, 0.9fr);
  gap: 16px;
}
.source-config-item__label {
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-regular, #475569);
}
.source-role-tag {
  min-height: 40px;
}
.source-script-shell--compact {
  padding: 10px 12px;
}
.source-script-shell__viewport--compact {
  min-height: 72px;
}
.source-script-shell__viewport--compact :deep(.el-loading-mask) {
  border-radius: 2px;
}
.agent-deploy-body {
  max-height: 60vh;
  overflow-y: auto;
}
.proxy-deploy-dialog__alert {
  margin-bottom: 16px;
  border-radius: 12px !important;
}
.proxy-deploy-dialog__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.proxy-deploy-dialog__desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: rgb(100 116 139);
}

.add-nas-steps {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 0 0;
}
.add-nas-steps--shell {
  margin-top: 4px;
}
.add-nas-steps--panel {
  padding-top: 0;
  margin-bottom: 10px;
}
.add-nas-section--merged {
  margin-top: 22px;
  padding-top: 22px;
  border-top: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.add-nas-steps--card {
  margin-bottom: 0;
  padding: 14px 0 0;
  border-top: 1px solid var(--color-border-light);
}
.add-nas-steps__item {
  display: flex;
  align-items: center;
  gap: 10px;
  opacity: 0.5;
  transition: opacity 0.2s ease;
}
.add-nas-steps__item--active,
.add-nas-steps__item--done {
  opacity: 1;
}
.add-nas-steps__num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 600;
  background: rgb(248 250 252);
  border: 1px solid rgba(203, 213, 225, 0.95);
  color: rgb(100 116 139);
  transition: all 0.2s ease;
}
.add-nas-steps__item--active .add-nas-steps__num,
.add-nas-steps__item--done .add-nas-steps__num {
  background: linear-gradient(180deg, rgb(37 99 235) 0%, rgb(29 78 216) 100%);
  border-color: rgb(29 78 216);
  color: #fff;
}
.add-nas-steps__label {
  font-size: 14px;
  font-weight: 500;
  color: rgb(30 41 59);
  white-space: nowrap;
}
.add-nas-steps__connector {
  flex: 1;
  max-width: 60px;
  height: 2px;
  background: rgba(203, 213, 225, 0.9);
  border-radius: 1px;
  transition: background 0.2s ease;
}
.add-nas-steps__connector--on {
  background: rgb(37 99 235);
}
.add-nas-step {
  display: flex;
  align-items: center;
  gap: 10px;
  opacity: 0.5;
  transition: opacity 0.2s ease;
}
.add-nas-step--active,
.add-nas-step--done {
  opacity: 1;
}
.add-nas-step__num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 600;
  background: rgb(248 250 252);
  border: 1px solid rgba(203, 213, 225, 0.95);
  color: rgb(100 116 139);
  transition: all 0.2s ease;
}
.add-nas-step--active .add-nas-step__num,
.add-nas-step--done .add-nas-step__num {
  background: linear-gradient(180deg, rgb(37 99 235) 0%, rgb(29 78 216) 100%);
  border-color: rgb(29 78 216);
  color: #fff;
}
.add-nas-step__label {
  font-size: 14px;
  font-weight: 500;
  color: rgb(30 41 59);
  white-space: nowrap;
}
.add-nas-step__connector {
  flex: 1;
  max-width: 60px;
  height: 2px;
  background: rgba(203, 213, 225, 0.9);
  border-radius: 1px;
  transition: background 0.2s ease;
}
.add-nas-step__connector.is-on {
  background: rgb(37 99 235);
}
.add-nas-section {
  padding: 16px 24px;
}
.add-nas-section__head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.add-nas-section__indicator {
  width: 4px;
  height: 16px;
  border-radius: 999px;
  background: linear-gradient(180deg, #3b82f6 0%, #1d4ed8 100%);
}
.add-nas-section__title-wrap {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.add-nas-section__title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: rgb(15 23 42);
  margin: 0 0 18px;
}
.add-nas-section__subtitle {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: rgb(30 41 59);
  margin: 22px 0 14px;
  padding-top: 18px;
  border-top: 1px solid rgba(226, 232, 240, 0.95);
}
.add-nas-section__subtitle-icon {
  flex-shrink: 0;
  color: var(--color-info);
}
.add-nas-card--step0 .add-nas-section {
  padding-bottom: 22px;
}
.add-nas-section__icon {
  color: var(--el-color-primary);
}
.add-nas-protocol-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  width: 100%;
  max-width: 480px;
}
.add-nas-protocol-card {
  height: auto !important;
  min-height: 58px;
  padding: 10px 14px !important;
  border-radius: 10px !important;
  border-color: #c8d3e0 !important;
  background: #f8fbff;
  transition: all 0.18s ease;
}
.add-nas-protocol-card:hover {
  border-color: #7a99bc !important;
  background: #f1f6fc;
  box-shadow: 0 8px 16px rgba(59, 130, 246, 0.08);
}
.add-nas-protocol-card.is-checked {
  border-color: var(--color-primary, #457AB0) !important;
  background: rgba(69, 125, 176, 0.14);
  color: var(--color-primary, #457AB0);
}
.add-nas-protocol-card__inner {
  display: flex;
  align-items: center;
  gap: 12px;
  color: #334155;
}
.add-nas-protocol-card__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  border-radius: 8px;
  background: rgba(69, 125, 176, 0.1);
  color: #457AB0;
  transition: all 0.18s ease;
}
.add-nas-protocol-card:hover .add-nas-protocol-card__icon,
.add-nas-protocol-card.is-checked .add-nas-protocol-card__icon {
  background: rgba(69, 125, 176, 0.16);
  color: var(--color-primary, #457AB0);
}
.add-nas-protocol-card__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.add-nas-form {
  margin-top: 4px;
  width: 100%;
}
.add-nas-form--step0 {
  max-width: none;
}
.add-nas-form--stack {
  width: 100%;
  max-width: none;
}
.add-nas-form--stack :deep(.el-form-item) {
  display: block;
  width: 100%;
}
.add-nas-form--stack :deep(.el-form-item__content) {
  width: 100%;
}
.add-nas-form--stack :deep(.el-input) {
  width: 100%;
}
.add-nas-form :deep(.el-form-item__label) {
  font-weight: 600;
  color: rgb(30 41 59);
  padding-bottom: 6px;
}
.add-nas-form :deep(.el-form-item) {
  margin-bottom: 18px;
}
.add-nas-form-row {
  display: flex;
  gap: 16px;
}
.add-nas-form-row > * {
  min-width: 0;
}
.add-nas-form-row--responsive {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 20px;
  margin-bottom: 2px;
}
.add-nas-form-row--responsive :deep(.el-form-item) {
  margin-bottom: 18px;
}
.add-nas-form-row--triple {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0 20px;
  margin-bottom: 2px;
}
.add-nas-form-row--triple :deep(.el-form-item) {
  margin-bottom: 18px;
}
.add-nas-form-row--pair {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.reset-status-cell {
  display: inline-flex;
  width: 92px;
  min-height: 30px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  border: 0;
  background: transparent;
  color: rgb(100 116 139);
  cursor: pointer;
}

.reset-status-cell__label {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 700;
}

.reset-status-cell--resetting .reset-status-cell__label {
  color: rgb(180 83 9);
}

.reset-status-cell--reset_failed .reset-status-cell__label {
  color: rgb(185 28 28);
}

.reset-status-cell__progress {
  display: grid;
  width: 86px;
  grid-template-columns: 1fr 28px;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: rgb(100 116 139);
}

.reset-status-cell__track {
  height: 5px;
  overflow: hidden;
  border-radius: 999px;
  background: rgb(254 243 199);
}

.reset-status-cell__fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: rgb(245 158 11);
}

.reset-status-cell__percent {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

@media (min-width: 1200px) {
  .add-nas-form-row--pair {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    max-width: calc((100% - 40px) * 2 / 3 + 20px);
  }
}
.add-nas-form-item--wide {
  max-width: min(100%, 560px);
}
.add-nas-field-hint {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-tertiary, #64748b);
}
.add-nas-inline-note {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary, #64748b);
}
.add-nas-section__tool-btn {
  width: 28px;
  height: 28px;
  padding: 0;
}
.add-nas-optional-badge {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  background: rgba(239, 246, 255, 0.95);
  color: rgb(29 78 216);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
.add-nas-field-label-with-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.add-nas-field-label-with-action__btn {
  width: 24px;
  height: 24px;
  padding: 0;
  color: var(--el-text-color-secondary, #64748b);
}
.add-nas-field-label-with-action__btn:hover {
  color: rgb(29 78 216);
  background: rgba(239, 246, 255, 0.95);
}
.add-nas-select-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.add-nas-select-row__search {
  width: 32px;
  height: 32px;
  padding: 0;
  flex-shrink: 0;
  color: var(--el-text-color-secondary, #64748b);
  background: var(--el-fill-color-light, #f8fafc);
  border: 1px solid var(--el-border-color-lighter, #e5e7eb);
}
.add-nas-select-row__refresh {
  width: 32px;
  height: 32px;
  padding: 0;
  flex-shrink: 0;
  color: var(--el-text-color-secondary, #64748b);
}
.add-nas-select-row__refresh .is-spinning {
  animation: source-refresh-spin 0.85s linear infinite;
}
.add-nas-select-row__select {
  flex: 1 1 auto;
  min-width: 0;
  width: 100%;
}
.add-nas-bind-form-item :deep(.el-form-item__content) {
  width: 100%;
}
.add-nas-proxy-alert {
  max-width: 780px;
  margin-bottom: 18px;
}
.add-nas-proxy-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 420px);
  gap: 18px;
  align-items: stretch;
}
.add-nas-proxy-form {
  min-width: 0;
}
.add-nas-proxy-benefits {
  display: grid;
  gap: 10px;
  max-width: 620px;
  margin-top: 2px;
}
.add-nas-proxy-benefit {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  background: rgba(248, 250, 252, 0.78);
  font-size: 13px;
  line-height: 1.5;
  color: rgb(51 65 85);
}
.add-nas-proxy-benefit__dot {
  width: 7px;
  height: 7px;
  margin-top: 7px;
  flex-shrink: 0;
  border-radius: 999px;
  background: var(--color-info);
}
.add-nas-path-card {
  display: grid;
  grid-template-columns: 74px 42px 88px 34px 72px;
  align-items: center;
  min-height: 150px;
  padding: 18px;
  border: 1px solid rgba(203, 213, 225, 0.95);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(248, 250, 252, 0.9) 100%);
}
.add-nas-path-card--direct {
  display: block;
}
.add-nas-path-card__agents {
  display: grid;
  gap: 10px;
}
.add-nas-path-card__agents span,
.add-nas-path-card__source {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 28px;
  border-radius: 6px;
  border: 1px solid rgba(203, 213, 225, 0.95);
  background: #fff;
  color: rgb(51 65 85);
  font-size: 12px;
  font-weight: 600;
}
.add-nas-path-card__source {
  min-width: 72px;
}
.add-nas-path-card__join {
  position: relative;
  height: 96px;
}
.add-nas-path-card__join::before {
  content: '';
  position: absolute;
  top: 14px;
  bottom: 14px;
  left: 12px;
  width: 1px;
  background: rgb(148 163 184);
}
.add-nas-path-card__join::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 12px;
  right: 0;
  height: 1px;
  background: rgb(148 163 184);
}
.add-nas-path-card__line {
  height: 1px;
  background: rgb(148 163 184);
}
.add-nas-path-card__direct-rows {
  display: grid;
  gap: 14px;
  min-width: 0;
}
.add-nas-path-card__direct-row {
  display: grid;
  grid-template-columns: minmax(72px, max-content) minmax(72px, 1fr) 72px;
  align-items: center;
  gap: 12px;
}
.add-nas-path-card__direct-line {
  height: 1px;
  min-width: 72px;
  background: rgb(148 163 184);
}
.add-nas-path-card__node {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 38px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 700;
}
.add-nas-path-card__node--proxy {
  border: 1px solid rgba(37, 99, 235, 0.28);
  background: rgba(239, 246, 255, 0.95);
  color: rgb(29 78 216);
}
.add-nas-path-card__node--nas {
  border: 1px solid rgba(22, 163, 74, 0.26);
  background: rgba(240, 253, 244, 0.92);
  color: rgb(21 128 61);
}
.add-nas-deploy-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  padding-inline: 14px;
  border-radius: 10px;
}
.nas-no-proxy-warn {
  padding: 16px;
  border-radius: 6px;
  background: var(--el-color-warning-light-9);
  border: 1px solid var(--el-color-warning-light-5);
  font-size: 13px;
  color: var(--el-color-warning);
}
.nas-no-proxy-warn .nas-bind-error {
  color: var(--el-color-danger);
}
.add-nas-select-row__select.is-error :deep(.el-select__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}
.source-fullscreen--form-shell .add-nas-form {
  max-width: none;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
@media (max-width: 1199.98px) {
  .add-nas-form-row--triple {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .add-nas-form-row--triple :deep(.el-form-item:last-child) {
    grid-column: 1 / -1;
    max-width: none;
  }
  .add-nas-form-row--pair {
    max-width: none;
  }
}
@media (max-width: 767.98px) {
  .source-underline-tabs :deep(.el-tabs__header) {
    padding-right: 12px;
    padding-left: 12px;
  }
  .source-hero,
  .source-config-grid,
  .source-hero__meta,
  .add-source-type-grid,
  .add-nas-protocol-grid,
  .add-nas-form-row--responsive,
  .add-nas-form-row--triple {
    grid-template-columns: 1fr;
    flex-direction: column;
  }
  .add-nas-form-row--responsive,
  .add-nas-form-row--triple {
    gap: 0;
  }
  .add-nas-form-item--wide {
    max-width: none;
  }
  .add-nas-protocol-grid {
    max-width: none;
  }
  .add-nas-select-row {
    align-items: stretch;
  }
  .add-nas-steps {
    gap: 8px;
  }
  .add-nas-step__connector {
    max-width: 36px;
  }
  .add-nas-steps__connector {
    max-width: 36px;
  }
  .add-nas-proxy-layout {
    grid-template-columns: 1fr;
  }
  .add-nas-path-card {
    grid-template-columns: 74px 36px 78px 28px 64px;
    overflow-x: auto;
  }
  .add-nas-path-card--direct {
    overflow-x: auto;
  }
  .source-hero__icon {
    width: 42px;
    height: 42px;
    border-radius: 12px;
  }
  .source-hero__title {
    font-size: 16px;
  }
}
</style>

<style>
.restore-already-running-dialog .el-message-box__message {
  width: 100%;
}

.restore-already-running-dialog .el-message-box__btns {
  display: none;
}

.restore-already-running-dialog .reset-backup-config-dialog__warning {
  width: 100%;
  border: 1px solid var(--color-warning-border);
  border-radius: 6px;
  background: var(--color-warning-light);
  padding: 12px 14px;
  color: var(--color-warning);
}

.restore-already-running-dialog .reset-backup-config-dialog__warning-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.restore-already-running-dialog .reset-backup-config-dialog__warning-item {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  align-items: flex-start;
  gap: 8px;
}

.restore-already-running-dialog .reset-backup-config-dialog__warning-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: 1px solid var(--color-warning-border);
  border-radius: 999px;
  background: #fff;
  color: var(--color-warning);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
}

.restore-already-running-dialog .reset-backup-config-dialog__warning-text {
  min-width: 0;
  color: rgb(120 75 12);
  font-size: 13px;
  line-height: 1.55;
}

.dp-rec-dest-drawer.el-drawer.rtl {
  display: flex;
  flex-direction: column;
}

.dp-rec-dest-drawer.el-drawer.rtl .el-drawer__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  padding-top: 0;
}

.dp-rec-dest-drawer__title {
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.dp-rec-dest-drawer__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}

.dp-rec-dest-drawer__content {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.dp-rec-dir-drawer__tree {
  min-height: 320px;
  max-height: min(58vh, 480px);
}

.flow-compression-cell {
  display: inline-flex !important;
  align-items: center;
  gap: 6px;
  min-width: 0;
  white-space: nowrap;
}

.flow-compression-cell__label {
  color: var(--el-text-color-primary);
  font-size: 14px;
  line-height: 20px;
}

.flow-compression-cell__icon,
.flow-compression-popover__icon {
  flex: 0 0 auto;
  color: rgb(100 116 139);
}

.flow-compression-cell__icon {
  width: 15px;
  height: 15px;
}

.flow-compression-cell__icon--balanced,
.flow-compression-popover__icon--balanced {
  color: var(--color-primary);
}

.flow-compression-cell__icon--high,
.flow-compression-popover__icon--high {
  color: rgb(180 83 9);
}

:global(.flow-compression-popper) {
  max-width: min(288px, calc(100vw - 32px));
}

.flow-compression-popover__title {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 650;
  line-height: 20px;
}

.flow-compression-popover__body {
  margin-top: 6px;
  color: rgb(30 41 59);
  font-size: 13px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}
</style>
