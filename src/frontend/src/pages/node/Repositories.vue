<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { Plus, RefreshCw, Search, Filter, ChevronDown, Pencil, Trash2, Copy, HardDrive, Folder, Unlink } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import type { ElTable } from 'element-plus'
import { api, apiErrorMessage } from '../../lib/api'
import { copyTextToClipboard } from '../../lib/clipboard'
import { formatAppDateTime, formatAppTime, parseLocalDateTime } from '../../lib/dateTime'
import { listAllNodes, type EnrollmentOs } from '../../lib/nodeApi'
import Modal from '../../components/Modal.vue'
import ModulePage from '../../components/ModulePage.vue'
import { useProtectionSideNav } from '../../composables/useProtectionSideNav'
import { useListTableLayout } from '../../composables/useListTableLayout'
import { useListSearch } from '../../composables/useListSearch'
import { nasMountProtocolIcon } from '../../lib/resourceIcons'
import {
  DEFAULT_S3_OBJECT_PREFIX,
  distinctS3EndpointPair,
  s3EndpointDisplay,
  s3PlatformLabelKey,
} from '../../lib/s3PlatformDisplay'
import S3PlatformBrandIcon from '../../components/S3PlatformBrandIcon.vue'
import HflCapacityCell from '../../components/HflCapacityCell.vue'
import HflBooleanStatusTag from '../../components/HflBooleanStatusTag.vue'
import { repositoryCapacityParts, repositoryStorageParts } from '../../lib/repositoryCapacityDisplay'
import { lifecycleStatusTagAttrs } from '../../lib/statusTag'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import { usePageRequestScope } from '../../composables/usePageRequestScope'
import {
  createStorageRepository,
  listStorageRepositories,
  listStorageRepositoryAssociatedSources,
  listStorageRepositoryTasks,
  deleteStorageRepository,
  preflightStorageRepositoryCleanup,
  type StorageRepositoryCleanupPreflight,
  type StorageRepositoryAssociatedSource,
} from '../../lib/storageRepositoryApi'
import { getTask, type TaskRow } from '../../lib/taskApi'
import type { ApiNode } from '../../types/node'
import AgentPlatformBrandIcon from '../../components/agent-deploy/AgentPlatformBrandIcon.vue'
import HflHelpTip from '../../components/HflHelpTip.vue'
import DangerConfirmDialog, { type DangerConfirmItem } from '../../components/DangerConfirmDialog.vue'
import TaskDetailDrawer from '../protection/components/TaskDetailDrawer.vue'
import FlowSourceConnectionCell from '../protection/components/FlowSourceConnectionCell.vue'

export type RepoKind = 's3' | 'nas' | 'proxy_fs'

export type RepoLifecycleStatus =
  | 'creating'
  | 'create_failed'
  | 'created'
  | 'removing'
  | 'remove_failed'
  | 'removed'

export type RepoHealth = 'online' | 'offline' | 'unverified'

export type NasProtocol = 'smb' | 'nfs'
export type BindNodeType = 'agent' | 'proxy'
export type RepoScope = 'private' | 'shared'

export type RepositoryRow = {
  id: number
  organization_id: number
  name: string
  kind: RepoKind
  status: RepoLifecycleStatus
  health: RepoHealth
  capacity_bytes: number
  estimated_usage_bytes: number
  physical_usage_bytes?: number | null
  storage_total_bytes: number
  storage_used_bytes: number
  storage_available_bytes: number
  storage_pool_key?: string
  storage_mount_point?: string
  last_checked_at?: string | null
  usage_probe_status?: string
  capacity_probe_status?: string
  created_at?: string | null
  source_node_id: number
  source_node_name: string
  location: string
  config: RepositoryConfig
  protocol?: NasProtocol
  bind_node_type?: BindNodeType
  bind_node_id?: number
  bind_node_name?: string
  repo_scope?: RepoScope
  s3_platform?: string
}

export type S3UrlStyle = 'auto' | 'virtual_hosted' | 'path'

export type RepositoryConfig = {
  server_address?: string
  share_path?: string
  bucket?: string
  region?: string
  endpoint?: string
  external_endpoint?: string
  internal_endpoint?: string
  prefix?: string
  access_key_id?: string
  secret_access_key?: string
  s3_url_style?: S3UrlStyle
  use_tls?: boolean
  mount_path?: string
  protocol?: NasProtocol
  bind_node_type?: BindNodeType
  bind_node_id?: number
  bind_node_name?: string
  bind_node_ip?: string
  proxy_mount_path?: string
  repo_scope?: RepoScope
  repo_dir?: string
  smb_server?: string
  smb_username?: string
  smb_password?: string
  smb_domain?: string
  nfs_host?: string
  nfs_export?: string
  nfs_options?: string
  proxy_node_id?: number
  proxy_node_name?: string
  proxy_node_ip?: string
  proxy_node_dir?: string
  proxy_node_base_dir?: string
  proxy_fs_layout?: string
  quota_gb?: number
  quota_alert_enabled?: boolean
  quota_alert_threshold?: number
}

type ApiRepository = {
  id: number
  organization?: number
  organization_id?: number
  name: string
  repo_type: string
  status: string
  config?: Record<string, unknown>
  health?: string
  health_failures?: number
  credential_id?: number | null
  s3_platform?: string | null
  s3_bucket?: string | null
  capacity_bytes: number
  estimated_usage_bytes: number
  physical_usage_bytes?: number | null
  storage_total_bytes?: number
  storage_used_bytes?: number
  storage_available_bytes?: number
  storage_pool_key?: string
  storage_mount_point?: string
  last_checked_at?: string | null
  usage_probe_status?: string
  capacity_probe_status?: string
  created_at?: string | null
  updated_at?: string | null
  nas_protocol?: NasProtocol | null
  s3_url_style?: string
  use_tls?: boolean
  protocol?: NasProtocol
  bind_node_type?: BindNodeType
  bind_node_id?: number | string | null
  bind_node_display_name?: string | null
  bind_node_name?: string
  bind_node_ip?: string | null
  proxy_node_id?: number
  proxy_node_name?: string
  proxy_node_ip?: string
  repo_scope?: RepoScope
  mount_path?: string
  smb_server?: string
  smb_username?: string
  smb_domain?: string
  nfs_host?: string
  nfs_export?: string
  nfs_options?: string
  active_cleanup_task?: Pick<TaskRow, 'task_uuid' | 'status' | 'error_code' | 'error_message' | 'created_at'> | null
}

const TABLE_HEADER_STYLE: Record<string, string> = {
  background: 'rgba(248, 250, 252, 0.96)',
  color: 'rgb(71 85 105)',
  fontWeight: '600',
  whiteSpace: 'nowrap',
}
const AGENT_REPOSITORY_MOUNT_ROOT = '/var/lib/hyperfilelens-agent/mounts/repositories'

function proxyRepositoryMountPath(repositoryId?: number | null, proxyId?: number | null) {
  if (!repositoryId || !proxyId) return ''
  return `${AGENT_REPOSITORY_MOUNT_ROOT}/repo-${repositoryId}-node-${proxyId}`
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

function formatLocalDateTime(value?: string | null) {
  return formatAppDateTime(value, '—')
}

function parseDateTime(value: string) {
  return parseLocalDateTime(value)
}

function emptySourceNode(): ApiNode {
  return {
    id: 0,
    organization: 0,
    name: '',
    role: 'agent',
    status: 'offline',
    ip_address: null,
  }
}

function findSourceNodeById(nodes: ApiNode[], nodeId?: number | null) {
  if (nodeId == null) return null
  return nodes.find((node) => node.id === nodeId) ?? null
}

function resolveSourceNode(params: {
  nodes: ApiNode[]
  preferredIds: Array<number | null | undefined>
  fallbackName?: string
  fallbackIp?: string
}): ApiNode {
  for (const nodeId of params.preferredIds) {
    const matched = findSourceNodeById(params.nodes, nodeId)
    if (matched) return matched
  }
  const name = params.fallbackName?.trim() || ''
  return {
    ...emptySourceNode(),
    id: params.preferredIds.find((nodeId): nodeId is number => typeof nodeId === 'number') ?? 0,
    name,
    ip_address: params.fallbackIp?.trim() || null,
  }
}

function configString(config: Record<string, unknown>, key: string) {
  const value = config[key]
  return typeof value === 'string' ? value.trim() : ''
}

function configStringAny(config: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = configString(config, key)
    if (value) return value
  }
  return ''
}

