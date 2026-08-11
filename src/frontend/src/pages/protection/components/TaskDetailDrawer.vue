<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import {
  Check,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleStop,
  Clock3,
  Copy,
  Globe,
  Link2,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  X,
} from 'lucide-vue-next'
import { apiErrorMessage, apiErrorMessageI18n } from '../../../lib/api'
import { copyTextToClipboard } from '../../../lib/clipboard'
import { formatLocalDateTime } from '../../../lib/dateTime'
import { formatTaskProgressBarPercent, formatTaskProgressPercent } from '../../../lib/kopiaProgress'
import { getNode } from '../../../lib/nodeApi'
import { getBackupSourceSnapshot } from '../../../lib/protectionBackupConfigApi'
import { cancelProtectionBackupTask } from '../../../lib/protectionBackupTaskApi'
import { cancelProtectionRestoreTask } from '../../../lib/protectionRestoreTaskApi'
import {
  buildStopConfirmItemFromTask,
} from '../../../lib/protectionStopConfirm'
import { useProtectionStopConfirmDialog } from '../../../composables/useProtectionStopConfirmDialog'
import { useDrawerScrollReset } from '../../../composables/useDrawerScrollReset'
import { useRepositoryTaskCancellation } from '../../../composables/useRepositoryTaskCancellation'
import ProtectionStopConfirmDialog from '../../../components/ProtectionStopConfirmDialog.vue'
import { getSourceResource } from '../../../lib/sourceApi'
import {
  cancelStorageRepositoryTask,
  getStorageRepository,
} from '../../../lib/storageRepositoryApi'
import {
  canCancelRepositoryTask,
} from '../../../lib/repositoryTaskCancellation'
import { taskResourceSnapshot } from '../../../lib/taskOutcomeDisplay'
import { lifecycleStatusTagAttrs } from '../../../lib/statusTag'
import { resolveTaskBackupSourceResource, resolveTaskBackupSourceResourceFromPayload } from '../../../lib/taskBackupSourceResource'
import { parseTaskStepStatusEvent, taskEventMessageKey, taskEventObjectText } from '../../../lib/taskEventDisplay'
import { hasExpandableTaskStep, hasExpandedTaskStep } from '../../../lib/taskStepExpansion'
import TaskStatusTag from '../../../components/TaskStatusTag.vue'
import FlowSourceSummaryCell from './FlowSourceSummaryCell.vue'
import FlowSourceConnectionCell from './FlowSourceConnectionCell.vue'
import { cancelTask, getTask, listTaskEvents, recheckTask, type TaskEventRow, type TaskResourceRow, type TaskRow } from '../../../lib/taskApi'

const props = withDefaults(defineProps<{
  modelValue: boolean
  taskUuid?: string
  drawerSize?: string
  readOnly?: boolean
  resourceListMode?: 'default' | 'target_repositories'
}>(), {
  taskUuid: '',
  drawerSize: '700px',
  readOnly: false,
  resourceListMode: 'default',
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'open-task': [taskUuid: string]
  'task-updated': [task: TaskRow]
}>()

const { t, te } = useI18n()
const stopConfirmDialog = useProtectionStopConfirmDialog()
const stopConfirmOpen = stopConfirmDialog.open
const stopConfirmKind = stopConfirmDialog.kind
const stopConfirmItems = stopConfirmDialog.items
const { drawerScrollAnchorRef, resetDrawerScroll } = useDrawerScrollReset()
const activeTask = ref<TaskRow | null>(null)
const detailEvents = ref<TaskEventRow[]>([])
const detailRefreshing = ref(false)
const actionBusy = ref(false)
const taskOwner = ref('')
const activeDetailTab = ref<'steps' | 'resources'>('steps')
const selectedResourceType = ref('')
const backupSourceRows = ref<Array<{
  id: number
  backupSource: string
  endpointName: string
  endpointIp: string
  status: string
  statusValue: string
  registeredAt: string
  flowSource: Awaited<ReturnType<typeof resolveTaskBackupSourceResource>>['flowSource']
}>>([])
const backupSourceLoading = ref(false)
type RepositoryResourceRow = {
  key: string
  id: number
  name: string
  repoType: string
  health: string
  registeredAt: string
}
const repositoryResourceRows = ref<RepositoryResourceRow[]>([])
const repositoryResourceLoading = ref(false)
const repositoryResourceLoadError = ref('')
let repositoryResourceController: AbortController | null = null
const expandedSteps = reactive<Record<string, boolean>>({})

const drawerOpen = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const cleanupMetadata = computed(() => activeTask.value?.repository_cleanup || null)
const activeDependencies = computed(() =>
  (activeTask.value?.dependencies || []).filter((dependency) => dependency.is_active),
)

function openTriggeredCleanupTask() {
  const taskUuid = cleanupMetadata.value?.triggered_by_task_uuid
  if (taskUuid) emit('open-task', taskUuid)
}

function openBlockingTask(taskUuid?: string | null) {
  if (taskUuid) emit('open-task', taskUuid)
}

const stepsWithEvents = computed(() => {
  const grouped: Record<number, TaskEventRow[]> = {}
  for (const event of detailEvents.value) {
    if (event.step_id == null) continue
    if (!grouped[event.step_id]) grouped[event.step_id] = []
    grouped[event.step_id].push(event)
  }
  return (activeTask.value?.steps || []).map((step) => ({
    ...step,
    events: grouped[step.id] || [],
  }))
})
const hasExpandableSteps = computed(() => hasExpandableTaskStep(stepsWithEvents.value))
const hasAnyExpandedStep = computed(() => hasExpandedTaskStep(stepsWithEvents.value, isStepExpanded))

const unlinkedEvents = computed(() => {
  const stepIds = new Set((activeTask.value?.steps || []).map((step) => step.id))
  return detailEvents.value.filter((event) => event.step_id == null || !stepIds.has(event.step_id))
})

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

const flatResources = computed(() => (activeTask.value?.resources || []).map((resource) => ({
  key: `${resource.resource_type}:${resource.resource_id}`,
  id: resource.resource_id,
  rawType: resource.resource_type,
  type: labelFor('resourceType', resource.resource_type),
  subtype: valueLabel(resource.resource_subtype),
})))

const selectedResources = computed(() => {
  if (!selectedResourceType.value) return flatResources.value
  return flatResources.value.filter((resource) => resource.rawType === selectedResourceType.value)
})

const targetRepositoryResources = computed(() => (activeTask.value?.resources || []).filter((resource) =>
  resource.resource_type === 'repository' || resource.resource_type === 'target_repository',
))

const usesTargetRepositoryResources = computed(() => props.resourceListMode === 'target_repositories')

const canCancel = computed(() => {
  if (props.readOnly) return false
  if (activeTask.value?.actions) return activeTask.value.actions.can_cancel
  if (activeTask.value?.task_type === 'source_unregister') {
    return activeTask.value.status === 'waiting' || activeTask.value.status === 'blocked'
  }
  if (activeTask.value?.task_type === 'repository_operation') {
    return canCancelRepositoryTask(activeTask.value)
  }
  const status = activeTask.value?.status
  return status === 'pending' || status === 'waiting' || status === 'running'
})
const canRecheck = computed(() => !props.readOnly && Boolean(
  activeTask.value?.actions?.can_recheck
  ?? (
    activeTask.value?.task_type === 'source_unregister'
    && ['waiting', 'blocked'].includes(activeTask.value?.status || '')
  ),
))
const {
  pending: repositoryCancellationPending,
  stop: stopRepositoryCancellation,
  sync: syncRepositoryCancellation,
} = useRepositoryTaskCancellation(activeTask, {
  onUpdate: async (task) => {
    activeTask.value = task
    emit('task-updated', task)
  },
  onTerminal: async (task) => {
    const events = await listTaskEvents(task.task_uuid, { page: 1, page_size: 300 })
    if (activeTask.value?.task_uuid !== task.task_uuid) return
    detailEvents.value = events.results
    initExpandedSteps(task)
    if (task.status === 'cancelled') {
      ElMessage.success(t('ops.task.repositoryCancelComplete'))
    }
  },
  onError: () => ElMessage.warning(t('ops.task.repositoryCancelPollFailed')),
})

function labelFor(scope: 'status' | 'taskType' | 'triggerType' | 'resourceType', value?: string | null) {
  if (!value) return t('ops.task.emptyMark')
  const key = `ops.task.${scope}.${value}`
  return te(key) ? t(key) : t('ops.task.unknownValue')
}

function valueLabel(value?: string | null) {
  if (!value) return t('ops.task.emptyMark')
  const key = `ops.task.resourceValue.${value}`
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

function taskDescription(row: TaskRow) {
  return row.display_name || `${labelFor('taskType', row.task_type)} #${row.id}`
}

function payloadRecord(task?: TaskRow | null) {
  return task?.request_payload && typeof task.request_payload === 'object'
    ? task.request_payload as Record<string, unknown>
    : {}
}

function objectValue(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key]
    if (value !== undefined && value !== null && value !== '') return String(value)
  }
  return ''
}

