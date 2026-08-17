<script setup lang="ts">
import { computed, onMounted, reactive, ref, toRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import {
  AlertTriangle, Check, ChevronDown, ChevronRight, Circle, CircleStop, Clock3, Copy, Filter, Globe, Link2, RefreshCw, RotateCcw, X,
} from 'lucide-vue-next'
import ModulePage from '../../components/ModulePage.vue'
import OpsStatCard from '../../components/ops/OpsStatCard.vue'
import HflTablePanel from '../../components/HflTablePanel.vue'
import HflDateTimeRangePicker from '../../components/HflDateTimeRangePicker.vue'
import ResourceNameSummaryCell from '../../components/ResourceNameSummaryCell.vue'
import TaskStatusTag from '../../components/TaskStatusTag.vue'
import TaskTypeLabel from '../../components/TaskTypeLabel.vue'
import FlowSourceSummaryCell from '../protection/components/FlowSourceSummaryCell.vue'
import FlowSourceConnectionCell from '../protection/components/FlowSourceConnectionCell.vue'
import { useOpsMenus } from '../../composables/useOpsMenus'
import { useListSearch } from '../../composables/useListSearch'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import { useDrawerScrollReset } from '../../composables/useDrawerScrollReset'
import { usePageRequestScope } from '../../composables/usePageRequestScope'
import { useRepositoryTaskCancellation } from '../../composables/useRepositoryTaskCancellation'
import { apiErrorMessage, apiErrorMessageI18n } from '../../lib/api'
import { copyTextToClipboard } from '../../lib/clipboard'
import { notifyError, notifySuccess } from '../../lib/notify'
import { formatLocalDateTime } from '../../lib/dateTime'
import { formatTaskProgressBarPercent, formatTaskProgressPercent } from '../../lib/kopiaProgress'
import { lifecycleStatusTagAttrs } from '../../lib/statusTag'
import { getNode } from '../../lib/nodeApi'
import { getBackupSourceSnapshot } from '../../lib/protectionBackupConfigApi'
import { cancelProtectionBackupTask } from '../../lib/protectionBackupTaskApi'
import { cancelProtectionRestoreTask } from '../../lib/protectionRestoreTaskApi'
import {
  buildStopConfirmItemFromTask,
} from '../../lib/protectionStopConfirm'
import { useProtectionStopConfirmDialog } from '../../composables/useProtectionStopConfirmDialog'
import ProtectionStopConfirmDialog from '../../components/ProtectionStopConfirmDialog.vue'
import { getSourceResource } from '../../lib/sourceApi'
import {
  cancelStorageRepositoryTask,
  getStorageRepository,
} from '../../lib/storageRepositoryApi'
import {
  canCancelRepositoryTask,
} from '../../lib/repositoryTaskCancellation'
import { resolveTaskBackupSourceResource, resolveTaskBackupSourceResourceFromPayload } from '../../lib/taskBackupSourceResource'
import { parseTaskStepStatusEvent, taskEventMessageKey, taskEventObjectText } from '../../lib/taskEventDisplay'
import { hasExpandableTaskStep, hasExpandedTaskStep } from '../../lib/taskStepExpansion'
import {
  taskCleanupFailures,
  taskCleanupWarnings,
  taskDisplayStatus,
  taskFailedCleanupChildren,
  taskNeedsManualCleanup,
  taskResourceSnapshot,
  taskRetainedResources,
} from '../../lib/taskOutcomeDisplay'
import {
  cancelTask,
  getTask,
  listTaskEvents,
  listTasks,
  taskStatistics,
  type TaskEventRow,
  type TaskRow,
  type TaskStatistics,
} from '../../lib/taskApi'
import type { TaskResourceRow } from '../../lib/taskApi'
import { isRestoreTaskType } from '../../lib/taskType'

const { t, te } = useI18n()
const stopConfirmDialog = useProtectionStopConfirmDialog()
const stopConfirmOpen = stopConfirmDialog.open
const stopConfirmKind = stopConfirmDialog.kind
const stopConfirmItems = stopConfirmDialog.items
const { drawerSize, bindDrawerResize, unbindDrawerResize } = useResponsiveDrawerWidth()
const { drawerSize: nestedDrawerSize } = useResponsiveDrawerWidth(2)
const { drawerScrollAnchorRef, resetDrawerScroll } = useDrawerScrollReset()
const pageRequests = usePageRequestScope()
const route = useRoute()
const router = useRouter()
const opsMenus = useOpsMenus()
const rows = ref<TaskRow[]>([])
const taskOwner = ref('')
const loading = ref(false)
const listLoadError = ref<string | null>(null)
const actionBusy = ref(false)
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })
const filters = reactive({
  search: '',
  search_field: 'name',
  status: '',
  task_type: textQueryValue(route.query.task_type),
  trigger_type: '',
  time_mode: routeTimeModeFromQuery(),
  time_field: textQueryValue(route.query.time_field) === 'finished' ? 'finished' : 'created',
  created_range: routeDateTimeRange(),
  terminal_only: textQueryValue(route.query.terminal_only) === 'true',
  resource_type: '',
  resource_id: '',
})
const {
  appliedSearch,
  clearSearch,
  handleSearchFieldChange,
  runSearchNow: runFilterSearch,
} = useListSearch(toRef(filters, 'search'), () => applyFilters())
const advancedFilterOpen = ref(false)
const lastQuickTimeMode = ref(filters.time_mode === 'range' ? '7d' : filters.time_mode)
const advancedFilterDraft = reactive({
  created_range: null as [Date, Date] | null,
  time_field: 'created' as 'created' | 'finished',
  terminal_only: false,
  resource_type: '',
  resource_id: '',
})

const searchFieldOptions = computed(() => [
  { value: 'name', label: t('ops.task.searchName') },
  { value: 'uuid', label: t('ops.task.searchUuid') },
])
const stats = ref<TaskStatistics>({
  total: 0,
  running: 0,
  success: 0,
  failed: 0,
  cancelled: 0,
  timeout: 0,
  by_status: {},
  by_task_type: {},
})

const detailOpen = ref(false)
const activeTask = ref<TaskRow | null>(null)
const detailEvents = ref<TaskEventRow[]>([])
const detailRefreshing = ref(false)
const activeDetailTab = ref<'steps' | 'resources'>('steps')
const expandedSteps = reactive<Record<string, boolean>>({})
const selectedResourceType = ref('')
const resourceLoading = ref(false)
const resourceDetails = reactive<Record<string, ResourceDetailRow[]>>({})
const resourceErrors = reactive<Record<string, string>>({})
const activeCleanupFailures = computed(() => taskCleanupFailures(activeTask.value))
const activeCleanupWarnings = computed(() => taskCleanupWarnings(activeTask.value))
const activeRetainedResources = computed(() => taskRetainedResources(activeTask.value))
const activeFailedCleanupChildren = computed(() => taskFailedCleanupChildren(activeTask.value))
const activeNeedsManualCleanup = computed(() => taskNeedsManualCleanup(activeTask.value))
const cleanupOutcomeSucceeded = computed(() => activeTask.value?.status === 'success')
const showCleanupOutcome = computed(() => (
  taskDisplayStatus(activeTask.value) === 'partial'
  || activeCleanupFailures.value.length > 0
  || activeCleanupWarnings.value.length > 0
  || activeRetainedResources.value.length > 0
))

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

const statusOptions = ['pending', 'waiting', 'blocked', 'running', 'success', 'failed', 'cancelled', 'timeout']
const taskTypeOptions = ['backup', 'restore', 'insight_workspace_restore', 'snapshot_download', 'snapshot_delete', 'backup_config_reset', 'source_unregister', 'node_lifecycle', 'repository_operation']
const DEFAULT_TRIGGER_TYPE_OPTIONS = ['manual', 'system']
const triggerTypeOptions = computed(() => Array.from(new Set([
  ...DEFAULT_TRIGGER_TYPE_OPTIONS,
  ...rows.value.map((row) => row.trigger_type).filter(Boolean),
])))
const resourceTypeOptions = ['backup_source', 'repository', 'target_repository', 'snapshot', 'host', 'volume']
const timeModeOptions = computed(() => [
  { value: 'all', label: t('ops.task.timeAll') },
  { value: '24h', label: t('ops.task.time24h') },
  { value: '7d', label: t('ops.task.time7d') },
  { value: '30d', label: t('ops.task.time30d') },
  { value: 'range', label: t('ops.task.timeRange') },
])
const timeFieldOptions = computed(() => [
  { value: 'created', label: t('ops.task.timeFieldCreated') },
  { value: 'finished', label: t('ops.task.timeFieldFinished') },
])
const taskDateTimeRangePresets = computed(() => [
  { value: '24h', label: t('ops.task.time24h'), hours: 24 },
  { value: '7d', label: t('ops.task.time7d'), hours: 7 * 24 },
  { value: '30d', label: t('ops.task.time30d'), hours: 30 * 24 },
])
const advancedFilterCount = computed(() => {
  let count = 0
  if (filters.resource_type) count += 1
  if (filters.resource_id) count += 1
  if (filters.time_mode === 'range' && filters.created_range) count += 1
  if (filters.terminal_only) count += 1
  return count
})

function pad2(n: number) {
  return String(n).padStart(2, '0')
}