function configNumber(config: Record<string, unknown>, key: string) {
  const value = config[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function configBoolean(config: Record<string, unknown>, key: string) {
  const value = config[key]
  return typeof value === 'boolean' ? value : undefined
}

function numericId(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return undefined
}

function normalizeProtocol(raw: string | undefined | null): NasProtocol | undefined {
  if (raw === 'smb' || raw === 'nfs') return raw
  return undefined
}

function normalizeBindNodeType(raw: string | undefined | null): BindNodeType | undefined {
  if (raw === 'agent' || raw === 'proxy') return raw
  return undefined
}

function kindFromApiRepo(repo: ApiRepository): RepoKind {
  const t = (repo.repo_type || '').toLowerCase()
  const config = repo.config ?? {}
  if (t === 's3') return 's3'
  if (t === 'proxy_fs' || t === 'proxy-fs') return 'proxy_fs'
  if (
    t === 'filesystem' &&
    (configNumber(config, 'proxy_node_id') != null ||
      !!configString(config, 'proxy_node_name') ||
      !!configString(config, 'proxy_node_dir'))
  ) {
    return 'proxy_fs'
  }
  return 'nas'
}

const LIFECYCLE_KEYS = new Set<string>([
  'creating',
  'create_failed',
  'created',
  'removing',
  'remove_failed',
  'removed',
])

function normalizeLifecycleStatus(raw: string): RepoLifecycleStatus {
  const trimmed = (raw || '').trim()
  const s = trimmed.toLowerCase().replace(/\s+/g, '_')
  if (LIFECYCLE_KEYS.has(s)) return s as RepoLifecycleStatus
  if (s === 'ok' || s === 'healthy' || s === 'success' || s === 'active' || s === 'ready') return 'created'
  if (s === 'degraded' || s === 'warning') return 'created'
  if (s === 'error' || s === 'failed') return 'create_failed'
  return 'created'
}

function normalizeHealth(raw: string | undefined | null, statusHint?: string): RepoHealth {
  const trimmed = (raw || '').trim()
  const x = trimmed.toLowerCase()
  if (x === 'online' || x === 'up' || x === '1' || x === 'true') return 'online'
  if (x === 'offline' || x === 'down' || x === '0' || x === 'false') return 'offline'
  if (x === 'unverified' || x === 'pending' || x === 'pending_verification') return 'unverified'
  const st = (statusHint || '').toLowerCase()
  if (st === 'degraded' || st === 'offline') return 'offline'
  return 'online'
}

function normalizeS3UrlStyle(raw: string | undefined | null, platform?: string): S3UrlStyle {
  const s = (raw || '').toString().toLowerCase().trim().replace(/-/g, '_')
  if (s === 'path' || s === 'path_style') return 'path'
  if (s === 'virtual_hosted' || s === 'virtual') return 'virtual_hosted'
  return platform === 'huaweicloud' ? 'virtual_hosted' : 'auto'
}

function mapApiToRow(r: ApiRepository): RepositoryRow {
  const config = r.config ?? {}
  const kind = kindFromApiRepo(r)
  const lifecycle = normalizeLifecycleStatus(r.status)
  const health = normalizeHealth(r.health, r.status)
  const organizationId = r.organization_id ?? r.organization ?? 0
  const bucket = r.s3_bucket || configString(config, 'bucket') || r.name
  const prefix = configString(config, 'prefix')
  const mountPath = configString(config, 'mount_path') || r.mount_path || ''
  const serverAddress = configString(config, 'server_address')
  const sharePath = configString(config, 'share_path')
  const protocol = normalizeProtocol(r.nas_protocol || configString(config, 'protocol') || r.protocol)
  const bindNodeType = normalizeBindNodeType(configString(config, 'bind_node_type') || r.bind_node_type)
  const bindNodeId = configNumber(config, 'bind_node_id') ?? numericId(r.bind_node_id)
  const bindNodeName = configString(config, 'bind_node_name') || r.bind_node_display_name || r.bind_node_name || ''
  const proxyNodeId = configNumber(config, 'proxy_node_id') ?? r.proxy_node_id
  const proxyNodeName = configString(config, 'proxy_node_name') || r.proxy_node_name || ''
  const proxyNodeDir = configString(config, 'proxy_node_dir')
  const proxyNodeBaseDir = configString(config, 'proxy_node_base_dir')
  const proxyMountPath = configString(config, 'proxy_mount_path')
  const repoScope = (configString(config, 'repo_scope') || r.repo_scope || '') as RepoScope | ''
  const loc =
    kind === 's3'
      ? prefix
        ? `s3://${bucket}/${prefix.replace(/^\/+/, '')}`
        : `s3://${bucket}`
      : kind === 'proxy_fs'
        ? proxyNodeDir || mountPath || `/repos/${(r.name || 'repo').toLowerCase().replace(/\s+/g, '-')}`
        : mountPath ||
          (serverAddress && sharePath
            ? protocol === 'smb'
              ? `//${serverAddress}/${sharePath.replace(/^\/+/, '')}`
              : `${serverAddress}:${sharePath}`
            : `/mnt/${(r.name || 'repo').toLowerCase().replace(/\s+/g, '-')}`)

  const sourceNode = resolveSourceNode({
    nodes: sourceNodes.value,
    preferredIds: [bindNodeId, proxyNodeId],
    fallbackName: bindNodeName || proxyNodeName,
    fallbackIp: r.bind_node_ip || r.proxy_node_ip || undefined,
  })
  const bindNodeIp =
    configStringAny(config, ['bind_node_ip', 'bindNodeIp', 'node_ip', 'nodeIp', 'ip_address']) ||
    r.bind_node_ip ||
    findSourceNodeById(sourceNodes.value, bindNodeId)?.ip_address ||
    ''
  const proxyNodeIp =
    configStringAny(config, ['proxy_node_ip', 'proxyNodeIp', 'host_ip', 'ip_address']) ||
    r.proxy_node_ip ||
    findSourceNodeById(sourceNodes.value, proxyNodeId)?.ip_address ||
    findSourceNodeById(sourceNodes.value, bindNodeId)?.ip_address ||
    ''

  if (kind === 's3') {
    return {
      id: r.id,
      organization_id: organizationId,
      name: r.name,
      kind,
      status: lifecycle,
      health,
      capacity_bytes: r.capacity_bytes,
      estimated_usage_bytes: r.estimated_usage_bytes,
      physical_usage_bytes: r.physical_usage_bytes ?? null,
      storage_total_bytes: r.storage_total_bytes ?? 0,
      storage_used_bytes: r.storage_used_bytes ?? 0,
      storage_available_bytes: r.storage_available_bytes ?? 0,
      storage_pool_key: r.storage_pool_key || '',
      storage_mount_point: r.storage_mount_point || '',
      last_checked_at: r.last_checked_at ?? null,
      usage_probe_status: r.usage_probe_status || 'pending',
      capacity_probe_status: r.capacity_probe_status || 'pending',
      created_at: r.created_at ?? null,
      source_node_id: sourceNode.id,
      source_node_name: sourceNode.name,
      location: loc,
      s3_platform: r.s3_platform || configString(config, 's3_platform') || 'custom',
      config: {
        bucket,
        region: configString(config, 'region') || r.region || '',
        endpoint: configString(config, 'endpoint') || r.endpoint || '',
        external_endpoint: configString(config, 'external_endpoint'),
        internal_endpoint: configString(config, 'internal_endpoint'),
        prefix,
        access_key_id: configString(config, 'access_key_id') || r.access_key_id || '',
        secret_access_key: configString(config, 'secret_access_key') || '',
        s3_url_style: normalizeS3UrlStyle(
          configString(config, 's3_url_style') || r.s3_url_style,
          r.s3_platform,
        ),
        use_tls: configBoolean(config, 'use_tls') ?? r.use_tls !== false,
        quota_gb: configNumber(config, 'quota_gb') ?? 0,
        quota_alert_enabled: configBoolean(config, 'quota_alert_enabled') ?? false,
        quota_alert_threshold: configNumber(config, 'quota_alert_threshold') ?? 0,
      },
    }
  }

  if (kind === 'proxy_fs') {
    return {
      id: r.id,
      organization_id: organizationId,
      name: r.name,
      kind,
      status: lifecycle,
      health,
      capacity_bytes: r.capacity_bytes,
      estimated_usage_bytes: r.estimated_usage_bytes,
      physical_usage_bytes: r.physical_usage_bytes ?? null,
      storage_total_bytes: r.storage_total_bytes ?? 0,
      storage_used_bytes: r.storage_used_bytes ?? 0,
      storage_available_bytes: r.storage_available_bytes ?? 0,
      storage_pool_key: r.storage_pool_key || '',
      storage_mount_point: r.storage_mount_point || '',
      last_checked_at: r.last_checked_at ?? null,
      usage_probe_status: r.usage_probe_status || 'pending',
      capacity_probe_status: r.capacity_probe_status || 'pending',
      created_at: r.created_at ?? null,
      source_node_id: sourceNode.id,
      source_node_name: proxyNodeName || sourceNode.name,
      location: loc,
      config: {
        proxy_node_id: proxyNodeId,
        proxy_node_name: proxyNodeName || sourceNode.name,
        proxy_node_ip: proxyNodeIp || sourceNode.ip_address,
        proxy_node_dir: proxyNodeDir || loc,
        proxy_node_base_dir: proxyNodeBaseDir,
        proxy_fs_layout: configString(config, 'proxy_fs_layout'),
        proxy_repository_server_host: configString(config, 'proxy_repository_server_host'),
        quota_gb: configNumber(config, 'quota_gb') ?? 0,
        quota_alert_enabled: configBoolean(config, 'quota_alert_enabled') ?? false,
        quota_alert_threshold: configNumber(config, 'quota_alert_threshold') ?? 0,
      },
    }
  }

  const resolvedBindNodeName = bindNodeName || sourceNode.name
  return {
    id: r.id,
    organization_id: organizationId,
    name: r.name,
    kind,
    status: lifecycle,
    health,
    capacity_bytes: r.capacity_bytes,
    estimated_usage_bytes: r.estimated_usage_bytes,
    physical_usage_bytes: r.physical_usage_bytes ?? null,
    storage_total_bytes: r.storage_total_bytes ?? 0,
    storage_used_bytes: r.storage_used_bytes ?? 0,
    storage_available_bytes: r.storage_available_bytes ?? 0,
    storage_pool_key: r.storage_pool_key || '',
    storage_mount_point: r.storage_mount_point || '',
    last_checked_at: r.last_checked_at ?? null,
    usage_probe_status: r.usage_probe_status || 'pending',
    capacity_probe_status: r.capacity_probe_status || 'pending',
    created_at: r.created_at ?? null,
    source_node_id: sourceNode.id,
    source_node_name: sourceNode.name,
    location: loc,
    protocol,
    bind_node_type: bindNodeType,
    bind_node_id: bindNodeId,
    bind_node_name: resolvedBindNodeName,
    repo_scope: repoScope || undefined,
    config: {
      mount_path: mountPath || loc,
      protocol,
      bind_node_type: bindNodeType,
      bind_node_id: bindNodeId,
      bind_node_name: resolvedBindNodeName,
      bind_node_ip: bindNodeIp,
      proxy_node_id: bindNodeType === 'proxy' ? bindNodeId : proxyNodeId,
      proxy_node_name: bindNodeType === 'proxy' ? resolvedBindNodeName : proxyNodeName,
      proxy_node_ip: bindNodeType === 'proxy' ? bindNodeIp : proxyNodeIp,
      proxy_mount_path: proxyMountPath || (bindNodeType === 'proxy' ? proxyRepositoryMountPath(r.id, bindNodeId) : ''),
      repo_scope: repoScope || undefined,
      server_address: serverAddress,
      share_path: sharePath,
      repo_dir: configString(config, 'repo_dir') || sharePath || mountPath || loc,
      smb_server: configString(config, 'smb_server') || r.smb_server || (protocol === 'smb' ? serverAddress || loc : ''),
      smb_username: configString(config, 'smb_username') || r.smb_username,
      smb_password: configString(config, 'smb_password') || '',
      smb_domain: configString(config, 'smb_domain') || r.smb_domain,
      nfs_host: configString(config, 'nfs_host') || r.nfs_host || (protocol === 'nfs' ? serverAddress : ''),
      nfs_export: configString(config, 'nfs_export') || r.nfs_export || (protocol === 'nfs' ? sharePath : ''),
      nfs_options: configString(config, 'nfs_options') || r.nfs_options || configString(config, 'mount_options'),
      quota_gb: configNumber(config, 'quota_gb') ?? 0,
      quota_alert_enabled: configBoolean(config, 'quota_alert_enabled') ?? false,
      quota_alert_threshold: configNumber(config, 'quota_alert_threshold') ?? 0,
    },
  }
}

function normalizeTabParam(tab: unknown): RepoKind {
  return tab === 'nas' || tab === 'proxy_fs' || tab === 's3' ? tab : 's3'
}

const rows = ref<RepositoryRow[]>([])
const sourceNodes = ref<ApiNode[]>([])
const busy = ref(false)
const repositoryListLoaded = ref(false)
const repositoryListError = ref<string | null>(null)
const activeTab = ref<RepoKind>('s3')
const formKind = ref<RepoKind>('s3')
const modalOpen = ref(false)
const modalMode = ref<'add' | 'edit'>('add')
const editingId = ref<number | null>(null)

const searchQuery = ref('')
const searchField = ref<'name' | 'bucket' | 'server'>('name')
const {
  appliedSearch: appliedSearchQuery,
  clearSearch,
  handleSearchFieldChange,
  resetSearch,
  runSearchNow: runFilterSearch,
} = useListSearch(searchQuery, () => {
  applySearch()
})
const pageSize = ref(10)
const currentPage = ref(1)
const selectedRows = ref<RepositoryRow[]>([])
const repositoryTotal = ref(0)
const deleteRepositoriesDialogOpen = ref(false)
const pendingDeleteRepositories = ref<RepositoryRow[]>([])
const repositoryCleanupBlockedDialogOpen = ref(false)
const repositoryCleanupBlockedRows = ref<Array<{
  repository: RepositoryRow
  blockers: string[]
  associations: StorageRepositoryAssociatedSource[]
  associationCount: number
}>>([])
const forceDeleteRepository = ref(false)
const normalDeletePreflight = ref<StorageRepositoryCleanupPreflight | null>(null)
const forceDeletePreflight = ref<StorageRepositoryCleanupPreflight | null>(null)
const moreActionsOpen = ref(false)
const tableRef = ref<InstanceType<typeof ElTable>>()
const tableBlockRef = ref<HTMLElement | null>(null)
const {
  tableMaxHeight,
  layoutTable,
  handleTableScroll: onTableScroll,
} = useListTableLayout(tableRef, tableBlockRef)
const drawerDetailOpen = ref(false)
const detailRow = ref<RepositoryRow | null>(null)
const detailDistinctS3Endpoints = computed(() => {
  const config = detailRow.value?.config
  return distinctS3EndpointPair(config?.external_endpoint, config?.internal_endpoint)
})
const detailActiveTab = ref('basic')
const associatedSources = ref<StorageRepositoryAssociatedSource[]>([])
const associatedSourcesLoading = ref(false)
const associatedSourcesPage = ref(1)
const associatedSourcesPageSize = ref(10)
const associatedSourcesCount = ref(0)
const repositoryTasks = ref<TaskRow[]>([])
const repositoryTasksLoading = ref(false)
const repositoryTasksError = ref('')
const repositoryTasksPage = ref(1)
const repositoryTasksPageSize = ref(10)
const repositoryTasksCount = ref(0)
const repositoryTaskSearch = ref('')
const repositoryTaskSearchField = ref<'name' | 'uuid'>('name')
const repositoryTaskOperation = ref('')
const repositoryTaskStatus = ref('')
const repositoryTaskTimeMode = ref<'all' | '24h' | '7d' | '30d' | 'range'>('all')
const repositoryTaskDateRange = ref<[Date, Date] | null>(null)
const repositoryTaskAdvancedFilterOpen = ref(false)
const repositoryTaskAdvancedFilterDraft = reactive({
  dateRange: null as [Date, Date] | null,
})
const lastRepositoryTaskQuickTimeMode = ref<'all' | '24h' | '7d' | '30d'>('all')
const repositoryTaskSearchFieldOptions = computed(() => [
  { value: 'name' as const, label: t('ops.task.searchName') },
  { value: 'uuid' as const, label: t('ops.task.searchUuid') },
])
const repositoryTaskTimeModeOptions = computed(() => [
  { value: 'all' as const, label: t('ops.task.timeAll') },
  { value: '24h' as const, label: t('ops.task.time24h') },
  { value: '7d' as const, label: t('ops.task.time7d') },
  { value: '30d' as const, label: t('ops.task.time30d') },
  { value: 'range' as const, label: t('ops.task.timeRange') },
])
const repositoryTaskDateRangeShortcuts = computed(() => [
  { text: t('ops.task.time24h'), value: () => repositoryTaskRangeForHours(24) },
  { text: t('ops.task.time7d'), value: () => repositoryTaskRangeForHours(7 * 24) },
  { text: t('ops.task.time30d'), value: () => repositoryTaskRangeForHours(30 * 24) },
])
const repositoryTaskAdvancedFilterCount = computed(() => (
  repositoryTaskTimeMode.value === 'range' && repositoryTaskDateRange.value ? 1 : 0
))
const {
  appliedSearch: appliedRepositoryTaskSearch,
  cancelSearch: cancelRepositoryTaskSearch,
  clearSearch: clearRepositoryTaskSearch,
  handleSearchFieldChange: handleRepositoryTaskSearchFieldChange,
  resetSearch: resetRepositoryTaskSearch,
  runSearchNow: runRepositoryTaskSearchNow,
} = useListSearch(repositoryTaskSearch, () => {
  applyRepositoryTaskFilters()
})
const repositoryTaskDetailOpen = ref(false)
const repositoryTaskDetailUuid = ref('')
let repositoryTasksPollTimer: ReturnType<typeof setInterval> | undefined
const rowAssociatedSourceCounts = ref<Record<number, number>>({})
const { drawerSize, updateDrawerWidth, bindDrawerResize, unbindDrawerResize } = useResponsiveDrawerWidth()
const { drawerSize: nestedDrawerSize } = useResponsiveDrawerWidth(2)
const repositoryTaskDetailDrawerSize = computed(() => {
  const outerWidth = Number.parseFloat(drawerSize.value)
  const referenceWidth = Number.isFinite(outerWidth) && outerWidth > 0
    ? Math.max(650, Math.min(775, (outerWidth - 120) * 1.25))
    : 700
  const viewportWidth = typeof window === 'undefined' ? referenceWidth : Math.max(320, window.innerWidth - 16)
  return `${Math.min(referenceWidth, viewportWidth)}px`
})

let mounted = false

const repositoryTableLoading = computed(() => busy.value || !repositoryListLoaded.value)

const form = ref({
  source_node_id: undefined as number | undefined,
  name: '',
  bucket: '',
  region: '',
  endpoint: '',
  prefix: DEFAULT_S3_OBJECT_PREFIX,
  access_key_id: '',
  secret_access_key: '',
  s3_url_style: 'auto' as S3UrlStyle,
  use_tls: true,
  mount_path: '',
})

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const pageRequests = usePageRequestScope()
const bindProxyLeadItems = computed(() => [
  t('addNasRepo.bindProxyLeadItemRecommend'),
  t('addNasRepo.bindProxyLeadItemAfterBinding'),
  t('addNasRepo.bindProxyLeadItemSkip'),
])

const nodeMenus = useProtectionSideNav()

const pageTitle = computed(() => {
  switch (activeTab.value) {
    case 'nas':
      return t('repositoriesPage.tabNas')
    case 'proxy_fs':
      return t('repositoriesPage.tabProxyFs')
    default:
      return t('repositoriesPage.tabS3')
  }
})

const listSearchPlaceholder = computed(() => t('protection.listSearch.placeholder'))

const searchFieldOptions = computed(() => {
  if (activeTab.value === 's3') {
    return [
      { value: 'name', label: t('protection.listSearchFields.name') },
      { value: 'bucket', label: t('protection.listSearchFields.bucket') },
    ] as const
  }
  if (activeTab.value === 'nas') {
    return [
      { value: 'name', label: t('protection.listSearchFields.name') },
      { value: 'server', label: t('protection.listSearchFields.server') },
    ] as const
  }
  return [
    { value: 'name', label: t('protection.listSearchFields.name') },
  ] as const
})

const REPO_STATUS_I18N: Record<RepoLifecycleStatus, string> = {
  creating: 'repositoriesPage.statusCreating',
  create_failed: 'repositoriesPage.statusCreateFailed',
  created: 'repositoriesPage.statusCreated',
  removing: 'repositoriesPage.statusRemoving',
  remove_failed: 'repositoriesPage.statusRemoveFailed',
  removed: 'repositoriesPage.statusRemoved',
}

function repoLifecycleLabel(s: RepoLifecycleStatus | string) {
  const k = normalizeLifecycleStatus(String(s))
  return t(REPO_STATUS_I18N[k])
}

function repoHealthLabel(h: RepoHealth | string) {
  const k = normalizeHealth(String(h))
  if (k === 'unverified') return t('repositoriesPage.healthUnverified')
  return k === 'online' ? t('repositoriesPage.healthOnline') : t('repositoriesPage.healthOffline')
}

const deleteRepositoriesTitle = computed(() =>
  activeTab.value === 's3'
    ? t('repositoriesPage.batchDeleteS3Title')
    : t('repositoriesPage.batchDeleteTitle'),
)

const deleteRepositoriesMessage = computed(() =>
  activeTab.value === 's3'
    ? t('repositoriesPage.batchDeleteS3Confirm', { n: pendingDeleteRepositories.value.length })
    : t('repositoriesPage.batchDeleteConfirm', { n: pendingDeleteRepositories.value.length }),
)

const deleteRepositoriesItems = computed<DangerConfirmItem[]>(() =>
  pendingDeleteRepositories.value.map((row) => ({
    key: row.id,
    name: row.name,
    status: {
      label: repoLifecycleLabel(row.status),
      tone: lifecycleTagType(row.status),
    },
    description: row.location || row.source_node_name || row.kind,
  })),
)

const canForceDeleteRepository = computed(() => pendingDeleteRepositories.value.length === 1)
const activeDeletePreflight = computed(() => (
  forceDeleteRepository.value ? forceDeletePreflight.value : normalDeletePreflight.value
))
const deleteConfirmKeyword = computed(() => (
  forceDeleteRepository.value ? 'FORCE CLEANUP' : t('common.deleteKeyword')
))
const deletePreflightMessages = computed(() => {
  const preflight = activeDeletePreflight.value
  if (!preflight) return []
  return [
    ...preflight.blockers.map(item => item.detail),
    ...preflight.warnings.map(item => item.detail),
  ]
})
const deleteConfirmDisabled = computed(() => (
  Boolean(activeDeletePreflight.value) && !activeDeletePreflight.value?.allowed
))

const REPOSITORY_CLEANUP_PENDING_KEY = 'hfl-repository-cleanup-pending-v1'
const REPOSITORY_CREATE_PENDING_KEY = 'hfl-repository-create-pending-v1'
const REPOSITORY_CLEANUP_POLL_MS = 2000
const REPOSITORY_CLEANUP_TIMEOUT_MS = 15 * 60 * 1000
const REPOSITORY_CREATE_TIMEOUT_MS = 15 * 60 * 1000
type RepositoryPendingEntry = {
  taskUuid: string
  repositoryName: string
  startedAt: number
}
const repositoryCleanupPending = ref(new Map<number, RepositoryPendingEntry>())
const repositoryCreatePending = ref(new Map<number, RepositoryPendingEntry>())
let repositoryCleanupPollTimer: number | null = null
let repositoryCleanupPollInFlight = false
let repositoryCreatePollTimer: number | null = null
let repositoryCreatePollInFlight = false

function persistRepositoryCleanupPending() {
  if (!repositoryCleanupPending.value.size) {
    sessionStorage.removeItem(REPOSITORY_CLEANUP_PENDING_KEY)
    return
  }
  sessionStorage.setItem(
    REPOSITORY_CLEANUP_PENDING_KEY,
    JSON.stringify([...repositoryCleanupPending.value.entries()]),
  )
}

function persistRepositoryCreatePending() {
  if (!repositoryCreatePending.value.size) {
    sessionStorage.removeItem(REPOSITORY_CREATE_PENDING_KEY)
    return
  }
  sessionStorage.setItem(
    REPOSITORY_CREATE_PENDING_KEY,
    JSON.stringify([...repositoryCreatePending.value.entries()]),
  )
}

function restoreRepositoryCleanupPending() {
  try {
    const raw = sessionStorage.getItem(REPOSITORY_CLEANUP_PENDING_KEY)
    repositoryCleanupPending.value = raw
      ? new Map(JSON.parse(raw) as Array<[number, RepositoryPendingEntry]>)
      : new Map()
  } catch {
    repositoryCleanupPending.value = new Map()
    sessionStorage.removeItem(REPOSITORY_CLEANUP_PENDING_KEY)
  }
}

function restoreRepositoryCreatePending() {
  try {
    const raw = sessionStorage.getItem(REPOSITORY_CREATE_PENDING_KEY)
    repositoryCreatePending.value = raw
      ? new Map(JSON.parse(raw) as Array<[number, RepositoryPendingEntry]>)
      : new Map()
  } catch {
    repositoryCreatePending.value = new Map()
    sessionStorage.removeItem(REPOSITORY_CREATE_PENDING_KEY)
  }
}

function trackRepositoryCreatePending(
  repositoryId: number,
  taskUuid: string,
  repositoryName: string,
  startedAt = Date.now(),
) {
  if (!repositoryId || !taskUuid) return
  repositoryCreatePending.value.set(repositoryId, {
    taskUuid,
    repositoryName,
    startedAt,
  })
  persistRepositoryCreatePending()
  scheduleRepositoryCreatePoll(0)
}

function scheduleRepositoryCleanupPoll(delay = REPOSITORY_CLEANUP_POLL_MS) {
  if (repositoryCleanupPollTimer != null) window.clearTimeout(repositoryCleanupPollTimer)
  repositoryCleanupPollTimer = null
  if (!mounted || !repositoryCleanupPending.value.size) return
  repositoryCleanupPollTimer = window.setTimeout(() => {
    repositoryCleanupPollTimer = null
    void reconcileRepositoryCleanupTasks()
  }, delay)
}

function scheduleRepositoryCreatePoll(delay = REPOSITORY_CLEANUP_POLL_MS) {
  if (repositoryCreatePollTimer != null) window.clearTimeout(repositoryCreatePollTimer)
  repositoryCreatePollTimer = null
  if (!mounted || !repositoryCreatePending.value.size) return
  repositoryCreatePollTimer = window.setTimeout(() => {
    repositoryCreatePollTimer = null
    void reconcileRepositoryCreateTasks()
  }, delay)
}

async function reconcileRepositoryCleanupTasks() {
  if (repositoryCleanupPollInFlight || !repositoryCleanupPending.value.size) return
  repositoryCleanupPollInFlight = true
  let terminal = false
  try {
    const entries = [...repositoryCleanupPending.value.entries()]
    const tasks = await Promise.allSettled(entries.map(([, pending]) => getTask(pending.taskUuid)))
    tasks.forEach((result, index) => {
      const [repositoryId, pending] = entries[index]
      if (result.status === 'rejected') {
        if (Date.now() - pending.startedAt >= REPOSITORY_CLEANUP_TIMEOUT_MS) {
          repositoryCleanupPending.value.delete(repositoryId)
          ElMessage.error({
            message: `${pending.repositoryName}: ${t('repositoriesPage.cleanupFailed')}`,
            grouping: true,
          })
          terminal = true
        }
        return
      }
      const status = String(result.value.status || '').toLowerCase()
      if (status === 'success') {
        repositoryCleanupPending.value.delete(repositoryId)
        terminal = true
      } else if (['failed', 'cancelled', 'timeout'].includes(status)) {
        repositoryCleanupPending.value.delete(repositoryId)
        ElMessage.error({
          message: result.value.error_message || `${pending.repositoryName}: ${t('repositoriesPage.cleanupFailed')}`,
          grouping: true,
        })
        terminal = true
      }
    })
    persistRepositoryCleanupPending()
    if (terminal) await load()
  } finally {
    repositoryCleanupPollInFlight = false
    scheduleRepositoryCleanupPoll()
  }
}

async function reconcileRepositoryCreateTasks() {
  if (repositoryCreatePollInFlight || !repositoryCreatePending.value.size) return
  repositoryCreatePollInFlight = true
  let terminal = false
  try {
    const entries = [...repositoryCreatePending.value.entries()]
    const tasks = await Promise.allSettled(entries.map(([, pending]) => getTask(pending.taskUuid)))
    tasks.forEach((result, index) => {
      const [repositoryId, pending] = entries[index]
      if (result.status === 'rejected') {
        if (Date.now() - pending.startedAt >= REPOSITORY_CREATE_TIMEOUT_MS) {
          repositoryCreatePending.value.delete(repositoryId)
          ElMessage.error({
            message: `${pending.repositoryName}: ${t('repositoriesPage.createFailed')}`,
            grouping: true,
          })
          terminal = true
        }
        return
      }
      const status = String(result.value.status || '').toLowerCase()
      if (status === 'success') {
        repositoryCreatePending.value.delete(repositoryId)
        ElMessage.success({
          message: `${pending.repositoryName}: ${t('repositoriesPage.createSucceeded')}`,
          grouping: true,
        })
        terminal = true
      } else if (['failed', 'cancelled', 'timeout'].includes(status)) {
        repositoryCreatePending.value.delete(repositoryId)
        ElMessage.error({
          message: result.value.error_message || `${pending.repositoryName}: ${t('repositoriesPage.createFailed')}`,
          grouping: true,
        })
        terminal = true
      }
    })
    persistRepositoryCreatePending()
    if (terminal) await load()
  } finally {
    repositoryCreatePollInFlight = false
    scheduleRepositoryCreatePoll()
  }
}

async function showRepositoryCleanupBlocked(
  blocked: Array<{ row: RepositoryRow; preflight: StorageRepositoryCleanupPreflight }>,
) {
  const rowsWithAssociations = await Promise.all(blocked.map(async ({ row, preflight }) => {
    let associations: StorageRepositoryAssociatedSource[] = []
    let associationCount = preflight.associated_source_count
    if (associationCount > 0) {
      try {
        const result = await listStorageRepositoryAssociatedSources(row.id, { page: 1, page_size: 50 })
        associations = result.results
        associationCount = result.count
      } catch {
        // The authoritative preflight blockers still make the dialog useful.
      }
    }
    return {
      repository: row,
      blockers: preflight.blockers.map((item) => item.detail),
      associations,
      associationCount,
    }
  }))
  repositoryCleanupBlockedRows.value = rowsWithAssociations
  repositoryCleanupBlockedDialogOpen.value = true
}

function s3UrlStyleLabel(style: S3UrlStyle | string | undefined) {
  const normalized = normalizeS3UrlStyle(style ?? undefined)
  if (normalized === 'auto') return t('addS3Repo.s3UrlStyleAuto')
  if (normalized === 'path') return t('addS3Repo.s3UrlStylePath')
  return t('addS3Repo.s3UrlStyleVirtualHosted')
}

function tlsConnectionLabel(useTls: boolean | undefined) {
  return useTls !== false ? t('repositoriesPage.detailTlsEnabled') : t('repositoriesPage.detailTlsDisabled')
}

const DETAIL_EMPTY = '—'

function formatRelativeAgo(iso?: string | null): string {
  if (!iso) return ''
  const d = parseDateTime(iso)
  if (Number.isNaN(d.getTime())) return ''
  const diffMs = Math.max(0, Date.now() - d.getTime())
  const minutes = Math.floor(diffMs / 60000)
  if (minutes < 1) return t('nav.notificationPopover.relative.minutesAgo', { n: 1 })
  if (minutes < 60) return t('nav.notificationPopover.relative.minutesAgo', { n: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t('nav.notificationPopover.relative.hoursAgo', { n: hours })
  const days = Math.floor(hours / 24)
  return t('nav.notificationPopover.relative.daysAgo', { n: days })
}

function formatLastCheckedAt(iso?: string | null): string {
  if (!iso) return DETAIL_EMPTY
  const timeStr = formatAppTime(iso, iso)
  const relative = formatRelativeAgo(iso)
  return relative ? `${timeStr} (${relative})` : timeStr
}

function detailEmptyValueClass(value: string) {
  return value === DETAIL_EMPTY ? 'hfl-detail-row__empty' : ''
}

async function copyDetailText(value: string) {
  const trimmed = (value || '').trim()
  if (!trimmed || trimmed === DETAIL_EMPTY) return
  try {
    await copyTextToClipboard(trimmed)
    ElMessage.success({ message: t('common.copied'), grouping: true })
  } catch {
    ElMessage.error({ message: t('repositoriesPage.copyFailed'), grouping: true })
  }
}

function s3QuotaLimitLabel(row: RepositoryRow) {
  const quotaGb = Number(row.config.quota_gb ?? 0)
  if (quotaGb > 0) return `${quotaGb} GB`
  return t('repositoriesPage.noConfiguredLimit')
}

function storageLimitLabel(row: RepositoryRow) {
  const quotaGb = Number(row.config.quota_gb ?? 0)
  if (quotaGb > 0) return `${quotaGb} GB`
  return t('repositoriesPage.noConfiguredLimit')
}

function quotaMonitoringEnabled(row: RepositoryRow) {
  return Boolean(row.config.quota_alert_enabled)
}

function quotaMonitoringLabel(row: RepositoryRow) {
  if (!quotaMonitoringEnabled(row)) return t('repositoriesPage.disabled')
  return t('repositoriesPage.enabled')
}

function quotaMonitoringThresholdLabel(row: RepositoryRow) {
  if (!quotaMonitoringEnabled(row)) return ''
  const threshold = Number(row.config.quota_alert_threshold ?? 0)
  return threshold > 0 ? `${threshold}%` : ''
}

function s3SecretKeyDisplay(row: RepositoryRow) {
  return (row.config.secret_access_key || '').trim() ? t('repositoriesPage.detailSecretKeyMasked') : DETAIL_EMPTY
}

function normalizeS3PrefixForLocation(prefix?: string) {
  return (prefix || '').trim().replace(/^\/+|\/+$/g, '')
}

function s3RepositoryLocation(row: RepositoryRow) {
  const bucket = (row.config.bucket || '').trim()
  const endpoint = (row.config.endpoint || '').trim()
  const prefix = normalizeS3PrefixForLocation(row.config.prefix)
  if (!bucket && !endpoint) return DETAIL_EMPTY
  const base = bucket && endpoint
    ? `s3://${bucket}@${endpoint}`
    : bucket
      ? `s3://${bucket}`
      : endpoint
  return prefix ? `${base}/${prefix}` : base
}

function normalizeDetailPath(path?: string) {
  return (path || '').trim().replace(/^\/+|\/+$/g, '')
}

function ensureAbsoluteDetailPath(path?: string) {
  const normalized = normalizeDetailPath(path)
  return normalized ? `/${normalized}` : DETAIL_EMPTY
}

function nasRepositoryLocation(row: RepositoryRow) {
  if (row.kind !== 'nas') return DETAIL_EMPTY
  const protocol = row.protocol === 'nfs' ? 'nfs' : 'smb'
  const server = repoLocationPrimary(row).replace(/^\/+|\/+$/g, '')
  const sharedDirectory = normalizeDetailPath(nasRepoShareOrExport(row))
  if (!server || server === DETAIL_EMPTY) return DETAIL_EMPTY
  if (!sharedDirectory || sharedDirectory === DETAIL_EMPTY) return `${protocol}://${server}`
  return `${protocol}://${server}/${sharedDirectory}`
}

function localDiskRepositoryLocation(row: RepositoryRow) {
  if (row.kind !== 'proxy_fs') return DETAIL_EMPTY
  const proxyIp = (proxyNodeIp(row) || '').trim()
  const repositoryPath = (row.config.proxy_node_dir || row.location || '').trim()
  if (!proxyIp && !repositoryPath) return DETAIL_EMPTY
  if (!proxyIp) return repositoryPath
  if (!repositoryPath) return `local://${proxyIp}`
  const normalizedPath = repositoryPath.startsWith('/') ? repositoryPath : `/${repositoryPath}`
  return `local://${proxyIp}${normalizedPath}`
}

function hasBoundProxy(row: RepositoryRow) {
  if (row.kind !== 'nas') return false
  return row.bind_node_type === 'proxy' ||
    Boolean(row.config.proxy_node_id || row.config.proxy_node_name || row.config.proxy_node_ip)
}

function nasBoundProxyNodeId(row: RepositoryRow) {
  if (row.kind !== 'nas') return undefined
  return row.bind_node_id || row.config.proxy_node_id
}

function nasProxyMountSourcePath(row: RepositoryRow) {
  if (row.kind !== 'nas') return DETAIL_EMPTY
  if (row.protocol === 'smb') {
    const server = (row.config.server_address || repoLocationPrimary(row)).trim().replace(/^\/+|\/+$/g, '')
    const share = normalizeDetailPath(row.config.share_path || nasRepoShareOrExport(row))
    if (!server || !share || server === DETAIL_EMPTY || share === DETAIL_EMPTY) return DETAIL_EMPTY
    return `//${server}/${share}`
  }
  const server = (row.config.server_address || row.config.nfs_host || repoLocationPrimary(row)).trim()
  const exportPath = ensureAbsoluteDetailPath(row.config.share_path || row.config.nfs_export)
  if (!server || !exportPath || server === DETAIL_EMPTY || exportPath === DETAIL_EMPTY) return DETAIL_EMPTY
  return `${server}:${exportPath}`
}

function nasProxyMountTargetPath(row: RepositoryRow) {
  if (row.kind !== 'nas') return DETAIL_EMPTY
  const savedPath = (row.config.proxy_mount_path || '').trim()
  if (savedPath && !savedPath.startsWith('/proxy-data/')) return savedPath
  return proxyRepositoryMountPath(row.id, nasBoundProxyNodeId(row)) || savedPath || DETAIL_EMPTY
}

const searchedRows = computed(() => {
  return rows.value
})

const totalFiltered = computed(() => repositoryTotal.value)
const hasSelectedRows = computed(() => selectedRows.value.length > 0)
const selectedEditableRow = computed(() => {
  if (selectedRows.value.length !== 1) return null
  const row = selectedRows.value[0]!
  return row.kind === 's3' || row.kind === 'nas' || row.kind === 'proxy_fs' ? row : null
})
const canEditSelectedRow = computed(() => selectedEditableRow.value !== null)

const createdAtColumnMinWidth = 190

const pagedRows = computed(() => {
  return searchedRows.value
})

const nameColumnLabel = computed(() => t('repositoriesPage.colListName'))

const usageColumnLabel = computed(() =>
  t('repositoriesPage.colRepositoryUsage'),
)
const sourceProxyColumnTip = computed(() =>
  activeTab.value === 'nas'
    ? t('repositoriesPage.targetNasProxyHostColumnTip')
    : t('repositoriesPage.proxyFsProxyHostColumnTip'),
)

const emptyText = computed(() => {
  if (activeTab.value === 's3') return t('repositoriesPage.emptyS3')
  if (activeTab.value === 'nas') return t('repositoriesPage.emptyNas')
  return t('repositoriesPage.emptyProxyFs')
})

const modalTitle = computed(() =>
  modalMode.value === 'edit' ? t('repositoriesPage.modalEditTitle') : t('repositoriesPage.modalAddTitle'),
)

function openDetail(row: RepositoryRow) {
  detailRow.value = row
  detailActiveTab.value = 'basic'
  resetAssociatedSources()
  resetRepositoryTasks()
  drawerDetailOpen.value = true
  nextTick(() => {
    requestAnimationFrame(() => updateDrawerWidth())
  })
}

function onDrawerOpened() {
  bindDrawerResize()
}

function onDrawerClosed() {
  unbindDrawerResize()
  detailActiveTab.value = 'basic'
  resetAssociatedSources()
  resetRepositoryTasks()
  detailRow.value = null
}

function resetRepositoryTasks() {
  repositoryTasks.value = []
  repositoryTasksError.value = ''
  repositoryTasksPage.value = 1
  repositoryTasksPageSize.value = 10
  repositoryTasksCount.value = 0
  resetRepositoryTaskSearch()
  repositoryTaskSearchField.value = 'name'
  repositoryTaskOperation.value = ''
  repositoryTaskStatus.value = ''
  repositoryTaskTimeMode.value = 'all'
  repositoryTaskDateRange.value = null
  repositoryTaskAdvancedFilterOpen.value = false
  repositoryTaskAdvancedFilterDraft.dateRange = null
  lastRepositoryTaskQuickTimeMode.value = 'all'
  cancelRepositoryTaskSearch()
  stopRepositoryTasksPolling()
}

function stopRepositoryTasksPolling() {
  if (repositoryTasksPollTimer) clearInterval(repositoryTasksPollTimer)
  repositoryTasksPollTimer = undefined
}

function updateRepositoryTasksPolling() {
  stopRepositoryTasksPolling()
  if (!repositoryTasks.value.some((task) => task.status === 'pending' || task.status === 'running')) return
  repositoryTasksPollTimer = setInterval(() => {
    if (drawerDetailOpen.value && detailActiveTab.value === 'tasks') void loadRepositoryTasks(false)
  }, 5000)
}

async function loadRepositoryTasks(showLoading = true, row = detailRow.value) {
  if (!row) return
  if (showLoading) repositoryTasksLoading.value = true
  repositoryTasksError.value = ''
  try {
    const timeParams = repositoryTaskCreatedRangeParams()
    const result = await listStorageRepositoryTasks(row.id, {
      search: appliedRepositoryTaskSearch.value.trim() || undefined,
      search_field: repositoryTaskSearchField.value,
      operation_type: repositoryTaskOperation.value || undefined,
      status: repositoryTaskStatus.value || undefined,
      created_after: timeParams.created_after,
      created_before: timeParams.created_before,
      page: repositoryTasksPage.value,
      page_size: repositoryTasksPageSize.value,
    })
    if (detailRow.value?.id !== row.id) return
    repositoryTasks.value = result.results
    repositoryTasksCount.value = result.count
    updateRepositoryTasksPolling()
  } catch (err) {
    repositoryTasksError.value = apiErrorMessage(err, t('repositoriesPage.tasksLoadFailed'))
    repositoryTasks.value = []
    repositoryTasksCount.value = 0
    stopRepositoryTasksPolling()
  } finally {
    repositoryTasksLoading.value = false
  }
}

function applyRepositoryTaskFilters() {
  if (!drawerDetailOpen.value || detailActiveTab.value !== 'tasks') return
  repositoryTasksPage.value = 1
  void loadRepositoryTasks()
}

function repositoryTaskRangeForHours(hours: number): [Date, Date] {
  const end = new Date()
  return [new Date(end.getTime() - hours * 60 * 60 * 1000), end]
}

function repositoryTaskCreatedRangeParams() {
  if (repositoryTaskTimeMode.value === '24h') {
    return { created_after: repositoryTaskRangeForHours(24)[0].toISOString(), created_before: undefined }
  }
  if (repositoryTaskTimeMode.value === '7d') {
    return { created_after: repositoryTaskRangeForHours(7 * 24)[0].toISOString(), created_before: undefined }
  }
  if (repositoryTaskTimeMode.value === '30d') {
    return { created_after: repositoryTaskRangeForHours(30 * 24)[0].toISOString(), created_before: undefined }
  }
  if (repositoryTaskTimeMode.value === 'range' && repositoryTaskDateRange.value) {
    return {
      created_after: repositoryTaskDateRange.value[0].toISOString(),
      created_before: repositoryTaskDateRange.value[1].toISOString(),
    }
  }
  return { created_after: undefined, created_before: undefined }
}

function onRepositoryTaskTimeModeChange(value: string) {
  if (value === 'range') {
    openRepositoryTaskAdvancedFilter()
    return
  }
  lastRepositoryTaskQuickTimeMode.value = value as 'all' | '24h' | '7d' | '30d'
  repositoryTaskDateRange.value = null
  applyRepositoryTaskFilters()
}

function openRepositoryTaskAdvancedFilter() {
  repositoryTaskAdvancedFilterDraft.dateRange = repositoryTaskDateRange.value
  repositoryTaskAdvancedFilterOpen.value = true
}

function resetRepositoryTaskAdvancedFilterDraft() {
  repositoryTaskAdvancedFilterDraft.dateRange = null
}

function applyRepositoryTaskAdvancedFilters() {
  repositoryTaskDateRange.value = repositoryTaskAdvancedFilterDraft.dateRange
  if (repositoryTaskDateRange.value) repositoryTaskTimeMode.value = 'range'
  else if (repositoryTaskTimeMode.value === 'range') repositoryTaskTimeMode.value = lastRepositoryTaskQuickTimeMode.value
  repositoryTaskAdvancedFilterOpen.value = false
  applyRepositoryTaskFilters()
}

function onRepositoryTaskAdvancedFilterClosed() {
  if (repositoryTaskTimeMode.value === 'range' && !repositoryTaskDateRange.value) {
    repositoryTaskTimeMode.value = lastRepositoryTaskQuickTimeMode.value
  }
  repositoryTaskAdvancedFilterDraft.dateRange = repositoryTaskDateRange.value
}

function openRepositoryTaskDetail(task: TaskRow) {
  openRepositoryTaskByUuid(task.task_uuid)
}

function openRepositoryTaskByUuid(taskUuid: string) {
  if (!taskUuid) return
  repositoryTaskDetailUuid.value = taskUuid
  repositoryTaskDetailOpen.value = true
}

function repositoryTaskStatusType(status: string) {
  if (status === 'success') return 'success'
  if (status === 'failed' || status === 'timeout' || status === 'cancelled') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
}

function enumDisplayLabel(value: string) {
  return value
    .trim()
    .split(/[._-]+/)
    .filter(Boolean)
    .map(part => `${part.charAt(0).toUpperCase()}${part.slice(1).toLowerCase()}`)
    .join(' ')
}

function repositoryTaskLabel(scope: 'operation' | 'status' | 'trigger', value?: string | null) {
  if (!value) return DETAIL_EMPTY
  const keys: Record<string, string> = {
    'operation:maintenance.quick': 'repositoriesPage.taskOperationQuick',
    'operation:maintenance.full': 'repositoriesPage.taskOperationFull',
    'operation:cleanup.target': 'repositoriesPage.taskOperationCleanupTarget',
    'operation:cleanup.repository': 'repositoriesPage.taskOperationCleanupRepository',
    'operation:create.repository': 'repositoriesPage.taskOperationCreateRepository',
    'operation:repair.bind': 'repositoriesPage.taskOperationRepairBind',
    'operation:repair.remount': 'repositoriesPage.taskOperationRepairRemount',
    'operation:check': 'repositoriesPage.taskOperationCheck',
    'status:pending': 'repositoriesPage.taskStatusPending',
    'status:running': 'repositoriesPage.taskStatusRunning',
    'status:success': 'repositoriesPage.taskStatusSuccess',
    'status:failed': 'repositoriesPage.taskStatusFailed',
    'status:timeout': 'repositoriesPage.taskStatusTimeout',
    'status:cancelled': 'repositoriesPage.taskStatusCancelled',
    'trigger:system': 'repositoriesPage.taskTriggerSystem',
    'trigger:retry': 'repositoriesPage.taskTriggerRetry',
    'trigger:manual': 'repositoriesPage.taskTriggerManual',
  }
  const key = keys[`${scope}:${value}`] || ''
  if (!key) return enumDisplayLabel(value)
  const translated = t(key)
  return translated === key ? enumDisplayLabel(value) : translated
}

function repositoryTaskProgress(task: TaskRow) {
  const value = Number(task.progress || 0)
  return Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0
}

function resetAssociatedSources() {
  associatedSources.value = []
  associatedSourcesPage.value = 1
  associatedSourcesPageSize.value = 10
  associatedSourcesCount.value = 0
}

async function loadAssociatedSources(row = detailRow.value) {
  if (!row) return
  const page = associatedSourcesPage.value
  const pageSize = associatedSourcesPageSize.value
  associatedSourcesLoading.value = true
  try {
    const result = await listStorageRepositoryAssociatedSources(row.id, {
      page,
      page_size: pageSize,
    })
    if (
      detailRow.value?.id === row.id &&
      associatedSourcesPage.value === page &&
      associatedSourcesPageSize.value === pageSize
    ) {
      associatedSources.value = result.results
      associatedSourcesCount.value = result.count
    }
  } catch (err) {
    if (!pageRequests.isAbortError(err)) {
      ElMessage.error({ message: apiErrorMessage(err, t('repositoriesPage.associatedSourcesLoadFailed')), grouping: true })
    }
  } finally {
    associatedSourcesLoading.value = false
  }
}

function associatedSourceCountForRow(row: RepositoryRow) {
  return rowAssociatedSourceCounts.value[row.id] || 0
}

function proxyBindTipItems(row: RepositoryRow) {
  const count = associatedSourceCountForRow(row)
  if (row.kind === 'nas' && !hasBoundProxy(row) && count > 0) {
    return [
      t('repositoriesPage.proxyBindBlockedByAssociatedSources', { n: count }),
      t('repositoriesPage.proxyBindBlockedByAssociatedSourcesDetail'),
    ]
  }
  return bindProxyLeadItems.value
}

async function loadUnboundNasAssociatedSourceCounts(list: RepositoryRow[], signal?: AbortSignal) {
  const targets = list.filter((row) => row.kind === 'nas' && !hasBoundProxy(row))
  if (!targets.length) {
    rowAssociatedSourceCounts.value = {}
    return
  }
  const entries = await Promise.all(targets.map(async (row) => {
    try {
      const result = await listStorageRepositoryAssociatedSources(row.id, {
        page: 1,
        page_size: 1,
      }, { signal })
      return [row.id, result.count] as const
    } catch {
      return [row.id, 0] as const
    }
  }))
  if (signal?.aborted) return
  rowAssociatedSourceCounts.value = Object.fromEntries(entries)
}

function onDetailTabChange(name: string | number) {
  if (name === 'associated-sources') void loadAssociatedSources()
  if (name === 'tasks') void loadRepositoryTasks()
}

watch([associatedSourcesPage, associatedSourcesPageSize], () => {
  if (drawerDetailOpen.value && detailActiveTab.value === 'associated-sources') {
    void loadAssociatedSources()
  }
})

watch([repositoryTasksPage, repositoryTasksPageSize], () => {
  if (drawerDetailOpen.value && detailActiveTab.value === 'tasks') void loadRepositoryTasks()
})

watch(
  () => [repositoryTaskOperation.value, repositoryTaskStatus.value] as const,
  () => applyRepositoryTaskFilters(),
)


function protocolLabel(protocol?: NasProtocol) {
  if (protocol === 'smb') return t('repositoriesPage.protocolSmb')
  if (protocol === 'nfs') return t('repositoriesPage.protocolNfs')
  return '—'
}

function associatedSourcePlatform(row: StorageRepositoryAssociatedSource): EnrollmentOs {
  const platform = String(row.platform || '').toLowerCase()
  if (platform === 'windows' || platform === 'macos') return platform
  return 'linux'
}

function associatedSourceTypeLabel(row: StorageRepositoryAssociatedSource) {
  return row.source_kind === 'nas'
    ? t('repositoriesPage.associatedSourceTypeNas')
    : t('repositoriesPage.associatedSourceTypeHost')
}

function associatedSourceStatusTagAttrs(status?: string) {
  return lifecycleStatusTagAttrs(status)
}

function associatedSourceStatusLabel(status?: string) {
  const normalized = String(status || '').trim().toLowerCase()
  if (normalized === 'online') return t('repositoriesPage.associatedSourceOnline')
  if (normalized === 'reconnecting') return t('repositoriesPage.associatedSourceReconnecting')
  if (normalized === 'offline') return t('repositoriesPage.associatedSourceOffline')
  return t('repositoriesPage.associatedSourceUnknown')
}

function isDirectNasAssociatedSources(row = detailRow.value) {
  return Boolean(row && row.kind === 'nas' && !hasBoundProxy(row))
}

function associatedSourceProtocol(row: StorageRepositoryAssociatedSource): NasProtocol {
  return row.protocol === 'smb' ? 'smb' : 'nfs'
}

function associatedSourceTraitLabel(row: StorageRepositoryAssociatedSource) {
  if (row.source_kind === 'nas') {
    const protocol = String(row.protocol || '').trim().toLowerCase()
    if (protocol === 'smb' || protocol === 'nfs') return protocolLabel(protocol)
    return DETAIL_EMPTY
  }
  const platform = String(row.platform || '').trim().toLowerCase()
  if (platform === 'windows') return t('protection.sourceResources.osPlatformWindows')
  if (platform === 'macos') return t('protection.sourceResources.osPlatformMacos')
  if (platform === 'linux') return t('protection.sourceResources.osPlatformLinux')
  return DETAIL_EMPTY
}

function associatedSourceHealthLabel(row: StorageRepositoryAssociatedSource) {
  const health = normalizeHealth(String(row.health || ''))
  if (health === 'online') return t('repositoriesPage.associatedNasConnectivityReachable')
  if (health === 'offline') return t('repositoriesPage.associatedNasConnectivityUnreachable')
  if (health === 'unverified') return t('repositoriesPage.associatedNasConnectivityNotChecked')
  return t('repositoriesPage.associatedNasConnectivityUnknown')
}

function associatedSourceHealthTagAttrs(row: StorageRepositoryAssociatedSource) {
  return lifecycleStatusTagAttrs(row.health)
}

function associatedSourceCheckedAt(row: StorageRepositoryAssociatedSource) {
  return formatLastCheckedAt(row.last_checked_at || row.last_success_checked_at || null)
}

function associatedNasMountTarget(row: StorageRepositoryAssociatedSource) {
  return String(row.repository_mount_point || row.mount_point || '').trim() || DETAIL_EMPTY
}

function associatedSourceEndpointRow(row: StorageRepositoryAssociatedSource) {
  const type = row.source_kind === 'nas' ? 'nas' : 'host'
  return {
    type,
    name: row.source_name,
    hostname: row.hostname || '',
    nodeName: row.node_name || '',
    nodeIp: row.node_ip || '',
  }
}

function repoLocationPrimary(row: RepositoryRow) {
  if (row.kind === 's3') return row.location
  if (row.kind === 'proxy_fs') return row.config.proxy_node_dir || row.location
  if (row.protocol === 'smb') {
    const raw = (row.config.smb_server || '').trim()
    const matched = raw.match(/^\/\/([^/]+)(\/.*)?$/)
    return matched?.[1] || raw || '—'
  }
  return row.config.nfs_host || '—'
}



function nasRepoShareOrExport(row: RepositoryRow) {
  if (row.kind !== 'nas') return '—'
  if (row.protocol === 'smb') {
    const raw = (row.config.smb_server || '').trim()
    const matched = raw.match(/^\/\/[^/]+\/(.+)$/)
    if (matched?.[1]) return ensureAbsoluteDetailPath(matched[1])
    const sharePath = row.config.repo_dir || row.config.mount_path || row.location
    return ensureAbsoluteDetailPath(sharePath)
  }
  return ensureAbsoluteDetailPath(row.config.nfs_export)
}

function nasServerValueWithProtocol(row: RepositoryRow) {
  return repoLocationPrimary(row)
}

function nasProtocolTagType(protocol?: NasProtocol) {
  return protocol === 'nfs' ? 'primary' : 'warning'
}

function nasProxyAccessPathLabel(row: RepositoryRow) {
  return hasBoundProxy(row) ? t('repositoriesPage.accessPathProxy') : accessPathLabel(row)
}

function localDiskRepositoryPath(row: RepositoryRow) {
  if (row.kind !== 'proxy_fs') return DETAIL_EMPTY
  return row.config.proxy_node_dir || row.location || DETAIL_EMPTY
}

function localDiskBaseDirectory(row: RepositoryRow) {
  if (row.kind !== 'proxy_fs') return DETAIL_EMPTY
  return row.config.proxy_node_base_dir || DETAIL_EMPTY
}

function localDiskHostingProxyNode(row: RepositoryRow) {
  if (row.kind !== 'proxy_fs') return DETAIL_EMPTY
  return proxyNodeLabel(row) || DETAIL_EMPTY
}

function localDiskRepositoryServerHost(row: RepositoryRow) {
  if (row.kind !== 'proxy_fs') return DETAIL_EMPTY
  return row.config.proxy_repository_server_host || t('repositoriesPage.repositoryServerHostNotConfigured')
}

function nasRepoProxyIp(row: RepositoryRow) {
  if (row.kind !== 'nas') return '—'
  return boundProxyNodeIp(row) || '—'
}

function nasRepoMountPoint(row: RepositoryRow) {
  if (row.kind !== 'nas') return '—'
  return row.config.mount_path || '—'
}

function repoListProtocolLabel(row: RepositoryRow) {
  if (row.kind === 'proxy_fs') return t('repositoriesPage.tabProxyFs')
  return protocolLabel(row.protocol)
}

function repoListSourceProxyIp(row: RepositoryRow) {
  if (row.kind === 'nas') return nasRepoProxyIp(row)
  if (row.kind === 'proxy_fs') return proxyNodeIp(row) || '—'
  return '—'
}

function repoListSourceProxyName(row: RepositoryRow) {
  if (row.kind === 'nas') return boundProxyNodeName(row) || '—'
  if (row.kind === 'proxy_fs') return proxyNodeName(row) || '—'
  return '—'
}

function repoListMountPoint(row: RepositoryRow) {
  if (row.kind === 'nas') return nasRepoMountPoint(row)
  if (row.kind === 'proxy_fs') return row.config.proxy_node_dir || '—'
  return '—'
}

function boundProxyNodeName(row: RepositoryRow) {
  if (row.kind !== 'nas') return ''
  return row.config.proxy_node_name || (row.bind_node_type === 'proxy' ? row.bind_node_name : '') || ''
}

function boundProxyNodeIp(row: RepositoryRow) {
  if (row.kind !== 'nas') return ''
  return row.config.proxy_node_ip || row.config.bind_node_ip || ''
}

function formatNodeDisplay(name?: string, ip?: string) {
  const safeName = (name || '').trim()
  const safeIp = (ip || '').trim()
  if (!safeName) return ''
  return safeIp ? `${safeName}(${safeIp})` : safeName
}

function boundProxyNodeLabel(row: RepositoryRow) {
  return formatNodeDisplay(boundProxyNodeName(row), boundProxyNodeIp(row))
}

function proxyNodeName(row: RepositoryRow) {
  if (row.kind !== 'proxy_fs') return ''
  return row.config.proxy_node_name || row.source_node_name || ''
}

function proxyNodeIp(row: RepositoryRow) {
  if (row.kind !== 'proxy_fs') return ''
  return row.config.proxy_node_ip || ''
}

function proxyNodeLabel(row: RepositoryRow) {
  return formatNodeDisplay(proxyNodeName(row), proxyNodeIp(row))
}



function accessPathLabel(row: RepositoryRow) {
  if (row.bind_node_type === 'agent' || row.repo_scope === 'private') return t('repositoriesPage.accessPathAgent')
  if (row.bind_node_type === 'proxy' || row.repo_scope === 'shared') return t('repositoriesPage.accessPathProxy')
  return '—'
}


function s3UsageParts(row: RepositoryRow) {
  return repositoryCapacityParts(row)
}

function repoUsageParts(row: RepositoryRow) {
  return repositoryCapacityParts(row)
}

function backingStorageParts(row: RepositoryRow) {
  return repositoryStorageParts(row)
}

function backingStorageAvailableLabel(row: RepositoryRow) {
  const storage = backingStorageParts(row)
  if (storage.total <= 0) return DETAIL_EMPTY
  return t('repositoriesPage.storageAvailableValue', {
    available: fmtBytes(storage.available),
    total: fmtBytes(storage.total),
  })
}

function repoUsageProbeFailed(row: RepositoryRow) {
  return String(row.usage_probe_status || 'pending') === 'failed'
}

function repoCapacityProbeFailed(row: RepositoryRow) {
  return String(row.capacity_probe_status || 'pending') === 'failed'
}

function hasS3UsageData(row: RepositoryRow) {
  const { total, used } = s3UsageParts(row)
  return total > 0 || used > 0
}

function hasRepositoryUsageData(row: RepositoryRow) {
  const { total, used } = repoUsageParts(row)
  return total > 0 || used > 0
}

function applySearch() {
  selectedRows.value = []
  tableRef.value?.clearSelection()
  if (currentPage.value !== 1) {
    currentPage.value = 1
    return
  }
  void load()
}

watch(
  () => route.query.tab,
  (tab) => {
    const nextTab = normalizeTabParam(tab)
    activeTab.value = nextTab
    formKind.value = nextTab
  },
  { immediate: true },
)

watch(
  () => [route.query.tab, route.query.focus, repositoryListLoaded.value] as const,
  () => {
    if (!repositoryListLoaded.value) return
    const focusId = Number(route.query.focus || 0)
    if (!focusId) return
    const target = (rows.value || []).find((row) => Number(row.id) === focusId)
    if (target) {
      openDetail(target)
      if (typeof window !== 'undefined' && window.history && window.history.replaceState) {
        const url = new URL(window.location.href)
        url.searchParams.delete('focus')
        window.history.replaceState({}, '', url.toString())
      }
    }
  },
  { immediate: true },
)

watch(activeTab, (tab) => {
  resetSearch()
  searchField.value = 'name'
  currentPage.value = 1
  moreActionsOpen.value = false
  tableRef.value?.clearSelection()
  if (route.query.tab !== tab) {
    router.replace({
      path: route.path,
      query: {
        ...route.query,
        tab,
      },
    })
  }
  if (mounted) {
    repositoryListLoaded.value = false
    load()
  }
  requestAnimationFrame(layoutTable)
})

watch(repositoryTableLoading, (loading) => {
  if (!loading) layoutTable()
})

watch(pageSize, () => {
  if (currentPage.value !== 1) {
    currentPage.value = 1
    return
  }
  if (mounted) void load()
})

watch(currentPage, () => {
  if (mounted) void load()
})

function onSelectionChange(sel: RepositoryRow[]) {
  selectedRows.value = sel
  if (!sel.length) moreActionsOpen.value = false
}

function resetForm() {
  form.value = {
    source_node_id: undefined,
    name: '',
    bucket: '',
    region: '',
    endpoint: '',
    prefix: DEFAULT_S3_OBJECT_PREFIX,
    access_key_id: '',
    secret_access_key: '',
    s3_url_style: 'auto',
    use_tls: true,
    mount_path: '',
  }
}

async function ensureSourceNodes(signal?: AbortSignal) {
  if (sourceNodes.value.length > 0) return
  sourceNodes.value = await listAllNodes(undefined, { signal }).catch((e) => {
    if (pageRequests.isAbortError(e)) throw e
    return []
  })
}

function goEditS3(row: RepositoryRow) {
  drawerDetailOpen.value = false
  router.push({ path: `/node/repositories/s3/${row.id}/edit` })
}

function goEditProxyFs(row: RepositoryRow) {
  drawerDetailOpen.value = false
  router.push({ path: `/node/repositories/proxy-fs/${row.id}/edit` })
}

function goRepairNas(row: RepositoryRow) {
  drawerDetailOpen.value = false
  router.push({ path: `/node/repositories/nas/${row.id}/repair` })
}

function validateForm(kind: RepoKind): boolean {
  if (kind === 's3') {
    if (!form.value.name.trim()) {
      ElMessage.warning({ message: t('repositoriesPage.errName'), grouping: true })
      return false
    }
    if (!form.value.bucket.trim()) {
      ElMessage.warning({ message: t('repositoriesPage.errBucket'), grouping: true })
      return false
    }
    if (!form.value.access_key_id.trim() || (modalMode.value === 'add' && !form.value.secret_access_key.trim())) {
      ElMessage.warning({ message: t('repositoriesPage.errKeys'), grouping: true })
      return false
    }
  } else {
    if (!form.value.name.trim()) {
      ElMessage.warning({ message: t('repositoriesPage.errName'), grouping: true })
      return false
    }
    if (!form.value.mount_path.trim()) {
      ElMessage.warning({ message: t('repositoriesPage.errPath'), grouping: true })
      return false
    }
  }
  return true
}

function buildRepositoryPayload(kind: RepoKind) {
  const name = form.value.name.trim()
  if (kind === 's3') {
    const payload: Record<string, unknown> = {
      name,
      repo_type: 's3',
      s3_platform: 'custom',
      s3_bucket: form.value.bucket.trim(),
      config: {
        region: form.value.region.trim() || undefined,
        endpoint: form.value.endpoint.trim() || undefined,
        prefix: form.value.prefix.trim() || undefined,
        access_key_id: form.value.access_key_id.trim(),
        s3_url_style: form.value.s3_url_style,
        use_tls: form.value.use_tls,
      },
    }
    const secret = form.value.secret_access_key.trim()
    if (secret) {
      const config = payload.config as Record<string, unknown>
      config.secret_access_key = secret
    }
    return payload
  }

  if (kind === 'proxy_fs') {
    return {
      name,
      repo_type: 'proxy_fs',
      bind_node_type: 'proxy',
      bind_node_id: form.value.source_node_id,
      config: {
        proxy_node_dir: form.value.mount_path.trim(),
      },
    }
  }

  return {
    name,
    repo_type: 'nas',
    nas_protocol: 'nfs',
    config: {
      server_address: form.value.mount_path.trim(),
      share_path: form.value.mount_path.trim(),
    },
  }
}

async function submitModal() {
  const kind = formKind.value
  if (!validateForm(kind)) return

  busy.value = true
  try {
    if (modalMode.value === 'edit' && editingId.value != null) {
      await api(`/api/v1/storage/repositories/${editingId.value}/`, {
        method: 'PATCH',
        body: JSON.stringify(buildRepositoryPayload(kind)),
      })
      ElMessage.success({ message: t('repositoriesPage.msgUpdated'), grouping: true })
    } else {
      const created = await createStorageRepository(buildRepositoryPayload(kind) as never)
      const accepted = String(created.status || '').toLowerCase() === 'creating'
      ElMessage.success({
        message: t(accepted ? 'repositoriesPage.msgCreateAccepted' : 'repositoriesPage.msgCreated'),
        grouping: true,
      })
      if (accepted && created.active_create_task?.task_uuid) {
        trackRepositoryCreatePending(created.id, created.active_create_task.task_uuid, created.name)
      }
    }
    modalOpen.value = false
    resetForm()
    await load()
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err), grouping: true })
  } finally {
    busy.value = false
  }
}

async function batchDeleteSelected() {
  const sel = selectedRows.value
  if (!sel.length) {
    ElMessage.warning({ message: t('repositoriesPage.batchDeleteNeedSelect'), grouping: true })
    return
  }
  busy.value = true
  try {
    forceDeleteRepository.value = false
    normalDeletePreflight.value = null
    forceDeletePreflight.value = null
    if (sel.length === 1) {
      const row = sel[0]
      const [normalPreflight, forcePreflight] = await Promise.all([
        preflightStorageRepositoryCleanup(row.id, false),
        preflightStorageRepositoryCleanup(row.id, true),
      ])
      if (!normalPreflight.allowed && !forcePreflight.allowed) {
        await showRepositoryCleanupBlocked([{ row, preflight: normalPreflight }])
        return
      }
      normalDeletePreflight.value = normalPreflight
      forceDeletePreflight.value = forcePreflight
      pendingDeleteRepositories.value = [row]
      deleteRepositoriesDialogOpen.value = true
      return
    }

    const preflights = await Promise.all(
      sel.map(async (row) => ({
        row,
        preflight: await preflightStorageRepositoryCleanup(row.id, false),
      })),
    )
    const blocked = preflights.filter(({ preflight }) => !preflight.allowed)
    if (blocked.length) {
      await showRepositoryCleanupBlocked(blocked)
      return
    }
    pendingDeleteRepositories.value = preflights.map(({ row }) => row)
    deleteRepositoriesDialogOpen.value = true
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err), grouping: true })
  } finally {
    busy.value = false
  }
}

