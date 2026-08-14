<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ArrowLeft,
  Trash2,
  Folder,
  File,
  FolderInput,
  FolderTree,
  ClipboardCheck,
  ChevronsRight,
  Check,
  Undo2,
} from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import type { ElTable, ElTree } from 'element-plus'
import ModulePage from '../../components/ModulePage.vue'
import Modal from '../../components/Modal.vue'
import DangerConfirmDialog, { type DangerConfirmItem } from '../../components/DangerConfirmDialog.vue'
import TaskStatusTag from '../../components/TaskStatusTag.vue'
import TaskTypeLabel from '../../components/TaskTypeLabel.vue'
import SnapshotStatusTag from '../../components/SnapshotStatusTag.vue'
import { apiErrorMessage } from '../../lib/api'
import { useProtectionSideNav } from '../../composables/useProtectionSideNav'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import { useDrawerScrollReset } from '../../composables/useDrawerScrollReset'
import { formatLocalDateTime } from '../../lib/dateTime'
import {
  useProtectionDemoStore,
  type DemoFsNode,
  type DemoPathType,
  type DemoSnapshotDir,
} from '../../composables/useProtectionDemoStore'
import {
  deleteBackupSourceSnapshot,
  getBackupConfig,
  getBackupSourceSnapshot,
  listBackupSourceSnapshots,
  type BackupConfigDetail,
  type BackupSourceSnapshot,
  type CompressionLevel,
} from '../../lib/protectionBackupConfigApi'
import {
  listBackupPolicies,
  listFileFilterRules,
  type BackupPolicy,
  type FileFilterRule,
} from '../../lib/protectionPolicyApi'
import { listAllStorageRepositories, type StorageRepository } from '../../lib/storageRepositoryApi'
import { listBackupSelectableSources, type BackupSelectableSource } from '../../lib/sourceApi'
import { listTasks, type TaskRow } from '../../lib/taskApi'

const { t, te, locale } = useI18n()
const route = useRoute()
const store = useProtectionDemoStore()

const backupId = computed(() => String(route.params.backupId || ''))
const realBackupConfig = ref<BackupConfigDetail | null>(null)
const realSnapshots = ref<BackupSourceSnapshot[]>([])
const realTasks = ref<TaskRow[]>([])
const sourceInfo = ref<BackupSelectableSource | null>(null)
const repositoryById = ref(new Map<number, StorageRepository>())
const policyById = ref(new Map<number, BackupPolicy>())
const filterById = ref(new Map<number, FileFilterRule>())
const detailLoading = ref(false)
const detailLoadError = ref('')
const deleteSnapshotDialogOpen = ref(false)
const deleteSnapshotLoading = ref(false)
const pendingDeleteSnapshot = ref<DetailSnapshot | null>(null)

const protectionMenus = useProtectionSideNav()

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

type DrawerTreeRow = {
  label: string
  type: 'file' | 'dir'
  children?: DrawerTreeRow[]
}

type DetailSnapshotDir = DemoSnapshotDir

type DetailSnapshot = {
  apiId: number
  id: string
  startTime: string
  endTime: string
  sizeBytes: number
  fileCount: number
  dirCount: number
  status: string
  triggerType: string
  taskUuid: string
  dirs: DetailSnapshotDir[]
  treeByPath: Record<string, DemoFsNode[]>
}

const deleteSnapshotItems = computed<DangerConfirmItem[]>(() => {
  const row = pendingDeleteSnapshot.value
  if (!row) return []
  return [{
    key: row.id,
    name: row.id,
    status: { label: t('protection.backupsPage.snapshotStatusAvailable'), tone: 'success' },
    description: `${row.startTime} - ${row.endTime} / ${fmtBytes(row.sizeBytes)}`,
  }]
})

type DetailBackup = {
  id: string
  name: string
  remark: string
  status: string
  sources: Array<{ hostId: string; path: string; name: string; hostname: string }>
  targetId: string
  targetName: string
  targetLocation: string
  policyId: string | null
  policyName: string
  policySchedule: string
  policyRetention: string
  compression: CompressionLevel
  globalFilterId: string | null
  filterName: string
  filterSummary: string
  snapshots: DetailSnapshot[]
  latestSnapshotAt: string | null
}

function nodesToDrawerTree(nodes: DemoFsNode[]): DrawerTreeRow[] {
  return nodes.map((n) => ({
    label: n.name,
    type: n.type,
    children: n.children?.length ? nodesToDrawerTree(n.children) : undefined,
  }))
}

function fmtDateTime(raw?: string | null) {
  return formatLocalDateTime(raw, t('protection.backupDetail.durationDash'))
}

function compressionLevelLabel(level: CompressionLevel) {
  if (level === 'none') return t('protection.backupsPage.compressionNoneTitle')
  if (level === 'high') return t('protection.backupsPage.compressionHighTitle')
  return t('protection.backupsPage.compressionBalancedTitle')
}

function sourceLabel() {
  if (sourceInfo.value?.name) return sourceInfo.value.name
  if (realSnapshots.value[0]?.source_display_name) return realSnapshots.value[0].source_display_name
  const config = realBackupConfig.value
  return config ? `${config.source_type}:${config.source_ref_id}` : ''
}

function sourceHostname() {
  return sourceInfo.value?.hostname || sourceInfo.value?.node_ip || ''
}

function sourceNameByHostId(hostId: string) {
  if (backup.value?.sources[0]?.hostId === hostId) return backup.value.sources[0].name || hostId
  return store.getHost(hostId)?.name ?? sourceLabel() ?? hostId
}

function sourceHostnameByHostId(hostId: string) {
  if (backup.value?.sources[0]?.hostId === hostId) return backup.value.sources[0].hostname || ''
  return store.getHost(hostId)?.hostname ?? sourceHostname()
}

function normalizeDetailPathType(value?: string | null): DemoPathType {
  if (value === 'file' || value === 'directory') return value
  return 'unknown'
}

function snapshotDirKind(row: DetailSnapshotDir) {
  return row.pathType === 'file' ? 'file' : 'dir'
}

function snapshotDirIcon(row: DetailSnapshotDir) {
  return snapshotDirKind(row) === 'file' ? File : Folder
}

function canRestoreSnapshot(row: DetailSnapshot) {
  return row.status === 'available' || row.status === 'partial'
}

function canDeleteSnapshot(row: DetailSnapshot) {
  return row.status !== 'deleting' && row.status !== 'deleted'
}

function repositoryLabel(repo?: StorageRepository) {
  if (!repo) return ''
  const location = repo.repo_type === 's3'
    ? (repo.s3_bucket || '')
    : String(repo.config?.proxy_node_dir || repo.config?.path || repo.config?.root_path || '')
  return location ? `${repo.name} · ${location}` : repo.name
}

const backup = computed<DetailBackup | null>(() => {
  const config = realBackupConfig.value
  if (!config) return null
  const repo = repositoryById.value.get(config.repository_id)
  const policyRow = config.backup_policy_id ? policyById.value.get(config.backup_policy_id) : undefined
  const filterRow = config.file_filter_rule_id ? filterById.value.get(config.file_filter_rule_id) : undefined
  const latest = realSnapshots.value.find((item) => item.finished_at || item.started_at || item.created_at)
  const sourceName = sourceLabel()
  const hostname = sourceHostname()
  const snapshots = realSnapshots.value.map<DetailSnapshot>((snapshot) => ({
    apiId: snapshot.id,
    id: snapshot.snapshot_uid || String(snapshot.id),
    startTime: fmtDateTime(snapshot.started_at || snapshot.created_at),
    endTime: fmtDateTime(snapshot.finished_at),
    sizeBytes: Number(snapshot.total_size_bytes || 0),
    fileCount: Number(snapshot.file_count || 0),
    dirCount: Number(snapshot.dir_count || 0),
    status: snapshot.status,
    triggerType: snapshot.trigger_type,
    taskUuid: snapshot.task_uuid,
    dirs: (snapshot.directories || []).map((dir) => ({
      hostId: `${snapshot.source_type}:${snapshot.source_ref_id}`,
      hostName: sourceName,
      path: dir.source_path,
      pathType: normalizeDetailPathType(dir.path_type),
      sizeBytes: Number(dir.size_bytes || 0),
      fileCount: Number(dir.file_count || 0),
      innerDirCount: Number(dir.dir_count || 0),
    })),
    treeByPath: {},
  }))
  return {
    id: String(config.id),
    name: config.name,
    remark: config.remark,
    status: snapshots.some((item) => item.status === 'failed') ? 'failed' : 'success',
    sources: config.directories.map((dir) => ({
      hostId: `${config.source_type}:${config.source_ref_id}`,
      path: dir.path,
      name: sourceName,
      hostname,
    })),
    targetId: String(config.repository_id),
    targetName: repo?.name || `#${config.repository_id}`,
    targetLocation: repositoryLabel(repo),
    policyId: config.backup_policy_id ? String(config.backup_policy_id) : null,
    policyName: policyRow?.name || '',
    policySchedule: policyRow?.schedule_summary || '',
    policyRetention: policyRow?.retention_summary || '',
    compression: config.compression_level,
    globalFilterId: config.file_filter_rule_id ? String(config.file_filter_rule_id) : null,
    filterName: filterRow?.name || '',
    filterSummary: filterRow?.summary || '',
    snapshots,
    latestSnapshotAt: latest ? fmtDateTime(latest.finished_at || latest.started_at || latest.created_at) : null,
  }
})