function formatLocalInputDateTime(date?: Date | null) {
  if (!(date instanceof Date) || !Number.isFinite(date.getTime())) return ''
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`
}

function parseLocalInputDateTime(value: string) {
  if (!value) return null
  const normalized = value.length === 16 ? `${value}:00` : value
  const date = new Date(normalized)
  return Number.isFinite(date.getTime()) ? date : null
}

function routeTimeModeFromQuery() {
  const value = textQueryValue(route.query.time_mode)
  return ['all', '24h', '7d', '30d', 'range'].includes(value) ? value : '7d'
}

function routeDateTimeRange(): [Date, Date] | null {
  if (textQueryValue(route.query.time_mode) !== 'range') return null
  const timeField = textQueryValue(route.query.time_field) === 'finished' ? 'finished' : 'created'
  const after = new Date(textQueryValue(route.query[`${timeField}_after`]))
  const before = new Date(textQueryValue(route.query[`${timeField}_before`]))
  if (!Number.isFinite(after.getTime()) || !Number.isFinite(before.getTime()) || after > before) return null
  return [after, before]
}

const taskAdvancedRangeStart = computed(() => formatLocalInputDateTime(advancedFilterDraft.created_range?.[0]))
const taskAdvancedRangeEnd = computed(() => formatLocalInputDateTime(advancedFilterDraft.created_range?.[1]))
const taskAdvancedRangeLabel = computed(() => {
  if (!advancedFilterDraft.created_range) return t('ops.task.timeRange')
  return `${formatLocalDateTime(advancedFilterDraft.created_range[0].toISOString())} ~ ${formatLocalDateTime(advancedFilterDraft.created_range[1].toISOString())}`
})

const canCancel = computed(() => {
  if (activeTask.value?.actions) return activeTask.value.actions.can_cancel
  if (activeTask.value?.task_type === 'source_unregister') return false
  if (activeTask.value?.task_type === 'node_lifecycle') return false
  if (activeTask.value?.task_type === 'repository_operation') {
    return canCancelRepositoryTask(activeTask.value)
  }
  const status = activeTask.value?.status
  return status === 'pending' || status === 'waiting' || status === 'running'
})
const activeDependencies = computed(() =>
  (activeTask.value?.dependencies || []).filter((dependency) => dependency.is_active),
)
const {
  pending: repositoryCancellationPending,
  stop: stopRepositoryCancellation,
  sync: syncRepositoryCancellation,
} = useRepositoryTaskCancellation(activeTask, {
  onUpdate: async (task) => {
    syncTask(task)
  },
  onTerminal: async (task) => {
    const eventPage = await listTaskEvents(task.task_uuid, { page_size: 50 })
    if (activeTask.value?.task_uuid === task.task_uuid) {
      detailEvents.value = eventPage.results
      initExpandedSteps(task)
    }
    await load()
    if (task.status === 'cancelled') {
      ElMessage.success(t('ops.task.repositoryCancelComplete'))
    }
  },
  onError: () => ElMessage.warning({
    message: t('ops.task.repositoryCancelPollFailed'),
    grouping: true,
  }),
})
const stepsWithEvents = computed(() => {
  const steps = activeTask.value?.steps || []
  const grouped: Record<number, TaskEventRow[]> = {}
  for (const evt of detailEvents.value) {
    if (evt.step_id == null) continue
    const key = evt.step_id
    if (!grouped[key]) grouped[key] = []
    grouped[key].push(evt)
  }
  return steps.map((s) => ({
    ...s,
    events: grouped[s.id] || [],
  }))
})
const hasExpandableSteps = computed(() => hasExpandableTaskStep(stepsWithEvents.value))
const hasAnyExpandedStep = computed(() => hasExpandedTaskStep(stepsWithEvents.value, isStepExpanded))
const unlinkedEvents = computed(() => {
  const stepIds = new Set((activeTask.value?.steps || []).map((s) => s.id))
  return detailEvents.value.filter((e) => e.step_id == null || !stepIds.has(e.step_id))
})
const detailResourceCount = computed(() => activeTask.value?.resources?.length || 0)
const detailStepCount = computed(() => activeTask.value?.steps?.length || 0)
const groupedResources = computed(() => {
  const groups: Record<string, TaskResourceRow[]> = {}
  for (const resource of activeTask.value?.resources || []) {
    if (!groups[resource.resource_type]) groups[resource.resource_type] = []
    groups[resource.resource_type].push(resource)
  }
  return groups
})
const resourceTypeTabs = computed(() => Object.entries(groupedResources.value).map(([type, items]) => ({
  type,
  label: labelFor('resourceType', type),
  count: items.length,
})))
const selectedResourceRows = computed(() => resourceDetails[selectedResourceType.value] || [])
const isRepositoryResourceType = computed(() => selectedResourceType.value === 'repository')

function textQueryValue(value: unknown) {
  if (Array.isArray(value)) return String(value[0] || '')
  return String(value || '')
}

function labelFor(scope: 'status' | 'taskType' | 'triggerType' | 'resourceType', value?: string | null) {
  if (!value) return t('ops.task.emptyMark')
  const key = `ops.task.${scope}.${value}`
  return te(key) ? t(key) : t('ops.task.unknownValue')
}

function valueLabel(scope: 'resourceValue', value?: string | null) {
  if (!value) return t('ops.task.emptyMark')
  const key = `ops.task.${scope}.${value}`
  return te(key) ? t(key) : t('ops.task.unknownValue')
}

function progressValue(row: TaskRow) {
  return formatTaskProgressBarPercent(row.progress)
}

function progressText(row: TaskRow) {
  return formatTaskProgressPercent(row.progress)
}

function formatTime(iso?: string | null) {
  return formatLocalDateTime(iso, t('ops.task.emptyMark'))
}

function isoDateParam(date?: Date | null, inclusiveEnd = false) {
  if (!(date instanceof Date) || !Number.isFinite(date.getTime())) return undefined
  const normalized = new Date(date)
  if (inclusiveEnd) normalized.setMilliseconds(999)
  return normalized.toISOString()
}

function taskTimeRangeParams() {
  const now = new Date()
  let after: string | undefined
  let before: string | undefined
  if (filters.time_mode === '24h') after = new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString()
  if (filters.time_mode === '7d') after = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString()
  if (filters.time_mode === '30d') after = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString()
  if (filters.time_mode === 'range' && filters.created_range) {
    after = isoDateParam(filters.created_range[0])
    before = isoDateParam(filters.created_range[1], true)
  }
  if (filters.time_field === 'finished') {
    return { created_after: undefined, created_before: undefined, finished_after: after, finished_before: before }
  }
  return { created_after: after, created_before: before, finished_after: undefined, finished_before: undefined }
}

function taskFilterParams() {
  const timeParams = taskTimeRangeParams()
  return {
    search: appliedSearch.value.trim(),
    search_field: filters.search_field,
    status: filters.status,
    task_type: filters.task_type,
    trigger_type: filters.trigger_type,
    resource_type: filters.resource_type,
    resource_id: filters.resource_id,
    created_after: timeParams.created_after,
    created_before: timeParams.created_before,
    finished_after: timeParams.finished_after,
    finished_before: timeParams.finished_before,
    terminal_only: filters.terminal_only ? 'true' : undefined,
  }
}

function payloadRecord(task?: TaskRow | null) {
  return task?.request_payload && typeof task.request_payload === 'object'
    ? task.request_payload as Record<string, unknown>
    : {}
}

function taskDescription(task?: TaskRow | null) {
  if (!task) return t('ops.task.emptyMark')
  return task.display_name || (task.current_step ? stepDisplayName(task.current_step, task.task_type) : labelFor('taskType', task.task_type))
}

function durationText(start?: string | null, end?: string | null) {
  if (!start || !end) return t('ops.task.emptyMark')
  const startMs = new Date(start).getTime()
  const endMs = new Date(end).getTime()
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs < startMs) return t('ops.task.emptyMark')
  const totalSeconds = Math.max(0, Math.round((endMs - startMs) / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':')
}

function taskDuration(task?: TaskRow | null) {
  return durationText(task?.started_at || task?.created_at, task?.finished_at || task?.updated_at)
}

function stepDuration(index: number) {
  const current = stepsWithEvents.value[index]
  const next = stepsWithEvents.value[index + 1]
  const start = current?.events[0]?.created_at || activeTask.value?.started_at || activeTask.value?.created_at
  const end = next?.events[0]?.created_at || current?.events[current.events.length - 1]?.created_at || activeTask.value?.finished_at
  return durationText(start, end)
}

function timelineIconClass(status?: string) {
  if (status === 'success') return 'hfl-task-drawer__timeline-icon--success'
  if (status === 'warning') return 'hfl-task-drawer__timeline-icon--warning'
  if (status === 'failed' || status === 'timeout') return 'hfl-task-drawer__timeline-icon--danger'
  if (status === 'running') return 'hfl-task-drawer__timeline-icon--running'
  if (status === 'cancelled') return 'hfl-task-drawer__timeline-icon--muted'
  return 'hfl-task-drawer__timeline-icon--pending'
}

function displayTaskStatus(task?: TaskRow | null) {
  return taskDisplayStatus(task)
}

function eventTone(event: TaskEventRow) {
  const level = String(event.level || '').toUpperCase()
  const message = String(event.message || '').toLowerCase()
  if (level === 'ERROR' || message.includes('failed') || message.includes('error')) return 'danger'
  if (message.includes('timeout')) return 'danger'
  if (message.includes('cancelled')) return 'muted'
  if (level === 'DEBUG') return 'muted'
  return 'success'
}

function eventMessageClass(event: TaskEventRow) {
  const tone = eventTone(event)
  return {
    'hfl-task-drawer__event-msg--danger': tone === 'danger',
    'hfl-task-drawer__event-msg--muted': tone === 'muted',
  }
}

function stepDisplayName(stepName?: string | null, taskType?: string | null) {
  const step = String(stepName || '')
  if (!step) return t('ops.task.emptyMark')
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

function eventObjectText(event: TaskEventRow) {
  const canonicalValue = taskEventObjectText(event)
  if (canonicalValue) return canonicalValue
  const directValue = taskEventMetadataText(event, [
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

function eventErrorText(event: TaskEventRow) {
  const message = taskEventMetadataText(event, ['error_message'])
  if (!message) return ''
  const code = taskEventMetadataText(event, ['error_code'])
  return code ? `[${code}] ${message}` : message
}

function eventDisplayMessage(event: TaskEventRow) {
  const text = String(event.message || '')
  const key = taskEventMessageKey(text)
  if (key) return t(key)
  const stepStatus = parseTaskStepStatusEvent(text)
  if (stepStatus) {
    return t('ops.task.eventMessage.stepStatusUpdated', {
      step: stepDisplayName(stepStatus.step),
      status: labelFor('status', stepStatus.status),
    })
  }
  return text
}

function syncTask(task: TaskRow) {
  const index = rows.value.findIndex((row) => row.task_uuid === task.task_uuid)
  if (index >= 0) rows.value[index] = task
  if (activeTask.value?.task_uuid === task.task_uuid) activeTask.value = task
}

function stepKey(stepId: number | string) {
  return String(stepId)
}

function initExpandedSteps(task?: TaskRow | null) {
  for (const key of Object.keys(expandedSteps)) delete expandedSteps[key]
  const steps = task?.steps || []
  steps.forEach((step) => {
    expandedSteps[stepKey(step.id)] = true
  })
}

function isStepExpanded(stepId: number | string) {
  return expandedSteps[stepKey(stepId)] !== false
}

function toggleStep(stepId: number | string, eventCount: number) {
  if (eventCount === 0) return
  const key = stepKey(stepId)
  expandedSteps[key] = !isStepExpanded(key)
}

function setAllStepsExpanded(expanded: boolean) {
  for (const step of activeTask.value?.steps || []) {
    expandedSteps[stepKey(step.id)] = expanded
  }
}

function toggleAllStepsExpanded() {
  setAllStepsExpanded(!hasAnyExpandedStep.value)
}

function resetResourceDetails() {
  selectedResourceType.value = ''
  for (const key of Object.keys(resourceDetails)) delete resourceDetails[key]
  for (const key of Object.keys(resourceErrors)) delete resourceErrors[key]
}

function firstResourceType() {
  return resourceTypeTabs.value[0]?.type || ''
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
  const config = record.config && typeof record.config === 'object' ? record.config as Record<string, unknown> : {}
  return [
    objectValue(record, ['connection_summary', 'bound_node_name', 'bind_node_display_name', 's3_bucket', 'kopia_snapshot_id']),
    objectValue(config, ['path', 'bucket', 'endpoint', 'server', 'export_path']),
  ].filter(Boolean).join(' · ')
}

function backupSourceConnectivityLabel(value?: string) {
  return value === 'online'
    ? t('protection.sourceResources.nodeStatusOnline')
    : t('protection.sourceResources.nodeStatusOffline')
}

function backupSourceConnectivityTagType(value?: string): 'success' | 'danger' {
  return value === 'online' ? 'success' : 'danger'
}

function normalizeResourceDetail(type: string, id: number, raw: unknown, source?: Awaited<ReturnType<typeof resolveTaskBackupSourceResource>>): ResourceDetailRow {
  const record = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
  const rawType = objectValue(record, ['resource_type', 'repo_type', 'role', 'snapshot_type'])
  const rawStatus = objectValue(record, ['status', 'health', 'mount_status', 'source_snapshot_status'])
  return {
    key: `${type}:${id}`,
    id,
    name: objectValue(record, ['name', 'display_name', 'hostname', 'snapshot_uid', 'task_uuid']) || String(id),
    type: rawType ? valueLabel('resourceValue', rawType) : labelFor('resourceType', type),
    status: rawStatus ? valueLabel('resourceValue', rawStatus) : '',
    statusValue: rawStatus,
    updatedAt: objectValue(record, ['updated_at', 'last_checked_at', 'created_at']) || t('ops.task.emptyMark'),
    summary: resourceSummary(record) || t('ops.task.emptyMark'),
    backupSource: source?.backupSource,
    endpointName: source?.endpointName || objectValue(record, ['hostname', 'name', 'display_name']),
    endpointIp: source?.endpointIp || objectValue(record, ['ip_address', 'endpoint']),
    registeredAt: source?.registeredAt || objectValue(record, ['created_at']),
    availability: source?.availability,
    flowSource: source?.flowSource,
  }
}

function resourceSubtype(resource: TaskResourceRow) {
  if (resource.resource_subtype) return resource.resource_subtype
  if (resource.resource_type !== 'backup_source') return ''
  const payload = payloadRecord(activeTask.value)
  return objectValue(payload, ['source_type']).toLowerCase()
}

async function fetchResourceDetail(resource: TaskResourceRow, signal?: AbortSignal) {
  const { resource_type: type, resource_id: id } = resource
  const subtype = resourceSubtype(resource)
  if (type === 'backup_source' && subtype === 'agent') return getNode(id, { signal })
  if (type === 'backup_source') return getSourceResource(id, { signal })
  if (type === 'repository' || type === 'target_repository') return getStorageRepository(id, { signal })
  if (type === 'snapshot') return getBackupSourceSnapshot(id, { signal })
  if (type === 'host') return getNode(id, { signal })
  return { id, resource_type: type, resource_subtype: subtype }
}

async function loadTaskOwner(task: TaskRow, signal?: AbortSignal) {
  const resource = task.primary_resource
  if (!resource) {
    taskOwner.value = t('ops.task.emptyMark')
    return
  }
  // For source_unregister tasks, resolve the owner from immutable task payload
  // data first, since the live resource has already been removed.
  if (task.task_type === 'source_unregister' && resource.resource_type === 'backup_source') {
    const fromPayload = resolveTaskBackupSourceResourceFromPayload(resource, task)
    if (activeTask.value?.task_uuid === task.task_uuid) {
      taskOwner.value = fromPayload?.backupSource || t('ops.task.emptyMark')
    }
    return
  }
  try {
    const detail = await fetchResourceDetail(resource, signal)
    if (activeTask.value?.task_uuid === task.task_uuid) {
      taskOwner.value = normalizeResourceDetail(resource.resource_type, resource.resource_id, detail).name
    }
  } catch {
    const snapshot = taskResourceSnapshot(task, resource)
    if (activeTask.value?.task_uuid === task.task_uuid) {
      taskOwner.value = snapshot
        ? normalizeResourceDetail(resource.resource_type, resource.resource_id, snapshot).name
        : t('ops.task.emptyMark')
    }
  }
}

async function loadResourceType(type: string) {
  if (!type) return
  selectedResourceType.value = type
  if (resourceDetails[type]) return
  const resources = groupedResources.value[type] || []
  const signal = pageRequests.nextSignal('task-resources')
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
          ? await resolveTaskBackupSourceResource(resource, resourceSubtype(resource), signal)
          : undefined
        return normalizeResourceDetail(resource.resource_type, resource.resource_id, raw, source)
      } catch (error) {
        const snapshot = taskResourceSnapshot(activeTask.value, resource)
        if (snapshot) {
          return normalizeResourceDetail(resource.resource_type, resource.resource_id, snapshot)
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
    if (pageRequests.isAbortError(err)) return
    resourceErrors[type] = apiErrorMessage(err)
    resourceDetails[type] = resources.map((resource) => normalizeResourceDetail(resource.resource_type, resource.resource_id, resource))
  } finally {
    pageRequests.releaseSignal('task-resources', signal)
    if (!signal.aborted) resourceLoading.value = false
  }
}

function openResourceTab() {
  activeDetailTab.value = 'resources'
  const type = selectedResourceType.value || firstResourceType()
  if (type) loadResourceType(type)
}

function openStepsTab() {
  activeDetailTab.value = 'steps'
  setAllStepsExpanded(true)
}

function onDetailTabChange(name: string | number) {
  if (name === 'resources') openResourceTab()
  else if (name === 'steps') openStepsTab()
}

async function copyTaskUuid() {
  if (!activeTask.value?.task_uuid) return
  try {
    await copyTextToClipboard(activeTask.value.task_uuid)
    notifySuccess({
      message: t('ops.task.msgCopied'),
      dedupeKey: `task-copy:${activeTask.value.task_uuid}`,
    })
  } catch {
    notifyError({
      message: t('ops.task.msgCopyFailed'),
      dedupeKey: `task-copy-failed:${activeTask.value.task_uuid}`,
    })
  }
}

async function load() {
  const signal = pageRequests.nextSignal('task-list')
  loading.value = true
  listLoadError.value = null
  try {
    const filterParams = taskFilterParams()
    const res = await listTasks({
      page: pagination.page,
      page_size: pagination.pageSize,
      ...filterParams,
    }, { signal })
    rows.value = res.results
    pagination.count = res.count
    stats.value = await taskStatistics(filterParams, { signal }).catch((e) => {
      if (pageRequests.isAbortError(e)) throw e
      return stats.value
    })
    if (rows.value.length === 0 && pagination.count > 0 && pagination.page > 1) {
      pagination.page = 1
      await load()
    }
  } catch (err) {
    if (pageRequests.isAbortError(err)) return
    rows.value = []
    pagination.count = 0
    listLoadError.value = apiErrorMessage(err)
    ElMessage.error({ message: listLoadError.value, grouping: true })
  } finally {
    pageRequests.releaseSignal('task-list', signal)
    if (!signal.aborted) loading.value = false
  }
}

function applyFilters() {
  pagination.page = 1
  load()
}

function resetAdvancedFilterDraft() {
  advancedFilterDraft.created_range = null
  advancedFilterDraft.time_field = 'created'
  advancedFilterDraft.terminal_only = false
  advancedFilterDraft.resource_type = ''
  advancedFilterDraft.resource_id = ''
}

function setAdvancedDateTimeRange(start: Date, end: Date) {
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime())) return
  advancedFilterDraft.created_range = [start, end]
}

function onAdvancedDateTimePreset(_value: string, hours?: number) {
  if (!hours) return
  const end = new Date()
  const start = new Date(end.getTime() - hours * 60 * 60 * 1000)
  setAdvancedDateTimeRange(start, end)
}

function onAdvancedDateTimeApply(startValue: string, endValue: string) {
  const start = parseLocalInputDateTime(startValue)
  const end = parseLocalInputDateTime(endValue)
  if (!start || !end) return
  setAdvancedDateTimeRange(start, end)
}

function onAdvancedDateTimeClear() {
  advancedFilterDraft.created_range = null
}

function onTaskTimeModeChange(value: string) {
  if (value === 'range') {
    openAdvancedFilterDrawer()
    return
  }
  lastQuickTimeMode.value = value
  filters.created_range = null
  runFilterSearch()
}

function syncAdvancedFilterDraft() {
  advancedFilterDraft.created_range = filters.created_range
  advancedFilterDraft.time_field = filters.time_field
  advancedFilterDraft.terminal_only = filters.terminal_only
  advancedFilterDraft.resource_type = filters.resource_type
  advancedFilterDraft.resource_id = filters.resource_id
}

function openAdvancedFilterDrawer() {
  syncAdvancedFilterDraft()
  advancedFilterOpen.value = true
}

function cancelAdvancedFilter() {
  advancedFilterOpen.value = false
}

function applyAdvancedFilters() {
  filters.created_range = advancedFilterDraft.created_range
  filters.time_field = advancedFilterDraft.time_field
  filters.terminal_only = advancedFilterDraft.terminal_only
  filters.resource_type = advancedFilterDraft.resource_type
  filters.resource_id = advancedFilterDraft.resource_id
  if (filters.created_range) filters.time_mode = 'range'
  else if (filters.time_mode === 'range') filters.time_mode = lastQuickTimeMode.value
  advancedFilterOpen.value = false
  runFilterSearch()
}

function onAdvancedFilterClosed() {
  if (filters.time_mode === 'range' && !filters.created_range) filters.time_mode = lastQuickTimeMode.value
  syncAdvancedFilterDraft()
}

async function openTaskDetail(row: TaskRow | string) {
  const taskUuid = typeof row === 'string' ? row : row.task_uuid
  if (!taskUuid) return
  stopRepositoryCancellation()
  const signal = pageRequests.nextSignal('task-detail')
  detailOpen.value = true
  detailRefreshing.value = true
  detailEvents.value = []
  try {
    activeTask.value = await getTask(taskUuid, { signal })
    resetDrawerScroll()
    taskOwner.value = t('ops.task.emptyMark')
    await loadTaskOwner(activeTask.value, signal)
    const eventPage = await listTaskEvents(taskUuid, { page_size: 50 }, { signal })
    detailEvents.value = eventPage.results
    activeDetailTab.value = 'steps'
    initExpandedSteps(activeTask.value)
    resetResourceDetails()
    syncTask(activeTask.value)
    syncRepositoryCancellation(activeTask.value)
    router.replace({ query: { ...route.query, taskUuid } })
  } catch (err) {
    if (pageRequests.isAbortError(err)) return
    ElMessage.error({ message: apiErrorMessage(err), grouping: true })
  } finally {
    const isCurrent = pageRequests.isCurrentSignal('task-detail', signal)
    pageRequests.releaseSignal('task-detail', signal)
    if (isCurrent) detailRefreshing.value = false
  }
}

function closeDetail() {
  stopRepositoryCancellation()
  pageRequests.abortScope('task-detail')
  unbindDrawerResize()
  detailOpen.value = false
  detailRefreshing.value = false
  activeTask.value = null
  taskOwner.value = ''
  detailEvents.value = []
  activeDetailTab.value = 'steps'
  initExpandedSteps(null)
  resetResourceDetails()
  const nextQuery = { ...route.query }
  delete nextQuery.taskUuid
  delete nextQuery.taskId
  router.replace({ query: nextQuery })
}

function onDetailOpened() {
  bindDrawerResize()
  resetDrawerScroll()
}

async function cancelActiveTask() {
  if (!activeTask.value) return
  const task = activeTask.value
  if (task.task_type === 'backup') {
    const confirmed = await stopConfirmDialog.confirmStopBackup([buildStopConfirmItemFromTask(task)])
    if (!confirmed) return
  } else if (isRestoreTaskType(task.task_type)) {
    const confirmed = await stopConfirmDialog.confirmStopRestore([buildStopConfirmItemFromTask(task)])
    if (!confirmed) return
  } else if (task.task_type === 'repository_operation') {
    const confirmed = await stopConfirmDialog.confirmCancelMaintenance([
      buildStopConfirmItemFromTask(task),
    ])
    if (!confirmed) return
  }
  actionBusy.value = true
  try {
    const updated = task.task_type === 'backup'
      ? await (async () => {
          const result = await cancelProtectionBackupTask(task.task_uuid)
          return getTask(result.task_uuid)
        })()
      : isRestoreTaskType(task.task_type)
        ? await (async () => {
            const result = await cancelProtectionRestoreTask(task.task_uuid)
            return getTask(result.task_uuid)
          })()
        : task.task_type === 'repository_operation'
          ? await cancelStorageRepositoryTask(task.task_uuid, t('ops.task.cancelReason'))
          : await cancelTask(task.task_uuid, t('ops.task.cancelReason'))
    syncTask(updated)
    const eventPage = await listTaskEvents(updated.task_uuid, { page_size: 50 })
    detailEvents.value = eventPage.results
    stats.value = await taskStatistics(taskFilterParams()).catch(() => stats.value)
    if (syncRepositoryCancellation(updated)) {
      ElMessage.success(t('ops.task.repositoryCancelQueued'))
    }
  } catch (err) {
    ElMessage.error({ message: apiErrorMessageI18n(err, t), grouping: true })
  } finally {
    actionBusy.value = false
  }
}

async function refreshActiveTask() {
  if (!activeTask.value) return
  await openTaskDetail(activeTask.value.task_uuid)
}

onMounted(async () => {
  await load()
  const taskUuid = textQueryValue(route.query.taskUuid || route.query.taskId)
  if (taskUuid) openTaskDetail(taskUuid)
})

watch(
  () => route.query.taskUuid || route.query.taskId,
  (value) => {
    const taskUuid = textQueryValue(value)
    if (taskUuid && taskUuid !== activeTask.value?.task_uuid) openTaskDetail(taskUuid)
  },
)

watch(
  () => [filters.status, filters.task_type, filters.trigger_type],
  () => runFilterSearch(),
)

watch(
  () => advancedFilterDraft.resource_type,
  (value) => {
    if (!value) advancedFilterDraft.resource_id = ''
  },
)
</script>

<template>
  <ModulePage
    :title="t('ops.task.title')"
    :menus="opsMenus"
    body-fill
  >
    <div class="hfl-ops-page hfl-ops-page--fill">
      <div class="hfl-ops-stats-grid hfl-ops-stats-grid--5">
        <OpsStatCard
          :label="t('ops.task.status.running')"
          :value="stats.running"
          accent="blue"
          accent-side="left"
          value-class="text-blue-600"
          :pulse="stats.running > 0"
        >
          <template #icon>
            <Clock3 :size="17" />
          </template>
        </OpsStatCard>
        <OpsStatCard
          :label="t('ops.task.status.pending')"
          :value="stats.by_status?.pending ?? 0"
          accent="gray"
          accent-side="left"
        >
          <template #icon>
            <Circle :size="15" />
          </template>
        </OpsStatCard>
        <OpsStatCard
          :label="t('ops.task.status.success')"
          :value="stats.success"
          accent="green"
          accent-side="left"
          value-class="text-emerald-600"
        >
          <template #icon>
            <Check :size="17" />
          </template>
        </OpsStatCard>
        <OpsStatCard
          :label="t('ops.task.status.failedTimedOut')"
          :value="stats.failed + stats.timeout"
          accent="red"
          accent-side="left"
          value-class="text-red-600"
        >
          <template #icon>
            <AlertTriangle :size="17" />
          </template>
        </OpsStatCard>
        <OpsStatCard
          :label="t('ops.task.status.cancelled')"
          :value="stats.cancelled"
          accent="gray"
          accent-side="left"
        >
          <template #icon>
            <CircleStop :size="16" />
          </template>
        </OpsStatCard>
      </div>

      <HflTablePanel fill>
        <template #toolbar-actions>
          <ElInput
            v-model="filters.search"
            class="hfl-list-search hfl-list-search-group"
            clearable
            :placeholder="t('ops.task.phSearch')"
            @clear="clearSearch"
          >
            <template #prepend>
              <ElSelect
                v-model="filters.search_field"
                @change="handleSearchFieldChange"
              >
                <ElOption
                  v-for="opt in searchFieldOptions"
                  :key="opt.value"
                  :value="opt.value"
                  :label="opt.label"
                />
              </ElSelect>
            </template>
          </ElInput>
          <ElSelect
            v-model="filters.task_type"
            clearable
            :placeholder="t('ops.task.filterType')"
            style="width: 130px"
          >
            <ElOption
              v-for="item in taskTypeOptions"
              :key="item"
              :label="labelFor('taskType', item)"
              :value="item"
            />
          </ElSelect>
          <ElSelect
            v-model="filters.status"
            clearable
            :placeholder="t('ops.task.filterStatus')"
            style="width: 130px"
          >
            <ElOption
              v-for="item in statusOptions"
              :key="item"
              :label="labelFor('status', item)"
              :value="item"
            />
          </ElSelect>
          <ElSelect
            v-model="filters.trigger_type"
            clearable
            :placeholder="t('ops.task.filterTrigger')"
            style="width: 130px"
          >
            <ElOption
              v-for="item in triggerTypeOptions"
              :key="item"
              :label="labelFor('triggerType', item)"
              :value="item"
            />
          </ElSelect>
          <ElSelect
            v-model="filters.time_field"
            :placeholder="t('ops.task.timeField')"
            style="width: 140px"
            @change="runFilterSearch"
          >
            <ElOption
              v-for="item in timeFieldOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </ElSelect>
          <ElSelect
            v-model="filters.time_mode"
            :placeholder="t('ops.task.filterTime')"
            style="width: 130px"
            @change="onTaskTimeModeChange"
          >
            <ElOption
              v-for="item in timeModeOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </ElSelect>
        </template>
        <template #toolbar-utility>
          <ElButton
            class="hfl-filter-button"
            :class="{ 'hfl-filter-button--active': advancedFilterCount > 0 }"
            :title="t('ops.task.advancedFilter')"
            :aria-label="t('ops.task.advancedFilter')"
            @click="openAdvancedFilterDrawer"
          >
            <ElBadge
              v-if="advancedFilterCount > 0"
              :value="advancedFilterCount"
              :max="9"
              class="hfl-filter-badge"
            >
              <Filter :size="16" />
            </ElBadge>
            <Filter
              v-else
              :size="16"
            />
          </ElButton>
          <ElButton
            class="hfl-refresh-button"
            :title="t('ops.task.btnRefresh')"
            :aria-label="t('ops.task.btnRefresh')"
            :disabled="loading"
            @click="load"
          >
            <RefreshCw
              :size="16"
              :class="{ 'is-spinning': loading }"
            />
          </ElButton>
        </template>

        <template #table="{ tableMaxHeight }">
          <el-table
            v-table-column-resize="'ops.tasks'"
            v-loading="loading"
            :data="rows"
            stripe
            class="hfl-list-table"
            :max-height="tableMaxHeight"
          >
            <el-table-column
              :label="t('ops.task.colName')"
              min-width="240"
              fixed="left"
            >
              <template #default="{ row }">
                <ResourceNameSummaryCell
                  :name="row.display_name"
                  :summary="row.task_uuid"
                  kind="task"
                  :show-icon="false"
                  @open="openTaskDetail(row)"
                />
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.task.colType')"
              min-width="190"
            >
              <template #default="{ row }">
                <TaskTypeLabel
                  :type="row.task_type"
                  :operation-type="row.operation_type"
                />
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.task.colStatus')"
              min-width="120"
            >
              <template #default="{ row }">
                <TaskStatusTag :status="displayTaskStatus(row)" />
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.task.colStep')"
              min-width="150"
            >
              <template #default="{ row }">
                {{ stepDisplayName(row.current_step, row.task_type) }}
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.task.colProgress')"
              min-width="160"
            >
              <template #default="{ row }">
                <div class="hfl-task-list-progress">
                  <div class="hfl-task-list-progress__track">
                    <div
                      class="hfl-task-list-progress__fill"
                      :class="`hfl-task-list-progress__fill--${displayTaskStatus(row)}`"
                      :style="{ width: `${progressValue(row)}%` }"
                    />
                  </div>
                  <span class="hfl-task-list-progress__text">{{ progressText(row) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.task.colTrigger')"
              width="110"
            >
              <template #default="{ row }">
                <el-tag
                  type="info"
                  size="small"
                >
                  {{ labelFor('triggerType', row.trigger_type) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.task.startedAt')"
              width="165"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-time"
                  :class="{ 'hfl-empty-mark': !(row.started_at || row.created_at) }"
                >{{ formatTime(row.started_at || row.created_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.task.finishedAt')"
              width="165"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-time"
                  :class="{ 'hfl-empty-mark': !row.finished_at }"
                >{{ formatTime(row.finished_at) }}</span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty
                v-if="listLoadError"
                :description="listLoadError"
                :image-size="80"
              >
                <ElButton
                  type="primary"
                  @click="load()"
                >
                  {{ t('ops.task.btnRetry') }}
                </ElButton>
              </el-empty>
              <el-empty
                v-else
                :description="t('ops.task.empty')"
                :image-size="80"
              />
            </template>
          </el-table>
        </template>

        <template #footer>
          <HflPagination
            v-model:current-page="pagination.page"
            v-model:page-size="pagination.pageSize"
            class="hfl-list-footer__pagination"
            layout="total, sizes, prev, pager, next"
            :total="pagination.count"
            :page-sizes="[20, 30, 50, 100]"
            @current-change="load"
            @size-change="() => { pagination.page = 1; load() }"
          />
        </template>
      </HflTablePanel>
    </div>

    <ElDrawer
      v-model="advancedFilterOpen"
      :title="t('ops.task.advancedFilter')"
      :size="nestedDrawerSize"
      class="hfl-task-filter-drawer"
      @closed="onAdvancedFilterClosed"
    >
      <ElForm
        label-position="top"
        class="hfl-task-filter-form"
      >
        <ElFormItem :label="t('ops.task.timeField')">
          <ElSelect
            v-model="advancedFilterDraft.time_field"
            class="w-full"
          >
            <ElOption
              v-for="item in timeFieldOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </ElSelect>
        </ElFormItem>
        <ElFormItem :label="t('ops.task.filterTime')">
          <HflDateTimeRangePicker
            class="hfl-filter-range"
            constrain-to-trigger
            :label="taskAdvancedRangeLabel"
            :presets="taskDateTimeRangePresets"
            :start="taskAdvancedRangeStart"
            :end="taskAdvancedRangeEnd"
            :clear-text="t('ops.task.resetFilter')"
            :apply-text="t('ops.task.applyFilter')"
            @preset="onAdvancedDateTimePreset"
            @apply="onAdvancedDateTimeApply"
            @clear="onAdvancedDateTimeClear"
          />
        </ElFormItem>
        <ElFormItem :label="t('ops.task.terminalOnly')">
          <ElSwitch v-model="advancedFilterDraft.terminal_only" />
        </ElFormItem>
        <ElFormItem :label="t('ops.task.filterResource')">
          <ElSelect
            v-model="advancedFilterDraft.resource_type"
            clearable
            class="w-full"
            :placeholder="t('ops.task.filterResource')"
          >
            <ElOption
              v-for="item in resourceTypeOptions"
              :key="item"
              :label="labelFor('resourceType', item)"
              :value="item"
            />
          </ElSelect>
        </ElFormItem>
        <ElFormItem :label="t('ops.task.resourceId')">
          <ElInput
            v-model="advancedFilterDraft.resource_id"
            clearable
            :disabled="!advancedFilterDraft.resource_type"
            :placeholder="t('ops.task.phResourceId')"
            @keyup.enter="applyAdvancedFilters"
          />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <div class="el-drawer__footer-main">
          <ElButton @click="cancelAdvancedFilter">
            {{ t('ops.task.cancelFilter') }}
          </ElButton>
          <div class="el-drawer__footer-actions">
            <ElButton @click="resetAdvancedFilterDraft">
              {{ t('ops.task.resetFilter') }}
            </ElButton>
            <ElButton
              type="primary"
              :disabled="loading"
              @click="applyAdvancedFilters"
            >
              {{ t('ops.task.applyFilter') }}
            </ElButton>
          </div>
        </div>
      </template>
    </ElDrawer>

    <ElDrawer
      v-model="detailOpen"
      class="hfl-task-drawer"
      :size="drawerSize"
      @opened="onDetailOpened"
      @closed="closeDetail"
    >
      <template #header>
        <div class="hfl-task-drawer__header-bar">
          <div
            v-if="activeTask"
            class="hfl-task-drawer__header-summary"
          >
            <div class="hfl-task-drawer__header-title-row">
              <h2 class="hfl-task-drawer__header-title">
                {{ taskDescription(activeTask) }}
              </h2>
              <TaskStatusTag :status="displayTaskStatus(activeTask)" />
            </div>
            <div class="hfl-task-drawer__header-meta">
              <span>{{ t('ops.task.ownerLabel') }}: <span :class="{ 'hfl-empty-mark': !taskOwner || taskOwner === t('ops.task.emptyMark') }">{{ taskOwner || t('ops.task.emptyMark') }}</span></span>
              <span class="hfl-task-drawer__header-divider" />
              <span class="hfl-task-drawer__uuid">
                {{ activeTask.task_uuid }}
                <ElButton
                  class="hfl-task-drawer__copy-button"
                  text
                  :title="t('ops.task.copyUuid')"
                  @click.stop="copyTaskUuid"
                >
                  <Copy :size="13" />
                </ElButton>
              </span>
            </div>
          </div>
          <h2
            v-else
            class="hfl-task-drawer__header-title"
          >
            {{ t('ops.task.detailTitle') }}
          </h2>
          <div class="hfl-task-drawer__header-actions">
            <ElButton
              v-if="canCancel"
              class="hfl-task-drawer__cancel-button"
              :loading="actionBusy"
              :disabled="actionBusy || repositoryCancellationPending"
              @click="cancelActiveTask"
            >
              <span class="hfl-task-drawer__cancel-label">
                <CircleStop :size="15" />
                <span>{{ repositoryCancellationPending ? t('ops.task.btnCancelling') : t('ops.task.btnCancel') }}</span>
              </span>
            </ElButton>
            <button
              v-if="activeTask"
              type="button"
              class="hfl-drawer-header-action"
              :title="t('ops.task.btnRefresh')"
              :aria-label="t('ops.task.btnRefresh')"
              :disabled="detailRefreshing"
              @click="refreshActiveTask"
            >
              <RefreshCw
                :size="20"
                :class="{ 'is-spinning': detailRefreshing }"
              />
            </button>
          </div>
        </div>
      </template>

      <div
        v-if="activeTask"
        ref="drawerScrollAnchorRef"
        class="hfl-task-drawer__body"
      >
        <section class="hfl-task-drawer__hero">
          <div class="hfl-task-drawer__hero-section-title">
            {{ t('ops.task.basicData') }}
          </div>
          <div class="hfl-task-drawer__hero-grid">
            <div class="hfl-task-drawer__metric">
              <Globe
                :size="18"
                class="hfl-task-drawer__metric-icon hfl-task-drawer__metric-icon--blue"
              />
              <div class="hfl-task-drawer__metric-copy">
                <span class="hfl-task-drawer__metric-label">{{ t('ops.task.typeAndTrigger') }}</span>
                <div class="hfl-task-drawer__metric-tags">
                  <el-tag
                    class="hfl-task-meta-tag"
                    size="small"
                    type="info"
                  >
                    {{ labelFor('taskType', activeTask.task_type) }}
                  </el-tag>
                  <el-tag
                    class="hfl-task-meta-tag"
                    size="small"
                    type="info"
                  >
                    {{ labelFor('triggerType', activeTask.trigger_type) }}
                  </el-tag>
                </div>
              </div>
            </div>

            <div class="hfl-task-drawer__metric">
              <RotateCcw
                :size="18"
                class="hfl-task-drawer__metric-icon hfl-task-drawer__metric-icon--indigo"
              />
              <div class="hfl-task-drawer__metric-copy">
                <span class="hfl-task-drawer__metric-label">{{ t('ops.task.retryCount') }}</span>
                <span class="hfl-task-drawer__metric-value">{{ t('ops.task.retryCountValue', { count: activeTask.retry_count }) }}</span>
              </div>
            </div>

            <div class="hfl-task-drawer__metric hfl-task-drawer__metric--wide">
              <Clock3
                :size="18"
                class="hfl-task-drawer__metric-icon"
              />
              <div class="hfl-task-drawer__time-grid">
                <div>
                  <span class="hfl-task-drawer__metric-label">{{ t('ops.task.startedAt') }}</span>
                  <span
                    class="hfl-task-drawer__time-value"
                    :class="{ 'hfl-empty-mark': !(activeTask.started_at || activeTask.created_at) }"
                  >{{ formatTime(activeTask.started_at || activeTask.created_at) }}</span>
                </div>
                <div>
                  <span class="hfl-task-drawer__metric-label">{{ t('ops.task.finishedAt') }}</span>
                  <span
                    class="hfl-task-drawer__time-value"
                    :class="{ 'hfl-empty-mark': !activeTask.finished_at }"
                  >{{ formatTime(activeTask.finished_at) }}</span>
                </div>
                <div>
                  <span class="hfl-task-drawer__metric-label">{{ t('ops.task.totalDuration') }}</span>
                  <span
                    class="hfl-task-drawer__time-value hfl-task-drawer__time-value--strong"
                    :class="{ 'hfl-empty-mark': taskDuration(activeTask) === t('ops.task.emptyMark') }"
                  >{{ taskDuration(activeTask) }}</span>
                </div>
              </div>
            </div>
          </div>

          <div class="hfl-task-drawer__progress-block">
            <div class="hfl-task-drawer__progress-head">
              <span>{{ t('ops.task.progressLabel') }}</span>
              <span>{{ progressText(activeTask) }}</span>
            </div>
            <div class="hfl-task-drawer__progress-track">
              <div
                class="hfl-task-drawer__progress-fill"
                :class="`hfl-task-drawer__progress-fill--${displayTaskStatus(activeTask)}`"
                :style="{ width: `${progressValue(activeTask)}%` }"
              />
            </div>
          </div>
        </section>

        <ElAlert
          v-if="['waiting', 'blocked'].includes(activeTask.status) && activeDependencies.length"
          :title="activeTask.status === 'blocked' ? t('ops.task.blockedTitle') : t('ops.task.waitingTitle')"
          type="warning"
          :closable="false"
          show-icon
        >
          <p>{{ activeTask.status === 'blocked' ? t('ops.task.blockedDescription') : t('ops.task.waitingDescription') }}</p>
          <ul>
            <li
              v-for="dependency in activeDependencies"
              :key="dependency.id"
            >
              <span>{{ dependency.detail }}</span>
              <ElButton
                v-if="dependency.blocking_task_uuid"
                link
                type="primary"
                @click="openTaskDetail(dependency.blocking_task_uuid)"
              >
                {{ t('ops.task.viewBlockingTask') }}
              </ElButton>
            </li>
          </ul>
        </ElAlert>

        <section
          v-if="showCleanupOutcome"
          class="hfl-task-drawer__cleanup-outcome"
          aria-live="polite"
        >
          <div class="hfl-task-drawer__cleanup-outcome-head">
            <AlertTriangle
              :size="17"
              aria-hidden="true"
            />
            <div>
              <strong>{{ cleanupOutcomeSucceeded ? t('ops.task.cleanupWarningTitle') : t('ops.task.cleanupIncompleteTitle') }}</strong>
              <p>{{ cleanupOutcomeSucceeded ? t('ops.task.cleanupWarningDescription') : t('ops.task.cleanupIncompleteDescription') }}</p>
            </div>
          </div>
          <div
            v-if="activeCleanupFailures.length"
            class="hfl-task-drawer__cleanup-group"
          >
            <span class="hfl-task-drawer__cleanup-label">{{ t('ops.task.cleanupFailures') }}</span>
            <ul>
              <li
                v-for="failure in activeCleanupFailures"
                :key="`${failure.sourceId || ''}:${failure.code}:${failure.detail}`"
              >
                <strong>{{ failure.sourceName || failure.code }}</strong>
                <span>{{ failure.detail }}</span>
              </li>
            </ul>
          </div>
          <div
            v-if="activeCleanupWarnings.length"
            class="hfl-task-drawer__cleanup-group"
          >
            <span class="hfl-task-drawer__cleanup-label">{{ t('ops.task.cleanupWarnings') }}</span>
            <ul>
              <li
                v-for="warning in activeCleanupWarnings"
                :key="`${warning.sourceId || ''}:${warning.code}:${warning.detail}`"
              >
                <strong>{{ warning.sourceName || warning.code }}</strong>
                <span>{{ warning.detail }}</span>
              </li>
            </ul>
          </div>
          <div
            v-if="activeRetainedResources.length"
            class="hfl-task-drawer__cleanup-group"
          >
            <span class="hfl-task-drawer__cleanup-label">{{ t('ops.task.retainedResources') }}</span>
            <ul>
              <li
                v-for="resource in activeRetainedResources"
                :key="resource"
              >
                <code>{{ resource }}</code>
              </li>
            </ul>
          </div>
          <div
            v-if="activeFailedCleanupChildren.length"
            class="hfl-task-drawer__cleanup-group"
          >
            <span class="hfl-task-drawer__cleanup-label">{{ t('ops.task.failedCleanupTasks') }}</span>
            <ul>
              <li
                v-for="child in activeFailedCleanupChildren"
                :key="child.taskUuid"
              >
                <button
                  type="button"
                  class="hfl-table-name-link"
                  @click="openTaskDetail(child.taskUuid)"
                >
                  {{ child.taskUuid }}
                </button>
                <span>{{ child.error }}</span>
              </li>
            </ul>
          </div>
          <ElAlert
            v-if="activeNeedsManualCleanup"
            type="warning"
            :closable="false"
            :title="t('ops.task.manualCleanupRequired')"
            :description="t('ops.task.manualCleanupDescription')"
            show-icon
          />
        </section>

        <ElTabs
          v-model="activeDetailTab"
          class="hfl-detail-tabs hfl-task-drawer__tabs"
          @tab-change="onDetailTabChange"
        >
          <ElTabPane name="steps">
            <template #label>
              <span class="hfl-detail-tab-label">
                {{ t('ops.task.steps') }}
                <span class="hfl-detail-tab-count">{{ detailStepCount }}</span>
              </span>
            </template>
            <section class="hfl-task-drawer__tab-panel">
              <div class="hfl-task-drawer__steps-head">
                <span>{{ t('ops.task.stepsHealthy') }}</span>
                <ElButton
                  v-if="hasExpandableSteps"
                  size="small"
                  class="hfl-btn-with-icon"
                  @click="toggleAllStepsExpanded"
                >
                  {{ hasAnyExpandedStep ? t('ops.task.collapseAll') : t('ops.task.expandAll') }}
                  <ChevronDown
                    v-if="hasAnyExpandedStep"
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
                v-if="stepsWithEvents.length > 0"
                class="hfl-task-drawer__step-list"
              >
                <div
                  v-for="(step, si) in stepsWithEvents"
                  :key="step.id"
                  class="hfl-task-drawer__step-item"
                  :class="{ 'hfl-task-drawer__step-item--last': si === stepsWithEvents.length - 1 && unlinkedEvents.length === 0 }"
                >
                  <div
                    class="hfl-task-drawer__step-anchor"
                    :class="timelineIconClass(step.status)"
                  >
                    <Check
                      v-if="step.status === 'success'"
                      :size="15"
                    />
                    <AlertTriangle
                      v-else-if="step.status === 'warning'"
                      :size="13"
                    />
                    <X
                      v-else-if="step.status === 'failed' || step.status === 'timeout'"
                      :size="15"
                    />
                    <Clock3
                      v-else-if="step.status === 'running'"
                      :size="15"
                    />
                    <Circle
                      v-else
                      :size="9"
                    />
                  </div>

                  <article class="hfl-task-drawer__step-card">
                    <button
                      type="button"
                      class="hfl-task-drawer__step-card-head"
                      :class="{ 'hfl-task-drawer__step-card-head--disabled': step.events.length === 0 }"
                      :aria-expanded="step.events.length > 0 && isStepExpanded(step.id)"
                      :aria-disabled="step.events.length === 0"
                      :aria-description="step.events.length === 0 ? t('ops.task.emptyEvents') : undefined"
                      @click="toggleStep(step.id, step.events.length)"
                    >
                      <span class="hfl-task-drawer__step-title">
                        {{ stepDisplayName(step.step_name, activeTask.task_type) }}
                        <span class="hfl-task-drawer__step-executed-at">
                          <span :class="{ 'hfl-empty-mark': !(step.created_at || activeTask.created_at) }">{{ formatTime(step.created_at || activeTask.created_at) }}</span>
                        </span>
                      </span>
                      <TaskStatusTag :status="step.status" />
                      <span class="hfl-task-drawer__step-duration">
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
                      v-if="isStepExpanded(step.id) && step.events.length > 0"
                      class="hfl-task-drawer__event-list"
                    >
                      <div
                        v-for="event in step.events"
                        :key="event.id"
                        class="hfl-task-drawer__event-row"
                      >
                        <span
                          class="hfl-task-drawer__event-dot"
                          :class="`hfl-task-drawer__event-dot--${eventTone(event)}`"
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
                        <span class="hfl-task-drawer__event-content">
                          <span
                            class="hfl-task-drawer__event-msg"
                            :class="eventMessageClass(event)"
                          >{{ eventDisplayMessage(event) }}</span>
                          <span
                            v-if="eventObjectText(event)"
                            class="hfl-task-drawer__event-object"
                          >
                            <Link2 :size="11" />
                            <span>{{ eventObjectText(event) }}</span>
                          </span>
                          <span
                            v-if="eventErrorText(event)"
                            class="hfl-task-drawer__event-error"
                          >{{ eventErrorText(event) }}</span>
                        </span>
                        <span
                          class="hfl-task-drawer__event-time"
                          :class="{ 'hfl-empty-mark': !event.created_at }"
                        >{{ formatTime(event.created_at) }}</span>
                      </div>
                    </div>
                  </article>
                </div>

                <div
                  v-if="unlinkedEvents.length > 0"
                  class="hfl-task-drawer__step-item hfl-task-drawer__step-item--last"
                >
                  <div class="hfl-task-drawer__step-anchor hfl-task-drawer__timeline-icon--muted">
                    <Circle :size="9" />
                  </div>
                  <article class="hfl-task-drawer__step-card">
                    <div class="hfl-task-drawer__step-card-head hfl-task-drawer__step-card-head--static">
                      <span class="hfl-task-drawer__step-title">{{ t('ops.task.events') }}</span>
                      <span class="hfl-task-drawer__step-duration">{{ taskDuration(activeTask) }}</span>
                    </div>
                    <div class="hfl-task-drawer__event-list">
                      <div
                        v-for="event in unlinkedEvents"
                        :key="event.id"
                        class="hfl-task-drawer__event-row"
                      >
                        <span
                          class="hfl-task-drawer__event-dot"
                          :class="`hfl-task-drawer__event-dot--${eventTone(event)}`"
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
                        <span class="hfl-task-drawer__event-content">
                          <span
                            class="hfl-task-drawer__event-msg"
                            :class="eventMessageClass(event)"
                          >{{ eventDisplayMessage(event) }}</span>
                          <span
                            v-if="eventObjectText(event)"
                            class="hfl-task-drawer__event-object"
                          >
                            <Link2 :size="11" />
                            <span>{{ eventObjectText(event) }}</span>
                          </span>
                          <span
                            v-if="eventErrorText(event)"
                            class="hfl-task-drawer__event-error"
                          >{{ eventErrorText(event) }}</span>
                        </span>
                        <span
                          class="hfl-task-drawer__event-time"
                          :class="{ 'hfl-empty-mark': !event.created_at }"
                        >{{ formatTime(event.created_at) }}</span>
                      </div>
                    </div>
                  </article>
                </div>
              </div>

              <div
                v-else-if="detailEvents.length > 0"
                class="hfl-task-drawer__event-only"
              >
                <div
                  v-for="event in detailEvents"
                  :key="event.id"
                  class="hfl-task-drawer__event-row"
                >
                  <span
                    class="hfl-task-drawer__event-dot"
                    :class="`hfl-task-drawer__event-dot--${eventTone(event)}`"
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
                  <span class="hfl-task-drawer__event-content">
                    <span
                      class="hfl-task-drawer__event-msg"
                      :class="eventMessageClass(event)"
                    >{{ eventDisplayMessage(event) }}</span>
                    <span
                      v-if="eventObjectText(event)"
                      class="hfl-task-drawer__event-object"
                    >
                      <Link2 :size="11" />
                      <span>{{ eventObjectText(event) }}</span>
                    </span>
                    <span
                      v-if="eventErrorText(event)"
                      class="hfl-task-drawer__event-error"
                    >{{ eventErrorText(event) }}</span>
                  </span>
                  <span class="hfl-task-drawer__event-time">#{{ event.seq }} · <span :class="{ 'hfl-empty-mark': !event.created_at }">{{ formatTime(event.created_at) }}</span></span>
                </div>
              </div>

              <el-empty
                v-else
                :description="t('ops.task.emptySteps')"
                :image-size="52"
              />
            </section>
          </ElTabPane>

          <ElTabPane name="resources">
            <template #label>
              <span class="hfl-detail-tab-label">
                {{ t('ops.task.resources') }}
                <span class="hfl-detail-tab-count">{{ detailResourceCount }}</span>
              </span>
            </template>
            <section class="hfl-task-drawer__tab-panel">
              <div
                v-if="resourceTypeTabs.length > 0"
                class="hfl-task-drawer__resource-view"
              >
                <div class="hfl-task-drawer__resource-switcher">
                  <button
                    v-for="item in resourceTypeTabs"
                    :key="item.type"
                    type="button"
                    class="hfl-task-drawer__resource-switch"
                    :class="{ 'hfl-task-drawer__resource-switch--active': selectedResourceType === item.type }"
                    @click="loadResourceType(item.type)"
                  >
                    <Link2 :size="14" />
                    {{ item.label }}
                    <span class="hfl-task-drawer__resource-switch-count">{{ item.count }}</span>
                  </button>
                </div>

                <div
                  v-if="resourceErrors[selectedResourceType]"
                  class="hfl-task-drawer__resource-error"
                >
                  {{ resourceErrors[selectedResourceType] }}
                </div>

                <el-table
                  v-table-column-resize="'ops.tasks.resources'"
                  v-table-overflow-title
                  v-loading="resourceLoading"
                  :data="selectedResourceRows"
                  class="hfl-list-table hfl-list-table--compact hfl-task-drawer__resource-table"
                >
                  <el-table-column
                    :label="t('ops.task.resourceId')"
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
                      <div v-else>
                        <div class="hfl-task-drawer__resource-name">
                          {{ row.name }}
                        </div>
                        <div class="hfl-task-drawer__resource-summary">
                          {{ row.type }}
                        </div>
                      </div>
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
                        :class="{ 'hfl-empty-mark': !row.endpointIp && !row.endpointName }"
                      >{{ row.endpointIp || row.endpointName || t('ops.task.emptyMark') }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column
                    v-if="selectedResourceType !== 'backup_source'"
                    :label="t('ops.task.nameLabel')"
                    :min-width="isRepositoryResourceType ? 330 : 180"
                  >
                    <template #default="{ row }">
                      <div class="hfl-task-drawer__resource-name">
                        {{ row.name }}
                      </div>
                      <div class="hfl-task-drawer__resource-summary">
                        {{ row.summary }}
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column
                    v-if="selectedResourceType !== 'backup_source'"
                    :label="t('ops.task.resourceTypeLabel')"
                    :width="isRepositoryResourceType ? 195 : 140"
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
                    :width="isRepositoryResourceType ? 165 : 110"
                  >
                    <template #default="{ row }">
                      <ElTag
                        v-if="selectedResourceType === 'backup_source' && row.availability"
                        :type="backupSourceConnectivityTagType(row.availability)"
                        size="small"
                      >
                        {{ backupSourceConnectivityLabel(row.availability) }}
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
                    :label="selectedResourceType === 'backup_source'
                      ? t('protection.backupsPage.colRegistered')
                      : isRepositoryResourceType
                        ? t('ops.task.registeredAt')
                        : t('ops.task.updatedAt')"
                    width="180"
                  >
                    <template #default="{ row }">
                      <span
                        class="hfl-table-cell-time"
                        :class="{ 'hfl-empty-mark': !(selectedResourceType === 'backup_source' || isRepositoryResourceType ? row.registeredAt : row.updatedAt) }"
                      >{{ formatTime(selectedResourceType === 'backup_source' || isRepositoryResourceType ? row.registeredAt : row.updatedAt) }}</span>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
              <el-empty
                v-else
                :description="t('ops.task.emptyResources')"
                :image-size="52"
              />
            </section>
          </ElTabPane>
        </ElTabs>
      </div>
      <div
        v-else
        ref="drawerScrollAnchorRef"
        class="hfl-task-drawer__body"
      >
        <div
          class="hfl-task-drawer__loading"
          aria-busy="true"
        >
          <el-skeleton
            :rows="6"
            animated
          />
        </div>
      </div>
    </ElDrawer>
    <ProtectionStopConfirmDialog
      v-if="stopConfirmOpen"
      v-model="stopConfirmOpen"
      :kind="stopConfirmKind"
      :items="stopConfirmItems"
      :loading="actionBusy"
      @confirm="stopConfirmDialog.settleConfirm()"
      @cancel="stopConfirmDialog.settleCancel()"
    />
  </ModulePage>
</template>

<style scoped>
.hfl-task-filter-drawer :deep(.el-drawer__body) {
  padding: 18px 20px;
}

.hfl-task-filter-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.hfl-task-drawer :deep(.el-drawer__header) {
  margin: 0;
  padding: 10px 24px 8px;
  border-bottom: 1px solid rgb(241 245 249);
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(8px);
}

.hfl-task-meta-tag {
  max-width: 100%;
  vertical-align: middle;
}

.hfl-task-meta-tag :deep(.el-tag__content) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:global(.hfl-task-name-overflow-tooltip.el-popper) {
  max-width: min(520px, calc(100vw - 32px));
  white-space: pre-line;
  overflow-wrap: anywhere;
  line-height: 1.55;
}

.hfl-task-drawer :deep(.el-drawer__body) {
  padding: 0;
  background: #fff;
}

.hfl-task-drawer__header-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
}

.hfl-task-drawer__header-summary {
  min-width: 0;
}

.hfl-task-drawer__header-title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.hfl-task-drawer__header-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  line-height: 1.25;
  color: rgb(15 23 42);
  overflow-wrap: anywhere;
}

.hfl-task-drawer__header-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  min-width: 0;
  font-size: 12px;
  color: rgb(100 116 139);
}

.hfl-task-drawer__header-divider {
  align-self: center;
  width: 1px;
  height: 12px;
  background: rgb(203 213 225);
}

.hfl-task-drawer__header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.hfl-task-drawer__cancel-button {
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

.hfl-task-drawer__cancel-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.hfl-task-drawer__cancel-button:hover:not(:disabled) {
  border-color: var(--color-error);
  background: var(--color-error);
  color: #fff;
}

.hfl-task-drawer__cancel-button:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--color-error) 42%, transparent);
  outline-offset: 2px;
}

.hfl-task-drawer__cancel-button:disabled {
  border-color: var(--color-error-border);
  background: var(--color-error-light);
  color: var(--color-error);
  opacity: 0.55;
}

.hfl-task-drawer__body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 2px 24px 24px;
  background: #fff;
}

.hfl-task-drawer__loading {
  min-height: 320px;
  padding: 16px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 12px;
  background: rgb(248 250 252);
}

.hfl-task-drawer__hero {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin: 0;
  padding: 16px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 12px;
  background: rgb(248 250 252);
  box-shadow: inset 0 1px 1px rgba(15, 23, 42, 0.03);
}

.hfl-task-drawer__hero-section-title {
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: rgb(100 116 139);
}

.hfl-task-drawer__uuid {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 11px;
  font-weight: 700;
  color: rgb(71 85 105);
  overflow-wrap: anywhere;
}

.hfl-task-drawer__copy-button {
  align-self: center;
  width: 20px;
  height: 20px;
  padding: 0;
  color: rgb(100 116 139);
}

.hfl-task-drawer__hero-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}

@media (min-width: 720px) {
  .hfl-task-drawer__hero-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.hfl-task-drawer__metric {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  min-width: 0;
  padding: 14px;
  border: 1px solid var(--color-border-light, #f1f5f9);
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
}

.hfl-task-drawer__metric--wide {
  grid-column: 1 / -1;
}

.hfl-task-drawer__metric-icon {
  flex: 0 0 18px;
  margin-top: 2px;
  color: rgb(100 116 139);
}

.hfl-task-drawer__metric-icon--blue {
  color: rgb(14 165 233);
}

.hfl-task-drawer__metric-icon--indigo {
  color: rgb(79 70 229);
}

.hfl-task-drawer__metric-copy {
  min-width: 0;
}

.hfl-task-drawer__metric-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: rgb(148 163 184);
}

.hfl-task-drawer__metric-value {
  display: block;
  margin-top: 4px;
  font-size: 14px;
  font-weight: 800;
  color: rgb(15 23 42);
}

.hfl-task-drawer__metric-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
}

.hfl-task-drawer__time-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  width: 100%;
  min-width: 0;
}

@media (min-width: 720px) {
  .hfl-task-drawer__time-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

.hfl-task-drawer__time-value {
  display: block;
  margin-top: 4px;
  min-width: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  font-weight: 600;
  color: rgb(51 65 85);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hfl-task-drawer__time-value.hfl-empty-mark {
  font-family: inherit;
  font-size: 13px;
  font-weight: 400;
}

.hfl-task-drawer__time-value--strong {
  color: var(--color-info);
}

.hfl-task-drawer__progress-block {
  padding-top: 2px;
}

.hfl-task-drawer__progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 700;
  color: rgb(100 116 139);
}

.hfl-task-drawer__progress-head span:last-child {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  color: var(--color-info);
}

.hfl-task-drawer__progress-track {
  height: 6px;
  overflow: hidden;
  border-radius: 999px;
  background-color: rgb(226 232 240);
}

.hfl-task-drawer__progress-fill {
  height: 100%;
  min-width: 4px;
  border-radius: inherit;
  background-color: var(--color-info);
  transition: width 0.35s ease;
}

.hfl-task-drawer__progress-fill--success {
  background-color: var(--color-success);
}

.hfl-task-drawer__progress-fill--partial {
  background-color: var(--color-warning);
}

.hfl-task-drawer__progress-fill--failed,
.hfl-task-drawer__progress-fill--timeout {
  background-color: var(--color-error);
}

.hfl-task-drawer__progress-fill--pending,
.hfl-task-drawer__progress-fill--cancelled {
  background-color: rgb(100 116 139);
}

.hfl-task-drawer__timeline-icon--success {
  border-color: var(--color-success);
  background-color: var(--color-success);
  color: #fff;
}

.hfl-task-drawer__timeline-icon--danger {
  border-color: var(--color-error);
  background-color: var(--color-error);
  color: #fff;
}

.hfl-task-drawer__timeline-icon--warning {
  border-color: var(--color-warning);
  background-color: var(--color-warning);
  color: #fff;
}

.hfl-task-drawer__timeline-icon--running {
  border-color: var(--color-info);
  background-color: var(--color-info);
  color: #fff;
}

.hfl-task-drawer__timeline-icon--muted {
  border-color: rgb(100 116 139);
  background-color: rgb(100 116 139);
  color: #fff;
}

.hfl-task-drawer__timeline-icon--pending {
  background-color: rgb(100 116 139);
  color: #fff;
}

.hfl-task-drawer__tabs {
  min-width: 0;
  margin-top: 4px;
}

.hfl-task-drawer__cleanup-outcome {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--color-warning-border);
  border-radius: 12px;
  background: var(--color-warning-light);
  color: rgb(120 53 15);
}

.hfl-task-drawer__cleanup-outcome-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.hfl-task-drawer__cleanup-outcome-head > svg {
  flex: 0 0 auto;
  margin-top: 2px;
}

.hfl-task-drawer__cleanup-outcome-head strong {
  font-size: 13px;
}

.hfl-task-drawer__cleanup-outcome-head p {
  margin: 3px 0 0;
  font-size: 12px;
  line-height: 1.5;
}

.hfl-task-drawer__cleanup-group {
  padding-top: 10px;
  border-top: 1px solid color-mix(in srgb, var(--color-warning-border) 75%, transparent);
}

.hfl-task-drawer__cleanup-label {
  display: block;
  margin-bottom: 5px;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.hfl-task-drawer__cleanup-group ul {
  display: grid;
  gap: 5px;
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.5;
}

.hfl-task-drawer__cleanup-group li span {
  display: block;
}

.hfl-task-drawer__cleanup-group code {
  overflow-wrap: anywhere;
  color: inherit;
}

.hfl-task-drawer__tab-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.hfl-task-drawer__steps-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 700;
}

.hfl-task-drawer__step-list {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.hfl-task-drawer__step-item {
  position: relative;
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 12px;
}

.hfl-task-drawer__step-item::before {
  content: '';
  position: absolute;
  left: 12px;
  top: 34px;
  bottom: -18px;
  width: 0;
  border-left: 2px dashed rgb(226 232 240);
}

.hfl-task-drawer__step-item--last::before {
  display: none;
}

.hfl-task-drawer__step-anchor {
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

.hfl-task-drawer__step-card {
  min-width: 0;
  padding: 14px 16px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.hfl-task-drawer__step-card:hover {
  border-color: rgb(203 213 225);
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.06);
}

.hfl-task-drawer__step-card-head {
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

.hfl-task-drawer__step-card-head--static {
  cursor: default;
}

.hfl-task-drawer__step-title {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  font-weight: 800;
  line-height: 1.45;
  color: rgb(15 23 42);
  overflow-wrap: anywhere;
}

.hfl-task-drawer__step-executed-at {
  display: inline-flex;
  margin-left: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 11px;
  font-weight: 700;
  color: rgb(71 85 105);
}

.hfl-task-drawer__step-duration {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  padding: 2px 7px;
  border: 1px solid rgb(241 245 249);
  border-radius: 6px;
  background: rgb(248 250 252);
  color: rgb(100 116 139);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 11px;
  font-weight: 700;
}

.hfl-task-drawer__step-created {
  font-size: 11px;
  font-weight: 700;
  color: rgb(148 163 184);
}

.hfl-task-drawer__event-list,
.hfl-task-drawer__event-only {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid rgb(241 245 249);
}

.hfl-task-drawer__event-only {
  margin-top: 0;
  padding: 14px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 12px;
}

.hfl-task-drawer__event-row {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr) auto;
  gap: 8px;
  align-items: start;
  min-width: 0;
}

.hfl-task-drawer__event-dot {
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

.hfl-task-drawer__event-dot--danger {
  background-color: var(--color-error);
}

.hfl-task-drawer__event-dot--warning {
  background-color: var(--color-warning);
}

.hfl-task-drawer__event-dot--running {
  background-color: var(--color-info);
}

.hfl-task-drawer__event-dot--muted {
  background-color: rgb(100 116 139);
}

.hfl-task-drawer__event-msg {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 12px;
  line-height: 1.55;
  font-weight: 400;
  color: rgb(51 65 85);
}

.hfl-task-drawer__event-content {
  display: flex;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}

.hfl-task-drawer__event-object {
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

.hfl-task-drawer__event-object svg {
  margin-top: 2px;
  flex: 0 0 auto;
  color: rgb(100 116 139);
}

.hfl-task-drawer__event-error {
  display: block;
  max-width: 100%;
  color: rgb(185 28 28);
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.hfl-task-drawer__event-msg--danger {
  color: rgb(185 28 28);
}

.hfl-task-drawer__event-msg--muted {
  color: rgb(100 116 139);
}

.hfl-task-drawer__event-time {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgb(100 116 139);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 11px;
  font-weight: 600;
}

.hfl-task-drawer__event-time.hfl-empty-mark {
  font-family: inherit;
  font-size: 13px;
  font-weight: 400;
}

.hfl-task-drawer__panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  padding: 16px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 12px;
  background: #fff;
}

.hfl-task-drawer__panel--code {
  background: rgb(248 250 252);
}

.hfl-task-drawer__panel-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 10px;
  border-bottom: 1px solid rgb(241 245 249);
  color: rgb(15 23 42);
}

.hfl-task-drawer__panel-icon {
  color: rgb(43 125 196);
  flex: 0 0 16px;
}

.hfl-task-drawer__panel-title {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.hfl-task-drawer__resource-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}

.hfl-task-drawer__resource-view {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.hfl-task-drawer__resource-switcher {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.hfl-task-drawer__resource-switch {
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

.hfl-task-drawer__resource-switch:hover {
  border-color: var(--color-info-border);
  background: var(--color-info-light);
  color: var(--color-info);
}

.hfl-task-drawer__resource-switch--active {
  border-color: var(--color-info);
  background: var(--color-info-light);
  color: var(--color-info);
}

.hfl-task-drawer__resource-error {
  padding: 10px 12px;
  border: 1px solid rgb(254 202 202);
  border-radius: 8px;
  background: rgb(254 242 242);
  color: rgb(185 28 28);
  font-size: 12px;
  font-weight: 600;
}

.hfl-task-drawer__resource-table {
  border-radius: 8px;
  overflow: hidden;
}

.hfl-task-drawer__resource-name {
  font-size: 13px;
  font-weight: 700;
  color: rgb(15 23 42);
}

.hfl-task-drawer__resource-summary {
  margin-top: 2px;
  font-size: 11px;
  color: rgb(100 116 139);
}

.hfl-task-drawer__resource-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 10px;
  background: rgb(248 250 252);
  transition: border-color 0.15s ease;
}

.hfl-task-drawer__resource-card:hover {
  border-color: rgb(43 125 196);
}

.hfl-task-drawer__resource-type {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: rgb(71 85 105);
  font-weight: 600;
}

.hfl-task-drawer__resource-id {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 13px;
  font-weight: 600;
  color: rgb(15 23 42);
}

.hfl-task-drawer__empty-line {
  padding: 6px 0;
  font-size: 13px;
  color: rgb(148 163 184);
}

@media (max-width: 760px) {
  .hfl-task-drawer :deep(.el-drawer__header) {
    padding: 10px 18px 8px;
  }

  .hfl-task-drawer :deep(.el-drawer__body) {
    padding: 0;
  }

  .hfl-task-drawer__body {
    padding: 2px 18px 18px;
  }

  .hfl-task-drawer__header-title {
    font-size: 21px;
  }

  .hfl-task-drawer__steps-head {
    flex-direction: column;
    align-items: stretch;
  }

  .hfl-task-drawer__step-card-head,
  .hfl-task-drawer__event-row {
    grid-template-columns: 1fr;
  }

  .hfl-task-drawer__step-card-head {
    flex-wrap: wrap;
  }

  .hfl-task-drawer__event-time {
    grid-column: 2;
    max-width: 100%;
  }
}
</style>