async function confirmDeleteRepositories() {
  const sel = pendingDeleteRepositories.value
  if (!sel.length) return
  const force = canForceDeleteRepository.value && forceDeleteRepository.value
  if (sel.length === 1 && !activeDeletePreflight.value?.allowed) return
  busy.value = true
  try {
    const results = await Promise.allSettled(
      sel.map((row) => deleteStorageRepository(row.id, force)),
    )
    const accepted = results.filter((result) => result.status === 'fulfilled').length
    const failed = results.length - accepted
    tableRef.value?.clearSelection()
    pendingDeleteRepositories.value = []
    deleteRepositoriesDialogOpen.value = false
    forceDeleteRepository.value = false
    normalDeletePreflight.value = null
    forceDeletePreflight.value = null
    if (accepted) {
      ElMessage.success({
        message: t('repositoriesPage.cleanupAcceptedCount', { accepted, failed }),
        grouping: true,
      })
    }
    results.forEach((result, index) => {
      if (result.status === 'fulfilled') {
        const taskUuid = String(result.value.task_uuid || '')
        if (taskUuid) {
          repositoryCleanupPending.value.set(sel[index].id, {
            taskUuid,
            repositoryName: sel[index].name,
            startedAt: Date.now(),
          })
        }
      }
      if (result.status === 'rejected') {
        ElMessage.error({ message: apiErrorMessage(result.reason), grouping: true })
      }
    })
    persistRepositoryCleanupPending()
    scheduleRepositoryCleanupPoll(0)
    await load()
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err), grouping: true })
  } finally {
    busy.value = false
  }
}