const policy = computed(() => {
  const b = backup.value
  if (!b?.policyId) return undefined
  return {
    name: b.policyName || `#${b.policyId}`,
    backupFrequencyDesc: b.policySchedule || t('protection.backupDetail.durationDash'),
    schedule: b.policySchedule || t('protection.backupDetail.durationDash'),
    retentionDesc: b.policyRetention || t('protection.backupDetail.durationDash'),
  }
})

const globalFilter = computed(() => {
  const b = backup.value
  if (!b?.globalFilterId) return undefined
  return {
    name: b.filterName || `#${b.globalFilterId}`,
    summary: b.filterSummary || t('protection.backupDetail.durationDash'),
  }
})

const activeTab = ref('detail')
const snapshotTableRef = ref<InstanceType<typeof ElTable>>()
const snapshotDrawerOpen = ref(false)
const activeSnapshot = ref<DetailSnapshot | null>(null)
const taskTableRef = ref<InstanceType<typeof ElTable>>()
const taskDrawerOpen = ref(false)
const dirTreeDrawerOpen = ref(false)
const dirTreeDrawerTitle = ref('')
const dirTreeDrawerRows = ref<DrawerTreeRow[]>([])
const { drawerSize, updateDrawerWidth, bindDrawerResize, unbindDrawerResize } = useResponsiveDrawerWidth()
const { drawerSize: nestedDrawerSize } = useResponsiveDrawerWidth(2)
type DemoTaskStatus = 'success' | 'running' | 'failed' | 'pending' | 'cancelled' | 'timeout'
type DemoTask = {
  id: string
  type: string
  status: DemoTaskStatus
  createdAt: string
  duration: string
  trigger: string
  executor: string
  snapshotId: string | null
  detail: string
}
const activeTask = ref<DemoTask | null>(null)
const { drawerScrollAnchorRef, resetDrawerScroll } = useDrawerScrollReset()

function taskPayload(task: TaskRow): Record<string, unknown> {
  return task.request_payload && typeof task.request_payload === 'object'
    ? task.request_payload as Record<string, unknown>
    : {}
}

const taskList = computed<DemoTask[]>(() => {
  if (!backup.value) return []
  const snapshotByTaskUuid = new Map(backup.value.snapshots.map((snapshot) => [snapshot.taskUuid, snapshot]))
  return realTasks.value.map((task) => {
    const linkedSnapshot = snapshotByTaskUuid.get(task.task_uuid)
    const payload = taskPayload(task)
    const sourceName = sourceLabel()
    const status = String(task.status || 'pending') as DemoTaskStatus
    const createdAt = fmtDateTime(task.created_at)
    return {
      id: task.task_uuid || String(task.id),
      type: task.display_name || taskTypeLabel(task.task_type),
      status,
      createdAt,
      duration: taskDurationText(task.started_at || task.created_at || '', task.finished_at || ''),
      trigger: triggerLabel(task.trigger_type || String(payload.trigger_type || '')),
      executor: sourceName || `${payload.source_type || ''}:${payload.source_ref_id || ''}`,
      snapshotId: linkedSnapshot?.id ?? null,
      detail: task.error_message || stepLabel(task.current_step) || task.display_name || t('protection.backupDetail.durationDash'),
    }
  })
})

function triggerLabel(trigger: string) {
  if (trigger === 'manual') return t('protection.backupDetail.triggerManual')
  if (trigger === 'retry') return t('protection.backupDetail.taskTypeRetry')
  if (trigger === 'schedule' || trigger === 'system') return t('protection.backupDetail.triggerPolicy')
  return trigger ? t('ops.task.unknownValue') : t('protection.backupDetail.durationDash')
}

function taskTypeLabel(taskType?: string | null) {
  const value = String(taskType || '').trim()
  if (!value) return t('protection.backupDetail.durationDash')
  const key = `ops.task.taskType.${value}`
  return te(key) ? t(key) : t('ops.task.unknownValue')
}

function stepLabel(step?: string | null) {
  const value = String(step || '').trim()
  if (!value) return ''
  const key = `ops.task.step.${value}`
  return te(key) ? t(key) : t('ops.task.unknownValue')
}

function taskDurationText(startTime: string, endTime: string) {
  const s = new Date(startTime).getTime()
  const e = new Date(endTime).getTime()
  if (!Number.isFinite(s) || !Number.isFinite(e) || e <= s) return t('protection.backupDetail.durationDash')
  const sec = Math.floor((e - s) / 1000)
  const min = Math.floor(sec / 60)
  const remSec = sec % 60
  if (min <= 0) return t('protection.backupDetail.durationSec', { n: remSec })
  return t('protection.backupDetail.durationMinSec', { m: min, s: remSec })
}

function snapshotRowClassName({ row }: { row: DetailSnapshot }) {
  return activeSnapshot.value?.id === row.id ? 'snapshot-row--active' : ''
}

function openSnapshotDrawer(s: DetailSnapshot) {
  activeSnapshot.value = s
  snapshotDrawerOpen.value = true
  nextTick(() => {
    requestAnimationFrame(() => updateDrawerWidth())
  })
}

function onSnapshotDrawerOpened() {
  bindDrawerResize()
}

function onSnapshotDrawerClosed() {
  unbindDrawerResize()
  activeSnapshot.value = null
  dirTreeDrawerOpen.value = false
}

function openTaskDrawer(row: DemoTask) {
  activeTask.value = row
  taskDrawerOpen.value = true
  nextTick(() => {
    requestAnimationFrame(() => updateDrawerWidth())
  })
}

function onTaskDrawerOpened() {
  bindDrawerResize()
  resetDrawerScroll()
}

function onTaskDrawerClosed() {
  unbindDrawerResize()
  activeTask.value = null
}

function openDirTree(row: DemoSnapshotDir) {
  const snap = activeSnapshot.value
  if (!snap) return
  const tree = snap.treeByPath[row.path] ?? []
  dirTreeDrawerTitle.value = t('protection.backupDetail.drawerDirTitle', { path: row.path })
  dirTreeDrawerRows.value = nodesToDrawerTree(tree)
  dirTreeDrawerOpen.value = true
}

onBeforeUnmount(() => {
  unbindDrawerResize()
})

async function loadBackupDetail() {
  const numericId = Number(backupId.value)
  if (!Number.isFinite(numericId) || numericId <= 0) {
    realBackupConfig.value = null
    realSnapshots.value = []
    realTasks.value = []
    detailLoadError.value = t('protection.backupDetail.notFound')
    return
  }
  detailLoading.value = true
  detailLoadError.value = ''
  try {
    const config = await getBackupConfig(numericId)
    realBackupConfig.value = config
    const sourceId = `${config.source_type}:${config.source_ref_id}`
    const [snapshotsPage, repositories, policies, filters, selectable, tasksPage] = await Promise.all([
      listBackupSourceSnapshots({ backup_config_id: config.id, page: 1, page_size: 200, ordering: '-created_at' }),
      listAllStorageRepositories({ page_size: 10 }).catch(() => [] as StorageRepository[]),
      listBackupPolicies({ page: 1, page_size: 500 }).catch(() => ({ count: 0, results: [] as BackupPolicy[] })),
      listFileFilterRules({ page: 1, page_size: 500 }).catch(() => ({ count: 0, results: [] as FileFilterRule[] })),
      listBackupSelectableSources({ ids: sourceId, page: 1, page_size: 1 }).catch(() => ({ count: 0, results: [] as BackupSelectableSource[] })),
      listTasks({ task_type: 'backup', page: 1, page_size: 200 }).catch(() => ({ count: 0, results: [] as TaskRow[] })),
    ])
    repositoryById.value = new Map(repositories.map((repo) => [repo.id, repo]))
    policyById.value = new Map(policies.results.map((item) => [item.id, item]))
    filterById.value = new Map(filters.results.map((item) => [item.id, item]))
    sourceInfo.value = selectable.results[0] ?? null
    const snapshotDetails = await Promise.all(
      snapshotsPage.results.map((snapshot) =>
        getBackupSourceSnapshot(snapshot.id).catch(() => snapshot),
      ),
    )
    realSnapshots.value = snapshotDetails
    realTasks.value = tasksPage.results.filter((task) => {
      const payload = taskPayload(task)
      return Number(payload.backup_config_id || 0) === config.id
    })
  } catch (err) {
    realBackupConfig.value = null
    realSnapshots.value = []
    realTasks.value = []
    detailLoadError.value = apiErrorMessage(err, t('errors.generic.loadFailed'))
  } finally {
    detailLoading.value = false
  }
}

onMounted(() => {
  void loadBackupDetail()
})

watch(backupId, () => {
  void loadBackupDetail()
})

type RecoveryDirNode = {
  id: string
  label: string
  hostId: string
  path: string
  isCustom?: boolean
  children?: RecoveryDirNode[]
}

type RecoveryResultTreeNode = {
  id: string
  label: string
  children?: RecoveryResultTreeNode[]
}

const recOpen = ref(false)
const recStep = ref(0)
const recSnapshotForRecover = ref<DetailSnapshot | null>(null)
const recDirKeys = ref<string[]>([])
const recSelectTreeRef = ref<InstanceType<typeof ElTree>>()
const recDestHostId = ref('')
const recDestDirKey = ref('')
const recHostDirNameMap = ref<Record<string, string>>({})
const recDestTreeRef = ref<InstanceType<typeof ElTree>>()
const recDestCurrentNodeKey = ref('')
const recCustomDestSubDir = ref('')
const recDestExpandedKeys = ref<string[]>([])