function taskResourceSubtype(resource: TaskResourceRow, task: TaskRow) {
  if (resource.resource_subtype) return resource.resource_subtype
  if (resource.resource_type !== 'backup_source') return ''
  return objectValue(payloadRecord(task), ['source_type']).toLowerCase()
}

async function fetchResourceDetail(resource: TaskResourceRow, task: TaskRow) {
  const { resource_type: type, resource_id: id } = resource
  const subtype = taskResourceSubtype(resource, task)
  if (type === 'backup_source' && subtype === 'agent') return getNode(id)
  if (type === 'backup_source') return getSourceResource(id)
  if (type === 'repository' || type === 'target_repository') return getStorageRepository(id)
  if (type === 'snapshot') return getBackupSourceSnapshot(id)
  if (type === 'host') return getNode(id)
  return { id, resource_type: type, resource_subtype: subtype }
}

async function loadTaskOwner(task: TaskRow) {
  const resource = task.primary_resource || task.resources?.[0]
  if (!resource) {
    taskOwner.value = t('ops.task.emptyMark')
    return
  }
  // For source_unregister tasks, resolve the owner from immutable task payload
  // data first, since the live resource has already been removed.
  if (task.task_type === 'source_unregister' && resource.resource_type === 'backup_source') {
    const fromPayload = resolveTaskBackupSourceResourceFromPayload(resource, task)
    if (fromPayload && activeTask.value?.task_uuid === task.task_uuid) {
      taskOwner.value = fromPayload.backupSource || t('ops.task.emptyMark')
      return
    }
  }
  try {
    const detail = await fetchResourceDetail(resource, task)
    const name = objectValue(detail as Record<string, unknown>, ['name', 'display_name', 'hostname', 'snapshot_uid'])
    if (activeTask.value?.task_uuid === task.task_uuid) taskOwner.value = name || t('ops.task.emptyMark')
  } catch {
    // Fallback: try to resolve from task payload snapshot
    const snapshot = taskResourceSnapshot(task, resource)
    if (activeTask.value?.task_uuid === task.task_uuid) {
      taskOwner.value = snapshot
        ? objectValue(snapshot, ['name', 'display_name', 'hostname'])
        : t('ops.task.emptyMark')
    }
  }
}

function taskDuration(row: TaskRow) {
  const start = new Date(row.started_at || row.created_at || '').getTime()
  const end = new Date(row.finished_at || '').getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return t('ops.task.emptyMark')
  const seconds = Math.floor((end - start) / 1000)
  const minutes = Math.floor(seconds / 60)
  const restSeconds = seconds % 60
  if (minutes <= 0) return `${restSeconds}s`
  return `${minutes}m ${restSeconds}s`
}

function taskEventMetadata(event: TaskEventRow): Record<string, unknown> {
  return event.metadata && typeof event.metadata === 'object' ? event.metadata as Record<string, unknown> : {}
}

function taskEventMetadataText(event: TaskEventRow, keys: string[]) {
  const metadata = taskEventMetadata(event)
  for (const key of keys) {
    const value = metadata[key]
    if (value !== undefined && value !== null && value !== '') return String(value)
  }
  return ''
}

function eventErrorText(event: TaskEventRow) {
  const message = taskEventMetadataText(event, ['error_message'])
  if (!message) return ''
  const code = taskEventMetadataText(event, ['error_code'])
  return code ? `[${code}] ${message}` : message
}

function eventObjectText(event: TaskEventRow) {
  const canonicalValue = taskEventObjectText(event)
  if (canonicalValue) return canonicalValue
  return taskEventMetadataText(event, [
    'kopia_snapshot_display',
    'source_path',
    'target_path',
    'node_task_id',
    'task_uuid',
  ])
}