function closeDeleteRepositoriesDialog() {
  if (busy.value) return
  deleteRepositoriesDialogOpen.value = false
  pendingDeleteRepositories.value = []
  forceDeleteRepository.value = false
  normalDeletePreflight.value = null
  forceDeletePreflight.value = null
}

function editFirstSelected() {
  const row = selectedEditableRow.value
  if (!row) {
    ElMessage.warning({ message: t('repositoriesPage.editNeedSelect'), grouping: true })
    return
  }
  if (row.kind === 's3') {
    goEditS3(row)
    return
  }
  if (row.kind === 'nas') {
    goRepairNas(row)
    return
  }
  if (row.kind === 'proxy_fs') {
    goEditProxyFs(row)
  }
}

function onMoreCommand(cmd: string) {
  if (cmd === 'batch-delete') {
    if (!hasSelectedRows.value) return
    batchDeleteSelected()
  } else if (cmd === 'edit-selected') {
    if (!canEditSelectedRow.value) return
    editFirstSelected()
  }
}

async function refreshRepositories() {
  await load()
}

async function load() {
  const repoType = activeTab.value
  const signal = pageRequests.nextSignal('repository-list')
  busy.value = true
  repositoryListError.value = null
  try {
    await ensureSourceNodes(signal)
    if (signal.aborted) return
    const list = await listStorageRepositories({
      repo_type: repoType,
      page: currentPage.value,
      page_size: pageSize.value,
      search: appliedSearchQuery.value.trim() || undefined,
      search_field: appliedSearchQuery.value.trim() ? searchField.value : undefined,
    }, {
      signal,
    })
    for (const repository of list.results) {
      const activeCleanup = repository.active_cleanup_task
      if (activeCleanup?.task_uuid) {
        repositoryCleanupPending.value.set(repository.id, {
          taskUuid: activeCleanup.task_uuid,
          repositoryName: repository.name,
          startedAt: activeCleanup.created_at ? Date.parse(activeCleanup.created_at) || Date.now() : Date.now(),
        })
      }
      const activeCreate = repository.active_create_task
      if (activeCreate?.task_uuid) {
        repositoryCreatePending.value.set(repository.id, {
          taskUuid: activeCreate.task_uuid,
          repositoryName: repository.name,
          startedAt: activeCreate.created_at ? Date.parse(activeCreate.created_at) || Date.now() : Date.now(),
        })
      }
    }
    persistRepositoryCleanupPending()
    persistRepositoryCreatePending()
    scheduleRepositoryCleanupPoll()
    scheduleRepositoryCreatePoll()
    const mappedRows = list.results.map(mapApiToRow)
    rows.value = mappedRows
    repositoryTotal.value = list.count
    await loadUnboundNasAssociatedSourceCounts(mappedRows, signal)
  } catch (err) {
    if (pageRequests.isAbortError(err)) return
    rows.value = []
    repositoryTotal.value = 0
    rowAssociatedSourceCounts.value = {}
    repositoryListError.value = apiErrorMessage(err)
    ElMessage.error({ message: repositoryListError.value, grouping: true })
  } finally {
    pageRequests.releaseSignal('repository-list', signal)
    if (!signal.aborted) {
      repositoryListLoaded.value = true
      busy.value = false
    }
  }
}