const recWizardSteps = computed(() => [
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

function dirKey(d: DemoSnapshotDir) {
  return `${d.hostId}::${d.path}`
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

function fmtLocalTime(raw: string) {
  return formatLocalDateTime(raw, t('protection.backupDetail.durationDash'))
}

const treeSortLocale = () => String(locale.value || 'en')

function buildSubDirNodes(
  hostId: string,
  parentPath: string,
  nodes: DetailSnapshot['treeByPath'][string] | undefined,
): RecoveryDirNode[] {
  if (!nodes?.length) return []
  return nodes
    .filter((n) => n.type === 'dir')
    .map((n) => {
      const path = joinPath(parentPath, n.name)
      return {
        id: `${hostId}::${path}`,
        label: n.name,
        hostId,
        path,
        children: buildSubDirNodes(hostId, path, n.children),
      }
    })
}

function isSameOrAncestorPath(ancestorPath: string, childPath: string) {
  if (ancestorPath === childPath) return true
  const prefix = ancestorPath.endsWith('/') ? ancestorPath : `${ancestorPath}/`
  return childPath.startsWith(prefix)
}

const recDirTreeData = computed<RecoveryDirNode[]>(() => {
  const snap = recSnapshotForRecover.value
  if (!snap) return []
  return snap.dirs.map((d) => ({
    id: dirKey(d),
    label: `${d.hostName} · ${d.path}`,
    hostId: d.hostId,
    path: d.path,
    children: buildSubDirNodes(d.hostId, d.path, snap.treeByPath[d.path]),
  }))
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

const recSelectedDirNodes = computed(
  () => recDirKeys.value.map((k) => recDirNodeMap.value.get(k)).filter(Boolean) as RecoveryDirNode[],
)

const recDestinationHostOptions = computed(() => {
  const b = backup.value
  if (!b) return []
  const set = new Set<string>()
  return b.sources
    .filter((s) => {
      if (set.has(s.hostId)) return false
      set.add(s.hostId)
      return true
    })
    .map((s) => ({
      hostId: s.hostId,
      hostName: sourceNameByHostId(s.hostId),
      hostname: sourceHostnameByHostId(s.hostId),
    }))
})

function ensureHostFolderNames() {
  const next = { ...recHostDirNameMap.value }
  const hostIds = new Set(recSelectedDirNodes.value.map((d) => d.hostId))
  hostIds.forEach((hostId) => {
    if (!next[hostId]) {
      next[hostId] = sourceNameByHostId(hostId)
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

watch(recDestinationHostOptions, (list) => {
  if (!list.length) {
    recDestHostId.value = ''
    return
  }
  if (!list.some((x) => x.hostId === recDestHostId.value)) {
    recDestHostId.value = list[0].hostId
  }
})

watch(recSelectedDirNodes, (list) => {
  if (!list.length) {
    recDestDirKey.value = ''
    return
  }
  if (!list.some((x) => x.id === recDestDirKey.value)) {
    recDestDirKey.value = list[0].id
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
  const snap = recSnapshotForRecover.value
  const hostId = recDestHostId.value
  if (!snap || !hostId) {
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
    nodes: DetailSnapshot['treeByPath'][string] | undefined,
  ) => {
    if (!nodes?.length) return
    for (const n of nodes) {
      if (n.type !== 'dir') continue
      const childPath = joinPathBySep(parentPath, n.name, sep)
      const childNode = ensureNode(nodeHostId, childPath, n.name, parent)
      appendSubDirs(childNode, nodeHostId, childPath, sep, n.children)
    }
  }
  const hostDirs = snap.dirs.filter((d) => d.hostId === hostId)
  for (const d of hostDirs) {
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
  if (!finalMap.has(recDestDirKey.value)) {
    recDestDirKey.value = recDestTreeData.value[0]?.id ?? ''
  }
  if (!finalMap.has(recDestCurrentNodeKey.value)) {
    recDestCurrentNodeKey.value = ''
  }
}

watch(recDestHostId, () => {
  rebuildRecDestTree()
})

function buildCustomNodeId(node: RecoveryDirNode) {
  const custom = recCustomDestSubDir.value.trim()
  const sep: '/' | '\\' = isWindowsPath(node.path) ? '\\' : '/'
  const customPath = joinPathBySep(node.path, custom, sep)
  return `custom::${node.hostId}::${customPath}`
}

const recDestTreeDisplayData = computed<RecoveryDirNode[]>(() => {
  const suffix = t('protection.backupsPage.customTreeSuffix')
  const cloned = recDestTreeData.value.map(cloneRecoveryDirNode)
  const custom = recCustomDestSubDir.value.trim()
  if (!custom) return cloned
  const key = recDestCurrentNodeKey.value || recDestDirKey.value
  if (!key) return cloned
  const walk = (nodes: RecoveryDirNode[]): RecoveryDirNode | null => {
    for (const n of nodes) {
      if (n.id === key) return n
      if (n.children?.length) {
        const hit = walk(n.children)
        if (hit) return hit
      }
    }
    return null
  }
  const parent = walk(cloned)
  if (!parent) return cloned
  const sep: '/' | '\\' = isWindowsPath(parent.path) ? '\\' : '/'
  const customPath = joinPathBySep(parent.path, custom, sep)
  const customId = buildCustomNodeId(parent)
  parent.children = parent.children || []
  parent.children = parent.children.filter((x) => !x.id.startsWith('custom::'))
  parent.children.unshift({
    id: customId,
    label: `${custom}${suffix}`,
    hostId: parent.hostId,
    path: customPath,
    isCustom: true,
    children: [],
  })
  return cloned
})

function findRecoveryPathKeys(nodes: RecoveryDirNode[], targetId: string, acc: string[] = []): string[] | null {
  for (const n of nodes) {
    const next = [...acc, n.id]
    if (n.id === targetId) return next
    if (n.children?.length) {
      const hit = findRecoveryPathKeys(n.children, targetId, next)
      if (hit) return hit
    }
  }
  return null
}

function findRecoveryNodeById(nodes: RecoveryDirNode[], id: string): RecoveryDirNode | null {
  for (const n of nodes) {
    if (n.id === id) return n
    if (n.children?.length) {
      const hit = findRecoveryNodeById(n.children, id)
      if (hit) return hit
    }
  }
  return null
}

const recDestSelectedPath = computed(() => {
  const fromDisplay = findRecoveryNodeById(recDestTreeDisplayData.value, recDestDirKey.value)
  if (fromDisplay?.path) return fromDisplay.path
  return recDirNodeMap.value.get(recDestDirKey.value)?.path || ''
})

function onRecDirTreeCheckChange(data: RecoveryDirNode, checked: boolean) {
  const next = new Set(recDirKeys.value)
  if (!checked) {
    next.delete(data.id)
    recDirKeys.value = [...next]
    recSelectTreeRef.value?.setCheckedKeys(recDirKeys.value)
    return
  }
  for (const k of [...next]) {
    const node = recDirNodeMap.value.get(k)
    if (!node) continue
    const sameHost = node.hostId === data.hostId
    if (!sameHost) continue
    const overlap = isSameOrAncestorPath(node.path, data.path) || isSameOrAncestorPath(data.path, node.path)
    if (overlap) next.delete(k)
  }
  next.add(data.id)
  recDirKeys.value = [...next]
  recSelectTreeRef.value?.setCheckedKeys(recDirKeys.value)
}

function onRecDestTreeCurrentChange(data: RecoveryDirNode | null) {
  if (!data?.id) return
  if (data.id.startsWith('custom::')) {
    recDestDirKey.value = data.id
    const pathKeys = findRecoveryPathKeys(recDestTreeDisplayData.value, data.id) ?? []
    recDestExpandedKeys.value = [...new Set([...recDestExpandedKeys.value, ...pathKeys.slice(0, -1)])]
    return
  }
  recDestCurrentNodeKey.value = data.id
  const custom = recCustomDestSubDir.value.trim()
  recDestDirKey.value = custom ? buildCustomNodeId(data) : data.id
  recDestExpandedKeys.value = [...new Set([...recDestExpandedKeys.value, data.id])]
}

const recResultTreeData = computed<RecoveryResultTreeNode[]>(() => {
  const baseDestPath = recDestSelectedPath.value
  if (!baseDestPath) return []
  const customSubDir = recCustomDestSubDir.value.trim()
  const selectedIsCustomNode = recDestDirKey.value.startsWith('custom::')
  const parentLabel =
    customSubDir && !selectedIsCustomNode
      ? isWindowsPath(baseDestPath)
        ? joinPathBySep(baseDestPath, customSubDir, '\\')
        : joinPath(baseDestPath, customSubDir)
      : baseDestPath
  const hostMap = new Map<string, RecoveryResultTreeNode>()
  const directChildren: RecoveryResultTreeNode[] = []
  for (const d of recSelectedDirNodes.value) {
    const hostFolder = (recHostDirNameMap.value[d.hostId] ?? (sourceNameByHostId(d.hostId) || d.hostId)).trim()
    if (!hostFolder) {
      directChildren.push({
        id: `item-direct::${d.id}`,
        label: basenamePath(d.path),
      })
      continue
    }
    const hostNodeId = `host::${d.hostId}::${hostFolder}`
    if (!hostMap.has(hostNodeId)) hostMap.set(hostNodeId, { id: hostNodeId, label: hostFolder, children: [] })
    const hostNode = hostMap.get(hostNodeId)!
    hostNode.children!.push({
      id: `item::${d.id}`,
      label: basenamePath(d.path),
    })
  }
  const hostChildren = [...hostMap.values()]
  hostChildren.forEach((n) => n.children?.sort((a, b) => a.label.localeCompare(b.label, treeSortLocale())))
  hostChildren.sort((a, b) => a.label.localeCompare(b.label, treeSortLocale()))
  directChildren.sort((a, b) => a.label.localeCompare(b.label, treeSortLocale()))
  return [
    {
      id: `parent::${parentLabel}`,
      label: parentLabel,
      children: [...directChildren, ...hostChildren],
    },
  ]
})

watch(recCustomDestSubDir, () => {
  const custom = recCustomDestSubDir.value.trim()
  const base = findRecoveryNodeById(recDestTreeData.value, recDestCurrentNodeKey.value || recDestDirKey.value)
  if (!base || base.id.startsWith('custom::')) return
  recDestDirKey.value = custom ? buildCustomNodeId(base) : base.id
  recDestExpandedKeys.value = [...new Set([...recDestExpandedKeys.value, base.id])]
})

function openRecoveryWizard(snap: DetailSnapshot) {
  if (!canRestoreSnapshot(snap)) return
  recSnapshotForRecover.value = snap
  recStep.value = 0
  recDirKeys.value = []
  recHostDirNameMap.value = {}
  recCustomDestSubDir.value = ''
  recDestDirKey.value = ''
  recDestCurrentNodeKey.value = ''
  recDestExpandedKeys.value = []
  const firstHost = backup.value?.sources[0]?.hostId ?? ''
  recDestHostId.value = firstHost
  recOpen.value = true
  nextTick(() => {
    rebuildRecDestTree()
  })
}

function closeRecoveryWizard() {
  recOpen.value = false
  recSnapshotForRecover.value = null
}

function nextRec() {
  if (recStep.value === 0) {
    if (!recDestHostId.value) {
      ElMessage.warning({ message: t('protection.backupsPage.msgPickRecoveryNode'), grouping: true })
      return
    }
    if (recDestTreeData.value.length > 0 && !recDestDirKey.value) {
      ElMessage.warning({ message: t('protection.backupsPage.msgPickRecoveryDir'), grouping: true })
      return
    }
  }
  if (recStep.value === 1) {
    if (!recDirTreeData.value.length) {
      ElMessage.warning({ message: t('protection.backupsPage.msgSnapshotNoDirsDemo'), grouping: true })
      return
    }
    if (!recDirKeys.value.length) {
      ElMessage.warning({ message: t('protection.backupsPage.msgPickAtLeastOneDir'), grouping: true })
      return
    }
  }
  if (recStep.value < 2) recStep.value += 1
}

function prevRec() {
  if (recStep.value > 0) recStep.value -= 1
}

function runRecovery() {
  ElMessage.success({ message: t('protection.backupsPage.msgRecoverySubmitted'), grouping: true })
  closeRecoveryWizard()
}

function onDeleteSnapshot(row: DetailSnapshot) {
  pendingDeleteSnapshot.value = row
  deleteSnapshotDialogOpen.value = true
}

async function confirmDeleteSnapshot() {
  const row = pendingDeleteSnapshot.value
  if (!row || deleteSnapshotLoading.value) return
  deleteSnapshotLoading.value = true
  try {
    await deleteBackupSourceSnapshot(row.apiId)
    await loadBackupDetail()
    deleteSnapshotDialogOpen.value = false
    pendingDeleteSnapshot.value = null
    ElMessage.success({ message: t('protection.backupDetail.msgSnapshotDeleted'), grouping: true })
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('protection.policiesPage.msgDeleteFailed')), grouping: true })
  } finally {
    deleteSnapshotLoading.value = false
  }
}

function closeDeleteSnapshotDialog() {
  if (deleteSnapshotLoading.value) return
  deleteSnapshotDialogOpen.value = false
}
</script>

<template>
  <ModulePage
    :title="t('protection.moduleTitle')"
    :menus="protectionMenus"
  >
    <div
      v-if="detailLoading"
      class="py-16 text-center"
    >
      <el-empty
        :description="t('common.loading')"
        :image-size="64"
      />
    </div>

    <div
      v-else-if="backup"
      class="space-y-[var(--card-gap)]"
    >
      <div class="flex flex-wrap items-center gap-3">
        <RouterLink to="/protection/backups">
          <ElButton text>
            <ArrowLeft
              :size="16"
              class="inline mr-1 align-text-bottom"
            />
            {{ t('protection.backupDetail.backToList') }}
          </ElButton>
        </RouterLink>
      </div>

      <el-tabs
        v-model="activeTab"
        class="detail-tabs repo-underline-tabs"
      >
        <el-tab-pane
          :label="t('protection.backupDetail.tabDetail')"
          name="detail"
        >
          <el-descriptions
            :column="1"
            border
            size="default"
            class="max-w-3xl"
          >
            <el-descriptions-item :label="t('protection.backupDetail.labelName')">
              {{ backup.name }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('protection.backupDetail.labelRemark')">
              <span :class="{ 'hfl-empty-mark': !backup.remark }">{{ backup.remark || t('protection.backupDetail.durationDash') }}</span>
            </el-descriptions-item>
            <el-descriptions-item :label="t('protection.backupDetail.labelSnapshotCount')">
              {{ backup.snapshots.length }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('protection.backupDetail.labelLatestEnd')">
              <span :class="{ 'hfl-empty-mark': !backup.latestSnapshotAt }">{{ backup.latestSnapshotAt ?? t('protection.backupDetail.durationDash') }}</span>
            </el-descriptions-item>
            <el-descriptions-item :label="t('protection.backupDetail.labelSources')">
              <ul class="list-none m-0 p-0 space-y-2">
                <li
                  v-for="(s, i) in backup.sources"
                  :key="i"
                  class="border border-slate-100 rounded-md px-3 py-2 bg-[var(--color-grey-1,#f8fafc)]"
                >
                  <div class="text-sm font-medium text-slate-800">
                    {{ s.path }}
                  </div>
                  <div class="text-xs text-slate-500 mt-1">
                    {{
                      t('protection.backupDetail.hostLine', {
                        name: s.name || sourceNameByHostId(s.hostId),
                        hostname: s.hostname || sourceHostnameByHostId(s.hostId) || t('protection.backupDetail.durationDash'),
                      })
                    }}
                  </div>
                </li>
              </ul>
            </el-descriptions-item>
            <el-descriptions-item :label="t('protection.backupDetail.labelTarget')">
              {{ backup.targetName }}
              <span class="text-slate-400 text-sm ml-2">{{ backup.targetLocation }}</span>
            </el-descriptions-item>
            <el-descriptions-item :label="t('protection.backupDetail.labelPolicyName')">
              <template v-if="policy">
                {{ policy.name }}
              </template>
              <span
                v-else
                class="text-slate-400"
              >{{ t('protection.backupDetail.noPolicy') }}</span>
            </el-descriptions-item>
            <template v-if="policy">
              <el-descriptions-item :label="t('protection.backupDetail.labelFreq')">
                {{ policy.backupFrequencyDesc }}
              </el-descriptions-item>
              <el-descriptions-item :label="t('protection.backupDetail.labelCron')">
                <code class="rounded bg-slate-100 px-2 py-0.5 text-sm text-slate-800">{{ policy.schedule }}</code>
              </el-descriptions-item>
              <el-descriptions-item :label="t('protection.backupDetail.labelRetention')">
                {{ policy.retentionDesc }}
              </el-descriptions-item>
            </template>
            <el-descriptions-item :label="t('protection.backupDetail.labelCompression')">
              {{ compressionLevelLabel(backup.compression) }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('protection.backupDetail.labelGlobalFilterName')">
              <template v-if="globalFilter">
                {{ globalFilter.name }}
              </template>
              <span
                v-else
                class="text-slate-400"
              >{{ t('protection.backupDetail.noGlobalFilter') }}</span>
            </el-descriptions-item>
            <el-descriptions-item
              v-if="globalFilter"
              :label="t('protection.backupDetail.labelGlobalFilterSummary')"
            >
              {{ globalFilter.summary }}
            </el-descriptions-item>
          </el-descriptions>
        </el-tab-pane>

        <el-tab-pane
          :label="t('protection.backupDetail.tabSnapshots')"
          name="snapshots"
        >
          <div class="hfl-list-panel">
            <el-table
              ref="snapshotTableRef"
              v-table-overflow-title
              :data="backup.snapshots"
              stripe
              :row-class-name="snapshotRowClassName"
            >
              <el-table-column
                :label="t('protection.backupDetail.colSnapStart')"
                width="260"
              >
                <template #default="{ row }">
                  <button
                    type="button"
                    class="snapshot-start-link hfl-table-cell-time inline-flex items-center font-medium hover:underline text-[var(--el-color-primary)]"
                    @click="openSnapshotDrawer(row)"
                  >
                    {{ row.startTime }}
                  </button>
                </template>
              </el-table-column>
              <el-table-column
                :label="t('protection.backupDetail.colSnapEnd')"
                width="260"
                prop="endTime"
              >
                <template #default="{ row }">
                  <span
                    class="hfl-table-cell-time"
                    :class="{ 'hfl-empty-mark': !row.endTime }"
                  >{{ row.endTime || '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column
                :label="t('protection.backupDetail.colSnapSize')"
                width="120"
                header-cell-class-name="snapshot-th-split-start"
              >
                <template #default="{ row }">
                  {{ fmtBytes(row.sizeBytes) }}
                </template>
              </el-table-column>
              <el-table-column
                :label="t('protection.backupDetail.colSnapId')"
                width="300"
                prop="id"
              >
                <template #default="{ row }">
                  <span class="hfl-table-cell-mono">{{ row.id }}</span>
                </template>
              </el-table-column>
              <el-table-column
                :label="t('protection.backupDetail.colFileCount')"
                width="100"
                prop="fileCount"
              />
              <el-table-column
                :label="t('protection.backupDetail.colDirCount')"
                width="100"
                prop="dirCount"
              />
              <el-table-column
                :label="t('protection.backupDetail.labelStatus')"
                width="120"
              >
                <template #default="{ row }">
                  <SnapshotStatusTag :status="row.status" />
                </template>
              </el-table-column>
              <el-table-column
                :label="t('protection.backupDetail.colActions')"
                min-width="220"
                fixed="right"
                align="right"
              >
                <template #default="{ row }">
                  <ElButton
                    type="primary"
                    link
                    class="snapshot-action-btn"
                    :disabled="!canRestoreSnapshot(row)"
                    @click.stop="openRecoveryWizard(row)"
                  >
                    <Undo2
                      :size="16"
                      class="inline mr-1 align-text-bottom"
                    />
                    {{ t('protection.backupDetail.btnRecoverSnapshot') }}
                  </ElButton>
                  <ElButton
                    type="danger"
                    link
                    class="snapshot-action-btn"
                    :disabled="!canDeleteSnapshot(row)"
                    @click.stop="onDeleteSnapshot(row)"
                  >
                    <Trash2
                      :size="16"
                      class="inline mr-1 align-text-bottom"
                    />
                    {{ t('protection.backupDetail.btnDeleteSnapshot') }}
                  </ElButton>
                </template>
              </el-table-column>
              <template #empty>
                <el-empty
                  :description="t('protection.backupDetail.emptySnapshots')"
                  :image-size="64"
                />
              </template>
            </el-table>
          </div>
        </el-tab-pane>

        <el-tab-pane
          :label="t('protection.backupDetail.tabTasks')"
          name="tasks"
        >
          <div class="hfl-list-panel">
            <el-table
              ref="taskTableRef"
              v-table-overflow-title
              :data="taskList"
              stripe
            >
              <el-table-column
                :label="t('protection.backupDetail.colTaskId')"
                min-width="260"
              >
                <template #default="{ row }">
                  <button
                    type="button"
                    class="snapshot-start-link hfl-table-cell-mono inline-flex items-center font-medium hover:underline text-[var(--el-color-primary)]"
                    @click="openTaskDrawer(row)"
                  >
                    {{ row.id }}
                  </button>
                </template>
              </el-table-column>
              <el-table-column
                :label="t('protection.backupDetail.colTaskType')"
                width="150"
              >
                <template #default="{ row }">
                  <TaskTypeLabel :type="row.type" />
                </template>
              </el-table-column>
              <el-table-column
                :label="t('protection.backupDetail.colTaskStatus')"
                width="100"
              >
                <template #default="{ row }">
                  <TaskStatusTag :status="row.status" />
                </template>
              </el-table-column>
              <el-table-column
                :label="t('protection.backupDetail.colDuration')"
                width="110"
                prop="duration"
              />
              <el-table-column
                :label="t('protection.backupDetail.colTrigger')"
                width="110"
                prop="trigger"
              />
              <el-table-column
                :label="t('protection.backupDetail.colLinkedSnap')"
                min-width="140"
                prop="snapshotId"
                header-cell-class-name="task-th-split-start"
              >
                <template #default="{ row }">
                  <span class="hfl-table-cell-mono">{{ row.snapshotId }}</span>
                </template>
              </el-table-column>
              <el-table-column
                :label="t('protection.backupDetail.colCreated')"
                min-width="180"
                prop="createdAt"
              >
                <template #default="{ row }">
                  <span
                    class="hfl-table-cell-time"
                    :class="{ 'hfl-empty-mark': !row.createdAt }"
                  >{{ row.createdAt || '—' }}</span>
                </template>
              </el-table-column>
              <template #empty>
                <el-empty
                  :description="t('protection.backupDetail.emptyTasks')"
                  :image-size="64"
                />
              </template>
            </el-table>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <div
      v-else
      class="py-16 text-center"
    >
      <el-empty :description="detailLoadError || t('protection.backupDetail.notFound')">
        <RouterLink to="/protection/backups">
          <ElButton type="primary">
            {{ t('protection.backupDetail.backToDataProtection') }}
          </ElButton>
        </RouterLink>
      </el-empty>
    </div>
  </ModulePage>

  <ElDrawer
    v-model="snapshotDrawerOpen"
    direction="rtl"
    destroy-on-close
    :modal="true"
    :size="drawerSize"
    class="snapshot-detail-drawer"
    @opened="onSnapshotDrawerOpened"
    @closed="onSnapshotDrawerClosed"
  >
    <template #header>
      <span class="snapshot-drawer-title">{{ t('protection.backupDetail.drawerSnapTitle', { id: activeSnapshot?.id || '' }) }}</span>
    </template>
    <div
      v-if="activeSnapshot"
      class="snapshot-drawer-body"
    >
      <p class="text-sm text-slate-500 m-0 mb-3">
        {{
          t('protection.backupDetail.snapMetaLine', {
            start: activeSnapshot.startTime,
            end: activeSnapshot.endTime,
            size: fmtBytes(activeSnapshot.sizeBytes),
          })
        }}
      </p>
      <div class="hfl-list-panel">
        <div class="px-4 py-3 border-b border-slate-100 text-sm font-medium text-slate-800">
          {{ t('protection.backupDetail.panelDirList') }}
        </div>
        <el-table
          v-table-column-resize="'protection.backupDetail.snapshotDrawer.dirs'"
          v-table-overflow-title
          :data="activeSnapshot.dirs"
          stripe
          class="hfl-list-table"
        >
          <el-table-column
            :label="t('protection.backupDetail.colBackupDir')"
            min-width="300"
          >
            <template #default="{ row }">
              <button
                type="button"
                class="reset-btn snapshot-source-path-cell"
                @click="openDirTree(row)"
              >
                <span class="snapshot-source-path-cell__parent">
                  <component
                    :is="snapshotDirIcon(row)"
                    :size="15"
                    class="snapshot-source-path-cell__icon"
                    :class="`snapshot-source-path-cell__icon--${snapshotDirKind(row)}`"
                  />
                  <span class="snapshot-source-path-cell__path hfl-table-cell-mono">{{ row.path }}</span>
                </span>
                <span class="snapshot-source-path-cell__child">
                  <span
                    class="snapshot-source-path-cell__branch"
                    aria-hidden="true"
                  />
                  <span class="snapshot-source-path-cell__host">
                    {{
                      t('protection.backupDetail.hostSubline', {
                        name: row.hostName,
                        hostname: sourceHostnameByHostId(row.hostId) || t('protection.backupDetail.durationDash'),
                      })
                    }}
                  </span>
                </span>
              </button>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.backupDetail.colDirSize')"
            width="120"
          >
            <template #default="{ row }">
              {{ fmtBytes(row.sizeBytes) }}
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.backupDetail.colFileCountDirs')"
            width="110"
            prop="fileCount"
          />
          <el-table-column
            :label="t('protection.backupDetail.colInnerDirs')"
            width="120"
            prop="innerDirCount"
          />
          <template #empty>
            <el-empty
              :description="t('protection.backupDetail.emptyDirs')"
              :image-size="64"
            />
          </template>
        </el-table>
      </div>
    </div>
  </ElDrawer>

  <ElDrawer
    v-model="taskDrawerOpen"
    direction="rtl"
    destroy-on-close
    :modal="true"
    :size="drawerSize"
    class="task-detail-drawer"
    @opened="onTaskDrawerOpened"
    @closed="onTaskDrawerClosed"
  >
    <template #header>
      <span class="snapshot-drawer-title">{{ t('protection.backupDetail.drawerTaskTitle', { id: activeTask?.id || '' }) }}</span>
    </template>
    <div
      v-if="activeTask"
      ref="drawerScrollAnchorRef"
      class="snapshot-drawer-body"
    >
      <el-descriptions
        :column="1"
        border
      >
        <el-descriptions-item :label="t('protection.backupDetail.labelTaskId')">
          {{ activeTask.id }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('protection.backupDetail.labelTaskType')">
          {{ activeTask.type }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('protection.backupDetail.labelStatus')">
          <TaskStatusTag :status="activeTask.status" />
        </el-descriptions-item>
        <el-descriptions-item :label="t('protection.backupDetail.labelStart')">
          {{ activeTask.startTime }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('protection.backupDetail.labelEnd')">
          {{ activeTask.endTime }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('protection.backupDetail.labelDuration')">
          {{ activeTask.duration }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('protection.backupDetail.labelTrigger')">
          {{ activeTask.trigger }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('protection.backupDetail.labelExecutor')">
          {{ activeTask.executor }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('protection.backupDetail.labelLinkedSnap')">
          {{
            activeTask.snapshotId ?? t('protection.backupDetail.noLinkedSnap')
          }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('protection.backupDetail.labelDetail')">
          {{ activeTask.detail }}
        </el-descriptions-item>
      </el-descriptions>
    </div>
  </ElDrawer>

  <ElDrawer
    v-model="dirTreeDrawerOpen"
    destroy-on-close
    :title="dirTreeDrawerTitle"
    class="snapshot-dir-contents-drawer"
    direction="rtl"
    :size="nestedDrawerSize"
    append-to-body
  >
    <el-tree
      class="hfl-dir-tree hfl-dir-tree--tall"
      :data="dirTreeDrawerRows"
      default-expand-all
      :props="{ children: 'children', label: 'label' }"
    >
      <template #default="{ node, data }">
        <span class="hfl-dir-tree-node">
          <component
            :is="data.type === 'dir' ? Folder : File"
            :size="16"
            class="hfl-dir-tree-node__icon"
            :class="data.type === 'dir' ? '' : 'text-slate-500'"
          />
          <span class="hfl-dir-tree-node__text">
            <span
              class="hfl-dir-tree-node__label"
              :title="node.label"
            >{{ node.label }}</span>
            <span
              v-if="data.type === 'dir'"
              class="hfl-dir-tree-node__path"
            >{{ t('protection.backupDetail.typeDir') }}</span>
            <span
              v-else
              class="hfl-dir-tree-node__path"
            >{{ t('protection.backupDetail.typeFile') }}</span>
          </span>
        </span>
      </template>
    </el-tree>
    <p class="text-xs text-slate-400 mt-4">
      {{ t('protection.backupDetail.treeDemoHint') }}
    </p>
  </ElDrawer>

  <Modal
    :open="recOpen"
    :title="t('protection.backupsPage.modalCreateRestoreTitle')"
    size="xxlarge"
    fixed
    min-height="min(820px, calc(var(--app-viewport-height) - 3rem))"
    @close="closeRecoveryWizard"
  >
    <div
      v-if="recSnapshotForRecover && backup"
      class="dp-restore-wizard-body space-y-4"
    >
      <p class="text-sm text-slate-500 m-0">
        {{ t('protection.backupDetail.recoverFromSnapshotLead', { backup: backup.name, snapshot: fmtLocalTime(recSnapshotForRecover.endTime) }) }}
      </p>

      <nav
        class="dp-create-wizard"
        role="navigation"
        :aria-label="t('protection.backupsPage.createRestoreWizardAria')"
      >
        <ol class="dp-create-wizard__list m-0 list-none p-0">
          <template
            v-for="(step, idx) in recWizardSteps"
            :key="`bd-rec-step-${idx}`"
          >
            <li
              class="dp-create-wizard__step"
              :class="{
                'dp-create-wizard__step--active': recStep === idx,
                'dp-create-wizard__step--done': recStep > idx,
                'dp-create-wizard__step--pending': recStep < idx,
              }"
              :aria-current="recStep === idx ? 'step' : undefined"
            >
              <div class="dp-create-wizard__card">
                <div
                  class="dp-create-wizard__icon-wrap"
                  aria-hidden="true"
                >
                  <component
                    :is="step.icon"
                    :size="22"
                    stroke-width="2"
                  />
                </div>
                <div class="dp-create-wizard__content min-w-0 flex-1">
                  <div class="dp-create-wizard__head">
                    <span
                      class="dp-create-wizard__badge"
                      aria-hidden="true"
                    >
                      <Check
                        v-if="recStep > idx"
                        :size="13"
                        stroke-width="3"
                      />
                      <span v-else>{{ String(idx + 1).padStart(2, '0') }}</span>
                    </span>
                    <span class="dp-create-wizard__title">{{ step.title }}</span>
                  </div>
                  <p class="dp-create-wizard__hint">
                    {{ step.hint }}
                  </p>
                </div>
              </div>
            </li>
            <li
              v-if="idx < recWizardSteps.length - 1"
              :key="`bd-rec-conn-${idx}`"
              class="dp-create-wizard__connector"
              :class="{ 'dp-create-wizard__connector--on': recStep > idx }"
              aria-hidden="true"
            >
              <span class="dp-create-wizard__rail dp-create-wizard__rail--left" />
              <span class="dp-create-wizard__chev">
                <ChevronsRight
                  :size="16"
                  stroke-width="2.4"
                />
              </span>
              <span class="dp-create-wizard__rail dp-create-wizard__rail--right" />
            </li>
          </template>
        </ol>
      </nav>

      <div
        v-show="recStep === 0"
        class="dp-wizard-pane"
      >
        <p class="text-sm text-slate-500 mb-3">
          {{ t('protection.backupsPage.recStep3Lead') }}
        </p>
        <div class="flex flex-col gap-3 lg:flex-row lg:items-stretch min-h-[380px]">
          <div
            class="rounded-[var(--radius-card)] border border-[var(--color-border,#e2e8f0)] bg-[var(--color-grey-1,#f8fafc)]/70 p-3 lg:w-[min(100%,380px)] shrink-0"
          >
            <div class="text-sm font-semibold text-slate-800 mb-2">
              {{ t('protection.backupsPage.recDestNodeTitle') }}
            </div>
            <el-select
              v-model="recDestHostId"
              :placeholder="t('protection.backupsPage.phPickRecoveryNode')"
              class="w-full"
              filterable
            >
              <el-option
                v-for="h in recDestinationHostOptions"
                :key="h.hostId"
                :label="t('protection.backupsPage.recHostOptionLabel', { name: h.hostName, hostname: h.hostname })"
                :value="h.hostId"
              />
            </el-select>
          </div>
          <div class="rounded-[var(--radius-card)] border border-[var(--color-border,#e2e8f0)] bg-[var(--color-card-bg,#fff)] p-3 dp-wizard-scroll-card flex-1 min-w-0">
            <div class="text-sm font-semibold text-slate-800 mb-2">
              {{ t('protection.backupsPage.recDestDirTitle') }}
            </div>
            <div class="mb-2 flex items-center gap-2">
              <span class="shrink-0 text-sm text-slate-600">{{ t('protection.backupsPage.labelCustomSubdir') }}</span>
              <ElInput
                v-model="recCustomDestSubDir"
                :placeholder="t('protection.backupsPage.phCustomSubdir')"
              />
            </div>
            <el-tree
              ref="recDestTreeRef"
              class="rec-dest-tree hfl-dir-tree hfl-dir-tree--fill"
              :data="recDestTreeDisplayData"
              node-key="id"
              highlight-current
              :expanded-keys="recDestExpandedKeys"
              :expand-on-click-node="false"
              :props="{ label: 'label', children: 'children' }"
              :current-node-key="recDestDirKey"
              @current-change="onRecDestTreeCurrentChange"
            >
              <template #default="{ data }">
                <span class="rec-tree-node-label hfl-dir-tree-node">
                  <span
                    class="rec-custom-arrow"
                    :class="{ 'rec-custom-arrow--hidden': !data.isCustom }"
                  >▸</span>
                  <Folder
                    :size="15"
                    class="hfl-dir-tree-node__icon"
                  />
                  <span class="hfl-dir-tree-node__text">
                    <span class="hfl-dir-tree-node__label">{{ data.label }}</span>
                    <span class="hfl-dir-tree-node__path">{{ data.path }}</span>
                  </span>
                </span>
              </template>
            </el-tree>
          </div>
        </div>
      </div>

      <div
        v-show="recStep === 1"
        class="dp-wizard-pane"
      >
        <p class="text-sm text-slate-500 mb-3">
          {{ t('protection.backupsPage.recStep2Lead') }}
        </p>
        <div class="rounded-[var(--radius-card)] border border-[var(--color-border,#e2e8f0)] bg-[var(--color-card-bg,#fff)] p-3 dp-wizard-scroll-card">
          <div class="text-sm font-semibold text-slate-800 mb-2">
            {{ t('protection.backupsPage.recDirsTitle') }}
          </div>
          <p class="text-xs text-slate-500 m-0 mb-2">
            {{ t('protection.backupsPage.recDirsHint') }}
          </p>
          <el-tree
            ref="recSelectTreeRef"
            class="rec-select-tree hfl-dir-tree hfl-dir-tree--fill"
            :data="recDirTreeData"
            node-key="id"
            show-checkbox
            check-strictly
            default-expand-all
            :props="{ label: 'label', children: 'children' }"
            :default-checked-keys="recDirKeys"
            @check-change="onRecDirTreeCheckChange"
          >
            <template #default="{ data }">
              <span class="hfl-dir-tree-node">
                <Folder
                  :size="15"
                  class="hfl-dir-tree-node__icon"
                />
                <span class="hfl-dir-tree-node__text">
                  <span class="hfl-dir-tree-node__label">{{ data.label }}</span>
                  <span class="hfl-dir-tree-node__path">{{ data.path }}</span>
                </span>
              </span>
            </template>
          </el-tree>
        </div>
      </div>

      <div
        v-show="recStep === 2"
        class="dp-wizard-pane"
      >
        <p class="text-sm text-slate-600 mb-3">
          {{ t('protection.backupsPage.recStep4Lead') }}
        </p>
        <el-descriptions
          :column="1"
          border
          size="small"
          class="mt-2"
        >
          <el-descriptions-item :label="t('protection.backupsPage.descBackup')">
            {{ backup.name }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('protection.backupsPage.descSnapshot')">
            {{ fmtLocalTime(recSnapshotForRecover.endTime) }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('protection.backupsPage.descRecoveryDirs')">
            <ul class="list-disc pl-4 m-0">
              <li
                v-for="d in recSelectedDirNodes"
                :key="d.id"
              >
                {{ sourceNameByHostId(d.hostId) }} - {{ d.path }}
              </li>
            </ul>
          </el-descriptions-item>
          <el-descriptions-item :label="t('protection.backupsPage.descRecoveryDest')">
            {{
              t('protection.backupsPage.recoveryDestLine', {
                node: sourceNameByHostId(recDestHostId),
                path: recResultTreeData[0]?.label || '—',
              })
            }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('protection.backupsPage.descHostDirRename')">
            <div class="space-y-2">
              <div
                v-for="h in [...new Set(recSelectedDirNodes.map((d) => d.hostId))]"
                :key="h"
                class="flex items-center gap-2"
              >
                <span class="w-32 text-slate-500">{{ sourceNameByHostId(h) }}</span>
                <ElInput
                  v-model="recHostDirNameMap[h]"
                  :placeholder="t('protection.backupsPage.phHostDirName')"
                />
              </div>
            </div>
          </el-descriptions-item>
          <el-descriptions-item :label="t('protection.backupsPage.descRecoveryResult')">
            <el-tree
              class="hfl-dir-tree hfl-dir-tree--tall"
              :data="recResultTreeData"
              node-key="id"
              default-expand-all
              :expand-on-click-node="false"
              :props="{ label: 'label', children: 'children' }"
            />
          </el-descriptions-item>
        </el-descriptions>
      </div>
    </div>

    <template #footer>
      <div class="flex items-center justify-between">
        <ElButton
          v-if="recStep > 0"
          @click="prevRec"
        >
          {{ t('protection.backupsPage.btnPrev') }}
        </ElButton>
        <span v-else />
        <div class="flex gap-2">
          <ElButton @click="closeRecoveryWizard">
            {{ t('protection.backupsPage.btnCancel') }}
          </ElButton>
          <ElButton
            v-if="recStep < 2"
            type="primary"
            @click="nextRec"
          >
            {{ t('protection.backupsPage.btnNext') }}
          </ElButton>
          <ElButton
            v-else
            type="primary"
            @click="runRecovery"
          >
            {{ t('protection.backupsPage.btnConfirmRecovery') }}
          </ElButton>
        </div>
      </div>
    </template>
  </Modal>
  <DangerConfirmDialog
    v-model="deleteSnapshotDialogOpen"
    :title="t('protection.backupDetail.titleDeleteSnapshot')"
    :message="t('protection.backupDetail.msgDeleteSnapshotConfirm')"
    :items="deleteSnapshotItems"
    :items-heading="t('protection.backupDetail.labelLinkedSnap')"
    :item-name-label="t('protection.backupsPage.snapshotBrowserName')"
    :item-status-label="t('protection.backupsPage.colStatus')"
    :item-details-label="t('protection.backupDetail.labelStart')"
    confirm-mode="keyword"
    :confirm-keyword="t('common.deleteKeyword')"
    :confirm-keyword-hint="t('common.deleteKeywordHint')"
    :confirm-keyword-placeholder="t('common.deleteKeyword')"
    :cancel-text="t('common.cancel')"
    :confirm-text="t('protection.backupsPage.btnConfirmDelete')"
    :loading="deleteSnapshotLoading"
    level="high"
    width="560px"
    @confirm="confirmDeleteSnapshot"
    @cancel="closeDeleteSnapshotDialog"
  />
</template>

<style scoped>
.detail-tabs :deep(.el-tab-pane) {
  padding: 16px;
}

.repo-underline-tabs :deep(.el-tabs__header) {
  margin: 0;
  border-bottom: 1px solid var(--color-border-light, #ebeef5);
}
.repo-underline-tabs :deep(.el-tabs__nav-wrap::after) {
  height: 1px;
  background-color: var(--color-border-light, #ebeef5);
}
.repo-underline-tabs :deep(.el-tabs__nav) {
  padding-left: 8px;
}
.repo-underline-tabs :deep(.el-tabs__item) {
  height: 48px;
  line-height: 48px;
  padding: 0 22px;
  font-size: 14px;
  color: var(--color-text-primary, #303133);
  border: none;
}
.repo-underline-tabs :deep(.el-tabs__item:hover) {
  color: var(--el-color-primary);
}
.repo-underline-tabs :deep(.el-tabs__item.is-active) {
  color: var(--el-color-primary);
  font-weight: 600;
}
.repo-underline-tabs :deep(.el-tabs__active-bar) {
  height: 2px;
  border-radius: 1px;
  background-color: var(--el-color-primary);
}

.reset-btn,
.snapshot-start-link {
  border: none;
  padding: 0;
  margin: 0;
  background: none;
  font: inherit;
  cursor: pointer;
}

.snapshot-start-link {
  white-space: nowrap;
}

.detail-tabs :deep(.el-table .cell) {
  white-space: nowrap;
}

.detail-tabs :deep(tr.snapshot-row--active > td.el-table__cell) {
  background-color: var(--el-fill-color-light, #ecf5ff) !important;
}

.detail-tabs :deep(tr.snapshot-row--active > td.el-table-fixed-column--right) {
  background-color: var(--el-fill-color-light, #ecf5ff) !important;
}

.detail-tabs :deep(tr.snapshot-row--active:hover > td.el-table__cell) {
  background-color: var(--el-fill-color-light, #ecf5ff) !important;
}

.snapshot-drawer-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.snapshot-drawer-body {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.snapshot-source-path-cell {
  display: flex;
  width: 100%;
  min-width: 0;
  flex-direction: column;
  align-items: stretch;
  color: inherit;
  text-align: left;
}

.snapshot-source-path-cell__parent,
.snapshot-source-path-cell__child {
  display: flex;
  min-width: 0;
  align-items: center;
}

.snapshot-source-path-cell__parent {
  gap: 7px;
  color: var(--el-color-primary);
  font-weight: 650;
}

.snapshot-source-path-cell__parent:hover .snapshot-source-path-cell__path {
  text-decoration: underline;
}

.snapshot-source-path-cell__icon {
  flex: 0 0 auto;
}

.snapshot-source-path-cell__icon--dir {
  color: #d97706;
}

.snapshot-source-path-cell__icon--file {
  color: #2563eb;
}

.snapshot-source-path-cell__path,
.snapshot-source-path-cell__host {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.snapshot-source-path-cell__child {
  position: relative;
  margin-top: 4px;
  padding-left: 21px;
  gap: 6px;
  color: rgb(100 116 139);
  font-size: 12px;
}

.snapshot-source-path-cell__branch {
  width: 9px;
  height: 12px;
  flex: 0 0 auto;
  border-left: 1px solid rgb(203 213 225);
  border-bottom: 1px solid rgb(203 213 225);
  border-bottom-left-radius: 3px;
  transform: translateY(-3px);
}

.snapshot-start-link:visited {
  color: var(--el-color-primary);
}

.detail-tabs :deep(.snapshot-action-btn.el-button.is-link) {
  min-height: auto;
  padding: 0 4px;
  height: auto;
}

.detail-tabs :deep(.snapshot-action-btn.el-button--primary.is-link) {
  color: var(--el-color-primary, var(--color-primary)) !important;
}

.detail-tabs :deep(.snapshot-action-btn.el-button--primary.is-link:hover),
.detail-tabs :deep(.snapshot-action-btn.el-button--primary.is-link:focus) {
  color: var(--color-primary-hover, var(--el-color-primary-light-3)) !important;
  background-color: transparent !important;
}

.detail-tabs :deep(.snapshot-action-btn.el-button--danger.is-link) {
  color: var(--color-error) !important;
}

.detail-tabs :deep(.snapshot-action-btn.el-button--danger.is-link:hover),
.detail-tabs :deep(.snapshot-action-btn.el-button--danger.is-link:focus) {
  color: var(--el-color-danger-dark-2) !important;
  background-color: transparent !important;
}

.snapshot-action-btn + .snapshot-action-btn {
  margin-left: 8px;
}

.dp-restore-wizard-body {
  display: flex;
  flex-direction: column;
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

.dp-create-wizard {
  --dp-wiz-blue: #3d7ec8;
  --dp-wiz-blue-mid: #4588ce;
  --dp-wiz-blue-deep: #2d5f9e;
  --dp-wiz-blue-soft: rgba(61, 126, 200, 0.08);
}

.dp-create-wizard {
  border-radius: var(--radius-card);
  border: 1px solid rgb(226 232 240);
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.6) 0%, #ffffff 100%);
  padding: 14px 12px;
}

@media (min-width: 640px) {
  .dp-create-wizard {
    padding: 16px;
  }
}

.dp-create-wizard__list {
  display: grid;
  gap: 10px;
  grid-template-columns: 1fr;
}

@media (min-width: 768px) {
  .dp-create-wizard__list {
    grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) auto minmax(0, 1fr);
    align-items: stretch;
    gap: 0;
  }
}

.dp-create-wizard__step {
  display: flex;
  min-width: 0;
}

.dp-create-wizard__card {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  flex: 1 1 auto;
  min-width: 0;
  padding: 12px 12px 12px 14px;
  border-radius: 10px;
  border: 1px solid rgb(226 232 240);
  background: #fff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
  transition:
    border-color 0.22s ease,
    background 0.22s ease,
    box-shadow 0.22s ease,
    transform 0.22s ease;
}

.dp-create-wizard__icon-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 38px;
  height: 38px;
  border-radius: 9px;
  background: rgb(241 245 249);
  color: rgb(100 116 139);
  border: 1px solid rgb(226 232 240);
  transition:
    background 0.22s ease,
    color 0.22s ease,
    border-color 0.22s ease,
    box-shadow 0.22s ease;
}

.dp-create-wizard__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}

.dp-create-wizard__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.03em;
  font-feature-settings: 'tnum' 1;
  background: rgb(241 245 249);
  color: rgb(100 116 139);
  border: 1px solid rgb(226 232 240);
  transition:
    background 0.22s ease,
    color 0.22s ease,
    border-color 0.22s ease;
}

.dp-create-wizard__title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.35;
  color: rgb(30 41 59);
  transition: color 0.2s ease;
}

.dp-create-wizard__hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.45;
  color: rgb(100 116 139);
}

.dp-create-wizard__connector {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.dp-create-wizard__rail {
  height: 2px;
  border-radius: 2px;
  background: rgb(226 232 240);
  transition: background 0.25s ease;
}

.dp-create-wizard__chev {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 999px;
  background: #fff;
  color: rgb(148 163 184);
  border: 1px solid rgb(226 232 240);
  margin: 0 2px;
  transition:
    color 0.25s ease,
    border-color 0.25s ease,
    background 0.25s ease,
    box-shadow 0.25s ease;
}

@media (max-width: 767.98px) {
  .dp-create-wizard__connector {
    width: 100%;
    flex-direction: column;
    padding: 2px 0;
  }

  .dp-create-wizard__rail {
    width: 2px;
    height: 10px;
  }

  .dp-create-wizard__chev {
    transform: rotate(90deg);
  }
}

@media (min-width: 768px) {
  .dp-create-wizard__rail {
    width: 14px;
  }

  .dp-create-wizard__rail--left {
    background: linear-gradient(90deg, transparent, rgb(226 232 240));
  }

  .dp-create-wizard__rail--right {
    background: linear-gradient(90deg, rgb(226 232 240), transparent);
  }

  .dp-create-wizard__connector {
    padding: 0 4px;
  }
}

.dp-create-wizard__step--active .dp-create-wizard__card {
  border-color: rgba(61, 126, 200, 0.55);
  background: linear-gradient(180deg, #ffffff 0%, var(--dp-wiz-blue-soft) 100%);
  box-shadow:
    0 0 0 3px rgba(61, 126, 200, 0.12),
    0 6px 18px rgba(45, 95, 158, 0.12);
}

.dp-create-wizard__step--active .dp-create-wizard__icon-wrap {
  background: linear-gradient(155deg, var(--dp-wiz-blue-mid) 0%, var(--dp-wiz-blue-deep) 100%);
  color: #fff;
  border-color: transparent;
  box-shadow: 0 4px 10px rgba(45, 95, 158, 0.25);
}

.dp-create-wizard__step--active .dp-create-wizard__badge {
  background: var(--dp-wiz-blue);
  color: #fff;
  border-color: transparent;
}

.dp-create-wizard__step--active .dp-create-wizard__title {
  color: var(--dp-wiz-blue-deep);
}

.dp-create-wizard__step--done .dp-create-wizard__card {
  border-color: rgba(61, 126, 200, 0.4);
  background: linear-gradient(180deg, #ffffff 0%, rgba(61, 126, 200, 0.05) 100%);
}

.dp-create-wizard__step--done .dp-create-wizard__icon-wrap {
  background: rgba(61, 126, 200, 0.12);
  color: var(--dp-wiz-blue-deep);
  border-color: rgba(61, 126, 200, 0.25);
}

.dp-create-wizard__step--done .dp-create-wizard__badge {
  background: linear-gradient(155deg, var(--dp-wiz-blue-mid) 0%, var(--dp-wiz-blue-deep) 100%);
  color: #fff;
  border-color: transparent;
  box-shadow: 0 1px 2px rgba(45, 95, 158, 0.25);
}

.dp-create-wizard__step--done .dp-create-wizard__title {
  color: rgb(30 41 59);
}

.dp-create-wizard__step--pending .dp-create-wizard__card {
  background: rgb(252 253 254);
}

.dp-create-wizard__step--pending .dp-create-wizard__title {
  color: rgb(71 85 105);
}

.dp-create-wizard__connector--on .dp-create-wizard__chev {
  color: #fff;
  background: linear-gradient(155deg, var(--dp-wiz-blue-mid) 0%, var(--dp-wiz-blue-deep) 100%);
  border-color: transparent;
  box-shadow: 0 2px 6px rgba(45, 95, 158, 0.3);
}

@media (min-width: 768px) {
  .dp-create-wizard__connector--on .dp-create-wizard__rail--left {
    background: linear-gradient(90deg, rgba(61, 126, 200, 0.25), var(--dp-wiz-blue));
  }

  .dp-create-wizard__connector--on .dp-create-wizard__rail--right {
    background: linear-gradient(90deg, var(--dp-wiz-blue), rgba(61, 126, 200, 0.25));
  }
}

@media (max-width: 767.98px) {
  .dp-create-wizard__connector--on .dp-create-wizard__rail {
    background: var(--dp-wiz-blue);
  }
}

.dp-create-wizard {
  --dp-hbr-blue: #3d7ec8;
  --dp-hbr-blue-deep: #2d5f9e;
}

.dp-create-wizard {
  border: 0;
  background: transparent;
  padding: 0;
}

@media (min-width: 640px) {
  .dp-create-wizard {
    padding: 0;
  }
}

.dp-create-wizard__card {
  gap: 10px;
  min-height: 0;
  padding: 12px 12px 12px 14px;
  border-color: rgba(203, 213, 225, 0.95);
  background: rgba(255, 255, 255, 0.92);
  overflow: hidden;
  isolation: isolate;
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    background 0.18s ease,
    transform 0.18s ease;
}

.dp-create-wizard__card::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background: linear-gradient(125deg, transparent 58%, rgba(255, 255, 255, 0.2) 73%, transparent 88%);
  opacity: 0;
  transition: opacity 0.22s ease;
}

.dp-create-wizard__card::after {
  content: '';
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 0;
  z-index: 1;
  height: 3px;
  border-radius: 999px 999px 0 0;
  background: linear-gradient(90deg, transparent, rgba(59, 130, 246, 0.12), transparent);
  opacity: 0;
  transition: opacity 0.18s ease, background 0.18s ease;
}

.dp-create-wizard__card > * {
  position: relative;
  z-index: 2;
}

.dp-create-wizard__icon-wrap {
  width: 38px;
  height: 38px;
  border-radius: 9px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(248, 250, 252, 0.96) 100%);
  color: rgb(51 65 85);
  border-color: rgba(203, 213, 225, 0.95);
  box-shadow:
    0 1px 2px rgba(15, 23, 42, 0.05),
    inset 0 1px 0 rgba(255, 255, 255, 0.78);
}

.dp-create-wizard__title {
  font-size: 14px;
  line-height: 1.35;
  color: rgb(15 23 42);
}

.dp-create-wizard__hint {
  font-size: 12px;
  line-height: 1.45;
}

.dp-create-wizard__badge {
  background: rgba(248, 250, 252, 0.92);
  color: rgb(71 85 105);
  border-color: rgba(226, 232, 240, 0.95);
}

.dp-create-wizard__rail--left {
  background: linear-gradient(90deg, transparent, rgba(61, 126, 200, 0.35));
}

.dp-create-wizard__rail--right {
  background: linear-gradient(90deg, rgba(61, 126, 200, 0.35), transparent);
}

.dp-create-wizard__chev,
.dp-create-wizard__connector--on .dp-create-wizard__chev {
  width: 22px;
  height: 22px;
  color: var(--dp-hbr-blue-deep);
  background: linear-gradient(160deg, var(--color-card-bg, #ffffff) 0%, var(--color-grey-1, #eef3f9) 100%);
  border-color: rgba(61, 126, 200, 0.3);
  box-shadow:
    0 4px 12px rgba(45, 95, 158, 0.14),
    inset 0 1px 0 rgba(255, 255, 255, 0.95);
}

.dp-create-wizard__step--active .dp-create-wizard__card {
  border-color: rgb(29 78 216);
  background: linear-gradient(180deg, rgba(219, 234, 254, 0.88) 0%, rgba(239, 246, 255, 0.92) 100%);
  box-shadow:
    0 0 0 1px rgba(29, 78, 216, 0.08) inset,
    0 8px 18px rgba(37, 99, 235, 0.12);
}

.dp-create-wizard__step--active .dp-create-wizard__card::before,
.dp-create-wizard__step--active .dp-create-wizard__card::after,
.dp-create-wizard__step--done .dp-create-wizard__card::after {
  opacity: 1;
}

.dp-create-wizard__step--active .dp-create-wizard__card::after {
  background: linear-gradient(90deg, transparent, rgba(37, 99, 235, 0.72), transparent);
}

.dp-create-wizard__step--active .dp-create-wizard__icon-wrap,
.dp-create-wizard__step--done .dp-create-wizard__icon-wrap {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(239, 246, 255, 0.98) 100%);
  color: rgb(29 78 216);
  border-color: rgb(147 197 253);
}

.dp-create-wizard__step--active .dp-create-wizard__badge {
  background: rgba(255, 255, 255, 0.84);
  border-color: rgba(147, 197, 253, 0.95);
  color: rgb(29 78 216);
}

.dp-create-wizard__step--done .dp-create-wizard__card {
  border-color: rgba(147, 197, 253, 0.95);
  background: linear-gradient(180deg, rgba(239, 246, 255, 0.88) 0%, rgba(255, 255, 255, 0.94) 100%);
  box-shadow: 0 4px 12px rgba(37, 99, 235, 0.07);
}

.dp-create-wizard__step--done .dp-create-wizard__badge {
  background: rgb(29 78 216);
  color: #fff;
  border-color: transparent;
}

.dp-create-wizard__step--pending .dp-create-wizard__card {
  background: rgba(255, 255, 255, 0.92);
}

.rec-select-tree,
.rec-dest-tree {
  max-height: none;
  overflow: auto;
}

.rec-select-tree :deep(.el-tree-node__content),
.rec-dest-tree :deep(.el-tree-node__content) {
  height: auto;
  min-height: 34px;
  padding-top: 3px;
  padding-bottom: 3px;
  border-radius: 8px;
}

.rec-tree-node-label {
  display: flex;
  width: 100%;
}

.rec-custom-arrow {
  color: var(--el-color-primary);
  font-size: 12px;
  line-height: 1;
  width: 12px;
  display: inline-flex;
  justify-content: center;
}

.rec-custom-arrow--hidden {
  visibility: hidden;
}
</style>

<style>
.snapshot-detail-drawer.el-drawer.rtl .el-drawer__body {
  padding-top: 0;
}

.task-detail-drawer.el-drawer.rtl .el-drawer__body {
  padding-top: 0;
}

.snapshot-dir-contents-drawer.el-drawer.rtl {
  width: min(760px, 92vw);
  max-width: 100%;
}
.snapshot-dir-contents-drawer .el-drawer__body {
  overflow: auto;
}
</style>