function eventTone(event: TaskEventRow): 'success' | 'warning' | 'danger' | 'running' | 'muted' {
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

function timelineIconClass(status: string) {
  if (status === 'success') return 'hfl-task-drawer__timeline-icon--success'
  if (status === 'failed' || status === 'timeout') return 'hfl-task-drawer__timeline-icon--danger'
  if (status === 'running') return 'hfl-task-drawer__timeline-icon--running'
  if (status === 'cancelled') return 'hfl-task-drawer__timeline-icon--muted'
  return 'hfl-task-drawer__timeline-icon--pending'
}

function stepDisplayName(stepName: string) {
  const key = `ops.task.step.${stepName}`
  return te(key) ? t(key) : t('ops.task.unknownValue')
}

function stepDuration(index: number) {
  const step = stepsWithEvents.value[index]
  const next = stepsWithEvents.value[index + 1]
  const start = step?.events[0]?.created_at || step?.created_at || activeTask.value?.started_at || activeTask.value?.created_at
  const end = next?.events[0]?.created_at || step?.events[step.events.length - 1]?.created_at || activeTask.value?.finished_at
  if (!start || !end) return t('ops.task.emptyMark')
  return taskDuration({ ...(activeTask.value as TaskRow), started_at: start, finished_at: end })
}

function stepKey(stepId: number | string) {
  return String(stepId)
}

function initExpandedSteps(task?: TaskRow | null) {
  for (const key of Object.keys(expandedSteps)) delete expandedSteps[key]
  for (const step of task?.steps || []) expandedSteps[stepKey(step.id)] = true
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
  for (const step of activeTask.value?.steps || []) expandedSteps[stepKey(step.id)] = expanded
}

function toggleAllStepsExpanded() {
  setAllStepsExpanded(!hasAnyExpandedStep.value)
}

function selectResourceType(type: string) {
  selectedResourceType.value = type
  if (type === 'backup_source') void loadBackupSourceRows()
}

function repositoryTypeLabel(type: string) {
  const normalized = String(type || '').trim().toLowerCase()
  if (normalized === 's3') return t('protection.backupsPage.repoTypeS3')
  if (normalized === 'nas') return t('protection.backupsPage.repoTypeNas')
  if (normalized === 'proxy_fs') return t('protection.backupsPage.repoTypeProxyFs')
  return normalized ? t('ops.task.unknownValue') : t('ops.task.emptyMark')
}

function repositoryTypeTagClass(type: string) {
  return `wizard-repo-type-tag wizard-repo-type-tag--${String(type || '').trim().toLowerCase()}`
}

function repositoryHealthLabel(health: string) {
  const normalized = String(health || '').trim().toLowerCase()
  if (normalized === 'online') return t('repositoriesPage.healthOnline')
  if (normalized === 'unverified') return t('repositoriesPage.healthUnverified')
  if (normalized === 'offline') return t('repositoriesPage.healthOffline')
  return normalized ? t('ops.task.unknownValue') : t('ops.task.emptyMark')
}

function resetRepositoryResourceRows() {
  repositoryResourceController?.abort()
  repositoryResourceController = null
  repositoryResourceRows.value = []
  repositoryResourceLoadError.value = ''
  repositoryResourceLoading.value = false
}

async function loadTargetRepositoryResources() {
  if (!usesTargetRepositoryResources.value || repositoryResourceLoading.value || repositoryResourceRows.value.length) return
  const resources = targetRepositoryResources.value
  if (!resources.length) return
  repositoryResourceController?.abort()
  const controller = new AbortController()
  repositoryResourceController = controller
  repositoryResourceLoading.value = true
  repositoryResourceLoadError.value = ''
  try {
    const results = await Promise.allSettled(resources.map(async (resource) => {
      const repository = await getStorageRepository(resource.resource_id, { signal: controller.signal })
      return {
        key: `${resource.resource_type}:${resource.resource_id}`,
        id: resource.resource_id,
        name: repository.name || t('ops.task.emptyMark'),
        repoType: repository.repo_type || '',
        health: repository.health || '',
        registeredAt: repository.created_at || '',
      }
    }))
    if (controller.signal.aborted || repositoryResourceController !== controller) return
    repositoryResourceRows.value = results.map((result, index) => result.status === 'fulfilled'
      ? result.value
      : {
          key: `${resources[index].resource_type}:${resources[index].resource_id}`,
          id: resources[index].resource_id,
          name: t('ops.task.emptyMark'),
          repoType: '',
          health: '',
          registeredAt: '',
        })
    const failedCount = results.filter((result) => result.status === 'rejected').length
    if (failedCount) repositoryResourceLoadError.value = t('ops.task.resourceLoadFailed', { count: failedCount })
  } finally {
    if (repositoryResourceController === controller) {
      repositoryResourceController = null
      repositoryResourceLoading.value = false
    }
  }
}

async function loadBackupSourceRows() {
  const task = activeTask.value
  if (!task || backupSourceLoading.value) return
  const resources = groupedResources.value.backup_source || []
  backupSourceLoading.value = true
  try {
    backupSourceRows.value = await Promise.all(resources.map(async (resource) => {
      const detail = await resolveTaskBackupSourceResource(resource, taskResourceSubtype(resource, task))
      return { id: resource.resource_id, ...detail }
    }))
  } catch {
    backupSourceRows.value = resources.map((resource) => ({
      id: resource.resource_id,
      backupSource: t('ops.task.emptyMark'),
      endpointName: t('ops.task.emptyMark'),
      endpointIp: t('ops.task.emptyMark'),
      status: '',
      statusValue: '',
      registeredAt: '',
      flowSource: {
        id: `unknown:${resource.resource_id}`,
        name: t('ops.task.emptyMark'),
        hostname: '',
        nodeName: '',
        nodeIp: '',
        status: 'offline',
        registeredAt: '',
        type: resource.resource_subtype === 'agent' ? 'host' : 'nas',
      },
    }))
  } finally {
    backupSourceLoading.value = false
  }
}

function onDetailTabChange(name: string | number) {
  if (name !== 'resources') return
  if (usesTargetRepositoryResources.value) {
    void loadTargetRepositoryResources()
  } else if (selectedResourceType.value === 'backup_source') {
    void loadBackupSourceRows()
  }
}

async function copyTaskUuid() {
  if (!activeTask.value?.task_uuid) return
  try {
    await copyTextToClipboard(activeTask.value.task_uuid)
    ElMessage.success(t('ops.task.msgCopied'))
  } catch {
    ElMessage.error(t('ops.task.msgCopyFailed'))
  }
}

async function loadTaskDetail(taskUuid: string) {
  const uuid = String(taskUuid || '').trim()
  if (!uuid) return
  stopRepositoryCancellation()
  detailRefreshing.value = true
  try {
    const task = await getTask(uuid)
    activeTask.value = task
    resetDrawerScroll()
    taskOwner.value = t('ops.task.emptyMark')
    detailEvents.value = task.recent_events || []
    selectedResourceType.value = task.resources?.[0]?.resource_type || ''
    backupSourceRows.value = []
    resetRepositoryResourceRows()
    initExpandedSteps(task)
    activeDetailTab.value = 'steps'
    void loadTaskOwner(task)
    const events = await listTaskEvents(uuid, { page: 1, page_size: 300 })
    detailEvents.value = events.results
    syncRepositoryCancellation(task)
  } catch (err) {
    ElMessage.error(apiErrorMessage(err))
  } finally {
    detailRefreshing.value = false
  }
}

async function cancelActiveTask() {
  if (!activeTask.value || !canCancel.value) return
  const task = activeTask.value
  if (task.task_type === 'backup') {
    const confirmed = await stopConfirmDialog.confirmStopBackup([buildStopConfirmItemFromTask(task)])
    if (!confirmed) return
  } else if (task.task_type === 'restore') {
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
      : task.task_type === 'restore'
        ? await (async () => {
            const result = await cancelProtectionRestoreTask(task.task_uuid)
            return getTask(result.task_uuid)
          })()
        : task.task_type === 'repository_operation'
          ? await cancelStorageRepositoryTask(task.task_uuid, t('ops.task.cancelReason'))
          : await cancelTask(task.task_uuid, t('ops.task.cancelReason'))
    activeTask.value = updated
    emit('task-updated', updated)
    const events = await listTaskEvents(updated.task_uuid, { page: 1, page_size: 300 })
    detailEvents.value = events.results
    initExpandedSteps(updated)
    resetRepositoryResourceRows()
    if (activeDetailTab.value === 'resources' && usesTargetRepositoryResources.value) {
      void loadTargetRepositoryResources()
    }
    if (syncRepositoryCancellation(updated)) {
      ElMessage.success(t('ops.task.repositoryCancelQueued'))
    }
  } catch (err) {
    ElMessage.error(apiErrorMessageI18n(err, t))
  } finally {
    actionBusy.value = false
  }
}

async function recheckActiveTask() {
  if (!activeTask.value || !canRecheck.value) return
  actionBusy.value = true
  try {
    const updated = await recheckTask(activeTask.value.task_uuid)
    activeTask.value = updated
    emit('task-updated', updated)
    const events = await listTaskEvents(updated.task_uuid, { page: 1, page_size: 300 })
    detailEvents.value = events.results
    initExpandedSteps(updated)
    ElMessage.success(t('ops.task.recheckComplete'))
  } catch (err) {
    ElMessage.error(apiErrorMessageI18n(err, t))
  } finally {
    actionBusy.value = false
  }
}

function refreshActiveTask() {
  if (!activeTask.value?.task_uuid) return
  void loadTaskDetail(activeTask.value.task_uuid)
}

function closeDetail() {
  stopRepositoryCancellation()
  activeTask.value = null
  taskOwner.value = ''
  detailEvents.value = []
  activeDetailTab.value = 'steps'
  selectedResourceType.value = ''
  backupSourceRows.value = []
  resetRepositoryResourceRows()
  initExpandedSteps(null)
}

onBeforeUnmount(() => {
  repositoryResourceController?.abort()
})

watch(
  () => [props.modelValue, props.taskUuid] as const,
  ([open, taskUuid]) => {
    if (open && taskUuid) void loadTaskDetail(taskUuid)
  },
  { immediate: true },
)
</script>

<template>
  <ElDrawer
    v-model="drawerOpen"
    class="hfl-task-drawer"
    :class="{ 'hfl-task-drawer--target-repositories': usesTargetRepositoryResources }"
    :size="drawerSize"
    @opened="resetDrawerScroll"
    @closed="closeDetail"
  >
    <template #header>
      <div class="hfl-task-drawer__header-bar">
        <div v-if="activeTask" class="hfl-task-drawer__header-summary">
          <div class="hfl-task-drawer__header-title-row">
            <h2 class="hfl-task-drawer__header-title">{{ taskDescription(activeTask) }}</h2>
            <TaskStatusTag :status="activeTask.status" />
          </div>
          <div class="hfl-task-drawer__header-meta">
            <span>{{ t('ops.task.ownerLabel') }}: <span :class="{ 'hfl-empty-mark': !taskOwner || taskOwner === t('ops.task.emptyMark') }">{{ taskOwner || t('ops.task.emptyMark') }}</span></span>
            <span class="hfl-task-drawer__header-divider" />
            <span class="hfl-task-drawer__uuid">
              {{ activeTask.task_uuid }}
              <ElButton class="hfl-task-drawer__copy-button" text :title="t('ops.task.copyUuid')" @click.stop="copyTaskUuid">
                <Copy :size="13" />
              </ElButton>
            </span>
          </div>
        </div>
        <h2 v-else class="hfl-task-drawer__header-title">{{ t('ops.task.detailTitle') }}</h2>
        <div class="hfl-task-drawer__header-actions">
          <ElButton
            v-if="canRecheck"
            :loading="actionBusy"
            :disabled="actionBusy || detailRefreshing"
            @click="recheckActiveTask"
          >
            <RefreshCw :size="15" />
            <span>{{ t('ops.task.btnRecheck') }}</span>
          </ElButton>
          <ElButton
            v-if="canCancel"
            class="hfl-task-drawer__cancel-button"
            :loading="actionBusy"
            :disabled="actionBusy || detailRefreshing || repositoryCancellationPending"
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
            :disabled="detailRefreshing || actionBusy"
            @click="refreshActiveTask"
          >
            <RefreshCw :size="20" :class="{ 'is-spinning': detailRefreshing }" />
          </button>
        </div>
      </div>
    </template>

    <div v-if="activeTask" ref="drawerScrollAnchorRef" v-loading="detailRefreshing" class="hfl-task-drawer__body">
      <section class="hfl-task-drawer__hero">
        <div class="hfl-task-drawer__hero-section-title">{{ t('ops.task.basicData') }}</div>
        <div class="hfl-task-drawer__hero-grid">
          <div class="hfl-task-drawer__metric">
            <Globe :size="18" class="hfl-task-drawer__metric-icon hfl-task-drawer__metric-icon--blue" />
            <div class="hfl-task-drawer__metric-copy">
              <span class="hfl-task-drawer__metric-label">{{ t('ops.task.typeAndTrigger') }}</span>
              <div class="hfl-task-drawer__metric-tags">
                <template v-if="usesTargetRepositoryResources">
                  <span class="hfl-task-drawer__type-pill" :class="`hfl-task-drawer__type-pill--${activeTask.task_type}`">
                    <span class="hfl-task-drawer__pill-text">{{ labelFor('taskType', activeTask.task_type) }}</span>
                  </span>
                  <span class="hfl-task-drawer__trigger-pill" :class="`hfl-task-drawer__trigger-pill--${activeTask.trigger_type}`">
                    <span class="hfl-task-drawer__pill-text">{{ labelFor('triggerType', activeTask.trigger_type) }}</span>
                  </span>
                </template>
                <template v-else>
                  <el-tag class="hfl-task-meta-tag" size="small" type="info">{{ labelFor('taskType', activeTask.task_type) }}</el-tag>
                  <el-tag class="hfl-task-meta-tag" size="small" type="info">{{ labelFor('triggerType', activeTask.trigger_type) }}</el-tag>
                </template>
              </div>
            </div>
          </div>
          <div class="hfl-task-drawer__metric">
            <RotateCcw :size="18" class="hfl-task-drawer__metric-icon hfl-task-drawer__metric-icon--indigo" />
            <div class="hfl-task-drawer__metric-copy">
              <span class="hfl-task-drawer__metric-label">{{ t('ops.task.retryCount') }}</span>
              <span class="hfl-task-drawer__metric-value">{{ t('ops.task.retryCountValue', { count: activeTask.retry_count }) }}</span>
            </div>
          </div>
          <div v-if="cleanupMetadata" class="hfl-task-drawer__metric hfl-task-drawer__metric--wide">
            <Link2 :size="18" class="hfl-task-drawer__metric-icon hfl-task-drawer__metric-icon--indigo" />
            <div class="hfl-task-drawer__metric-copy">
              <span class="hfl-task-drawer__metric-label">{{ t('ops.task.cleanupMode') }}</span>
              <div class="hfl-task-drawer__metric-tags">
                <ElTag :type="cleanupMetadata.force ? 'danger' : 'info'" size="small" effect="plain">
                  {{ cleanupMetadata.force ? t('ops.task.cleanupModeForce') : t('ops.task.cleanupModeNormal') }}
                </ElTag>
                <ElButton
                  v-if="cleanupMetadata.triggered_by_task_uuid"
                  link
                  type="primary"
                  size="small"
                  @click="openTriggeredCleanupTask"
                >
                  {{ t('ops.task.triggeredBySourceUnregister') }}
                </ElButton>
              </div>
            </div>
          </div>
          <div class="hfl-task-drawer__metric hfl-task-drawer__metric--wide">
            <Clock3 :size="18" class="hfl-task-drawer__metric-icon" />
            <div class="hfl-task-drawer__time-grid">
              <div>
                <span class="hfl-task-drawer__metric-label">{{ t('ops.task.startedAt') }}</span>
                <span class="hfl-task-drawer__time-value" :class="{ 'hfl-empty-mark': !(activeTask.started_at || activeTask.created_at) }">{{ formatTime(activeTask.started_at || activeTask.created_at) }}</span>
              </div>
              <div>
                <span class="hfl-task-drawer__metric-label">{{ t('ops.task.finishedAt') }}</span>
                <span class="hfl-task-drawer__time-value" :class="{ 'hfl-empty-mark': !activeTask.finished_at }">{{ formatTime(activeTask.finished_at) }}</span>
              </div>
              <div>
                <span class="hfl-task-drawer__metric-label">{{ t('ops.task.totalDuration') }}</span>
                <span class="hfl-task-drawer__time-value hfl-task-drawer__time-value--strong" :class="{ 'hfl-empty-mark': taskDuration(activeTask) === t('ops.task.emptyMark') }">{{ taskDuration(activeTask) }}</span>
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
              :class="`hfl-task-drawer__progress-fill--${activeTask.status}`"
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
          <li v-for="dependency in activeDependencies" :key="dependency.id">
            <span>{{ dependency.detail }}</span>
            <ElButton
              v-if="dependency.blocking_task_uuid"
              link
              type="primary"
              @click="openBlockingTask(dependency.blocking_task_uuid)"
            >
              {{ t('ops.task.viewBlockingTask') }}
            </ElButton>
          </li>
        </ul>
      </ElAlert>

      <ElTabs v-model="activeDetailTab" class="hfl-detail-tabs hfl-task-drawer__tabs" @tab-change="onDetailTabChange">
        <ElTabPane name="steps">
          <template #label>
            <span class="hfl-detail-tab-label">
              {{ t('ops.task.steps') }}
              <span class="hfl-detail-tab-count">{{ activeTask.steps?.length || 0 }}</span>
            </span>
          </template>
          <section class="hfl-task-drawer__tab-panel hfl-task-drawer__tab-panel--borderless">
        <div class="hfl-task-drawer__steps-head">
          <span>{{ t('ops.task.stepsHealthy') }}</span>
          <ElButton
            v-if="hasExpandableSteps"
            size="small"
            class="hfl-btn-with-icon"
            @click="toggleAllStepsExpanded"
          >
            {{ hasAnyExpandedStep ? t('ops.task.collapseAll') : t('ops.task.expandAll') }}
            <ChevronDown v-if="hasAnyExpandedStep" :size="16" class="hfl-task-step-chevron" />
            <ChevronRight v-else :size="16" class="hfl-task-step-chevron" />
          </ElButton>
        </div>
        <div v-if="stepsWithEvents.length" class="hfl-task-drawer__step-list">
          <div
            v-for="(step, index) in stepsWithEvents"
            :key="step.id"
            class="hfl-task-drawer__step-item"
            :class="{ 'hfl-task-drawer__step-item--last': index === stepsWithEvents.length - 1 && unlinkedEvents.length === 0 }"
          >
            <div class="hfl-task-drawer__step-anchor" :class="timelineIconClass(step.status)">
              <Check v-if="step.status === 'success'" :size="15" />
              <X v-else-if="step.status === 'failed' || step.status === 'timeout'" :size="15" />
              <LoaderCircle v-else-if="step.status === 'running'" :size="15" />
              <Circle v-else :size="9" />
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
                  {{ stepDisplayName(step.step_name) }}
                  <span class="hfl-task-drawer__step-executed-at" :class="{ 'hfl-empty-mark': !(step.created_at || activeTask.created_at) }">{{ formatTime(step.created_at || activeTask.created_at) }}</span>
                </span>
                <TaskStatusTag :status="step.status" />
                <span class="hfl-task-drawer__step-duration"><Clock3 :size="12" /> {{ stepDuration(index) }}</span>
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
                    <ChevronRight :size="16" aria-hidden="true" />
                  </span>
                </ElTooltip>
                <ChevronDown v-else-if="isStepExpanded(step.id)" :size="16" class="hfl-task-step-chevron" />
                <ChevronRight v-else :size="16" class="hfl-task-step-chevron" />
              </button>
              <div v-if="isStepExpanded(step.id) && step.events.length > 0" class="hfl-task-drawer__event-list">
                <div v-for="event in step.events" :key="event.id" class="hfl-task-drawer__event-row">
                  <span class="hfl-task-drawer__event-dot" :class="`hfl-task-drawer__event-dot--${eventTone(event)}`">
                    <X v-if="eventTone(event) === 'danger'" :size="9" />
                    <Circle v-else-if="eventTone(event) === 'muted'" :size="7" />
                    <Check v-else :size="9" />
                  </span>
                  <span class="hfl-task-drawer__event-content">
                    <span class="hfl-task-drawer__event-msg" :class="eventMessageClass(event)">{{ eventDisplayMessage(event) }}</span>
                    <span v-if="eventObjectText(event)" class="hfl-task-drawer__event-object"><Link2 :size="11" /><span>{{ eventObjectText(event) }}</span></span>
                    <span v-if="eventErrorText(event)" class="hfl-task-drawer__event-error">{{ eventErrorText(event) }}</span>
                  </span>
                  <span class="hfl-task-drawer__event-time" :class="{ 'hfl-empty-mark': !event.created_at }">{{ formatTime(event.created_at) }}</span>
                </div>
              </div>
            </article>
          </div>
          <div v-if="unlinkedEvents.length" class="hfl-task-drawer__event-only">
            <div v-for="event in unlinkedEvents" :key="event.id" class="hfl-task-drawer__event-row">
              <span class="hfl-task-drawer__event-dot" :class="`hfl-task-drawer__event-dot--${eventTone(event)}`">
                <X v-if="eventTone(event) === 'danger'" :size="9" />
                <Circle v-else-if="eventTone(event) === 'muted'" :size="7" />
                <Check v-else :size="9" />
              </span>
              <span class="hfl-task-drawer__event-content">
                <span class="hfl-task-drawer__event-msg" :class="eventMessageClass(event)">{{ eventDisplayMessage(event) }}</span>
                <span v-if="eventErrorText(event)" class="hfl-task-drawer__event-error">{{ eventErrorText(event) }}</span>
              </span>
              <span class="hfl-task-drawer__event-time">#{{ event.seq }} · <span :class="{ 'hfl-empty-mark': !event.created_at }">{{ formatTime(event.created_at) }}</span></span>
            </div>
          </div>
        </div>
        <el-empty v-else :description="t('ops.task.emptySteps')" :image-size="52" />
          </section>
        </ElTabPane>

        <ElTabPane name="resources">
          <template #label>
            <span class="hfl-detail-tab-label">
              {{ t('ops.task.resources') }}
              <span class="hfl-detail-tab-count">{{ usesTargetRepositoryResources ? targetRepositoryResources.length : activeTask.resources?.length || 0 }}</span>
            </span>
          </template>
          <section class="hfl-task-drawer__tab-panel hfl-task-drawer__tab-panel--borderless">
        <template v-if="usesTargetRepositoryResources">
          <div v-if="repositoryResourceLoadError" class="hfl-task-drawer__resource-error">
            {{ repositoryResourceLoadError }}
          </div>
          <el-table
            v-if="targetRepositoryResources.length"
            v-table-column-resize="'repositories.taskDetail.resources'"
            v-table-overflow-title
            v-loading="repositoryResourceLoading"
            :data="repositoryResourceRows"
            class="hfl-list-table hfl-list-table--compact hfl-task-drawer__resource-table"
          >
            <el-table-column :label="t('ops.task.resourceId')" width="120">
              <template #default="{ row }"><span class="hfl-table-cell-mono">{{ row.id }}</span></template>
            </el-table-column>
            <el-table-column :label="t('repositoriesPage.colListName')" min-width="180">
              <template #default="{ row }"><span>{{ row.name }}</span></template>
            </el-table-column>
            <el-table-column :label="t('ops.task.resourceTypeLabel')" width="150">
              <template #default="{ row }">
                <ElTag size="small" effect="plain" :class="[repositoryTypeTagClass(row.repoType), 'hfl-task-drawer__repository-type-tag']">
                  {{ repositoryTypeLabel(row.repoType) }}
                </ElTag>
              </template>
            </el-table-column>
            <el-table-column :label="t('repositoriesPage.colStatus')" width="116">
              <template #default="{ row }">
                <ElTag v-if="row.health" :type="row.health === 'online' ? 'success' : row.health === 'unverified' ? 'warning' : 'danger'" size="small">
                  {{ repositoryHealthLabel(row.health) }}
                </ElTag>
                <span v-else class="hfl-empty-mark">{{ t('ops.task.emptyMark') }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('repositoriesPage.colRegistered')" width="180">
              <template #default="{ row }"><span class="hfl-table-cell-time" :class="{ 'hfl-empty-mark': !row.registeredAt }">{{ formatTime(row.registeredAt) }}</span></template>
            </el-table-column>
          </el-table>
          <el-empty v-else :description="t('ops.task.emptyResources')" :image-size="52" />
        </template>
        <template v-else>
        <div v-if="resourceTypeTabs.length" class="hfl-task-drawer__resource-switcher">
          <button
            v-for="item in resourceTypeTabs"
            :key="item.type"
            type="button"
            class="hfl-task-drawer__resource-switch"
            :class="{ 'hfl-task-drawer__resource-switch--active': selectedResourceType === item.type }"
            @click="selectResourceType(item.type)"
          >
            <Link2 :size="14" />
            {{ item.label }}
            <span class="hfl-task-drawer__resource-switch-count">{{ item.count }}</span>
          </button>
        </div>
        <el-table
          v-if="resourceTypeTabs.length && selectedResourceType === 'backup_source'"
          v-table-column-resize="'protection.taskDetail.resources'"
          v-table-overflow-title
          v-loading="backupSourceLoading"
          :data="backupSourceRows"
          class="hfl-list-table hfl-list-table--compact hfl-task-drawer__resource-table"
        >
          <el-table-column :label="t('ops.task.resourceId')" width="120">
            <template #default="{ row }"><span class="hfl-table-cell-mono">{{ row.id }}</span></template>
          </el-table-column>
          <el-table-column :label="t('protection.backupsPage.colBackupSource')" min-width="180">
            <template #default="{ row }">
              <FlowSourceSummaryCell
                :row="row.flowSource"
                :interactive="false"
              />
            </template>
          </el-table-column>
          <el-table-column :label="t('protection.backupsPage.colConnectionAddress')" min-width="200">
            <template #default="{ row }">
              <FlowSourceConnectionCell :row="row.flowSource" />
            </template>
          </el-table-column>
          <el-table-column :label="t('ops.task.colStatus')" width="110">
            <template #default="{ row }">
              <ElTag v-if="row.status" v-bind="lifecycleStatusTagAttrs(row.statusValue)" size="small">{{ row.status }}</ElTag>
              <span v-else class="hfl-empty-mark">{{ t('ops.task.emptyMark') }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('protection.backupsPage.colRegistered')" width="180">
            <template #default="{ row }"><span class="hfl-table-cell-time" :class="{ 'hfl-empty-mark': !row.registeredAt }">{{ formatTime(row.registeredAt) }}</span></template>
          </el-table-column>
        </el-table>
        <el-table
          v-else
          v-table-column-resize="'protection.taskDetail.resources'"
          v-table-overflow-title
          :data="selectedResources"
          class="hfl-list-table hfl-list-table--compact hfl-task-drawer__resource-table"
        >
          <el-table-column :label="t('ops.task.resourceId')" width="120">
            <template #default="{ row }">
              <span class="hfl-table-cell-mono">{{ row.id }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('ops.task.resourceTypeLabel')" min-width="160">
            <template #default="{ row }">
              <ElTag type="info" size="small" effect="plain">
                {{ row.type }}
              </ElTag>
            </template>
          </el-table-column>
          <el-table-column :label="t('ops.task.resourceSubtype')" min-width="140" prop="subtype" />
        </el-table>
        <el-empty v-if="!resourceTypeTabs.length" :description="t('ops.task.emptyResources')" :image-size="52" />
        </template>
          </section>
        </ElTabPane>

      </ElTabs>
    </div>
    <div v-else ref="drawerScrollAnchorRef" class="hfl-task-drawer__body">
      <div class="hfl-task-drawer__loading" aria-busy="true">
        <el-skeleton :rows="6" animated />
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
</template>

<style scoped>
.hfl-task-drawer :deep(.el-drawer__header) {
  margin: 0;
  padding: 0 18px;
  border-bottom: 1px solid rgb(226 232 240);
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(8px);
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
  padding: 14px 0;
  width: 100%;
}

.hfl-task-drawer__header-summary {
  min-width: 0;
}

.hfl-task-drawer__header-title-row,
.hfl-task-drawer__header-meta,
.hfl-task-drawer__header-actions,
.hfl-task-drawer__metric,
.hfl-task-drawer__metric-tags,
.hfl-task-drawer__progress-head,
.hfl-task-drawer__steps-head,
.hfl-task-drawer__step-card-head,
.hfl-task-drawer__step-duration,
.hfl-task-drawer__event-object {
  display: flex;
  align-items: center;
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

.hfl-task-drawer__header-title {
  margin: 0;
  color: rgb(15 23 42);
  font-size: 18px;
  font-weight: 700;
}

.hfl-task-drawer__header-title-row {
  flex-wrap: wrap;
  gap: 10px;
}

.hfl-task-drawer__header-meta {
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
  min-width: 0;
  color: rgb(100 116 139);
  font-size: 12px;
}

.hfl-task-drawer__header-divider {
  align-self: center;
  width: 1px;
  height: 12px;
  background: rgb(203 213 225);
}

.hfl-task-drawer__uuid {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  font-weight: 700;
  color: rgb(71 85 105);
  overflow-wrap: anywhere;
}

.hfl-task-drawer__header-actions {
  gap: 8px;
  flex-shrink: 0;
}

.hfl-task-drawer__icon-button {
  width: 32px;
  height: 32px;
  padding: 0;
  color: rgb(107 114 128);
}

.hfl-task-drawer__icon-button.hfl-refresh-button {
  width: 34px;
  height: 34px;
}

.hfl-task-drawer__copy-button {
  width: 20px;
  height: 20px;
  padding: 0;
  color: rgb(100 116 139);
}

.hfl-task-drawer__body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
}

.hfl-task-drawer__loading {
  min-height: 320px;
  padding: 14px;
  border: 1px solid rgb(226 232 240);
  border-radius: 10px;
  background: #fff;
}

.hfl-task-drawer__hero,
.hfl-task-drawer__tab-panel {
  border: 1px solid rgb(226 232 240);
  border-radius: 10px;
  background: white;
}

.hfl-task-drawer__hero {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin: 0;
  padding: 14px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 12px;
  background: rgb(248 250 252);
  box-shadow: inset 0 1px 1px rgba(15, 23, 42, 0.03);
}

.hfl-task-drawer__hero-section-title {
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.06em;
  text-transform: uppercase;
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
  align-items: flex-start;
  gap: 10px;
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
  color: rgb(71 85 105);
}

.hfl-task-drawer__metric-icon--blue {
  color: rgb(37 99 235);
}

.hfl-task-drawer__metric-icon--indigo {
  color: rgb(79 70 229);
}

.hfl-task-drawer__metric-copy {
  min-width: 0;
}

.hfl-task-drawer__metric-label {
  display: block;
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 600;
}

.hfl-task-drawer__metric-value,
.hfl-task-drawer__time-value {
  display: block;
  margin-top: 4px;
  color: rgb(15 23 42);
  font-size: 14px;
  font-weight: 800;
}

.hfl-task-drawer__time-grid {
  display: grid;
  width: 100%;
  min-width: 0;
  grid-template-columns: 1fr;
  gap: 10px;
}

@media (min-width: 720px) {
  .hfl-task-drawer__time-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

.hfl-task-drawer__time-grid > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.hfl-task-drawer__progress-block {
  padding-top: 2px;
}

.hfl-task-drawer__progress-head {
  gap: 12px;
  justify-content: space-between;
  margin-bottom: 8px;
  color: rgb(71 85 105);
  font-size: 12px;
  font-weight: 700;
}

.hfl-task-drawer__progress-track {
  height: 6px;
  overflow: hidden;
  border-radius: 999px;
  background: rgb(226 232 240);
}

.hfl-task-drawer__progress-fill {
  height: 100%;
  min-width: 4px;
  border-radius: inherit;
  background: var(--color-info);
  transition: width 0.35s ease;
}

.hfl-task-drawer__progress-fill--success {
  background: rgb(22 163 74);
}

.hfl-task-drawer__progress-fill--failed,
.hfl-task-drawer__progress-fill--timeout {
  background: rgb(220 38 38);
}

.hfl-task-drawer__tabs {
  min-width: 0;
  margin-top: 4px;
}

.hfl-task-drawer__tab-panel {
  padding: 14px;
}

.hfl-task-drawer__tab-panel--borderless {
  border: 0;
}

.hfl-task-drawer__steps-head {
  justify-content: space-between;
  margin-bottom: 12px;
  color: rgb(100 116 139);
  font-size: 12px;
}

.hfl-task-drawer__step-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.hfl-task-drawer__step-item {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  gap: 8px;
}

.hfl-task-drawer__step-anchor,
.hfl-task-drawer__event-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
}

.hfl-task-drawer__step-anchor {
  width: 24px;
  height: 24px;
}

.hfl-task-drawer__timeline-icon--success {
  background: rgb(220 252 231);
  color: rgb(22 101 52);
}

.hfl-task-drawer__timeline-icon--danger {
  background: rgb(254 226 226);
  color: rgb(185 28 28);
}

.hfl-task-drawer__timeline-icon--running,
.hfl-task-drawer__timeline-icon--pending {
  background: rgb(219 234 254);
  color: rgb(37 99 235);
}

.hfl-task-drawer__timeline-icon--muted {
  background: rgb(241 245 249);
  color: rgb(100 116 139);
}

.hfl-task-drawer__step-card {
  min-width: 0;
  border: 1px solid rgb(226 232 240);
  border-radius: 8px;
  background: white;
}

.hfl-task-drawer__step-card-head {
  width: 100%;
  justify-content: space-between;
  gap: 10px;
  border: 0;
  background: transparent;
  padding: 10px 12px;
  text-align: left;
  cursor: pointer;
}

.hfl-task-drawer__step-title {
  min-width: 0;
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 700;
}

.hfl-task-drawer__step-executed-at {
  display: block;
  margin-top: 3px;
  color: rgb(100 116 139);
  font-size: 11px;
  font-weight: 500;
}

.hfl-task-drawer__step-duration {
  gap: 4px;
  color: rgb(100 116 139);
  font-size: 12px;
}

.hfl-task-drawer__event-list,
.hfl-task-drawer__event-only {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-top: 1px solid rgb(226 232 240);
  padding: 10px 12px;
}

.hfl-task-drawer__event-row {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto;
  gap: 8px;
  align-items: start;
}

.hfl-task-drawer__event-dot {
  width: 16px;
  height: 16px;
  background: rgb(241 245 249);
  color: rgb(100 116 139);
}

.hfl-task-drawer__event-dot--danger {
  background: rgb(254 226 226);
  color: rgb(185 28 28);
}

.hfl-task-drawer__event-dot--warning {
  background: rgb(254 243 199);
  color: rgb(180 83 9);
}

.hfl-task-drawer__event-dot--success {
  background: rgb(220 252 231);
  color: rgb(22 101 52);
}

.hfl-task-drawer__event-content {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.hfl-task-drawer__event-msg {
  color: rgb(51 65 85);
  font-size: 13px;
}

.hfl-task-drawer__event-error {
  color: rgb(185 28 28);
  font-size: 12px;
}

.hfl-task-drawer__event-object {
  gap: 4px;
  color: rgb(100 116 139);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
}

.hfl-task-drawer__event-time {
  color: rgb(148 163 184);
  font-size: 11px;
  white-space: nowrap;
}

.hfl-task-drawer__resource-switcher {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.hfl-task-drawer__resource-switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid rgb(226 232 240);
  border-radius: 8px;
  background: rgb(248 250 252);
  padding: 7px 9px;
}

.hfl-task-drawer__resource-switch--active {
  border-color: rgb(37 99 235);
  background: rgb(239 246 255);
  color: rgb(37 99 235);
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

:deep(.hfl-task-drawer__repository-type-tag.el-tag--plain) {
  font-weight: 400;
}

.hfl-task-drawer__resource-table {
  width: 100%;
}

@media (max-width: 760px) {
  .hfl-task-drawer__hero-grid {
    grid-template-columns: 1fr;
  }

  .hfl-task-drawer__time-grid {
    grid-template-columns: 1fr;
  }
}

/* Keep protection task Steps aligned with the operations task-detail drawer. */
.hfl-task-drawer__tab-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 0;
}

.hfl-task-drawer__steps-head {
  margin-bottom: 0;
  font-weight: 700;
}

.hfl-task-drawer__steps-actions {
  align-items: center;
  flex-shrink: 0;
}

.hfl-task-drawer__step-list {
  position: relative;
  gap: 18px;
}

.hfl-task-drawer__step-item {
  position: relative;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 12px;
}

.hfl-task-drawer__step-item::before {
  content: '';
  position: absolute;
  left: 12px;
  top: 34px;
  bottom: -18px;
  border-left: 2px dashed rgb(226 232 240);
}

.hfl-task-drawer__step-item--last::before {
  display: none;
}

.hfl-task-drawer__step-anchor {
  position: relative;
  z-index: 1;
  width: 26px;
  height: 26px;
  margin-top: 1px;
  border: 2px solid #fff;
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

.hfl-task-drawer__timeline-icon--running {
  border-color: var(--color-info);
  background-color: var(--color-info);
  color: #fff;
}

.hfl-task-drawer__timeline-icon--running > svg {
  animation: hfl-task-step-spin 0.8s linear infinite;
  transform-origin: center center;
  will-change: transform;
}

@keyframes hfl-task-step-spin {
  to {
    transform: rotate(360deg);
  }
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

.hfl-task-drawer__step-card {
  padding: 14px 16px;
  border-radius: 12px;
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
  padding: 0;
}

.hfl-task-drawer__step-title {
  flex: 1;
  font-size: 14px;
  font-weight: 800;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.hfl-task-drawer__step-executed-at {
  display: inline-flex;
  margin-top: 0;
  margin-left: 8px;
  color: rgb(71 85 105);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-weight: 700;
}

.hfl-task-drawer__step-duration {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  padding: 2px 7px;
  border: 1px solid rgb(241 245 249);
  border-radius: 6px;
  background: rgb(248 250 252);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 11px;
  font-weight: 700;
}

.hfl-task-drawer__event-list,
.hfl-task-drawer__event-only {
  gap: 10px;
  margin-top: 14px;
  padding-top: 12px;
  border-top-color: rgb(241 245 249);
}

.hfl-task-drawer__event-only {
  margin-top: 0;
  padding: 14px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 12px;
}

.hfl-task-drawer__event-row {
  grid-template-columns: 16px minmax(0, 1fr) auto;
  min-width: 0;
}

.hfl-task-drawer__event-dot {
  width: 14px;
  height: 14px;
  margin-top: 3px;
  background-color: var(--color-success);
  color: #fff;
}

.hfl-task-drawer__event-dot--danger { background-color: var(--color-error); }
.hfl-task-drawer__event-dot--warning { background-color: var(--color-warning); }
.hfl-task-drawer__event-dot--running { background-color: var(--color-info); }
.hfl-task-drawer__event-dot--muted { background-color: rgb(100 116 139); }

.hfl-task-drawer__event-content {
  align-items: flex-start;
}

.hfl-task-drawer__event-msg {
  min-width: 0;
  font-size: 12px;
  font-weight: 400;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.hfl-task-drawer__event-object {
  display: inline-flex;
  max-width: 100%;
  align-items: flex-start;
  border: 1px solid rgb(226 232 240);
  border-radius: 6px;
  background: rgb(248 250 252);
  padding: 2px 6px;
  color: rgb(71 85 105);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.hfl-task-drawer__event-error {
  display: block;
  max-width: 100%;
  line-height: 1.45;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.hfl-task-drawer__event-time {
  max-width: 180px;
  background: rgb(241 245 249);
  padding: 1px 6px;
  white-space: nowrap;
}

@media (max-width: 760px) {
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

/* Match the nested task detail used by Backup Wizard > Start Backup. */
.hfl-task-drawer--target-repositories :deep(.el-drawer__header) {
  border-bottom: 0;
  backdrop-filter: none;
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__header-bar {
  min-width: 0;
  padding: 0;
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__header-title {
  font-weight: 800;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__uuid {
  align-items: center;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__copy-button {
  align-self: center;
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__body {
  gap: 14px;
  padding: 0;
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__hero {
  padding: 16px;
  border-color: rgb(226 232 240);
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__metric {
  gap: 12px;
  border-color: rgb(241 245 249);
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__metric-icon {
  color: rgb(100 116 139);
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__metric-icon--blue {
  color: rgb(14 165 233);
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__metric-icon--indigo {
  color: rgb(79 70 229);
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__metric-label {
  font-weight: 650;
}

.hfl-task-drawer__metric-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

.hfl-task-drawer__type-pill,
.hfl-task-drawer__trigger-pill {
  display: inline-grid;
  place-items: center;
  box-sizing: border-box;
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
  vertical-align: middle;
  white-space: nowrap;
}

.hfl-task-drawer__pill-text {
  display: block;
  line-height: 12px;
  transform: translateY(1px);
}

.hfl-task-drawer__type-pill--backup {
  border-color: var(--color-info-border);
  background: var(--color-info-light);
  color: var(--color-info);
}

.hfl-task-drawer__type-pill--restore {
  border-color: rgb(196 181 253);
  background: rgb(245 243 255);
  color: rgb(109 40 217);
}

.hfl-task-drawer__trigger-pill {
  border-color: rgb(226 232 240);
  background: rgb(248 250 252);
  color: rgb(71 85 105);
}

.hfl-task-drawer__trigger-pill--manual {
  border-color: var(--color-info-border);
  background: var(--color-info-light);
  color: var(--color-info);
}

.hfl-task-drawer__trigger-pill--system {
  border-color: rgb(216 180 254);
  background: rgb(250 245 255);
  color: rgb(147 51 234);
}

.hfl-task-drawer__trigger-pill--retry {
  border-color: rgb(254 215 170);
  background: rgb(255 247 237);
  color: rgb(234 88 12);
}

.hfl-task-drawer__trigger-pill--api {
  border-color: rgb(153 246 228);
  background: rgb(240 253 250);
  color: rgb(13 148 136);
}

.hfl-task-drawer__trigger-pill--hook {
  border-color: rgb(251 207 232);
  background: rgb(253 242 248);
  color: rgb(219 39 119);
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__time-value {
  min-width: 0;
  color: rgb(51 65 85);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  font-weight: 650;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__time-value--strong,
.hfl-task-drawer--target-repositories .hfl-task-drawer__progress-head span:last-child {
  color: var(--color-info);
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__progress-head span:last-child {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__progress-fill--pending,
.hfl-task-drawer--target-repositories .hfl-task-drawer__progress-fill--cancelled {
  background: rgb(100 116 139);
}

.hfl-task-drawer--target-repositories .hfl-task-drawer__tab-panel {
  border: 0;
  border-radius: 0;
  background: transparent;
}
</style>