onMounted(() => {
  mounted = true
  restoreRepositoryCleanupPending()
  restoreRepositoryCreatePending()
  const pendingCreateId = Number(route.query.pending_create_id || 0)
  const pendingCreateTask = String(route.query.pending_create_task || '').trim()
  if (pendingCreateId > 0 && pendingCreateTask) {
    trackRepositoryCreatePending(pendingCreateId, pendingCreateTask, t('repositoriesPage.statusCreating'))
    const nextQuery = { ...route.query }
    delete nextQuery.pending_create_id
    delete nextQuery.pending_create_task
    void router.replace({ path: route.path, query: nextQuery })
  }
  void load()
  scheduleRepositoryCleanupPoll(0)
  scheduleRepositoryCreatePoll(0)
})

onBeforeUnmount(() => {
  mounted = false
  if (repositoryCleanupPollTimer != null) window.clearTimeout(repositoryCleanupPollTimer)
  repositoryCleanupPollTimer = null
  if (repositoryCreatePollTimer != null) window.clearTimeout(repositoryCreatePollTimer)
  repositoryCreatePollTimer = null
  unbindDrawerResize()
  stopRepositoryTasksPolling()
})

function lifecycleTagType(s: RepoLifecycleStatus | string) {
  switch (normalizeLifecycleStatus(String(s))) {
    case 'created':
      return 'success'
    case 'creating':
    case 'removing':
      return 'info'
    case 'create_failed':
    case 'remove_failed':
      return 'danger'
    case 'removed':
      return 'info'
    default:
      return 'info'
  }
}

function healthTagType(h: RepoHealth | string) {
  const k = normalizeHealth(String(h))
  if (k === 'online') return 'success'
  if (k === 'unverified') return 'warning'
  return 'danger'
}

function s3RegionCellText(row: RepositoryRow) {
  return (row.config.region || '').trim() || '—'
}

function s3BucketCell(row: RepositoryRow) {
  if (row.kind !== 's3') return '—'
  return (row.config.bucket || '').trim() || '—'
}

function s3ObjectPrefixCell(row: RepositoryRow) {
  if (row.kind !== 's3') return '—'
  return (row.config.prefix || '').trim() || '—'
}

</script>

<template>
  <ModulePage :menus="nodeMenus" :page-title-override="pageTitle" body-fill>
    <div class="hfl-list-shell hfl-list-shell--fill">
      <div class="hfl-list-panel hfl-list-panel--fill">
        <div class="hfl-list-toolbar">
          <ElButton type="primary" @click="activeTab === 'nas' ? router.push('/node/repositories/nas/add') : activeTab === 'proxy_fs' ? router.push('/node/repositories/proxy-fs/add') : router.push('/node/repositories/s3/add')">
            <Plus :size="16" />
            {{ t('repositoriesPage.btnAdd') }}
          </ElButton>
          <ElDropdown trigger="click" @command="onMoreCommand" @visible-change="moreActionsOpen = $event">
            <ElButton>
              {{ t('repositoriesPage.moreActions') }}
              <ChevronDown
                :size="16"
                class="hfl-list-more__chev"
                :class="{ 'hfl-list-more__chev--open': moreActionsOpen }"
              />
            </ElButton>
            <template #dropdown>
              <ElDropdownMenu>
                <ElDropdownItem command="edit-selected" :disabled="!canEditSelectedRow">
                  <span class="el-dropdown-menu__item-content">
                    <Pencil :size="14" class="shrink-0" />
                    <span>{{ t('repositoriesPage.btnEdit') }}</span>
                  </span>
                </ElDropdownItem>
                <ElDropdownItem command="batch-delete" divided class="el-dropdown-menu__item--danger" :disabled="!hasSelectedRows">
                  <span class="el-dropdown-menu__item-content">
                    <Trash2 :size="14" class="shrink-0" />
                    <span>{{ t('repositoriesPage.batchDelete') }}</span>
                  </span>
                </ElDropdownItem>
              </ElDropdownMenu>
            </template>
          </ElDropdown>

          <div class="hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split">
            <ElInput
              v-model="searchQuery"
              clearable
              size="small"
              class="hfl-list-search hfl-list-search-group"
              :placeholder="listSearchPlaceholder"
              @keyup.enter="runFilterSearch"
              @clear="clearSearch"
            >
              <template #prepend>
                <ElSelect v-model="searchField" @change="handleSearchFieldChange">
                  <ElOption
                    v-for="option in searchFieldOptions"
                    :key="option.value"
                    :value="option.value"
                    :label="option.label"
                  />
                </ElSelect>
              </template>
              <template #prefix>
                <Search :size="16" class="hfl-list-search__icon" />
              </template>
            </ElInput>
            
            <div class="hfl-list-toolbar__utility">
              <ElButton
                class="hfl-refresh-button"
                :title="t('repositoriesPage.refresh')"
                :aria-label="t('repositoriesPage.refresh')"
                :disabled="busy"
                @click="refreshRepositories()"
              >
                <RefreshCw :size="16" :class="{ 'is-spinning': busy }" />
              </ElButton>
            </div>
          </div>
        </div>

        <div ref="tableBlockRef" class="hfl-list-table-block">
          <el-table
            v-table-overflow-title
            v-table-header-scroll-sync
            v-table-column-resize="`repositories.${activeTab}.list`"
            ref="tableRef"
            v-loading="repositoryTableLoading"
            class="hfl-list-table"
            :data="pagedRows"
            stripe
            row-key="id"
            :max-height="tableMaxHeight"
            :header-cell-style="TABLE_HEADER_STYLE"
            @scroll="onTableScroll"
            @selection-change="onSelectionChange"
          >
            <el-table-column type="selection" width="35" fixed="left" reserve-selection />
            <el-table-column
              prop="name"
              :label="nameColumnLabel"
              min-width="200"
              fixed="left"
              class-name="hfl-table-name-col"
              header-cell-class-name="repo-th-split-1"
            >
              <template #default="{ row }">
                <button
                  type="button"
                  class="hfl-table-name-link hfl-table-name-link--full"
                  @click="openDetail(row)"
                >
                  {{ row.name }}
                </button>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('repositoriesPage.colStatus')"
              :width="activeTab === 'proxy_fs' ? 148 : activeTab === 's3' || activeTab === 'nas' ? 134 : 112"
            >
              <template #default="{ row }">
                <div class="hfl-table-no-tooltip">
                  <ElTag :type="lifecycleTagType(row.status)" size="small">{{ repoLifecycleLabel(row.status) }}</ElTag>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              v-if="activeTab === 's3'"
              :label="t('repositoriesPage.colPlatform')"
              :min-width="180"
            >
              <template #default="{ row }">
                <div class="source-os-cell source-os-cell--compact source-os-cell--repo-platform hfl-table-no-tooltip">
                  <span class="source-os-cell__icon-wrap">
                    <S3PlatformBrandIcon
                      :platform="row.s3_platform"
                      :size="14"
                      :alt="t(s3PlatformLabelKey(row.s3_platform))"
                      icon-class="source-os-cell__s3-icon"
                      lucide-class="source-os-cell__s3-icon-lucide"
                    />
                  </span>
                  <span class="source-os-cell__platform">{{ t(s3PlatformLabelKey(row.s3_platform)) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              v-if="activeTab === 's3'"
              :label="t('repositoriesPage.colRegion')"
              min-width="120"
            >
              <template #default="{ row }">
                <span :class="{ 'hfl-empty-mark': s3RegionCellText(row) === DETAIL_EMPTY }">
                  {{ s3RegionCellText(row) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column
              v-if="activeTab === 's3'"
              :label="t('repositoriesPage.colEndpoint')"
              min-width="200"
            >
              <template #default="{ row }">
                <span :class="{ 'hfl-empty-mark': s3EndpointDisplay(row.config.endpoint) === DETAIL_EMPTY }">{{ s3EndpointDisplay(row.config.endpoint) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              v-if="activeTab === 's3'"
              :label="t('repositoriesPage.colS3Bucket')"
              min-width="160"
            >
              <template #default="{ row }">
                <span :class="{ 'hfl-empty-mark': s3BucketCell(row) === DETAIL_EMPTY }">{{ s3BucketCell(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              v-if="activeTab === 's3'"
              :label="t('repositoriesPage.colS3ObjectPrefix')"
              min-width="126"
            >
              <template #default="{ row }">
                <span :class="{ 'hfl-empty-mark': s3ObjectPrefixCell(row) === DETAIL_EMPTY }">{{ s3ObjectPrefixCell(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              v-if="activeTab === 'nas'"
              :label="t('repositoriesPage.colProtocol')"
              width="132"
            >
              <template #default="{ row }">
                <span
                  v-if="row.protocol || activeTab === 'proxy_fs'"
                  class="repo-protocol-pill"
                  :class="`repo-protocol-pill--${activeTab === 'proxy_fs' ? 'proxy-fs' : row.protocol}`"
                >
                  <HardDrive v-if="activeTab === 'proxy_fs'" :size="12" stroke-width="2.25" />
                  <component v-else :is="nasMountProtocolIcon(row.protocol)" :size="12" stroke-width="2.25" />
                  {{ activeTab === 'proxy_fs' ? repoListProtocolLabel(row) : protocolLabel(row.protocol) }}
                </span>
                <span v-else class="hfl-empty-mark">—</span>
              </template>
            </el-table-column>
            <el-table-column
              v-if="activeTab === 'nas'"
              :label="t('repositoriesPage.colLocation')"
              min-width="260"
            >
              <template #default="{ row }">
                <span class="hfl-table-mono" :class="{ 'hfl-empty-mark': nasRepositoryLocation(row) === DETAIL_EMPTY }">{{ nasRepositoryLocation(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              v-if="activeTab === 'nas' || activeTab === 'proxy_fs'"
              :label="t('repositoriesPage.colSourceProxyIp')"
              :min-width="activeTab === 'proxy_fs' ? 144 : 173"
            >
              <template #header>
                <span class="repo-table-header-with-tip">
                  <span>{{ t('repositoriesPage.colSourceProxyIp') }}</span>
                  <HflHelpTip
                    :content="sourceProxyColumnTip"
                    :aria-label="t('repositoriesPage.colSourceProxyIpHelp')"
                  />
                </span>
              </template>
              <template #default="{ row }">
                <ElTooltip
                  v-if="row.kind === 'nas' && !hasBoundProxy(row)"
                  placement="top-start"
                  :show-after="250"
                  popper-class="repo-proxy-bind-tip-popper"
                >
                  <template #content>
                    <ol class="repo-proxy-bind-tip">
                      <li
                        v-for="(item, index) in proxyBindTipItems(row)"
                        :key="item"
                        class="repo-proxy-bind-tip__item"
                      >
                        <span class="repo-proxy-bind-tip__index">{{ index + 1 }}</span>
                        <span class="repo-proxy-bind-tip__text">{{ item }}</span>
                      </li>
                    </ol>
                  </template>
                  <span class="repo-proxy-unbound-cell hfl-table-no-tooltip" tabindex="0">
                    <span class="repo-proxy-unbound-cell__text">
                      <span class="repo-proxy-unbound-cell__primary">
                        <Unlink :size="14" stroke-width="2.2" aria-hidden="true" />
                        <span>{{ t('repositoriesPage.proxyNotBound') }}</span>
                      </span>
                      <span class="repo-proxy-unbound-cell__secondary">{{ t('addNasRepo.accessPathDirect') }}</span>
                    </span>
                  </span>
                </ElTooltip>
                <div v-else class="table-stack-cell">
                  <span class="table-stack-cell__primary" :class="{ 'hfl-empty-mark': repoListSourceProxyName(row) === DETAIL_EMPTY }">{{ repoListSourceProxyName(row) }}</span>
                  <span v-if="repoListSourceProxyIp(row)" class="table-stack-cell__secondary">{{ repoListSourceProxyIp(row) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              v-if="activeTab === 'proxy_fs'"
              :label="t('repositoriesPage.colRepoPath')"
              min-width="220"
            >
              <template #default="{ row }">
                <span :class="{ 'hfl-empty-mark': repoListMountPoint(row) === DETAIL_EMPTY }">{{ repoListMountPoint(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="usageColumnLabel"
              :min-width="activeTab === 's3' ? 170 : activeTab === 'nas' ? 190 : 198"
            >
              <template #default="{ row }">
                <template v-if="activeTab === 's3'">
                  <HflCapacityCell
                    v-if="s3UsageParts(row).total > 0"
                    :used-bytes="s3UsageParts(row).used"
                    :total-bytes="s3UsageParts(row).total"
                    variant="compact"
                    :format-bytes="fmtBytes"
                  />
                  <HflCapacityCell
                    v-else
                    :used-bytes="s3UsageParts(row).used"
                    :total-bytes="0"
                    :unlimited-total-label="t('repositoriesPage.noConfiguredLimit')"
                    variant="compact"
                    :format-bytes="fmtBytes"
                    :show-bar="false"
                    :show-percent="false"
                  />
                </template>
                <template v-else-if="activeTab === 'nas' || activeTab === 'proxy_fs'">
                  <span v-if="repoUsageProbeFailed(row)">
                    {{ t('repositoriesPage.usageUnavailable') }} / {{ fmtBytes(repoUsageParts(row).total) }}
                  </span>
                  <HflCapacityCell
                    v-else-if="repoUsageParts(row).total > 0"
                    :used-bytes="repoUsageParts(row).used"
                    :total-bytes="repoUsageParts(row).total"
                    variant="compact"
                    :format-bytes="fmtBytes"
                  />
                  <HflCapacityCell
                    v-else
                    :used-bytes="repoUsageParts(row).used"
                    :total-bytes="0"
                    :unlimited-total-label="t('repositoriesPage.noConfiguredLimit')"
                    variant="compact"
                    :format-bytes="fmtBytes"
                    :show-bar="false"
                    :show-percent="false"
                  />
                </template>
              </template>
            </el-table-column>
            <el-table-column
              v-if="activeTab === 'nas' || activeTab === 'proxy_fs'"
              :label="t('repositoriesPage.colBackingStorage')"
              min-width="178"
            >
              <template #default="{ row }">
                <span v-if="repoCapacityProbeFailed(row)" class="hfl-empty-mark">
                  {{ t('repositoriesPage.capacityUnavailable') }}
                </span>
                <span v-else-if="row.capacity_probe_status === 'pending'" class="hfl-empty-mark">
                  {{ t('repositoriesPage.metricsPending') }}
                </span>
                <div v-else class="table-stack-cell">
                  <span class="table-stack-cell__primary" :class="{ 'hfl-empty-mark': backingStorageAvailableLabel(row) === DETAIL_EMPTY }">
                    {{ backingStorageAvailableLabel(row) }}
                  </span>
                  <span v-if="row.storage_mount_point" class="table-stack-cell__secondary">
                    {{ row.storage_mount_point }}
                  </span>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('repositoriesPage.colAvailability')"
              width="116"
            >
              <template #default="{ row }">
                <div class="hfl-table-no-tooltip">
                  <ElTag :type="healthTagType(row.health)" size="small">{{ repoHealthLabel(row.health) }}</ElTag>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              prop="created_at"
              :label="activeTab === 's3' || activeTab === 'nas' || activeTab === 'proxy_fs' ? t('repositoriesPage.colRegistered') : t('repositoriesPage.colCreated')"
              :min-width="activeTab === 'nas' ? 163 : activeTab === 's3' || activeTab === 'proxy_fs' ? 170 : createdAtColumnMinWidth"
            >
              <template #default="{ row }">
                <span class="hfl-table-cell-time" :class="{ 'hfl-empty-mark': !row.created_at }">{{ formatLocalDateTime(row.created_at) }}</span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty v-if="repositoryListError" :description="repositoryListError" :image-size="80">
                <ElButton type="primary" @click="load()">{{ t('repositoriesPage.retry') }}</ElButton>
              </el-empty>
              <el-empty v-else :description="emptyText" :image-size="80" />
            </template>
          </el-table>
          </div>

          <div class="hfl-list-footer">
            <span v-if="selectedRows.length > 0" class="hfl-list-footer__selected">
              {{ t('repositoriesPage.selectedCount', { n: selectedRows.length }) }}
            </span>
            <HflPagination
              class="hfl-list-footer__pagination"
              v-model:current-page="currentPage"
              v-model:page-size="pageSize"
              :total="totalFiltered"
            />
          </div>
      </div>
    </div>
    <ElDrawer
      v-model="drawerDetailOpen"
      direction="rtl"
      destroy-on-close
      :modal="true"
      :size="drawerSize"
      class="hfl-detail-drawer"
      @opened="onDrawerOpened"
      @closed="onDrawerClosed"
    >
      <template #header>
        <span class="hfl-detail-drawer__title">{{ detailRow?.name || '—' }}</span>
      </template>

      <div v-if="detailRow" class="hfl-detail-drawer__body">
        <ElTabs v-model="detailActiveTab" class="hfl-detail-tabs" @tab-change="onDetailTabChange">
          <ElTabPane :label="t('protection.sourceResources.detailTabBasic')" name="basic">
        <div v-if="detailRow.kind === 's3'" class="hfl-detail-sections repo-storage-detail">
              <section class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">{{ t('repositoriesPage.stepRepo') }}</h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('addS3Repo.fieldRepoName') }}</span>
                    <span class="hfl-detail-row__value repo-detail__inline-value">
                      <span class="repo-detail__name-summary">
                        <span class="hfl-detail-row__text">{{ detailRow.name }}</span>
                      </span>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.colStatus') }}</span>
                    <span class="hfl-detail-row__value">
                      <ElTag :type="lifecycleTagType(detailRow.status)" size="small">
                        {{ repoLifecycleLabel(detailRow.status) }}
                      </ElTag>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.colAvailability') }}</span>
                    <span class="hfl-detail-row__value">
                      <ElTag :type="healthTagType(detailRow.health)" size="small">
                        {{ repoHealthLabel(detailRow.health) }}
                      </ElTag>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.colRegistered') }}</span>
                    <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__empty': !detailRow.created_at }">{{ formatLocalDateTime(detailRow.created_at) }}</span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldPlatform') }}</span>
                    <span class="hfl-detail-row__value">
                      <div class="source-os-cell source-os-cell--compact">
                        <span class="source-os-cell__icon-wrap">
                          <S3PlatformBrandIcon
                            :platform="detailRow.s3_platform"
                            :size="20"
                            :alt="t(s3PlatformLabelKey(detailRow.s3_platform))"
                            icon-class="source-os-cell__s3-icon"
                            lucide-class="source-os-cell__s3-icon-lucide"
                          />
                        </span>
                        <span class="source-os-cell__platform">{{ t(s3PlatformLabelKey(detailRow.s3_platform)) }}</span>
                      </div>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('addS3Repo.fieldPrefix') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--mono">
                      <span class="hfl-detail-row__text" :class="detailEmptyValueClass(detailRow.config.prefix || DETAIL_EMPTY)">{{ detailRow.config.prefix || DETAIL_EMPTY }}</span>
                    </span>
                  </div>
                  <div class="hfl-detail-row hfl-detail-row--full">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldLocation') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--editable hfl-detail-row__value--mono">
                      <span class="hfl-detail-row__text" :class="detailEmptyValueClass(s3RepositoryLocation(detailRow))">{{ s3RepositoryLocation(detailRow) }}</span>
                      <ElButton
                        v-if="s3RepositoryLocation(detailRow) !== DETAIL_EMPTY"
                        text
                        circle
                        size="small"
                        class="hfl-detail-row__edit"
                        :title="t('common.copy')"
                        @click="copyDetailText(s3RepositoryLocation(detailRow))"
                      >
                        <Copy :size="13" />
                      </ElButton>
                    </span>
                  </div>
                </div>
              </section>

              <section class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">{{ t('repositoriesPage.detailSectionConnectionAuth') }}</h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('addS3Repo.fieldEndpoint') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--mono">
                      <span class="hfl-detail-row__text" :class="detailEmptyValueClass(s3EndpointDisplay(detailRow.config.endpoint) || DETAIL_EMPTY)">{{ s3EndpointDisplay(detailRow.config.endpoint) || DETAIL_EMPTY }}</span>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('addS3Repo.fieldRegion') }}</span>
                    <span class="hfl-detail-row__value">
                      <span class="hfl-detail-row__text" :class="detailEmptyValueClass(detailRow.config.region || DETAIL_EMPTY)">{{ detailRow.config.region || DETAIL_EMPTY }}</span>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('addS3Repo.fieldAccessKey') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--editable hfl-detail-row__value--mono">
                      <span class="hfl-detail-row__text" :class="detailEmptyValueClass(detailRow.config.access_key_id || DETAIL_EMPTY)">{{ detailRow.config.access_key_id || DETAIL_EMPTY }}</span>
                      <ElButton
                        v-if="detailRow.config.access_key_id"
                        text
                        circle
                        size="small"
                        class="hfl-detail-row__edit"
                        :title="t('common.copy')"
                        @click="copyDetailText(detailRow.config.access_key_id || '')"
                      >
                        <Copy :size="13" />
                      </ElButton>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('addS3Repo.fieldSecretKey') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--mono" :class="detailEmptyValueClass(s3SecretKeyDisplay(detailRow))">{{ s3SecretKeyDisplay(detailRow) }}</span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('addS3Repo.fieldS3UrlStyle') }}</span>
                    <span class="hfl-detail-row__value">
                      <span class="hfl-detail-row__text">
                        {{ s3UrlStyleLabel(detailRow.config.s3_url_style) }}
                      </span>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('addS3Repo.fieldUseTls') }}</span>
                    <span class="hfl-detail-row__value">
                      <HflBooleanStatusTag
                        :value="detailRow.config.use_tls !== false"
                        :label="tlsConnectionLabel(detailRow.config.use_tls)"
                      />
                    </span>
                  </div>
                  <template v-if="detailDistinctS3Endpoints">
                    <div class="hfl-detail-row">
                      <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldExternalEndpoint') }}</span>
                      <span class="hfl-detail-row__value hfl-detail-row__value--mono">
                        <span class="hfl-detail-row__text">{{ detailDistinctS3Endpoints.external }}</span>
                      </span>
                    </div>
                    <div class="hfl-detail-row">
                      <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldInternalEndpoint') }}</span>
                      <span class="hfl-detail-row__value hfl-detail-row__value--mono">
                        <span class="hfl-detail-row__text">{{ detailDistinctS3Endpoints.internal }}</span>
                      </span>
                    </div>
                  </template>
                  <div class="hfl-detail-row hfl-detail-row--full">
                    <span class="hfl-detail-row__label">{{ t('addS3Repo.fieldBucket') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--mono" :class="detailEmptyValueClass(detailRow.config.bucket || DETAIL_EMPTY)">{{ detailRow.config.bucket || DETAIL_EMPTY }}</span>
                  </div>
                </div>
              </section>

              <section class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">{{ t('repositoriesPage.detailSectionCapacityUsage') }}</h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldRepositoryUsage') }}</span>
                    <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__value--stacked': hasS3UsageData(detailRow) }">
                      <HflCapacityCell
                        v-if="s3UsageParts(detailRow).total > 0"
                        :used-bytes="s3UsageParts(detailRow).used"
                        :total-bytes="s3UsageParts(detailRow).total"
                        variant="compact"
                        :format-bytes="fmtBytes"
                        :empty-label="DETAIL_EMPTY"
                      />
                      <HflCapacityCell
                        v-else-if="s3UsageParts(detailRow).used > 0"
                        :used-bytes="s3UsageParts(detailRow).used"
                        :total-bytes="0"
                        :unlimited-total-label="t('repositoriesPage.noConfiguredLimit')"
                        variant="compact"
                        :format-bytes="fmtBytes"
                        :show-bar="false"
                        :show-percent="false"
                        :empty-label="DETAIL_EMPTY"
                      />
                      <span v-else class="hfl-detail-row__text hfl-detail-row__empty">{{ DETAIL_EMPTY }}</span>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldLastChecked') }}</span>
                    <span class="hfl-detail-row__value" :class="detailEmptyValueClass(formatLastCheckedAt(detailRow.last_checked_at))">{{ formatLastCheckedAt(detailRow.last_checked_at) }}</span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('addS3Repo.fieldQuota') }}</span>
                    <span class="hfl-detail-row__value" :class="detailEmptyValueClass(s3QuotaLimitLabel(detailRow))">{{ s3QuotaLimitLabel(detailRow) }}</span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('addS3Repo.fieldQuotaAlert') }}</span>
                    <span class="hfl-detail-row__value repo-detail__tag-group">
                      <HflBooleanStatusTag
                        :value="quotaMonitoringEnabled(detailRow)"
                        :label="quotaMonitoringLabel(detailRow)"
                      />
                      <ElTag v-if="quotaMonitoringThresholdLabel(detailRow)" type="info" size="small" effect="plain">
                        {{ quotaMonitoringThresholdLabel(detailRow) }}
                      </ElTag>
                    </span>
                  </div>
                </div>
              </section>
            </div>

            <div v-else-if="detailRow.kind === 'nas'" class="hfl-detail-sections repo-storage-detail">
              <section class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">{{ t('repositoriesPage.stepRepo') }}</h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldName') }}</span>
                    <span class="hfl-detail-row__value repo-detail__inline-value">
                      <span class="repo-detail__name-summary">
                        <span class="hfl-detail-row__text">{{ detailRow.name }}</span>
                      </span>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.colStatus') }}</span>
                    <span class="hfl-detail-row__value">
                      <ElTag :type="lifecycleTagType(detailRow.status)" size="small">
                        {{ repoLifecycleLabel(detailRow.status) }}
                      </ElTag>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.colAvailability') }}</span>
                    <span class="hfl-detail-row__value">
                      <ElTag :type="healthTagType(detailRow.health)" size="small">
                        {{ repoHealthLabel(detailRow.health) }}
                      </ElTag>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.colRegistered') }}</span>
                    <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__empty': !detailRow.created_at }">{{ formatLocalDateTime(detailRow.created_at) }}</span>
                  </div>
                  <div class="hfl-detail-row hfl-detail-row--full">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldLocation') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--editable hfl-detail-row__value--mono">
                      <span class="hfl-detail-row__text">{{ nasRepositoryLocation(detailRow) }}</span>
                      <ElButton
                        v-if="nasRepositoryLocation(detailRow) !== DETAIL_EMPTY"
                        text
                        circle
                        size="small"
                        class="hfl-detail-row__edit"
                        :title="t('common.copy')"
                        @click="copyDetailText(nasRepositoryLocation(detailRow))"
                      >
                        <Copy :size="13" />
                      </ElButton>
                    </span>
                  </div>
                </div>
              </section>

              <section class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">{{ t('repositoriesPage.detailSectionConnectionAuth') }}</h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">
                      {{ detailRow.protocol === 'smb' ? t('addNasRepo.fieldSmbHost') : t('addNasRepo.fieldNfsHost') }}
                    </span>
                    <span class="hfl-detail-row__value repo-detail__inline-value">
                      <span class="hfl-detail-row__text hfl-detail-row__value--mono" :class="detailEmptyValueClass(nasServerValueWithProtocol(detailRow))">{{ nasServerValueWithProtocol(detailRow) }}</span>
                      <ElTag :type="nasProtocolTagType(detailRow.protocol)" size="small" effect="plain">
                        <span class="hfl-detail-protocol-tag">
                          <component :is="nasMountProtocolIcon(detailRow.protocol)" :size="12" stroke-width="2.25" />
                          {{ protocolLabel(detailRow.protocol) }}
                        </span>
                      </ElTag>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">
                      {{ detailRow.protocol === 'smb' ? t('addNasRepo.fieldSmbShare') : t('addNasRepo.fieldNfsExport') }}
                    </span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--mono" :class="detailEmptyValueClass(nasRepoShareOrExport(detailRow))">{{ nasRepoShareOrExport(detailRow) }}</span>
                  </div>
                  <template v-if="detailRow.protocol === 'smb'">
                    <div class="hfl-detail-row">
                      <span class="hfl-detail-row__label">{{ t('repositoriesPage.fieldSmbUsername') }}</span>
                      <span class="hfl-detail-row__value" :class="detailEmptyValueClass(detailRow.config.smb_username || DETAIL_EMPTY)">{{ detailRow.config.smb_username || DETAIL_EMPTY }}</span>
                    </div>
                    <div class="hfl-detail-row">
                      <span class="hfl-detail-row__label">{{ t('repositoriesPage.fieldSmbDomain') }}</span>
                      <span class="hfl-detail-row__value" :class="detailEmptyValueClass(detailRow.config.smb_domain || DETAIL_EMPTY)">{{ detailRow.config.smb_domain || DETAIL_EMPTY }}</span>
                    </div>
                  </template>
                  <div class="hfl-detail-row hfl-detail-row--full">
                    <span class="hfl-detail-row__label">{{ t('addNasRepo.fieldMountOptions') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--mono" :class="detailEmptyValueClass(detailRow.config.nfs_options || DETAIL_EMPTY)">{{ detailRow.config.nfs_options || DETAIL_EMPTY }}</span>
                  </div>
                </div>
              </section>

              <section v-if="hasBoundProxy(detailRow)" class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">{{ t('repositoriesPage.detailSectionProxyAccessMount') }}</h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.colBoundProxyNode') }}</span>
                    <span class="hfl-detail-row__value">{{ boundProxyNodeLabel(detailRow) || t('addNasRepo.notBoundProxy') }}</span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldAccessPath') }}</span>
                    <span class="hfl-detail-row__value">{{ nasProxyAccessPathLabel(detailRow) }}</span>
                  </div>
                  <div class="hfl-detail-row hfl-detail-row--full">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldNasMountMapping') }}</span>
                    <span class="hfl-detail-row__value repo-detail__mount-map">
                      <span class="repo-detail__mount-tag repo-detail__mount-tag--source">
                        {{ nasProxyMountSourcePath(detailRow) }}
                      </span>
                      <span class="repo-detail__mount-arrow">{{ t('repositoriesPage.detailMountedTo') }}</span>
                      <span class="repo-detail__mount-tag repo-detail__mount-tag--target">
                        {{ nasProxyMountTargetPath(detailRow) }}
                      </span>
                    </span>
                  </div>
                </div>
              </section>

              <section class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">{{ t('repositoriesPage.detailSectionCapacityUsage') }}</h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldRepositoryUsage') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="{
                        'hfl-detail-row__value--stacked':
                          repoUsageProbeFailed(detailRow) ||
                          hasRepositoryUsageData(detailRow),
                        'hfl-detail-row__value--metric-state':
                          repoUsageProbeFailed(detailRow),
                      }"
                    >
                      <span v-if="repoUsageProbeFailed(detailRow)" class="hfl-detail-row__text">
                        {{ t('repositoriesPage.usageUnavailable') }} / {{ fmtBytes(repoUsageParts(detailRow).total) }}
                      </span>
                      <HflCapacityCell
                        v-else-if="repoUsageParts(detailRow).total > 0"
                        :used-bytes="repoUsageParts(detailRow).used"
                        :total-bytes="repoUsageParts(detailRow).total"
                        variant="compact"
                        :format-bytes="fmtBytes"
                        :empty-label="DETAIL_EMPTY"
                      />
                      <HflCapacityCell
                        v-else
                        :used-bytes="repoUsageParts(detailRow).used"
                        :total-bytes="0"
                        :unlimited-total-label="t('repositoriesPage.noConfiguredLimit')"
                        variant="compact"
                        :format-bytes="fmtBytes"
                        :show-bar="false"
                        :show-percent="false"
                        :empty-label="DETAIL_EMPTY"
                      />
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldLastChecked') }}</span>
                    <span class="hfl-detail-row__value" :class="detailEmptyValueClass(formatLastCheckedAt(detailRow.last_checked_at))">{{ formatLastCheckedAt(detailRow.last_checked_at) }}</span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldBackingStorage') }}</span>
                    <span v-if="repoCapacityProbeFailed(detailRow)" class="hfl-detail-row__value hfl-detail-row__empty">
                      {{ t('repositoriesPage.capacityUnavailable') }}
                    </span>
                    <span v-else class="hfl-detail-row__value" :class="detailEmptyValueClass(backingStorageAvailableLabel(detailRow))">
                      {{ backingStorageAvailableLabel(detailRow) }}
                    </span>
                  </div>
                  <div v-if="backingStorageParts(detailRow).total > 0" class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldPhysicalUsage') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                      <HflCapacityCell
                        :used-bytes="backingStorageParts(detailRow).used"
                        :total-bytes="backingStorageParts(detailRow).total"
                        variant="compact"
                        :format-bytes="fmtBytes"
                      />
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('addS3Repo.fieldQuota') }}</span>
                    <span class="hfl-detail-row__value" :class="detailEmptyValueClass(storageLimitLabel(detailRow))">{{ storageLimitLabel(detailRow) }}</span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('addS3Repo.fieldQuotaAlert') }}</span>
                    <span class="hfl-detail-row__value repo-detail__tag-group">
                      <HflBooleanStatusTag
                        :value="quotaMonitoringEnabled(detailRow)"
                        :label="quotaMonitoringLabel(detailRow)"
                      />
                      <ElTag v-if="quotaMonitoringThresholdLabel(detailRow)" type="info" size="small" effect="plain">
                        {{ quotaMonitoringThresholdLabel(detailRow) }}
                      </ElTag>
                    </span>
                  </div>
                </div>
              </section>
            </div>

            <div v-else class="hfl-detail-sections repo-storage-detail">
              <section class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">{{ t('repositoriesPage.stepRepo') }}</h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldName') }}</span>
                    <span class="hfl-detail-row__value repo-detail__inline-value">
                      <span class="repo-detail__name-summary">
                        <span class="hfl-detail-row__text">{{ detailRow.name }}</span>
                      </span>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.colStatus') }}</span>
                    <span class="hfl-detail-row__value">
                      <ElTag :type="lifecycleTagType(detailRow.status)" size="small">
                        {{ repoLifecycleLabel(detailRow.status) }}
                      </ElTag>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.colAvailability') }}</span>
                    <span class="hfl-detail-row__value">
                      <ElTag :type="healthTagType(detailRow.health)" size="small">
                        {{ repoHealthLabel(detailRow.health) }}
                      </ElTag>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.colRegistered') }}</span>
                    <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__empty': !detailRow.created_at }">{{ formatLocalDateTime(detailRow.created_at) }}</span>
                  </div>
                  <div class="hfl-detail-row hfl-detail-row--full">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldLocation') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--editable hfl-detail-row__value--mono">
                      <span class="hfl-detail-row__text">{{ localDiskRepositoryLocation(detailRow) }}</span>
                      <ElButton
                        v-if="localDiskRepositoryLocation(detailRow) !== DETAIL_EMPTY"
                        text
                        circle
                        size="small"
                        class="hfl-detail-row__edit"
                        :title="t('common.copy')"
                        @click="copyDetailText(localDiskRepositoryLocation(detailRow))"
                      >
                        <Copy :size="13" />
                      </ElButton>
                    </span>
                  </div>
                </div>
              </section>

              <section class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">{{ t('repositoriesPage.detailSectionRepoConfig') }}</h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldHostingProxyNode') }}</span>
                    <span class="hfl-detail-row__value" :class="detailEmptyValueClass(localDiskHostingProxyNode(detailRow))">{{ localDiskHostingProxyNode(detailRow) }}</span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.fieldProxyNodeDir') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--mono" :class="detailEmptyValueClass(localDiskRepositoryPath(detailRow))">{{ localDiskRepositoryPath(detailRow) }}</span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.fieldProxyNodeBaseDir') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--mono" :class="detailEmptyValueClass(localDiskBaseDirectory(detailRow))">{{ localDiskBaseDirectory(detailRow) }}</span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.fieldRepositoryServerHost') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--mono">{{ localDiskRepositoryServerHost(detailRow) }}</span>
                  </div>
                </div>
              </section>

              <section class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">{{ t('repositoriesPage.detailSectionCapacityUsage') }}</h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldRepositoryUsage') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="{
                        'hfl-detail-row__value--stacked':
                          repoUsageProbeFailed(detailRow) ||
                          hasRepositoryUsageData(detailRow),
                        'hfl-detail-row__value--metric-state':
                          repoUsageProbeFailed(detailRow),
                      }"
                    >
                      <span v-if="repoUsageProbeFailed(detailRow)" class="hfl-detail-row__text">
                        {{ t('repositoriesPage.usageUnavailable') }} / {{ fmtBytes(repoUsageParts(detailRow).total) }}
                      </span>
                      <HflCapacityCell
                        v-else-if="repoUsageParts(detailRow).total > 0"
                        :used-bytes="repoUsageParts(detailRow).used"
                        :total-bytes="repoUsageParts(detailRow).total"
                        variant="compact"
                        :format-bytes="fmtBytes"
                        :empty-label="DETAIL_EMPTY"
                      />
                      <HflCapacityCell
                        v-else
                        :used-bytes="repoUsageParts(detailRow).used"
                        :total-bytes="0"
                        :unlimited-total-label="t('repositoriesPage.noConfiguredLimit')"
                        variant="compact"
                        :format-bytes="fmtBytes"
                        :show-bar="false"
                        :show-percent="false"
                        :empty-label="DETAIL_EMPTY"
                      />
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldLastChecked') }}</span>
                    <span class="hfl-detail-row__value" :class="detailEmptyValueClass(formatLastCheckedAt(detailRow.last_checked_at))">{{ formatLastCheckedAt(detailRow.last_checked_at) }}</span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldBackingStorage') }}</span>
                    <span v-if="repoCapacityProbeFailed(detailRow)" class="hfl-detail-row__value hfl-detail-row__empty">
                      {{ t('repositoriesPage.capacityUnavailable') }}
                    </span>
                    <span v-else class="hfl-detail-row__value" :class="detailEmptyValueClass(backingStorageAvailableLabel(detailRow))">
                      {{ backingStorageAvailableLabel(detailRow) }}
                    </span>
                  </div>
                  <div v-if="backingStorageParts(detailRow).total > 0" class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.detailFieldPhysicalUsage') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                      <HflCapacityCell
                        :used-bytes="backingStorageParts(detailRow).used"
                        :total-bytes="backingStorageParts(detailRow).total"
                        variant="compact"
                        :format-bytes="fmtBytes"
                      />
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.fieldQuota') }}</span>
                    <span class="hfl-detail-row__value" :class="detailEmptyValueClass(storageLimitLabel(detailRow))">{{ storageLimitLabel(detailRow) }}</span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('repositoriesPage.fieldQuotaAlert') }}</span>
                    <span class="hfl-detail-row__value repo-detail__tag-group">
                      <HflBooleanStatusTag
                        :value="quotaMonitoringEnabled(detailRow)"
                        :label="quotaMonitoringLabel(detailRow)"
                      />
                      <ElTag v-if="quotaMonitoringThresholdLabel(detailRow)" type="info" size="small" effect="plain">
                        {{ quotaMonitoringThresholdLabel(detailRow) }}
                      </ElTag>
                    </span>
                  </div>
                </div>
              </section>
            </div>
          </ElTabPane>
          <ElTabPane
            :label="t('repositoriesPage.detailTabAssociatedSources')"
            name="associated-sources"
          >
            <div class="repo-associated-sources">
              <ElTable
                v-loading="associatedSourcesLoading"
                :data="associatedSources"
                stripe
                :empty-text="t('repositoriesPage.associatedSourcesEmpty')"
                class="hfl-list-table repo-associated-sources__table"
              >
                <ElTableColumn
                  :label="t('repositoriesPage.associatedSourceColId')"
                  :width="isDirectNasAssociatedSources() ? 80 : 90"
                >
                  <template #default="{ row }">
                    <span class="hfl-table-mono">{{ row.source_ref_id }}</span>
                  </template>
                </ElTableColumn>
                <ElTableColumn
                  :label="t('repositoriesPage.associatedSourceColSource')"
                  :min-width="isDirectNasAssociatedSources() ? 160 : 260"
                >
                  <template #default="{ row }">
                    <div class="repo-associated-source-cell">
                      <div class="repo-associated-source-cell__body">
                        <div class="repo-associated-source-cell__head">
                          <span class="repo-associated-source-cell__name">{{ row.source_name }}</span>
                        </div>
                        <div class="repo-associated-source-cell__meta">
                          <ElTag
                            v-if="isDirectNasAssociatedSources()"
                            size="small"
                            v-bind="associatedSourceStatusTagAttrs(row.availability)"
                          >
                            {{ associatedSourceStatusLabel(row.availability) }}
                          </ElTag>
                          <ElTag
                            size="small"
                            effect="plain"
                          >
                            {{ associatedSourceTypeLabel(row) }}
                          </ElTag>
                          <ElTooltip
                            v-if="associatedSourceTraitLabel(row) !== DETAIL_EMPTY"
                            :content="associatedSourceTraitLabel(row)"
                            placement="top"
                          >
                            <span
                              v-if="row.source_kind !== 'nas'"
                              class="source-os-cell__icon-wrap repo-associated-source-cell__trait-icon"
                            >
                              <AgentPlatformBrandIcon :os="associatedSourcePlatform(row)" />
                            </span>
                            <span
                              v-else
                              class="repo-protocol-pill repo-protocol-pill--icon-only repo-associated-source-cell__trait-icon"
                              :class="`repo-protocol-pill--${associatedSourceProtocol(row)}`"
                            >
                              <component
                                :is="nasMountProtocolIcon(associatedSourceProtocol(row))"
                                :size="12"
                                stroke-width="2.25"
                              />
                            </span>
                          </ElTooltip>
                          <span v-else class="hfl-empty-mark">
                            {{ DETAIL_EMPTY }}
                          </span>
                        </div>
                      </div>
                    </div>
                  </template>
                </ElTableColumn>
                <ElTableColumn
                  :label="t('repositoriesPage.associatedSourceColEndpoint')"
                  :min-width="isDirectNasAssociatedSources() ? 180 : 220"
                >
                  <template #default="{ row }">
                    <FlowSourceConnectionCell :row="associatedSourceEndpointRow(row)" />
                  </template>
                </ElTableColumn>
                <ElTableColumn
                  v-if="isDirectNasAssociatedSources()"
                  :label="t('repositoriesPage.associatedSourceColMountPoint')"
                  min-width="441"
                >
                  <template #default="{ row }">
                    <div class="repo-associated-mount-point-cell">
                      <Folder
                        :size="16"
                        :stroke-width="2"
                        class="repo-associated-mount-point-cell__icon"
                      />
                      <ElTooltip
                        :content="associatedNasMountTarget(row)"
                        :disabled="associatedNasMountTarget(row) === DETAIL_EMPTY"
                        placement="top-start"
                      >
                        <span class="repo-associated-mount-point-cell__path hfl-table-mono">
                          {{ associatedNasMountTarget(row) }}
                        </span>
                      </ElTooltip>
                    </div>
                  </template>
                </ElTableColumn>
                <ElTableColumn
                  v-if="isDirectNasAssociatedSources()"
                  :label="t('repositoriesPage.associatedSourceColNasConnectivity')"
                  min-width="129"
                >
                  <template #default="{ row }">
                    <div class="repo-associated-health-cell">
                      <ElTag size="small" v-bind="associatedSourceHealthTagAttrs(row)">
                        {{ associatedSourceHealthLabel(row) }}
                      </ElTag>
                      <span class="repo-associated-health-cell__time" :class="{ 'hfl-empty-mark': associatedSourceCheckedAt(row) === DETAIL_EMPTY }">{{ associatedSourceCheckedAt(row) }}</span>
                      <ElTooltip
                        v-if="row.last_error"
                        :content="row.last_error"
                        placement="top"
                      >
                        <span class="repo-associated-health-cell__error">{{ row.last_error }}</span>
                      </ElTooltip>
                    </div>
                  </template>
                </ElTableColumn>
                <ElTableColumn
                  v-else
                  :label="t('repositoriesPage.associatedSourceColAvailability')"
                  min-width="130"
                >
                  <template #default="{ row }">
                    <ElTag size="small" v-bind="associatedSourceStatusTagAttrs(row.availability)">
                      {{ associatedSourceStatusLabel(row.availability) }}
                    </ElTag>
                  </template>
                </ElTableColumn>
                <ElTableColumn
                  v-if="!isDirectNasAssociatedSources()"
                  :label="t('protection.sourceResources.colRegisteredAt')"
                  min-width="170"
                >
                  <template #default="{ row }">
                    <span class="hfl-table-cell-time" :class="{ 'hfl-empty-mark': !row.registered_at }">{{ formatLocalDateTime(row.registered_at) }}</span>
                  </template>
                </ElTableColumn>
              </ElTable>
              <div
                v-if="associatedSourcesCount > associatedSourcesPageSize || associatedSources.length > 0"
                class="repo-associated-sources__footer"
              >
                <HflPagination
                  v-model:current-page="associatedSourcesPage"
                  v-model:page-size="associatedSourcesPageSize"
                  :total="associatedSourcesCount"
                  @update:page-size="associatedSourcesPage = 1"
                />
              </div>
            </div>
          </ElTabPane>
          <ElTabPane :label="t('repositoriesPage.detailTabTasks')" name="tasks">
            <div class="repo-tasks">
              <div class="hfl-list-toolbar repo-tasks__filters">
                <ElInput
                  v-model="repositoryTaskSearch"
                  clearable
                  :placeholder="t('repositoriesPage.tasksSearchPlaceholder')"
                  class="repo-tasks__search"
                  @keyup.enter="runRepositoryTaskSearchNow"
                  @clear="clearRepositoryTaskSearch"
                >
                  <template #prepend>
                    <ElSelect v-model="repositoryTaskSearchField" @change="handleRepositoryTaskSearchFieldChange">
                      <ElOption
                        v-for="option in repositoryTaskSearchFieldOptions"
                        :key="option.value"
                        :value="option.value"
                        :label="option.label"
                      />
                    </ElSelect>
                  </template>
                  <template #prefix><Search :size="15" /></template>
                </ElInput>
                <ElSelect v-model="repositoryTaskOperation" clearable :placeholder="t('repositoriesPage.tasksOperation')">
                  <ElOption :label="t('repositoriesPage.taskOperationQuick')" value="maintenance.quick" />
                  <ElOption :label="t('repositoriesPage.taskOperationFull')" value="maintenance.full" />
                  <ElOption :label="t('repositoriesPage.taskOperationCleanupTarget')" value="cleanup.target" />
                  <ElOption :label="t('repositoriesPage.taskOperationCleanupRepository')" value="cleanup.repository" />
                  <ElOption :label="t('repositoriesPage.taskOperationCheck')" value="check" />
                </ElSelect>
                <ElSelect v-model="repositoryTaskStatus" clearable :placeholder="t('repositoriesPage.tasksStatus')">
                  <ElOption :label="t('repositoriesPage.taskStatusPending')" value="pending" />
                  <ElOption :label="t('repositoriesPage.taskStatusRunning')" value="running" />
                  <ElOption :label="t('repositoriesPage.taskStatusSuccess')" value="success" />
                  <ElOption :label="t('repositoriesPage.taskStatusFailed')" value="failed" />
                  <ElOption :label="t('repositoriesPage.taskStatusTimeout')" value="timeout" />
                </ElSelect>
                <ElSelect
                  v-model="repositoryTaskTimeMode"
                  :placeholder="t('ops.task.filterTime')"
                  @change="onRepositoryTaskTimeModeChange"
                >
                  <ElOption
                    v-for="option in repositoryTaskTimeModeOptions"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </ElSelect>
                <div class="hfl-list-toolbar__right">
                  <ElBadge
                    :value="repositoryTaskAdvancedFilterCount"
                    :hidden="repositoryTaskAdvancedFilterCount === 0"
                    class="repo-tasks__filter-badge"
                  >
                    <ElButton
                      :class="{ 'repo-tasks__filter-button--active': repositoryTaskAdvancedFilterCount > 0 }"
                      :title="t('ops.task.advancedFilter')"
                      :aria-label="t('ops.task.advancedFilter')"
                      @click="openRepositoryTaskAdvancedFilter"
                    >
                      <Filter :size="16" />
                    </ElButton>
                  </ElBadge>
                  <ElButton
                    class="hfl-refresh-button"
                    :title="t('repositoriesPage.refresh')"
                    :aria-label="t('repositoriesPage.refresh')"
                    :disabled="repositoryTasksLoading"
                    @click="loadRepositoryTasks"
                  >
                    <RefreshCw :size="16" :class="{ 'is-spinning': repositoryTasksLoading }" />
                  </ElButton>
                </div>
              </div>
              <ElAlert
                v-if="repositoryTasksError"
                type="error"
                :title="repositoryTasksError"
                :closable="false"
                show-icon
                class="repo-tasks__error"
              />
              <ElTable
                v-loading="repositoryTasksLoading"
                :data="repositoryTasks"
                stripe
                :empty-text="t('repositoriesPage.tasksEmpty')"
                class="hfl-list-table repo-tasks__table"
                @row-click="openRepositoryTaskDetail"
              >
                <ElTableColumn :label="t('repositoriesPage.tasksName')" min-width="250">
                  <template #default="{ row }">
                    <div class="repo-task-name">
                      <span>{{ row.display_name }}</span>
                      <code>{{ row.task_uuid }}</code>
                    </div>
                  </template>
                </ElTableColumn>
                <ElTableColumn :label="t('repositoriesPage.tasksOperation')" min-width="150">
                  <template #default="{ row }">{{ repositoryTaskLabel('operation', row.operation_type) }}</template>
                </ElTableColumn>
                <ElTableColumn :label="t('repositoriesPage.tasksStatus')" width="120">
                  <template #default="{ row }">
                    <ElTag size="small" effect="plain" :type="repositoryTaskStatusType(row.status)">
                      {{ repositoryTaskLabel('status', row.status) }}
                    </ElTag>
                  </template>
                </ElTableColumn>
                <ElTableColumn :label="t('repositoriesPage.tasksProgress')" min-width="150">
                  <template #default="{ row }">
                    <ElProgress :percentage="repositoryTaskProgress(row)" :stroke-width="7" />
                  </template>
                </ElTableColumn>
                <ElTableColumn :label="t('repositoriesPage.tasksTrigger')" min-width="130">
                  <template #default="{ row }">
                    <ElTag type="info" size="small">
                      {{ repositoryTaskLabel('trigger', row.trigger_type) }}
                    </ElTag>
                  </template>
                </ElTableColumn>
                <ElTableColumn :label="t('repositoriesPage.tasksCreated')" width="180">
                  <template #default="{ row }"><span :class="{ 'hfl-empty-mark': !row.created_at }">{{ formatLocalDateTime(row.created_at) }}</span></template>
                </ElTableColumn>
              </ElTable>
              <div v-if="repositoryTasksCount > repositoryTasksPageSize || repositoryTasks.length" class="repo-tasks__footer">
                <HflPagination
                  v-model:current-page="repositoryTasksPage"
                  v-model:page-size="repositoryTasksPageSize"
                  :total="repositoryTasksCount"
                  :page-sizes="[10, 20, 30, 50, 100]"
                  @update:page-size="repositoryTasksPage = 1"
                />
              </div>
            </div>
          </ElTabPane>
        </ElTabs>
      </div>
    </ElDrawer>

    <ElDrawer
      v-model="repositoryTaskAdvancedFilterOpen"
      :title="t('ops.task.advancedFilter')"
      :size="nestedDrawerSize"
      class="repo-tasks__filter-drawer"
      @closed="onRepositoryTaskAdvancedFilterClosed"
    >
      <ElForm label-position="top">
        <ElFormItem :label="t('ops.task.filterTime')">
          <ElDatePicker
            v-model="repositoryTaskAdvancedFilterDraft.dateRange"
            type="datetimerange"
            :shortcuts="repositoryTaskDateRangeShortcuts"
            :start-placeholder="t('ops.task.startTime')"
            :end-placeholder="t('ops.task.endTime')"
            class="repo-tasks__date"
          />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <div class="repo-tasks__filter-drawer-footer">
          <ElButton @click="resetRepositoryTaskAdvancedFilterDraft">{{ t('ops.task.resetFilter') }}</ElButton>
          <ElButton type="primary" @click="applyRepositoryTaskAdvancedFilters">{{ t('ops.task.applyFilter') }}</ElButton>
        </div>
      </template>
    </ElDrawer>

    <TaskDetailDrawer
      v-model="repositoryTaskDetailOpen"
      :task-uuid="repositoryTaskDetailUuid"
      :drawer-size="repositoryTaskDetailDrawerSize"
      resource-list-mode="target_repositories"
      @open-task="openRepositoryTaskByUuid"
      @task-updated="loadRepositoryTasks(false)"
    />

    <Modal :open="modalOpen" :title="modalTitle" @close="modalOpen = false; resetForm()">
      <ElAlert type="info" :closable="false" class="mb-4" show-icon>
        {{ modalMode === 'add' ? t('repositoriesPage.modalLead') : t('repositoriesPage.modalEditLead') }}
      </ElAlert>
      <ElForm label-position="top">
        <template v-if="formKind === 's3'">
          <ElFormItem :label="t('repositoriesPage.fieldAccessKey')" required>
            <ElInput v-model="form.access_key_id" :placeholder="t('repositoriesPage.phAccessKey')" />
          </ElFormItem>
          <ElFormItem :label="t('repositoriesPage.fieldSecretKey')" required>
            <ElInput v-model="form.secret_access_key" type="password" show-password :placeholder="t('repositoriesPage.phSecretKey')" />
          </ElFormItem>
          <ElFormItem :label="t('repositoriesPage.fieldRegion')">
            <ElInput v-model="form.region" :placeholder="t('repositoriesPage.phRegion')" />
          </ElFormItem>
          <ElFormItem :label="t('repositoriesPage.fieldEndpoint')">
            <ElInput v-model="form.endpoint" :placeholder="t('repositoriesPage.phEndpoint')" />
          </ElFormItem>
          <ElFormItem :label="t('repositoriesPage.fieldS3UrlStyle')">
            <ElSelect v-model="form.s3_url_style" style="width: 100%">
              <ElOption value="auto" :label="t('repositoriesPage.s3UrlStyleAuto')" />
              <ElOption value="virtual_hosted" :label="t('repositoriesPage.s3UrlStyleVirtualHosted')" />
              <ElOption value="path" :label="t('repositoriesPage.s3UrlStylePath')" />
            </ElSelect>
          </ElFormItem>
          <ElFormItem :label="t('repositoriesPage.fieldUseTls')">
            <div class="repo-form-inline">
              <ElSwitch v-model="form.use_tls" />
              <span class="repo-form-inline__hint">{{ form.use_tls ? t('repositoriesPage.tlsOnHint') : t('repositoriesPage.tlsOffHint') }}</span>
            </div>
          </ElFormItem>
          <ElFormItem :label="t('repositoriesPage.fieldRepoName')" required>
            <ElInput v-model="form.name" :placeholder="t('repositoriesPage.phRepoName')" />
          </ElFormItem>
          <ElFormItem :label="t('repositoriesPage.fieldBucket')" required>
            <ElInput v-model="form.bucket" :placeholder="t('repositoriesPage.phBucket')" />
          </ElFormItem>
          <ElFormItem :label="t('repositoriesPage.fieldPrefix')">
            <ElInput v-model="form.prefix" :placeholder="t('repositoriesPage.phPrefix')" />
          </ElFormItem>
          <ElFormItem :label="t('repositoriesPage.fieldSourceNode')">
            <ElSelect
              v-model="form.source_node_id"
              style="width: 100%"
              clearable
              :placeholder="t('repositoriesPage.phSourceNode')"
            >
              <ElOption
                v-for="n in sourceNodes"
                :key="n.id"
                :value="n.id"
                :label="n.ip_address ? `${n.name} (${n.ip_address})` : n.name"
              />
            </ElSelect>
          </ElFormItem>
        </template>
        <template v-else>
          <ElFormItem :label="t('repositoriesPage.fieldRepoName')" required>
            <ElInput v-model="form.name" :placeholder="t('repositoriesPage.phRepoName')" />
          </ElFormItem>
          <ElFormItem :label="t('repositoriesPage.fieldMountPath')" required>
            <ElInput v-model="form.mount_path" :placeholder="t('repositoriesPage.phMountPath')" />
          </ElFormItem>
          <p class="-mt-1 mb-2 text-xs text-[var(--color-text-tertiary)]">{{ t('repositoriesPage.hintNasPath') }}</p>
        </template>
      </ElForm>
      <template #footer>
        <div class="flex justify-end gap-2">
          <ElButton @click="modalOpen = false; resetForm()">{{ t('repositoriesPage.btnCancel') }}</ElButton>
          <ElButton type="primary" :loading="busy" @click="submitModal">
            {{ formKind === 'proxy_fs' ? t('repositoriesPage.btnCreateRepo') : t('repositoriesPage.btnVerifyInit') }}
          </ElButton>
        </div>
      </template>
    </Modal>
    <ElDialog
      v-model="repositoryCleanupBlockedDialogOpen"
      :title="t('repositoriesPage.cleanupBlockedTitle')"
      width="680px"
      destroy-on-close
    >
      <ElAlert
        type="error"
        :title="t('repositoriesPage.cleanupBlockedForceHint')"
        :closable="false"
        show-icon
      />
      <div class="repo-cleanup-blocked-list">
        <section
          v-for="entry in repositoryCleanupBlockedRows"
          :key="entry.repository.id"
          class="repo-cleanup-blocked-item"
        >
          <strong>{{ entry.repository.name }}</strong>
          <ul>
            <li v-for="reason in entry.blockers" :key="reason">{{ reason }}</li>
          </ul>
          <div v-if="entry.associationCount" class="repo-cleanup-blocked-associations">
            <span>{{ t('repositoriesPage.cleanupBlockedAssociations', { n: entry.associationCount }) }}</span>
            <ul>
              <li v-for="association in entry.associations" :key="association.backup_config_id">
                {{ association.backup_config_name }} — {{ association.source_name }}
              </li>
            </ul>
          </div>
        </section>
      </div>
      <template #footer>
        <ElButton type="primary" @click="repositoryCleanupBlockedDialogOpen = false">
          {{ t('common.close') }}
        </ElButton>
      </template>
    </ElDialog>
    <DangerConfirmDialog
      v-model="deleteRepositoriesDialogOpen"
      :title="deleteRepositoriesTitle"
      :message="deleteRepositoriesMessage"
      :items="deleteRepositoriesItems"
      :items-heading="t('repositoriesPage.batchDelete')"
      :item-name-label="t('repositoriesPage.colListName')"
      :item-status-label="t('repositoriesPage.colStatus')"
      :item-details-label="t('repositoriesPage.colBucketPath')"
      confirm-mode="keyword"
      :confirm-keyword="deleteConfirmKeyword"
      :confirm-keyword-hint="forceDeleteRepository ? t('repositoriesPage.cleanupForcePrompt') : t('common.deleteKeywordHint')"
      :confirm-keyword-placeholder="deleteConfirmKeyword"
      :confirm-disabled="deleteConfirmDisabled"
      :cancel-text="t('repositoriesPage.btnCancel')"
      :confirm-text="forceDeleteRepository ? t('repositoriesPage.cleanupForceConfirm') : activeTab === 's3' ? t('common.confirmDelete') : t('repositoriesPage.btnDelete')"
      :loading="busy"
      level="high"
      width="680px"
      @confirm="confirmDeleteRepositories"
      @cancel="closeDeleteRepositoriesDialog"
    >
      <div v-if="canForceDeleteRepository" class="repo-delete-force-option">
        <ElCheckbox v-model="forceDeleteRepository">
          {{ t('repositoriesPage.cleanupForceOption') }}
        </ElCheckbox>
        <p>{{ t('repositoriesPage.cleanupForceOptionHint') }}</p>
      </div>
      <ElAlert
        v-if="deletePreflightMessages.length"
        :type="activeDeletePreflight?.allowed ? 'warning' : 'error'"
        :title="deletePreflightMessages.join('; ')"
        :closable="false"
        show-icon
      />
    </DangerConfirmDialog>
  </ModulePage>
</template>

<style scoped>
.repo-cleanup-blocked-list {
  display: grid;
  gap: 12px;
  margin-top: 16px;
}

.repo-cleanup-blocked-item {
  padding: 12px 14px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
}

.repo-cleanup-blocked-item ul {
  margin: 8px 0 0;
  padding-left: 20px;
}

.repo-cleanup-blocked-associations {
  margin-top: 10px;
  color: var(--el-text-color-regular);
}

.repo-delete-force-option {
  display: grid;
  gap: 4px;
  margin: 14px 0;
}

.repo-delete-force-option p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.repo-form-inline {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.repo-form-inline__hint {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.repo-storage-detail .hfl-detail-section__title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.repo-storage-detail .hfl-detail-section__title::before {
  width: 3px;
  height: 14px;
  border-radius: 999px;
  background: var(--color-primary);
  content: '';
}

.repo-detail__inline-value {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 8px;
}

.repo-detail__name-summary {
  display: grid;
  min-width: 0;
  flex: 1 1 180px;
  align-items: start;
  gap: 5px;
}

.repo-detail__tag-group {
  display: inline-flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.repo-detail__mount-map {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.repo-detail__mount-tag {
  display: inline-flex;
  max-width: 100%;
  min-height: 24px;
  align-items: center;
  padding: 2px 8px;
  border: 1px solid rgb(203 213 225);
  border-radius: 6px;
  background: rgb(248 250 252);
  color: rgb(51 65 85);
  overflow-wrap: anywhere;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace);
  font-size: 12px;
  line-height: 1.45;
}

.repo-detail__mount-tag--source {
  border-color: var(--color-info-border);
  background: var(--color-info-light);
  color: var(--color-info);
}

.repo-detail__mount-tag--target {
  border-color: var(--color-success-border);
  background: var(--color-success-light);
  color: var(--color-success);
}

.repo-detail__mount-arrow {
  color: var(--color-text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.repo-associated-sources {
  min-width: 0;
}

.repo-tasks {
  display: grid;
  gap: 14px;
  min-width: 0;
}

.repo-tasks__filters {
  margin-bottom: 0;
}

.repo-tasks__search {
  width: 260px;
}

.repo-tasks__filters :deep(.el-select) {
  width: 130px;
}

.repo-tasks__filters :deep(.repo-tasks__search .el-select) {
  width: 90px;
}

.repo-tasks__date {
  width: 100%;
  max-width: 100%;
}

.repo-tasks__filter-badge {
  margin-right: 4px;
}

.repo-tasks__filter-button--active {
  color: var(--color-info);
  border-color: var(--color-info-border);
  background: var(--color-info-light);
}

.repo-tasks__filter-drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.repo-tasks__table :deep(.el-table__row) {
  cursor: pointer;
}

.repo-tasks__footer {
  display: flex;
  justify-content: flex-end;
}

.repo-task-name {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.repo-task-name > span {
  overflow: hidden;
  color: var(--el-text-color-primary);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.repo-task-name code {
  overflow: hidden;
  color: var(--el-text-color-secondary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 760px) {
  .repo-tasks__filters > * {
    flex: 1 1 100%;
    width: 100% !important;
  }

  .repo-tasks__search {
    width: 100%;
  }
}

.repo-associated-sources__table {
  width: 100%;
}

.repo-associated-sources__footer {
  display: flex;
  justify-content: flex-end;
  padding: 12px 0 0;
}

.repo-associated-source-cell {
  display: grid;
  min-width: 0;
  grid-template-columns: minmax(0, 1fr);
  align-items: start;
}

.repo-associated-source-cell__body {
  display: grid;
  min-width: 0;
  gap: 4px;
}

.repo-associated-source-cell__head {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.repo-associated-source-cell__meta {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.repo-associated-source-cell__name {
  min-width: 0;
  overflow: hidden;
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 650;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.repo-associated-source-cell__trait-icon {
  flex: 0 0 auto;
}

.repo-associated-mount-point-cell {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  gap: 7px;
}

.repo-associated-mount-point-cell__icon {
  flex: 0 0 auto;
  margin-top: 1px;
  color: rgb(217 119 6);
}

.repo-associated-mount-point-cell__path {
  display: -webkit-box;
  min-width: 0;
  overflow: hidden;
  color: rgb(30 41 59);
  font-size: 12px;
  line-height: 18px;
  text-overflow: ellipsis;
  overflow-wrap: anywhere;
  white-space: normal;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.repo-associated-health-cell {
  display: grid;
  min-width: 0;
  justify-items: start;
  gap: 5px;
}

.repo-associated-health-cell__main {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
}

.repo-associated-health-cell__time {
  min-width: 0;
  overflow: hidden;
  color: rgb(100 116 139);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.repo-associated-health-cell__error {
  min-width: 0;
  overflow: hidden;
  color: var(--color-error);
  font-size: 12px;
  line-height: 17px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.repo-list-tabs :deep(.el-tabs__header) {
  margin-bottom: 0;
}

.repo-list-tabs :deep(.el-tabs__content) {
  display: none;
}

.repo-location-cell {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 6px;
}

.repo-location-cell__meta {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: rgb(100 116 139);
}

.repo-protocol-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 62px;
  height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}

.repo-protocol-pill--smb {
  background: rgba(255, 247, 237, 0.95);
  color: rgb(194 65 12);
}

.repo-protocol-pill--nfs {
  background: rgba(239, 246, 255, 0.95);
  color: rgb(29 78 216);
}

.repo-protocol-pill--proxy-fs {
  background: rgba(239, 246, 255, 0.95);
  color: rgb(29 78 216);
}

.repo-proxy-cell {
  display: inline-flex;
  max-width: 100%;
  align-items: center;
  gap: 7px;
  min-height: 24px;
  color: rgb(30 41 59);
  font-size: 13px;
}

.repo-proxy-cell__dot {
  width: 7px;
  height: 7px;
  flex-shrink: 0;
  border-radius: 999px;
  background: #165fff;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
}

.repo-proxy-cell__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.repo-proxy-cell--empty {
  color: rgb(148 163 184);
  font-style: italic;
}

.repo-proxy-unbound-cell {
  display: inline-block;
  max-width: 100%;
  padding: 2px 0;
  color: rgb(30 41 59);
  cursor: default;
}

.repo-proxy-unbound-cell:focus-visible {
  outline: 2px solid var(--color-warning-border);
  outline-offset: 2px;
}

.repo-proxy-unbound-cell__text {
  display: grid;
  min-width: 0;
  gap: 1px;
}

.repo-proxy-unbound-cell__primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.25;
  color: rgb(30 41 59);
}

.repo-proxy-unbound-cell__primary svg {
  flex: 0 0 auto;
  color: var(--color-warning);
}

.repo-proxy-unbound-cell__primary span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.repo-proxy-unbound-cell__secondary {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  line-height: 1.25;
  color: rgb(100 116 139);
}

.repo-table-header-with-tip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}

:global(.repo-proxy-bind-tip-popper) {
  max-width: 420px;
  padding: 10px 12px !important;
  border-color: var(--color-warning-border) !important;
  background: var(--color-warning-light) !important;
}

:global(.repo-proxy-bind-tip) {
  display: grid;
  gap: 9px;
  margin: 0;
  padding: 0;
  list-style: none;
}

:global(.repo-proxy-bind-tip__item) {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  align-items: flex-start;
  gap: 8px;
}

:global(.repo-proxy-bind-tip__index) {
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

:global(.repo-proxy-bind-tip__text) {
  min-width: 0;
  color: rgb(120 75 12);
  font-size: 13px;
  line-height: 1.6;
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

</style>
