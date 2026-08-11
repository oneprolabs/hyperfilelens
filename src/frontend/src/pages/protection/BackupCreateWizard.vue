<script setup lang="ts">
import { computed, defineComponent, h, nextTick, onMounted, onUnmounted, reactive, ref, watch, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  Plus,
  Pencil,
  ArrowLeft,
  X,
  Database,
  RefreshCw,
  ChevronDown,
  CloudUpload,
  Trash2,
  Search,
  HardDrive,
  Filter,
  Route,
  ClipboardCheck,
  Archive,
  Camera,
  Cloud,
  Folder,
  File as FileIcon,
  FolderTree,
  FolderOpen,
  TextCursorInput,
  Info,
  ShieldCheck,
  ShieldAlert,
  CircleOff,
  Scale,
} from 'lucide-vue-next'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { ElTable, ElTree } from 'element-plus'
import { copyTextToClipboard } from '../../lib/clipboard'
import ModulePage from '../../components/ModulePage.vue'
import HflHelpTip from '../../components/HflHelpTip.vue'
import HflPopover from '../../components/HflPopover.vue'
import HostSourceDetailDrawer from '../../components/HostSourceDetailDrawer.vue'
import BackupConfigCreateWizard from './BackupConfigCreateWizard.vue'
import ProtectionPolicyEditorForm from './components/ProtectionPolicyEditorForm.vue'
import NasSourceDetailDrawer from './components/NasSourceDetailDrawer.vue'
import { useProtectionSideNav } from '../../composables/useProtectionSideNav'
import { useListSearch } from '../../composables/useListSearch'
import { useRestoreTargetCatalog } from '../../composables/useRestoreTargetCatalog'
import {
  backupFlowSourceStepIcon,
  backupSourceTypeIcon,
} from '../../lib/sourceTypeIcons'
import {
  nasMountProtocolIcon,
  targetNasSidebarIcon,
} from '../../lib/resourceIcons'
import { getEffectiveOrgKey } from '../../composables/useAuth'
import { apiErrorMessage, apiErrorMessageI18n, isAbortError } from '../../lib/api'
import { normalizeThrownError } from '../../lib/errors'
import { openErrorDetails } from '../../lib/errors/details'
import { notifyError } from '../../lib/notify'
import { logger } from '../../lib/logger'
import {
  createBackupConfig,
  createRecoveryPlan,
  deleteRecoveryPlan,
  getBackupConfig,
  isInvalidCompressionLevelError,
  parseSourceRefId,
  updateBackupConfig,
  updateRecoveryPlan,
  type BackupConfigDetail,
  type BackupConfigCreatePayload,
  type BackupConfigRecoveryPlanPayload,
  type CompressionLevel,
  type RecoveryPlanCreatePayload,
} from '../../lib/protectionBackupConfigApi'
import {
  validateProtectionBackupTargets,
  type BackupTargetValidationResult,
} from '../../lib/protectionBackupTargetValidationApi'
import {
  backupTargetValidationFailureDetails,
  backupTargetValidationFailureSummary,
} from '../../lib/protectionBackupTargetValidationDetails'
import {
  createSourceResource,
  listBackupSourceDirectories,
  getBackupSourcePathInfo,
  getSourceResource,
  type BackupSelectableSource,
  type BackupSourceDirectoryEntry,
  type SourceResource,
} from '../../lib/sourceApi'
import {
  backupSelectableNasId,
  buildNasSourceCreatePayload,
} from '../../lib/nasSourceCreate'
import { preflightSourceNasCreate, showNasDraftPreflightGuidance } from '../../lib/nasDraftPreflight'
import { resolveNasSubmitName } from '../../lib/nasSourceNaming'
import {
  isLikelySmbFilenameEncodingIssue,
  isLikelySmbFilenamePathNotFound,
  selectBackupSourceDirectoryTreeEntries,
  shouldAutoExpandRefreshedDirectory,
} from '../../lib/backupSourceDirectoryTree'
import { restoreDirectoryBrowseSourceId } from '../../lib/restoreDirectoryTarget'
import { useBackupSourcePipeline } from '../../composables/useBackupSourcePipeline'
import {
  createBackupPolicy,
  createFileFilterRule,
  listBackupPolicies,
  listFileFilterRules,
  type BackupPolicy,
  type FileFilterRule,
} from '../../lib/protectionPolicyApi'
import {
  createStorageRepository,
  listAllStorageRepositories,
  type StorageRepository,
} from '../../lib/storageRepositoryApi'
import {
  defaultS3UrlStyle,
  S3_PROVIDER_OPTIONS,
  type S3PlatformSelection as AddTargetS3Platform,
  type S3UrlStyle as AddTargetS3UrlStyle,
} from '../../lib/s3ProviderProfiles'
import {
  fetchStorageProviderCatalog,
  type StorageProviderConfig,
} from '../../lib/storageProviderCatalogApi'
import { storageRepositoryLocation } from '../../lib/storageRepositoryDisplay'
import { booleanStatusTag } from '../../lib/statusTag'
import { DEFAULT_S3_OBJECT_PREFIX, normalizeS3EndpointInput } from '../../lib/s3PlatformDisplay'
import {
  useProtectionDemoStore,
  type DemoDirTreeItem,
} from '../../composables/useProtectionDemoStore'
import { issueEnrollmentInstall, listAllNodes, type EnrollmentOs } from '../../lib/nodeApi'
import { formatOfflineBackupPlanMessage } from './lib/offlineBackupPlanMessage'
import {
  backupTargetIncompatibilityReason,
  backupTargetIncompatibilityReasonForSources,
  type BackupTargetIncompatibilityReason,
} from './lib/backupTargetCompatibility'
import {
  createEmptyPolicyForm,
  createEmptyFileFilterForm,
  backupPolicyToForm,
  compileFilterIgnorePatterns,
  fileFilterRuleToForm,
  getFilterExcludedExtensions,
  getFilterExcludedPaths,
  fileFilterFormToWritePayload,
  policyFormToWritePayload,
  validateScheduleForm,
  validateRetentionForm,
  type BackupPolicyForm,
  type FileFilterRuleForm,
  type MessageLocale,
} from '../../lib/protectionPolicyFormModel'
import type { ApiNode } from '../../types/node'
import { buildGeneratedNasMountDir, buildGeneratedNasName } from '../../lib/nasMountPath'
import {
  buildNasRepairMountOptionsPath,
  isSmbMountNegotiationError,
  SMB_MOUNT_OPTION_EXAMPLES,
} from '../../lib/nasMountTroubleshooting'
import TargetRepositoryPicker from './components/TargetRepositoryPicker.vue'
import TargetRepositoryDetailCard from './components/TargetRepositoryDetailCard.vue'
import CompressionLevelSelector from './components/CompressionLevelSelector.vue'
import HostAddForm from './components/HostAddForm.vue'
import NasAddForm from './components/NasAddForm.vue'
import AddS3Repo from '../node/AddS3Repo.vue'
import AddNasRepository from '../node/AddNasRepository.vue'
import AddProxyFsRepository from '../node/AddProxyFsRepository.vue'
import AgentPlatformBrandIcon from '../../components/agent-deploy/AgentPlatformBrandIcon.vue'

const router = useRouter()
const route = useRoute()
const PROXY_DEPLOY_ROUTE = { path: '/node/nodes/deploy', query: { role: 'proxy' } } as const
const FLOW_RETURN_STEP_STORAGE_KEY = 'protection-flow-return-step'
const props = withDefaults(defineProps<{
  embedded?: boolean
  initialSources?: string[]
  initialEditConfigIds?: number[]
  initialEditSection?: 'paths' | 'policy' | 'recovery-plan'
}>(), {
  embedded: false,
  initialSources: () => [],
  initialEditConfigIds: () => [],
})
const emit = defineEmits<{
  closed: []
  completed: [sourceIds: string[]]
  ready: []
}>()
const EmbeddedWizardShell = defineComponent({
  name: 'EmbeddedWizardShell',
  inheritAttrs: false,
  setup(_, { slots }) {
    return () => slots.default?.() ?? []
  },
})

function goToBackupPolicyPage() {
  const { href } = router.resolve({ path: '/protection/policies', query: { tab: 'backup' } })
  window.open(href, '_blank', 'noopener,noreferrer')
}

function goToFilterRulePage() {
  const { href } = router.resolve({ path: '/protection/policies', query: { tab: 'filter' } })
  window.open(href, '_blank', 'noopener,noreferrer')
}

function goToStorageRepositoryPage() {
  const { href } = router.resolve({ path: '/node/repositories', query: { tab: 's3' } })
  window.open(href, '_blank', 'noopener,noreferrer')
}

const { t, locale } = useI18n()
const bindProxyLeadItems = computed(() => [
  t('addNasRepo.bindProxyLeadItemRecommend'),
  t('addNasRepo.bindProxyLeadItemAfterBinding'),
  t('addNasRepo.bindProxyLeadItemSkip'),
])
const store = useProtectionDemoStore()
const STEP2_SOURCES_STORAGE_KEY = 'protection-create-wizard-step2-sources'
const { setPipelineStep } = useBackupSourcePipeline()

function nasRepairMountOptionsHref(repositoryId: number) {
  return router.resolve(buildNasRepairMountOptionsPath(repositoryId)).href
}

function showBackupConfigCreateError(err: unknown, backupName: string, repositoryId: number) {
  const fallback = t('protection.backupsPage.createFailedWithName', { name: backupName })
  const message = apiErrorMessageI18n(err, t, fallback)
  if (!repositoryId || !isSmbMountNegotiationError(message)) {
    notifyError({
      title: t('protection.backupsPage.createFailed'),
      message,
      duration: 10000,
      dedupeKey: `backup-config-create:${repositoryId || backupName}`,
      error: err,
      errorDetails: { issue: message },
      showDetails: true,
    })
    return
  }

  const href = nasRepairMountOptionsHref(repositoryId)
  void ElMessageBox.alert(
    h('div', { class: 'space-y-3 text-sm leading-6 text-[rgb(51_65_85)]' }, [
      h('p', { class: 'font-medium text-[rgb(15_23_42)]' },
        'The NAS SMB/CIFS mount may have failed during protocol negotiation.'),
      h('p',
        'Different NAS devices, SMB servers, and client kernels may support different protocol versions. Open the NAS repository connection settings and try specifying a version in Mount Options.'),
      h('div', { class: 'flex flex-wrap gap-1.5' }, SMB_MOUNT_OPTION_EXAMPLES.map((example) =>
        h('code', {
          class: 'rounded border border-[rgb(203_213_225)] bg-[rgb(248_250_252)] px-1.5 py-0.5 font-mono text-xs text-[rgb(15_23_42)]',
        }, example),
      )),
      h('p', { class: 'whitespace-pre-wrap rounded border border-[rgb(226_232_240)] bg-[rgb(248_250_252)] p-2 text-xs text-[rgb(71_85_105)]' }, message),
      h('a', {
        href,
        target: '_blank',
        rel: 'noopener noreferrer',
        class: 'inline-flex font-medium text-[rgb(37_99_235)] underline underline-offset-2',
      }, 'Modify NAS connection in a new tab'),
    ]),
    'Failed to create backup config',
    {
      confirmButtonText: 'Close',
      dangerouslyUseHTMLString: false,
      customClass: 'backup-config-create-error-dialog',
    },
  )
}

type RealSourceRow = {
  id: string
  type: 'host' | 'nas'
  name: string
  platform?: EnrollmentOs
  hostname: string
  nodeName: string
  nodeIp: string
  /** Lifecycle state; connectivity is represented by availability. */
  status: string
  availability: 'online' | 'offline'
  protocol?: 'nfs' | 'smb'
  boundNodeId?: number | null
  registeredAt?: string | null
}

type WizardTarget = {
  id: string
  name: string
  location: string
  repoType: string
  status?: 'online' | 'warning' | 'offline'
  health?: 'online' | 'offline' | 'unverified' | string
  nasProtocol?: 'nfs' | 'smb' | string | null
  bindNodeType?: 'proxy' | string | null
  bindNodeId?: number | string | null
  bindNodeName?: string | null
  bindNodeIp?: string | null
  s3Endpoint?: string | null
  s3ExternalEndpoint?: string | null
  s3InternalEndpoint?: string | null
  s3Region?: string | null
  s3Bucket?: string | null
  nasServerAddress?: string | null
  nasSharePath?: string | null
  proxyNodeDir?: string | null
  crossProxyReady?: boolean
  crossProxyReason?: string | null
  disabled?: boolean
  disabledReason?: string | null
  usedBytes?: number
  capacityBytes?: number
  storageTotalBytes?: number
  storageUsedBytes?: number
  storageAvailableBytes?: number
}

type RepositoryEndpointType = 'external' | 'internal'

type WizardPolicy = {
  id: string
  name: string
  isActive: boolean
  schedule?: string
  scheduleEnabled?: boolean
  backupFrequencyDesc?: string
  retentionEnabled?: boolean
  relatedBackupCount: number
  formData: BackupPolicyForm
}

type WizardFilter = {
  id: string
  name: string
  isActive: boolean
  summary?: string
  largeFileLimitEnabled: boolean
  largeFileBytesMax: number
  relatedBackupCount: number
  raw: FileFilterRule
}

const realSourceById = ref(new Map<string, RealSourceRow>())
const restoreTargetCatalog = useRestoreTargetCatalog()
const recoveryTargetLoading = restoreTargetCatalog.loading
const recoveryTargetLoadingMore = restoreTargetCatalog.loadingMore
const recoveryTargetError = restoreTargetCatalog.error
const realPolicies = ref<WizardPolicy[]>([])
const realFilters = ref<WizardFilter[]>([])
const realTargets = ref<WizardTarget[]>([])
const policiesRefreshing = ref(false)
const filtersRefreshing = ref(false)
const targetsRefreshing = ref(false)

function isBackupSelectableSourceId(id: string) {
  return /^agent:\d+$/.test(id) || /^nas:\d+$/.test(id)
}

function mapSelectableSource(item: BackupSelectableSource): RealSourceRow {
  return {
    id: item.id,
    type: item.type,
    name: item.name,
    platform: item.platform,
    hostname: item.hostname || item.name,
    nodeName: item.node_name || item.name,
    nodeIp: item.node_ip || '',
    status: item.status,
    availability: item.availability === 'online' ? 'online' : 'offline',
    protocol: item.protocol === 'smb' ? 'smb' : item.protocol === 'nfs' ? 'nfs' : undefined,
    boundNodeId: item.bound_node_id ?? null,
    registeredAt: item.registered_at,
  }
}

function mapRepository(repo: StorageRepository): WizardTarget {
  const bucket = repo.repo_type === 's3'
    ? repo.s3_bucket
    : String(repo.config?.bucket || repo.config?.s3_bucket || repo.config?.share_path || repo.config?.proxy_node_dir || '')
  const location = storageRepositoryLocation(repo)
  return {
    id: String(repo.id),
    name: repo.name,
    location: location || bucket || repo.bind_node_display_name || repo.repo_type,
    repoType: String(repo.repo_type || '').toUpperCase(),
    status: repo.health === 'offline' || repo.status === 'create_failed' ? 'offline' : repo.health === 'online' ? 'online' : 'warning',
    health: repo.health,
    nasProtocol: (repo.nas_protocol ?? null) as WizardTarget['nasProtocol'],
    bindNodeType: (repo.bind_node_type ?? null) as WizardTarget['bindNodeType'],
    bindNodeId: (repo.bind_node_id ?? null) as WizardTarget['bindNodeId'],
    bindNodeName: repo.bind_node_display_name ?? null,
    bindNodeIp: repo.bind_node_ip ?? null,
    s3Endpoint: (repo.config?.endpoint as string | undefined) ?? null,
    s3ExternalEndpoint: (repo.config?.external_endpoint as string | undefined)
      ?? (repo.config?.endpoint as string | undefined)
      ?? null,
    s3InternalEndpoint: (repo.config?.internal_endpoint as string | undefined)
      ?? (repo.config?.external_endpoint as string | undefined)
      ?? (repo.config?.endpoint as string | undefined)
      ?? null,
    s3Region: (repo.config?.region as string | undefined) ?? null,
    s3Bucket: repo.s3_bucket ?? null,
    nasServerAddress: (repo.config?.server_address as string | undefined) ?? null,
    nasSharePath: (repo.config?.share_path as string | undefined) ?? null,
    proxyNodeDir: (repo.config?.proxy_node_dir as string | undefined) ?? null,
    crossProxyReady: repo.cross_proxy_access?.ready === true,
    crossProxyReason: repo.cross_proxy_access?.reason ?? null,
    usedBytes: Number(repo.estimated_usage_bytes || 0),
    capacityBytes: Math.max(0, Number(repo.config?.quota_gb || 0)) * 1024 ** 3,
    storageTotalBytes: Number(repo.storage_total_bytes || 0),
    storageUsedBytes: Number(repo.storage_used_bytes || 0),
    storageAvailableBytes: Number(repo.storage_available_bytes || 0),
  }
}

function mapPolicy(policy: BackupPolicy): WizardPolicy {
  const formData = backupPolicyToForm(policy)
  return {
    id: String(policy.id),
    name: policy.name,
    isActive: policy.is_active !== false,
    schedule: policy.schedule_summary || policy.schedule?.cron_expr || '',
    scheduleEnabled: policy.schedule?.enabled === true,
    backupFrequencyDesc: policy.schedule_summary || policy.schedule?.cron_expr || '',
    retentionEnabled: policy.retention?.enabled === true,
    relatedBackupCount: Number(policy.related_backup_count || 0),
    formData,
  }
}

function mapFilter(rule: FileFilterRule): WizardFilter {
  return {
    id: String(rule.id),
    name: rule.name,
    isActive: rule.is_active !== false,
    summary: rule.summary,
    largeFileLimitEnabled: Boolean(rule.large_file_limit_enabled),
    largeFileBytesMax: Number(rule.large_file_bytes_max || 0),
    relatedBackupCount: Number(rule.related_backup_count || 0),
    raw: rule,
  }
}

function getRealTarget(id: string | undefined) {
  return realTargets.value.find((target) => target.id === id)
}

function normalizedTargetEndpoint(value: string | null | undefined) {
  return normalizeS3EndpointInput(value).toLowerCase().replace(/\.$/, '')
}

function targetExternalEndpoint(target: WizardTarget | null | undefined) {
  return normalizedTargetEndpoint(target?.s3ExternalEndpoint || target?.s3Endpoint)
}

function targetInternalEndpoint(target: WizardTarget | null | undefined) {
  return normalizedTargetEndpoint(
    target?.s3InternalEndpoint || target?.s3ExternalEndpoint || target?.s3Endpoint,
  )
}

function targetHasDistinctEndpoints(target: WizardTarget | null | undefined) {
  if (!target || target.repoType !== 'S3') return false
  const external = targetExternalEndpoint(target)
  const internal = targetInternalEndpoint(target)
  return Boolean(external && internal && external !== internal)
}

function endpointForTarget(
  target: WizardTarget | null | undefined,
  endpointType: RepositoryEndpointType,
) {
  return endpointType === 'internal'
    ? targetInternalEndpoint(target)
    : targetExternalEndpoint(target)
}

function endpointTypeForGroup(groupKey: string): RepositoryEndpointType {
  const target = getRealTarget(sourceTargetMap.value[groupKey])
  if (!targetHasDistinctEndpoints(target)) return 'external'
  return sourceEndpointTypeMap.value[groupKey] || 'external'
}

function endpointSummaryForGroup(group: WizardSourceGroup) {
  const target = getRealTarget(sourceTargetMap.value[group.key])
  if (!target || target.repoType !== 'S3') return ''
  const endpointType = endpointTypeForGroup(group.key)
  const label = endpointType === 'internal'
    ? t('addS3Repo.endpointInternal')
    : t('addS3Repo.endpointExternal')
  const endpoint = endpointForTarget(target, endpointType)
  return endpoint ? `${label} · ${endpoint}` : label
}

function getRealPolicy(id: string | undefined) {
  return realPolicies.value.find((policy) => policy.id === id)
}

function getRealFilter(id: string | undefined) {
  return realFilters.value.find((filter) => filter.id === id)
}

function getSourceName(sourceId: string) {
  return realSourceById.value.get(sourceId)?.name || store.getNodeName(sourceId)
}

function readPersistedStep2Sources() {
  if (typeof localStorage === 'undefined') return []
  try {
    const raw = localStorage.getItem(STEP2_SOURCES_STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === 'string' && id.length > 0) : []
  } catch {
    return []
  }
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

const TABLE_HEADER_STYLE: Record<string, string> = {
  background: 'rgba(248, 250, 252, 0.96)',
  color: 'rgb(71 85 105)',
  fontWeight: '600',
  whiteSpace: 'nowrap',
}

const step2Sources = ref<string[]>(normalizeSourceIdList(readPersistedStep2Sources()))

const step1Selection = ref<string[]>([])

function isRealSourceId(id: string) {
  return realSourceById.value.has(id) || isBackupSelectableSourceId(id)
}

function filterExistingRealSourceIds(ids: string[]) {
  return normalizeSourceIdList(ids).filter(isRealSourceId)
}

const step2AvailableSourceIds = computed(() => {
  return step2Sources.value.filter(isRealSourceId)
})

const createSourceIds = computed(() => {
  return step1Selection.value.filter(isRealSourceId)
})

function sourceHasBackupConfig(sourceId: string) {
  return isRealSourceId(sourceId)
}

const lastCreatedSourceIds = ref<string[]>([])

function finishCreateAndGoToStep3(sourceIds?: string[]) {
  const focusIds = (sourceIds ?? lastCreatedSourceIds.value).filter((id) => sourceHasBackupConfig(id))
  if (focusIds.length > 0) {
    const idSet = new Set(focusIds)
    step1Selection.value = step1Selection.value.filter((id) => !idSet.has(id))
    step2Sources.value = step2Sources.value.filter((id) => !idSet.has(id))
  }
  createOpen.value = false
  if (props.embedded) {
    emit('completed', focusIds)
    return
  }
  if (typeof sessionStorage !== 'undefined') {
    sessionStorage.setItem(FLOW_RETURN_STEP_STORAGE_KEY, '2')
  }
  void router.push({ path: '/protection/backups', query: { step: 'start-backup' } })
}

function goToStartBackupFromCreate() {
  finishCreateAndGoToStep3()
}

type BackupPathType = 'directory' | 'file' | 'unknown'

type BackupDirEntry = {
  key: string
  backupConfigId?: number
  sourceId: string
  sourceName: string
  sourceType: 'host' | 'nas'
  platform?: EnrollmentOs
  path: string
  pathType: BackupPathType
}

type SourceTreeItem = DemoDirTreeItem & {
  is_dir?: boolean
  path_type?: BackupPathType
  disabled?: boolean
  disabledReason?: string
  nodeKind?: 'entry' | 'loadMore'
  sourceId?: string
  parentPath?: string
  nextCursor?: string
  loading?: boolean
  filenameEncodingSuspected?: boolean
}

type CreateSourceRow = {
  id: string
  name: string
  type: 'host' | 'nas'
  platform?: EnrollmentOs
  typeLabel: string
  selectedEntries: BackupDirEntry[]
  selectedPreviewEntries: BackupDirEntry[]
  hiddenDirCount: number
}

type TargetPickerState = {
  search: string
  repoType: string
  targetId: string
}

type NasTargetMode = 'single_repo' | 'per_directory_repo'
type AddTargetRepoKind = 's3' | 'nas' | 'proxy_fs'
type AddTargetNasProtocol = 'smb' | 'nfs'
type CreateRecoveryTargetMode = '' | 'original' | 'new'
type CreateRecoveryConflictMode = '' | 'skip' | 'overwrite'
type AddTargetDirTreeNode = {
  id: string
  label: string
  pathLabel: string
  children?: AddTargetDirTreeNode[]
}

type WizardSourceGroup = {
  key: string
  backupConfigId?: number
  sourceId: string
  sourceName: string
  sourceType: 'host' | 'nas'
  platform?: EnrollmentOs
  entries: BackupDirEntry[]
}

type CreateRecoveryPlanConfig = {
  enabled: boolean
  conflictMode: CreateRecoveryConflictMode
  dirPlans: CreateRecoveryDirPlanConfig[]
}

type CreateRecoveryDirPlanConfig = {
  id: string
  sourcePath: string
  sourcePathType?: BackupPathType
  sourcePathValidation?: 'valid' | 'pending' | 'invalid'
  sourcePathError?: string
  targetMode: CreateRecoveryTargetMode
  targetHostId: string
  restoreDir: string
  restoreDirValidation?: 'valid' | 'pending' | 'invalid'
  restoreDirError?: string
}

type CreateRecoveryDirPlanField = 'sourcePath' | 'targetHostId' | 'restoreDir'
type CreateRecoveryPlanField = CreateRecoveryDirPlanField | 'conflictMode'

const sourceTargetMap = ref<Record<string, string>>({})
const sourceEndpointTypeMap = ref<Record<string, RepositoryEndpointType>>({})
const WHOLE_SNAPSHOT_RECOVERY_PATH = '__whole_snapshot__'

const addSourceOpen = ref(false)
const addSourceShellRef = ref<HTMLElement | null>(null)
type AddSourceType = 'hostFileSystem' | 'nas'
const addSourceType = ref<AddSourceType>('hostFileSystem')
const proxyNodes = ref<ApiNode[]>([])
const proxyNodesRefreshing = ref(false)
const createHostDetailOpen = ref(false)
const createHostDetailNodeId = ref<number | null>(null)
const createNasDetailOpen = ref(false)
const createNasDetailResource = ref<SourceResource | null>(null)
const createSourceDetailLoading = ref(false)
const MOCK_PROXY_NODES: ApiNode[] = [
  { id: -101, organization: 0, name: 'Proxy-GW-01', role: 'proxy', status: 'online' },
  { id: -102, organization: 0, name: 'Proxy-GW-02', role: 'proxy', status: 'online' },
]

async function loadProxyNodes() {
  try {
    const all = await listAllNodes()
    const proxies = all.filter(node => node.role === 'proxy')
    proxyNodes.value = proxies.length > 0 ? proxies : [...MOCK_PROXY_NODES]
  } catch {
    proxyNodes.value = [...MOCK_PROXY_NODES]
  }
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
const deployOrgKey = ref('')
const deployScriptCache: Partial<Record<EnrollmentOs, string>> = {}
let deployGeneration = 0




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
  deployScript.value = ''
  if (!deployOrgKey.value) {
    if (generation === deployGeneration && deploySelectedOs.value === os) {
      deployScriptLoading.value = false
    }
    return
  }
  try {
    const { command } = await issueEnrollmentInstall({
      role: 'agent',
      os,
      note: 'deploy:agent:backup-wizard',
    })
    if (generation !== deployGeneration) return
    deployScriptCache[os] = command
    if (deploySelectedOs.value !== os) return
    deployScript.value = command
  } catch (e) {
    if (generation === deployGeneration && deploySelectedOs.value === os) {
      ElMessage.error({ message: apiErrorMessage(e, t('nodesDeploy.scriptLoadFailed')), grouping: true })
    }
  } finally {
    if (generation === deployGeneration && deploySelectedOs.value === os) {
      deployScriptLoading.value = false
    }
  }
}

function startDeploySession() {
  deployOrgKey.value = getEffectiveOrgKey()
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
  nasNfsOptions.value = ''
}

function closeAddSource() {
  addSourceOpen.value = false
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
const nasNfsOptions = ref('')

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
    const created = await createSourceResource(payload)
    const selectableId = backupSelectableNasId(created.id)
    await loadWizardSources([selectableId])
    if (!realSourceById.value.has(selectableId)) {
      const server = String(created.config?.server || '').trim()
      const next = new Map(realSourceById.value)
      next.set(selectableId, {
        id: selectableId,
        type: 'nas',
        name: created.name,
        hostname: server || created.name,
        nodeName: created.bound_node_name || created.name,
        nodeIp: '',
        status: created.availability === 'online' ? 'online' : 'offline',
        protocol: nasProtocol.value,
        boundNodeId: created.bound_node ?? nasBindNodeId.value ?? null,
        registeredAt: created.created_at ?? null,
      })
      realSourceById.value = next
    }
    if (realSourceById.value.has(selectableId) && !step1Selection.value.includes(selectableId)) {
      step1Selection.value = [...step1Selection.value, selectableId]
    }
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

watch(nasProtocol, () => {
  nasSmbServer.value = ''
  nasSmbShare.value = ''
  nasSmbUsername.value = ''
  nasSmbPassword.value = ''
  nasSmbDomain.value = ''
  nasNfsHost.value = ''
  nasNfsExport.value = ''
  nasNfsOptions.value = ''
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

watch(addSourceOpen, (open) => {
  if (typeof document === 'undefined') return
  document.body.style.overflow = open ? 'hidden' : ''
  if (open) {
    void nextTick(() => addSourceShellRef.value?.focus())
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

watch(step2Sources, (ids) => {
  const next = filterExistingRealSourceIds(ids)
  if (!sameIdList(next, ids)) {
    step2Sources.value = next
    return
  }
  if (typeof localStorage === 'undefined') return
  if (next.length > 0) {
    localStorage.setItem(STEP2_SOURCES_STORAGE_KEY, JSON.stringify(next))
  } else {
    localStorage.removeItem(STEP2_SOURCES_STORAGE_KEY)
  }
}, { immediate: true })

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

const messageLocale = computed<MessageLocale>(() => 'en')

const createOpen = ref(false)
const createStep = ref(0)
const createCompletedSteps = ref<Set<number>>(new Set())
type BackupConfigEditSection = 'paths' | 'policy' | 'recovery-plan'
const editConfigs = ref<BackupConfigDetail[]>([])
const editSection = ref<BackupConfigEditSection | null>(null)
const isEditMode = computed(() => editConfigs.value.length > 0)
const hasEditRequest = computed(() => {
  if (isEditMode.value) return true
  if (props.embedded) {
    return props.initialEditConfigIds.some((id) => Number.isFinite(Number(id)) && Number(id) > 0)
  }
  return Boolean(route.query.edit_config_ids || route.query.edit_config_id)
})
const activeEditSection = computed<BackupConfigEditSection>(() =>
  editSection.value
  ?? props.initialEditSection
  ?? editSectionFromQuery(route.query.edit_section),
)
const editorTitle = computed(() => {
  if (!hasEditRequest.value) return t('protection.backupsPage.modalCreateTitle')
  if (activeEditSection.value === 'policy') return t('protection.backupsPage.flowActionEditBackupPolicy')
  if (activeEditSection.value === 'recovery-plan') return t('protection.backupsPage.flowActionEditRestorePlan')
  return t('protection.backupsPage.flowActionEditBackupPaths')
})
const editorDescription = computed(() => {
  if (!hasEditRequest.value) return t('protection.backupsPage.modalCreateDesc')
  if (activeEditSection.value === 'policy') return t('protection.backupsPage.editBackupPolicyDesc')
  if (activeEditSection.value === 'recovery-plan') return t('protection.backupsPage.editRestorePlanDesc')
  return t('protection.backupsPage.editBackupPathsDesc')
})
const RECOVERY_PLAN_MAPPING_PREVIEW_MAX = 3
const wizardDirEntries = ref<BackupDirEntry[]>([])
const wizardBrowsingSourceId = ref('')
const createSourceSearch = ref('')
const filterPolicySourceSearch = ref('')
const targetSourceSearch = ref('')
const recoveryPlanSourceSearch = ref('')
const {
  appliedSearch: appliedCreateSourceSearch,
  clearSearch: clearCreateSourceSearch,
  resetSearch: resetCreateSourceSearch,
} = useListSearch(createSourceSearch)
const {
  appliedSearch: appliedFilterPolicySourceSearch,
  clearSearch: clearFilterPolicySourceSearch,
  resetSearch: resetFilterPolicySourceSearch,
} = useListSearch(filterPolicySourceSearch)
const {
  appliedSearch: appliedTargetSourceSearch,
  clearSearch: clearTargetSourceSearch,
  resetSearch: resetTargetSourceSearch,
} = useListSearch(targetSourceSearch)
const {
  appliedSearch: appliedRecoveryPlanSourceSearch,
  clearSearch: clearRecoveryPlanSourceSearch,
  resetSearch: resetRecoveryPlanSourceSearch,
} = useListSearch(recoveryPlanSourceSearch)
const createSourceTableRef = ref<InstanceType<typeof ElTable>>()
const createRecoveryPlanTableRef = ref<InstanceType<typeof ElTable>>()
const createSourceTreeRef = ref<InstanceType<typeof ElTree>>()
const createSourceDirKeys = ref<string[]>([])
const createSourceTreeRefs = new Map<string, InstanceType<typeof ElTree>>()
const createRecoveryDirectoryTreeRefs = new Map<string, InstanceType<typeof ElTree>>()
const createSourceDirKeysBySource = reactive<Record<string, string[]>>({})
const createSourcePathTypeBySource = reactive<Record<string, Record<string, BackupPathType>>>({})
const manualSourcePathBySource = reactive<Record<string, string>>({})
const manualSourcePathValidatingBySource = reactive<Record<string, boolean>>({})
const manualSourcePathErrorBySource = reactive<Record<string, string>>({})
const createExpandedSourceIds = ref<string[]>([])
const createSourceValidationAttempted = ref(false)
const highlightedCreateSourceId = ref('')
const createRecoveryPlanExpandedKeys = ref<string[]>([])
type ValidationPopoverKind = '' | 'source-dirs' | 'target' | 'recovery-plan'
const validationPopoverVisible = ref(false)
const validationPopoverKind = ref<ValidationPopoverKind>('')
const validationPopoverTitle = ref('')
const validationPopoverItems = ref<string[]>([])
const treeRemountKey = ref(0)
const noSourceTreeRoots = ref(false)
const noSourceTreeRootsBySource = reactive<Record<string, boolean>>({})
const createSourceTreeErrorBySource = reactive<Record<string, string>>({})
const createSourceTreeRemountKeyBySource = reactive<Record<string, number>>({})
const refreshingSourceDirectoryByKey = reactive<Record<string, boolean>>({})
const sourceDirectoryExpansionRevisionByKey = new Map<string, number>()
const directoryLoadingByKey = reactive<Record<string, number>>({})
const sourceTreeConflictWarnedAt = reactive<Record<string, number>>({})
const dirTreeProps = { label: 'label', children: 'children', isLeaf: 'isLeaf', disabled: 'disabled' }
const targetAssignmentCheckedGroupKeys = ref<string[]>([])
const targetValidationAttempted = ref(false)
const highlightedTargetGroupKey = ref('')
const targetValidationInProgress = ref(false)
const targetConnectionResults = ref<Record<string, BackupTargetValidationResult>>({})
let targetValidationController: AbortController | null = null
const TARGET_VALIDATION_CLIENT_TIMEOUT_MS = 125_000
const batchTargetPicker = reactive<TargetPickerState>({ search: '', repoType: '', targetId: '' })
const batchNasTargetMode = ref<NasTargetMode>('per_directory_repo')
const batchRepositoryEndpointType = ref<RepositoryEndpointType | ''>('')
type TargetAssignDialogMode = 'single' | 'batch'
const targetAssignDialogOpen = ref(false)
const targetAssignDialogMode = ref<TargetAssignDialogMode>('batch')
const targetAssignActiveGroupKey = ref('')
const filterPolicyAssignmentCheckedGroupKeys = ref<string[]>([])
const recoveryPlanCheckedGroupKeys = ref<string[]>([])
const batchPolicyId = ref('')
const batchFilterIds = ref<string[]>([])
const batchFilterSelectRef = ref<{ blur?: () => void }>()
const batchCompression = ref<CompressionLevel | ''>('')
type FilterPolicyBatchOperation = 'policy' | 'filter' | 'compression'
const filterPolicyBatchDialogOpen = ref(false)
const activeFilterPolicyBatchOperation = ref<FilterPolicyBatchOperation>('policy')
type HflPopoverInstance = InstanceType<typeof HflPopover>
const optionPopoverRefs = ref<HflPopoverInstance[]>([])
const configSelectMenuOpen = ref(false)
const nasTargetModes = reactive<Record<string, NasTargetMode>>({})
const sourcePolicyMap = ref<Record<string, string>>({})
const createRecoveryPlanMap = ref<Record<string, CreateRecoveryPlanConfig>>({})
const createRecoveryPlanValidationAttempted = ref(false)
const highlightedRecoveryDirPlanIds = ref<string[]>([])
const createRecoverySourcePathPickerVisible = reactive<Record<string, boolean>>({})
const createRecoveryDestPathPickerVisible = reactive<Record<string, boolean>>({})
const dismissedCreateRecoveryPathErrors = reactive<Record<string, string>>({})
const createRecoveryTargetSelectRefs = new Map<string, { blur?: () => void }>()
const addTargetOpen = ref(false)
const addTargetKind = ref<AddTargetRepoKind>('s3')
const addTargetSaving = ref(false)
const addTargetS3Step = ref<0 | 1>(0)
const addTargetNasStep = ref<0 | 1 | 2>(0)
const addTargetRepoName = ref('')
const addTargetS3Platform = ref<AddTargetS3Platform | undefined>(undefined)
const addTargetS3RegionPreset = ref<string | undefined>(undefined)
const addTargetS3Endpoint = ref('')
const addTargetS3Region = ref('')
const addTargetS3Bucket = ref('')
const addTargetS3BucketMode = ref<'existing' | 'new'>('existing')
const addTargetS3Prefix = ref(DEFAULT_S3_OBJECT_PREFIX)
const addTargetS3AccessKey = ref('')
const addTargetS3SecretKey = ref('')
const addTargetS3UrlStyle = ref<AddTargetS3UrlStyle>(defaultS3UrlStyle(undefined))
const addTargetS3UseTls = ref(true)
const addTargetS3CatalogProviders = ref<StorageProviderConfig[]>([])
const addTargetNasProtocol = ref<AddTargetNasProtocol>('smb')
const addTargetNasHost = ref('')
const addTargetNasShare = ref('')
const addTargetNasExport = ref('')
const addTargetNasUsername = ref('')
const addTargetNasPassword = ref('')
const addTargetNasMountOptions = ref('')
const addTargetNasProxyNodeId = ref<number | undefined>(undefined)
const addTargetProxyNodeId = ref<number | undefined>(undefined)
const addTargetProxyDir = ref('')
const addTargetProxyUseTree = ref(true)
const addTargetProxyDirTreeRef = ref<InstanceType<typeof ElTree>>()
const addTargetQuota = ref(0)
const addTargetEnableQuotaAlert = ref(false)
const addTargetQuotaAlertThreshold = ref(80)
const selPolicy = ref('')
const policySearch = ref('')
const filterSearch = ref('')
const selGlobalFilter = ref('')
const sourceFilterMap = ref<Record<string, string[]>>({})
const sourceCompressionMap = ref<Record<string, CompressionLevel>>({})
const addFilterOpen = ref(false)
const addPolicyCreateKind = ref<'backup' | 'filter'>('backup')
const addPolicyForm = ref<BackupPolicyForm>(createEmptyPolicyForm())
const addFileFilterForm = ref<FileFilterRuleForm>(createEmptyFileFilterForm())
const addPolicySaving = ref(false)
const createPhase = ref<'form' | 'waiting' | 'done'>('form')
const createBootstrapping = ref(true)
const editorWaitingText = computed(() => {
  if (createBootstrapping.value) {
    return hasEditRequest.value
      ? t('protection.backupsPage.loadingEditWizard')
      : t('protection.backupsPage.loadingCreateWizard')
  }
  if (!hasEditRequest.value) return t('protection.backupsPage.waitingCreate')
  if (activeEditSection.value === 'policy') return t('protection.backupsPage.waitingEditBackupPolicy')
  if (activeEditSection.value === 'recovery-plan') return t('protection.backupsPage.waitingEditRestorePlan')
  return t('protection.backupsPage.waitingEditBackupPaths')
})
const editorFailureText = computed(() =>
  hasEditRequest.value
    ? t('protection.backupsPage.editFailed')
    : t('protection.backupsPage.createFailed'),
)

function hideOptionPopovers() {
  for (const popover of optionPopoverRefs.value) {
    popover?.hide?.()
  }
}

function handleOptionSelectVisibleChange(visible: boolean) {
  if (!visible) hideOptionPopovers()
}

function handleConfigSelectVisibleChange(visible: boolean) {
  configSelectMenuOpen.value = visible
}

function closeBatchFilterSelect() {
  void nextTick(() => {
    batchFilterSelectRef.value?.blur?.()
    hideOptionPopovers()
  })
}

const filteredPoliciesForPick = computed(() => {
  const q = policySearch.value.trim().toLowerCase()
  if (!q) return realPolicies.value
  return realPolicies.value.filter((p) => policySearchText(p).includes(q))
})

watch(policySearch, () => {
  if (
    selPolicy.value &&
    !filteredPoliciesForPick.value.some((p) => p.id === selPolicy.value)
  ) {
    selPolicy.value = ''
  }
})

const filteredGlobalFiltersForPick = computed(() => {
  const q = filterSearch.value.trim().toLowerCase()
  if (!q) return realFilters.value
  return realFilters.value.filter((g) => filterSearchText(g).includes(q))
})

watch(filterSearch, () => {
  if (
    selGlobalFilter.value &&
    !filteredGlobalFiltersForPick.value.some((g) => g.id === selGlobalFilter.value)
  ) {
    selGlobalFilter.value = ''
  }
})

const addPolicyRetentionError = computed(() => validateRetentionForm(addPolicyForm.value, messageLocale.value))
const addPolicyDialogTitle = computed(() =>
  addPolicyCreateKind.value === 'filter'
    ? t('protection.policiesPage.dialogAddFilterTitle')
    : t('protection.policiesPage.dialogAddTitle'),
)
const addPolicyDialogDesc = computed(() =>
  addPolicyCreateKind.value === 'filter'
    ? t('protection.policiesPage.createTypeFilterSub')
    : t('protection.policiesPage.createTypeBackupSub'),
)
const addPolicySaveLabel = computed(() =>
  addPolicyCreateKind.value === 'filter'
    ? t('protection.policiesPage.btnSaveFilter')
    : t('protection.policiesPage.btnSavePolicy'),
)
const compressionOptions = computed<Array<{
  value: CompressionLevel
  title: string
  description: string
  tooltip: string
  icon: Component
}>>(() => [
  {
    value: 'none',
    title: t('protection.backupsPage.compressionNoneTitle'),
    description: t('protection.backupsPage.compressionNoneDescription'),
    tooltip: t('protection.backupsPage.compressionNoneTooltip'),
    icon: CircleOff,
  },
  {
    value: 'balanced',
    title: t('protection.backupsPage.compressionBalancedTitle'),
    description: t('protection.backupsPage.compressionBalancedDescription'),
    tooltip: t('protection.backupsPage.compressionBalancedTooltip'),
    icon: Scale,
  },
  {
    value: 'high',
    title: t('protection.backupsPage.compressionHighTitle'),
    description: t('protection.backupsPage.compressionHighDescription'),
    tooltip: t('protection.backupsPage.compressionHighTooltip'),
    icon: Archive,
  },
])
const repoTypeOptions = computed(() => {
  const set = new Set<string>()
  const otherLabel = t('protection.backupsPage.repoTypeOther')
  for (const tgt of selectableTargets.value) {
    const rt = tgt.repoType ?? otherLabel
    set.add(rt)
  }
  const preferredOrder = ['S3', 'NAS', 'PROXY_FS']
  const list = [...set]
  list.sort((a, b) => {
    const ai = preferredOrder.indexOf(a)
    const bi = preferredOrder.indexOf(b)
    if (ai !== -1 || bi !== -1) {
      if (ai === -1) return 1
      if (bi === -1) return -1
      return ai - bi
    }
    const collator = String(locale.value || 'en')
    return a.localeCompare(b, collator)
  })
  return list
})

const selectableTargets = computed(() =>
  realTargets.value.filter((target) => isSelectableTarget(target)),
)

const addTargetTypeOptions = computed(() => [
  {
    value: 's3' as const,
    label: t('protection.backupsPage.addTargetTypeS3'),
    desc: t('repositoriesPage.addS3PageDesc'),
    icon: CloudUpload,
  },
  {
    value: 'nas' as const,
    label: t('protection.backupsPage.addTargetTypeNas'),
    desc: t('repositoriesPage.addNasPageDesc'),
    icon: targetNasSidebarIcon,
  },
  {
    value: 'proxy_fs' as const,
    label: t('protection.backupsPage.addTargetTypeProxyFs'),
    desc: t('repositoriesPage.addProxyFsPageDesc'),
    icon: FolderTree,
  },
])

const addTargetProxyNodeOptions = computed(() =>
  proxyNodes.value.length > 0
    ? proxyNodes.value.map((node) => ({ id: node.id, name: node.name }))
    : [
        { id: -101, name: 'Proxy-GW-01' },
        { id: -102, name: 'Proxy-GW-02' },
      ],
)

const addTargetProxyNodeName = computed(() =>
  addTargetProxyNodeOptions.value.find((node) => node.id === addTargetProxyNodeId.value)?.name || '',
)

const addTargetNasProxyNodeName = computed(() =>
  addTargetProxyNodeOptions.value.find((node) => node.id === addTargetNasProxyNodeId.value)?.name || '',
)

const isAddTargetNasDirectAccess = computed(() => !addTargetNasProxyNodeId.value)

const addTargetNasAccessPathLabel = computed(() =>
  isAddTargetNasDirectAccess.value ? t('addNasRepo.accessPathDirect') : t('addNasRepo.accessPathWithProxy'),
)

const addTargetNextButtonLabel = computed(() =>
  addTargetKind.value === 'nas' && addTargetNasStep.value === 1 && !addTargetNasProxyNodeId.value
    ? t('addNasRepo.btnSkipProxy')
    : t('repositoriesPage.btnNext'),
)

const addTargetS3Regions = computed(() => {
  const provider = addTargetS3CatalogProviders.value.find((item) => item.id === addTargetS3Platform.value)
  return (provider?.regions || []).map((region) => ({
    key: region.id,
    label: region.display_name,
    endpoint: region.external_endpoint,
    region: region.id,
  }))
})

const addTargetS3PlatformOptions = computed(() => [
  ...addTargetS3CatalogProviders.value.map((provider) => ({
    value: provider.id,
    label: provider.display_name,
  })),
  ...S3_PROVIDER_OPTIONS.map((provider) => ({
    value: provider.value,
    label: provider.label || (provider.labelKey ? t(provider.labelKey) : provider.value),
  })),
])

const addTargetS3PlatformLabel = computed(() => {
  return addTargetS3PlatformOptions.value.find((item) => item.value === addTargetS3Platform.value)?.label || ''
})

const addTargetS3UrlStyleLabel = computed(() =>
  addTargetS3UrlStyle.value === 'auto'
    ? t('repositoriesPage.s3UrlStyleAuto')
    : addTargetS3UrlStyle.value === 'virtual_hosted'
      ? t('repositoriesPage.s3UrlStyleVirtualHosted')
      : t('repositoriesPage.s3UrlStylePath'),
)

const addTargetNasProtocolLabel = computed(() =>
  addTargetNasProtocol.value === 'smb' ? t('repositoriesPage.protocolSmb') : t('repositoriesPage.protocolNfs'),
)

const addTargetNasEndpoint = computed(() => {
  const host = addTargetNasHost.value.trim()
  if (!host) return ''
  if (addTargetNasProtocol.value === 'smb') {
    const share = addTargetNasShare.value.trim().replace(/^\/+/, '')
    return share ? `smb://${host}/${share}` : `smb://${host}`
  }
  return `nfs://${host}${addTargetNasExport.value.trim()}`
})

function normalizeAddTargetNodeName(nodeName: string) {
  return nodeName.toLowerCase().replace(/[^a-z0-9]/g, '-')
}

function createAddTargetTreeNode(id: string, label: string, children?: AddTargetDirTreeNode[]): AddTargetDirTreeNode {
  return { id, label, pathLabel: id, children }
}

function generateAddTargetProxyTreeData(nodeId: number | undefined): AddTargetDirTreeNode[] {
  if (!nodeId) return []
  const nodeName = addTargetProxyNodeOptions.value.find((node) => node.id === nodeId)?.name || 'proxy'
  const nodeSlug = normalizeAddTargetNodeName(nodeName)
  return [
    createAddTargetTreeNode(`/proxy-data/${nodeSlug}`, 'proxy-data', [
      createAddTargetTreeNode(`/proxy-data/${nodeSlug}/backup-repo`, 'backup-repo'),
      createAddTargetTreeNode(`/proxy-data/${nodeSlug}/archives`, 'archives', [
        createAddTargetTreeNode(`/proxy-data/${nodeSlug}/archives/2025`, '2025'),
        createAddTargetTreeNode(`/proxy-data/${nodeSlug}/archives/2026`, '2026'),
      ]),
      createAddTargetTreeNode(`/proxy-data/${nodeSlug}/snapshots`, 'snapshots'),
    ]),
    createAddTargetTreeNode(`/mnt/storage/${nodeSlug}`, 'mnt/storage', [
      createAddTargetTreeNode(`/mnt/storage/${nodeSlug}/repo-1`, 'repo-1'),
      createAddTargetTreeNode(`/mnt/storage/${nodeSlug}/repo-2`, 'repo-2'),
    ]),
  ]
}

function collectAddTargetTreeNodeIds(nodes: AddTargetDirTreeNode[]): string[] {
  return nodes.flatMap((node) => [node.id, ...(node.children?.length ? collectAddTargetTreeNodeIds(node.children) : [])])
}

const addTargetProxyTreeData = computed(() => generateAddTargetProxyTreeData(addTargetProxyNodeId.value))
const addTargetProxyTreeNodeIds = computed(() => collectAddTargetTreeNodeIds(addTargetProxyTreeData.value))
const addTargetProxyCurrentTreeNodeKey = computed(() =>
  addTargetProxyTreeNodeIds.value.includes(addTargetProxyDir.value) ? addTargetProxyDir.value : '',
)
const addTargetProxyCheckedTreeKeys = computed(() =>
  addTargetProxyTreeNodeIds.value.includes(addTargetProxyDir.value) ? [addTargetProxyDir.value] : [],
)

function sourceGroupKey(entry: Pick<BackupDirEntry, 'sourceType' | 'sourceId' | 'backupConfigId'>) {
  if (entry.backupConfigId) return `config:${entry.backupConfigId}`
  return `${entry.sourceType}:${entry.sourceId}`
}

const wizardSourceGroups = computed<WizardSourceGroup[]>(() => {
  const groups = new Map<string, WizardSourceGroup>()
  for (const entry of wizardDirEntries.value) {
    const key = sourceGroupKey(entry)
    const existing = groups.get(key)
    if (existing) {
      existing.entries.push(entry)
      continue
    }
    groups.set(key, {
      key,
      backupConfigId: entry.backupConfigId,
      sourceId: entry.sourceId,
      sourceName: entry.sourceName,
      sourceType: entry.sourceType,
      platform: entry.platform,
      entries: [entry],
    })
  }
  return [...groups.values()]
})

const createRecoveryTargetOptions = computed(() =>
  restoreTargetCatalog.rows.value
    .filter((source) => restoreTargetCatalog.isSelectable(source.id))
    .map(mapSelectableSource)
)

const createRecoveryPlanGroups = computed(() =>
  wizardSourceGroups.value.map((group) => ({
    ...group,
    plan: recoveryPlanForGroup(group),
  })),
)

function backupSourceTypeLabel(type: 'host' | 'nas') {
  return type === 'host'
    ? t('protection.backupsPage.sourceTypeHost')
    : t('protection.backupsPage.sourceTypeNas')
}

function sourceGroupMatchesSearch(group: WizardSourceGroup, keyword: string) {
  const normalized = keyword.trim().toLowerCase()
  if (!normalized) return true
  const typeLabel = backupSourceTypeLabel(group.sourceType)
  return [
    group.sourceName,
    group.sourceType,
    typeLabel,
    ...group.entries.map((entry) => entry.path),
  ].some((value) => value.toLowerCase().includes(normalized))
}

const filteredFilterPolicyGroups = computed(() =>
  wizardSourceGroups.value.filter((group) => sourceGroupMatchesSearch(group, appliedFilterPolicySourceSearch.value)),
)

const filteredTargetGroups = computed(() =>
  wizardSourceGroups.value.filter((group) => sourceGroupMatchesSearch(group, appliedTargetSourceSearch.value)),
)

function targetIncompatibilityReasonForGroup(
  target: WizardTarget | null | undefined,
  group: WizardSourceGroup | null | undefined,
): BackupTargetIncompatibilityReason | null {
  if (!target || !group) return null
  return backupTargetIncompatibilityReason(
    targetCompatibilitySourceForGroup(group),
    target,
  )
}

function targetCompatibilitySourceForGroup(group: WizardSourceGroup) {
  const source = realSourceById.value.get(group.sourceId)
  return {
    sourceType: group.sourceType,
    boundNodeId: source?.boundNodeId,
    platform: source?.platform ?? group.platform,
  }
}

function targetIncompatibilityMessage(reason: BackupTargetIncompatibilityReason) {
  return reason === 'direct_nas_linux_only'
    ? t('protection.backupsPage.targetDirectNasLinuxOnly')
    : t('protection.backupsPage.targetCrossProxyUnavailable')
}

function targetIncompatibilityMessageItems(reason: BackupTargetIncompatibilityReason) {
  const prefix = reason === 'direct_nas_linux_only'
    ? 'targetDirectNasLinuxOnlyItem'
    : 'targetCrossProxyUnavailableItem'
  return [1, 2, 3].map((index) => t(`protection.backupsPage.${prefix}${index}`))
}

function targetOptionsForGroup(group: WizardSourceGroup | null | undefined) {
  return filterTargetsByCriteria(batchTargetPicker.search, batchTargetPicker.repoType)
    .map((target) => {
      const reason = targetIncompatibilityReasonForGroup(target, group)
      return {
        ...target,
        disabled: Boolean(reason),
        disabledReason: reason ? targetIncompatibilityMessage(reason) : null,
        disabledReasonItems: reason ? targetIncompatibilityMessageItems(reason) : [],
      }
    })
}

function targetOptionsForCheckedGroups() {
  const groups = checkedTargetGroups.value
  return filterTargetsByCriteria(batchTargetPicker.search, batchTargetPicker.repoType)
    .map((target) => {
      const reason = backupTargetIncompatibilityReasonForSources(
        groups.map(targetCompatibilitySourceForGroup),
        target,
      )
      const compatible = groups.length > 0 && !reason
      return {
        ...target,
        disabled: !compatible,
        disabledReason: compatible
          ? null
          : targetIncompatibilityMessage(reason ?? 'cross_proxy_unavailable'),
        disabledReasonItems: compatible
          ? []
          : targetIncompatibilityMessageItems(reason ?? 'cross_proxy_unavailable'),
      }
    })
}

const filteredRecoveryPlanGroups = computed(() =>
  createRecoveryPlanGroups.value.filter((group) => sourceGroupMatchesSearch(group, appliedRecoveryPlanSourceSearch.value)),
)

function defaultRecoveryPlanForGroup(group: WizardSourceGroup): CreateRecoveryPlanConfig {
  return {
    enabled: false,
    conflictMode: '',
    dirPlans: [createDefaultRecoveryDirPlan(group)],
  }
}

function createDefaultRecoveryDirPlan(group: WizardSourceGroup): CreateRecoveryDirPlanConfig {
  const sourceTargetId = createRecoverySourceTargetAvailable(group) ? group.sourceId : ''
  const fallbackTargetId = sourceTargetId || createRecoveryTargetOptions.value[0]?.id || ''
  return {
    id: `recovery-dir-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    sourcePath: WHOLE_SNAPSHOT_RECOVERY_PATH,
    sourcePathType: 'directory',
    sourcePathValidation: 'valid',
    sourcePathError: '',
    targetMode: sourceTargetId ? 'original' : fallbackTargetId ? 'new' : '',
    targetHostId: fallbackTargetId,
    restoreDir: '',
    restoreDirError: '',
  }
}

function createEmptyRecoveryDirPlan(group: WizardSourceGroup): CreateRecoveryDirPlanConfig {
  const sourceTargetId = createRecoverySourceTargetAvailable(group) ? group.sourceId : ''
  return {
    id: `recovery-dir-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    sourcePath: '',
    sourcePathType: 'unknown',
    sourcePathValidation: undefined,
    sourcePathError: '',
    targetMode: sourceTargetId ? 'original' : '',
    targetHostId: sourceTargetId,
    restoreDir: '',
    restoreDirError: '',
  }
}

function isWholeSnapshotRecoveryPath(path: string) {
  return path === WHOLE_SNAPSHOT_RECOVERY_PATH
}

function wholeSnapshotRecoveryLabel() {
  return t('protection.backupsPage.createRecoveryScopeSnapshot')
}

function recoverySourcePathLabel(path: string) {
  if (isWholeSnapshotRecoveryPath(path)) return wholeSnapshotRecoveryLabel()
  return path || '—'
}

function recoverySourcePathInputValue(path: string) {
  if (isWholeSnapshotRecoveryPath(path)) return wholeSnapshotRecoveryLabel()
  return path
}

function createRecoverySourcePathInputIcon(dirPlan: CreateRecoveryDirPlanConfig) {
  if (dirPlan.sourcePathValidation !== 'valid' || !dirPlan.sourcePath) return TextCursorInput
  if (isWholeSnapshotRecoveryPath(dirPlan.sourcePath)) return Camera
  return dirPlan.sourcePathType === 'file' ? FileIcon : FolderOpen
}

function createRecoverySourcePathInputIconClass(dirPlan: CreateRecoveryDirPlanConfig) {
  if (dirPlan.sourcePathValidation !== 'valid' || !dirPlan.sourcePath) return ''
  if (isWholeSnapshotRecoveryPath(dirPlan.sourcePath)) return 'create-dir-row__icon--snapshot'
  return dirPlan.sourcePathType === 'file' ? 'create-dir-row__icon--file' : 'create-dir-row__icon--folder'
}

function createRecoveryDestPathInputIcon(dirPlan: CreateRecoveryDirPlanConfig) {
  return dirPlan.restoreDirValidation === 'valid' && dirPlan.restoreDir ? FolderOpen : TextCursorInput
}

function createRecoveryDestPathInputIconClass(dirPlan: CreateRecoveryDirPlanConfig) {
  return dirPlan.restoreDirValidation === 'valid' && dirPlan.restoreDir ? 'create-dir-row__icon--folder' : ''
}

function isCreateRecoverySourcePathInputInvalid(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  return dirPlan.sourcePathValidation === 'invalid' || isCreateRecoveryDirPlanFieldInvalid(group, dirPlan, 'sourcePath')
}

function isCreateRecoveryDestPathInputInvalid(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  return dirPlan.restoreDirValidation === 'invalid' || isCreateRecoveryDirPlanFieldInvalid(group, dirPlan, 'restoreDir')
}

function createRecoverySourcePathInputError(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  if (!isCreateRecoverySourcePathInputInvalid(group, dirPlan)) return ''
  if (dirPlan.sourcePathError) return dirPlan.sourcePathError
  if (!dirPlan.sourcePath) return t('protection.backupsPage.msgManualPathRequired')
  if (dirPlan.sourcePathValidation === 'pending') return t('protection.backupsPage.createRecoveryPathInputHint')
  return t('protection.backupsPage.msgRestoreScopeVerifyFailed')
}

function createRecoveryDestPathInputError(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  if (!isCreateRecoveryDestPathInputInvalid(group, dirPlan)) return ''
  if (dirPlan.restoreDirError) return dirPlan.restoreDirError
  if (!dirPlan.restoreDir) return t('protection.backupsPage.msgRestoreDirectoryRequired')
  if (dirPlan.restoreDirValidation === 'pending') return t('protection.backupsPage.createRecoveryPathInputHint')
  return t('protection.backupsPage.msgRestoreDirectoryVerifyFailed')
}

function createRecoveryPathErrorNoticeKey(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig, kind: 'source' | 'dest') {
  return recoveryDirPlanPickerKey(group, dirPlan, kind)
}

function isCreateRecoverySourcePathErrorVisible(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  const message = createRecoverySourcePathInputError(group, dirPlan)
  return Boolean(message) && dismissedCreateRecoveryPathErrors[createRecoveryPathErrorNoticeKey(group, dirPlan, 'source')] !== message
}

function isCreateRecoveryDestPathErrorVisible(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  const message = createRecoveryDestPathInputError(group, dirPlan)
  return Boolean(message) && dismissedCreateRecoveryPathErrors[createRecoveryPathErrorNoticeKey(group, dirPlan, 'dest')] !== message
}

function dismissCreateRecoveryPathError(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig, kind: 'source' | 'dest') {
  const message = kind === 'source'
    ? createRecoverySourcePathInputError(group, dirPlan)
    : createRecoveryDestPathInputError(group, dirPlan)
  if (message) dismissedCreateRecoveryPathErrors[createRecoveryPathErrorNoticeKey(group, dirPlan, kind)] = message
}

function restoreCreateRecoveryPathErrorNotice(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig, kind: 'source' | 'dest') {
  delete dismissedCreateRecoveryPathErrors[createRecoveryPathErrorNoticeKey(group, dirPlan, kind)]
}

function recoverySourcePathsForPayload(path: string, group: WizardSourceGroup) {
  if (isWholeSnapshotRecoveryPath(path)) {
    return group.entries.map((entry) => entry.path).filter(Boolean)
  }
  return path ? [path] : []
}

function recoveryPlanForGroup(group: WizardSourceGroup) {
  const current = createRecoveryPlanMap.value[group.key]
  if (current) return current
  const fallback = defaultRecoveryPlanForGroup(group)
  createRecoveryPlanMap.value = { ...createRecoveryPlanMap.value, [group.key]: fallback }
  return fallback
}

function updateRecoveryPlanForGroup(group: WizardSourceGroup, patch: Partial<CreateRecoveryPlanConfig>) {
  const current = recoveryPlanForGroup(group)
  const next = { ...current, ...patch }
  if (patch.enabled && next.dirPlans.length === 0) {
    next.dirPlans = [createDefaultRecoveryDirPlan(group)]
  }
  createRecoveryPlanMap.value = { ...createRecoveryPlanMap.value, [group.key]: next }
}

function onCreateRecoveryPlanEnabledChange(group: WizardSourceGroup, enabled: boolean) {
  updateRecoveryPlanForGroup(group, { enabled })
  if (enabled) {
    createRecoveryPlanExpandedKeys.value = [...new Set([...createRecoveryPlanExpandedKeys.value, group.key])]
    nextTick(() => createRecoveryPlanTableRef.value?.doLayout?.())
    return
  }
  createRecoveryPlanExpandedKeys.value = createRecoveryPlanExpandedKeys.value.filter((key) => key !== group.key)
}

function updateRecoveryPlanConflictMode(group: WizardSourceGroup, value: string | number | boolean | undefined) {
  updateRecoveryPlanForGroup(group, {
    conflictMode: value === 'overwrite' ? 'overwrite' : value === 'skip' ? 'skip' : '',
  })
}

function addRecoveryDirPlan(group: WizardSourceGroup) {
  const plan = recoveryPlanForGroup(group)
  updateRecoveryPlanForGroup(group, {
    dirPlans: [...plan.dirPlans, createEmptyRecoveryDirPlan(group)],
  })
}

function removeRecoveryDirPlan(group: WizardSourceGroup, dirPlanId: string) {
  const plan = recoveryPlanForGroup(group)
  updateRecoveryPlanForGroup(group, {
    dirPlans: plan.dirPlans.filter((dirPlan) => dirPlan.id !== dirPlanId),
  })
}

function updateRecoveryDirPlan(
  group: WizardSourceGroup,
  dirPlan: CreateRecoveryDirPlanConfig,
  patch: Partial<CreateRecoveryDirPlanConfig>,
) {
  const plan = recoveryPlanForGroup(group)
  const nextDirPlan = { ...dirPlan, ...patch }
  if (patch.targetMode === 'original') {
    nextDirPlan.targetHostId = group.sourceId
  }
  updateRecoveryPlanForGroup(group, {
    dirPlans: plan.dirPlans.map((item) => (item.id === dirPlan.id ? nextDirPlan : item)),
  })
}

type CreateRecoveryTargetSummary = {
  sourceType: 'host' | 'nas'
  displayName: string
  ipLine: string
  typeLabel: string
  typeTagType: 'primary' | 'warning'
  platform?: EnrollmentOs
}

type CreateRecoveryTargetOption = {
  value: string
  label: string
  ipLabel: string
  typeLabel: string
  isSource: boolean
  selectable: boolean
  unavailableReason: 'offline' | 'busy' | null
  summary: CreateRecoveryTargetSummary
}

function createRecoveryTargetSummary(source: RealSourceRow | null | undefined, fallback: Pick<WizardSourceGroup, 'sourceId' | 'sourceName' | 'sourceType' | 'platform'>): CreateRecoveryTargetSummary {
  const sourceType = source?.type ?? fallback.sourceType
  const displayName = source?.name || fallback.sourceName || store.getNodeName(fallback.sourceId)
  const ipLine = source?.nodeIp || source?.hostname || '—'
  return {
    sourceType,
    displayName,
    ipLine,
    typeLabel: sourceType === 'nas'
      ? t('protection.backupsPage.sourceTypeNas')
      : t('protection.backupsPage.sourceTypeHost'),
    typeTagType: sourceType === 'nas' ? 'warning' : 'primary',
    platform: source?.platform ?? fallback.platform,
  }
}

function createRecoveryTargetOptionFromSource(source: RealSourceRow, group: WizardSourceGroup): CreateRecoveryTargetOption {
  const summary = createRecoveryTargetSummary(source, group)
  return {
    value: source.id,
    label: summary.displayName,
    ipLabel: summary.ipLine,
    typeLabel: summary.typeLabel,
    isSource: source.id === group.sourceId,
    selectable: restoreTargetCatalog.isSelectable(source.id),
    unavailableReason: source.availability !== 'online' ? 'offline' : restoreTargetCatalog.isSelectable(source.id) ? null : 'busy',
    summary,
  }
}

function createRecoverySourceTargetAvailable(group: WizardSourceGroup) {
  return restoreTargetCatalog.isSelectable(group.sourceId)
}

function createRecoverySourceTargetActionValue(group: WizardSourceGroup) {
  return `__restore_to_source__:${group.key}`
}

function isCreateRecoverySourceTargetActionValue(group: WizardSourceGroup, value: string) {
  return value === createRecoverySourceTargetActionValue(group)
}

function isCreateRecoverySourceTargetSelected(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  return dirPlan.targetHostId === group.sourceId
}

function createRecoveryTargetOptionsForGroup(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig): CreateRecoveryTargetOption[] {
  return restoreTargetCatalog.options([group.sourceId, dirPlan.targetHostId])
    .map(({ source }) => createRecoveryTargetOptionFromSource(mapSelectableSource(source), group))
}

function onCreateRecoveryTargetVisible(visible: boolean) {
  if (visible && !restoreTargetCatalog.rows.value.length && !recoveryTargetLoading.value) {
    void restoreTargetCatalog.reset()
  }
}

function onCreateRecoveryTargetPopupScroll(event: Event | { scrollTop?: number; clientHeight?: number; scrollHeight?: number }) {
  const target = (event instanceof Event ? event.target : event) as HTMLElement | null
  if (!target) return
  if (target.scrollHeight - target.scrollTop - target.clientHeight <= 48) void restoreTargetCatalog.loadMore()
}

function selectCreateRecoverySourceTarget(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  if (!createRecoverySourceTargetAvailable(group)) return
  updateRecoveryDirPlan(group, dirPlan, {
    targetHostId: group.sourceId,
    targetMode: 'original',
    restoreDir: '',
    restoreDirValidation: undefined,
  })
}

function setCreateRecoveryTargetSelectRef(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig, el: unknown) {
  const key = recoveryDirPlanPickerKey(group, dirPlan, 'target')
  if (el && typeof el === 'object') {
    createRecoveryTargetSelectRefs.set(key, el as { blur?: () => void })
    return
  }
  createRecoveryTargetSelectRefs.delete(key)
}

function closeCreateRecoveryTargetSelect(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  const selectRef = createRecoveryTargetSelectRefs.get(recoveryDirPlanPickerKey(group, dirPlan, 'target'))
  selectRef?.blur?.()
}

function selectCreateRecoverySourceTargetAndClose(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  selectCreateRecoverySourceTarget(group, dirPlan)
  void nextTick(() => closeCreateRecoveryTargetSelect(group, dirPlan))
}

function onCreateRecoveryTargetHostChange(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig, value: string | number | boolean | undefined) {
  const targetHostId = String(value || '')
  if (isCreateRecoverySourceTargetActionValue(group, targetHostId)) {
    selectCreateRecoverySourceTarget(group, dirPlan)
    return
  }
  updateRecoveryDirPlan(group, dirPlan, {
    targetHostId,
    targetMode: targetHostId === group.sourceId ? 'original' : targetHostId ? 'new' : '',
    restoreDir: '',
    restoreDirValidation: undefined,
    restoreDirError: '',
  })
}

function recoveryDirPlanTargetName(dirPlan: CreateRecoveryDirPlanConfig) {
  return store.getNodeName(dirPlan.targetHostId) || dirPlan.targetHostId || '—'
}

function recoveryPlanConflictLabel(conflictMode: CreateRecoveryPlanConfig['conflictMode']) {
  if (!conflictMode) return t('protection.backupsPage.fileConflictPolicyRequiredShort')
  return conflictMode === 'overwrite'
    ? t('protection.backupsPage.createRecoveryConflictOverwriteFull')
    : t('protection.backupsPage.createRecoveryConflictSkipFull')
}

/** Compact list chip label — full sentence stays on confirm, dropdowns, and tooltips. */
function recoveryPlanConflictSummary(group: WizardSourceGroup) {
  const plan = recoveryPlanForGroup(group)
  if (!plan.conflictMode) return t('protection.backupsPage.fileConflictPolicyRequiredShort')
  return plan.conflictMode === 'overwrite'
    ? t('protection.backupsPage.createRecoveryConflictOverwrite')
    : t('protection.backupsPage.createRecoveryConflictSkip')
}

function recoveryDirPlanMissingFields(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  const missing: CreateRecoveryDirPlanField[] = []
  if (!dirPlan.sourcePath || dirPlan.sourcePathValidation === 'pending' || dirPlan.sourcePathValidation === 'invalid') {
    missing.push('sourcePath')
  }
  if (!dirPlan.targetHostId || !restoreTargetCatalog.isSelectable(dirPlan.targetHostId)) missing.push('targetHostId')
  if (!dirPlan.restoreDir || dirPlan.restoreDirValidation === 'pending' || dirPlan.restoreDirValidation === 'invalid') {
    missing.push('restoreDir')
  }
  return missing
}

function recoveryPlanMissingFields(group: WizardSourceGroup) {
  const plan = recoveryPlanForGroup(group)
  const missing: CreateRecoveryPlanField[] = []
  if (!plan.conflictMode) missing.push('conflictMode')
  return missing
}

function isRecoveryDirPlanComplete(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  return recoveryDirPlanMissingFields(group, dirPlan).length === 0
}

function isRecoveryPlanComplete(group: WizardSourceGroup) {
  const plan = recoveryPlanForGroup(group)
  if (!plan.enabled) return true
  return recoveryPlanMissingFields(group).length === 0
    && plan.dirPlans.length > 0
    && plan.dirPlans.every((dirPlan) => isRecoveryDirPlanComplete(group, dirPlan))
}

function recoveryDirPlanPendingLabel() {
  return '—'
}

function recoveryDirPlanFieldLabel(field: CreateRecoveryDirPlanField) {
  const labelMap: Record<CreateRecoveryDirPlanField, string> = {
    sourcePath: t('protection.backupsPage.createRecoveryPlanScopeCol'),
    targetHostId: t('protection.backupsPage.createRecoveryPlanTargetHostCol'),
    restoreDir: t('protection.backupsPage.createRecoveryPlanTargetDirCol'),
  }
  return labelMap[field]
}

function recoveryPlanFieldLabel(field: CreateRecoveryPlanField) {
  if (field === 'conflictMode') return t('protection.backupsPage.fileConflictPolicyLabel')
  return recoveryDirPlanFieldLabel(field)
}

function isCreateRecoveryDirPlanFieldInvalid(
  group: WizardSourceGroup,
  dirPlan: CreateRecoveryDirPlanConfig,
  field: CreateRecoveryDirPlanField,
) {
  return createRecoveryPlanValidationAttempted.value && recoveryDirPlanMissingFields(group, dirPlan).includes(field)
}

function isCreateRecoveryConflictPolicyInvalid(group: WizardSourceGroup) {
  return createRecoveryPlanValidationAttempted.value && recoveryPlanMissingFields(group).includes('conflictMode')
}

function recoveryPlanConflictIssue(group: WizardSourceGroup) {
  return recoveryPlanMissingFields(group).includes('conflictMode')
    ? t('protection.backupsPage.fileConflictPolicyRequiredInline')
    : ''
}

function recoveryPlanIncompleteReason(group: WizardSourceGroup) {
  const plan = recoveryPlanForGroup(group)
  if (!plan.enabled || isRecoveryPlanComplete(group)) return ''
  const conflictIssue = recoveryPlanConflictIssue(group)
  if (conflictIssue) return conflictIssue

  for (const dirPlan of plan.dirPlans) {
    const dirMissing = recoveryDirPlanMissingFields(group, dirPlan)
    if (dirMissing.includes('restoreDir')) {
      return t('protection.backupsPage.recoveryPlanIncompleteRestoreDir')
    }
    if (dirMissing.includes('targetHostId')) {
      return t('protection.backupsPage.recoveryPlanIncompleteRestoreTarget')
    }
    if (dirMissing.includes('sourcePath')) {
      return t('protection.backupsPage.recoveryPlanIncompleteRestoreScope')
    }
  }

  return ''
}

function incompleteRecoveryPlans() {
  const incompletePlans: Array<{
    group: WizardSourceGroup
    dirPlan: CreateRecoveryDirPlanConfig
    dirPlanIndex: number
    missingFields: CreateRecoveryPlanField[]
  }> = []
  for (const group of createRecoveryPlanGroups.value) {
    if (!group.plan.enabled) continue
    const planMissingFields = recoveryPlanMissingFields(group)
    let planFieldsAttached = false
    group.plan.dirPlans.forEach((dirPlan, dirPlanIndex) => {
      const missingFields: CreateRecoveryPlanField[] = [
        ...(!planFieldsAttached ? planMissingFields : []),
        ...recoveryDirPlanMissingFields(group, dirPlan),
      ]
      if (missingFields.length > 0) {
        incompletePlans.push({ group, dirPlan, dirPlanIndex, missingFields })
        planFieldsAttached = planFieldsAttached || planMissingFields.length > 0
      }
    })
  }
  return incompletePlans
}

function recoveryPlanIncompleteSummary(
  incomplete: ReturnType<typeof incompleteRecoveryPlans>[number],
) {
  const missingLabels = incomplete.missingFields.map(recoveryPlanFieldLabel).join(t('protection.backupsPage.listSeparator'))
  return t('protection.backupsPage.recoveryPlanIncompleteListItem', {
    source: incomplete.group.sourceName,
    index: incomplete.dirPlanIndex + 1,
    fields: missingLabels,
  })
}

function focusIncompleteRecoveryPlan() {
  const incompletePlans = incompleteRecoveryPlans()
  const first = incompletePlans[0]
  if (!first) {
    createRecoveryPlanValidationAttempted.value = false
    highlightedRecoveryDirPlanIds.value = []
    closeValidationPopover()
    return false
  }

  createRecoveryPlanValidationAttempted.value = true
  highlightedRecoveryDirPlanIds.value = incompletePlans.map((item) => item.dirPlan.id)
  closeValidationPopover()
  createRecoveryPlanExpandedKeys.value = [
    ...new Set([...createRecoveryPlanExpandedKeys.value, ...incompletePlans.map((item) => item.group.key)]),
  ]
  void nextTick(() => {
    createRecoveryPlanTableRef.value?.doLayout?.()
    const row = document.querySelector<HTMLElement>(`[data-recovery-dir-plan-id="${first.dirPlan.id}"]`)
    row?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' })
  })

  return true
}



function recoveryPlanStatusTone(group: WizardSourceGroup) {
  const plan = recoveryPlanForGroup(group)
  if (!plan.enabled) return 'disabled'
  if (!isRecoveryPlanComplete(group)) return 'pending'
  return 'enabled'
}

function recoveryPlanStatusLabel(group: WizardSourceGroup) {
  const plan = recoveryPlanForGroup(group)
  if (!plan.enabled) return t('protection.backupsPage.createRecoveryPlanDisabled')
  if (!isRecoveryPlanComplete(group)) return t('protection.backupsPage.createRecoveryPlanPending')
  if (plan.dirPlans.some((dirPlan) => isWholeSnapshotRecoveryPath(dirPlan.sourcePath))) {
    return wholeSnapshotRecoveryLabel()
  }
  return t('protection.backupsPage.recoveryPlanEnabledPathCount', { n: plan.dirPlans.length })
}

function recoveryPlanPrimaryPathSummary(group: WizardSourceGroup) {
  const plan = recoveryPlanForGroup(group)
  if (!plan.enabled) return t('protection.backupsPage.recoveryPlanDisabledHint')
  const first = plan.dirPlans[0]
  if (!first) return recoveryDirPlanPendingLabel()
  return isRecoveryDirPlanComplete(group, first)
    ? recoveryDirPlanSummaryLine(group, first)
    : recoveryDirPlanPendingLabel()
}

function recoveryPlanExtraCount(group: WizardSourceGroup) {
  const plan = recoveryPlanForGroup(group)
  return plan.enabled ? Math.max(plan.dirPlans.length - RECOVERY_PLAN_MAPPING_PREVIEW_MAX, 0) : 0
}

function recoveryPlanPreviewDirPlans(group: WizardSourceGroup) {
  return recoveryPlanForGroup(group).dirPlans.slice(0, RECOVERY_PLAN_MAPPING_PREVIEW_MAX)
}

function recoveryDirPlanSummaryLine(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  if (!isRecoveryDirPlanComplete(group, dirPlan)) return recoveryDirPlanPendingLabel()
  return `${recoverySourcePathLabel(dirPlan.sourcePath)} → ${recoveryDirPlanTargetSummary(dirPlan)}`
}

function recoveryDirPlanTargetSummary(dirPlan: CreateRecoveryDirPlanConfig) {
  const targetHost = realSourceById.value.get(dirPlan.targetHostId)
  const targetName = targetHost?.name || recoveryDirPlanTargetName(dirPlan)
  const targetIp = targetHost?.nodeIp || ''
  const targetLabel = targetIp ? `${targetName} ${targetIp}` : targetName
  return `${dirPlan.restoreDir || '—'} (${targetLabel})`
}

function recoveryDirPlanSourceIconName(dirPlan: CreateRecoveryDirPlanConfig) {
  if (isWholeSnapshotRecoveryPath(dirPlan.sourcePath)) return 'snapshot'
  return dirPlan.sourcePathType === 'file' ? 'file' : 'folder'
}

type CreateRecoveryPlanTableRow = WizardSourceGroup & { plan: CreateRecoveryPlanConfig }

function onCreateRecoveryPlanExpandChange(_row: CreateRecoveryPlanTableRow, expandedRows: CreateRecoveryPlanTableRow[]) {
  if (!_row.plan.enabled && expandedRows.some((row) => row.key === _row.key)) {
    ElMessage.warning({ message: t('protection.backupsPage.recoveryPlanEnableBeforeConfigure'), grouping: true })
  }
  const visibleKeys = new Set(createRecoveryPlanGroups.value.map((group) => group.key))
  const hiddenExpandedKeys = createRecoveryPlanExpandedKeys.value.filter((key) => !visibleKeys.has(key))
  createRecoveryPlanExpandedKeys.value = [
    ...hiddenExpandedKeys,
    ...expandedRows.filter((row) => row.plan.enabled).map((row) => row.key),
  ]
}

function toggleCreateRecoveryPlanRow(group: CreateRecoveryPlanTableRow) {
  createRecoveryPlanTableRef.value?.toggleRowExpansion(group)
}

function requestToggleCreateRecoveryPlanRow(group: CreateRecoveryPlanTableRow) {
  if (!group.plan.enabled) {
    ElMessage.warning({ message: t('protection.backupsPage.recoveryPlanEnableBeforeConfigure'), grouping: true })
    return
  }
  toggleCreateRecoveryPlanRow(group)
}

function recoveryDirPlanPickerKey(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig, kind: 'source' | 'target' | 'dest') {
  return `${group.key}::${dirPlan.id}::${kind}`
}

function setCreateRecoverySourcePathPickerVisible(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig, visible: boolean) {
  const sourceKey = recoveryDirPlanPickerKey(group, dirPlan, 'source')
  createRecoverySourcePathPickerVisible[sourceKey] = visible
  if (visible) {
    createRecoveryDestPathPickerVisible[recoveryDirPlanPickerKey(group, dirPlan, 'dest')] = false
  }
}

function setCreateRecoveryDestPathPickerVisible(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig, visible: boolean) {
  const destKey = recoveryDirPlanPickerKey(group, dirPlan, 'dest')
  createRecoveryDestPathPickerVisible[destKey] = visible
  if (visible) {
    createRecoverySourcePathPickerVisible[recoveryDirPlanPickerKey(group, dirPlan, 'source')] = false
  }
}

function isCreateRecoverySourcePathPickerVisible(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  return Boolean(createRecoverySourcePathPickerVisible[recoveryDirPlanPickerKey(group, dirPlan, 'source')])
}

function isCreateRecoveryDestPathPickerVisible(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  return Boolean(createRecoveryDestPathPickerVisible[recoveryDirPlanPickerKey(group, dirPlan, 'dest')])
}

function setDirectoryLoading(key: string, loading: boolean) {
  if (!key) return
  const current = directoryLoadingByKey[key] ?? 0
  if (loading) {
    directoryLoadingByKey[key] = current + 1
    return
  }
  const next = Math.max(0, current - 1)
  if (next === 0) {
    delete directoryLoadingByKey[key]
  } else {
    directoryLoadingByKey[key] = next
  }
}

function isDirectoryLoading(key: string) {
  return (directoryLoadingByKey[key] ?? 0) > 0
}

function createSourceDirectoryLoadingKey(sourceId: string) {
  return `create-source:${sourceId}`
}

function createRecoverySourceRootNodes(group: WizardSourceGroup): DemoDirTreeItem[] {
  const seen = new Set<string>()
  const dirNodes = group.entries
    .filter((entry) => {
      if (!entry.path || seen.has(entry.path)) return false
      seen.add(entry.path)
      return true
    })
    .map((entry) => ({
      label: basenamePath(entry.path),
      path: entry.path,
      isLeaf: entry.pathType === 'file',
      path_type: entry.pathType,
    }))
  return [
    {
      label: wholeSnapshotRecoveryLabel(),
      path: WHOLE_SNAPSHOT_RECOVERY_PATH,
      isLeaf: true,
    },
    ...dirNodes,
  ]
}

function createRecoverySourceTreePathLabel(data: DemoDirTreeItem) {
  if (isWholeSnapshotRecoveryPath(data.path)) return t('protection.backupsPage.createRecoveryScopeSnapshotDesc')
  return data.path
}

async function loadCreateRecoverySourceTreeNode(
  group: WizardSourceGroup,
  node: { level: number; data?: SourceTreeItem },
  resolve: (data: DemoDirTreeItem[]) => void,
  loadingKey = '',
) {
  if (node.level === 0) {
    resolve(createRecoverySourceRootNodes(group))
    return
  }
  const parentPath = String(node.data?.path ?? '')
  setDirectoryLoading(loadingKey, true)
  try {
    const page = await listRealSourceDirChildren(group.sourceId, parentPath, { includeFiles: true }).catch((err) => {
      ElMessage.error({ message: apiErrorMessageI18n(err, t, t('protection.backupsPage.dirTreeLoadFailed')), grouping: true })
      return { entries: [], hasMore: false, nextCursor: '', limit: SOURCE_TREE_CHILD_LIMIT } as SourceTreePage
    })
    resolve(page.entries)
  } finally {
    setDirectoryLoading(loadingKey, false)
  }
}

async function loadCreateRecoveryDestTreeNode(
  dirPlan: CreateRecoveryDirPlanConfig,
  node: { level: number; data?: DemoDirTreeItem },
  resolve: (data: DemoDirTreeItem[]) => void,
  loadingKey = '',
) {
  const targetHostId = dirPlan.targetHostId
  if (!targetHostId) {
    resolve([])
    return
  }
  const parentPath = node.level === 0 ? '' : String(node.data?.path ?? '')
  setDirectoryLoading(loadingKey, true)
  try {
    const page = await listRealSourceDirChildren(restoreDirectoryBrowseSourceId(targetHostId), parentPath, { includeFiles: false }).catch((err) => {
      ElMessage.error({ message: apiErrorMessageI18n(err, t, t('protection.backupsPage.dirTreeLoadFailed')), grouping: true })
      return { entries: [], hasMore: false, nextCursor: '', limit: SOURCE_TREE_CHILD_LIMIT } as SourceTreePage
    })
    resolve(page.entries)
  } finally {
    setDirectoryLoading(loadingKey, false)
  }
}

function setCreateRecoveryDirectoryTreeRef(treeKey: string, el: unknown) {
  if (el) {
    createRecoveryDirectoryTreeRefs.set(treeKey, el as InstanceType<typeof ElTree>)
  } else {
    createRecoveryDirectoryTreeRefs.delete(treeKey)
  }
}

function createRecoveryDirectoryRefreshKey(treeKey: string, path: string) {
  return createSourceDirectoryRefreshKey(`recovery:${treeKey}`, path)
}

function isCreateRecoveryDirectoryRefreshing(treeKey: string, path: string) {
  return Boolean(refreshingSourceDirectoryByKey[createRecoveryDirectoryRefreshKey(treeKey, path)])
}

function onCreateRecoveryDirectoryExpansionChange(treeKey: string, data: SourceTreeItem) {
  if (!data.path) return
  const refreshKey = createRecoveryDirectoryRefreshKey(treeKey, data.path)
  sourceDirectoryExpansionRevisionByKey.set(
    refreshKey,
    (sourceDirectoryExpansionRevisionByKey.get(refreshKey) ?? 0) + 1,
  )
}

async function refreshCreateRecoveryDirectory(
  treeKey: string,
  data: SourceTreeItem,
  loadChildren: () => Promise<DemoDirTreeItem[]>,
) {
  if (!data.path || data.path_type === 'file' || isWholeSnapshotRecoveryPath(data.path)) return
  const tree = createRecoveryDirectoryTreeRefs.get(treeKey)
  if (!tree) return
  const refreshKey = createRecoveryDirectoryRefreshKey(treeKey, data.path)
  if (refreshingSourceDirectoryByKey[refreshKey]) return
  const sourceNode = tree.getNode(data.path) as unknown as { expanded?: boolean } | null
  if (!sourceNode) return
  const wasExpanded = Boolean(sourceNode.expanded)
  const expansionRevisionAtStart = sourceDirectoryExpansionRevisionByKey.get(refreshKey) ?? 0

  refreshingSourceDirectoryByKey[refreshKey] = true
  try {
    const children = await loadChildren()
    if (createRecoveryDirectoryTreeRefs.get(treeKey) !== tree) return
    tree.updateKeyChildren(data.path, children)
    const refreshedNode = tree.getNode(data.path) as unknown as {
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
    const expansionRevisionAfterRefresh = sourceDirectoryExpansionRevisionByKey.get(refreshKey) ?? 0
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
  } catch (err) {
    ElMessage.error({
      message: apiErrorMessageI18n(err, t, t('protection.backupsPage.dirTreeLoadFailed')),
      grouping: true,
    })
  } finally {
    delete refreshingSourceDirectoryByKey[refreshKey]
  }
}

function refreshCreateRecoverySourceDirectory(
  group: WizardSourceGroup,
  dirPlan: CreateRecoveryDirPlanConfig,
  data: SourceTreeItem,
) {
  const treeKey = recoveryDirPlanPickerKey(group, dirPlan, 'source')
  return refreshCreateRecoveryDirectory(treeKey, data, async () => {
    const page = await listRealSourceDirChildren(group.sourceId, data.path, {
      includeFiles: true,
      forceRefresh: true,
    })
    return page.entries
  })
}

function refreshCreateRecoveryDestDirectory(
  group: WizardSourceGroup,
  dirPlan: CreateRecoveryDirPlanConfig,
  data: SourceTreeItem,
) {
  const treeKey = recoveryDirPlanPickerKey(group, dirPlan, 'dest')
  return refreshCreateRecoveryDirectory(treeKey, data, async () => {
    const page = await listRealSourceDirChildren(restoreDirectoryBrowseSourceId(dirPlan.targetHostId), data.path, {
      includeFiles: false,
      forceRefresh: true,
    })
    return page.entries
  })
}

function onCreateRecoverySourcePathTreePick(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig, data: DemoDirTreeItem) {
  restoreCreateRecoveryPathErrorNotice(group, dirPlan, 'source')
  updateRecoveryDirPlan(group, dirPlan, {
    sourcePath: data.path,
    sourcePathType: isWholeSnapshotRecoveryPath(data.path) ? 'directory' : backupPathTypeForEntry(data as BackupSourceDirectoryEntry),
    sourcePathValidation: 'valid',
    sourcePathError: '',
  })
  setCreateRecoverySourcePathPickerVisible(group, dirPlan, false)
}

function updateCreateRecoverySourcePathInput(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig, value: string) {
  restoreCreateRecoveryPathErrorNotice(group, dirPlan, 'source')
  const nextValue = value.trim()
  const isWholeSnapshot = nextValue === wholeSnapshotRecoveryLabel()
  updateRecoveryDirPlan(group, dirPlan, {
    sourcePath: isWholeSnapshot ? WHOLE_SNAPSHOT_RECOVERY_PATH : nextValue,
    sourcePathType: isWholeSnapshot ? 'directory' : 'unknown',
    sourcePathValidation: isWholeSnapshot
      ? 'valid'
      : nextValue
        ? 'pending'
        : 'invalid',
    sourcePathError: isWholeSnapshot || nextValue ? '' : t('protection.backupsPage.msgManualPathRequired'),
  })
}

function onCreateRecoveryDestPathTreePick(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig, data: DemoDirTreeItem) {
  restoreCreateRecoveryPathErrorNotice(group, dirPlan, 'dest')
  updateRecoveryDirPlan(group, dirPlan, { restoreDir: data.path, restoreDirValidation: 'valid', restoreDirError: '' })
  setCreateRecoveryDestPathPickerVisible(group, dirPlan, false)
}

function findRecoveryPlanConfiguredRoot(group: WizardSourceGroup, path: string) {
  return [...group.entries]
    .filter((entry) => entry.path && isSameOrAncestorPath(entry.path, path))
    .sort((a, b) => b.path.length - a.path.length)[0] || null
}

async function validateCreateRecoverySourcePathInput(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  const path = recoverySourcePathInputValue(dirPlan.sourcePath).trim()
  if (!path || path === wholeSnapshotRecoveryLabel()) {
    if (path === wholeSnapshotRecoveryLabel()) {
      updateRecoveryDirPlan(group, dirPlan, {
        sourcePath: WHOLE_SNAPSHOT_RECOVERY_PATH,
        sourcePathType: 'directory',
        sourcePathValidation: 'valid',
        sourcePathError: '',
      })
      return
    }
    const message = t('protection.backupsPage.msgManualPathRequired')
    updateRecoveryDirPlan(group, dirPlan, { sourcePathValidation: 'invalid', sourcePathError: message })
    ElMessage.warning({ message, grouping: true })
    return
  }
  if (!isAbsoluteSourcePath(path)) {
    const message = t('protection.backupsPage.msgManualPathAbsolute')
    updateRecoveryDirPlan(group, dirPlan, { sourcePathValidation: 'invalid', sourcePathError: message })
    ElMessage.warning({ message, grouping: true })
    return
  }
  if (!findRecoveryPlanConfiguredRoot(group, path)) {
    const message = t('protection.backupsPage.msgRestoreScopeOutsideBackupDirs')
    updateRecoveryDirPlan(group, dirPlan, { sourcePathValidation: 'invalid', sourcePathError: message })
    ElMessage.warning({ message, grouping: true })
    return
  }
  const key = recoveryDirPlanPickerKey(group, dirPlan, 'source')
  setDirectoryLoading(key, true)
  try {
    const pathInfo = await getBackupSourcePathInfo({
      source_id: group.sourceId,
      path,
      timeout: 10,
    })
    updateRecoveryDirPlan(group, dirPlan, {
      sourcePath: pathInfo.path || path,
      sourcePathType: backupPathTypeForEntry(pathInfo),
      sourcePathValidation: 'valid',
      sourcePathError: '',
    })
    setCreateRecoverySourcePathPickerVisible(group, dirPlan, false)
  } catch (err) {
    const message = apiErrorMessageI18n(err, t, t('protection.backupsPage.msgRestoreScopeVerifyFailed'))
    updateRecoveryDirPlan(group, dirPlan, { sourcePathValidation: 'invalid', sourcePathError: message })
    ElMessage.error({ message, grouping: true })
  } finally {
    setDirectoryLoading(key, false)
  }
}

function updateCreateRecoveryDestPathInput(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig, value: string) {
  restoreCreateRecoveryPathErrorNotice(group, dirPlan, 'dest')
  const nextValue = String(value || '').trim()
  updateRecoveryDirPlan(group, dirPlan, {
    restoreDir: nextValue,
    restoreDirValidation: nextValue ? 'pending' : 'invalid',
    restoreDirError: nextValue ? '' : t('protection.backupsPage.msgRestoreDirectoryRequired'),
  })
}

async function validateCreateRecoveryDestPathInput(group: WizardSourceGroup, dirPlan: CreateRecoveryDirPlanConfig) {
  const path = String(dirPlan.restoreDir || '').trim()
  if (!path) {
    const message = t('protection.backupsPage.msgRestoreDirectoryRequired')
    updateRecoveryDirPlan(group, dirPlan, { restoreDirValidation: 'invalid', restoreDirError: message })
    ElMessage.warning({ message, grouping: true })
    return
  }
  if (!dirPlan.targetHostId) {
    const message = t('protection.backupsPage.createRecoveryTargetHostPlaceholder')
    updateRecoveryDirPlan(group, dirPlan, { restoreDirValidation: 'invalid', restoreDirError: message })
    ElMessage.warning({ message, grouping: true })
    return
  }
  if (!isAbsoluteSourcePath(path)) {
    const message = t('protection.backupsPage.msgManualPathAbsolute')
    updateRecoveryDirPlan(group, dirPlan, { restoreDirValidation: 'invalid', restoreDirError: message })
    ElMessage.warning({ message, grouping: true })
    return
  }
  const key = recoveryDirPlanPickerKey(group, dirPlan, 'dest')
  setDirectoryLoading(key, true)
  try {
    const pathInfo = await getBackupSourcePathInfo({
      source_id: restoreDirectoryBrowseSourceId(dirPlan.targetHostId),
      path,
      timeout: 10,
    })
    if (pathInfo.is_dir === false) {
      const message = t('protection.backupsPage.msgRestoreDirectoryMustBeDirectory')
      updateRecoveryDirPlan(group, dirPlan, { restoreDirValidation: 'invalid', restoreDirError: message })
      ElMessage.warning({ message, grouping: true })
      return
    }
    updateRecoveryDirPlan(group, dirPlan, {
      restoreDir: pathInfo.path || path,
      restoreDirValidation: 'valid',
      restoreDirError: '',
    })
    setCreateRecoveryDestPathPickerVisible(group, dirPlan, false)
  } catch (err) {
    const message = apiErrorMessageI18n(err, t, t('protection.backupsPage.msgRestoreDirectoryVerifyFailed'))
    updateRecoveryDirPlan(group, dirPlan, { restoreDirValidation: 'invalid', restoreDirError: message })
    ElMessage.error({ message, grouping: true })
  } finally {
    setDirectoryLoading(key, false)
  }
}

function filterTargetsByCriteria(search: string, repoType: string | undefined) {
  const q = search.trim().toLowerCase()
  const typ = repoType
  const otherLabel = t('protection.backupsPage.repoTypeOther')
  return selectableTargets.value.filter((target) => {
    const repo = target.repoType ?? otherLabel
    if (typ != null && typ !== '' && repo !== typ) return false
    if (!q) return true
    return (
      target.name.toLowerCase().includes(q) ||
      target.location.toLowerCase().includes(q) ||
      repo.toLowerCase().includes(q)
    )
  })
}

function targetHealth(target: WizardTarget | null | undefined) {
  return target?.health || target?.status || 'unverified'
}

function isSelectableTarget(target: WizardTarget | null | undefined) {
  const health = targetHealth(target)
  return health === 'online' || health === 'unverified'
}

function setTargetPickerSearch(picker: TargetPickerState | undefined, query: string) {
  if (picker) picker.search = query
}

function onTargetPickerVisible(picker: TargetPickerState | undefined, visible: boolean) {
  if (visible && picker) picker.search = ''
}

function applyAddTargetS3RegionPreset(key: string) {
  addTargetS3RegionPreset.value = key
  const preset = addTargetS3Regions.value.find((item) => item.key === key)
  if (!preset) return
  addTargetS3Endpoint.value = preset.endpoint
  addTargetS3Region.value = preset.region
}

function onAddTargetS3PlatformChange(platform: AddTargetS3Platform) {
  addTargetS3Platform.value = platform
  addTargetS3UrlStyle.value = defaultS3UrlStyle(platform)
  addTargetS3RegionPreset.value = undefined
  addTargetS3Endpoint.value = ''
  addTargetS3Region.value = ''
}

function maskAddTargetAccessKey(id: string) {
  if (!id) return ''
  if (id.length < 8) return '***'
  return `${id.slice(0, 4)}****${id.slice(-4)}`
}

function getDefaultAddTargetProxyDir(nodeId: number | undefined) {
  const nodes = generateAddTargetProxyTreeData(nodeId)
  return nodes[0]?.children?.[0]?.id ?? nodes[0]?.id ?? ''
}

function generateAddTargetRepoName(nodeName: string) {
  const now = new Date()
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
    String(now.getHours()).padStart(2, '0'),
    String(now.getMinutes()).padStart(2, '0'),
  ].join('')
  return `${nodeName}-repo-${stamp}`
}

function onAddTargetProxyTreeSelect(data: AddTargetDirTreeNode | null) {
  if (!data?.id) return
  addTargetProxyDir.value = data.id
  addTargetProxyDirTreeRef.value?.setCheckedKeys([data.id])
}

function onAddTargetProxyTreeCheck(data: AddTargetDirTreeNode, checked: boolean) {
  if (!checked) {
    if (addTargetProxyDir.value === data.id) {
      addTargetProxyDir.value = ''
      addTargetProxyDirTreeRef.value?.setCheckedKeys([])
    }
    return
  }
  addTargetProxyDir.value = data.id
  addTargetProxyDirTreeRef.value?.setCheckedKeys([data.id])
}

function resetAddTargetForm(kind: AddTargetRepoKind = addTargetKind.value) {
  addTargetKind.value = kind
  addTargetSaving.value = false
  addTargetS3Step.value = 0
  addTargetNasStep.value = 0
  addTargetRepoName.value = ''
  addTargetS3Platform.value = undefined
  addTargetS3RegionPreset.value = undefined
  addTargetS3Endpoint.value = ''
  addTargetS3Region.value = ''
  addTargetS3Bucket.value = ''
  addTargetS3BucketMode.value = 'existing'
  addTargetS3Prefix.value = DEFAULT_S3_OBJECT_PREFIX
  addTargetS3AccessKey.value = ''
  addTargetS3SecretKey.value = ''
  addTargetS3UrlStyle.value = defaultS3UrlStyle(undefined)
  addTargetS3UseTls.value = true
  addTargetNasProtocol.value = 'smb'
  addTargetNasHost.value = ''
  addTargetNasShare.value = ''
  addTargetNasExport.value = ''
  addTargetNasUsername.value = ''
  addTargetNasPassword.value = ''
  addTargetNasMountOptions.value = ''
  addTargetNasProxyNodeId.value = undefined
  addTargetProxyNodeId.value = undefined
  addTargetProxyDir.value = ''
  addTargetProxyUseTree.value = true
  addTargetQuota.value = 0
  addTargetEnableQuotaAlert.value = false
  addTargetQuotaAlertThreshold.value = 80
}


function onAddTargetKindChange(kind: string) {
  if (kind === 's3' || kind === 'nas' || kind === 'proxy_fs') {
    resetAddTargetForm(kind)
  }
}

watch(addTargetProxyNodeId, (nodeId) => {
  if (addTargetKind.value !== 'proxy_fs') return
  if (!nodeId) {
    addTargetProxyDir.value = ''
    return
  }
  const nodeName = addTargetProxyNodeName.value
  addTargetProxyDir.value = getDefaultAddTargetProxyDir(nodeId)
  if (!addTargetRepoName.value && nodeName) {
    addTargetRepoName.value = generateAddTargetRepoName(nodeName)
  }
})

watch(addTargetProxyCheckedTreeKeys, (keys) => {
  addTargetProxyDirTreeRef.value?.setCheckedKeys(keys)
})

function closeAddTargetDialog() {
  addTargetOpen.value = false
}

function validateAddTargetS3Step(step: 0 | 1) {
  if (step === 0) {
    if (!addTargetS3Platform.value) {
      ElMessage.warning({ message: t('repositoriesPage.errPlatform'), grouping: true })
      return false
    }
    if (addTargetS3Platform.value !== 'other' && !addTargetS3RegionPreset.value) {
      ElMessage.warning({ message: t('addS3Repo.errRegion'), grouping: true })
      return false
    }
    if (!addTargetS3Endpoint.value.trim()) {
      ElMessage.warning({ message: t('addS3Repo.errEndpoint'), grouping: true })
      return false
    }
    if (!addTargetS3AccessKey.value.trim()) {
      ElMessage.warning({ message: t('addS3Repo.errAccessKey'), grouping: true })
      return false
    }
    if (!addTargetS3SecretKey.value.trim()) {
      ElMessage.warning({ message: t('addS3Repo.errSecretKey'), grouping: true })
      return false
    }
    return true
  }
  if (!addTargetRepoName.value.trim()) {
    ElMessage.warning({ message: t('repositoriesPage.errName'), grouping: true })
    return false
  }
  if (!addTargetS3Bucket.value.trim()) {
    ElMessage.warning({ message: t('repositoriesPage.errBucket'), grouping: true })
    return false
  }
  return true
}

function validateAddTargetNasStep(step: 0 | 1 | 2) {
  if (step === 0) {
    if (!addTargetNasHost.value.trim()) {
      ElMessage.warning(
        addTargetNasProtocol.value === 'smb'
          ? t('addNasRepo.errSmbHost')
          : t('repositoriesPage.errNfsHost'),
      )
      return false
    }
    if (addTargetNasProtocol.value === 'smb') {
      if (!addTargetNasShare.value.trim()) {
        ElMessage.warning({ message: t('addNasRepo.errSmbShare'), grouping: true })
        return false
      }
      if (!addTargetNasUsername.value.trim()) {
        ElMessage.warning({ message: t('repositoriesPage.errSmbUsername'), grouping: true })
        return false
      }
      if (!addTargetNasPassword.value.trim()) {
        ElMessage.warning({ message: t('repositoriesPage.errSmbPassword'), grouping: true })
        return false
      }
    } else if (!addTargetNasExport.value.trim()) {
      ElMessage.warning({ message: t('repositoriesPage.errNfsExport'), grouping: true })
      return false
    }
    return true
  }
  if (step === 1) {
    return true
  }
  if (!addTargetRepoName.value.trim()) {
    ElMessage.warning({ message: t('repositoriesPage.errName'), grouping: true })
    return false
  }
  return true
}

function validateAddTargetForm() {
  if (addTargetKind.value === 's3') {
    return validateAddTargetS3Step(0) && validateAddTargetS3Step(1)
  }
  if (addTargetKind.value === 'nas') {
    return validateAddTargetNasStep(0) && validateAddTargetNasStep(1) && validateAddTargetNasStep(2)
  }
  if (!addTargetRepoName.value.trim()) {
    ElMessage.warning({ message: t('repositoriesPage.errName'), grouping: true })
    return false
  }
  if (!addTargetProxyNodeId.value) {
    ElMessage.warning({ message: t('repositoriesPage.errProxyNode'), grouping: true })
    return false
  }
  if (!addTargetProxyDir.value.trim()) {
    ElMessage.warning({ message: t('repositoriesPage.errProxyNodeDir'), grouping: true })
    return false
  }
  return true
}

const addTargetIsFirstStep = computed(() => {
  if (addTargetKind.value === 's3') return addTargetS3Step.value === 0
  if (addTargetKind.value === 'nas') return addTargetNasStep.value === 0
  return true
})

const addTargetIsFinalStep = computed(() => {
  if (addTargetKind.value === 's3') return addTargetS3Step.value === 1
  if (addTargetKind.value === 'nas') return addTargetNasStep.value === 2
  return true
})

function prevAddTargetStep() {
  if (addTargetKind.value === 's3' && addTargetS3Step.value > 0) {
    addTargetS3Step.value = 0
  } else if (addTargetKind.value === 'nas' && addTargetNasStep.value > 0) {
    addTargetNasStep.value = (addTargetNasStep.value - 1) as 0 | 1 | 2
  }
}

function nextAddTargetStep() {
  if (addTargetKind.value === 's3') {
    if (!validateAddTargetS3Step(addTargetS3Step.value)) return
    addTargetS3Step.value = 1
    return
  }
  if (addTargetKind.value === 'nas') {
    if (!validateAddTargetNasStep(addTargetNasStep.value)) return
    addTargetNasStep.value = (addTargetNasStep.value + 1) as 0 | 1 | 2
  }
}



function buildAddTargetPayload() {
  if (addTargetKind.value === 's3') {
    return {
      name: addTargetRepoName.value.trim(),
      repo_type: 's3',
      s3_platform: addTargetS3Platform.value === 'other' ? 'custom' : addTargetS3Platform.value,
      s3_bucket: addTargetS3Bucket.value.trim(),
      config: {
        endpoint: addTargetS3Endpoint.value.trim(),
        region: addTargetS3Region.value.trim(),
        prefix: addTargetS3Prefix.value.trim(),
        access_key_id: addTargetS3AccessKey.value.trim(),
        secret_access_key: addTargetS3SecretKey.value,
        s3_url_style: addTargetS3UrlStyle.value,
        use_tls: addTargetS3UseTls.value,
      },
    } as const
  }
  if (addTargetKind.value === 'nas') {
    const sharePath = addTargetNasProtocol.value === 'smb'
      ? addTargetNasShare.value.trim().replace(/^\/+/, '')
      : addTargetNasExport.value.trim()
    return {
      name: addTargetRepoName.value.trim(),
      repo_type: 'nas',
      nas_protocol: addTargetNasProtocol.value,
      bind_node_type: addTargetNasProxyNodeId.value ? 'proxy' : undefined,
      bind_node_id: addTargetNasProxyNodeId.value,
      config: {
        server_address: addTargetNasHost.value.trim(),
        share_path: sharePath,
        smb_username: addTargetNasProtocol.value === 'smb' ? addTargetNasUsername.value.trim() : undefined,
        smb_password: addTargetNasProtocol.value === 'smb' ? addTargetNasPassword.value : undefined,
        mount_options: addTargetNasMountOptions.value.trim() || undefined,
      },
    } as const
  }
  return {
    name: addTargetRepoName.value.trim(),
    repo_type: 'proxy_fs',
    bind_node_type: 'proxy',
    bind_node_id: addTargetProxyNodeId.value,
    config: {
      proxy_node_dir: addTargetProxyDir.value.trim(),
    },
  } as const
}

async function submitAddTargetDialog() {
  if (!validateAddTargetForm()) return
  addTargetSaving.value = true
  try {
    const created = await createStorageRepository(buildAddTargetPayload())
    const target = mapRepository(created)
    realTargets.value = [target, ...realTargets.value.filter((item) => item.id !== target.id)]
    prepareNewTargetAssignment(target)
    addTargetOpen.value = false
    ElMessage.success({ message: t('protection.backupsPage.msgTargetCreated'), grouping: true })
  } finally {
    addTargetSaving.value = false
  }
}

function onEmbeddedTargetCreated(repository: StorageRepository) {
  const target = mapRepository(repository)
  realTargets.value = [target, ...realTargets.value.filter((item) => item.id !== target.id)]
  prepareNewTargetAssignment(target)
  addTargetOpen.value = false
  ElMessage.success({ message: t('protection.backupsPage.msgTargetCreated'), grouping: true })
}

function prepareNewTargetAssignment(target: WizardTarget) {
  const selectedGroups = checkedTargetGroups.value.length > 0
    ? checkedTargetGroups.value
    : wizardSourceGroups.value.filter((group) => (
        !sourceTargetMap.value[group.key]
        && targetIncompatibilityReasonForGroup(target, group) === null
      ))
  targetAssignmentCheckedGroupKeys.value = selectedGroups.map((group) => group.key)
  targetAssignDialogMode.value = 'batch'
  targetAssignActiveGroupKey.value = ''
  resetTargetAssignPicker(target.id)
  targetAssignDialogOpen.value = true
}

const protectionMenus = useProtectionSideNav()
const backupCreateWizardShell = computed(() => (props.embedded ? EmbeddedWizardShell : ModulePage))
const backupCreateWizardShellProps = computed(() => {
  if (props.embedded) return {}
  return {
    title: t('protection.moduleTitle'),
    menus: protectionMenus.value,
    bodyFlush: true,
  }
})

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

async function loadWizardSources(ids: string[]) {
  const realIds = normalizeSourceIdList(ids.filter(isBackupSelectableSourceId))
  if (!realIds.length) return
  const rows = await restoreTargetCatalog.ensureByIds(realIds)
  const next = new Map(realSourceById.value)
  for (const row of rows.map(mapSelectableSource)) next.set(row.id, row)
  realSourceById.value = next
}

watch(restoreTargetCatalog.allRecords, (records) => {
  const next = new Map(realSourceById.value)
  for (const row of records.map(mapSelectableSource)) next.set(row.id, row)
  realSourceById.value = next
}, { flush: 'sync' })

async function refreshWizardTargets() {
  targetsRefreshing.value = true
  try {
    const repos = await listAllStorageRepositories({ page_size: 10 })
    realTargets.value = repos.map(mapRepository)
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, editorFailureText.value), grouping: true })
  } finally {
    targetsRefreshing.value = false
  }
}

async function refreshWizardPolicies() {
  policiesRefreshing.value = true
  try {
    const policies = await listBackupPolicies({ page: 1, page_size: 500 })
    realPolicies.value = policies.results.map(mapPolicy)
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, editorFailureText.value), grouping: true })
  } finally {
    policiesRefreshing.value = false
  }
}

async function refreshWizardFilters() {
  filtersRefreshing.value = true
  try {
    const filters = await listFileFilterRules({ page: 1, page_size: 500 })
    realFilters.value = filters.results.map(mapFilter)
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, editorFailureText.value), grouping: true })
  } finally {
    filtersRefreshing.value = false
  }
}

async function loadWizardReferences() {
  await Promise.all([
    refreshWizardTargets(),
    refreshWizardPolicies(),
    refreshWizardFilters(),
  ])
}

function editSectionFromQuery(value: unknown): BackupConfigEditSection {
  const raw = Array.isArray(value) ? value[0] : value
  if (raw === 'policy') return 'policy'
  if (raw === 'recovery' || raw === 'recovery-plan') return 'recovery-plan'
  return 'paths'
}

function editStepForSection(section: BackupConfigEditSection) {
  if (section === 'policy') return 1
  if (section === 'recovery-plan') return 3
  return 0
}

function sourceIdFromConfig(config: BackupConfigDetail) {
  return `${config.source_type === 'nas' ? 'nas' : 'agent'}:${config.source_ref_id}`
}

function targetIdFromPlan(plan: { target_type?: string; target_ref_id?: number | null }) {
  const type = plan.target_type === 'nas' ? 'nas' : 'agent'
  return plan.target_ref_id ? `${type}:${plan.target_ref_id}` : ''
}

function targetPayloadFromSourceId(sourceId: string) {
  const type = sourceId.startsWith('nas:') ? 'nas' : 'agent'
  return {
    target_type: type as 'agent' | 'nas',
    target_ref_id: parseSourceRefId(sourceId),
  }
}

function resetCreateStateForSources(sourceIds: string[], initialStep = 0) {
  cancelTargetValidation()
  targetConnectionResults.value = {}
  createOpen.value = true
  createStep.value = initialStep
  createCompletedSteps.value = new Set([0, 1, 2, 3].filter((step) => step < initialStep))
  createPhase.value = 'form'
  wizardDirEntries.value = []
  wizardBrowsingSourceId.value = sourceIds[0] ?? ''
  resetCreateSourceSearch()
  resetFilterPolicySourceSearch()
  resetTargetSourceSearch()
  resetRecoveryPlanSourceSearch()
  createExpandedSourceIds.value = [...sourceIds]
  createSourceValidationAttempted.value = false
  highlightedCreateSourceId.value = ''
  createSourceDirKeys.value = []
  createSourceTreeRefs.clear()
  Object.keys(createSourceDirKeysBySource).forEach((key) => delete createSourceDirKeysBySource[key])
  Object.keys(directoryLoadingByKey).forEach((key) => delete directoryLoadingByKey[key])
  Object.keys(sourceTreeConflictWarnedAt).forEach((key) => delete sourceTreeConflictWarnedAt[key])
  noSourceTreeRoots.value = false
  Object.keys(noSourceTreeRootsBySource).forEach((key) => delete noSourceTreeRootsBySource[key])
  Object.keys(createSourceTreeErrorBySource).forEach((key) => delete createSourceTreeErrorBySource[key])
  Object.keys(createSourceTreeRemountKeyBySource).forEach((key) => delete createSourceTreeRemountKeyBySource[key])
  treeRemountKey.value += 1
  sourceTargetMap.value = {}
  sourceEndpointTypeMap.value = {}
  sourcePolicyMap.value = {}
  createRecoveryPlanMap.value = {}
  createRecoveryPlanExpandedKeys.value = []
  targetAssignmentCheckedGroupKeys.value = []
  targetValidationAttempted.value = false
  highlightedTargetGroupKey.value = ''
  filterPolicyAssignmentCheckedGroupKeys.value = []
  recoveryPlanCheckedGroupKeys.value = []
  closeValidationPopover()
  sourceFilterMap.value = {}
  sourceCompressionMap.value = {}
  batchTargetPicker.search = ''
  batchTargetPicker.repoType = ''
  batchTargetPicker.targetId = ''
  batchNasTargetMode.value = 'per_directory_repo'
  batchRepositoryEndpointType.value = ''
  targetAssignDialogOpen.value = false
  targetAssignDialogMode.value = 'batch'
  targetAssignActiveGroupKey.value = ''
  batchPolicyId.value = ''
  batchFilterIds.value = []
  batchCompression.value = ''
  Object.keys(nasTargetModes).forEach((key) => delete nasTargetModes[key])
  Object.keys(createRecoverySourcePathPickerVisible).forEach((key) => delete createRecoverySourcePathPickerVisible[key])
  Object.keys(createRecoveryDestPathPickerVisible).forEach((key) => delete createRecoveryDestPathPickerVisible[key])
  policySearch.value = ''
  filterSearch.value = ''
  selPolicy.value = ''
  selGlobalFilter.value = ''
}

function applyEditConfigToWizard(config: BackupConfigDetail, section: BackupConfigEditSection) {
  const sourceId = sourceIdFromConfig(config)
  const source = realSourceById.value.get(sourceId)
  const sourceType = source?.type ?? (config.source_type === 'nas' ? 'nas' : 'host')
  const groupKey = `config:${config.id}`
  const entries = config.directories.map((directory) => ({
    key: `${config.id}||${sourceId}||${directory.path}`,
    backupConfigId: config.id,
    sourceId,
    sourceName: source?.name || getSourceName(sourceId),
    sourceType,
    platform: source?.platform,
    path: directory.path,
    pathType: normalizeBackupPathType(directory.path_type),
  }))
  wizardDirEntries.value = [...wizardDirEntries.value, ...entries]
  sourceTargetMap.value = { ...sourceTargetMap.value, [groupKey]: String(config.repository_id) }
  sourceEndpointTypeMap.value = {
    ...sourceEndpointTypeMap.value,
    [groupKey]: config.repository_endpoint_type || 'external',
  }
  sourcePolicyMap.value = { ...sourcePolicyMap.value, [groupKey]: config.backup_policy_id ? String(config.backup_policy_id) : '' }
  sourceFilterMap.value = { ...sourceFilterMap.value, [groupKey]: config.file_filter_rule_id ? [String(config.file_filter_rule_id)] : [] }
  sourceCompressionMap.value = { ...sourceCompressionMap.value, [groupKey]: config.compression_level }
  const sourceName = source?.name || getSourceName(sourceId)
  const fallbackGroup: WizardSourceGroup = {
    key: groupKey,
    backupConfigId: config.id,
    sourceId,
    sourceName,
    sourceType,
    platform: source?.platform,
    entries,
  }
  const enabledPlans = (config.recovery_plans || []).filter((plan) => plan.enabled !== false)
  const recoveryPlanEnabled = Boolean(config.recovery_plan_enabled && enabledPlans.length > 0)
  createRecoveryPlanMap.value = {
    ...createRecoveryPlanMap.value,
    [groupKey]: {
      enabled: recoveryPlanEnabled,
      conflictMode: enabledPlans[0]?.conflict_mode === 'overwrite' ? 'overwrite' : enabledPlans[0] ? 'skip' : '',
      dirPlans: enabledPlans.length
        ? enabledPlans.map((plan) => {
            const targetHostId = targetIdFromPlan(plan)
            return {
              id: `restore-plan-${plan.id}`,
              sourcePath: plan.scope === 'snapshot' ? WHOLE_SNAPSHOT_RECOVERY_PATH : plan.source_path,
              sourcePathType: normalizeBackupPathType(
                config.directories.find((directory) => directory.id === plan.backup_config_dir_id)?.path_type,
              ),
              sourcePathValidation: 'valid',
              targetMode: targetHostId === sourceId ? 'original' : 'new',
              targetHostId,
              restoreDir: plan.restore_dir,
              restoreDirValidation: 'valid',
            }
          })
        : [createDefaultRecoveryDirPlan(fallbackGroup)],
    },
  }
  if (section === 'recovery-plan' && recoveryPlanEnabled) {
    createRecoveryPlanExpandedKeys.value = [...new Set([...createRecoveryPlanExpandedKeys.value, groupKey])]
  }
  recoveryPlanCheckedGroupKeys.value = [...new Set([...recoveryPlanCheckedGroupKeys.value, groupKey])]
  filterPolicyAssignmentCheckedGroupKeys.value = [...new Set([...filterPolicyAssignmentCheckedGroupKeys.value, groupKey])]
  targetAssignmentCheckedGroupKeys.value = [...new Set([...targetAssignmentCheckedGroupKeys.value, groupKey])]
  createSourceDirKeysBySource[sourceId] = config.directories.map((directory) => directory.path)
  createStep.value = editStepForSection(section)
}

async function openEditConfigs(configIds: number[], section: BackupConfigEditSection) {
  const configs = await Promise.all(configIds.map((configId) => getBackupConfig(configId)))
  editConfigs.value = configs
  editSection.value = section
  const sourceIds = [...new Set(configs.map(sourceIdFromConfig))]
  const savedTargetIds = configs.flatMap((config) => (config.recovery_plans || []).map(targetIdFromPlan)).filter(Boolean)
  await loadWizardSources([...sourceIds, ...savedTargetIds])
  const existingSourceIds = sourceIds.filter((sourceId) => realSourceById.value.has(sourceId))
  if (!existingSourceIds.length) {
    ElMessage.error({ message: t('protection.backupsPage.msgSourceNoBackupConfig'), grouping: true })
    closeCreate()
    return
  }
  step1Selection.value = existingSourceIds
  resetCreateStateForSources(existingSourceIds, editStepForSection(section))
  for (const config of configs) {
    applyEditConfigToWizard(config, section)
  }
}

onMounted(async () => {
  try {
    const catalog = await fetchStorageProviderCatalog()
    addTargetS3CatalogProviders.value = catalog.providers.filter((provider) => provider.enabled)
  } catch (error) {
    logger.warn('Failed to load Provider Catalog for the legacy embedded target form', error)
  }
  const rawRouteEditConfigIds = Array.isArray(route.query.edit_config_ids)
    ? route.query.edit_config_ids.join(',')
    : route.query.edit_config_ids || route.query.edit_config_id || ''
  const routeEditConfigIds = String(rawRouteEditConfigIds)
    .split(',')
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0)
  const embeddedEditConfigIds = props.initialEditConfigIds
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0)
  const editConfigIds = props.embedded && embeddedEditConfigIds.length > 0
    ? embeddedEditConfigIds
    : routeEditConfigIds
  const section = props.embedded && props.initialEditSection
    ? props.initialEditSection
    : editSectionFromQuery(route.query.edit_section)
  const sourceIds = props.embedded
    ? normalizeSourceIdList(props.initialSources)
    : String(route.query.sources ?? '')
      .split(',')
      .map((id) => id.trim())
      .filter(Boolean)
  try {
    await Promise.all([
      loadWizardSources(sourceIds),
      restoreTargetCatalog.reset(),
      loadWizardReferences(),
    ])
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, editorFailureText.value), grouping: true })
    createBootstrapping.value = false
    closeCreate()
    return
  }
  if (editConfigIds.length > 0) {
    try {
      await openEditConfigs([...new Set(editConfigIds)], section)
      createBootstrapping.value = false
    } catch (err) {
      ElMessage.error({
        message: isInvalidCompressionLevelError(err)
          ? t('protection.backupsPage.compressionInvalidValue')
          : apiErrorMessage(err, editorFailureText.value),
        grouping: true,
      })
      createBootstrapping.value = false
      closeCreate()
    }
    return
  }
  if (sourceIds.length) {
    const existingIds = filterExistingRealSourceIds(sourceIds)
    step2Sources.value = normalizeSourceIdList([...step2Sources.value, ...existingIds])
    step1Selection.value = existingIds
  }
  if (!step1Selection.value.length) {
    step1Selection.value = step2AvailableSourceIds.value.slice(0, 1)
  }
  void loadProxyNodes()
  nextTick(async () => {
    createBootstrapping.value = false
    openCreate()
  })
})

onUnmounted(() => {
  cancelTargetValidation()
  if (typeof document !== 'undefined') document.body.style.overflow = ''
})

function sourceKey(hostId: string, path: string) {
  return `${hostId}||${path}`
}

function openCreate() {
  const sourceIds = createSourceIds.value
  if (sourceIds.length === 0) {
    ElMessage.warning({ message: t('protection.backupsPage.msgSelectSourceFirst'), grouping: true })
    closeCreate()
    return
  }
  const offlineRows = sourceIds
    .map((id) => sourceRecord(id))
    .filter((row): row is NonNullable<ReturnType<typeof sourceRecord>> => row != null && row.availability !== 'online')
  if (offlineRows.length > 0) {
    ElMessage.warning({ message: formatOfflineBackupPlanMessage(offlineRows, t), grouping: true })
    closeCreate()
    return
  }
  cancelTargetValidation()
  targetConnectionResults.value = {}
  createOpen.value = true
  createStep.value = 0
  createCompletedSteps.value = new Set()
  createPhase.value = 'form'
  wizardDirEntries.value = []
  wizardBrowsingSourceId.value = sourceIds[0] ?? ''
  resetCreateSourceSearch()
  resetFilterPolicySourceSearch()
  resetTargetSourceSearch()
  resetRecoveryPlanSourceSearch()
  createExpandedSourceIds.value = [...sourceIds]
  createSourceValidationAttempted.value = false
  highlightedCreateSourceId.value = ''
  createSourceDirKeys.value = []
  createSourceTreeRefs.clear()
  Object.keys(createSourceDirKeysBySource).forEach((key) => delete createSourceDirKeysBySource[key])
  Object.keys(directoryLoadingByKey).forEach((key) => delete directoryLoadingByKey[key])
  Object.keys(sourceTreeConflictWarnedAt).forEach((key) => delete sourceTreeConflictWarnedAt[key])
  noSourceTreeRoots.value = false
  Object.keys(noSourceTreeRootsBySource).forEach((key) => delete noSourceTreeRootsBySource[key])
  Object.keys(createSourceTreeErrorBySource).forEach((key) => delete createSourceTreeErrorBySource[key])
  Object.keys(createSourceTreeRemountKeyBySource).forEach((key) => delete createSourceTreeRemountKeyBySource[key])
  treeRemountKey.value += 1
  sourceTargetMap.value = {}
  sourceEndpointTypeMap.value = {}
  sourcePolicyMap.value = {}
  createRecoveryPlanMap.value = {}
  createRecoveryPlanExpandedKeys.value = []
  targetAssignmentCheckedGroupKeys.value = []
  targetValidationAttempted.value = false
  highlightedTargetGroupKey.value = ''
  filterPolicyAssignmentCheckedGroupKeys.value = []
  recoveryPlanCheckedGroupKeys.value = []
  closeValidationPopover()
  sourceFilterMap.value = {}
  sourceCompressionMap.value = {}
  batchTargetPicker.search = ''
  batchTargetPicker.repoType = ''
  batchTargetPicker.targetId = ''
  batchNasTargetMode.value = 'per_directory_repo'
  batchRepositoryEndpointType.value = ''
  targetAssignDialogOpen.value = false
  targetAssignDialogMode.value = 'batch'
  targetAssignActiveGroupKey.value = ''
  batchPolicyId.value = ''
  batchFilterIds.value = []
  batchCompression.value = ''
  Object.keys(nasTargetModes).forEach((key) => delete nasTargetModes[key])
  Object.keys(createRecoverySourcePathPickerVisible).forEach((key) => delete createRecoverySourcePathPickerVisible[key])
  Object.keys(createRecoveryDestPathPickerVisible).forEach((key) => delete createRecoveryDestPathPickerVisible[key])
  policySearch.value = ''
  filterSearch.value = ''
  selPolicy.value = ''
  selGlobalFilter.value = ''
}

watch(wizardBrowsingSourceId, () => {
  createSourceDirKeys.value = []
  noSourceTreeRoots.value = false
  treeRemountKey.value += 1
})

watch(createSourceDirKeys, (keys) => {
  createSourceTreeRef.value?.setCheckedKeys(keys)
})

function getSourceType(sourceId: string): 'host' | 'nas' {
  const source = realSourceById.value.get(sourceId)
  if (source?.type === 'nas') return 'nas'
  return 'host'
}

function setCreateSourceTreeRef(sourceId: string, el: unknown) {
  if (el) {
    createSourceTreeRefs.set(sourceId, el as InstanceType<typeof ElTree>)
  } else {
    createSourceTreeRefs.delete(sourceId)
  }
}

function createSourceCheckedKeys(sourceId: string) {
  return createSourceDirKeysBySource[sourceId] ?? []
}

function createSourceAddableCheckedKeys(sourceId: string) {
  return preserveShallowestPathOrder(createSourceCheckedKeys(sourceId).filter((path) => !isPathBlockedByAddedDir(sourceId, path)))
}

function hasCreateSourceAddableSelection(sourceId: string) {
  return createSourceAddableCheckedKeys(sourceId).length > 0
}

function sourceSelectedEntries(sourceId: string) {
  return wizardDirEntries.value.filter((entry) => entry.sourceId === sourceId)
}

function clearManualSourcePathError(sourceId: string) {
  delete manualSourcePathErrorBySource[sourceId]
}

function setManualSourcePathError(sourceId: string, message: string) {
  if (message) manualSourcePathErrorBySource[sourceId] = message
  else clearManualSourcePathError(sourceId)
}

function sourceNameForError(sourceId: string) {
  const name = getSourceName(sourceId)
  return name || sourceId
}

function stringMetaValue(meta: Record<string, unknown> | undefined, key: string) {
  const value = meta?.[key]
  return typeof value === 'string' ? value.trim() : ''
}

function manualPathValidationErrorMessage(err: unknown, sourceId: string, fallbackPath: string) {
  const normalized = normalizeThrownError(err)
  const meta = normalized.meta && typeof normalized.meta === 'object'
    ? normalized.meta as Record<string, unknown>
    : undefined
  const path = stringMetaValue(meta, 'path') || fallbackPath
  const diagnostic = stringMetaValue(meta, 'diagnostic')
  if ((normalized.errorCode || normalized.code) === 'AGENT.PATH_VALIDATE_FAILED') {
    return diagnostic
      ? t('protection.backupsPage.msgManualPathVerifyFailedWithDiagnostic', {
          path,
          source: sourceNameForError(sourceId),
          diagnostic,
        })
      : t('protection.backupsPage.msgManualPathVerifyFailedWithPath', {
          path,
          source: sourceNameForError(sourceId),
        })
  }
  return apiErrorMessageI18n(err, t, t('protection.backupsPage.msgManualPathVerifyFailed'))
}

function normalizeBackupPathType(value: unknown): BackupPathType {
  const raw = String(value || '').trim().toLowerCase()
  if (raw === 'file') return 'file'
  if (raw === 'directory' || raw === 'dir') return 'directory'
  return 'unknown'
}

function backupPathTypeForEntry(entry: BackupSourceDirectoryEntry): BackupPathType {
  if (entry.is_dir === true) return 'directory'
  if (entry.is_dir === false) return 'file'
  return normalizeBackupPathType(entry.path_type)
}

function setSourcePathType(sourceId: string, path: string, pathType: BackupPathType) {
  if (!createSourcePathTypeBySource[sourceId]) {
    createSourcePathTypeBySource[sourceId] = {}
  }
  createSourcePathTypeBySource[sourceId][path] = pathType
}

function sourcePathType(sourceId: string, path: string): BackupPathType {
  return createSourcePathTypeBySource[sourceId]?.[path] ?? 'unknown'
}

function isAbsoluteSourcePath(path: string) {
  return path.startsWith('/')
    || /^[A-Za-z]:[\\/]/.test(path)
    || path.startsWith('\\\\')
}

function manualSourcePathPlaceholder(sourceId: string) {
  const source = sourceRecord(sourceId)
  if (source?.type === 'nas') {
    return t('protection.backupsPage.phManualNasSourcePath')
  }
  return t('protection.backupsPage.phManualSourcePath')
}

function isPathBlockedByAddedDir(sourceId: string, path: string) {
  return sourceSelectedEntries(sourceId).some((entry) =>
    isSameOrAncestorPath(entry.path, path) || isSameOrAncestorPath(path, entry.path),
  )
}

function addedDirBlockReason(sourceId: string, path: string) {
  const entry = sourceSelectedEntries(sourceId).find((item) =>
    isSameOrAncestorPath(item.path, path) || isSameOrAncestorPath(path, item.path),
  )
  if (!entry) return ''
  if (entry.path === path) return t('protection.backupsPage.dirDisabledAlreadyAdded')
  if (isSameOrAncestorPath(entry.path, path)) {
    return t('protection.backupsPage.dirDisabledChildOfAdded')
  }
  return t('protection.backupsPage.dirDisabledParentOfAdded')
}

function addedDirTreeDisableReason(sourceId: string, path: string) {
  return addedDirBlockReason(sourceId, path)
}

function warnSourceTreeConflict(sourceId: string, data: SourceTreeItem) {
  if (data.nodeKind === 'loadMore') return false
  const reason = data.disabledReason || addedDirTreeDisableReason(sourceId, data.path)
  if (!reason) return false
  const key = `${sourceId}:${data.path}:${reason}`
  const now = Date.now()
  if (now - (sourceTreeConflictWarnedAt[key] || 0) > 1200) {
    ElMessage.warning({ message: reason, grouping: true })
    sourceTreeConflictWarnedAt[key] = now
  }
  return true
}

function sourceAddedDirPaths(sourceId: string) {
  return sourceSelectedEntries(sourceId).map((entry) => entry.path)
}

function createSourceTreeCheckedKeys(sourceId: string) {
  const pendingKeys = createSourceCheckedKeys(sourceId).filter((path) => !isPathBlockedByAddedDir(sourceId, path))
  return shallowestPaths([...sourceAddedDirPaths(sourceId), ...pendingKeys])
}

function syncCreateSourceTreeCheckedKeys(sourceId: string) {
  if (!createOpen.value || createStep.value !== 0) return
  const tree = createSourceTreeRefs.get(sourceId)
  if (!tree) return
  try {
    tree.setCheckedKeys(createSourceTreeCheckedKeys(sourceId))
  } catch (err) {
    logger.warn('BackupCreateWizard.vue', 2734, 'Skipped syncing source tree checked keys while tree is remounting', err)
  }
}

type CreateSourceLoadedTreeNode = {
  data?: SourceTreeItem
}

type CreateSourceLoadedTreeStore = {
  nodesMap?: Record<string, CreateSourceLoadedTreeNode> | Map<string, CreateSourceLoadedTreeNode>
  _getAllNodes?: () => CreateSourceLoadedTreeNode[]
}

function createSourceTreeStore(sourceId: string): CreateSourceLoadedTreeStore | null {
  const tree = createSourceTreeRefs.get(sourceId)
  const rawStore = (tree as unknown as { store?: unknown } | undefined)?.store
  if (!rawStore || typeof rawStore !== 'object') return null
  if ('value' in rawStore) {
    const value = (rawStore as { value?: unknown }).value
    return value && typeof value === 'object' ? value as CreateSourceLoadedTreeStore : null
  }
  return rawStore as CreateSourceLoadedTreeStore
}

function refreshCreateSourceTreeBlockedState(sourceId: string) {
  const store = createSourceTreeStore(sourceId)
  if (!store) {
    syncCreateSourceTreeCheckedKeys(sourceId)
    return
  }
  const nodes = typeof store._getAllNodes === 'function'
    ? store._getAllNodes()
    : store.nodesMap instanceof Map
      ? [...store.nodesMap.values()]
      : Object.values(store.nodesMap ?? {})

  nodes.forEach((node) => {
    const data = node.data
    if (!data?.path) return
    if (data.nodeKind === 'loadMore') return
    const disabledReason = addedDirTreeDisableReason(sourceId, data.path)
    const disabled = Boolean(disabledReason)
    if (data.disabled === disabled && (data.disabledReason || '') === disabledReason) return
    data.disabled = disabled
    data.disabledReason = disabledReason
  })
  syncCreateSourceTreeCheckedKeys(sourceId)
}

function sourceSelectedCount(sourceId: string) {
  return sourceSelectedEntries(sourceId).length
}


function closeValidationPopover() {
  validationPopoverVisible.value = false
  validationPopoverKind.value = ''
  validationPopoverTitle.value = ''
  validationPopoverItems.value = []
}

function updateValidationPopover(title: string, items: string[]) {
  if (items.length === 0) {
    closeValidationPopover()
    return
  }
  validationPopoverTitle.value = title
  validationPopoverItems.value = items
}

function syncOpenValidationPopover() {
  if (!validationPopoverVisible.value) return
  if (validationPopoverKind.value === 'source-dirs') {
    const missing = missingCreateSourceRows()
    updateValidationPopover(
      t('protection.backupsPage.validationMissingSourceDirsTitle', { count: missing.length }),
      missing.map((row) => row.name),
    )
    return
  }
  if (validationPopoverKind.value === 'target') {
    const missing = missingTargetGroups()
    updateValidationPopover(
      t('protection.backupsPage.validationMissingTargetsTitle', { count: missing.length }),
      missing.map((group) => group.sourceName),
    )
    return
  }
  if (validationPopoverKind.value === 'recovery-plan') {
    const incompletePlans = incompleteRecoveryPlans()
    updateValidationPopover(
      t('protection.backupsPage.validationIncompleteRecoveryPlansTitle', { count: incompletePlans.length }),
      incompletePlans.map(recoveryPlanIncompleteSummary),
    )
  }
}

function isCreateSourceDirsMissing(sourceId: string) {
  return sourceSelectedEntries(sourceId).length === 0
}

function createSourceDirIssue(sourceId: string) {
  if (manualSourcePathErrorBySource[sourceId]) return manualSourcePathErrorBySource[sourceId]
  if (createSourceValidationAttempted.value && isCreateSourceDirsMissing(sourceId)) {
    return t('protection.backupsPage.validationMissingSourceDirsInline')
  }
  return ''
}

function missingCreateSourceRows() {
  return createSourceRows.value.filter((row) => isCreateSourceDirsMissing(row.id))
}

function createSourceRowClassName({ row }: { row: CreateSourceRow }) {
  const classes: string[] = []
  const missing = isCreateSourceDirsMissing(row.id)
  if (createSourceValidationAttempted.value && missing) {
    classes.push('create-source-row--invalid')
  }
  if (missing && highlightedCreateSourceId.value === row.id) {
    classes.push('create-source-row--highlighted')
  }
  return classes.join(' ')
}

function focusIncompleteCreateSourceDirs() {
  const missing = missingCreateSourceRows()
  const first = missing[0]
  if (!first) {
    createSourceValidationAttempted.value = false
    highlightedCreateSourceId.value = ''
    closeValidationPopover()
    return false
  }

  createSourceValidationAttempted.value = true
  highlightedCreateSourceId.value = first.id
  closeValidationPopover()
  resetCreateSourceSearch()
  createExpandedSourceIds.value = [
    ...new Set([...createExpandedSourceIds.value, first.id]),
  ]
  void nextTick(() => {
    createSourceTableRef.value?.doLayout?.()
    const row = document.querySelector<HTMLElement>('.create-source-config-table .create-source-row--highlighted')
    row?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' })
  })

  return true
}

function formatBytes(bytes: number) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${value.toFixed(unitIndex >= 3 ? 1 : 0)} ${units[unitIndex]}`
}

function sourceRecord(sourceId: string) {
  return realSourceById.value.get(sourceId)
}

function sourceEndpointInfo(sourceId: string): { primary: string; secondary: string } {
  const source = realSourceById.value.get(sourceId)
  if (source) {
    return {
      primary: source.hostname || source.nodeName || source.name || '—',
      secondary: source.nodeIp || '—',
    }
  }
  const host = store.hosts.value.find((item) => item.id === sourceId)
  if (host) {
    return {
      primary: host.hostname || host.nodeName || host.name || '—',
      secondary: host.nodeIp || '—',
    }
  }
  const nas = store.nas.value.find((item) => item.id === sourceId)
  if (!nas) return { primary: '—', secondary: '—' }
  return {
    primary: nas.proxyNodeName || nas.hostname || nas.name || '—',
    secondary: nas.proxyNodeIp || '—',
  }
}

function sourceAvailabilityLabel(sourceId: string) {
  const availability = sourceRecord(sourceId)?.availability
  if (availability !== 'online') return t('protection.backupsPage.sourceStatusOffline')
  return t('protection.backupsPage.sourceStatusOnline')
}

function sourceAvailabilityTagType(sourceId: string) {
  return sourceRecord(sourceId)?.availability === 'online' ? 'success' : 'danger'
}

function sourceDirPreviewEntries(entries: BackupDirEntry[]): BackupDirEntry[] {
  return [...entries]
    .sort((a, b) => a.path.localeCompare(b.path, treeSortLocale()))
    .slice(0, 3)
}

function mapSourceDirectoryEntry(
  entry: BackupSourceDirectoryEntry,
  source: BackupSelectableSource | null | undefined,
): SourceTreeItem {
  const pathType = backupPathTypeForEntry(entry)
  return {
    label: entry.label || entry.path.split('/').filter(Boolean).pop() || entry.path || '/',
    path: entry.path,
    isLeaf: pathType === 'file' || Boolean(entry.isLeaf),
    is_dir: pathType === 'directory',
    path_type: pathType,
    filenameEncodingSuspected: isLikelySmbFilenameEncodingIssue({
      source,
      label: entry.label,
      path: entry.path,
    }),
  }
}

function showSourceTreeFilenamePathError(sourceId: string, path: string, err: unknown) {
  if (!isLikelySmbFilenamePathNotFound({
    source: sourceRecord(sourceId),
    path,
    error: err,
  })) return false

  notifyError({
    title: t('protection.backupsPage.dirTreeFilenameEncodingErrorTitle'),
    message: t('protection.backupsPage.dirTreeFilenameEncodingErrorMessage'),
    duration: 12000,
    dedupeKey: `smb-filename-path-not-found:${sourceId}:${path}`,
  })
  return true
}

const SOURCE_TREE_ROOT_LIMIT = 100
const SOURCE_TREE_CHILD_LIMIT = 500

type SourceTreePage = {
  entries: SourceTreeItem[]
  hasMore: boolean
  nextCursor: string
  limit: number
}

function createSourceLoadMoreKey(sourceId: string, parentPath: string, cursor: string) {
  return `__hfl_load_more__:${sourceId}:${encodeURIComponent(parentPath)}:${encodeURIComponent(cursor)}`
}

function createSourceLoadMoreItem(sourceId: string, parentPath: string, page: SourceTreePage): SourceTreeItem | null {
  if (!page.hasMore || !page.nextCursor) return null
  return {
    label: t('protection.backupsPage.dirTreeLoadMore', { count: page.limit }),
    path: createSourceLoadMoreKey(sourceId, parentPath, page.nextCursor),
    isLeaf: true,
    is_dir: false,
    path_type: 'unknown',
    disabled: true,
    nodeKind: 'loadMore',
    sourceId,
    parentPath,
    nextCursor: page.nextCursor,
  }
}

function sourceTreeItemsWithLoadMore(sourceId: string, parentPath: string, page: SourceTreePage) {
  const loadMore = createSourceLoadMoreItem(sourceId, parentPath, page)
  return loadMore ? [...page.entries, loadMore] : page.entries
}

function sourceTreeItemWithBlockedState(sourceId: string, item: SourceTreeItem): SourceTreeItem {
  if (item.nodeKind === 'loadMore') return item
  const disabledReason = addedDirTreeDisableReason(sourceId, item.path)
  return {
    ...item,
    disabled: Boolean(disabledReason),
    disabledReason,
  }
}

async function listRealSourceDirChildren(
  sourceId: string,
  parentPath: string,
  options: { includeFiles?: boolean; cursor?: string; timeout?: number; forceRefresh?: boolean } = {},
): Promise<SourceTreePage> {
  const isRoot = !parentPath
  const limit = isRoot ? SOURCE_TREE_ROOT_LIMIT : SOURCE_TREE_CHILD_LIMIT
  const result = await listBackupSourceDirectories(
    {
      source_id: sourceId,
      path: parentPath || undefined,
      timeout: options.timeout ?? (isRoot ? 10 : 30),
      limit,
      cursor: options.cursor,
      include_files: Boolean(options.includeFiles),
      include_metadata: false,
    },
    options.forceRefresh ? { cache: 'no-store' } : undefined,
  )
  const source = sourceRecord(sourceId)
  const entries = selectBackupSourceDirectoryTreeEntries({
    source,
    parentPath,
    result,
  })
  const mapped = entries
    .filter((entry) => options.includeFiles || entry.is_dir !== false)
    .map((entry) => {
      const item = mapSourceDirectoryEntry(entry, source)
      setSourcePathType(sourceId, item.path, item.path_type ?? 'unknown')
      return item
    })
  return {
    entries: mapped,
    hasMore: Boolean(result.has_more),
    nextCursor: String(result.next_cursor || ''),
    limit,
  }
}

function isCreateSourceRowExpanded(sourceId: string) {
  return createExpandedSourceIds.value.includes(sourceId)
}

function onCreateSourceExpandChange(_row: CreateSourceRow, expandedRows: CreateSourceRow[]) {
  const visibleIds = new Set(filteredCreateSourceRows.value.map((row) => row.id))
  const hiddenExpandedIds = createExpandedSourceIds.value.filter((id) => !visibleIds.has(id))
  createExpandedSourceIds.value = [...hiddenExpandedIds, ...expandedRows.map((row) => row.id)]
}

function toggleCreateSourceRow(row: CreateSourceRow) {
  createSourceTableRef.value?.toggleRowExpansion(row, !isCreateSourceRowExpanded(row.id))
}

async function openCreateSourceDetail(row: CreateSourceRow) {
  const [kind, rawId] = row.id.split(':')
  const refId = Number(rawId)
  if (!Number.isInteger(refId) || refId <= 0) return
  if (kind === 'agent') {
    createHostDetailNodeId.value = refId
    createHostDetailOpen.value = true
    return
  }
  if (kind !== 'nas' || createSourceDetailLoading.value) return
  createSourceDetailLoading.value = true
  try {
    createNasDetailResource.value = await getSourceResource(refId)
    createNasDetailOpen.value = true
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err), grouping: true })
  } finally {
    createSourceDetailLoading.value = false
  }
}

function closeCreateHostDetail() {
  createHostDetailOpen.value = false
  createHostDetailNodeId.value = null
}

async function loadSourceTreeNode(
  sourceId: string,
  node: { level: number; data?: SourceTreeItem },
  resolve: (data: SourceTreeItem[]) => void,
) {
  if (!sourceId) {
    noSourceTreeRootsBySource[sourceId] = true
    resolve([])
    return
  }
  const parentPath = node.level === 0 ? '' : String(node.data?.path ?? '')
  const loadingKey = createSourceDirectoryLoadingKey(sourceId)
  setDirectoryLoading(loadingKey, true)
  try {
    const page = await listRealSourceDirChildren(sourceId, parentPath, { includeFiles: true })
    createSourceTreeErrorBySource[sourceId] = ''
    const treeItems = sourceTreeItemsWithLoadMore(sourceId, parentPath, page)
      .map((item) => sourceTreeItemWithBlockedState(sourceId, item))
    if (node.level === 0) {
      noSourceTreeRootsBySource[sourceId] = page.entries.length === 0
    }
    resolve(treeItems)
    nextTick(() => {
      syncCreateSourceTreeCheckedKeys(sourceId)
    })
  } catch (err) {
    if (showSourceTreeFilenamePathError(sourceId, parentPath, err)) {
      createSourceTreeErrorBySource[sourceId] = ''
    } else {
      const message = apiErrorMessageI18n(err, t, t('protection.backupsPage.dirTreeLoadFailed'))
      createSourceTreeErrorBySource[sourceId] = message
      ElMessage.error({ message: message, grouping: true })
    }
    if (node.level === 0) {
      noSourceTreeRootsBySource[sourceId] = false
    }
    resolve([])
  } finally {
    setDirectoryLoading(loadingKey, false)
  }
}

function reloadCreateSourceTree(sourceId: string) {
  createSourceTreeErrorBySource[sourceId] = ''
  noSourceTreeRootsBySource[sourceId] = false
  createSourceTreeRemountKeyBySource[sourceId] = (createSourceTreeRemountKeyBySource[sourceId] ?? 0) + 1
}

function createSourceDirectoryRefreshKey(sourceId: string, path: string) {
  return `${sourceId}:${path}`
}

function isSourceDirectoryRefreshing(sourceId: string, path: string) {
  return Boolean(refreshingSourceDirectoryByKey[createSourceDirectoryRefreshKey(sourceId, path)])
}

function onSourceDirectoryExpansionChange(sourceId: string, data: SourceTreeItem) {
  if (!data.path || data.nodeKind === 'loadMore') return
  const key = createSourceDirectoryRefreshKey(sourceId, data.path)
  sourceDirectoryExpansionRevisionByKey.set(
    key,
    (sourceDirectoryExpansionRevisionByKey.get(key) ?? 0) + 1,
  )
}

async function refreshCreateSourceDirectory(sourceId: string, data: SourceTreeItem) {
  if (!sourceId || !data.path || data.path_type === 'file' || data.nodeKind === 'loadMore') return
  const tree = createSourceTreeRefs.get(sourceId)
  if (!tree) return
  const refreshKey = createSourceDirectoryRefreshKey(sourceId, data.path)
  if (refreshingSourceDirectoryByKey[refreshKey]) return
  const sourceNode = tree.getNode(data.path) as unknown as { expanded?: boolean } | null
  if (!sourceNode) return
  const wasExpanded = Boolean(sourceNode.expanded)
  const expansionRevisionAtStart = sourceDirectoryExpansionRevisionByKey.get(refreshKey) ?? 0

  refreshingSourceDirectoryByKey[refreshKey] = true
  try {
    const page = await listRealSourceDirChildren(sourceId, data.path, {
      includeFiles: true,
      forceRefresh: true,
    })
    const treeItems = sourceTreeItemsWithLoadMore(sourceId, data.path, page)
      .map((item) => sourceTreeItemWithBlockedState(sourceId, item))
    if (createSourceTreeRefs.get(sourceId) !== tree) return
    tree.updateKeyChildren(data.path, treeItems)
    const refreshedNode = tree.getNode(data.path) as unknown as {
      expanded?: boolean
      loaded?: boolean
      collapse?: () => void
      expand?: () => void
      updateLeafState?: () => void
    } | null
    if (!refreshedNode) return
    refreshedNode.loaded = true
    refreshedNode.updateLeafState?.()
    createSourceTreeErrorBySource[sourceId] = ''
    await nextTick()
    syncCreateSourceTreeCheckedKeys(sourceId)
    const hasChildren = page.entries.length > 0
    if (!hasChildren) {
      refreshedNode.collapse?.()
      ElMessage.info({
        message: t('protection.backupsPage.dirTreeRefreshEmpty', { path: data.path }),
        grouping: true,
      })
    } else {
      const expansionRevisionAfterRefresh = sourceDirectoryExpansionRevisionByKey.get(refreshKey) ?? 0
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
    }
  } catch (err) {
    if (!showSourceTreeFilenamePathError(sourceId, data.path, err)) {
      ElMessage.error({
        message: apiErrorMessageI18n(err, t, t('protection.backupsPage.dirTreeLoadFailed')),
        grouping: true,
      })
    }
  } finally {
    delete refreshingSourceDirectoryByKey[refreshKey]
  }
}

async function loadMoreSourceTreeChildren(item: SourceTreeItem) {
  if (item.nodeKind !== 'loadMore' || item.loading) return
  const sourceId = item.sourceId || ''
  const parentPath = item.parentPath || ''
  const cursor = item.nextCursor || ''
  if (!sourceId || !cursor) return
  const tree = createSourceTreeRefs.get(sourceId) as unknown as {
    getNode?: (key: string) => unknown
    remove?: (node: unknown) => void
    append?: (data: SourceTreeItem, parent?: unknown) => void
  } | undefined
  if (!tree?.append) return

  const loadingKey = createSourceDirectoryLoadingKey(sourceId)
  item.loading = true
  setDirectoryLoading(loadingKey, true)
  try {
    const page = await listRealSourceDirChildren(sourceId, parentPath, {
      includeFiles: true,
      cursor,
      timeout: 30,
    })
    createSourceTreeErrorBySource[sourceId] = ''
    const parentNode = parentPath ? tree.getNode?.(parentPath) : undefined
    const loadMoreNode = tree.getNode?.(item.path)
    if (loadMoreNode && tree.remove) {
      tree.remove(loadMoreNode)
    }
    sourceTreeItemsWithLoadMore(sourceId, parentPath, page)
      .map((entry) => sourceTreeItemWithBlockedState(sourceId, entry))
      .forEach((entry) => {
        tree.append?.(entry, parentNode)
      })
    nextTick(() => {
      syncCreateSourceTreeCheckedKeys(sourceId)
    })
  } catch (err) {
    if (showSourceTreeFilenamePathError(sourceId, parentPath, err)) {
      createSourceTreeErrorBySource[sourceId] = ''
    } else {
      const message = apiErrorMessageI18n(err, t, t('protection.backupsPage.dirTreeLoadFailed'))
      createSourceTreeErrorBySource[sourceId] = message
      ElMessage.error({ message: message, grouping: true })
    }
  } finally {
    item.loading = false
    setDirectoryLoading(loadingKey, false)
  }
}

function onSourceDirCheckChange(sourceId: string, data: SourceTreeItem, checked: boolean) {
  if (data.nodeKind === 'loadMore') {
    syncCreateSourceTreeCheckedKeys(sourceId)
    return
  }
  if ('disabled' in data && data.disabled) {
    warnSourceTreeConflict(sourceId, data)
    syncCreateSourceTreeCheckedKeys(sourceId)
    return
  }
  const next = createSourceCheckedKeys(sourceId)
  if (!checked) {
    createSourceDirKeysBySource[sourceId] = next.filter((path) => path !== data.path)
    syncCreateSourceTreeCheckedKeys(sourceId)
    return
  }
  const withoutOverlaps = next.filter((path) =>
    !(isSameOrAncestorPath(path, data.path) || isSameOrAncestorPath(data.path, path)),
  )
  createSourceDirKeysBySource[sourceId] = preserveShallowestPathOrder([...withoutOverlaps, data.path])
    .filter((path) => !isPathBlockedByAddedDir(sourceId, path))
  syncCreateSourceTreeCheckedKeys(sourceId)
}

function onSourceTreeNodeClick(sourceId: string, data: SourceTreeItem) {
  warnSourceTreeConflict(sourceId, data)
}

function onSourceTreeRowClick(sourceId: string, data: SourceTreeItem, event: MouseEvent) {
  if (warnSourceTreeConflict(sourceId, data)) {
    event.stopPropagation()
    event.preventDefault()
  }
}

function onSourceTreeClickCapture(sourceId: string, event: MouseEvent) {
  const target = event.target instanceof HTMLElement ? event.target : null
  if (target?.closest('.hfl-dir-tree-node__refresh')) return
  const nodeContent = target?.closest('.el-tree-node__content')
  if (!nodeContent) return
  const row = nodeContent.querySelector<HTMLElement>('.create-dir-row--tree[data-source-path]')
  const path = row?.dataset.sourcePath
  if (!path) return
  const reason = addedDirTreeDisableReason(sourceId, path)
  if (!reason) return
  const warned = warnSourceTreeConflict(sourceId, {
    label: basenamePath(path),
    path,
    isLeaf: true,
    disabled: true,
    disabledReason: reason,
  })
  if (!warned) return
  event.stopPropagation()
  event.preventDefault()
}

function addPickedSourceFor(sourceId: string) {
  addPickedSourcesFor([sourceId])
}

function editBackupConfigIdForSource(sourceId: string) {
  const ids = [...new Set(
    [
      ...editConfigs.value
        .filter((config) => sourceIdFromConfig(config) === sourceId)
        .map((config) => Number(config.id)),
      ...wizardSourceGroups.value
        .filter((group) => group.sourceId === sourceId && group.backupConfigId)
        .map((group) => Number(group.backupConfigId)),
    ].filter((id) => Number.isFinite(id) && id > 0),
  )]
  return ids.length === 1 ? ids[0] : undefined
}

function wizardDirEntryKey(sourceId: string, path: string, backupConfigId?: number) {
  return backupConfigId ? `${backupConfigId}||${sourceId}||${path}` : sourceKey(sourceId, path)
}

function addPickedSourcesFor(sourceIds: string[]) {
  const validSourceIds = normalizeSourceIdList(sourceIds)
  if (validSourceIds.length === 0) {
    ElMessage.warning({ message: t('protection.backupsPage.msgPickDirThenAdd'), grouping: true })
    return
  }
  const existingKeys = new Set(wizardDirEntries.value.map((entry) => entry.key))
  const addedEntries: BackupDirEntry[] = []
  let added = 0
  for (const sourceId of validSourceIds) {
    const pickedKeys = createSourceAddableCheckedKeys(sourceId)
    if (pickedKeys.length === 0) continue
    const source = realSourceById.value.get(sourceId)
    const sourceType = getSourceType(sourceId)
    const sourceName = getSourceName(sourceId)
    const backupConfigId = isEditMode.value ? editBackupConfigIdForSource(sourceId) : undefined
    for (const path of pickedKeys) {
      const key = wizardDirEntryKey(sourceId, path, backupConfigId)
      if (existingKeys.has(key)) continue
      addedEntries.push({
        key,
        backupConfigId,
        sourceId,
        sourceName,
        sourceType,
        platform: source?.platform,
        path,
        pathType: sourcePathType(sourceId, path),
      })
      existingKeys.add(key)
      added += 1
    }
  }
  if (added === 0) {
    ElMessage.warning({ message: t('protection.backupsPage.msgDirAlreadyInList'), grouping: true })
    return
  }
  wizardDirEntries.value = [...addedEntries.reverse(), ...wizardDirEntries.value]
  addedEntries.forEach((entry) => {
    const groupKey = sourceGroupKey(entry)
    if (!nasTargetModes[groupKey]) {
      nasTargetModes[groupKey] = 'per_directory_repo'
    }
  })
  validSourceIds.forEach((sourceId) => {
    createSourceDirKeysBySource[sourceId] = []
    refreshCreateSourceTreeBlockedState(sourceId)
  })
}

function isManualSourcePathValidating(sourceId: string) {
  return Boolean(manualSourcePathValidatingBySource[sourceId])
}

async function addManualSourcePath(sourceId: string) {
  const path = String(manualSourcePathBySource[sourceId] || '').trim()
  clearManualSourcePathError(sourceId)
  if (!path) {
    const message = t('protection.backupsPage.msgManualPathRequired')
    setManualSourcePathError(sourceId, message)
    ElMessage.warning({ message, grouping: true })
    return
  }
  if (!isAbsoluteSourcePath(path)) {
    const message = t('protection.backupsPage.msgManualPathAbsolute')
    setManualSourcePathError(sourceId, message)
    ElMessage.warning({ message, grouping: true })
    return
  }
  const blockedReason = addedDirBlockReason(sourceId, path)
  if (blockedReason) {
    setManualSourcePathError(sourceId, blockedReason)
    ElMessage.warning({ message: blockedReason, grouping: true })
    return
  }
  manualSourcePathValidatingBySource[sourceId] = true
  let pathInfo: Awaited<ReturnType<typeof getBackupSourcePathInfo>>
  try {
    pathInfo = await getBackupSourcePathInfo({
      source_id: sourceId,
      path,
      timeout: 10,
      include_metadata: false,
    })
  } catch (err) {
    const message = manualPathValidationErrorMessage(err, sourceId, path)
    setManualSourcePathError(sourceId, message)
    highlightedCreateSourceId.value = sourceId
    ElMessage.error({ message, grouping: true })
    return
  } finally {
    manualSourcePathValidatingBySource[sourceId] = false
  }
  const resolvedPath = pathInfo.path || path
  const resolvedPathType = backupPathTypeForEntry(pathInfo)
  const resolvedBlockedReason = addedDirBlockReason(sourceId, resolvedPath)
  if (resolvedBlockedReason) {
    setManualSourcePathError(sourceId, resolvedBlockedReason)
    ElMessage.warning({ message: resolvedBlockedReason, grouping: true })
    return
  }
  const source = realSourceById.value.get(sourceId)
  const sourceType = getSourceType(sourceId)
  const sourceName = getSourceName(sourceId)
  const backupConfigId = isEditMode.value ? editBackupConfigIdForSource(sourceId) : undefined
  const entry: BackupDirEntry = {
    key: wizardDirEntryKey(sourceId, resolvedPath, backupConfigId),
    backupConfigId,
    sourceId,
    sourceName,
    sourceType,
    platform: source?.platform,
    path: resolvedPath,
    pathType: resolvedPathType,
  }
  wizardDirEntries.value = [entry, ...wizardDirEntries.value]
  setSourcePathType(sourceId, resolvedPath, resolvedPathType)
  const groupKey = sourceGroupKey(entry)
  if (!nasTargetModes[groupKey]) {
    nasTargetModes[groupKey] = 'per_directory_repo'
  }
  manualSourcePathBySource[sourceId] = ''
  clearManualSourcePathError(sourceId)
  refreshCreateSourceTreeBlockedState(sourceId)
}

function removeWizardDirEntry(key: string) {
  const removed = wizardDirEntries.value.find((entry) => entry.key === key)
  wizardDirEntries.value = wizardDirEntries.value.filter((x) => x.key !== key)
  if (removed) {
    if (!sourceSelectedEntries(removed.sourceId).length) {
      createSourceDirKeysBySource[removed.sourceId] = []
    }
    nextTick(() => refreshCreateSourceTreeBlockedState(removed.sourceId))
  }
}

watch(
  wizardDirEntries,
  (entries) => {
    const groupKeys = new Set(entries.map((entry) => sourceGroupKey(entry)))

    ;[...wizardSourceGroups.value].forEach((group) => {
      if (sourceFilterMap.value[group.key] === undefined) {
        sourceFilterMap.value = { ...sourceFilterMap.value, [group.key]: [] }
      }
      if (sourcePolicyMap.value[group.key] === undefined) {
        sourcePolicyMap.value = { ...sourcePolicyMap.value, [group.key]: '' }
      }
      if (sourceCompressionMap.value[group.key] === undefined) {
        sourceCompressionMap.value = { ...sourceCompressionMap.value, [group.key]: 'balanced' }
      }
      if (!nasTargetModes[group.key]) {
        nasTargetModes[group.key] = 'per_directory_repo'
      }
      const currentPlan = createRecoveryPlanMap.value[group.key]
      const groupPaths = group.entries.map((entry) => entry.path)
      if (!currentPlan) {
        createRecoveryPlanMap.value = {
          ...createRecoveryPlanMap.value,
          [group.key]: defaultRecoveryPlanForGroup(group),
        }
      } else {
        const nextDirPlans = currentPlan.dirPlans.filter((dirPlan) =>
          isWholeSnapshotRecoveryPath(dirPlan.sourcePath) || groupPaths.some((path) => isSameOrAncestorPath(path, dirPlan.sourcePath)),
        )
        createRecoveryPlanMap.value = {
          ...createRecoveryPlanMap.value,
          [group.key]: {
            ...currentPlan,
            dirPlans: nextDirPlans.length > 0 ? nextDirPlans : [createDefaultRecoveryDirPlan(group)],
          },
        }
      }
    })

    Object.keys(nasTargetModes).forEach((key) => {
      if (!groupKeys.has(key)) delete nasTargetModes[key]
    })
    const nextSourceTargetMap = { ...sourceTargetMap.value }
    Object.keys(nextSourceTargetMap).forEach((key) => {
      if (!groupKeys.has(key)) delete nextSourceTargetMap[key]
    })
    sourceTargetMap.value = nextSourceTargetMap
    const nextSourceEndpointTypeMap = { ...sourceEndpointTypeMap.value }
    Object.keys(nextSourceEndpointTypeMap).forEach((key) => {
      if (!groupKeys.has(key)) delete nextSourceEndpointTypeMap[key]
    })
    sourceEndpointTypeMap.value = nextSourceEndpointTypeMap
    const nextRecoveryPlanMap = { ...createRecoveryPlanMap.value }
    Object.keys(nextRecoveryPlanMap).forEach((key) => {
      if (!groupKeys.has(key)) delete nextRecoveryPlanMap[key]
    })
    createRecoveryPlanMap.value = nextRecoveryPlanMap

    targetAssignmentCheckedGroupKeys.value = targetAssignmentCheckedGroupKeys.value.filter((key) => groupKeys.has(key))
    filterPolicyAssignmentCheckedGroupKeys.value = filterPolicyAssignmentCheckedGroupKeys.value.filter((key) => groupKeys.has(key))
    recoveryPlanCheckedGroupKeys.value = recoveryPlanCheckedGroupKeys.value.filter((key) => groupKeys.has(key))
    const nextSourceFilterMap = { ...sourceFilterMap.value }
    Object.keys(nextSourceFilterMap).forEach((key) => {
      if (!groupKeys.has(key)) delete nextSourceFilterMap[key]
    })
    sourceFilterMap.value = nextSourceFilterMap
    const nextSourcePolicyMap = { ...sourcePolicyMap.value }
    const nextSourceCompressionMap = { ...sourceCompressionMap.value }
    Object.keys(nextSourcePolicyMap).forEach((key) => {
      if (!groupKeys.has(key)) delete nextSourcePolicyMap[key]
    })
    Object.keys(nextSourceCompressionMap).forEach((key) => {
      if (!groupKeys.has(key)) delete nextSourceCompressionMap[key]
    })
    sourcePolicyMap.value = nextSourcePolicyMap
    sourceCompressionMap.value = nextSourceCompressionMap

  },
  { immediate: true },
)

function closeCreate() {
  cancelTargetValidation()
  if (props.embedded) {
    createOpen.value = false
    emit('closed')
    return
  }
  if (route.path === '/protection/backups/create') {
    const rawReturnStep = Array.isArray(route.query.returnStep)
      ? route.query.returnStep[0]
      : route.query.returnStep
    const returnStep = rawReturnStep === '2' ? '2' : '1'
    const step = returnStep === '1'
      ? 'backup-config'
      : returnStep === '2'
        ? 'start-backup'
        : ''
    if (typeof sessionStorage !== 'undefined') {
      sessionStorage.setItem(FLOW_RETURN_STEP_STORAGE_KEY, returnStep)
    }
    void router.push({ path: '/protection/backups', query: { step } })
    return
  }
  createOpen.value = false
}

function isCreateStepDone(step: number) {
  return createCompletedSteps.value.has(step)
}

function isCreateStepLocked(step: number) {
  if (step <= 0) return false
  for (let current = 0; current < step; current += 1) {
    if (!createCompletedSteps.value.has(current)) return true
  }
  return false
}

function markCreateStepDone(step: number) {
  createCompletedSteps.value = new Set(createCompletedSteps.value).add(step)
}

function resetCreateCompletedFrom(step: number) {
  const next = new Set(createCompletedSteps.value)
  for (let current = step; current < createWizardSteps.value.length; current += 1) {
    next.delete(current)
  }
  createCompletedSteps.value = next
}

function validateCreateStep(step: number) {
  if (step === 0) {
    if (focusIncompleteCreateSourceDirs()) {
      return false
    }
  }
  if (step === 2) {
    if (focusIncompleteTargetGroup()) {
      return false
    }
  }
  if (step === 3) {
    if (focusIncompleteRecoveryPlan()) {
      return false
    }
  }
  return true
}

function clearCreateStepSearch(step: number) {
  if (step === 0) resetCreateSourceSearch()
  if (step === 1) resetFilterPolicySourceSearch()
  if (step === 2) resetTargetSourceSearch()
  if (step === 3) resetRecoveryPlanSourceSearch()
}

function goCreateStep(step: number) {
  if (step === createStep.value) return
  closeValidationPopover()
  clearCreateStepSearch(createStep.value)
  createStep.value = step
}

async function nextCreate() {
  if (targetValidationInProgress.value || isCreateStepLocked(createStep.value)) return
  if (!validateCreateStep(createStep.value)) return
  if (createStep.value === 2 && !await validateCurrentBackupTargets()) return
  markCreateStepDone(createStep.value)
  if (createStep.value < createWizardSteps.value.length - 1) {
    goCreateStep(createStep.value + 1)
  }
}

function prevCreate() {
  if (targetValidationInProgress.value || createStep.value <= 0) return
  const nextStep = createStep.value - 1
  resetCreateCompletedFrom(nextStep)
  goCreateStep(nextStep)
}

function defaultCreateBackupNameForGroup(group: WizardSourceGroup, index: number) {
  const base = t('protection.backupsPage.nameDefaultPrefix')
  const order = String(index + 1).padStart(2, '0')
  return `${base}-${order}-${group.sourceName}`
}

function createBackupNameForGroup(group: WizardSourceGroup, index: number) {
  return defaultCreateBackupNameForGroup(group, index)
}

function buildCreateBackupPayload() {
  return {
    backups: wizardSourceGroups.value.map((group, index) => ({
      key: group.key,
      backupConfigId: group.backupConfigId,
      name: createBackupNameForGroup(group, index),
      source: {
        id: group.sourceId,
        name: group.sourceName,
        type: group.sourceType,
        paths: group.entries.map((entry) => entry.path),
        pathItems: group.entries.map((entry) => ({
          path: entry.path,
          path_type: entry.pathType,
        })),
      },
      targetId: sourceTargetMap.value[group.key] || '',
      repositoryEndpointType: endpointTypeForGroup(group.key),
      policyId: sourcePolicyMap.value[group.key] || selPolicy.value,
      globalFilterId: groupFilterIds(group)[0] || selGlobalFilter.value,
      globalFilterIds: groupFilterIds(group),
      compression: sourceCompressionMap.value[group.key] || 'balanced',
      nasTargetMode: nasTargetModes[group.key] ?? 'per_directory_repo',
      recoveryPlan: createRecoveryPlanMap.value[group.key],
      remark: '',
    })),
  }
}

const batchSelectedTarget = computed(() =>
  batchTargetPicker.targetId ? getRealTarget(batchTargetPicker.targetId) : null,
)

const batchSelectedTargetRequiresEndpoint = computed(() =>
  targetHasDistinctEndpoints(batchSelectedTarget.value),
)

const batchSelectedTargetIsNas = computed(() => batchSelectedTarget.value?.repoType === 'NAS')

const activeTargetAssignGroup = computed(() =>
  wizardSourceGroups.value.find((group) => group.key === targetAssignActiveGroupKey.value) ?? null,
)

function targetOptionsForBatch() {
  if (targetAssignDialogMode.value === 'single') {
    return targetOptionsForGroup(activeTargetAssignGroup.value)
  }
  return targetOptionsForCheckedGroups()
}

const targetAssignDialogTitle = computed(() =>
  targetAssignDialogMode.value === 'batch'
    ? t('protection.backupsPage.batchAssignTitle')
    : t('protection.backupsPage.targetRepositoryListLabel'),
)

function inferNasModeForTarget(target: WizardTarget | null | undefined): NasTargetMode {
  if (!target) return 'per_directory_repo'
  if (target.repoType !== 'NAS') return 'per_directory_repo'
  if (target.bindNodeType === 'proxy' && target.bindNodeId) return 'single_repo'
  return 'per_directory_repo'
}

function resetTargetAssignPicker(
  targetId = '',
  nasMode?: NasTargetMode,
  endpointType: RepositoryEndpointType | '' = '',
) {
  batchTargetPicker.search = ''
  batchTargetPicker.repoType = ''
  batchTargetPicker.targetId = targetId
  const resolved = nasMode ?? inferNasModeForTarget(getRealTarget(targetId))
  batchNasTargetMode.value = resolved
  const target = getRealTarget(targetId)
  batchRepositoryEndpointType.value = targetHasDistinctEndpoints(target)
    ? endpointType
    : target
      ? 'external'
      : ''
}

function onTargetPickerTargetChange(value: string) {
  const targetId = String(value || '')
  batchTargetPicker.targetId = targetId
  const target = getRealTarget(targetId)
  batchRepositoryEndpointType.value = targetHasDistinctEndpoints(target)
    ? ''
    : target
      ? 'external'
      : ''
}

async function openBatchTargetDialog() {
  await refreshWizardTargets()
  targetAssignDialogMode.value = 'batch'
  targetAssignActiveGroupKey.value = ''
  resetTargetAssignPicker()
  targetAssignDialogOpen.value = true
}

async function openSingleTargetDialog(group: WizardSourceGroup) {
  await refreshWizardTargets()
  targetAssignDialogMode.value = 'single'
  targetAssignActiveGroupKey.value = group.key
  const currentTargetId = sourceTargetMap.value[group.key] || ''
  const currentTarget = getRealTarget(currentTargetId)
  const initialTargetId = isSelectableTarget(currentTarget)
    && targetIncompatibilityReasonForGroup(currentTarget, group) === null
    ? currentTargetId
    : ''
  const targetDerivedMode = inferNasModeForTarget(currentTarget)
  const storedMode = nasTargetModes[group.key]
  const initialMode: NasTargetMode = currentTarget?.repoType === 'NAS'
    ? targetDerivedMode
    : (storedMode ?? targetDerivedMode)
  resetTargetAssignPicker(
    initialTargetId,
    initialMode,
    initialTargetId ? sourceEndpointTypeMap.value[group.key] || '' : '',
  )
  targetAssignDialogOpen.value = true
}

function isTargetGroupChecked(groupKey: string) {
  return targetAssignmentCheckedGroupKeys.value.includes(groupKey)
}

function onTargetGroupSelectionChange(rows: WizardSourceGroup[]) {
  targetAssignmentCheckedGroupKeys.value = rows.map((row) => row.key)
}

const checkedTargetGroups = computed(() =>
  wizardSourceGroups.value.filter((group) => isTargetGroupChecked(group.key)),
)

function isTargetGroupMissing(group: WizardSourceGroup) {
  const targetId = sourceTargetMap.value[group.key]
  const target = getRealTarget(targetId)
  if (!targetId || !target) return true
  return targetHasDistinctEndpoints(target) && !sourceEndpointTypeMap.value[group.key]
}

function targetGroupIssue(group: WizardSourceGroup) {
  const targetId = sourceTargetMap.value[group.key]
  const target = getRealTarget(targetId)
  if (!targetId || !target) return t('protection.backupsPage.targetMissingInline')
  if (targetHasDistinctEndpoints(target) && !sourceEndpointTypeMap.value[group.key]) {
    return t('protection.backupsPage.targetEndpointMissingInline')
  }
  return ''
}

function missingTargetGroups() {
  return wizardSourceGroups.value.filter((group) => isTargetGroupMissing(group))
}

function targetGroupRowClassName({ row }: { row: WizardSourceGroup }) {
  const classes: string[] = []
  const missing = isTargetGroupMissing(row)
  const connectionFailed = targetConnectionResults.value[row.key]?.status === 'failed'
  if (targetValidationAttempted.value && missing) {
    classes.push('target-group-row--invalid')
  }
  if (connectionFailed) {
    classes.push('target-group-row--connection-failed')
  }
  if ((missing || connectionFailed) && highlightedTargetGroupKey.value === row.key) {
    classes.push('target-group-row--highlighted')
  }
  return classes.join(' ')
}

function clearTargetConnectionResults() {
  targetConnectionResults.value = {}
  if (!targetValidationAttempted.value) highlightedTargetGroupKey.value = ''
}

function cancelTargetValidation() {
  targetValidationController?.abort()
  targetValidationController = null
  targetValidationInProgress.value = false
}

function targetConnectionResult(groupKey: string) {
  return targetConnectionResults.value[groupKey]
}

const targetValidationCounts = computed(() => {
  let succeeded = 0
  let failed = 0
  for (const group of wizardSourceGroups.value) {
    const result = targetConnectionResult(group.key)
    if (result?.status === 'success') succeeded += 1
    if (result?.status === 'failed') failed += 1
  }
  return { succeeded, failed }
})

const targetValidationHasFailures = computed(() => targetValidationCounts.value.failed > 0)

function targetConnectionFailureSummary(group: WizardSourceGroup) {
  const result = targetConnectionResult(group.key)
  if (!result || result.status !== 'failed') return ''
  return backupTargetValidationFailureSummary({
    result,
    sourceName: group.sourceName,
    t,
  })
}

function showTargetConnectionFailureDetails(group: WizardSourceGroup) {
  const result = targetConnectionResult(group.key)
  if (!result || result.status !== 'failed') return
  openErrorDetails(backupTargetValidationFailureDetails({
    result,
    sourceName: group.sourceName,
    t,
  }))
}

async function focusFirstFailedTargetGroup(results: Record<string, BackupTargetValidationResult>) {
  const first = wizardSourceGroups.value.find((group) => results[group.key]?.status === 'failed')
  highlightedTargetGroupKey.value = first?.key || ''
  resetTargetSourceSearch()
  await nextTick()
  const row = document.querySelector<HTMLElement>('.create-target-config-table .target-group-row--highlighted')
  row?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' })
}

async function validateCurrentBackupTargets() {
  const groups = [...wizardSourceGroups.value]
  if (groups.length === 0) return true
  targetValidationController?.abort()
  const controller = new AbortController()
  targetValidationController = controller
  targetValidationInProgress.value = true
  clearTargetConnectionResults()
  let timedOut = false
  const timeoutId = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, TARGET_VALIDATION_CLIENT_TIMEOUT_MS)
  try {
    const response = await validateProtectionBackupTargets({
      sources: groups.map((group) => ({
        key: group.key,
        source_type: group.sourceType === 'nas' ? 'nas' : 'agent',
        source_ref_id: parseSourceRefId(group.sourceId),
        repository_id: Number(sourceTargetMap.value[group.key]),
        repository_endpoint_type: endpointTypeForGroup(group.key),
      })),
    }, { signal: controller.signal })
    const mapped = Object.fromEntries(response.results.map((result) => [result.key, result]))
    for (const group of groups) {
      if (!mapped[group.key]) {
        mapped[group.key] = {
          key: group.key,
          status: 'failed',
          code: 'TARGET_CONNECTION_FAILED',
          message: t('protection.backupsPage.targetValidationMissingResult'),
        }
      }
    }
    targetConnectionResults.value = mapped
    const failed = groups.some((group) => mapped[group.key]?.status !== 'success')
    if (failed) {
      await focusFirstFailedTargetGroup(mapped)
      return false
    }
    highlightedTargetGroupKey.value = ''
    return true
  } catch (err) {
    if (timedOut) {
      ElMessage.error({
        message: t('protection.backupsPage.targetValidationTimedOut'),
        grouping: true,
      })
    } else if (!isAbortError(err)) {
      ElMessage.error({
        message: apiErrorMessageI18n(err, t, t('protection.backupsPage.targetValidationRequestFailed')),
        grouping: true,
      })
    }
    return false
  } finally {
    window.clearTimeout(timeoutId)
    if (targetValidationController === controller) {
      targetValidationController = null
      targetValidationInProgress.value = false
    }
  }
}

function focusIncompleteTargetGroup() {
  const missing = missingTargetGroups()
  const first = missing[0]
  if (!first) {
    targetValidationAttempted.value = false
    highlightedTargetGroupKey.value = ''
    closeValidationPopover()
    return false
  }

  targetValidationAttempted.value = true
  highlightedTargetGroupKey.value = first.key
  closeValidationPopover()
  resetTargetSourceSearch()
  void nextTick(() => {
    const row = document.querySelector<HTMLElement>('.create-target-config-table .target-group-row--highlighted')
    row?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' })
  })

  return true
}

function isFilterPolicyGroupChecked(groupKey: string) {
  return filterPolicyAssignmentCheckedGroupKeys.value.includes(groupKey)
}

function onFilterPolicyGroupSelectionChange(rows: WizardSourceGroup[]) {
  filterPolicyAssignmentCheckedGroupKeys.value = rows.map((row) => row.key)
}

const checkedFilterPolicyGroups = computed(() =>
  wizardSourceGroups.value.filter((group) => isFilterPolicyGroupChecked(group.key)),
)

function isRecoveryPlanGroupChecked(groupKey: string) {
  return recoveryPlanCheckedGroupKeys.value.includes(groupKey)
}

function onRecoveryPlanGroupSelectionChange(rows: CreateRecoveryPlanTableRow[]) {
  recoveryPlanCheckedGroupKeys.value = rows.map((row) => row.key)
}

const checkedRecoveryPlanGroups = computed(() =>
  createRecoveryPlanGroups.value.filter((group) => isRecoveryPlanGroupChecked(group.key)),
)

const checkedRecoveryPlanEnabledGroups = computed(() =>
  checkedRecoveryPlanGroups.value.filter((group) => group.plan.enabled),
)

const checkedRecoveryPlanDisabledGroups = computed(() =>
  checkedRecoveryPlanGroups.value.filter((group) => !group.plan.enabled),
)

function applyBatchRecoveryPlanEnabled(enabled: boolean) {
  const checkedGroups = checkedRecoveryPlanGroups.value
  if (checkedGroups.length === 0) {
    ElMessage.warning({ message: t('protection.backupsPage.msgPickDirsForBatchRecoveryPlan'), grouping: true })
    return
  }
  const targetGroups = checkedGroups.filter((group) => group.plan.enabled !== enabled)
  if (targetGroups.length === 0) return

  const next = { ...createRecoveryPlanMap.value }
  targetGroups.forEach((group) => {
    const current = recoveryPlanForGroup(group)
    const dirPlans = current.dirPlans.length ? current.dirPlans : [createDefaultRecoveryDirPlan(group)]
    next[group.key] = { ...current, enabled, dirPlans }
  })
  createRecoveryPlanMap.value = next

  if (enabled) {
    createRecoveryPlanExpandedKeys.value = [
      ...new Set([...createRecoveryPlanExpandedKeys.value, ...targetGroups.map((group) => group.key)]),
    ]
    nextTick(() => createRecoveryPlanTableRef.value?.doLayout?.())
    ElMessage.success({ message: t('protection.backupsPage.msgBatchRecoveryPlanEnabled', { n: targetGroups.length }), grouping: true })
    return
  }

  const disabledKeys = new Set(targetGroups.map((group) => group.key))
  createRecoveryPlanExpandedKeys.value = createRecoveryPlanExpandedKeys.value.filter((key) => !disabledKeys.has(key))
  ElMessage.success({ message: t('protection.backupsPage.msgBatchRecoveryPlanDisabled', { n: targetGroups.length }), grouping: true })
}

const filterPolicyBatchDialogTitle = computed(() => {
  if (activeFilterPolicyBatchOperation.value === 'policy') {
    return t('protection.backupsPage.batchPolicyDialogTitle')
  }
  if (activeFilterPolicyBatchOperation.value === 'filter') {
    return t('protection.backupsPage.batchFileFilterDialogTitle')
  }
  return t('protection.backupsPage.batchCompressionDialogTitle')
})

async function openFilterPolicyBatchDialog(command: string | number | object) {
  const operation = String(command) as FilterPolicyBatchOperation
  if (!['policy', 'filter', 'compression'].includes(operation)) return
  if (operation === 'policy') await refreshWizardPolicies()
  if (operation === 'filter') await refreshWizardFilters()
  activeFilterPolicyBatchOperation.value = operation
  filterPolicyBatchDialogOpen.value = true
}

function applyBatchFilterPolicy() {
  const checkedGroups = checkedFilterPolicyGroups.value
  if (checkedGroups.length === 0) {
    ElMessage.warning({ message: t('protection.backupsPage.msgPickDirsForBatchFilter'), grouping: true })
    return
  }
  const operation = activeFilterPolicyBatchOperation.value
  if (operation === 'policy' && !batchPolicyId.value) {
    ElMessage.warning({ message: t('protection.backupsPage.msgPickBatchPolicy'), grouping: true })
    return
  }
  if (operation === 'filter' && batchFilterIds.value.length === 0) {
    ElMessage.warning({ message: t('protection.backupsPage.msgPickBatchFilter'), grouping: true })
    return
  }
  if (operation === 'compression' && !batchCompression.value) {
    ElMessage.warning({ message: t('protection.backupsPage.msgPickBatchCompression'), grouping: true })
    return
  }

  const nextPolicyMap = { ...sourcePolicyMap.value }
  const nextFilterMap = { ...sourceFilterMap.value }
  const nextCompressionMap = { ...sourceCompressionMap.value }
  checkedGroups.forEach((group) => {
    if (operation === 'policy') nextPolicyMap[group.key] = batchPolicyId.value
    if (operation === 'filter') nextFilterMap[group.key] = [...batchFilterIds.value]
    if (operation === 'compression') nextCompressionMap[group.key] = batchCompression.value as CompressionLevel
  })
  sourcePolicyMap.value = nextPolicyMap
  sourceFilterMap.value = nextFilterMap
  sourceCompressionMap.value = nextCompressionMap
  filterPolicyBatchDialogOpen.value = false
  ElMessage.success({ message: t('protection.backupsPage.msgBatchFilterApplied', { n: checkedGroups.length }), grouping: true })
}

function groupPolicyId(group: WizardSourceGroup) {
  return sourcePolicyMap.value[group.key] ?? ''
}

function setGroupPolicyId(group: WizardSourceGroup, policyId: string) {
  sourcePolicyMap.value = { ...sourcePolicyMap.value, [group.key]: policyId }
}

function groupFilterIds(group: WizardSourceGroup) {
  const value = sourceFilterMap.value[group.key]
  return Array.isArray(value) ? value : []
}

function groupFilterId(group: WizardSourceGroup) {
  return groupFilterIds(group)[0] ?? ''
}

function setGroupFilterId(group: WizardSourceGroup, filterId: string) {
  sourceFilterMap.value = {
    ...sourceFilterMap.value,
    [group.key]: filterId ? [filterId] : [],
  }
}


function groupCompression(group: WizardSourceGroup): CompressionLevel {
  return sourceCompressionMap.value[group.key] ?? 'balanced'
}

function setGroupCompression(group: WizardSourceGroup, compression: CompressionLevel) {
  sourceCompressionMap.value = { ...sourceCompressionMap.value, [group.key]: compression }
}

type PolicyRetentionDetailLine = {
  label?: string
  text: string
}

function policyStateLabel(active: boolean) {
  return active
    ? t('protection.policiesPage.statusOn')
    : t('protection.policiesPage.statusOff')
}

function policyStateTagAttrs(active: boolean) {
  return booleanStatusTag(active)
}

function policyScheduleValue(policy: WizardPolicy) {
  if (policy.scheduleEnabled === false) return t('protection.backupsPage.policyConfigNotConfigured')
  return policy.backupFrequencyDesc || policy.schedule || t('protection.backupsPage.policyConfigNotConfigured')
}

function policyRetentionValue(policy: WizardPolicy | null | undefined) {
  return policyRetentionListSummary(policy)
}

function policyUsageLabel() {
  return t('protection.policiesPage.appliedToBackupSourcesLabel')
}

function policyUsageValue(count: number) {
  const n = Number(count) || 0
  if (messageLocale.value === 'en') {
    if (n === 1) return '1 Backup Source'
  }
  return t('protection.policiesPage.appliedToBackupSourcesCount', { n })
}

function policyOptionSummary(policy: WizardPolicy) {
  return policyScheduleValue(policy)
}

function policySelectedSummary(policy: WizardPolicy | null | undefined) {
  if (!policy) return t('protection.backupsPage.policyNoneOptional')
  return policyScheduleValue(policy)
}

function policySearchText(policy: WizardPolicy) {
  return [
    policy.name,
    policyStateLabel(policy.isActive),
    policyScheduleValue(policy),
    policyRetentionValue(policy),
  ].join(' ').toLowerCase()
}

function policyDetailRows(policy: WizardPolicy | null | undefined) {
  if (!policy) return []
  return [
    { label: t('protection.policiesPage.fieldSchedule'), value: policyScheduleValue(policy) },
    { label: policyUsageLabel(), value: policyUsageValue(policy.relatedBackupCount) },
  ]
}

function policyRetentionDetailLines(policy: WizardPolicy | null | undefined): PolicyRetentionDetailLine[] {
  const f = policy?.formData
  if (!f || !f.sectionRetentionEnabled) {
    return [{ text: t('protection.backupsPage.policyConfigNotConfigured') }]
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
  if (f.retentionHourlyEnabled) {
    lines.push({
      label: `${t('protection.policiesPage.hourlyTitle')}:`,
      text: t('protection.policiesPage.retentionHourlyDetail', { n: f.retentionHourlyHours }),
    })
  }
  if (f.retentionDailyEnabled) {
    lines.push({
      label: `${t('protection.policiesPage.dailyTitle')}:`,
      text: t('protection.policiesPage.retentionDailyDetail', { n: f.retentionDailyDays }),
    })
  }
  if (f.retentionWeeklyEnabled) {
    lines.push({
      label: `${t('protection.policiesPage.weeklyTitle')}:`,
      text: t('protection.policiesPage.retentionWeeklyDetail', { n: f.retentionWeeklyWeeks }),
    })
  }
  if (f.retentionMonthlyEnabled) {
    lines.push({
      label: `${t('protection.policiesPage.monthlyTitle')}:`,
      text: t('protection.policiesPage.retentionMonthlyDetail', { n: f.retentionMonthlyMonths }),
    })
  }
  if (f.retentionAnnualEnabled) {
    lines.push({
      label: `${t('protection.policiesPage.annualTitle')}:`,
      text: t('protection.policiesPage.retentionAnnualDetail', { n: f.retentionAnnualYears }),
    })
  }
  return lines
}

function policyRetentionListSummary(policy: WizardPolicy | null | undefined): string {
  const f = policy?.formData
  if (!f || !f.sectionRetentionEnabled) return t('protection.backupsPage.policyConfigNotConfigured')
  const recentPoints = Number(f.retentionRecentPoints)
  return t(
    recentPoints === 1
      ? 'protection.policiesPage.retentionLatestSummaryOne'
      : 'protection.policiesPage.retentionLatestSummaryMany',
    { n: recentPoints },
  )
}

function filterFormView(filter: WizardFilter) {
  return fileFilterRuleToForm(filter.raw)
}

function filterLargeFileLabel(filter: WizardFilter) {
  return filter.largeFileLimitEnabled
    ? fmtBytes(filter.largeFileBytesMax)
    : t('protection.policiesPage.previewNoLimit')
}

function filterExcludedExtensions(filter: WizardFilter) {
  return getFilterExcludedExtensions(filterFormView(filter), messageLocale.value)
}

function filterExcludedPaths(filter: WizardFilter) {
  return getFilterExcludedPaths(filterFormView(filter), messageLocale.value)
}

function filterCompiledRuleLines(filter: WizardFilter | null | undefined) {
  if (!filter) return []
  return compileFilterIgnorePatterns(filterFormView(filter))
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
}

function filterDisplayedRuleLines(filter: WizardFilter | null | undefined) {
  return filterCompiledRuleLines(filter)
}

function filterMaxSizeLimitValue(filter: WizardFilter) {
  return `>${filterLargeFileLabel(filter)}`
}

function filterEnabledLabel() {
  return t('protection.policiesPage.statusOn')
}

function filterHoverRows(filter: WizardFilter | null | undefined) {
  if (!filter) return []
  const rows: Array<{ label: string, value: string, enabled?: boolean }> = [
    { label: policyUsageLabel(), value: policyUsageValue(filter.relatedBackupCount) },
  ]
  if (filter.largeFileLimitEnabled) {
    rows.push({ label: t('protection.policiesPage.previewMaxSizeLimit'), value: filterMaxSizeLimitValue(filter) })
  }
  if (filterFormView(filter).ignoreCacheDirectories) {
    rows.push({ label: t('protection.policiesPage.cacheTitle'), value: filterEnabledLabel(), enabled: true })
  }
  if (filterFormView(filter).currentFilesystemOnly) {
    rows.push({ label: t('protection.policiesPage.fsOnlyTitle'), value: filterEnabledLabel(), enabled: true })
  }
  return rows
}

function filterOptionSummary(filter: WizardFilter) {
  return filter.summary || `${t('protection.policiesPage.previewMaxSizeLimit')}: ${filterLargeFileLabel(filter)}`
}

function filterSelectedSummary(filter: WizardFilter | null | undefined) {
  if (!filter) return t('protection.backupsPage.filterNoneOptional')
  return filterOptionSummary(filter)
}

function filterSearchText(filter: WizardFilter) {
  return [
    filter.name,
    policyStateLabel(filter.isActive),
    filterOptionSummary(filter),
    filterCompiledRuleLines(filter).join(' '),
    filterExcludedExtensions(filter),
    filterExcludedPaths(filter),
  ].join(' ').toLowerCase()
}


function filterNames(ids: string[]) {
  if (!ids.length) return t('protection.backupsPage.filterNoneOptional')
  return ids
    .map((id) => getRealFilter(id)?.name)
    .filter((name): name is string => Boolean(name))
    .join(' / ') || t('protection.backupsPage.filterNoneOptional')
}

function filterSummaryLines(ids: string[]) {
  return ids
    .map((id) => getRealFilter(id))
    .filter((filter): filter is NonNullable<ReturnType<typeof getRealFilter>> => Boolean(filter))
    .map((filter) => `${filter.name}: ${filter.summary || '—'}`)
}


function closeAddFilterDialog() {
  addFilterOpen.value = false
}

async function submitAddFilterDialog() {
  if (addPolicyCreateKind.value === 'filter') {
    const name = addFileFilterForm.value.name.trim()
    if (!name) {
      ElMessage.warning({ message: t('protection.policiesPage.msgNameRequired'), grouping: true })
      return
    }
    addPolicySaving.value = true
    try {
      const snapshot = JSON.parse(JSON.stringify({ ...addFileFilterForm.value, name })) as FileFilterRuleForm
      const created = await createFileFilterRule(fileFilterFormToWritePayload(snapshot))
      const id = String(created.id)
      realFilters.value = [mapFilter(created), ...realFilters.value.filter((item) => item.id !== id)]
      const emptyGroup = wizardSourceGroups.value.find((group) => groupFilterIds(group).length === 0)
      if (emptyGroup) {
        sourceFilterMap.value = { ...sourceFilterMap.value, [emptyGroup.key]: [id] }
      }
      addFilterOpen.value = false
      ElMessage.success({ message: t('protection.policiesPage.msgFilterAdded'), grouping: true })
    } finally {
      addPolicySaving.value = false
    }
    return
  }

  const name = addPolicyForm.value.name.trim()
  if (!name) {
    ElMessage.warning({ message: t('protection.policiesPage.msgPolicyNameRequired'), grouping: true })
    return
  }
  const scheduleError = validateScheduleForm(addPolicyForm.value)
  if (scheduleError) {
    ElMessage.warning({ message: scheduleError, grouping: true })
    return
  }
  if (addPolicyForm.value.sectionRetentionEnabled && addPolicyRetentionError.value) {
    ElMessage.warning({ message: addPolicyRetentionError.value, grouping: true })
    return
  }
  addPolicySaving.value = true
  try {
    const snapshot = JSON.parse(JSON.stringify({ ...addPolicyForm.value, name })) as BackupPolicyForm
    const created = await createBackupPolicy(policyFormToWritePayload(snapshot))
    const id = String(created.id)
    realPolicies.value = [mapPolicy(created), ...realPolicies.value.filter((item) => item.id !== id)]
    const emptyGroup = wizardSourceGroups.value.find((group) => !sourcePolicyMap.value[group.key])
    if (emptyGroup) {
      sourcePolicyMap.value = { ...sourcePolicyMap.value, [emptyGroup.key]: id }
    }
    addFilterOpen.value = false
    ElMessage.success({ message: t('protection.policiesPage.msgPolicyAdded'), grouping: true })
  } finally {
    addPolicySaving.value = false
  }
}

function applyBatchTarget() {
  const checkedGroups = checkedTargetGroups.value
  if (checkedGroups.length === 0) {
    ElMessage.warning({ message: t('protection.backupsPage.msgPickDirsForBatchTarget'), grouping: true })
    return
  }
  if (!batchTargetPicker.targetId || !getRealTarget(batchTargetPicker.targetId)) {
    ElMessage.warning({ message: t('protection.backupsPage.msgPickBatchTarget'), grouping: true })
    return
  }
  const target = getRealTarget(batchTargetPicker.targetId)
  if (targetHasDistinctEndpoints(target) && !batchRepositoryEndpointType.value) {
    ElMessage.warning({ message: t('addS3Repo.hintEndpointType'), grouping: true })
    return
  }
  if (checkedGroups.some((group) => targetIncompatibilityReasonForGroup(target, group) !== null)) {
    ElMessage.warning({ message: t('protection.backupsPage.msgPickBatchTarget'), grouping: true })
    return
  }
  const next = { ...sourceTargetMap.value }
  const nextEndpointTypes = { ...sourceEndpointTypeMap.value }
  checkedGroups.forEach((group) => {
    next[group.key] = batchTargetPicker.targetId
    nextEndpointTypes[group.key] = targetHasDistinctEndpoints(target)
      ? batchRepositoryEndpointType.value as RepositoryEndpointType
      : 'external'
    if (batchSelectedTargetIsNas.value) {
      nasTargetModes[group.key] = batchNasTargetMode.value
    }
  })
  sourceTargetMap.value = next
  sourceEndpointTypeMap.value = nextEndpointTypes
  targetAssignDialogOpen.value = false
  ElMessage.success({ message: t('protection.backupsPage.msgBatchTargetApplied', { n: checkedGroups.length }), grouping: true })
}

function applySingleTarget() {
  const groupKey = targetAssignActiveGroupKey.value
  if (!groupKey) return
  if (!batchTargetPicker.targetId || !getRealTarget(batchTargetPicker.targetId)) {
    ElMessage.warning({ message: t('protection.backupsPage.msgPickBatchTarget'), grouping: true })
    return
  }
  const group = wizardSourceGroups.value.find((item) => item.key === groupKey)
  const target = getRealTarget(batchTargetPicker.targetId)
  if (targetHasDistinctEndpoints(target) && !batchRepositoryEndpointType.value) {
    ElMessage.warning({ message: t('addS3Repo.hintEndpointType'), grouping: true })
    return
  }
  if (!group || targetIncompatibilityReasonForGroup(target, group) !== null) {
    ElMessage.warning({ message: t('protection.backupsPage.msgPickBatchTarget'), grouping: true })
    return
  }
  const next = { ...sourceTargetMap.value, [groupKey]: batchTargetPicker.targetId }
  sourceTargetMap.value = next
  sourceEndpointTypeMap.value = {
    ...sourceEndpointTypeMap.value,
    [groupKey]: targetHasDistinctEndpoints(target)
      ? batchRepositoryEndpointType.value as RepositoryEndpointType
      : 'external',
  }
  if (batchSelectedTargetIsNas.value) {
    nasTargetModes[groupKey] = batchNasTargetMode.value
  }
  targetAssignDialogOpen.value = false
}

function applyTargetAssignment() {
  if (targetAssignDialogMode.value === 'single') {
    applySingleTarget()
    return
  }
  applyBatchTarget()
}

function groupHasNasTarget(group: WizardSourceGroup) {
  return getRealTarget(sourceTargetMap.value[group.key])?.repoType === 'NAS'
}

function targetCapacityLabel(group: WizardSourceGroup) {
  const target = getRealTarget(sourceTargetMap.value[group.key])
  if (!target) return '—'
  const usage = t('protection.backupsPage.targetRepositoryUsageValue', {
    used: formatBytes(target.usedBytes ?? 0),
    limit: target.capacityBytes
      ? formatBytes(target.capacityBytes)
      : t('protection.backupsPage.targetNoConfiguredLimit'),
  })
  if (!target.storageTotalBytes) return usage
  return `${usage} · ${t('protection.backupsPage.targetBackingStorageValue', {
    available: formatBytes(target.storageAvailableBytes ?? 0),
    total: formatBytes(target.storageTotalBytes),
  })}`
}


function nasModeLabel(groupKey: string) {
  return nasTargetModes[groupKey] === 'single_repo'
    ? t('protection.backupsPage.nasModeSingleRepo')
    : t('protection.backupsPage.nasModePerDirectory')
}

function groupTargetSummary(group: WizardSourceGroup) {
  const target = getRealTarget(sourceTargetMap.value[group.key])
  if (!target) return sourceTargetMap.value[group.key] || t('protection.backupsPage.targetUnassigned')
  if (target.repoType !== 'NAS') return target.name
  const mode = nasTargetModes[group.key] ?? 'per_directory_repo'
  const suffix = mode === 'single_repo'
    ? t('protection.backupsPage.nasModeSummarySingle')
    : t('protection.backupsPage.nasModeSummaryPerDirectory')
  return `${target.name} · ${suffix}`
}

function groupTargetDetail(group: WizardSourceGroup) {
  const target = getRealTarget(sourceTargetMap.value[group.key])
  return {
    repoType: target?.repoType || '',
    location: target?.location || '',
    status: target?.status || '',
    capacity: targetCapacityLabel(group),
  }
}

function nasModeDesc(group: WizardSourceGroup) {
  const mode = nasTargetModes[group.key] ?? 'per_directory_repo'
  if (mode === 'single_repo') return t('protection.backupsPage.nasModeSingleRepoDesc')
  return t('protection.backupsPage.nasModePerDirectoryDesc', { host: group.sourceName })
}

function compressionSummary(compression: CompressionLevel) {
  return compressionOptions.value.find((option) => option.value === compression)?.title
    ?? ''
}

function compressionIcon(compression: CompressionLevel) {
  return compressionOptions.value.find((option) => option.value === compression)?.icon
    ?? Scale
}

function compressionDescription(compression: CompressionLevel) {
  return compressionOptions.value.find((option) => option.value === compression)?.description
    ?? ''
}

function compressionTooltip(compression: CompressionLevel) {
  return compressionOptions.value.find((option) => option.value === compression)?.tooltip
    ?? ''
}



function policyCompactSummary(group: WizardSourceGroup) {
  return policySelectedSummary(getRealPolicy(groupPolicyId(group)))
}


function backupDirPopoverSummary(entries: BackupDirEntry[]) {
  return t('protection.backupsPage.addedDirTotal', { n: entries.length })
}

function filterCompactSummary(group: WizardSourceGroup) {
  const ids = groupFilterIds(group)
  if (!ids.length) return t('protection.backupsPage.filterNoneOptional')
  const filter = getRealFilter(ids[0])
  if (ids.length === 1) return filterSelectedSummary(filter)
  return filterNames(ids)
}

function targetTone(target: WizardTarget | null | undefined) {
  if (!target) return 'unassigned'
  if (target.status === 'online') return 'online'
  if (target.status === 'warning') return 'warning'
  if (target.status === 'offline') return 'offline'
  return 'unknown'
}

const createConfirmRows = computed(() =>
  wizardSourceGroups.value.map((group, index) => {
    const recoveryGroup = createRecoveryPlanGroups.value.find((item) => item.key === group.key) ?? {
      ...group,
      plan: recoveryPlanForGroup(group),
    }
    const compression = sourceCompressionMap.value[group.key] || 'balanced'
    const policy = getRealPolicy(groupPolicyId(group))
    const filterIds = groupFilterIds(group)
    const filters = filterIds
      .map((id) => getRealFilter(id))
      .filter((filter): filter is WizardFilter => Boolean(filter))
    const targetDetail = groupTargetDetail(group)
    return {
      group,
      entries: group.entries,
      index,
      name: createBackupNameForGroup(group, index),
      target: groupTargetSummary(group),
      targetLocation: targetDetail.location,
      targetEndpoint: endpointSummaryForGroup(group),
      targetTone: targetDetail.status || 'unknown',
      targetCapacity: targetDetail.capacity,
      nasPlan: groupHasNasTarget(group) ? nasModeLabel(group.key) : '',
      nasPlanDesc: groupHasNasTarget(group) ? nasModeDesc(group) : '',
      policy: policy?.name ?? t('protection.backupsPage.policyNoneOptional'),
      policyObject: policy,
      policyDetailLines: policy
        ? [
            `${t('protection.backupsPage.descPolicyFrequency')}: ${policy.backupFrequencyDesc || policy.schedule || '—'}`,
            `${t('protection.backupsPage.descPolicyRetention')}: ${policyRetentionValue(policy) || '—'}`,
          ]
        : [],
      fileFilter: filterNames(filterIds),
      filterObjects: filters,
      fileFilterDetailLines: filterSummaryLines(filterIds),
      compression: compressionSummary(compression),
      recoveryGroup,
    }
  }),
)

const createSourceRows = computed(() =>
  createSourceIds.value.map((id) => {
    const selectedEntries = sourceSelectedEntries(id)
    const source = realSourceById.value.get(id)
    const type = source?.type ?? getSourceType(id)
    return {
      id,
      name: getSourceName(id),
      type,
      platform: source?.platform,
      typeLabel: type === 'nas'
        ? t('protection.backupsPage.sourceTypeNas')
        : t('protection.backupsPage.sourceTypeHost'),
      selectedEntries,
      selectedPreviewEntries: sourceDirPreviewEntries(selectedEntries),
      hiddenDirCount: Math.max(selectedEntries.length - 3, 0),
    }
  }),
)

const filteredCreateSourceRows = computed(() => {
  const keyword = appliedCreateSourceSearch.value.trim().toLowerCase()
  if (!keyword) return createSourceRows.value
  return createSourceRows.value.filter((row) =>
    row.name.toLowerCase().includes(keyword)
    || row.typeLabel.toLowerCase().includes(keyword)
    || row.type.toLowerCase().includes(keyword),
  )
})

const createWizardSteps = computed(() => [
  {
    title: t('protection.backupsPage.stepSource'),
    hint: t('protection.backupsPage.createWizardHintSource'),
    icon: backupFlowSourceStepIcon,
  },
  {
    title: t('protection.backupsPage.stepPolicy'),
    hint: t('protection.backupsPage.createWizardHintPolicy'),
    icon: Filter,
  },
  {
    title: t('protection.backupsPage.stepTarget'),
    hint: t('protection.backupsPage.createWizardHintTarget'),
    icon: Database,
  },
  {
    title: t('protection.backupsPage.stepRecoveryPlan'),
    hint: t('protection.backupsPage.createWizardHintRecoveryPlan'),
    icon: Route,
  },
  {
    title: t('protection.backupsPage.stepConfirm'),
    hint: t('protection.backupsPage.createWizardHintConfirm'),
    icon: ClipboardCheck,
  },
])

const createWizardStepItems = computed(() =>
  createWizardSteps.value.map((step, index) => ({
    step: index,
    label: step.title,
    icon: step.icon,
  })),
)

async function runCreateBackup() {
  for (let step = 0; step < createWizardSteps.value.length; step += 1) {
    if (!validateCreateStep(step)) {
      goCreateStep(step)
      resetCreateCompletedFrom(step)
      return
    }
    markCreateStepDone(step)
  }
  const payload = buildCreateBackupPayload()
  const groupMap = new Map(wizardSourceGroups.value.map((group) => [group.key, group]))

  createPhase.value = 'waiting'
  let allSuccess = true
  const configuredSourceIds: string[] = []

  for (const backup of payload.backups) {
    const group = groupMap.get(backup.key)
    if (!group) continue

    const sourceRefId = parseSourceRefId(backup.source.id)
    const repositoryId = parseInt(backup.targetId, 10) || 0
    const policyId = backup.policyId ? parseInt(backup.policyId, 10) || null : null
    const filterId = backup.globalFilterId ? parseInt(backup.globalFilterId, 10) || null : null

    const recoveryPlans: BackupConfigRecoveryPlanPayload[] = []
    const recoveryPlanEnabled = Boolean(backup.recoveryPlan?.enabled)
    if (recoveryPlanEnabled && backup.recoveryPlan?.dirPlans) {
      for (const dp of backup.recoveryPlan.dirPlans) {
        const target = targetPayloadFromSourceId(dp.targetHostId)
        if (isWholeSnapshotRecoveryPath(dp.sourcePath)) {
          recoveryPlans.push({
            scope: 'snapshot',
            source_path: '',
            backup_config_dir_id: null,
            target_type: target.target_type,
            target_ref_id: target.target_ref_id || null,
            restore_host_id: target.target_type === 'agent' ? target.target_ref_id || null : null,
            restore_dir: dp.restoreDir,
            conflict_mode: backup.recoveryPlan.conflictMode === 'overwrite' ? 'overwrite' : 'skip',
          })
          continue
        }
        for (const sourcePath of recoverySourcePathsForPayload(dp.sourcePath, group)) {
          recoveryPlans.push({
            scope: 'paths',
            source_path: sourcePath,
            target_type: target.target_type,
            target_ref_id: target.target_ref_id || null,
            restore_host_id: target.target_type === 'agent' ? target.target_ref_id || null : null,
            restore_dir: dp.restoreDir,
            conflict_mode: backup.recoveryPlan.conflictMode === 'overwrite' ? 'overwrite' : 'skip',
          })
        }
      }
    }

    const apiPayload: BackupConfigCreatePayload = {
      name: backup.name,
      remark: backup.remark || '',
      source_type: backup.source.type === 'nas' ? 'nas' : 'agent',
      source_ref_id: sourceRefId,
      repository_id: repositoryId || 1,
      repository_endpoint_type: backup.repositoryEndpointType,
      backup_policy_id: policyId,
      file_filter_rule_id: filterId,
      compression_level: (backup.compression as BackupConfigCreatePayload['compression_level']) || 'balanced',
      directories: (backup.source.pathItems || backup.source.paths.map((path: string) => ({ path, path_type: 'unknown' })))
        .map((item: { path: string; path_type?: BackupPathType }) => ({
          path: item.path,
          path_type: item.path_type || 'unknown',
        })),
      recovery_plan_enabled: recoveryPlanEnabled,
      recovery_plans: recoveryPlanEnabled ? recoveryPlans : undefined,
    }

    try {
      await createBackupConfig(apiPayload)
      configuredSourceIds.push(backup.source.id)
    } catch (e) {
      allSuccess = false
      logger.error('BackupCreateWizard.vue', 4285, 'Failed to create backup config', e)
      showBackupConfigCreateError(e, backup.name, repositoryId)
      break
    }
  }

  if (allSuccess) {
    if (configuredSourceIds.length > 0) {
      await setPipelineStep(configuredSourceIds, 3)
      step2Sources.value = step2Sources.value.filter((id) => !configuredSourceIds.includes(id))
    }
    ElMessage.success({ message: t('protection.backupsPage.createSuccess'), grouping: true })
    lastCreatedSourceIds.value = configuredSourceIds
    createPhase.value = 'done'
    nextTick(() => finishCreateAndGoToStep3(configuredSourceIds))
  } else {
    createPhase.value = 'form'
  }
}

function editableGroupPayloads() {
  const backups = buildCreateBackupPayload().backups
  const groupByKey = new Map(wizardSourceGroups.value.map((group) => [group.key, group]))
  const configById = new Map(editConfigs.value.map((config) => [Number(config.id), config]))
  return backups.flatMap((backup) => {
    const group = groupByKey.get(backup.key)
    const configId = Number(backup.backupConfigId)
    const config = Number.isFinite(configId) && configId > 0 ? configById.get(configId) : undefined
    return group && config ? [{ backup, group, config }] : []
  })
}

function directoryPayloadFromGroup(group: WizardSourceGroup) {
  return group.entries.map((entry) => ({
    path: entry.path,
    path_type: entry.pathType || 'unknown',
  }))
}

function backupConfigDirectoryIdForSourcePath(config: BackupConfigDetail, sourcePath: string) {
  const matches = config.directories
    .filter((directory) => isSameOrAncestorPath(directory.path, sourcePath))
    .sort((a, b) => b.path.length - a.path.length)
  return matches[0]?.id || 0
}

async function syncEditRecoveryPlans(config: BackupConfigDetail, backup: ReturnType<typeof buildCreateBackupPayload>['backups'][number], group: WizardSourceGroup) {
  const existingPlans = new Map((config.recovery_plans || []).map((plan) => [plan.id, plan]))
  const seenPlanIds = new Set<number>()
  const recoveryPlanEnabled = Boolean(backup.recoveryPlan?.enabled)
  if (!recoveryPlanEnabled) {
    await updateBackupConfig(config.id, { recovery_plan_enabled: false })
    await Promise.all([...existingPlans.keys()].map((id) => deleteRecoveryPlan(id)))
    return
  }
  const dirPlans = backup.recoveryPlan?.dirPlans || []
  for (let index = 0; index < dirPlans.length; index += 1) {
    const dirPlan = dirPlans[index]
    const target = targetPayloadFromSourceId(dirPlan.targetHostId)
    if (isWholeSnapshotRecoveryPath(dirPlan.sourcePath)) {
      const payload: RecoveryPlanCreatePayload = {
        backup_config_id: config.id,
        scope: 'snapshot',
        backup_config_dir_id: null,
        source_type: config.source_type === 'nas' ? 'nas' : 'agent',
        source_ref_id: Number(config.source_ref_id),
        source_path: '',
        target_type: target.target_type,
        target_ref_id: target.target_ref_id,
        restore_dir: dirPlan.restoreDir,
        conflict_mode: backup.recoveryPlan?.conflictMode === 'overwrite' ? 'overwrite' : 'skip',
        sort_order: index,
      }
      const existingId = dirPlan.id.startsWith('restore-plan-')
        ? Number(dirPlan.id.replace('restore-plan-', ''))
        : 0
      if (existingId && existingPlans.has(existingId)) {
        seenPlanIds.add(existingId)
        await updateRecoveryPlan(existingId, payload)
      } else {
        const created = await createRecoveryPlan(payload)
        seenPlanIds.add(created.id)
      }
      continue
    }
    for (const sourcePath of recoverySourcePathsForPayload(dirPlan.sourcePath, group)) {
      const backupConfigDirId = backupConfigDirectoryIdForSourcePath(config, sourcePath)
      if (!backupConfigDirId) {
        throw new Error(t('protection.backupsPage.msgRecoverySourceOutsideDirs'))
      }
      const payload: RecoveryPlanCreatePayload = {
        backup_config_id: config.id,
        scope: 'paths',
        backup_config_dir_id: backupConfigDirId,
        source_type: config.source_type === 'nas' ? 'nas' : 'agent',
        source_ref_id: Number(config.source_ref_id),
        source_path: sourcePath,
        target_type: target.target_type,
        target_ref_id: target.target_ref_id,
        restore_dir: dirPlan.restoreDir,
        conflict_mode: backup.recoveryPlan?.conflictMode === 'overwrite' ? 'overwrite' : 'skip',
        sort_order: index,
      }
      const existingId = dirPlan.id.startsWith('restore-plan-')
        ? Number(dirPlan.id.replace('restore-plan-', ''))
        : 0
      if (existingId && existingPlans.has(existingId)) {
        seenPlanIds.add(existingId)
        await updateRecoveryPlan(existingId, payload)
      } else {
        const created = await createRecoveryPlan(payload)
        seenPlanIds.add(created.id)
      }
    }
  }
  await Promise.all(
    [...existingPlans.keys()]
      .filter((id) => !seenPlanIds.has(id))
      .map((id) => deleteRecoveryPlan(id)),
  )
  await updateBackupConfig(config.id, { recovery_plan_enabled: true })
}

async function runEditBackupConfig() {
  const section = activeEditSection.value
  const editables = editableGroupPayloads()
  if (!editables.length) {
    createPhase.value = 'form'
    ElMessage.error({ message: t('protection.backupsPage.msgEditConfigUnavailable'), grouping: true })
    return
  }
  const step = editStepForSection(section)
  if (createStep.value !== step) createStep.value = step
  if (!validateCreateStep(step)) return
  createPhase.value = 'waiting'
  try {
    for (const { backup, group, config } of editables) {
      if (section === 'paths') {
        await updateBackupConfig(config.id, {
          directories: directoryPayloadFromGroup(group),
        })
      } else if (section === 'policy') {
        await updateBackupConfig(config.id, {
          backup_policy_id: backup.policyId ? parseInt(backup.policyId, 10) || null : null,
          file_filter_rule_id: backup.globalFilterId ? parseInt(backup.globalFilterId, 10) || null : null,
          compression_level: (backup.compression as BackupConfigCreatePayload['compression_level']) || 'balanced',
        })
      } else {
        await syncEditRecoveryPlans(config, backup, group)
      }
    }
    const editedSourceIds = normalizeSourceIdList(editables.map(({ config }) =>
      `${config.source_type}:${config.source_ref_id}`,
    ))
    ElMessage.success({ message: t('protection.backupsPage.msgSaveEditDemo'), grouping: true })
    if (props.embedded) {
      emit('completed', editedSourceIds)
      return
    }
    closeCreate()
  } catch (err) {
    createPhase.value = 'form'
    ElMessage.error({ message: apiErrorMessage(err, editorFailureText.value), grouping: true })
  }
}

function submitCreateWizard() {
  if (isEditMode.value) {
    void runEditBackupConfig()
    return
  }
  void runCreateBackup()
}

watch(createOpen, (v) => {
  if (!v) {
    cancelTargetValidation()
    targetConnectionResults.value = {}
    createStep.value = 0
    createPhase.value = 'form'
    createCompletedSteps.value = new Set()
    editConfigs.value = []
    editSection.value = null
    closeValidationPopover()
  }
})

watch(wizardDirEntries, () => {
  resetCreateCompletedFrom(0)
  if (validationPopoverKind.value === 'source-dirs') syncOpenValidationPopover()
}, { deep: true })

watch([sourcePolicyMap, sourceFilterMap, sourceCompressionMap, selPolicy, selGlobalFilter], () => {
  resetCreateCompletedFrom(1)
}, { deep: true })

watch([sourceTargetMap, sourceEndpointTypeMap, nasTargetModes], () => {
  resetCreateCompletedFrom(2)
  clearTargetConnectionResults()
  if (validationPopoverKind.value === 'target') syncOpenValidationPopover()
}, { deep: true })

watch(createRecoveryPlanMap, () => {
  resetCreateCompletedFrom(3)
  if (validationPopoverKind.value === 'recovery-plan') syncOpenValidationPopover()
}, { deep: true })

function basenamePath(path: string) {
  const parts = path.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] || path
}

const treeSortLocale = () => String(locale.value || 'en')

function isSameOrAncestorPath(ancestorPath: string, childPath: string) {
  const normalize = (value: string) => normalizeComparableSourcePath(value)
  const ancestor = normalize(ancestorPath)
  const child = normalize(childPath)
  if (ancestor === child) return true
  if (/^[A-Za-z]:/.test(ancestor) || /^[A-Za-z]:/.test(child) || ancestor.includes('\\') || child.includes('\\')) {
    const a = ancestor.toLowerCase()
    const c = child.toLowerCase()
    const prefix = a.endsWith('\\') ? a : `${a}\\`
    return c.startsWith(prefix)
  }
  const prefix = ancestor.endsWith('/') ? ancestor : `${ancestor}/`
  return child.startsWith(prefix)
}

function normalizeComparableSourcePath(path: string) {
  const trimmed = String(path || '').trim()
  if (/^[A-Za-z]:[\\/]?$/.test(trimmed)) return `${trimmed[0].toUpperCase()}:\\`
  const stripped = trimmed.replace(/[\\/]+$/, '')
  return stripped || trimmed
}

function shallowestPaths(paths: string[]) {
  const sorted = normalizeSourceIdList(paths)
    .map(normalizeComparableSourcePath)
    .sort((a, b) => a.length - b.length)
  const picked: string[] = []
  for (const path of sorted) {
    if (picked.some((parent) => isSameOrAncestorPath(parent, path))) continue
    picked.push(path)
  }
  return picked
}

function preserveShallowestPathOrder(paths: string[]) {
  const picked: string[] = []
  for (const path of normalizeSourceIdList(paths).map(normalizeComparableSourcePath)) {
    if (picked.some((parent) => isSameOrAncestorPath(parent, path))) continue
    for (let index = picked.length - 1; index >= 0; index -= 1) {
      if (isSameOrAncestorPath(path, picked[index])) picked.splice(index, 1)
    }
    picked.push(path)
  }
  return picked
}

</script>

<template>
  <component
    :is="backupCreateWizardShell"
    v-bind="backupCreateWizardShellProps"
  >
    <BackupConfigCreateWizard
      v-if="createOpen"
      :phase="createPhase"
      :steps="createWizardStepItems"
      :current-step="createStep"
      :is-step-done="isCreateStepDone"
      :is-step-locked="isCreateStepLocked"
      :is-last-step="hasEditRequest || createStep >= 4"
      :can-go-prev="!hasEditRequest && createStep > 0"
      :show-steps="!hasEditRequest"
      :title="editorTitle"
      :description="editorDescription"
      :aria-label="t('protection.backupsPage.createWizardAria')"
      :waiting-text="editorWaitingText"
      :bootstrapping="createBootstrapping"
      :busy="targetValidationInProgress"
      :animated="!embedded"
      :result-title="t('protection.backupsPage.resultCreateTitle')"
      :result-subtitle="t('protection.backupsPage.resultCreateSub')"
      :cancel-label="t('protection.backupsPage.btnCancel')"
      :prev-label="t('protection.backupsPage.btnPrev')"
      :next-label="t('protection.backupsPage.btnNext')"
      :confirm-label="hasEditRequest ? t('protection.backupsPage.btnSave') : t('protection.backupsPage.btnConfirmCreate')"
      :close-label="t('protection.backupsPage.btnClose')"
      :go-to-start-backup-label="t('protection.backupsPage.btnGoToStartBackup')"
      @close="closeCreate"
      @prev="prevCreate"
      @next="nextCreate"
      @confirm="submitCreateWizard"
      @go-to-start-backup="goToStartBackupFromCreate"
      @visible="emit('ready')"
    >
      <template #form>
        <div
          v-if="validationPopoverVisible"
          class="create-validation-popover"
          role="dialog"
          aria-live="polite"
        >
          <button
            type="button"
            class="create-validation-popover__close"
            :aria-label="t('protection.backupsPage.ariaRemove')"
            @click="closeValidationPopover"
          >
            <X :size="15" />
          </button>
          <div class="create-validation-popover__title">{{ validationPopoverTitle }}</div>
          <ul class="create-validation-popover__list">
            <li v-for="item in validationPopoverItems" :key="item">{{ item }}</li>
          </ul>
        </div>

        <div v-if="createStep === 0" class="dp-wizard-pane">
          <p class="text-sm text-slate-500 mb-3">
            {{ t('protection.backupsPage.createStep0Lead') }}
          </p>

          <div class="create-source-config-toolbar hfl-list-toolbar">
            <div class="hfl-list-toolbar__right">
              <ElInput
                v-model="createSourceSearch"
                clearable
                @clear="clearCreateSourceSearch"
                size="small"
                :placeholder="t('protection.backupsPage.phSearchCreateSources')"
                class="create-source-config-search hfl-list-search"
              >
                <template #prefix>
                  <Search :size="16" class="hfl-list-search__icon" />
                </template>
              </ElInput>
            </div>
          </div>

          <el-table
          v-table-overflow-title
            ref="createSourceTableRef"
            :data="filteredCreateSourceRows"
            row-key="id"
            stripe
            :expand-row-keys="createExpandedSourceIds"
            :header-cell-style="TABLE_HEADER_STYLE"
            :row-class-name="createSourceRowClassName"
            class="hfl-list-table create-source-config-table"
            @expand-change="onCreateSourceExpandChange"
          >
            <el-table-column type="expand" width="44" fixed="left" class-name="create-source-config-expand-column">
              <template #default="{ row }">
                <div class="create-source-config-detail">
                  <section class="create-source-config-detail__tree">
                    <div class="create-source-config-detail__head create-source-config-detail__head--compact">
                      <div class="create-source-config-detail__title-row">
                        <div class="text-sm font-semibold text-slate-900">{{ t('protection.backupsPage.labelEnterPath') }}</div>
                      </div>
                    </div>
                    <div class="create-manual-path">
                      <ElInput
                        v-model="manualSourcePathBySource[row.id]"
                        clearable
                        size="small"
                        :placeholder="manualSourcePathPlaceholder(row.id)"
                        class="create-manual-path__input"
                        :disabled="isManualSourcePathValidating(row.id)"
                        :class="{ 'create-manual-path__input--error': Boolean(manualSourcePathErrorBySource[row.id]) }"
                        @input="clearManualSourcePathError(row.id)"
                        @keyup.enter="addManualSourcePath(row.id)"
                      />
                      <ElButton
                        size="small"
                        type="primary"
                        class="create-manual-path__button"
                        :loading="isManualSourcePathValidating(row.id)"
                        @click="addManualSourcePath(row.id)"
                      >
                        {{ t('protection.backupsPage.btnAdd') }}
                      </ElButton>
                      <p v-if="manualSourcePathErrorBySource[row.id]" class="create-manual-path__error">
                        {{ manualSourcePathErrorBySource[row.id] }}
                      </p>
                    </div>
                    <div class="create-source-config-detail__head create-source-config-detail__head--browse">
                      <div class="create-source-config-detail__title-row">
                        <div class="text-sm font-semibold text-slate-900">{{ t('protection.backupsPage.labelBrowsePaths') }}</div>
                      </div>
                      <ElButton
                        size="small"
                        type="primary"
                        plain
                        :disabled="!hasCreateSourceAddableSelection(row.id)"
                        @click="addPickedSourceFor(row.id)"
                      >
                        {{ t('protection.backupsPage.btnAddSelectedPaths') }}
                      </ElButton>
                    </div>
                    <p v-if="noSourceTreeRootsBySource[row.id]" class="dp-create-tree-shell__warn">
                      {{ t('protection.backupsPage.dirTreeEmptyHost') }}
                    </p>
                    <div v-if="createSourceTreeErrorBySource[row.id]" class="dp-create-tree-shell__error">
                      <span>{{ createSourceTreeErrorBySource[row.id] }}</span>
                      <ElButton
                        size="small"
                        text
                        type="primary"
                        class="dp-create-tree-shell__retry"
                        :disabled="isDirectoryLoading(createSourceDirectoryLoadingKey(row.id))"
                        @click="reloadCreateSourceTree(row.id)"
                      >
                        <RefreshCw
                          :size="14"
                          :class="{ 'is-spinning': isDirectoryLoading(createSourceDirectoryLoadingKey(row.id)) }"
                        />
                        {{ t('protection.backupsPage.btnReload') }}
                      </ElButton>
                    </div>
                    <el-tree
                      :ref="(el) => setCreateSourceTreeRef(row.id, el)"
                      :key="`src-tree-${row.id}-${treeRemountKey}-${createSourceTreeRemountKeyBySource[row.id] ?? 0}`"
                      v-loading="isDirectoryLoading(createSourceDirectoryLoadingKey(row.id))"
                      class="source-dir-tree hfl-dir-tree hfl-dir-tree--fill"
                      :props="dirTreeProps"
                      lazy
                      :load="(node, resolve) => loadSourceTreeNode(row.id, node, resolve)"
                      node-key="path"
                      show-checkbox
                      :check-strictly="true"
                      :check-on-click-node="true"
                      :expand-on-click-node="false"
                      :default-checked-keys="createSourceTreeCheckedKeys(row.id)"
                      @click.capture="(event) => onSourceTreeClickCapture(row.id, event)"
                      @check-change="(data, checked) => onSourceDirCheckChange(row.id, data, checked)"
                      @node-click="(data) => onSourceTreeNodeClick(row.id, data)"
                      @node-collapse="(data) => onSourceDirectoryExpansionChange(row.id, data)"
                      @node-expand="(data) => onSourceDirectoryExpansionChange(row.id, data)"
                    >
                      <template #default="{ data }">
                        <div v-if="data.nodeKind === 'loadMore'" class="create-dir-row create-dir-row--load-more">
                          <ElButton
                            size="small"
                            text
                            type="primary"
                            :loading="data.loading"
                            @click.stop="loadMoreSourceTreeChildren(data)"
                          >
                            {{ data.label }}
                          </ElButton>
                          <span class="create-dir-row__path">{{ t('protection.backupsPage.dirTreeHasMoreHint') }}</span>
                        </div>
                        <ElTooltip
                          v-else
                          :disabled="!data.filenameEncodingSuspected"
                          effect="light"
                          placement="top-start"
                          popper-class="smb-filename-encoding-tooltip"
                          :show-after="250"
                        >
                          <template #content>
                            <div class="smb-filename-encoding-tooltip__content">
                              <div class="smb-filename-encoding-tooltip__title">
                                {{ t('protection.backupsPage.dirTreeFilenameEncodingWarningTitle') }}
                              </div>
                              <div class="smb-filename-encoding-tooltip__section">
                                <span class="smb-filename-encoding-tooltip__label">
                                  {{ t('protection.backupsPage.dirTreeFilenameEncodingWarningReasonLabel') }}:
                                </span>
                                <span>{{ t('protection.backupsPage.dirTreeFilenameEncodingWarningReason') }}</span>
                              </div>
                              <div class="smb-filename-encoding-tooltip__section">
                                <span class="smb-filename-encoding-tooltip__label">
                                  {{ t('protection.backupsPage.dirTreeFilenameEncodingWarningSolutionLabel') }}:
                                </span>
                                <span>{{ t('protection.backupsPage.dirTreeFilenameEncodingWarningSolution') }}</span>
                              </div>
                            </div>
                          </template>
                          <div
                            class="create-dir-row create-dir-row--tree"
                            :class="{
                              'create-dir-row--disabled': Boolean(addedDirTreeDisableReason(row.id, data.path)),
                              'create-dir-row--filename-encoding-warning': Boolean(data.filenameEncodingSuspected),
                            }"
                            :data-source-path="data.path"
                            @click="(event) => onSourceTreeRowClick(row.id, data, event)"
                          >
                            <component
                              :is="data.path_type === 'file' ? FileIcon : Folder"
                              :size="15"
                              class="create-dir-row__icon"
                              :class="data.path_type === 'file' ? 'create-dir-row__icon--file' : 'create-dir-row__icon--folder'"
                            />
                            <div class="create-dir-row__body">
                              <span class="create-dir-row__label">{{ data.label }}</span>
                              <span class="create-dir-row__path">{{ data.path }}</span>
                            </div>
                            <ShieldAlert
                              v-if="data.filenameEncodingSuspected"
                              :size="15"
                              class="create-dir-row__filename-encoding-icon"
                              aria-hidden="true"
                            />
                            <button
                              v-if="data.path_type === 'directory'"
                              type="button"
                              class="hfl-dir-tree-node__refresh"
                              :class="{ 'is-refreshing': isSourceDirectoryRefreshing(row.id, data.path) }"
                              :disabled="isSourceDirectoryRefreshing(row.id, data.path)"
                              :aria-label="t('protection.backupsPage.ariaRefreshDirectory', { path: data.path })"
                              :title="t('protection.backupsPage.ariaRefreshDirectory', { path: data.path })"
                              @click.stop="refreshCreateSourceDirectory(row.id, data)"
                            >
                              <RefreshCw
                                :size="14"
                                :class="{ 'is-spinning': isSourceDirectoryRefreshing(row.id, data.path) }"
                              />
                            </button>
                            <HflHelpTip
                              v-if="addedDirTreeDisableReason(row.id, data.path)"
                              :content="addedDirTreeDisableReason(row.id, data.path)"
                              trigger-class="create-dir-row__disabled-icon"
                              :aria-label="addedDirTreeDisableReason(row.id, data.path) || undefined"
                            />
                          </div>
                        </ElTooltip>
                      </template>
                    </el-tree>
                  </section>

                  <section class="create-source-config-detail__selected">
                    <div class="create-source-config-detail__head create-source-config-detail__head--compact">
                      <div class="create-source-config-detail__title-row">
                        <div class="text-sm font-semibold text-slate-900">{{ t('protection.backupsPage.labelAddedPaths') }}</div>
                        <div class="text-xs text-slate-500">{{ t('protection.backupsPage.addedCount', { n: sourceSelectedCount(row.id) }) }}</div>
                      </div>
                    </div>
                    <div
                      v-if="!sourceSelectedEntries(row.id).length"
                      class="dp-added-dir-empty text-xs py-8 text-center border border-dashed rounded-md"
                      :class="{ 'dp-added-dir-empty--error': Boolean(createSourceDirIssue(row.id)) }"
                    >
                      <template v-if="createSourceDirIssue(row.id)">
                        <ShieldAlert :size="18" class="dp-added-dir-empty__icon" />
                        <div class="dp-added-dir-empty__title">
                          {{ t('protection.backupsPage.validationMissingSourceDirsShort') }}
                        </div>
                        <div class="dp-added-dir-empty__hint">
                          {{ t('protection.backupsPage.validationMissingSourceDirsHint') }}
                        </div>
                      </template>
                      <template v-else>
                        {{ t('protection.backupsPage.addedEmpty') }}
                      </template>
                    </div>
                    <ul v-else class="dp-added-dir-list list-none m-0 p-0">
                      <li
                        v-for="entry in sourceSelectedEntries(row.id)"
                        :key="entry.key"
                        class="create-dir-row create-dir-row--added"
                      >
                        <component
                          :is="entry.pathType === 'file' ? FileIcon : Folder"
                          :size="15"
                          class="create-dir-row__icon"
                          :class="entry.pathType === 'file' ? 'create-dir-row__icon--file' : 'create-dir-row__icon--folder'"
                        />
                        <div class="create-dir-row__body">
                          <span class="create-dir-row__path">{{ entry.path }}</span>
                        </div>
                        <ElButton text type="danger" class="create-dir-row__action" :aria-label="t('protection.backupsPage.ariaRemove')" @click="removeWizardDirEntry(entry.key)">
                          <X :size="16" />
                        </ElButton>
                      </li>
                    </ul>
                  </section>
                </div>
              </template>
            </el-table-column>

            <el-table-column :label="t('protection.backupsPage.colBackupSource')" min-width="200" fixed="left">
              <template #default="{ row }">
                <button
                  type="button"
                  class="backup-source-cell backup-source-cell--summary backup-source-cell--clickable create-source-cell-trigger"
                  :aria-label="row.name"
                  @click.stop="openCreateSourceDetail(row)"
                >
                  <div class="backup-source-cell__body">
                    <span class="create-backup-source-name" :title="row.name">{{ row.name }}</span>
                    <span class="create-backup-source-meta-row">
                      <el-tag
                        size="small"
                        effect="plain"
                        class="create-backup-source-type-tag"
                      >
                        {{ backupSourceTypeLabel(row.type) }}
                      </el-tag>
                      <span v-if="row.platform" class="create-backup-source-platform">
                        <AgentPlatformBrandIcon :os="row.platform" />
                      </span>
                    </span>
                  </div>
                </button>
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.sourceResources.colConnectivity')" min-width="110">
              <template #default="{ row }">
                <el-tag size="small" :type="sourceAvailabilityTagType(row.id)" effect="plain">
                  {{ sourceAvailabilityLabel(row.id) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.backupsPage.labelSourceHostProxy')" min-width="180">
              <template #default="{ row }">
                <HflPopover trigger="hover" placement="top-start" :width="320">
                  <template #reference>
                    <div class="source-endpoint-cell table-stack-cell">
                      <span class="table-stack-cell__primary">{{ sourceEndpointInfo(row.id).primary }}</span>
                      <span class="table-stack-cell__secondary">{{ sourceEndpointInfo(row.id).secondary }}</span>
                    </div>
                  </template>
                  <div class="create-table-info-popover">
                    <div>{{ sourceEndpointInfo(row.id).primary }}</div>
                    <div>{{ sourceEndpointInfo(row.id).secondary }}</div>
                  </div>
                </HflPopover>
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.backupsPage.labelAddedPaths')" min-width="300">
              <template #default="{ row }">
                <div
                  v-if="!row.selectedEntries.length"
                  class="create-source-dir-preview-empty"
                  :class="{ 'create-source-dir-preview-empty--error': Boolean(createSourceDirIssue(row.id)) }"
                >
                  <div>{{ t('protection.backupsPage.addedEmptyCompact') }}</div>
                  <div v-if="createSourceDirIssue(row.id)" class="create-source-dir-preview-empty__reason">
                    {{ createSourceDirIssue(row.id) }}
                  </div>
                </div>
                <HflPopover v-else trigger="hover" placement="top-start" :width="520">
                  <template #reference>
                    <div class="create-source-dir-preview">
                      <div class="create-source-dir-preview__count">
                        {{ t('protection.backupsPage.addedDirTotal', { n: row.selectedEntries.length }) }}
                      </div>
                      <div
                        v-for="entry in row.selectedPreviewEntries"
                        :key="entry.key"
                        class="create-source-dir-preview__item"
                      >
                        <component
                          :is="entry.pathType === 'file' ? FileIcon : Folder"
                          :size="14"
                          class="create-source-dir-preview__icon"
                          :class="entry.pathType === 'file' ? 'create-source-dir-preview__icon--file' : 'create-source-dir-preview__icon--folder'"
                        />
                        <span class="create-source-dir-preview__path hfl-table-cell-mono">{{ entry.path }}</span>
                      </div>
                      <div v-if="row.hiddenDirCount > 0" class="create-source-dir-preview__more">
                        {{ t('protection.backupsPage.moreDirs', { n: row.hiddenDirCount }) }}
                      </div>
                    </div>
                  </template>
                  <div class="create-table-info-popover create-table-info-popover--dirs create-table-info-popover--selected-paths">
                    <div class="create-table-info-popover__summary">
                      {{ t('protection.backupsPage.addedDirTotal', { n: row.selectedEntries.length }) }}
                    </div>
                    <div
                      v-for="entry in row.selectedEntries"
                      :key="entry.key"
                      class="create-table-info-popover__dir-row create-dir-row create-dir-row--added"
                    >
                      <component
                        :is="entry.pathType === 'file' ? FileIcon : Folder"
                        :size="15"
                        class="create-dir-row__icon"
                        :class="entry.pathType === 'file' ? 'create-dir-row__icon--file' : 'create-dir-row__icon--folder'"
                      />
                      <div class="create-dir-row__body">
                        <span class="create-dir-row__path hfl-table-cell-mono">{{ entry.path }}</span>
                      </div>
                    </div>
                  </div>
                </HflPopover>
              </template>
            </el-table-column>
            <el-table-column :label="t('protection.backupsPage.colAction')" width="112" fixed="right">
              <template #default="{ row }">
                <ElButton
                  text
                  type="primary"
                  class="create-source-expand-btn"
                  :title="isCreateSourceRowExpanded(row.id) ? t('protection.backupsPage.btnCollapseDirs') : t('protection.backupsPage.btnSelectDirs')"
                  @click="toggleCreateSourceRow(row)"
                >
                  <ChevronDown
                    :size="16"
                    class="create-source-expand-btn__icon"
                    :class="{ 'create-source-expand-btn__icon--open': isCreateSourceRowExpanded(row.id) }"
                  />
                  <span>
                    {{ isCreateSourceRowExpanded(row.id) ? t('protection.backupsPage.btnCollapseDirs') : t('protection.backupsPage.btnSelect') }}
                  </span>
                </ElButton>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div v-if="createStep === 1" class="policy-step dp-wizard-pane space-y-6">
          <div>
            <div>
              <div class="create-source-config-toolbar create-source-config-toolbar--actions hfl-list-toolbar">
                <div class="create-source-config-selection">
                  {{ t('protection.backupsPage.selectedSourceCount', { selected: checkedFilterPolicyGroups.length, total: wizardSourceGroups.length }) }}
                </div>
                <div class="create-source-config-toolbar__divider"></div>
                <ElButton type="primary" class="hfl-btn-with-icon dp-create-action-btn" @click="goToBackupPolicyPage">
                  <Plus :size="16" stroke-width="2" class="shrink-0" />
                  <span>{{ t('protection.backupsPage.btnAddBackupPolicy') }}</span>
                </ElButton>
                <ElButton type="primary" class="hfl-btn-with-icon dp-create-action-btn" @click="goToFilterRulePage">
                  <Plus :size="16" stroke-width="2" class="shrink-0" />
                  <span>{{ t('protection.backupsPage.btnAddFileFilterRule') }}</span>
                </ElButton>
                <ElDropdown trigger="click" @command="openFilterPolicyBatchDialog">
                  <ElButton class="hfl-btn-with-icon">
                    <span>{{ t('protection.backupsPage.btnBatchActions') }}</span>
                    <ChevronDown :size="15" stroke-width="2" class="shrink-0" />
                  </ElButton>
                  <template #dropdown>
                    <ElDropdownMenu>
                      <ElDropdownItem command="policy" :disabled="checkedFilterPolicyGroups.length === 0">
                        {{ t('protection.backupsPage.batchPolicyAction') }}
                      </ElDropdownItem>
                      <ElDropdownItem command="filter" :disabled="checkedFilterPolicyGroups.length === 0">
                        {{ t('protection.backupsPage.batchFileFilterAction') }}
                      </ElDropdownItem>
                      <ElDropdownItem command="compression" :disabled="checkedFilterPolicyGroups.length === 0">
                        {{ t('protection.backupsPage.batchCompressionAction') }}
                      </ElDropdownItem>
                    </ElDropdownMenu>
                  </template>
                </ElDropdown>
                <div class="hfl-list-toolbar__right">
                  <ElInput
                    v-model="filterPolicySourceSearch"
                    clearable
                    @clear="clearFilterPolicySourceSearch"
                    size="small"
                    :placeholder="t('protection.backupsPage.phSearchCreateSources')"
                    class="create-source-config-search hfl-list-search"
                  >
                    <template #prefix>
                      <Search :size="16" class="hfl-list-search__icon" />
                    </template>
                  </ElInput>
                </div>
              </div>

              <el-dialog
                v-model="filterPolicyBatchDialogOpen"
                :title="filterPolicyBatchDialogTitle"
                width="560px"
                destroy-on-close
                class="filter-policy-batch-dialog dp-dialog-soft-bg"
              >
                <div class="filter-policy-batch-dialog__body">
                  <el-select
                    v-if="activeFilterPolicyBatchOperation === 'policy'"
                    v-model="batchPolicyId"
                    filterable
                    clearable
                    :placeholder="t('protection.backupsPage.phPolicyForDir')"
                    class="w-full"
                    placement="bottom-start"
                    popper-class="create-policy-select-popper"
                    @visible-change="handleOptionSelectVisibleChange"
                  >
                    <el-option
                      v-for="pol in realPolicies"
                      :key="pol.id"
                      :label="pol.name"
                      :value="pol.id"
                    >
                      <HflPopover ref="optionPopoverRefs" trigger="hover" placement="bottom-start" :fallback-placements="['top-start', 'right-start']" :width="420" :persistent="false" popper-class="create-policy-option-popper">
                        <template #reference>
                          <div class="create-policy-option" @pointerdown.capture="hideOptionPopovers">
                            <div class="create-policy-option__head">
                              <span class="create-policy-option__name">{{ pol.name }}</span>
                              <el-tag v-bind="policyStateTagAttrs(pol.isActive)" size="small">
                                {{ policyStateLabel(pol.isActive) }}
                              </el-tag>
                            </div>
                            <div class="create-policy-option__summary">{{ policyOptionSummary(pol) }}</div>
                          </div>
                        </template>
                        <div class="create-policy-detail-popover">
                          <div class="create-policy-detail-popover__head">
                            <div class="create-policy-detail-popover__title">{{ pol.name }}</div>
                            <el-tag v-bind="policyStateTagAttrs(pol.isActive)" size="small">
                              {{ policyStateLabel(pol.isActive) }}
                            </el-tag>
                          </div>
                          <div class="create-policy-detail-popover__sections">
                            <section
                              v-for="row in policyDetailRows(pol)"
                              :key="row.label"
                              class="create-policy-detail-popover__section create-policy-detail-popover__section--line"
                            >
                              <span class="create-policy-detail-popover__section-title">{{ row.label }}:</span>
                              <el-tag
                                v-if="row.state !== undefined"
                                size="small"
                                v-bind="policyStateTagAttrs(Boolean(row.state))"
                              >
                                {{ row.value }}
                              </el-tag>
                              <span
                                v-else
                                class="create-policy-detail-popover__value"
                                :class="{ 'create-policy-detail-popover__value--mono': row.mono }"
                              >{{ row.value }}</span>
                            </section>
                            <section class="create-policy-detail-popover__section">
                              <div class="create-policy-detail-popover__section-title">
                                {{ t('protection.policiesPage.fieldRetention') }}:
                              </div>
                              <div class="create-policy-detail-popover__retention-box">
                                <div class="policy-retention-detail-list">
                                  <div
                                    v-for="line in policyRetentionDetailLines(pol)"
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
                      </HflPopover>
                    </el-option>
                  </el-select>
                  <el-select
                    v-else-if="activeFilterPolicyBatchOperation === 'filter'"
                    ref="batchFilterSelectRef"
                    v-model="batchFilterIds"
                    multiple
                    filterable
                    clearable
                    collapse-tags
                    collapse-tags-tooltip
                    :max-collapse-tags="1"
                    :placeholder="t('protection.backupsPage.phBatchFilters')"
                    class="w-full file-filter-multi-select"
                    placement="bottom-start"
                    popper-class="create-policy-select-popper"
                    @change="closeBatchFilterSelect"
                    @visible-change="handleOptionSelectVisibleChange"
                  >
                    <el-option
                      v-for="gf in realFilters"
                      :key="gf.id"
                      :label="gf.name"
                      :value="gf.id"
                    >
                      <HflPopover ref="optionPopoverRefs" trigger="hover" placement="bottom-start" :fallback-placements="['top-start', 'right-start']" :width="460" :persistent="false" popper-class="create-policy-option-popper">
                        <template #reference>
                          <div class="create-policy-option" @pointerdown.capture="hideOptionPopovers">
                            <div class="create-policy-option__head">
                              <span class="create-policy-option__name">{{ gf.name }}</span>
                              <el-tag v-bind="policyStateTagAttrs(gf.isActive)" size="small">
                                {{ policyStateLabel(gf.isActive) }}
                              </el-tag>
                            </div>
                            <div class="create-filter-rules-option">
                              <span class="create-filter-rules-option__prefix">{{ t('protection.policiesPage.filterCustomTypeExclude') }}:</span>
                              <div v-if="filterDisplayedRuleLines(gf).length" class="create-filter-rules-option__list">
                                <code
                                  v-for="(line, index) in filterDisplayedRuleLines(gf)"
                                  :key="`${index}-${line}`"
                                  class="create-filter-rules-option__rule"
                                >
                                  {{ line }}
                                </code>
                              </div>
                              <span v-else class="create-filter-rules-option__empty">
                                {{ t('protection.policiesPage.filterNoActiveRules') }}
                              </span>
                            </div>
                          </div>
                        </template>
                        <div class="create-policy-detail-popover">
                          <div class="create-policy-detail-popover__head">
                            <div class="create-policy-detail-popover__title">{{ gf.name }}</div>
                            <el-tag v-bind="policyStateTagAttrs(gf.isActive)" size="small">
                              {{ policyStateLabel(gf.isActive) }}
                            </el-tag>
                          </div>
                          <div class="create-policy-detail-popover__sections">
                            <section
                              v-for="row in filterHoverRows(gf)"
                              :key="row.label"
                              class="create-policy-detail-popover__section create-policy-detail-popover__section--filter-line"
                            >
                              <span class="create-policy-detail-popover__section-title">{{ row.label }}:</span>
                              <el-tag
                                v-if="row.enabled"
                                size="small"
                                v-bind="policyStateTagAttrs(true)"
                              >
                                {{ row.value }}
                              </el-tag>
                              <span v-else class="create-policy-detail-popover__value">{{ row.value }}</span>
                            </section>
                            <section class="create-policy-detail-popover__section">
                              <div class="create-policy-detail-popover__section-title">
                                {{ t('protection.policiesPage.filterRulesPreviewTitle') }}:
                              </div>
                              <div class="create-filter-rules-preview">
                                <template v-if="filterCompiledRuleLines(gf).length">
                                  <code
                                    v-for="(line, index) in filterCompiledRuleLines(gf)"
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
                      </HflPopover>
                    </el-option>
                  </el-select>
                  <CompressionLevelSelector
                    v-else
                    v-model="batchCompression"
                    mode="select"
                    clearable
                    :options="compressionOptions"
                    :aria-label="t('protection.backupsPage.labelCompressionStrategy')"
                  />
                </div>
                <template #footer>
                  <ElButton @click="filterPolicyBatchDialogOpen = false">
                    {{ t('protection.backupsPage.btnCancel') }}
                  </ElButton>
                  <ElButton type="primary" @click="applyBatchFilterPolicy">
                    {{ t('protection.backupsPage.btnConfirm') }}
                  </ElButton>
                </template>
              </el-dialog>

              <el-table
          v-table-overflow-title
                :data="filteredFilterPolicyGroups"
                row-key="key"
                stripe
                :header-cell-style="TABLE_HEADER_STYLE"
                class="hfl-list-table create-source-config-table filter-policy-config-table"
                @selection-change="onFilterPolicyGroupSelectionChange"
              >
                <el-table-column type="selection" width="35" fixed="left" reserve-selection />
                <el-table-column :label="t('protection.backupsPage.colBackupSource')" min-width="162" fixed="left">
                  <template #default="{ row: group }">
                    <div class="backup-source-cell">
                      <div class="backup-source-cell__body">
                        <span class="create-backup-source-name" :title="group.sourceName">{{ group.sourceName }}</span>
                        <span class="create-backup-source-meta-row">
                          <el-tag size="small" effect="plain" class="create-backup-source-type-tag">
                            {{ backupSourceTypeLabel(group.sourceType) }}
                          </el-tag>
                          <span v-if="group.platform" class="create-backup-source-platform">
                            <AgentPlatformBrandIcon :os="group.platform" />
                          </span>
                        </span>
                      </div>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('protection.backupsPage.labelBackupDirs')"
                  min-width="198"
                  class-name="hfl-table-no-tooltip"
                >
                  <template #default="{ row: group }">
                    <HflPopover
                      trigger="hover"
                      placement="bottom-start"
                      :width="520"
                      :disabled="configSelectMenuOpen"
                    >
                      <template #reference>
                        <div class="create-source-dir-preview create-source-dir-preview--single-line-paths hfl-table-no-tooltip">
                          <div class="create-source-dir-preview__count">
                            {{ t('protection.backupsPage.addedDirTotal', { n: group.entries.length }) }}
                          </div>
                          <div
                            v-for="entry in sourceDirPreviewEntries(group.entries)"
                            :key="entry.key"
                            class="create-source-dir-preview__item"
                          >
                            <component
                              :is="entry.pathType === 'file' ? FileIcon : Folder"
                              :size="14"
                              class="create-source-dir-preview__icon"
                              :class="entry.pathType === 'file' ? 'create-source-dir-preview__icon--file' : 'create-source-dir-preview__icon--folder'"
                            />
                            <span class="create-source-dir-preview__path hfl-table-cell-mono">{{ entry.path }}</span>
                          </div>
                          <div v-if="group.entries.length > 3" class="create-source-dir-preview__more">
                            {{ t('protection.backupsPage.moreDirs', { n: group.entries.length - 3 }) }}
                          </div>
                        </div>
                      </template>
                      <div class="create-table-info-popover create-table-info-popover--dirs create-table-info-popover--selected-paths">
                        <div class="create-table-info-popover__summary">{{ backupDirPopoverSummary(group.entries) }}</div>
                        <div
                          v-for="entry in group.entries"
                          :key="entry.key"
                          class="create-table-info-popover__dir-row create-dir-row create-dir-row--added"
                        >
                          <component
                            :is="entry.pathType === 'file' ? FileIcon : Folder"
                            :size="15"
                            class="create-dir-row__icon"
                            :class="entry.pathType === 'file' ? 'create-dir-row__icon--file' : 'create-dir-row__icon--folder'"
                          />
                          <div class="create-dir-row__body">
                            <span class="create-dir-row__path hfl-table-cell-mono">{{ entry.path }}</span>
                          </div>
                        </div>
                      </div>
                    </HflPopover>
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('protection.backupsPage.labelBackupPolicy')"
                  min-width="220"
                  class-name="hfl-table-no-tooltip"
                >
                  <template #default="{ row: group }">
                    <div class="create-config-summary-cell">
                      <div class="create-config-select-row">
                        <div class="create-config-select-hover-target hfl-table-no-tooltip">
                              <el-select
                                :model-value="groupPolicyId(group)"
                                filterable
                                clearable
                                :placeholder="t('protection.backupsPage.phPolicyForDir')"
                                class="target-picker-grid__target"
                                placement="bottom-start"
                                popper-class="create-policy-select-popper"
                                @update:model-value="setGroupPolicyId(group, String($event ?? ''))"
                                @visible-change="handleConfigSelectVisibleChange"
                              >
                                <el-option
                                  v-for="pol in realPolicies"
                                  :key="pol.id"
                                  :label="pol.name"
                                  :value="pol.id"
                                >
                                  <HflPopover ref="optionPopoverRefs" trigger="hover" placement="bottom-start" :fallback-placements="['top-start', 'right-start']" :width="420" :persistent="false" popper-class="create-policy-option-popper">
                                    <template #reference>
                                      <div class="create-policy-option" @pointerdown.capture="hideOptionPopovers">
                                        <div class="create-policy-option__head">
                                          <span class="create-policy-option__name">{{ pol.name }}</span>
                                          <el-tag v-bind="policyStateTagAttrs(pol.isActive)" size="small">
                                            {{ policyStateLabel(pol.isActive) }}
                                          </el-tag>
                                        </div>
                                        <div class="create-policy-option__summary">{{ policyOptionSummary(pol) }}</div>
                                      </div>
                                    </template>
                                    <div class="create-policy-detail-popover">
                                      <div class="create-policy-detail-popover__head">
                                        <div class="create-policy-detail-popover__title">{{ pol.name }}</div>
                                        <el-tag v-bind="policyStateTagAttrs(pol.isActive)" size="small">
                                          {{ policyStateLabel(pol.isActive) }}
                                        </el-tag>
                                      </div>
                                      <div class="create-policy-detail-popover__sections">
                                        <section
                                          v-for="row in policyDetailRows(pol)"
                                          :key="row.label"
                                          class="create-policy-detail-popover__section create-policy-detail-popover__section--line"
                                        >
                                          <span class="create-policy-detail-popover__section-title">{{ row.label }}:</span>
                                          <el-tag
                                            v-if="row.state !== undefined"
                                            size="small"
                                            v-bind="policyStateTagAttrs(Boolean(row.state))"
                                          >
                                            {{ row.value }}
                                          </el-tag>
                                          <span
                                            v-else
                                            class="create-policy-detail-popover__value"
                                            :class="{ 'create-policy-detail-popover__value--mono': row.mono }"
                                          >{{ row.value }}</span>
                                        </section>
                                        <section class="create-policy-detail-popover__section">
                                          <div class="create-policy-detail-popover__section-title">
                                            {{ t('protection.policiesPage.fieldRetention') }}:
                                          </div>
                                          <div class="create-policy-detail-popover__retention-box">
                                            <div class="policy-retention-detail-list">
                                              <div
                                                v-for="line in policyRetentionDetailLines(pol)"
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
                                  </HflPopover>
                                </el-option>
                                <template #empty>
                                  {{ realPolicies.length ? t('protection.backupsPage.policiesNoMatch') : t('protection.backupsPage.policyTemplatesEmpty') }}
                                </template>
                              </el-select>
                        </div>
                        <ElButton
                          text
                          circle
                          size="small"
                          class="hfl-refresh-button create-config-refresh-btn"
                          :title="t('common.refresh')"
                          :aria-label="t('common.refresh')"
                          :disabled="policiesRefreshing"
                          @click.stop="refreshWizardPolicies"
                        >
                          <RefreshCw :size="15" stroke-width="2" :class="{ 'is-spinning': policiesRefreshing }" />
                        </ElButton>
                      </div>
                      <HflPopover
                        v-if="getRealPolicy(groupPolicyId(group))"
                        trigger="hover"
                        placement="bottom-start"
                        :width="420"
                        :disabled="configSelectMenuOpen"
                        popper-class="create-policy-option-popper"
                      >
                        <template #reference>
                          <span class="create-config-selected-summary hfl-table-no-tooltip">
                            <span
                              class="wizard-summary-cell wizard-summary-cell--inline"
                              :class="Boolean(getRealPolicy(groupPolicyId(group))?.isActive) ? 'wizard-summary-cell--online' : 'wizard-summary-cell--offline'"
                            >
                              <el-tag v-bind="policyStateTagAttrs(Boolean(getRealPolicy(groupPolicyId(group))?.isActive))" size="small">
                                {{ policyStateLabel(Boolean(getRealPolicy(groupPolicyId(group))?.isActive)) }}
                              </el-tag>
                              <span class="wizard-summary-cell__detail">
                                {{ policyCompactSummary(group) }}
                              </span>
                            </span>
                          </span>
                        </template>
                        <div class="create-policy-detail-popover">
                          <div class="create-policy-detail-popover__head">
                            <div class="create-policy-detail-popover__title">{{ getRealPolicy(groupPolicyId(group))?.name }}</div>
                            <el-tag
                              v-if="getRealPolicy(groupPolicyId(group))"
                              size="small"
                              v-bind="policyStateTagAttrs(Boolean(getRealPolicy(groupPolicyId(group))?.isActive))"
                            >
                              {{ policyStateLabel(Boolean(getRealPolicy(groupPolicyId(group))?.isActive)) }}
                            </el-tag>
                          </div>
                          <div class="create-policy-detail-popover__sections">
                            <section
                              v-for="row in policyDetailRows(getRealPolicy(groupPolicyId(group)))"
                              :key="row.label"
                              class="create-policy-detail-popover__section create-policy-detail-popover__section--line"
                            >
                              <span class="create-policy-detail-popover__section-title">{{ row.label }}:</span>
                              <el-tag
                                v-if="row.state !== undefined"
                                size="small"
                                v-bind="policyStateTagAttrs(Boolean(row.state))"
                              >
                                {{ row.value }}
                              </el-tag>
                              <span
                                v-else
                                class="create-policy-detail-popover__value"
                                :class="{ 'create-policy-detail-popover__value--mono': row.mono }"
                              >{{ row.value }}</span>
                            </section>
                            <section class="create-policy-detail-popover__section">
                              <div class="create-policy-detail-popover__section-title">
                                {{ t('protection.policiesPage.fieldRetention') }}:
                              </div>
                              <div class="create-policy-detail-popover__retention-box">
                                <div class="policy-retention-detail-list">
                                  <div
                                    v-for="line in policyRetentionDetailLines(getRealPolicy(groupPolicyId(group)))"
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
                      </HflPopover>
                      <span v-else class="create-config-summary-line create-config-summary-line--empty hfl-table-no-tooltip">
                        {{ policyCompactSummary(group) }}
                      </span>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('protection.backupsPage.labelFileFilter')"
                  min-width="220"
                  class-name="hfl-table-no-tooltip"
                >
                  <template #default="{ row: group }">
                    <div class="create-config-summary-cell">
                      <div class="create-config-select-row">
                        <div class="create-config-select-hover-target hfl-table-no-tooltip">
                              <el-select
                                :model-value="groupFilterId(group)"
                                filterable
                                clearable
                                :placeholder="t('protection.backupsPage.phFilterForDir')"
                                class="target-picker-grid__target"
                                placement="bottom-start"
                                popper-class="create-policy-select-popper"
                                @update:model-value="(value) => setGroupFilterId(group, String(value || ''))"
                                @visible-change="handleConfigSelectVisibleChange"
                              >
                                <el-option
                                  v-for="gf in realFilters"
                                  :key="gf.id"
                                  :label="gf.name"
                                  :value="gf.id"
                                >
                                  <HflPopover ref="optionPopoverRefs" trigger="hover" placement="bottom-start" :fallback-placements="['top-start', 'right-start']" :width="460" :persistent="false" popper-class="create-policy-option-popper">
                                    <template #reference>
                                      <div class="create-policy-option" @pointerdown.capture="hideOptionPopovers">
                                        <div class="create-policy-option__head">
                                          <span class="create-policy-option__name">{{ gf.name }}</span>
                                          <el-tag v-bind="policyStateTagAttrs(gf.isActive)" size="small">
                                            {{ policyStateLabel(gf.isActive) }}
                                          </el-tag>
                                        </div>
                                        <div class="create-filter-rules-option">
                                          <span class="create-filter-rules-option__prefix">{{ t('protection.policiesPage.filterCustomTypeExclude') }}:</span>
                                          <div v-if="filterDisplayedRuleLines(gf).length" class="create-filter-rules-option__list">
                                            <code
                                              v-for="(line, index) in filterDisplayedRuleLines(gf)"
                                              :key="`${index}-${line}`"
                                              class="create-filter-rules-option__rule"
                                            >
                                              {{ line }}
                                            </code>
                                          </div>
                                          <span v-else class="create-filter-rules-option__empty">
                                            {{ t('protection.policiesPage.filterNoActiveRules') }}
                                          </span>
                                        </div>
                                      </div>
                                    </template>
                                    <div class="create-policy-detail-popover">
                                      <div class="create-policy-detail-popover__head">
                                        <div class="create-policy-detail-popover__title">{{ gf.name }}</div>
                                        <el-tag v-bind="policyStateTagAttrs(gf.isActive)" size="small">
                                          {{ policyStateLabel(gf.isActive) }}
                                        </el-tag>
                                      </div>
                                      <div class="create-policy-detail-popover__sections">
                                        <section
                                          v-for="row in filterHoverRows(gf)"
                                          :key="row.label"
                                          class="create-policy-detail-popover__section create-policy-detail-popover__section--filter-line"
                                        >
                                          <span class="create-policy-detail-popover__section-title">{{ row.label }}:</span>
                                          <el-tag
                                            v-if="row.enabled"
                                            size="small"
                                            v-bind="policyStateTagAttrs(true)"
                                          >
                                            {{ row.value }}
                                          </el-tag>
                                          <span v-else class="create-policy-detail-popover__value">{{ row.value }}</span>
                                        </section>
                                        <section class="create-policy-detail-popover__section">
                                          <div class="create-policy-detail-popover__section-title">
                                            {{ t('protection.policiesPage.filterRulesPreviewTitle') }}:
                                          </div>
                                          <div class="create-filter-rules-preview">
                                            <template v-if="filterCompiledRuleLines(gf).length">
                                              <code
                                                v-for="(line, index) in filterCompiledRuleLines(gf)"
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
                                  </HflPopover>
                                </el-option>
                                <template #empty>
                                  {{ realFilters.length ? t('protection.backupsPage.filtersNoMatch') : t('protection.backupsPage.filterRulesEmpty') }}
                                </template>
                              </el-select>
                        </div>
                        <ElButton
                          text
                          circle
                          size="small"
                          class="hfl-refresh-button create-config-refresh-btn"
                          :title="t('common.refresh')"
                          :aria-label="t('common.refresh')"
                          :disabled="filtersRefreshing"
                          @click.stop="refreshWizardFilters"
                        >
                          <RefreshCw :size="15" stroke-width="2" :class="{ 'is-spinning': filtersRefreshing }" />
                        </ElButton>
                      </div>
                      <HflPopover
                        v-if="getRealFilter(groupFilterId(group))"
                        trigger="hover"
                        placement="bottom-start"
                        :width="460"
                        :disabled="configSelectMenuOpen"
                        popper-class="create-policy-option-popper"
                      >
                        <template #reference>
                          <span class="create-config-selected-summary hfl-table-no-tooltip">
                            <span
                              class="wizard-summary-cell wizard-summary-cell--inline"
                              :class="Boolean(getRealFilter(groupFilterId(group))?.isActive) ? 'wizard-summary-cell--online' : 'wizard-summary-cell--offline'"
                            >
                              <el-tag v-bind="policyStateTagAttrs(Boolean(getRealFilter(groupFilterId(group))?.isActive))" size="small">
                                {{ policyStateLabel(Boolean(getRealFilter(groupFilterId(group))?.isActive)) }}
                              </el-tag>
                              <span class="wizard-summary-cell__detail">
                                {{ filterCompactSummary(group) }}
                              </span>
                            </span>
                          </span>
                        </template>
                        <div class="create-policy-detail-popover">
                          <div class="create-policy-detail-popover__head">
                            <div class="create-policy-detail-popover__title">{{ getRealFilter(groupFilterId(group))?.name }}</div>
                            <el-tag
                              v-if="getRealFilter(groupFilterId(group))"
                              size="small"
                              v-bind="policyStateTagAttrs(Boolean(getRealFilter(groupFilterId(group))?.isActive))"
                            >
                              {{ policyStateLabel(Boolean(getRealFilter(groupFilterId(group))?.isActive)) }}
                            </el-tag>
                          </div>
                          <div class="create-policy-detail-popover__sections">
                            <section
                              v-for="row in filterHoverRows(getRealFilter(groupFilterId(group)))"
                              :key="row.label"
                              class="create-policy-detail-popover__section create-policy-detail-popover__section--filter-line"
                            >
                              <span class="create-policy-detail-popover__section-title">{{ row.label }}:</span>
                              <el-tag
                                v-if="row.enabled"
                                size="small"
                                v-bind="policyStateTagAttrs(true)"
                              >
                                {{ row.value }}
                              </el-tag>
                              <span v-else class="create-policy-detail-popover__value">{{ row.value }}</span>
                            </section>
                            <section class="create-policy-detail-popover__section">
                              <div class="create-policy-detail-popover__section-title">
                                {{ t('protection.policiesPage.filterRulesPreviewTitle') }}:
                              </div>
                              <div class="create-filter-rules-preview">
                                <template v-if="filterCompiledRuleLines(getRealFilter(groupFilterId(group))).length">
                                  <code
                                    v-for="(line, index) in filterCompiledRuleLines(getRealFilter(groupFilterId(group)))"
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
                      </HflPopover>
                      <span v-else class="create-config-summary-line create-config-summary-line--empty hfl-table-no-tooltip">
                        {{ filterCompactSummary(group) }}
                      </span>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('protection.backupsPage.labelCompressionStrategy')"
                  min-width="208"
                  class-name="hfl-table-no-tooltip"
                >
                  <template #default="{ row: group }">
                    <div class="create-config-summary-cell create-config-compression-cell">
                      <div class="create-config-select-row">
                        <div class="create-config-select-hover-target hfl-table-no-tooltip">
                          <CompressionLevelSelector
                            :model-value="groupCompression(group)"
                            mode="select"
                            class="target-picker-grid__target create-config-compression-select"
                            :options="compressionOptions"
                            :aria-label="t('protection.backupsPage.labelCompressionStrategy')"
                            @update:model-value="(value) => value && setGroupCompression(group, value)"
                            @visible-change="handleConfigSelectVisibleChange"
                          />
                        </div>
                      </div>
                      <HflPopover
                        trigger="hover"
                        placement="bottom-start"
                        :width="288"
                        :fallback-placements="['bottom-start', 'bottom-end']"
                        :disabled="configSelectMenuOpen"
                        popper-class="create-policy-option-popper create-compression-description-popper"
                      >
                        <template #reference>
                          <span class="create-compression-description hfl-table-no-tooltip">
                            {{ compressionDescription(groupCompression(group)) }}
                          </span>
                        </template>
                        <div class="create-policy-detail-popover create-compression-detail-popover">
                          <div class="create-policy-detail-popover__head">
                            <div class="create-compression-detail-popover__title">
                              <component
                                :is="compressionIcon(groupCompression(group))"
                                :size="17"
                                aria-hidden="true"
                                class="create-compression-detail-popover__icon"
                                :class="`create-compression-detail-popover__icon--${groupCompression(group)}`"
                              />
                              <span>{{ compressionSummary(groupCompression(group)) }}</span>
                            </div>
                          </div>
                          <div class="create-compression-detail-popover__body">
                            {{ compressionTooltip(groupCompression(group)) }}
                          </div>
                        </div>
                      </HflPopover>
                    </div>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </div>
        </div>

        <el-dialog
          v-model="addFilterOpen"
          :title="addPolicyDialogTitle"
          width="1080px"
          destroy-on-close
          class="dp-add-target-dialog create-policy-dialog dp-dialog-soft-bg"
        >
          <p class="create-policy-dialog__desc">{{ addPolicyDialogDesc }}</p>

          <ProtectionPolicyEditorForm
            v-model:policy-form="addPolicyForm"
            v-model:filter-form="addFileFilterForm"
            :show-backup="addPolicyCreateKind === 'backup'"
            :show-filter="addPolicyCreateKind === 'filter'"
            variant="dialog"
            :show-cron-help="false"
            cron-input-id="create-backup-policy-cron"
          />
          <template #footer>
            <div class="flex justify-end gap-2">
              <ElButton @click="closeAddFilterDialog">{{ t('protection.backupsPage.btnCancel') }}</ElButton>
              <ElButton type="primary" :loading="addPolicySaving" @click="submitAddFilterDialog">
                {{ addPolicySaveLabel }}
              </ElButton>
            </div>
          </template>
        </el-dialog>

        <div
          v-if="createStep === 2"
          class="dp-wizard-pane"
          :aria-busy="targetValidationInProgress"
        >
          <div v-if="!wizardDirEntries.length" class="text-sm text-slate-400 py-8 text-center border border-dashed border-slate-200 rounded-md">
            {{ t('protection.backupsPage.addedEmpty') }}
          </div>
          <div v-else>
            <div class="create-source-config-toolbar create-source-config-toolbar--actions hfl-list-toolbar">
              <div class="create-source-config-selection">
                {{ t('protection.backupsPage.selectedSourceCount', { selected: checkedTargetGroups.length, total: wizardSourceGroups.length }) }}
              </div>
              <div class="create-source-config-toolbar__divider"></div>
              <ElButton
                type="primary"
                class="hfl-btn-with-icon dp-create-action-btn"
                :disabled="targetValidationInProgress"
                @click="goToStorageRepositoryPage"
              >
                <Plus :size="16" stroke-width="2" class="shrink-0" />
                <span>{{ t('protection.backupsPage.btnAddTarget') }}</span>
              </ElButton>
              <ElButton
                type="primary"
                class="hfl-btn-with-icon dp-create-action-btn"
                :disabled="targetValidationInProgress || checkedTargetGroups.length === 0"
                @click="openBatchTargetDialog"
              >
                <Database :size="16" stroke-width="2" class="shrink-0" />
                <span>{{ t('protection.backupsPage.batchAssignTitle') }}</span>
              </ElButton>
              <div class="hfl-list-toolbar__right">
                <ElInput
                  v-model="targetSourceSearch"
                  clearable
                  @clear="clearTargetSourceSearch"
                  size="small"
                  :placeholder="t('protection.backupsPage.phSearchCreateSources')"
                  class="create-source-config-search hfl-list-search"
                >
                  <template #prefix>
                    <Search :size="16" class="hfl-list-search__icon" />
                  </template>
                </ElInput>
              </div>
            </div>

            <div
              v-if="targetValidationInProgress || targetValidationHasFailures"
              class="target-validation-status"
              :class="targetValidationInProgress
                ? 'target-validation-status--pending'
                : 'target-validation-status--failed'"
              role="status"
              aria-live="polite"
              aria-atomic="true"
            >
              <RefreshCw
                v-if="targetValidationInProgress"
                :size="16"
                class="target-validation-status__icon target-validation-status__spinner"
                aria-hidden="true"
              />
              <ShieldAlert
                v-else
                :size="16"
                class="target-validation-status__icon"
                aria-hidden="true"
              />
              <span class="target-validation-status__text">
                {{ targetValidationInProgress
                  ? t('protection.backupsPage.targetValidationInProgress')
                  : t('protection.backupsPage.targetValidationFailedStatus', targetValidationCounts) }}
              </span>
            </div>

            <el-dialog
              v-model="targetAssignDialogOpen"
              :title="targetAssignDialogTitle"
              width="640px"
              destroy-on-close
              class="target-batch-dialog dp-dialog-soft-bg"
            >
              <ElForm label-position="top" class="target-batch-form">
                <TargetRepositoryPicker
                  :model-value="batchTargetPicker.targetId"
                  v-model:repo-type="batchTargetPicker.repoType"
                  v-model:nas-mode="batchNasTargetMode"
                  :targets="targetOptionsForBatch()"
                  :selected-target="batchSelectedTarget"
                  :repo-type-options="repoTypeOptions"
                  :target-placeholder="t('protection.backupsPage.phSearchTargets')"
                  :repo-type-placeholder="t('protection.backupsPage.phAllRepoTypes')"
                  :no-data-text="t('protection.backupsPage.targetsEmpty')"
                  refreshable
                  :refreshing="targetsRefreshing"
                  :refresh-title="t('common.refresh')"
                  @update:model-value="onTargetPickerTargetChange"
                  @update:repo-type="onTargetPickerTargetChange('')"
                  @search="(query) => setTargetPickerSearch(batchTargetPicker, query)"
                  @visible-change="(visible) => onTargetPickerVisible(batchTargetPicker, visible)"
                  @refresh="refreshWizardTargets"
                />
                <ElFormItem
                  v-if="batchSelectedTargetRequiresEndpoint"
                  :label="t('addS3Repo.fieldEndpointType')"
                  required
                  class="target-endpoint-choice"
                >
                  <ElRadioGroup
                    v-model="batchRepositoryEndpointType"
                    class="target-endpoint-choice__options"
                  >
                    <ElRadio value="external" border>
                      <span class="target-endpoint-choice__option">
                        <strong>{{ t('addS3Repo.endpointExternal') }}</strong>
                        <span>{{ targetExternalEndpoint(batchSelectedTarget) }}</span>
                      </span>
                    </ElRadio>
                    <ElRadio value="internal" border>
                      <span class="target-endpoint-choice__option">
                        <strong>{{ t('addS3Repo.endpointInternal') }}</strong>
                        <span>{{ targetInternalEndpoint(batchSelectedTarget) }}</span>
                      </span>
                    </ElRadio>
                  </ElRadioGroup>
                  <p class="target-endpoint-choice__hint">{{ t('addS3Repo.hintEndpointType') }}</p>
                </ElFormItem>
              </ElForm>
              <template #footer>
                <ElButton @click="targetAssignDialogOpen = false">
                  {{ t('protection.backupsPage.btnCancel') }}
                </ElButton>
                <ElButton
                  type="primary"
                  :disabled="batchSelectedTargetRequiresEndpoint && !batchRepositoryEndpointType"
                  @click="applyTargetAssignment"
                >
                  {{ t('protection.backupsPage.btnConfirm') }}
                </ElButton>
              </template>
            </el-dialog>

            <el-table
          v-table-overflow-title
              :data="filteredTargetGroups"
              row-key="key"
              stripe
              :header-cell-style="TABLE_HEADER_STYLE"
              :row-class-name="targetGroupRowClassName"
              class="hfl-list-table create-source-config-table filter-policy-config-table create-target-config-table"
              @selection-change="onTargetGroupSelectionChange"
            >
              <el-table-column type="selection" width="35" fixed="left" reserve-selection />
              <el-table-column :label="t('protection.backupsPage.colBackupSource')" min-width="200" fixed="left">
                <template #default="{ row: group }">
                  <div class="backup-source-cell">
                    <div class="backup-source-cell__body">
                      <span class="create-backup-source-name" :title="group.sourceName">{{ group.sourceName }}</span>
                      <span class="create-backup-source-meta-row">
                        <el-tag size="small" effect="plain" class="create-backup-source-type-tag">
                          {{ backupSourceTypeLabel(group.sourceType) }}
                        </el-tag>
                        <span v-if="group.platform" class="create-backup-source-platform">
                          <AgentPlatformBrandIcon :os="group.platform" />
                        </span>
                      </span>
                    </div>
                  </div>
                </template>
              </el-table-column>
              <el-table-column :label="t('protection.backupsPage.labelBackupDirs')" min-width="200">
                <template #default="{ row: group }">
                  <HflPopover trigger="hover" placement="top-start" :width="520">
                    <template #reference>
                      <div class="create-source-dir-preview">
                        <div class="create-source-dir-preview__count">
                          {{ t('protection.backupsPage.addedDirTotal', { n: group.entries.length }) }}
                        </div>
                        <div
                          v-for="entry in sourceDirPreviewEntries(group.entries)"
                          :key="entry.key"
                          class="create-source-dir-preview__item"
                        >
                          <component
                            :is="entry.pathType === 'file' ? FileIcon : Folder"
                            :size="14"
                            class="create-source-dir-preview__icon"
                            :class="entry.pathType === 'file' ? 'create-source-dir-preview__icon--file' : 'create-source-dir-preview__icon--folder'"
                          />
                          <span class="create-source-dir-preview__path hfl-table-cell-mono">{{ entry.path }}</span>
                        </div>
                        <div v-if="group.entries.length > 3" class="create-source-dir-preview__more">
                          {{ t('protection.backupsPage.moreDirs', { n: group.entries.length - 3 }) }}
                        </div>
                      </div>
                    </template>
                    <div class="create-table-info-popover create-table-info-popover--dirs create-table-info-popover--selected-paths">
                      <div class="create-table-info-popover__summary">{{ backupDirPopoverSummary(group.entries) }}</div>
                      <div
                        v-for="entry in group.entries"
                        :key="entry.key"
                        class="create-table-info-popover__dir-row create-dir-row create-dir-row--added"
                      >
                        <component
                          :is="entry.pathType === 'file' ? FileIcon : Folder"
                          :size="15"
                          class="create-dir-row__icon"
                          :class="entry.pathType === 'file' ? 'create-dir-row__icon--file' : 'create-dir-row__icon--folder'"
                        />
                        <div class="create-dir-row__body">
                          <span class="create-dir-row__path hfl-table-cell-mono">{{ entry.path }}</span>
                        </div>
                      </div>
                    </div>
                  </HflPopover>
                </template>
              </el-table-column>
              <el-table-column :label="t('protection.backupsPage.labelTargetRepository')" min-width="220">
                <template #default="{ row: group }">
                  <div class="target-select-with-meta">
                    <HflPopover
                      v-if="getRealTarget(sourceTargetMap[group.key])"
                      trigger="hover"
                      placement="top-start"
                      :width="420"
                      :show-after="300"
                    >
                      <template #reference>
                        <div class="target-assignment-summary">
                          <div
                            class="wizard-summary-cell"
                            :class="`wizard-summary-cell--${targetTone(getRealTarget(sourceTargetMap[group.key]))}`"
                          >
                            <span class="wizard-summary-cell__dot" aria-hidden="true"></span>
                            <div class="wizard-summary-cell__body">
                              <div class="wizard-summary-cell__name">
                                {{ getRealTarget(sourceTargetMap[group.key])?.name }}
                              </div>
                              <div
                                v-if="getRealTarget(sourceTargetMap[group.key])?.location"
                                class="wizard-summary-cell__detail hfl-table-cell-mono"
                              >
                                {{ getRealTarget(sourceTargetMap[group.key])?.location }}
                              </div>
                              <div
                                v-if="endpointSummaryForGroup(group)"
                                class="wizard-summary-cell__detail hfl-table-cell-mono"
                              >
                                {{ endpointSummaryForGroup(group) }}
                              </div>
                            </div>
                          </div>
                        </div>
                      </template>
                      <TargetRepositoryDetailCard :target="getRealTarget(sourceTargetMap[group.key])!" />
                    </HflPopover>
                    <span v-else class="target-assignment-empty">
                      {{ t('protection.backupsPage.targetUnassigned') }}
                    </span>
                    <ElButton
                      text
                      circle
                      size="small"
                      class="create-wizard-icon-action-btn target-assignment-change-btn"
                      :disabled="targetValidationInProgress"
                      :title="getRealTarget(sourceTargetMap[group.key]) ? t('protection.backupsPage.btnEdit') : t('protection.backupsPage.phTargetForDir')"
                      @click="openSingleTargetDialog(group)"
                    >
                      <Pencil :size="15" />
                    </ElButton>
                    <div
                      v-if="targetGroupIssue(group)"
                      class="target-assignment-issue"
                      role="status"
                    >
                      <ShieldAlert :size="14" class="target-assignment-issue__icon" />
                      <span>{{ targetGroupIssue(group) }}</span>
                    </div>
                    <div
                      v-if="targetValidationInProgress"
                      class="target-connection-result target-connection-result--pending"
                      aria-hidden="true"
                    >
                      <RefreshCw
                        :size="14"
                        class="target-connection-result__icon target-connection-result__spinner"
                      />
                      <span class="target-connection-result__summary">
                        {{ t('protection.backupsPage.targetValidationRowInProgress') }}
                      </span>
                    </div>
                    <div
                      v-else-if="targetConnectionResult(group.key)"
                      class="target-connection-result"
                      :class="`target-connection-result--${targetConnectionResult(group.key)?.status}`"
                      role="status"
                    >
                      <component
                        :is="targetConnectionResult(group.key)?.status === 'success' ? ShieldCheck : ShieldAlert"
                        :size="14"
                        class="target-connection-result__icon"
                      />
                      <span class="target-connection-result__summary">
                        {{ targetConnectionResult(group.key)?.status === 'success'
                          ? t('protection.backupsPage.targetValidationSucceeded')
                          : targetConnectionFailureSummary(group) }}
                      </span>
                      <button
                        v-if="targetConnectionResult(group.key)?.status === 'failed'"
                        type="button"
                        class="target-connection-result__details"
                        @click.stop="showTargetConnectionFailureDetails(group)"
                      >
                        {{ t('feedback.toast.viewDetails') }}
                      </button>
                    </div>
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>

        <div v-if="createStep === 3" class="dp-wizard-pane">
          <p class="text-sm text-slate-500 mb-3 m-0">
            {{ t('protection.backupsPage.createRecoveryPlanLead') }}
          </p>
          <div>
            <div class="create-source-config-toolbar create-source-config-toolbar--actions hfl-list-toolbar">
              <div class="create-source-config-selection">
                {{ t('protection.backupsPage.selectedSourceCount', { selected: checkedRecoveryPlanGroups.length, total: createRecoveryPlanGroups.length }) }}
              </div>
              <div class="create-source-config-toolbar__divider"></div>
              <ElDropdown trigger="click" @command="(command) => applyBatchRecoveryPlanEnabled(command === 'enable')">
                <ElButton class="hfl-btn-with-icon">
                  <span>{{ t('protection.backupsPage.btnBatchActions') }}</span>
                  <ChevronDown :size="15" stroke-width="2" class="shrink-0" />
                </ElButton>
                <template #dropdown>
                  <ElDropdownMenu>
                    <ElDropdownItem command="enable" :disabled="checkedRecoveryPlanDisabledGroups.length === 0">
                      {{ t('protection.backupsPage.batchEnableRecoveryPlanAction') }}
                    </ElDropdownItem>
                    <ElDropdownItem command="disable" :disabled="checkedRecoveryPlanEnabledGroups.length === 0">
                      {{ t('protection.backupsPage.batchDisableRecoveryPlanAction') }}
                    </ElDropdownItem>
                  </ElDropdownMenu>
                  </template>
                </ElDropdown>
              <div class="hfl-list-toolbar__right">
                <ElInput
                  v-model="recoveryPlanSourceSearch"
                  clearable
                  @clear="clearRecoveryPlanSourceSearch"
                  size="small"
                  :placeholder="t('protection.backupsPage.phSearchCreateSources')"
                  class="create-source-config-search hfl-list-search"
                >
                  <template #prefix>
                    <Search :size="16" class="hfl-list-search__icon" />
                  </template>
                </ElInput>
              </div>
            </div>
            <el-table
          v-table-overflow-title
              ref="createRecoveryPlanTableRef"
              :data="filteredRecoveryPlanGroups"
              row-key="key"
              stripe
              :header-cell-style="TABLE_HEADER_STYLE"
              :expand-row-keys="createRecoveryPlanExpandedKeys"
              class="hfl-list-table create-source-config-table filter-policy-config-table create-recovery-plan-table"
              @expand-change="onCreateRecoveryPlanExpandChange"
              @selection-change="onRecoveryPlanGroupSelectionChange"
            >
              <el-table-column type="selection" width="35" fixed="left" reserve-selection />
              <el-table-column type="expand" width="35" fixed="left" class-name="create-source-config-expand-column">
                <template #default="{ row: group }">
                  <div class="create-recovery-plan-expand">
                    <div v-if="group.plan.enabled" class="create-recovery-config-panel">
                      <div class="create-recovery-config-panel__head">
                        <div class="create-recovery-config-panel__title-wrap">
                          <div class="create-recovery-config-panel__title">
                            {{ t('protection.backupsPage.flowActionConfigureRecoveryPlan') }}
                          </div>
                        </div>
                        <div class="create-recovery-config-panel__policy">
                          <span class="create-recovery-config-panel__policy-label create-recovery-required-label">
                            {{ t('protection.backupsPage.fileConflictPolicyLabel') }}
                            <span class="create-recovery-required-mark">*</span>
                          </span>
                          <el-select
                            :model-value="group.plan.conflictMode"
                            class="create-recovery-conflict-policy-select"
                            :class="{
                              'create-recovery-conflict-policy-select--invalid': !group.plan.conflictMode || isCreateRecoveryConflictPolicyInvalid(group),
                              'create-recovery-conflict-policy-select--skip': group.plan.conflictMode === 'skip',
                              'create-recovery-conflict-policy-select--overwrite': group.plan.conflictMode === 'overwrite',
                            }"
                            :placeholder="t('protection.backupsPage.fileConflictPolicyPlaceholder')"
                            @update:model-value="(value) => updateRecoveryPlanConflictMode(group, value)"
                          >
                            <el-option :label="t('protection.backupsPage.createRecoveryConflictSkip')" value="skip">
                              <div class="recovery-conflict-policy-option recovery-conflict-policy-option--skip">
                                <ShieldCheck :size="14" />
                                <span>{{ t('protection.backupsPage.createRecoveryConflictSkipFull') }}</span>
                              </div>
                            </el-option>
                            <el-option :label="t('protection.backupsPage.createRecoveryConflictOverwrite')" value="overwrite">
                              <div class="recovery-conflict-policy-option recovery-conflict-policy-option--overwrite">
                                <ShieldAlert :size="14" />
                                <span>{{ t('protection.backupsPage.createRecoveryConflictOverwriteFull') }}</span>
                              </div>
                            </el-option>
                          </el-select>
                        </div>
                      </div>
                      <div class="create-recovery-dir-plan-stack">
                        <div class="create-recovery-dir-plan-header" aria-hidden="true">
                          <span />
                          <span class="create-recovery-required-label">
                            {{ t('protection.backupsPage.createRecoveryPlanScopeCol') }}
                            <span class="create-recovery-required-mark">*</span>
                            <HflHelpTip
                              :content="t('protection.backupsPage.createRecoveryPathInputHint')"
                              :size="13"
                              :aria-label="t('protection.backupsPage.createRecoveryPlanScopeCol')"
                            />
                          </span>
                          <span class="create-recovery-required-label">
                            {{ t('protection.backupsPage.createRecoveryPlanTargetHostCol') }}
                            <span class="create-recovery-required-mark">*</span>
                          </span>
                          <span class="create-recovery-required-label">
                            {{ t('protection.backupsPage.createRecoveryPlanTargetDirCol') }}
                            <span class="create-recovery-required-mark">*</span>
                            <HflHelpTip
                              :content="t('protection.backupsPage.createRecoveryPathInputHint')"
                              :size="13"
                              :aria-label="t('protection.backupsPage.createRecoveryPlanTargetDirCol')"
                            />
                          </span>
                          <span>{{ t('protection.backupsPage.colActions') }}</span>
                        </div>
                        <div
                          v-for="(dirPlan, dirPlanIndex) in group.plan.dirPlans"
                          :key="dirPlan.id"
                          class="create-recovery-dir-plan-row"
                          :class="{ 'create-recovery-dir-plan-row--invalid': highlightedRecoveryDirPlanIds.includes(dirPlan.id) }"
                          :data-recovery-dir-plan-id="dirPlan.id"
                        >
                          <span class="create-recovery-dir-plan-row__index">{{ String(dirPlanIndex + 1).padStart(2, '0') }}</span>
                          <div
                            class="create-recovery-dir-plan-cell"
                            :class="{ 'create-recovery-dir-plan-cell--invalid': isCreateRecoveryDirPlanFieldInvalid(group, dirPlan, 'sourcePath') }"
                            :data-label="t('protection.backupsPage.createRecoveryPlanScopeCol')"
                          >
                            <HflPopover
                              :visible="isCreateRecoverySourcePathPickerVisible(group, dirPlan)"
                              trigger="manual"
                              placement="bottom-start"
                              :width="360"
                              popper-class="create-recovery-path-popover"
                              @update:visible="(visible) => setCreateRecoverySourcePathPickerVisible(group, dirPlan, Boolean(visible))"
                            >
                              <template #reference>
                                <div class="create-recovery-path-input">
                                  <el-input
                                    :model-value="recoverySourcePathInputValue(dirPlan.sourcePath)"
                                    clearable
                                    :placeholder="t('protection.backupsPage.phSelectOrEnterRestoreScope')"
                                    :class="{
                                      'create-recovery-path-input--pending': dirPlan.sourcePathValidation === 'pending',
                                      'create-recovery-path-input--invalid': isCreateRecoverySourcePathInputInvalid(group, dirPlan),
                                    }"
                                    :aria-describedby="isCreateRecoverySourcePathErrorVisible(group, dirPlan) ? `create-recovery-source-path-error-${group.key}-${dirPlan.id}` : undefined"
                                    @click.stop
                                    @update:model-value="(value) => updateCreateRecoverySourcePathInput(group, dirPlan, String(value))"
                                    @blur="dirPlan.sourcePathValidation === 'pending' && validateCreateRecoverySourcePathInput(group, dirPlan)"
                                    @keydown.enter.prevent="validateCreateRecoverySourcePathInput(group, dirPlan)"
                                  >
                                    <template v-if="isDirectoryLoading(recoveryDirPlanPickerKey(group, dirPlan, 'source'))" #suffix>
                                      <span class="create-recovery-path-input__checking">{{ t('common.loading') }}</span>
                                    </template>
                                    <template #prefix>
                                      <component
                                        :is="createRecoverySourcePathInputIcon(dirPlan)"
                                        :size="14"
                                        class="create-recovery-path-input__type-icon"
                                        :class="createRecoverySourcePathInputIconClass(dirPlan)"
                                      />
                                    </template>
                                    <template #append>
                                      <ElButton
                                        class="create-recovery-path-input__btn"
                                        :aria-label="t('protection.backupsPage.btnBrowsePaths')"
                                        @click.stop="setCreateRecoverySourcePathPickerVisible(group, dirPlan, !isCreateRecoverySourcePathPickerVisible(group, dirPlan))"
                                      >
                                        <FolderOpen :size="16" />
                                      </ElButton>
                                    </template>
                                  </el-input>
                                  <p
                                    v-if="isCreateRecoverySourcePathErrorVisible(group, dirPlan)"
                                    :id="`create-recovery-source-path-error-${group.key}-${dirPlan.id}`"
                                    class="create-recovery-path-input__error"
                                    role="alert"
                                  >
                                    <span>{{ createRecoverySourcePathInputError(group, dirPlan) }}</span>
                                    <button
                                      type="button"
                                      class="create-recovery-path-input__error-close"
                                      :aria-label="t('protection.backupsPage.btnClose')"
                                      @click.stop="dismissCreateRecoveryPathError(group, dirPlan, 'source')"
                                    >
                                      <X :size="13" />
                                    </button>
                                  </p>
                                </div>
                              </template>
                              <div class="create-recovery-tree-popover hfl-dir-tree-shell">
                                <el-tree
                                  :ref="(el) => setCreateRecoveryDirectoryTreeRef(recoveryDirPlanPickerKey(group, dirPlan, 'source'), el)"
                                  :key="`create-recovery-source-${group.key}-${dirPlan.id}`"
                                  v-loading="isDirectoryLoading(recoveryDirPlanPickerKey(group, dirPlan, 'source'))"
                                  class="source-dir-tree create-recovery-popover-tree hfl-dir-tree hfl-dir-tree--tall"
                                  node-key="path"
                                  lazy
                                  :expand-on-click-node="false"
                                  :load="(node, resolve) => loadCreateRecoverySourceTreeNode(group, node, resolve, recoveryDirPlanPickerKey(group, dirPlan, 'source'))"
                                  :props="dirTreeProps"
                                  :current-node-key="dirPlan.sourcePath"
                                  highlight-current
                                  @node-click="(data) => onCreateRecoverySourcePathTreePick(group, dirPlan, data)"
                                  @node-collapse="(data) => onCreateRecoveryDirectoryExpansionChange(recoveryDirPlanPickerKey(group, dirPlan, 'source'), data)"
                                  @node-expand="(data) => onCreateRecoveryDirectoryExpansionChange(recoveryDirPlanPickerKey(group, dirPlan, 'source'), data)"
                                >
                                  <template #default="{ data }">
                                    <div class="create-tree-node-content hfl-dir-tree-node">
                                      <Camera
                                        v-if="isWholeSnapshotRecoveryPath(data.path)"
                                        :size="15"
                                        class="create-tree-node-content__icon hfl-dir-tree-node__icon create-dir-row__icon--snapshot"
                                      />
                                      <FileIcon
                                        v-else-if="data.path_type === 'file'"
                                        :size="15"
                                        class="create-tree-node-content__icon hfl-dir-tree-node__icon create-dir-row__icon--file"
                                      />
                                      <FolderOpen v-else :size="15" class="create-tree-node-content__icon hfl-dir-tree-node__icon create-dir-row__icon--folder" />
                                      <div class="create-tree-node-content__text hfl-dir-tree-node__text">
                                        <span class="create-tree-node-content__label hfl-dir-tree-node__label">{{ data.label }}</span>
                                        <span class="create-tree-node-content__path hfl-dir-tree-node__path">{{ createRecoverySourceTreePathLabel(data) }}</span>
                                      </div>
                                      <button
                                        v-if="data.path_type === 'directory' && !isWholeSnapshotRecoveryPath(data.path)"
                                        type="button"
                                        class="hfl-dir-tree-node__refresh"
                                        :class="{ 'is-refreshing': isCreateRecoveryDirectoryRefreshing(recoveryDirPlanPickerKey(group, dirPlan, 'source'), data.path) }"
                                        :disabled="isCreateRecoveryDirectoryRefreshing(recoveryDirPlanPickerKey(group, dirPlan, 'source'), data.path)"
                                        :aria-label="t('protection.backupsPage.ariaRefreshDirectory', { path: data.path })"
                                        :title="t('protection.backupsPage.ariaRefreshDirectory', { path: data.path })"
                                        @click.stop="refreshCreateRecoverySourceDirectory(group, dirPlan, data)"
                                      >
                                        <RefreshCw
                                          :size="14"
                                          :class="{ 'is-spinning': isCreateRecoveryDirectoryRefreshing(recoveryDirPlanPickerKey(group, dirPlan, 'source'), data.path) }"
                                        />
                                      </button>
                                    </div>
                                  </template>
                                </el-tree>
                              </div>
                            </HflPopover>
                          </div>
                          <div
                            class="create-recovery-dir-plan-cell"
                            :class="{ 'create-recovery-dir-plan-cell--invalid': isCreateRecoveryDirPlanFieldInvalid(group, dirPlan, 'targetHostId') }"
                            :data-label="t('protection.backupsPage.createRecoveryPlanTargetHostCol')"
                          >
                            <el-select
                              :ref="(el) => setCreateRecoveryTargetSelectRef(group, dirPlan, el)"
                              :model-value="dirPlan.targetHostId"
                              filterable
                              remote
                              reserve-keyword
                              class="w-full"
                              :remote-method="restoreTargetCatalog.setSearch"
                              :loading="recoveryTargetLoading || recoveryTargetLoadingMore"
                              popper-class="create-recovery-target-node-select-popper"
                              :placeholder="t('protection.backupsPage.createRecoveryTargetHostPlaceholder')"
                              @visible-change="onCreateRecoveryTargetVisible"
                              @popup-scroll="onCreateRecoveryTargetPopupScroll"
                              @update:model-value="(value) => onCreateRecoveryTargetHostChange(group, dirPlan, value)"
                            >
                              <template #header>
                                <div class="create-recovery-target-quick-option">
                                  <ElTooltip
                                    :disabled="createRecoverySourceTargetAvailable(group)"
                                    :content="t('protection.backupsPage.recoverySourceHostUnavailable')"
                                    placement="top"
                                  >
                                    <span class="create-recovery-target-quick-option__wrap">
                                      <ElButton
                                        type="primary"
                                        plain
                                        class="create-recovery-target-quick-option__button"
                                        :class="{
                                          'is-selected': isCreateRecoverySourceTargetSelected(group, dirPlan),
                                          'is-disabled': !createRecoverySourceTargetAvailable(group),
                                        }"
                                        :disabled="!createRecoverySourceTargetAvailable(group)"
                                        :aria-label="t('protection.backupsPage.recoveryUseSourceHost')"
                                        @click.stop="selectCreateRecoverySourceTargetAndClose(group, dirPlan)"
                                      >
                                        <span class="create-recovery-target-quick-option__content">
                                          <component
                                            :is="backupSourceTypeIcon('host')"
                                            :size="15"
                                            class="create-recovery-target-quick-option__icon"
                                          />
                                          <span class="create-recovery-target-quick-option__text">{{ t('protection.backupsPage.recoveryUseSourceHost') }}</span>
                                        </span>
                                      </ElButton>
                                    </span>
                                  </ElTooltip>
                                </div>
                              </template>
                              <el-option
                                v-for="option in createRecoveryTargetOptionsForGroup(group, dirPlan)"
                                :key="option.value"
                                :label="option.label"
                                :value="option.value"
                                :disabled="!option.selectable"
                              >
                                <div class="create-recovery-target-node-option">
                                  <div class="create-recovery-target-node-option__main">
                                    <span
                                      class="create-recovery-target-node-option__label"
                                      :title="option.summary.displayName"
                                    >
                                      {{ option.summary.displayName }}
                                    </span>
                                    <el-tag
                                      v-if="option.isSource"
                                      size="small"
                                      effect="plain"
                                      type="success"
                                    >
                                      {{ t('protection.backupsPage.createRecoveryTargetSourceTag') }}
                                    </el-tag>
                                    <el-tag
                                      size="small"
                                      effect="plain"
                                      :type="option.summary.typeTagType"
                                    >
                                      {{ option.summary.typeLabel }}
                                    </el-tag>
                                  </div>
                                  <div class="create-recovery-target-node-option__meta-row">
                                    <span
                                      class="create-recovery-target-node-option__meta"
                                      :title="option.summary.ipLine"
                                    >
                                      {{ option.summary.ipLine }}
                                    </span>
                                    <span class="create-recovery-target-node-option__platform">
                                      <AgentPlatformBrandIcon
                                        v-if="option.summary.platform"
                                        :os="option.summary.platform"
                                      />
                                      <component
                                        :is="backupSourceTypeIcon(option.summary.sourceType)"
                                        v-else
                                        :size="16"
                                      />
                                    </span>
                                  </div>
                                </div>
                              </el-option>
                              <el-option
                                v-if="recoveryTargetLoadingMore"
                                key="create-recovery-target-loading-more"
                                disabled
                                value="__loading_more__"
                                :label="t('common.loading')"
                              />
                              <el-option
                                v-else-if="recoveryTargetError"
                                key="create-recovery-target-load-more-error"
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
                                  <span>{{ recoveryTargetError ? t('protection.backupsPage.recoveryTargetLoadFailed') : t('protection.backupsPage.recoveryTargetEmpty') }}</span>
                                  <ElButton v-if="recoveryTargetError" link type="primary" @click.stop="restoreTargetCatalog.retry">
                                    {{ t('common.retry') }}
                                  </ElButton>
                                </div>
                              </template>
                            </el-select>
                          </div>
                          <div
                            class="create-recovery-dir-plan-cell"
                            :class="{ 'create-recovery-dir-plan-cell--invalid': isCreateRecoveryDirPlanFieldInvalid(group, dirPlan, 'restoreDir') }"
                            :data-label="t('protection.backupsPage.createRecoveryPlanTargetDirCol')"
                          >
                            <HflPopover
                              :visible="isCreateRecoveryDestPathPickerVisible(group, dirPlan)"
                              trigger="manual"
                              placement="bottom-start"
                              :width="360"
                              popper-class="create-recovery-path-popover"
                              @update:visible="(visible) => setCreateRecoveryDestPathPickerVisible(group, dirPlan, Boolean(visible))"
                            >
                              <template #reference>
                                <div class="create-recovery-path-input">
                                  <el-input
                                    :model-value="dirPlan.restoreDir"
                                    clearable
                                    :placeholder="t('protection.backupsPage.phSelectOrEnterRestoreDirectory')"
                                    :class="{
                                      'create-recovery-path-input--pending': dirPlan.restoreDirValidation === 'pending',
                                      'create-recovery-path-input--invalid': isCreateRecoveryDestPathInputInvalid(group, dirPlan),
                                    }"
                                    :aria-describedby="isCreateRecoveryDestPathErrorVisible(group, dirPlan) ? `create-recovery-dest-path-error-${group.key}-${dirPlan.id}` : undefined"
                                    @click.stop
                                    @update:model-value="(value) => updateCreateRecoveryDestPathInput(group, dirPlan, String(value))"
                                    @blur="dirPlan.restoreDirValidation === 'pending' && validateCreateRecoveryDestPathInput(group, dirPlan)"
                                    @keydown.enter.prevent="validateCreateRecoveryDestPathInput(group, dirPlan)"
                                  >
                                    <template v-if="isDirectoryLoading(recoveryDirPlanPickerKey(group, dirPlan, 'dest'))" #suffix>
                                      <span class="create-recovery-path-input__checking">{{ t('common.loading') }}</span>
                                    </template>
                                    <template #prefix>
                                      <component
                                        :is="createRecoveryDestPathInputIcon(dirPlan)"
                                        :size="14"
                                        class="create-recovery-path-input__type-icon"
                                        :class="createRecoveryDestPathInputIconClass(dirPlan)"
                                      />
                                    </template>
                                    <template #append>
                                      <ElButton
                                        class="create-recovery-path-input__btn"
                                        :aria-label="t('protection.backupsPage.btnBrowsePaths')"
                                        @click.stop="setCreateRecoveryDestPathPickerVisible(group, dirPlan, !isCreateRecoveryDestPathPickerVisible(group, dirPlan))"
                                      >
                                        <FolderOpen :size="16" />
                                      </ElButton>
                                    </template>
                                  </el-input>
                                  <p
                                    v-if="isCreateRecoveryDestPathErrorVisible(group, dirPlan)"
                                    :id="`create-recovery-dest-path-error-${group.key}-${dirPlan.id}`"
                                    class="create-recovery-path-input__error"
                                    role="alert"
                                  >
                                    <span>{{ createRecoveryDestPathInputError(group, dirPlan) }}</span>
                                    <button
                                      type="button"
                                      class="create-recovery-path-input__error-close"
                                      :aria-label="t('protection.backupsPage.btnClose')"
                                      @click.stop="dismissCreateRecoveryPathError(group, dirPlan, 'dest')"
                                    >
                                      <X :size="13" />
                                    </button>
                                  </p>
                                </div>
                              </template>
                              <div class="create-recovery-tree-popover hfl-dir-tree-shell">
                                <el-tree
                                  :ref="(el) => setCreateRecoveryDirectoryTreeRef(recoveryDirPlanPickerKey(group, dirPlan, 'dest'), el)"
                                  :key="`create-recovery-dest-${group.key}-${dirPlan.id}-${dirPlan.targetHostId}`"
                                  v-loading="isDirectoryLoading(recoveryDirPlanPickerKey(group, dirPlan, 'dest'))"
                                  class="source-dir-tree create-recovery-popover-tree hfl-dir-tree hfl-dir-tree--tall"
                                  node-key="path"
                                  lazy
                                  :expand-on-click-node="false"
                                  :load="(node, resolve) => loadCreateRecoveryDestTreeNode(dirPlan, node, resolve, recoveryDirPlanPickerKey(group, dirPlan, 'dest'))"
                                  :props="dirTreeProps"
                                  :current-node-key="dirPlan.restoreDir"
                                  highlight-current
                                  @node-click="(data) => onCreateRecoveryDestPathTreePick(group, dirPlan, data)"
                                  @node-collapse="(data) => onCreateRecoveryDirectoryExpansionChange(recoveryDirPlanPickerKey(group, dirPlan, 'dest'), data)"
                                  @node-expand="(data) => onCreateRecoveryDirectoryExpansionChange(recoveryDirPlanPickerKey(group, dirPlan, 'dest'), data)"
                                >
                                  <template #default="{ data }">
                                    <div class="create-tree-node-content hfl-dir-tree-node">
                                      <FolderOpen :size="15" class="create-tree-node-content__icon hfl-dir-tree-node__icon" />
                                      <div class="create-tree-node-content__text hfl-dir-tree-node__text">
                                        <span class="create-tree-node-content__label hfl-dir-tree-node__label">{{ data.label }}</span>
                                        <span class="create-tree-node-content__path hfl-dir-tree-node__path">{{ data.path }}</span>
                                      </div>
                                      <button
                                        v-if="data.path_type === 'directory'"
                                        type="button"
                                        class="hfl-dir-tree-node__refresh"
                                        :class="{ 'is-refreshing': isCreateRecoveryDirectoryRefreshing(recoveryDirPlanPickerKey(group, dirPlan, 'dest'), data.path) }"
                                        :disabled="isCreateRecoveryDirectoryRefreshing(recoveryDirPlanPickerKey(group, dirPlan, 'dest'), data.path)"
                                        :aria-label="t('protection.backupsPage.ariaRefreshDirectory', { path: data.path })"
                                        :title="t('protection.backupsPage.ariaRefreshDirectory', { path: data.path })"
                                        @click.stop="refreshCreateRecoveryDestDirectory(group, dirPlan, data)"
                                      >
                                        <RefreshCw
                                          :size="14"
                                          :class="{ 'is-spinning': isCreateRecoveryDirectoryRefreshing(recoveryDirPlanPickerKey(group, dirPlan, 'dest'), data.path) }"
                                        />
                                      </button>
                                    </div>
                                  </template>
                                </el-tree>
                              </div>
                            </HflPopover>
                          </div>
                          <div class="create-recovery-dir-plan-cell create-recovery-dir-plan-cell--actions" :data-label="t('protection.backupsPage.colActions')">
                            <ElButton
                              text
                              circle
                              type="danger"
                              size="small"
                              class="create-recovery-dir-plan-remove-btn"
                              :disabled="group.plan.dirPlans.length <= 1"
                              :title="t('protection.backupsPage.ariaRemove')"
                              @click="removeRecoveryDirPlan(group, dirPlan.id)"
                            >
                              <Trash2 :size="14" />
                            </ElButton>
                          </div>
                        </div>
                        <div class="create-recovery-dir-plan-add-wrap">
                          <button
                            type="button"
                            class="create-recovery-dir-plan-add-row"
                            @click="addRecoveryDirPlan(group)"
                          >
                            <Plus :size="16" />
                            <span>{{ t('protection.backupsPage.btnAddRecoveryDir') }}</span>
                          </button>
                        </div>
                      </div>
                    </div>
                    <span v-else class="create-recovery-disabled-cell">
                      {{ t('protection.backupsPage.createRecoveryPlanDisabled') }}
                    </span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column :label="t('protection.backupsPage.colBackupSource')" min-width="200" fixed="left">
                <template #default="{ row: group }">
                  <button
                    type="button"
                    class="backup-source-cell backup-source-cell--summary backup-source-cell--clickable create-source-cell-trigger"
                    :aria-expanded="createRecoveryPlanExpandedKeys.includes(group.key)"
                    @click.stop="requestToggleCreateRecoveryPlanRow(group)"
                  >
                    <div class="backup-source-cell__body">
                      <span class="create-backup-source-name" :title="group.sourceName">{{ group.sourceName }}</span>
                      <span class="create-backup-source-meta-row">
                        <el-tag size="small" effect="plain" class="create-backup-source-type-tag">
                          {{ backupSourceTypeLabel(group.sourceType) }}
                        </el-tag>
                        <span v-if="group.platform" class="create-backup-source-platform">
                          <AgentPlatformBrandIcon :os="group.platform" />
                        </span>
                      </span>
                    </div>
                  </button>
                </template>
              </el-table-column>
              <el-table-column :label="t('protection.backupsPage.labelBackupDirs')" min-width="200">
                <template #default="{ row: group }">
                  <HflPopover trigger="hover" placement="top-start" :width="520">
                    <template #reference>
                      <div class="create-source-dir-preview">
                        <div class="create-source-dir-preview__count">
                          {{ t('protection.backupsPage.addedDirTotal', { n: group.entries.length }) }}
                        </div>
                        <div
                          v-for="entry in sourceDirPreviewEntries(group.entries)"
                          :key="entry.key"
                          class="create-source-dir-preview__item"
                        >
                          <component
                            :is="entry.pathType === 'file' ? FileIcon : Folder"
                            :size="14"
                            class="create-source-dir-preview__icon"
                            :class="entry.pathType === 'file' ? 'create-source-dir-preview__icon--file' : 'create-source-dir-preview__icon--folder'"
                          />
                          <span class="create-source-dir-preview__path hfl-table-cell-mono">{{ entry.path }}</span>
                        </div>
                        <div v-if="group.entries.length > 3" class="create-source-dir-preview__more">
                          {{ t('protection.backupsPage.moreDirs', { n: group.entries.length - 3 }) }}
                        </div>
                      </div>
                    </template>
                    <div class="create-table-info-popover create-table-info-popover--dirs create-table-info-popover--selected-paths">
                      <div class="create-table-info-popover__summary">{{ backupDirPopoverSummary(group.entries) }}</div>
                      <div
                        v-for="entry in group.entries"
                        :key="entry.key"
                        class="create-table-info-popover__dir-row create-dir-row create-dir-row--added"
                      >
                        <component
                          :is="entry.pathType === 'file' ? FileIcon : Folder"
                          :size="15"
                          class="create-dir-row__icon"
                          :class="entry.pathType === 'file' ? 'create-dir-row__icon--file' : 'create-dir-row__icon--folder'"
                        />
                        <div class="create-dir-row__body">
                          <span class="create-dir-row__path hfl-table-cell-mono">{{ entry.path }}</span>
                        </div>
                      </div>
                    </div>
                  </HflPopover>
                </template>
              </el-table-column>
              <el-table-column
                :label="t('protection.backupsPage.descRecoveryPlan')"
                min-width="300"
                class-name="hfl-table-no-tooltip"
              >
                <template #default="{ row: group }">
                  <HflPopover
                    v-if="group.plan.enabled"
                    trigger="hover"
                    placement="top-start"
                    :width="520"
                    popper-class="create-recovery-plan-tooltip"
                  >
                    <template #reference>
                      <div
                        class="create-recovery-plan-cell create-recovery-plan-cell--wrap"
                        :class="`create-recovery-plan-cell--${recoveryPlanStatusTone(group)}`"
                      >
                        <div class="create-recovery-plan-cell__status">
                          <span class="create-recovery-plan-cell__dot" aria-hidden="true" />
                          <span class="create-recovery-plan-cell__status-label">
                            {{ recoveryPlanStatusLabel(group) }}
                          </span>
                        </div>
                        <div
                          class="create-recovery-plan-cell__policy"
                          :class="[
                            `create-recovery-plan-cell__policy--${group.plan.conflictMode || 'pending'}`,
                            { 'create-recovery-plan-cell__policy--pending': !group.plan.conflictMode },
                          ]"
                        >
                          <ShieldAlert
                            v-if="group.plan.conflictMode === 'overwrite'"
                            :size="14"
                            class="create-recovery-plan-cell__policy-icon"
                          />
                          <ShieldCheck
                            v-else-if="group.plan.conflictMode === 'skip'"
                            :size="14"
                            class="create-recovery-plan-cell__policy-icon"
                          />
                          <Info v-else :size="14" class="create-recovery-plan-cell__policy-icon" />
                          <span class="create-recovery-plan-cell__policy-text">
                            {{ recoveryPlanConflictSummary(group) }}
                          </span>
                        </div>
                        <div v-if="recoveryPlanIncompleteReason(group)" class="create-recovery-plan-cell__issue">
                          {{ recoveryPlanIncompleteReason(group) }}
                        </div>
                        <div class="create-recovery-plan-cell__mappings">
                          <div
                            v-for="dirPlan in recoveryPlanPreviewDirPlans(group)"
                            :key="dirPlan.id"
                            class="create-recovery-plan-mapping"
                          >
                            <template v-if="isRecoveryDirPlanComplete(group, dirPlan)">
                              <span
                                class="create-recovery-plan-mapping__endpoint"
                                :class="`create-recovery-plan-mapping__endpoint--${recoveryDirPlanSourceIconName(dirPlan)}`"
                              >
                                <Camera
                                  v-if="recoveryDirPlanSourceIconName(dirPlan) === 'snapshot'"
                                  :size="14"
                                  class="create-recovery-plan-mapping__icon"
                                />
                                <FileIcon
                                  v-else-if="recoveryDirPlanSourceIconName(dirPlan) === 'file'"
                                  :size="14"
                                  class="create-recovery-plan-mapping__icon"
                                />
                                <Folder v-else :size="14" class="create-recovery-plan-mapping__icon" />
                                <span class="create-recovery-plan-mapping__text">{{ recoverySourcePathLabel(dirPlan.sourcePath) }}</span>
                              </span>
                              <span class="create-recovery-plan-mapping__arrow" aria-hidden="true">-&gt;</span>
                              <span
                                class="create-recovery-plan-mapping__endpoint create-recovery-plan-mapping__endpoint--target"
                              >
                                <FolderOpen :size="14" class="create-recovery-plan-mapping__icon create-dir-row__icon--folder" />
                                <span class="create-recovery-plan-mapping__text">{{ recoveryDirPlanTargetSummary(dirPlan) }}</span>
                              </span>
                            </template>
                            <span v-else class="create-recovery-plan-mapping__pending">
                              {{ recoveryDirPlanPendingLabel() }}
                            </span>
                          </div>
                          <div
                            v-if="recoveryPlanExtraCount(group) > 0"
                            class="create-recovery-plan-mapping create-recovery-plan-mapping--more"
                          >
                            {{ t('protection.backupsPage.recoveryPlanMorePaths', { n: recoveryPlanExtraCount(group) }) }}
                          </div>
                        </div>
                      </div>
                    </template>
                    <template #default>
                      <div class="create-recovery-plan-tooltip__content">
                        <div
                          class="create-recovery-plan-tooltip__policy"
                          :class="[
                            `create-recovery-plan-tooltip__policy--${group.plan.conflictMode || 'pending'}`,
                            { 'create-recovery-plan-tooltip__policy--pending': !group.plan.conflictMode },
                          ]"
                        >
                          <ShieldAlert
                            v-if="group.plan.conflictMode === 'overwrite'"
                            :size="14"
                            class="create-recovery-plan-cell__policy-icon"
                          />
                          <ShieldCheck
                            v-else-if="group.plan.conflictMode === 'skip'"
                            :size="14"
                            class="create-recovery-plan-cell__policy-icon"
                          />
                          <Info v-else :size="14" class="create-recovery-plan-cell__policy-icon" />
                          <span class="create-recovery-plan-cell__policy-text">
                            {{ recoveryPlanConflictLabel(group.plan.conflictMode) }}
                          </span>
                        </div>
                        <div v-if="recoveryPlanIncompleteReason(group)" class="create-recovery-plan-tooltip__issue">
                          {{ recoveryPlanIncompleteReason(group) }}
                        </div>
                        <div
                          v-for="dirPlan in group.plan.dirPlans"
                          :key="dirPlan.id"
                          class="create-recovery-plan-tooltip__mapping create-recovery-plan-mapping"
                        >
                          <template v-if="isRecoveryDirPlanComplete(group, dirPlan)">
                            <span
                              class="create-recovery-plan-mapping__endpoint"
                              :class="`create-recovery-plan-mapping__endpoint--${recoveryDirPlanSourceIconName(dirPlan)}`"
                            >
                              <Camera
                                v-if="recoveryDirPlanSourceIconName(dirPlan) === 'snapshot'"
                                :size="14"
                                class="create-recovery-plan-mapping__icon"
                              />
                              <FileIcon
                                v-else-if="recoveryDirPlanSourceIconName(dirPlan) === 'file'"
                                :size="14"
                                class="create-recovery-plan-mapping__icon"
                              />
                              <Folder v-else :size="14" class="create-recovery-plan-mapping__icon" />
                              <span class="create-recovery-plan-mapping__text">{{ recoverySourcePathLabel(dirPlan.sourcePath) }}</span>
                            </span>
                            <span class="create-recovery-plan-mapping__arrow" aria-hidden="true">-&gt;</span>
                            <span
                              class="create-recovery-plan-mapping__endpoint create-recovery-plan-mapping__endpoint--target"
                            >
                              <FolderOpen :size="14" class="create-recovery-plan-mapping__icon create-dir-row__icon--folder" />
                              <span class="create-recovery-plan-mapping__text">{{ recoveryDirPlanTargetSummary(dirPlan) }}</span>
                            </span>
                          </template>
                          <span v-else class="create-recovery-plan-mapping__pending">
                            {{ recoveryDirPlanPendingLabel() }}
                          </span>
                        </div>
                      </div>
                    </template>
                  </HflPopover>
                  <div v-else class="create-recovery-plan-cell create-recovery-plan-cell--disabled">
                    <div class="create-recovery-plan-cell__status">
                      <span class="create-recovery-plan-cell__dot" aria-hidden="true" />
                      <span class="create-recovery-plan-cell__status-label">
                        {{ recoveryPlanStatusLabel(group) }}
                      </span>
                    </div>
                    <div class="create-recovery-plan-cell__primary">
                      {{ recoveryPlanPrimaryPathSummary(group) }}
                    </div>
                  </div>
                </template>
              </el-table-column>
              <el-table-column :label="t('protection.backupsPage.createRecoveryPlanEnabled')" width="96" fixed="right" align="center">
                <template #default="{ row: group }">
                  <div class="create-recovery-plan-action">
                    <el-switch
                      :model-value="group.plan.enabled"
                      inline-prompt
                      :width="58"
                      class="create-recovery-plan-switch"
                      :active-text="t('protection.backupsPage.recoveryPlanSwitchOn')"
                      :inactive-text="t('protection.backupsPage.recoveryPlanSwitchOff')"
                      @update:model-value="(value) => onCreateRecoveryPlanEnabledChange(group, Boolean(value))"
                    />
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>

        <div v-if="createStep === 4" class="dp-wizard-pane">
          <p class="create-confirm-lead">{{ t('protection.backupsPage.createStep3Lead') }}</p>
          <section class="create-confirm-section create-confirm-section--first">
            <div class="create-confirm-list">
              <article
                v-for="row in createConfirmRows"
                :key="`confirm-${row.group.key}`"
                class="create-confirm-card"
              >
                <div class="create-confirm-card__main">
                  <div class="create-confirm-card__name">
                    <span class="create-confirm-card__index" :class="`is-${row.group.sourceType}`">
                      <component :is="backupSourceTypeIcon(row.group.sourceType)" :size="14" />
                      {{ String(row.index + 1).padStart(2, '0') }}
                    </span>
                    <div class="create-confirm-card__name-text">{{ row.name }}</div>
                  </div>
                  <dl class="create-confirm-card__meta">
                    <div class="create-confirm-card__meta-item create-confirm-card__meta-item--wide">
                      <dt>
                        <span class="create-confirm-meta-label">
                          <FolderTree :size="14" />
                          {{ t('protection.backupsPage.descSources') }}
                        </span>
                      </dt>
                      <dd>
                        <div class="create-confirm-source">
                          <div class="create-confirm-source__head">
                            <div class="create-confirm-source__identity">
                              <span class="create-confirm-source__name">{{ row.group.sourceName }}</span>
                              <span class="create-backup-source-meta-row">
                                <el-tag size="small" effect="plain" class="create-backup-source-type-tag">
                                  {{ backupSourceTypeLabel(row.group.sourceType) }}
                                </el-tag>
                                <span v-if="row.group.platform" class="create-backup-source-platform">
                                  <AgentPlatformBrandIcon :os="row.group.platform" />
                                </span>
                              </span>
                            </div>
                            <span class="create-confirm-source__count">
                              {{ t('protection.backupsPage.addedDirTotal', { n: row.entries.length }) }}
                            </span>
                          </div>
                          <div class="create-confirm-dir-box">
                            <ul class="create-confirm-dir-list">
                              <li v-for="entry in row.entries" :key="entry.key" class="create-confirm-dir-list__item">
                                <component
                                  :is="entry.pathType === 'file' ? FileIcon : Folder"
                                  :size="14"
                                  class="create-confirm-dir-list__icon"
                                  :class="entry.pathType === 'file' ? 'create-confirm-dir-list__icon--file' : 'create-confirm-dir-list__icon--folder'"
                                />
                                <span class="create-confirm-dir-list__path hfl-table-cell-mono">{{ entry.path }}</span>
                              </li>
                            </ul>
                          </div>
                        </div>
                      </dd>
                    </div>
                    <div class="create-confirm-card__meta-item">
                      <dt>
                        <span class="create-confirm-meta-label">
                          <Database :size="14" />
                          {{ t('protection.backupsPage.descTarget') }}
                        </span>
                      </dt>
                      <dd>
                        <div
                          class="wizard-target-repository-cell wizard-target-repository-cell--confirm"
                          :class="`wizard-target-repository-cell--${row.targetTone}`"
                        >
                          <span class="wizard-target-repository-cell__dot" aria-hidden="true"></span>
                          <div class="wizard-target-repository-cell__body">
                            <div class="wizard-target-repository-cell__name">{{ row.target }}</div>
                            <div v-if="row.targetLocation" class="wizard-target-repository-cell__location hfl-table-cell-mono">
                              {{ row.targetLocation }}
                            </div>
                            <div v-if="row.targetEndpoint" class="wizard-target-repository-cell__location hfl-table-cell-mono">
                              {{ row.targetEndpoint }}
                            </div>
                          </div>
                        </div>
                      </dd>
                    </div>
                    <div class="create-confirm-card__meta-item">
                      <dt>
                        <span class="create-confirm-meta-label">
                          <Archive :size="14" />
                          {{ t('protection.backupsPage.descCompression') }}
                        </span>
                      </dt>
                      <dd>{{ row.compression }}</dd>
                    </div>
                    <div class="create-confirm-card__meta-item">
                      <dt>
                        <span class="create-confirm-meta-label">
                          <ClipboardCheck :size="14" />
                          {{ t('protection.backupsPage.descPolicy') }}
                        </span>
                      </dt>
                      <dd>
                        <HflPopover
                          v-if="row.policyObject"
                          trigger="hover"
                          placement="top-start"
                          :width="420"
                          popper-class="create-policy-option-popper"
                        >
                          <template #reference>
                            <button type="button" class="create-confirm-hover-summary create-confirm-binding-summary">
                              <span class="create-confirm-binding-summary__name">{{ row.policyObject.name }}</span>
                              <el-tag v-bind="policyStateTagAttrs(row.policyObject.isActive)" size="small">
                                {{ policyStateLabel(row.policyObject.isActive) }}
                              </el-tag>
                            </button>
                          </template>
                          <div class="create-policy-detail-popover">
                            <div class="create-policy-detail-popover__head">
                              <div class="create-policy-detail-popover__title">{{ row.policyObject.name }}</div>
                              <el-tag v-bind="policyStateTagAttrs(row.policyObject.isActive)" size="small">
                                {{ policyStateLabel(row.policyObject.isActive) }}
                              </el-tag>
                            </div>
                            <div class="create-policy-detail-popover__sections">
                              <section
                                v-for="detailRow in policyDetailRows(row.policyObject)"
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
                                      v-for="line in policyRetentionDetailLines(row.policyObject)"
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
                        </HflPopover>
                        <span v-else class="create-confirm-binding-empty">
                          {{ t('protection.backupsPage.policyNoneOptional') }}
                        </span>
                      </dd>
                    </div>
                    <div class="create-confirm-card__meta-item">
                      <dt>
                        <span class="create-confirm-meta-label">
                          <Filter :size="14" />
                          {{ t('protection.backupsPage.descFileFilter') }}
                        </span>
                      </dt>
                      <dd>
                        <HflPopover
                          v-if="row.filterObjects.length"
                          trigger="hover"
                          placement="top-start"
                          :width="460"
                          popper-class="create-policy-option-popper"
                        >
                          <template #reference>
                            <button type="button" class="create-confirm-hover-summary create-confirm-binding-summary">
                              <span
                                v-for="filter in row.filterObjects"
                                :key="filter.id"
                                class="create-confirm-binding-summary__item"
                              >
                                <span class="create-confirm-binding-summary__name">{{ filter.name }}</span>
                                <el-tag v-bind="policyStateTagAttrs(filter.isActive)" size="small">
                                  {{ policyStateLabel(filter.isActive) }}
                                </el-tag>
                              </span>
                            </button>
                          </template>
                          <div class="create-confirm-binding-popover-stack">
                            <div
                              v-for="filter in row.filterObjects"
                              :key="filter.id"
                              class="create-policy-detail-popover"
                            >
                              <div class="create-policy-detail-popover__head">
                                <div class="create-policy-detail-popover__title">{{ filter.name }}</div>
                                <el-tag v-bind="policyStateTagAttrs(filter.isActive)" size="small">
                                  {{ policyStateLabel(filter.isActive) }}
                                </el-tag>
                              </div>
                              <div class="create-policy-detail-popover__sections">
                                <section
                                  v-for="detailRow in filterHoverRows(filter)"
                                  :key="detailRow.label"
                                  class="create-policy-detail-popover__section create-policy-detail-popover__section--filter-line"
                                >
                                  <span class="create-policy-detail-popover__section-title">{{ detailRow.label }}:</span>
                                  <el-tag
                                    v-if="detailRow.enabled"
                                    size="small"
                                    v-bind="policyStateTagAttrs(true)"
                                  >
                                    {{ detailRow.value }}
                                  </el-tag>
                                  <span v-else class="create-policy-detail-popover__value">{{ detailRow.value }}</span>
                                </section>
                                <section class="create-policy-detail-popover__section">
                                  <div class="create-policy-detail-popover__section-title">
                                    {{ t('protection.policiesPage.filterRulesPreviewTitle') }}:
                                  </div>
                                  <div class="create-filter-rules-preview">
                                    <template v-if="filterCompiledRuleLines(filter).length">
                                      <code
                                        v-for="(line, index) in filterCompiledRuleLines(filter)"
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
                        <span v-else class="create-confirm-binding-empty">
                          {{ row.fileFilter }}
                        </span>
                      </dd>
                    </div>
                    <div v-if="row.nasPlan" class="create-confirm-card__meta-item">
                      <dt>
                        <span class="create-confirm-meta-label">
                          <component :is="targetNasSidebarIcon" :size="14" />
                          {{ t('protection.backupsPage.descNasPlan') }}
                        </span>
                      </dt>
                      <dd>
                        <div class="create-confirm-detail-stack">
                          <div>{{ row.nasPlan }}</div>
                          <div class="create-confirm-detail-line">{{ row.nasPlanDesc }}</div>
                        </div>
                      </dd>
                    </div>
                    <div class="create-confirm-card__meta-item create-confirm-card__meta-item--wide">
                      <dt>
                        <span class="create-confirm-meta-label">
                          <ShieldCheck :size="14" />
                          {{ t('protection.backupsPage.descRecoveryPlan') }}
                        </span>
                      </dt>
                      <dd>
                        <div
                          v-if="row.recoveryGroup.plan.enabled"
                          class="create-recovery-plan-cell create-recovery-plan-cell--review"
                          :class="`create-recovery-plan-cell--${recoveryPlanStatusTone(row.recoveryGroup)}`"
                        >
                          <div class="create-recovery-plan-cell__status">
                            <span class="create-recovery-plan-cell__dot" aria-hidden="true" />
                            <span class="create-recovery-plan-cell__status-label">
                              {{ recoveryPlanStatusLabel(row.recoveryGroup) }}
                            </span>
                          </div>
                          <div
                            class="create-recovery-plan-cell__policy"
                            :class="[
                              `create-recovery-plan-cell__policy--${row.recoveryGroup.plan.conflictMode || 'pending'}`,
                              { 'create-recovery-plan-cell__policy--pending': !row.recoveryGroup.plan.conflictMode },
                            ]"
                          >
                            <ShieldAlert
                              v-if="row.recoveryGroup.plan.conflictMode === 'overwrite'"
                              :size="14"
                              class="create-recovery-plan-cell__policy-icon"
                            />
                            <ShieldCheck
                              v-else-if="row.recoveryGroup.plan.conflictMode === 'skip'"
                              :size="14"
                              class="create-recovery-plan-cell__policy-icon"
                            />
                            <Info v-else :size="14" class="create-recovery-plan-cell__policy-icon" />
                            <span class="create-recovery-plan-cell__policy-text">
                              {{ recoveryPlanConflictLabel(row.recoveryGroup.plan.conflictMode) }}
                            </span>
                          </div>
                          <div v-if="recoveryPlanIncompleteReason(row.recoveryGroup)" class="create-recovery-plan-cell__issue">
                            {{ recoveryPlanIncompleteReason(row.recoveryGroup) }}
                          </div>
                          <div class="create-recovery-plan-cell__mappings">
                            <div
                              v-for="dirPlan in recoveryPlanPreviewDirPlans(row.recoveryGroup)"
                              :key="dirPlan.id"
                              class="create-recovery-plan-mapping"
                            >
                              <template v-if="isRecoveryDirPlanComplete(row.recoveryGroup, dirPlan)">
                                <span
                                  class="create-recovery-plan-mapping__endpoint"
                                  :class="`create-recovery-plan-mapping__endpoint--${recoveryDirPlanSourceIconName(dirPlan)}`"
                                  :title="recoverySourcePathLabel(dirPlan.sourcePath)"
                                >
                                  <Camera
                                    v-if="recoveryDirPlanSourceIconName(dirPlan) === 'snapshot'"
                                    :size="14"
                                    class="create-recovery-plan-mapping__icon"
                                  />
                                  <FileIcon
                                    v-else-if="recoveryDirPlanSourceIconName(dirPlan) === 'file'"
                                    :size="14"
                                    class="create-recovery-plan-mapping__icon"
                                  />
                                  <Folder v-else :size="14" class="create-recovery-plan-mapping__icon" />
                                  <span class="create-recovery-plan-mapping__text" :title="recoverySourcePathLabel(dirPlan.sourcePath)">{{ recoverySourcePathLabel(dirPlan.sourcePath) }}</span>
                                </span>
                                <span class="create-recovery-plan-mapping__arrow" aria-hidden="true">-&gt;</span>
                                <span
                                  class="create-recovery-plan-mapping__endpoint create-recovery-plan-mapping__endpoint--target"
                                  :title="recoveryDirPlanTargetSummary(dirPlan)"
                                >
                                  <FolderOpen :size="14" class="create-recovery-plan-mapping__icon create-dir-row__icon--folder" />
                                  <span class="create-recovery-plan-mapping__text" :title="recoveryDirPlanTargetSummary(dirPlan)">{{ recoveryDirPlanTargetSummary(dirPlan) }}</span>
                                </span>
                              </template>
                              <span v-else class="create-recovery-plan-mapping__pending">
                                {{ recoveryDirPlanPendingLabel() }}
                              </span>
                            </div>
                            <div
                              v-if="recoveryPlanExtraCount(row.recoveryGroup) > 0"
                              class="create-recovery-plan-mapping create-recovery-plan-mapping--more"
                            >
                              {{ t('protection.backupsPage.recoveryPlanMorePaths', { n: recoveryPlanExtraCount(row.recoveryGroup) }) }}
                            </div>
                          </div>
                        </div>
                        <div v-else class="create-recovery-plan-cell create-recovery-plan-cell--disabled">
                          <div class="create-recovery-plan-cell__status">
                            <span class="create-recovery-plan-cell__dot" aria-hidden="true" />
                            <span class="create-recovery-plan-cell__status-label">
                              {{ recoveryPlanStatusLabel(row.recoveryGroup) }}
                            </span>
                          </div>
                          <div class="create-recovery-plan-cell__primary">
                            {{ recoveryPlanPrimaryPathSummary(row.recoveryGroup) }}
                          </div>
                        </div>
                      </dd>
                    </div>
                  </dl>
                </div>
              </article>
            </div>
          </section>

        </div>

      </template>
    </BackupConfigCreateWizard>

    <el-dialog
      v-model="addTargetOpen"
      :title="t('protection.backupsPage.addTargetDialogTitle')"
      width="1240px"
      destroy-on-close
      class="dp-add-target-dialog repository-add-dialog dp-dialog-soft-bg"
      @closed="resetAddTargetForm()"
    >
      <div class="repository-add-page">
        <section class="add-source-switcher repository-add-switcher">
          <div class="add-source-type-grid repository-add-type-grid">
            <button
              v-for="item in addTargetTypeOptions"
              :key="item.value"
              type="button"
              class="add-source-type-card"
              :class="{ 'is-active': addTargetKind === item.value }"
              :aria-pressed="addTargetKind === item.value"
              @click="onAddTargetKindChange(item.value)"
            >
              <span class="add-source-type-card__indicator" aria-hidden="true" />
              <span class="add-source-type-card__inner">
                <span class="add-source-type-card__text">
                  <span class="add-source-type-card__name">{{ item.label }}</span>
                  <span class="add-source-type-card__sub">{{ item.desc }}</span>
                </span>
              </span>
            </button>
          </div>
        </section>

        <div class="repository-add-content">
          <div class="repository-add-layout repository-add-layout--embedded">
            <div class="repository-add-main repository-add-main--embedded">
              <AddS3Repo
                v-if="addTargetKind === 's3'"
                embedded
                @cancel="closeAddTargetDialog"
                @created="onEmbeddedTargetCreated"
              />
              <AddNasRepository
                v-else-if="addTargetKind === 'nas'"
                embedded
                @cancel="closeAddTargetDialog"
                @created="onEmbeddedTargetCreated"
              />
              <AddProxyFsRepository
                v-else
                embedded
                @cancel="closeAddTargetDialog"
                @created="onEmbeddedTargetCreated"
              />

              <div v-show="false" aria-hidden="true">
              <template v-if="addTargetKind === 's3'">
                <div class="repository-add-steps">
                  <div
                    class="repository-add-step"
                    :class="{
                      'repository-add-step--active': addTargetS3Step === 0,
                      'repository-add-step--done': addTargetS3Step > 0,
                    }"
                  >
                    <span class="repository-add-step__num">{{ addTargetS3Step > 0 ? '✓' : '01' }}</span>
                    <span class="repository-add-step__label">{{ t('addS3Repo.stepAuth') }}</span>
                  </div>
                  <div class="repository-add-step__connector" :class="{ 'is-on': addTargetS3Step > 0 }" />
                  <div
                    class="repository-add-step"
                    :class="{ 'repository-add-step--active': addTargetS3Step === 1 }"
                  >
                    <span class="repository-add-step__num">02</span>
                    <span class="repository-add-step__label">{{ t('addS3Repo.stepRepo') }}</span>
                  </div>
                </div>

                <template v-if="addTargetS3Step === 0">
                <section class="repository-add-card">
                  <div class="repository-add-section">
                    <h4 class="repository-add-section__title">
                      <span class="repository-add-section__indicator" />
                      {{ t('addS3Repo.fieldPlatform') }}
                    </h4>
                    <div class="repository-platform-grid">
                      <button
                        v-for="platform in addTargetS3PlatformOptions"
                        :key="platform.value"
                        type="button"
                        class="repository-platform-btn"
                        :class="{ 'is-active': addTargetS3Platform === platform.value }"
                        @click="onAddTargetS3PlatformChange(platform.value)"
                      >
                        {{ platform.label }}
                      </button>
                    </div>

                    <template v-if="addTargetS3Platform && addTargetS3Platform !== 'other'">
                      <h5 class="repository-add-section__subtitle">{{ t('repositoriesPage.fieldRegion') }}</h5>
                      <div class="repository-region-grid">
                        <button
                          v-for="region in addTargetS3Regions"
                          :key="region.key"
                          type="button"
                          class="repository-region-btn"
                          :class="{ 'is-active': addTargetS3RegionPreset === region.key }"
                          @click="applyAddTargetS3RegionPreset(region.key)"
                        >
                          <span class="repository-region-btn__label">{{ region.label }}</span>
                          <span class="repository-region-btn__code">{{ region.region }}</span>
                        </button>
                      </div>
                    </template>
                  </div>
                </section>

                <section class="repository-add-card">
                  <div class="repository-add-section">
                    <h4 class="repository-add-section__title">
                      <span class="repository-add-section__indicator" />
                      {{ t('addS3Repo.connectionTitle') }}
                    </h4>
                    <div class="repository-add-warning">
                      <Info :size="18" />
                      <span>{{ t('addS3Repo.s3WarningHint') }}</span>
                    </div>
                    <ElForm label-position="top" class="repository-add-form repository-add-form--grid">
                      <ElFormItem :label="t('addS3Repo.fieldEndpoint')" required>
                        <ElInput v-model="addTargetS3Endpoint" :placeholder="t('addS3Repo.phEndpoint')" />
                      </ElFormItem>
                      <ElFormItem :label="t('repositoriesPage.fieldRegion')">
                        <ElInput v-model="addTargetS3Region" :placeholder="t('repositoriesPage.phRegion')" />
                      </ElFormItem>
                      <ElFormItem :label="t('repositoriesPage.fieldAccessKey')" required>
                        <ElInput v-model="addTargetS3AccessKey" :placeholder="t('repositoriesPage.phAccessKey')" />
                      </ElFormItem>
                      <ElFormItem :label="t('repositoriesPage.fieldSecretKey')" required>
                        <ElInput v-model="addTargetS3SecretKey" type="password" show-password :placeholder="t('repositoriesPage.phSecretKey')" />
                      </ElFormItem>
                      <ElFormItem :label="t('repositoriesPage.fieldS3UrlStyle')">
                        <ElSelect v-model="addTargetS3UrlStyle" class="w-full">
                          <ElOption :label="t('repositoriesPage.s3UrlStyleAuto')" value="auto" />
                          <ElOption :label="t('repositoriesPage.s3UrlStyleVirtualHosted')" value="virtual_hosted" />
                          <ElOption :label="t('repositoriesPage.s3UrlStylePath')" value="path" />
                        </ElSelect>
                      </ElFormItem>
                      <ElFormItem :label="t('repositoriesPage.fieldUseTls')">
                        <ElSwitch
                          v-model="addTargetS3UseTls"
                          :active-text="t('repositoriesPage.tlsOnHint')"
                          :inactive-text="t('repositoriesPage.tlsOffHint')"
                        />
                      </ElFormItem>
                    </ElForm>
                  </div>
                </section>
                </template>

                <section v-else class="repository-add-card">
                  <div class="repository-add-section">
                    <h4 class="repository-add-section__title">
                      <span class="repository-add-section__indicator" />
                      {{ t('repositoriesPage.stepRepo') }}
                    </h4>
                    <ElForm label-position="top" class="repository-add-form">
                      <ElFormItem :label="t('repositoriesPage.fieldRepoName')" required>
                        <ElInput v-model="addTargetRepoName" :placeholder="t('repositoriesPage.phRepoName')" />
                      </ElFormItem>
                      <ElFormItem :label="t('repositoriesPage.fieldBucket')" required>
                        <div class="repository-segmented">
                          <button
                            type="button"
                            class="repository-segmented__btn"
                            :class="{ 'is-active': addTargetS3BucketMode === 'existing' }"
                            @click="addTargetS3BucketMode = 'existing'"
                          >
                            {{ t('repositoriesPage.fieldBucketExisting') }}
                          </button>
                          <button
                            type="button"
                            class="repository-segmented__btn"
                            :class="{ 'is-active': addTargetS3BucketMode === 'new' }"
                            @click="addTargetS3BucketMode = 'new'"
                          >
                            {{ t('repositoriesPage.fieldBucketNew') }}
                          </button>
                        </div>
                        <ElInput
                          v-model="addTargetS3Bucket"
                          :placeholder="addTargetS3BucketMode === 'existing' ? t('repositoriesPage.phBucketSelect') : t('repositoriesPage.phBucketNew')"
                        />
                      </ElFormItem>
                      <ElFormItem :label="t('repositoriesPage.fieldPrefix')">
                        <ElInput v-model="addTargetS3Prefix" :placeholder="t('repositoriesPage.phPrefix')" />
                        <div class="mt-1 text-xs text-[var(--color-text-tertiary)]">{{ t('repositoriesPage.hintPrefix') }}</div>
                      </ElFormItem>
                    </ElForm>
                  </div>
                </section>
              </template>

              <template v-else-if="addTargetKind === 'nas'">
                <div class="add-nas-steps add-nas-steps--panel">
                  <div
                    class="add-nas-steps__item"
                    :class="{
                      'add-nas-steps__item--active': addTargetNasStep === 0,
                      'add-nas-steps__item--done': addTargetNasStep > 0,
                    }"
                  >
                    <span class="add-nas-steps__num">{{ addTargetNasStep > 0 ? '✓' : '01' }}</span>
                    <span class="add-nas-steps__label">{{ t('addNasRepo.stepAuthNas') }}</span>
                  </div>
                  <div class="add-nas-steps__connector" :class="{ 'add-nas-steps__connector--on': addTargetNasStep >= 1 }" />
                  <div
                    class="add-nas-steps__item"
                    :class="{
                      'add-nas-steps__item--active': addTargetNasStep === 1,
                      'add-nas-steps__item--done': addTargetNasStep > 1,
                    }"
                  >
                    <span class="add-nas-steps__num">{{ addTargetNasStep > 1 ? '✓' : '02' }}</span>
                    <span class="add-nas-steps__label">{{ t('addNasRepo.stepBindProxy') }}</span>
                  </div>
                  <div class="add-nas-steps__connector" :class="{ 'add-nas-steps__connector--on': addTargetNasStep >= 2 }" />
                  <div class="add-nas-steps__item" :class="{ 'add-nas-steps__item--active': addTargetNasStep === 2 }">
                    <span class="add-nas-steps__num">03</span>
                    <span class="add-nas-steps__label">{{ t('repositoriesPage.stepRepo') }}</span>
                  </div>
                </div>

                <section v-show="addTargetNasStep === 0" class="add-nas-card">
                  <div class="add-nas-section">
                    <h4 class="add-nas-section__title">
                      <span class="add-nas-section__indicator" />
                      {{ t('repositoriesPage.fieldProtocol') }}
                    </h4>
                    <ElRadioGroup v-model="addTargetNasProtocol" class="add-nas-protocol-grid">
                      <ElRadio value="smb" border class="add-nas-protocol-card !mr-0">
                        <div class="add-nas-protocol-card__inner">
                          <component :is="nasMountProtocolIcon('smb')" :size="24" />
                          <div class="add-nas-protocol-card__text">
                            <div class="font-semibold">{{ t('repositoriesPage.protocolSmb') }}</div>
                          </div>
                        </div>
                      </ElRadio>
                      <ElRadio value="nfs" border class="add-nas-protocol-card !mr-0">
                        <div class="add-nas-protocol-card__inner">
                          <component :is="nasMountProtocolIcon('nfs')" :size="24" />
                          <div class="add-nas-protocol-card__text">
                            <div class="font-semibold">{{ t('repositoriesPage.protocolNfs') }}</div>
                          </div>
                        </div>
                      </ElRadio>
                    </ElRadioGroup>

                    <h5 class="add-nas-section__subtitle">
                      <FolderOpen :size="16" />
                      {{ t('addNasRepo.titleNasInfo') }}
                    </h5>
                    <ElForm label-position="top" class="add-nas-form">
                      <div class="add-nas-form-row add-nas-form-row--responsive">
                        <ElFormItem
                          :label="addTargetNasProtocol === 'smb' ? t('addNasRepo.fieldSmbHost') : t('addNasRepo.fieldNfsHost')"
                          required
                          class="flex-1"
                        >
                          <ElInput v-model="addTargetNasHost" :placeholder="addTargetNasProtocol === 'smb' ? t('addNasRepo.phSmbHost') : t('addNasRepo.phNfsHost')" />
                        </ElFormItem>
                        <ElFormItem v-if="addTargetNasProtocol === 'smb'" :label="t('addNasRepo.fieldSmbShare')" required class="flex-1">
                          <ElInput v-model="addTargetNasShare" :placeholder="t('addNasRepo.phSmbShare')" />
                        </ElFormItem>
                        <ElFormItem v-else :label="t('addNasRepo.fieldNfsExport')" required class="flex-1">
                          <ElInput v-model="addTargetNasExport" :placeholder="t('addNasRepo.phNfsExport')" />
                        </ElFormItem>
                      </div>
                      <div v-if="addTargetNasProtocol === 'smb'" class="add-nas-form-row add-nas-form-row--responsive">
                        <ElFormItem :label="t('repositoriesPage.fieldSmbUsername')" required class="flex-1">
                          <ElInput v-model="addTargetNasUsername" :placeholder="t('repositoriesPage.phSmbUsername')" />
                        </ElFormItem>
                        <ElFormItem :label="t('repositoriesPage.fieldSmbPassword')" required class="flex-1">
                          <ElInput v-model="addTargetNasPassword" type="password" show-password :placeholder="t('repositoriesPage.phSmbPassword')" />
                        </ElFormItem>
                      </div>
                      <ElFormItem :label="t('addNasRepo.fieldMountOptions')">
                        <ElInput
                          v-model="addTargetNasMountOptions"
                          :placeholder="addTargetNasProtocol === 'smb' ? t('addNasRepo.phMountOptionsSmb') : t('addNasRepo.phMountOptionsNfs')"
                        />
                        <div class="mt-1 text-xs text-[rgb(100_116_139)]">
                          {{ addTargetNasProtocol === 'smb' ? t('addNasRepo.hintMountOptionsSmb') : t('addNasRepo.hintMountOptionsNfs') }}
                        </div>
                      </ElFormItem>
                    </ElForm>
                  </div>
                </section>

                <section v-show="addTargetNasStep === 1" class="add-nas-card">
                  <div class="add-nas-section">
                    <div class="add-nas-section__head">
                      <div class="add-nas-section__title-wrap">
                        <h4 class="add-nas-section__title !mb-0">
                          <span class="add-nas-section__indicator" />
                          {{ t('addNasRepo.titleBindProxy') }}
                        </h4>
                        <span class="add-nas-optional-badge">{{ t('addNasRepo.optional') }}</span>
                      </div>
                    </div>

                    <ElAlert type="warning" :closable="false" show-icon class="add-nas-proxy-alert">
                      <ol class="add-nas-proxy-alert__list">
                        <li v-for="item in bindProxyLeadItems" :key="item">{{ item }}</li>
                      </ol>
                    </ElAlert>

                    <div class="add-nas-proxy-layout">
                      <div class="add-nas-proxy-form">
                        <ElForm label-position="top" class="add-nas-form">
                          <ElFormItem class="add-nas-bind-form-item">
                            <template #label>
                              <span class="add-nas-field-label-with-action">
                                <span>{{ t('addNasRepo.fieldSourceProxyNode') }}</span>
                                <ElButton
                                  text
                                  circle
                                  class="hfl-refresh-button add-nas-field-label-with-action__btn"
                                  :title="t('addNasRepo.proxyRefresh')"
                                  :aria-label="t('addNasRepo.proxyRefresh')"
                                  :disabled="proxyNodesRefreshing"
                                  @click.stop="refreshProxyNodesManually"
                                >
                                  <RefreshCw :size="15" stroke-width="2" :class="{ 'is-spinning': proxyNodesRefreshing }" />
                                </ElButton>
                              </span>
                            </template>
                            <div class="add-nas-select-row">
                              <ElButton text circle class="add-nas-select-row__search" aria-hidden="true" tabindex="-1">
                                <Search :size="15" stroke-width="2" />
                              </ElButton>
                              <ElSelect
                                v-model="addTargetNasProxyNodeId"
                                class="add-nas-select-row__select"
                                :placeholder="t('addNasRepo.phSourceProxyNode')"
                                filterable
                                clearable
                              >
                                <ElOption :value="0" :label="t('addNasRepo.optionNoProxy')" />
                                <ElOption
                                  v-for="node in addTargetProxyNodeOptions"
                                  :key="node.id"
                                  :value="node.id"
                                  :label="node.name"
                                />
                              </ElSelect>
                            </div>
                            <div class="mt-2 text-xs text-[var(--color-text-tertiary)]">
                              {{ addTargetNasProxyNodeId ? t('addNasRepo.hintProxySelected') : t('addNasRepo.hintProxySkipped') }}
                            </div>
                          </ElFormItem>
                        </ElForm>

                        <div class="add-nas-proxy-benefits">
                          <div class="add-nas-proxy-benefit">
                            <span class="add-nas-proxy-benefit__dot" />
                            {{ t('addNasRepo.proxyBenefitAvoidMountFailure') }}
                          </div>
                          <div class="add-nas-proxy-benefit">
                            <span class="add-nas-proxy-benefit__dot" />
                            {{ t('addNasRepo.proxyBenefitSharedRepo') }}
                          </div>
                        </div>
                      </div>

                      <div class="add-nas-path-card" :class="{ 'add-nas-path-card--direct': isAddTargetNasDirectAccess }" aria-hidden="true">
                        <template v-if="!isAddTargetNasDirectAccess">
                          <div class="add-nas-path-card__agents">
                            <span>{{ t('addNasRepo.demoBackupSourceA') }}</span>
                            <span>{{ t('addNasRepo.demoBackupSourceB') }}</span>
                            <span>{{ t('addNasRepo.demoBackupSourceC') }}</span>
                          </div>
                          <div class="add-nas-path-card__join" />
                          <div class="add-nas-path-card__node add-nas-path-card__node--proxy">Proxy</div>
                          <div class="add-nas-path-card__line" />
                          <div class="add-nas-path-card__node add-nas-path-card__node--nas">NAS</div>
                        </template>
                        <template v-else>
                          <div class="add-nas-path-card__direct-rows">
                            <div class="add-nas-path-card__direct-row">
                              <span class="add-nas-path-card__source">{{ t('addNasRepo.demoBackupSourceA') }}</span>
                              <span class="add-nas-path-card__direct-line" />
                              <span class="add-nas-path-card__node add-nas-path-card__node--nas">NAS</span>
                            </div>
                            <div class="add-nas-path-card__direct-row">
                              <span class="add-nas-path-card__source">{{ t('addNasRepo.demoBackupSourceB') }}</span>
                              <span class="add-nas-path-card__direct-line" />
                              <span class="add-nas-path-card__node add-nas-path-card__node--nas">NAS</span>
                            </div>
                            <div class="add-nas-path-card__direct-row">
                              <span class="add-nas-path-card__source">{{ t('addNasRepo.demoBackupSourceC') }}</span>
                              <span class="add-nas-path-card__direct-line" />
                              <span class="add-nas-path-card__node add-nas-path-card__node--nas">NAS</span>
                            </div>
                          </div>
                        </template>
                      </div>
                    </div>

                    <ElButton class="add-nas-deploy-btn" type="primary" plain @click="openProxyDeploy">
                      <Plus :size="14" class="inline" />
                      {{ t('addNasRepo.deployProxy') }}
                    </ElButton>
                  </div>
                </section>

                <section v-show="addTargetNasStep === 2" class="add-nas-card">
                  <div class="add-nas-section">
                    <h4 class="add-nas-section__title">
                      <span class="add-nas-section__indicator" />
                      {{ t('repositoriesPage.stepRepo') }}
                    </h4>
                    <ElForm label-position="top" class="add-nas-form">
                      <ElFormItem :label="t('repositoriesPage.fieldRepoName')" required>
                        <ElInput v-model="addTargetRepoName" :placeholder="t('repositoriesPage.phRepoName')" />
                      </ElFormItem>
                    </ElForm>
                  </div>
                </section>
              </template>

              <template v-else>
                <section class="repository-add-card">
                  <div class="repository-add-section">
                    <h4 class="repository-add-section__title">
                      <span class="repository-add-section__indicator" />
                      {{ t('repositoriesPage.addProxyFsPage') }}
                    </h4>
                    <ElForm label-position="top" class="repository-add-form">
                      <ElFormItem :label="t('repositoriesPage.fieldRepoName')" required>
                        <ElInput v-model="addTargetRepoName" :placeholder="t('repositoriesPage.phRepoName')" />
                        <div class="mt-1 text-xs text-[var(--color-text-tertiary)]">{{ t('repositoriesPage.hintRepoNameAuto') }}</div>
                      </ElFormItem>
                      <ElFormItem :label="t('repositoriesPage.fieldProxyNode')" required>
                        <ElSelect
                          v-model="addTargetProxyNodeId"
                          class="w-full"
                          filterable
                          :placeholder="t('repositoriesPage.phProxyNode')"
                        >
                          <ElOption
                            v-for="node in addTargetProxyNodeOptions"
                            :key="node.id"
                            :label="node.name"
                            :value="node.id"
                          />
                        </ElSelect>
                      </ElFormItem>
                      <ElFormItem :label="t('repositoriesPage.fieldProxyNodeDir')" required>
                        <div class="repository-dir-selector">
                          <div class="repository-segmented">
                            <button
                              type="button"
                              class="repository-segmented__btn"
                              :class="{ 'is-active': addTargetProxyUseTree }"
                              @click="addTargetProxyUseTree = true"
                            >
                              <FolderTree :size="14" />
                              {{ t('repositoriesPage.dirSelectTree') }}
                            </button>
                            <button
                              type="button"
                              class="repository-segmented__btn"
                              :class="{ 'is-active': !addTargetProxyUseTree }"
                              @click="addTargetProxyUseTree = false"
                            >
                              <HardDrive :size="14" />
                              {{ t('repositoriesPage.dirSelectInput') }}
                            </button>
                          </div>
                          <div v-if="addTargetProxyUseTree" class="repository-dir-tree-shell hfl-dir-tree-shell">
                            <div v-if="!addTargetProxyNodeId" class="repository-dir-tree-shell__empty hfl-dir-tree-empty">
                              {{ t('repositoriesPage.errProxyNode') }}
                            </div>
                            <ElTree
                              v-else
                              ref="addTargetProxyDirTreeRef"
                              class="repository-dir-tree hfl-dir-tree"
                              :data="addTargetProxyTreeData"
                              :props="{ children: 'children', label: 'label' }"
                              node-key="id"
                              show-checkbox
                              check-strictly
                              default-expand-all
                              :check-on-click-node="true"
                              :expand-on-click-node="false"
                              :highlight-current="true"
                              :current-node-key="addTargetProxyCurrentTreeNodeKey"
                              :default-checked-keys="addTargetProxyCheckedTreeKeys"
                              @current-change="onAddTargetProxyTreeSelect"
                              @check-change="onAddTargetProxyTreeCheck"
                            >
                              <template #default="{ data }">
                                <div class="repository-tree-node hfl-dir-tree-node">
                                  <FolderOpen :size="15" class="repository-tree-node__icon hfl-dir-tree-node__icon" />
                                  <div class="repository-tree-node__text hfl-dir-tree-node__text">
                                    <span class="repository-tree-node__label hfl-dir-tree-node__label">{{ data.label }}</span>
                                    <span class="repository-tree-node__path hfl-dir-tree-node__path">{{ data.pathLabel }}</span>
                                  </div>
                                </div>
                              </template>
                            </ElTree>
                            <div class="mt-2 text-xs text-[var(--color-text-tertiary)]">{{ t('repositoriesPage.hintTreeSelect') }}</div>
                          </div>
                          <ElInput v-else v-model="addTargetProxyDir" :placeholder="t('repositoriesPage.phProxyNodeDir')" />
                        </div>
                      </ElFormItem>
                    </ElForm>
                  </div>
                </section>
              </template>

              <section
                v-if="
                  addTargetKind === 'proxy_fs' ||
                  (addTargetKind === 's3' && addTargetS3Step === 1) ||
                  (addTargetKind === 'nas' && addTargetNasStep === 2)
                "
                class="repository-add-card repository-add-card--quota"
              >
                <div class="repository-add-section">
                  <h4 class="repository-add-section__title">
                    <span class="repository-add-section__indicator" />
                    {{ t('repositoriesPage.fieldQuota') }}
                  </h4>
                  <ElForm label-position="top" class="repository-add-form repository-add-form--quota">
                    <ElFormItem :label="t('repositoriesPage.fieldQuota')" class="repository-add-form__grow">
                      <ElInput v-model.number="addTargetQuota" type="number" min="0" :placeholder="t('repositoriesPage.phQuota')" />
                      <div class="mt-1 text-xs text-[var(--color-text-tertiary)]">{{ t('repositoriesPage.hintQuota') }}</div>
                    </ElFormItem>
                    <div class="repository-quota-panel">
                      <ElCheckbox v-model="addTargetEnableQuotaAlert">{{ t('repositoriesPage.fieldQuotaAlert') }}</ElCheckbox>
                      <div class="repository-quota-panel__threshold">
                        <span>{{ t('repositoriesPage.fieldQuotaAlertThreshold') }}</span>
                        <ElInput
                          v-model.number="addTargetQuotaAlertThreshold"
                          type="number"
                          min="1"
                          max="100"
                          class="repository-quota-panel__input"
                        />
                        <span>%</span>
                      </div>
                    </div>
                  </ElForm>
                </div>
              </section>
              </div>
            </div>

            <aside v-show="false" class="repository-add-preview">
              <div class="repository-add-preview__head">
                <div class="repository-add-preview__icon">
                  <Cloud v-if="addTargetKind === 's3'" :size="26" />
                  <component :is="targetNasSidebarIcon" v-else-if="addTargetKind === 'nas'" :size="26" />
                  <FolderTree v-else :size="26" />
                </div>
                <div class="repository-add-preview__title-wrap">
                  <h4 class="repository-add-preview__title">{{ addTargetRepoName || t('addS3Repo.previewUnnamed') }}</h4>
                  <p class="repository-add-preview__type">
                    <template v-if="addTargetKind === 's3'">{{ addTargetS3PlatformLabel || t('addS3Repo.previewSelectPlatform') }}</template>
                    <template v-else-if="addTargetKind === 'nas'">{{ addTargetNasProtocolLabel }}</template>
                    <template v-else>{{ t('repositoriesPage.kindProxyFs') }}</template>
                  </p>
                </div>
              </div>

              <div class="repository-add-preview__body">
                <section class="repository-add-preview__section">
                  <h5 class="repository-add-preview__section-title">{{ t('addS3Repo.previewBasicInfo') }}</h5>
                  <div class="repository-add-preview__row">
                    <span>{{ addTargetKind === 's3' ? t('repositoriesPage.fieldBucket') : t('repositoriesPage.fieldRepoName') }}</span>
                    <strong>{{ addTargetKind === 's3' ? (addTargetS3Bucket || '—') : (addTargetRepoName || '—') }}</strong>
                  </div>
                  <div class="repository-add-preview__row">
                    <span>{{ t('repositoriesPage.fieldQuota') }}</span>
                    <strong>{{ addTargetQuota > 0 ? `${addTargetQuota} GB` : t('addS3Repo.previewUnlimited') }}</strong>
                  </div>
                  <div class="repository-add-preview__row">
                    <span>{{ t('repositoriesPage.fieldQuotaAlert') }}</span>
                    <strong class="repository-add-preview__badge" :class="{ 'is-on': addTargetEnableQuotaAlert }">
                      {{ addTargetEnableQuotaAlert ? `${t('repositoriesPage.enabled')} (${addTargetQuotaAlertThreshold}%)` : t('repositoriesPage.disabled') }}
                    </strong>
                  </div>
                </section>

                <section class="repository-add-preview__section">
                  <h5 class="repository-add-preview__section-title">{{ t('addS3Repo.previewConnection') }}</h5>
                  <template v-if="addTargetKind === 's3'">
                    <div class="repository-add-preview__row">
                      <span>{{ t('addS3Repo.fieldEndpoint') }}</span>
                      <strong class="repository-add-preview__mono">{{ addTargetS3Endpoint || '—' }}</strong>
                    </div>
                    <div class="repository-add-preview__row">
                      <span>{{ t('repositoriesPage.fieldRegion') }}</span>
                      <strong>{{ addTargetS3Region || '—' }}</strong>
                    </div>
                    <div class="repository-add-preview__row">
                      <span>{{ t('repositoriesPage.fieldS3UrlStyle') }}</span>
                      <strong>{{ addTargetS3UrlStyleLabel }}</strong>
                    </div>
                    <div class="repository-add-preview__row">
                      <span>{{ t('repositoriesPage.fieldAccessKey') }}</span>
                      <strong class="repository-add-preview__mono">{{ maskAddTargetAccessKey(addTargetS3AccessKey) || '—' }}</strong>
                    </div>
                    <div class="repository-add-preview__row">
                      <span>{{ t('repositoriesPage.fieldUseTls') }}</span>
                      <strong class="repository-add-preview__badge" :class="{ 'is-on': addTargetS3UseTls }">
                        <ShieldCheck v-if="addTargetS3UseTls" :size="14" />
                        {{ addTargetS3UseTls ? 'HTTPS' : 'HTTP' }}
                      </strong>
                    </div>
                  </template>
                  <template v-else-if="addTargetKind === 'nas'">
                    <div class="repository-add-preview__row">
                      <span>{{ t('repositoriesPage.fieldProtocol') }}</span>
                      <strong>{{ addTargetNasProtocolLabel }}</strong>
                    </div>
                    <div class="repository-add-preview__row">
                      <span>{{ t('repositoriesPage.fieldEndpoint') }}</span>
                      <strong class="repository-add-preview__mono">{{ addTargetNasEndpoint || '—' }}</strong>
                    </div>
                    <div class="repository-add-preview__row">
                      <span>{{ t('repositoriesPage.fieldProxyNode') }}</span>
                      <strong>{{ addTargetNasProxyNodeName || t('addNasRepo.notBoundProxy') }}</strong>
                    </div>
                    <div class="repository-add-preview__row">
                      <span>{{ t('repositoriesPage.fieldAccessPath') }}</span>
                      <strong>{{ addTargetNasAccessPathLabel }}</strong>
                    </div>
                  </template>
                  <template v-else>
                    <div class="repository-add-preview__row">
                      <span>{{ t('repositoriesPage.fieldProxyNode') }}</span>
                      <strong>{{ addTargetProxyNodeName || '—' }}</strong>
                    </div>
                    <div class="repository-add-preview__row">
                      <span>{{ t('repositoriesPage.fieldProxyNodeDir') }}</span>
                      <strong class="repository-add-preview__mono">{{ addTargetProxyDir || '—' }}</strong>
                    </div>
                  </template>
                </section>
              </div>
            </aside>
          </div>
        </div>
      </div>

      <template #footer>
        <div v-show="false" class="repository-add-footer">
          <ElButton v-if="!addTargetIsFirstStep" @click="prevAddTargetStep">
            {{ t('repositoriesPage.btnPrev') }}
          </ElButton>
          <span v-else />
          <div class="repository-add-footer__actions">
            <ElButton @click="closeAddTargetDialog">{{ t('protection.backupsPage.btnCancel') }}</ElButton>
            <ElButton v-if="!addTargetIsFinalStep" type="primary" @click="nextAddTargetStep">
              {{ addTargetNextButtonLabel }}
            </ElButton>
            <ElButton v-else type="primary" :loading="addTargetSaving" @click="submitAddTargetDialog">
            {{ t('protection.backupsPage.btnAddTargetConfirm') }}
            </ElButton>
          </div>
        </div>
      </template>
    </el-dialog>

    <Teleport to="body">
      <div
        v-if="addSourceOpen"
        ref="addSourceShellRef"
        class="fullscreen-form-fullscreen resource-add-fullscreen source-deploy-fullscreen add-source-fullscreen"
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
                      <span class="add-source-type-card__indicator" aria-hidden="true" />
                      <span class="add-source-type-card__inner">
                        <span class="add-source-type-card__icon">
                          <component :is="backupSourceTypeIcon('host')" :size="20" stroke-width="2" />
                        </span>
                        <span class="add-source-type-card__text">
                          <span class="add-source-type-card__name">{{ t('protection.sourceResources.addSourceTypeHostFile') }}</span>
                          <span class="add-source-type-card__sub">{{ t('protection.sourceResources.deployAgentHint') }}</span>
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
                      <span class="add-source-type-card__indicator" aria-hidden="true" />
                      <span class="add-source-type-card__inner">
                        <span class="add-source-type-card__icon">
                          <component :is="backupSourceTypeIcon('nas')" :size="20" stroke-width="2" />
                        </span>
                        <span class="add-source-type-card__text">
                          <span class="add-source-type-card__name">{{ t('protection.sourceResources.addSourceTypeNas') }}</span>
                          <span class="add-source-type-card__sub">{{ t('protection.sourceResources.addSourceTypeNasHint') }}</span>
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

    <HostSourceDetailDrawer
      :model-value="createHostDetailOpen"
      :node-id="createHostDetailNodeId"
      :source="null"
      @update:model-value="closeCreateHostDetail"
    />

    <NasSourceDetailDrawer
      v-if="createNasDetailOpen"
      v-model="createNasDetailOpen"
      :resource="createNasDetailResource"
      :proxy-nodes="proxyNodes"
    />
  </component>
</template>

<style src="../../styles/fullscreen-form-shell.css"></style>
<style src="../../styles/resource-add.css"></style>
<style src="../../styles/source-deploy-ui.css"></style>
<style src="../../styles/agent-install-wizard.css"></style>
<style scoped>
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

.create-source-config-table {
  width: 100%;
}

.create-source-config-table :deep(.create-source-row--invalid > td.el-table__cell) {
  background: rgb(255 247 237) !important;
}

.create-source-config-table :deep(.create-source-row--invalid > td.el-table__cell:first-child) {
  box-shadow: inset 3px 0 0 rgb(249 115 22);
}

.create-target-config-table :deep(.target-group-row--invalid > td.el-table__cell) {
  background: rgb(255 247 237) !important;
}

.create-target-config-table :deep(.target-group-row--invalid > td.el-table__cell:first-child) {
  box-shadow: inset 3px 0 0 rgb(249 115 22);
}

.create-target-config-table :deep(.target-group-row--connection-failed > td.el-table__cell) {
  background: var(--color-error-light) !important;
}

.create-target-config-table :deep(.target-group-row--connection-failed > td.el-table__cell:first-child) {
  box-shadow: inset 3px 0 0 var(--color-error);
}

.create-validation-popover {
  position: fixed;
  top: 112px;
  left: 50%;
  z-index: 3200;
  width: min(560px, calc(100vw - 32px));
  max-height: min(42vh, 300px);
  padding: 14px 38px 14px 16px;
  overflow: hidden;
  border: 1px solid rgba(245, 158, 11, 0.55);
  border-radius: 8px;
  background: rgb(255 251 235);
  box-shadow: 0 14px 36px rgba(15, 23, 42, 0.18);
  color: rgb(146 64 14);
  transform: translateX(-50%);
}

.create-validation-popover__close {
  position: absolute;
  top: 10px;
  right: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: rgb(146 64 14);
  cursor: pointer;
}

.create-validation-popover__close:hover {
  background: rgba(245, 158, 11, 0.16);
}

.create-validation-popover__title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 700;
  line-height: 18px;
}

.create-validation-popover__list {
  max-height: 220px;
  margin: 0;
  padding: 0 2px 0 18px;
  overflow: auto;
  font-size: 13px;
  line-height: 1.65;
}

.create-source-config-toolbar {
  margin-bottom: 12px;
}

.create-source-config-toolbar--actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.filter-policy-batch-dialog__body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.target-batch-dialog :deep(.el-dialog__body) {
  padding-top: 12px;
}

.target-batch-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.target-endpoint-choice {
  margin-bottom: 0;
}

.target-endpoint-choice__options {
  display: grid;
  width: 100%;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.target-endpoint-choice__options :deep(.el-radio) {
  width: 100%;
  height: auto;
  min-height: 58px;
  margin: 0;
  padding: 10px 14px;
}

.target-endpoint-choice__option {
  display: grid;
  min-width: 0;
  gap: 2px;
}

.target-endpoint-choice__option span {
  overflow: hidden;
  color: var(--el-text-color-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.target-endpoint-choice__hint {
  margin: 8px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.create-source-config-selection {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 20px;
  white-space: nowrap;
  display: flex;
  align-items: center;
}

.create-source-config-selection__count {
  font-weight: 600;
  color: var(--color-primary);
  background: rgba(99, 102, 241, 0.08);
  padding: 0 6px;
  border-radius: 4px;
  margin-left: 4px;
}

.create-source-config-toolbar__divider {
  width: 1px;
  height: 20px;
  background: var(--el-border-color, var(--color-border));
  margin: 0 12px;
}

.create-source-config-search {
  width: min(320px, 100%);
}

.create-source-config-table :deep(.el-table__expanded-cell) {
  padding: 16px;
  background: rgba(248, 250, 252, 0.76);
}

.create-recovery-plan-table :deep(.el-table__expanded-cell) {
  padding: 10px 12px 12px;
  overflow: visible;
}

.create-recovery-plan-table :deep(.el-table__expanded-cell .cell) {
  overflow: visible;
}

/* Long wrap rows: keep Backup Source / Dirs / Restore Plan tops aligned. */
.create-source-config-table :deep(.el-table__body td.el-table__cell:not(.el-table-column--selection):not(.el-table__expand-column)) {
  vertical-align: top !important;
}

.create-source-config-table .create-source-cell-trigger {
  padding-top: 0;
  padding-bottom: 0;
}

.create-source-config-table .create-recovery-plan-action {
  align-items: flex-start;
  padding-top: 0;
}

.create-source-config-table :deep(.create-source-config-expand-column) {
  text-align: center;
}

.create-source-config-table :deep(.create-source-config-expand-column .cell) {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}

.create-source-config-table :deep(.el-table__expand-icon) {
  width: 32px;
  height: 32px;
  color: var(--el-text-color-secondary);
  font-size: 18px;
}

.create-source-config-table :deep(.el-table__expand-icon .el-icon) {
  font-size: 18px;
}

.create-source-config-table :deep(.el-table__expand-icon svg) {
  width: 18px;
  height: 18px;
}

.create-source-config-table :deep(.el-table__expand-icon:hover) {
  color: var(--el-color-primary);
}

.filter-policy-config-table {
  margin-top: 12px;
}

.create-source-config-detail {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(280px, 0.9fr);
  gap: 12px;
  align-items: stretch;
}

.create-source-config-detail__tree,
.create-source-config-detail__selected {
  display: flex;
  min-width: 0;
  min-height: 280px;
  flex-direction: column;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
  padding: 12px;
}

.create-source-config-detail__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.create-source-config-detail__head--compact {
  align-items: flex-start;
}

.create-source-config-detail__head--browse {
  align-items: center;
  margin-top: 8px;
  padding-top: 12px;
  border-top: 1px solid rgba(226, 232, 240, 0.88);
}

.create-source-config-detail__title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.create-source-config-detail__title-row .el-checkbox {
  display: inline-flex;
  align-items: center;
  height: 20px;
  margin: 0;
}

.create-source-config-detail__selected {
  max-height: 480px;
  overflow: auto;
}

.create-source-config-detail__tree {
  max-height: 480px;
  overflow: hidden;
}

.create-source-config-detail__tree .source-dir-tree {
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  padding: 0;
}

.create-source-tree-title-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 2px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 18px;
}

.create-source-tree-title-hint__icon {
  flex: 0 0 auto;
  cursor: help;
  color: var(--el-text-color-placeholder);
}

.create-source-dir-preview {
  display: grid;
  min-width: 0;
}

.create-source-dir-preview-empty {
  color: var(--el-text-color-placeholder);
  font-size: 12px;
  line-height: 1.45;
}

.create-source-dir-preview-empty--error {
  color: var(--color-error-text);
}

.create-source-dir-preview-empty__reason {
  margin-top: 3px;
  color: var(--color-error-text);
  overflow-wrap: anywhere;
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
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  color: var(--el-text-color-primary);
  font-size: 13px;
  font-weight: 600;
  line-height: 20px;
}

.create-source-dir-preview__size,
.create-source-dir-preview__entry-size {
  flex: 0 0 auto;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 400;
}

.create-source-dir-preview__icon {
  flex: 0 0 auto;
  color: var(--el-text-color-secondary);
}

.create-source-dir-preview__icon--folder {
  color: #d97706;
}

.create-source-dir-preview__icon--file {
  color: #2563eb;
}

.create-source-dir-preview__path {
  min-width: 0;
  overflow: visible;
  text-overflow: initial;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: break-word;
}

.create-source-dir-preview--single-line-paths .create-source-dir-preview__item {
  align-items: center;
}

.create-source-dir-preview--single-line-paths .create-source-dir-preview__path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  word-break: normal;
  overflow-wrap: normal;
}

.create-source-dir-preview__more {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 18px;
}

.create-source-cell-trigger {
  width: 100%;
  padding: 4px 0;
}

.create-backup-source-platform {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
}

.create-backup-source-platform :deep(.agent-platform-brand-icon) {
  width: 20px;
  height: 20px;
}

.create-backup-source-meta-row {
  display: inline-flex;
  min-width: 0;
  max-width: 100%;
  align-items: center;
  gap: 6px;
  line-height: 1;
}

.create-backup-source-name {
  display: block;
  min-width: 0;
  max-width: 100%;
  overflow: visible;
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 650;
  line-height: 20px;
  text-overflow: initial;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: break-word;
}

.create-backup-source-type-tag {
  flex: 0 0 auto;
  min-height: 20px;
  line-height: 18px;
}

.backup-source-cell--clickable:hover .create-backup-source-name,
.backup-source-cell--clickable:focus-visible .create-backup-source-name {
  color: rgb(15 23 42);
  text-decoration: none;
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

@media (max-width: 1023.98px) {
  .create-source-config-detail {
    grid-template-columns: 1fr;
  }
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

.create-recovery-table-field {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 8px;
}

.create-recovery-plan-expand {
  min-width: 0;
}

.create-recovery-plan-summary {
  display: block;
  min-width: 0;
  overflow: hidden;
  color: rgb(71 85 105);
  font-size: 13px;
  line-height: 20px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-recovery-plan-cell {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 5px;
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

.create-recovery-plan-cell__policy {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: rgb(51 65 85);
  font-weight: 650;
}

.create-recovery-plan-cell__policy--skip {
  color: var(--color-success-text);
}

.create-recovery-plan-cell__policy--overwrite {
  color: var(--color-warning-text);
}

.create-recovery-plan-cell__policy--pending {
  color: var(--el-color-danger);
}

.create-recovery-plan-cell__issue {
  color: var(--color-error-text);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.35;
  overflow-wrap: anywhere;
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

.create-recovery-plan-cell__meta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.create-recovery-plan-cell__sep {
  width: 3px;
  height: 3px;
  flex: 0 0 3px;
  border-radius: 999px;
  background: rgb(203 213 225);
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
  border-radius: 7px;
  color: rgb(71 85 105);
  font-size: 12px;
  line-height: 1.45;
  transition: background-color 0.16s ease;
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

.create-recovery-plan-mapping__endpoint--folder .create-recovery-plan-mapping__icon,
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

:global(.create-recovery-plan-tooltip__mapping .create-recovery-plan-mapping__text) {
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
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

.create-recovery-plan-action {
  display: inline-flex;
  min-width: 0;
  min-height: 36px;
  align-items: center;
  justify-content: center;
  width: 100%;
  overflow: visible;
}

.create-recovery-plan-action__btn {
  flex: 0 0 32px;
}

.create-wizard-icon-action-btn {
  width: 32px;
  height: 32px;
  min-height: 32px;
  box-sizing: border-box;
  padding: 0;
  border: 1px solid color-mix(in srgb, var(--color-primary) 24%, rgb(203 213 225));
  border-radius: 8px;
  background: color-mix(in srgb, var(--color-primary) 9%, #ffffff);
  color: var(--color-primary);
  line-height: 1;
  box-shadow: 0 1px 2px rgb(15 23 42 / 0.08);
  transition:
    background-color 0.16s ease,
    border-color 0.16s ease,
    box-shadow 0.16s ease,
    color 0.16s ease;
}

.create-wizard-icon-action-btn:not(.is-disabled):hover,
.create-wizard-icon-action-btn:not(.is-disabled):focus-visible {
  border-color: color-mix(in srgb, var(--color-primary) 48%, rgb(203 213 225));
  background: color-mix(in srgb, var(--color-primary) 15%, #ffffff);
  box-shadow: 0 3px 8px rgb(37 99 235 / 0.16);
  color: var(--color-primary-hover);
}

.create-wizard-icon-action-btn.is-disabled {
  border-color: rgb(226 232 240);
  background: rgb(248 250 252);
  color: rgb(148 163 184);
  box-shadow: none;
  opacity: 0.75;
}

:global(.create-recovery-plan-tooltip) {
  max-width: min(560px, calc(100vw - 48px));
}

:global(.create-recovery-plan-tooltip__content) {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 100%;
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

:global(.create-recovery-plan-tooltip__issue) {
  color: var(--color-error-text);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

:global(.create-recovery-plan-tooltip__line) {
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
  line-height: 1.45;
}

:global(.create-recovery-plan-tooltip__line--risk) {
  color: rgb(71 85 105);
  font-size: 12px;
}

.create-recovery-config-panel {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 14px;
  padding: 14px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 8px;
  background: linear-gradient(180deg, rgb(255 255 255) 0%, rgb(248 250 252) 100%);
  overflow-x: auto;
  overflow-y: visible;
}

.create-recovery-config-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding-bottom: 2px;
}

.create-recovery-config-panel__title-wrap {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.create-recovery-config-panel__title {
  color: rgb(15 23 42);
  font-size: 14px;
  font-weight: 700;
  line-height: 20px;
}

.create-recovery-config-panel__policy {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
}

.create-recovery-config-panel__policy-label {
  color: rgb(51 65 85);
  font-size: 12px;
  font-weight: 700;
  line-height: 18px;
  white-space: nowrap;
}

.create-recovery-conflict-policy-select {
  width: 220px;
}

.create-recovery-conflict-policy-select :deep(.el-select__wrapper) {
  min-height: 34px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--color-primary) 7%, #ffffff);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--color-primary) 34%, rgb(203 213 225)) inset;
}

.create-recovery-conflict-policy-select :deep(.el-select__wrapper.is-focused) {
  box-shadow: 0 0 0 1px var(--color-primary) inset;
}

.create-recovery-conflict-policy-select--invalid :deep(.el-select__wrapper) {
  background: var(--color-error-light);
  box-shadow: 0 0 0 1px var(--color-error) inset;
}

.create-recovery-conflict-policy-select--skip :deep(.el-select__placeholder),
.create-recovery-conflict-policy-select--skip :deep(.el-select__placeholder span),
.create-recovery-conflict-policy-select--skip :deep(.el-select__selected-item),
.create-recovery-conflict-policy-select--skip :deep(.el-select__selected-item span) {
  color: rgb(22 101 52);
}

.create-recovery-conflict-policy-select--overwrite :deep(.el-select__placeholder),
.create-recovery-conflict-policy-select--overwrite :deep(.el-select__placeholder span),
.create-recovery-conflict-policy-select--overwrite :deep(.el-select__selected-item),
.create-recovery-conflict-policy-select--overwrite :deep(.el-select__selected-item span) {
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

.create-recovery-target-node-option {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
  padding: 5px 0;
  line-height: 1.3;
}

.create-recovery-target-node-option__main {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.create-recovery-target-node-option__label {
  min-width: 0;
  overflow: hidden;
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-recovery-target-node-option__meta-row {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.create-recovery-target-node-option__meta {
  min-width: 0;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-recovery-target-node-option__platform {
  display: inline-flex;
  width: 20px;
  height: 20px;
  flex: 0 0 20px;
  align-items: center;
  justify-content: center;
  color: rgb(71 85 105);
}

.create-recovery-target-node-option__platform :deep(.agent-platform-brand-icon) {
  width: 18px;
  height: 18px;
}

.create-recovery-target-quick-option {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: flex-start;
  padding: 8px 10px;
  background: #fff;
  border-bottom: 1px solid rgba(226, 232, 240, 0.96);
}

.create-recovery-target-quick-option__wrap {
  display: inline-flex;
  min-width: 0;
}

.create-recovery-target-quick-option__button {
  display: inline-flex !important;
  height: 32px;
  min-width: 0;
  max-width: 100%;
  align-items: center;
  justify-content: center;
  gap: 0;
  padding: 0 11px !important;
  border-color: color-mix(in srgb, var(--color-primary) 34%, rgb(203 213 225)) !important;
  border-radius: 8px !important;
  background: color-mix(in srgb, var(--color-primary) 8%, #ffffff) !important;
  color: color-mix(in srgb, var(--color-primary) 86%, #0f172a) !important;
  font-size: 12px;
  font-weight: 700;
  line-height: 16px;
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--color-primary) 5%, transparent),
    0 1px 3px rgb(37 99 235 / 0.08);
  transition:
    background-color 0.16s ease,
    border-color 0.16s ease,
    color 0.16s ease,
    box-shadow 0.16s ease;
}

:global(.create-recovery-target-quick-option__button > span) {
  display: inline-flex;
  min-width: 0;
  max-width: 100%;
}

.create-recovery-target-quick-option__content {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.create-recovery-target-quick-option__icon {
  flex: 0 0 auto;
}

.create-recovery-target-quick-option__text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-recovery-target-quick-option__button:not(.is-disabled):hover,
.create-recovery-target-quick-option__button:not(.is-disabled):focus {
  border-color: color-mix(in srgb, var(--color-primary) 58%, rgb(203 213 225)) !important;
  background: color-mix(in srgb, var(--color-primary) 13%, #ffffff) !important;
  color: var(--color-primary-hover) !important;
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--color-primary) 9%, transparent),
    0 2px 7px rgb(37 99 235 / 0.13);
}

.create-recovery-target-quick-option__button.is-selected {
  border-color: color-mix(in srgb, var(--color-primary) 72%, transparent) !important;
  background: color-mix(in srgb, var(--color-primary) 17%, #ffffff) !important;
  color: var(--color-primary) !important;
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--color-primary) 16%, transparent),
    0 2px 7px rgb(37 99 235 / 0.14);
}

.create-recovery-target-quick-option__button.is-disabled,
.create-recovery-target-quick-option__button.is-disabled:hover {
  border-color: rgb(226 232 240) !important;
  background: rgb(248 250 252) !important;
  color: rgb(148 163 184) !important;
  box-shadow: none !important;
}

:global(.create-recovery-target-node-select-popper) {
  min-width: 480px !important;
}

:global(.create-recovery-target-node-select-popper .el-select-dropdown__header) {
  padding: 0;
  border-bottom: 0;
}

:global(.create-recovery-target-node-select-popper .el-select-dropdown__item) {
  height: auto;
  min-height: 52px;
  padding-top: 6px;
  padding-bottom: 6px;
}

.create-recovery-dir-plan-stack {
  display: flex;
  min-width: 850px;
  flex-direction: column;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 8px;
  background: #fff;
  overflow: visible;
}

.create-recovery-dir-plan-header,
.create-recovery-dir-plan-row,
.create-recovery-dir-plan-add-wrap {
  display: grid;
  min-width: 850px;
  grid-template-columns: 34px minmax(230px, 1fr) minmax(260px, 1fr) minmax(230px, 1fr) 44px;
  gap: 8px;
  align-items: center;
}

.create-recovery-dir-plan-header {
  padding: 8px 10px;
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 700;
  line-height: 18px;
  background: rgb(248 250 252);
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

.create-recovery-dir-plan-row {
  align-items: start;
  overflow: visible;
  padding: 8px 10px;
  border-top: 1px solid rgba(226, 232, 240, 0.92);
}

.create-recovery-dir-plan-row--invalid {
  background: rgb(255 247 237);
  box-shadow: inset 3px 0 0 rgb(249 115 22);
}

.create-recovery-dir-plan-row:first-child {
  border-top: 0;
}

.create-recovery-dir-plan-add-wrap {
  padding: 8px 10px 10px;
  border-top: 1px solid rgba(226, 232, 240, 0.92);
}

.create-recovery-dir-plan-add-row {
  display: flex;
  grid-column: 2 / span 3;
  width: 70%;
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
  cursor: pointer;
  transition:
    border-color 0.16s ease,
    background 0.16s ease;
}

.create-recovery-dir-plan-add-row:hover {
  border-color: var(--color-primary);
  background: rgba(239, 246, 255, 0.82);
}

.create-recovery-dir-plan-cell {
  min-width: 0;
}

.create-recovery-dir-plan-cell--invalid :deep(.el-input__wrapper),
.create-recovery-dir-plan-cell--invalid :deep(.el-select__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}

.create-recovery-dir-plan-row__index {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 22px;
  border-radius: 6px;
  background: rgb(226 232 240);
  color: rgb(71 85 105);
  font-size: 11px;
  font-weight: 750;
}

.create-recovery-dir-plan-cell--actions {
  display: flex;
  justify-content: center;
}

.create-recovery-dir-plan-remove-btn:not(.is-disabled) {
  color: var(--el-color-danger);
}

.create-recovery-dir-plan-remove-btn:not(.is-disabled):hover,
.create-recovery-dir-plan-remove-btn:not(.is-disabled):focus-visible {
  background: rgb(254 242 242);
  color: var(--el-color-danger);
}

.create-recovery-dir-plan-row__placeholder {
  display: inline-flex;
  min-height: 32px;
  align-items: center;
  color: rgb(148 163 184);
  font-size: 12px;
}

.create-recovery-plan-switch {
  --el-switch-off-color: #94a3b8;
}

.create-recovery-plan-switch :deep(.el-switch__inner) {
  padding: 0 8px;
}

.create-recovery-disabled-cell {
  display: inline-flex;
  min-height: 32px;
  align-items: center;
  color: var(--el-text-color-placeholder);
  font-size: 13px;
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
  background: var(--color-error-light);
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
  width: min(360px, calc(100vw - 48px)) !important;
  max-width: calc(100vw - 48px);
  padding: 10px 10px 10px 12px;
  border-radius: 10px;
}

.create-confirm-lead {
  margin: 0 0 14px;
  color: rgb(71 85 105);
  font-size: 13px;
  line-height: 1.55;
}

.create-confirm-section {
  padding: 16px 0;
  border-top: 1px solid rgba(226, 232, 240, 0.95);
}

.create-confirm-section--first {
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

.create-confirm-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(140px, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.create-confirm-summary-item {
  display: grid;
  gap: 4px;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 8px;
  background: rgba(248, 250, 252, 0.82);
}

.create-confirm-summary-item span {
  color: rgb(100 116 139);
  font-size: 12px;
}

.create-confirm-summary-item strong {
  color: rgb(15 23 42);
  font-size: 16px;
  font-weight: 700;
}

.create-confirm-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.create-confirm-card {
  overflow: hidden;
  border: 1px solid rgba(203, 213, 225, 0.9);
  border-radius: 10px;
  background: linear-gradient(180deg, rgb(255 255 255) 0%, rgb(248 250 252) 100%);
  box-shadow: 0 8px 22px rgb(15 23 42 / 0.05);
}

.create-confirm-card__name {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  padding: 12px 14px;
  border-bottom: 1px solid rgba(226, 232, 240, 0.9);
  background: rgba(255, 255, 255, 0.82);
}

.create-confirm-card__index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-width: 50px;
  height: 32px;
  padding: 0 9px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--color-primary) 7%, #fff);
  border: 1px solid color-mix(in srgb, var(--color-primary) 26%, #fff);
  color: color-mix(in srgb, var(--color-primary) 82%, #000);
  font-size: 12px;
  font-weight: 700;
  font-feature-settings: 'tnum' 1;
}

.create-confirm-card__index.is-nas {
  background: color-mix(in srgb, var(--color-primary) 7%, #fff);
  border-color: color-mix(in srgb, var(--color-primary) 26%, #fff);
  color: color-mix(in srgb, var(--color-primary) 82%, #000);
}

.create-confirm-card__name-text {
  min-width: 0;
  color: rgb(15 23 42);
  font-size: 14px;
  font-weight: 650;
  line-height: 22px;
  word-break: break-word;
}

.create-confirm-card__meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 0;
  padding: 14px;
}

.create-confirm-card__meta div {
  min-width: 0;
}

.create-confirm-card__meta-item {
  min-width: 0;
  padding: 12px;
  border: 1px solid rgba(226, 232, 240, 0.92);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.88);
}

.create-confirm-card__meta-item--wide {
  grid-column: 1 / -1;
}

.create-confirm-card__meta dt {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 650;
  color: rgb(100 116 139);
}

.create-confirm-meta-label,
.create-confirm-detail-main {
  display: inline-flex;
  max-width: 100%;
  align-items: center;
  gap: 6px;
}

.create-confirm-meta-label svg {
  flex: 0 0 auto;
  width: 24px;
  height: 24px;
  box-sizing: border-box;
  padding: 5px;
  border: 1px solid color-mix(in srgb, var(--color-primary) 18%, transparent);
  border-radius: 8px;
  background: color-mix(in srgb, var(--color-primary) 10%, #ffffff);
  color: var(--color-primary);
}

.create-confirm-detail-main {
  flex-wrap: wrap;
}

.create-confirm-binding-summary {
  flex-wrap: wrap;
  justify-content: flex-start;
  text-align: left;
}

.create-confirm-binding-summary__item {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.create-confirm-binding-summary__name {
  min-width: 0;
  overflow: hidden;
  color: rgb(30 41 59);
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-confirm-binding-empty {
  color: rgb(148 163 184);
  font-size: 13px;
}

.create-confirm-binding-popover-stack {
  display: grid;
  min-width: 0;
  gap: 12px;
}

.create-confirm-card__meta dd {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: rgb(30 41 59);
  word-break: break-word;
  white-space: pre-line;
}

.create-confirm-detail-stack {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.create-confirm-detail-line {
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 1.45;
  word-break: break-word;
}

.create-confirm-source {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.create-confirm-source__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
}

.create-confirm-source__identity {
  display: inline-flex;
  min-width: 0;
  max-width: 100%;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.create-confirm-source__name {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 13px;
  font-weight: 650;
  color: rgb(15 23 42);
}

.create-confirm-source__count,
.create-confirm-source__size {
  font-size: 12px;
  color: rgb(100 116 139);
}

.create-confirm-dir-list {
  display: flex;
  flex-direction: column;
  max-height: 150px;
  margin: 0;
  padding: 0;
  overflow: auto;
  list-style: none;
}

.create-confirm-dir-box {
  overflow: hidden;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 8px;
  background: rgb(248 250 252);
}

.create-confirm-dir-list__item {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 28px;
  padding: 7px 10px;
  border-bottom: 1px solid rgba(226, 232, 240, 0.82);
  background: transparent;
}

.create-confirm-dir-list__item:last-child {
  border-bottom: 0;
}

.create-confirm-dir-list__icon {
  flex: 0 0 auto;
  color: rgb(100 116 139);
}

.create-confirm-dir-list__icon--folder {
  color: #d97706;
}

.create-confirm-dir-list__icon--file {
  color: #2563eb;
}

.create-confirm-dir-list__path {
  flex: 1 1 auto;
  min-width: 0;
  color: rgb(30 41 59);
  overflow-wrap: anywhere;
  white-space: normal;
}

.create-confirm-dir-list__size {
  flex: 0 0 auto;
  font-size: 12px;
  color: rgb(100 116 139);
  white-space: nowrap;
}

.create-confirm-hover-summary {
  display: inline-flex;
  max-width: 100%;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 4px 8px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 7px;
  background: rgb(248 250 252);
  color: rgb(30 41 59);
  font: inherit;
  line-height: 1.35;
  text-align: left;
  cursor: default;
}

.create-confirm-hover-summary span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-confirm-hover-summary svg {
  flex: 0 0 auto;
  color: var(--color-primary);
}

.create-confirm-hover-summary:hover,
.create-confirm-hover-summary:focus-visible {
  border-color: color-mix(in srgb, var(--color-primary) 32%, rgb(203 213 225));
  background: color-mix(in srgb, var(--color-primary) 8%, #ffffff);
  outline: none;
}

:global(.create-confirm-detail-popover) {
  max-width: min(360px, calc(100vw - 48px));
}

:global(.create-confirm-detail-popover__content) {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 8px;
}

:global(.create-confirm-detail-popover__title) {
  color: rgb(15 23 42);
  font-size: 12px;
  font-weight: 700;
  line-height: 1.4;
}

:global(.create-confirm-detail-popover__lines) {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 5px;
}

:global(.create-confirm-detail-popover__line) {
  color: rgb(71 85 105);
  font-size: 12px;
  line-height: 1.45;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.create-recovery-plan-cell--wrap .create-recovery-plan-cell__policy,
.create-recovery-plan-cell--review .create-recovery-plan-cell__policy {
  overflow: visible;
  white-space: normal;
}

.create-recovery-plan-cell--wrap .create-recovery-plan-cell__policy-text,
.create-recovery-plan-cell--wrap .create-recovery-plan-mapping__text,
.create-recovery-plan-cell--wrap .create-recovery-plan-mapping__pending,
.create-recovery-plan-cell--review .create-recovery-plan-cell__policy-text,
.create-recovery-plan-cell--review .create-recovery-plan-mapping__text,
.create-recovery-plan-cell--review .create-recovery-plan-mapping__pending {
  overflow: visible;
  text-overflow: initial;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: break-word;
}

/* List: source → on first row; restore path uses full width below with hanging indent. */
.create-recovery-plan-cell--wrap .create-recovery-plan-mapping {
  display: grid;
  grid-template-columns: max-content auto minmax(0, 1fr);
  grid-template-rows: auto auto;
  align-items: start;
  column-gap: 6px;
  row-gap: 4px;
}

.create-recovery-plan-cell--wrap .create-recovery-plan-mapping > .create-recovery-plan-mapping__endpoint:not(.create-recovery-plan-mapping__endpoint--target) {
  grid-column: 1;
  grid-row: 1;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.create-recovery-plan-cell--wrap .create-recovery-plan-mapping__arrow {
  grid-column: 2;
  grid-row: 1;
  align-self: center;
  padding-left: 0;
  line-height: 1;
}

.create-recovery-plan-cell--wrap .create-recovery-plan-mapping__endpoint--target {
  grid-column: 1 / -1;
  grid-row: 2;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: start;
  gap: 5px;
  min-width: 0;
}

.create-recovery-plan-cell--wrap .create-recovery-plan-mapping__text {
  display: block;
  min-width: 0;
  max-width: none;
}

.create-recovery-plan-cell--review .create-recovery-plan-mapping {
  align-items: start;
}

.create-recovery-plan-cell--review .create-recovery-plan-mapping__endpoint {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: start;
  gap: 5px;
  min-width: 0;
}

.create-recovery-plan-cell--review .create-recovery-plan-mapping__text {
  display: block;
  min-width: 0;
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

.dp-create-tree-shell__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 0 0 10px;
  padding: 10px 12px;
  border: 1px solid rgba(220, 38, 38, 0.22);
  border-radius: 8px;
  background: rgba(254, 242, 242, 0.92);
  color: var(--color-error-text);
  font-size: 13px;
  line-height: 1.45;
}

.dp-create-tree-shell__retry {
  flex: 0 0 auto;
  gap: 4px;
}

.dp-create-tree-shell__retry .is-spinning {
  animation: hfl-refresh-spin 0.85s linear infinite;
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
  border-top: 1px solid rgba(226, 232, 240, 0.82);
}

.source-dir-tree :deep(.el-tree-node__content) {
  display: flex;
  align-items: center;
  height: auto;
  min-height: 30px;
  padding-top: 1px;
  padding-bottom: 1px;
  border-radius: 4px;
}

.source-dir-tree :deep(.el-tree-node__expand-icon) {
  display: inline-flex;
  flex: 0 0 20px;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 30px;
  margin: 0;
  padding: 0;
}

.source-dir-tree :deep(.el-tree-node__content:hover) {
  background: rgba(226, 232, 240, 0.5);
}

.source-dir-tree :deep(.el-tree-node.is-current > .el-tree-node__content) {
  background: linear-gradient(180deg, color-mix(in srgb, var(--color-primary) 14%, #fff) 0%, color-mix(in srgb, var(--color-primary) 20%, #fff) 100%);
}

.source-dir-tree :deep(.el-checkbox) {
  display: inline-flex;
  flex: 0 0 20px;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 30px;
  margin-right: 6px;
}

.source-dir-tree :deep(.el-checkbox__input) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.source-dir-tree :deep(.el-tree-node.is-disabled > .el-tree-node__content .el-checkbox__inner) {
  border-color: rgb(100 116 139);
  background-color: rgb(203 213 225);
}

.source-dir-tree :deep(.el-tree-node.is-disabled > .el-tree-node__content .el-checkbox__input.is-checked .el-checkbox__inner) {
  border-color: rgb(71 85 105);
  background-color: rgb(100 116 139);
}

.source-dir-tree :deep(.el-tree-node.is-disabled > .el-tree-node__content .el-checkbox__input.is-checked .el-checkbox__inner::after) {
  border-color: #fff;
}

.create-manual-path {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  padding: 8px 0 10px;
}

.create-manual-path__input {
  min-width: 0;
}

.create-manual-path__input :deep(.el-input__wrapper),
.create-manual-path__input :deep(.el-input__inner),
.create-manual-path .create-manual-path__button.el-button--small {
  height: 34px !important;
  min-height: 34px !important;
}

.create-manual-path .create-manual-path__button.el-button--small {
  align-self: center;
  padding-inline: 14px;
}

.create-manual-path__input--error :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}

.create-manual-path__error {
  grid-column: 1 / -1;
  margin: -2px 0 0;
  color: var(--color-error-text);
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.dp-added-dir-empty {
  display: flex;
  min-height: 112px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding-inline: 20px;
  color: rgb(148 163 184);
  border-color: rgb(226 232 240);
  line-height: 1.45;
}

.dp-added-dir-empty--error {
  border-color: var(--color-error-border);
  background: var(--color-error-light);
  color: var(--color-error-text);
}

.dp-added-dir-empty__icon {
  color: var(--color-error);
}

.dp-added-dir-empty__title {
  color: var(--color-error-text);
  font-size: 13px;
  font-weight: 700;
}

.dp-added-dir-empty__hint {
  max-width: 240px;
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 500;
  overflow-wrap: anywhere;
}

.create-dir-row {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-height: 30px;
  padding: 2px 6px;
  border-radius: 6px;
  background: rgba(248, 250, 252, 0.72);
  color: rgb(30 41 59);
}

.create-dir-row--tree {
  background: transparent;
  padding-inline: 6px;
}

.create-dir-row--added {
  min-height: 38px;
  padding: 7px 2px 7px 0;
  border-bottom: 1px solid rgba(226, 232, 240, 0.72);
  border-radius: 0;
  background: transparent;
}

.create-dir-row--load-more {
  justify-content: flex-start;
  gap: 8px;
  background: transparent;
  color: var(--color-info);
}

.source-dir-tree :deep(.el-tree-node__content:hover) .create-dir-row--tree {
  background: rgba(241, 245, 249, 0.95);
}

.create-dir-row--added:hover {
  background: transparent;
}

.source-dir-tree :deep(.el-tree-node.is-checked > .el-tree-node__content) .create-dir-row--tree {
  background: color-mix(in srgb, var(--color-primary) 14%, #fff);
}

.source-dir-tree :deep(.el-tree-node__content) .create-dir-row--filename-encoding-warning {
  border: 1px solid var(--color-warning-border, #f2dba8);
  background: var(--color-warning-light, #fcf3e1);
}

.source-dir-tree :deep(.el-tree-node__content:hover) .create-dir-row--filename-encoding-warning,
.source-dir-tree :deep(.el-tree-node.is-checked > .el-tree-node__content) .create-dir-row--filename-encoding-warning {
  background: color-mix(in srgb, var(--color-warning-light, #fcf3e1) 86%, #fff);
}

.create-dir-row--filename-encoding-warning .create-dir-row__label,
.create-dir-row--filename-encoding-warning .create-dir-row__path,
.create-dir-row__filename-encoding-icon {
  color: rgb(146 89 8);
}

.create-dir-row__filename-encoding-icon {
  flex: 0 0 auto;
}

:global(.smb-filename-encoding-tooltip) {
  width: min(420px, calc(100vw - 32px));
  max-width: min(420px, calc(100vw - 32px));
  padding: 12px 14px !important;
  border-color: var(--color-warning-border, #f2dba8) !important;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.16);
}

:global(.smb-filename-encoding-tooltip__content) {
  display: grid;
  gap: 10px;
  color: rgb(51 65 85);
  white-space: normal;
  line-height: 1.5;
}

:global(.smb-filename-encoding-tooltip__title) {
  color: rgb(146 89 8);
  font-size: 14px;
  font-weight: 700;
}

:global(.smb-filename-encoding-tooltip__section) {
  display: grid;
  gap: 3px;
}

:global(.smb-filename-encoding-tooltip__label) {
  color: rgb(30 41 59);
  font-weight: 600;
}

.source-dir-tree :deep(.el-tree-node.is-disabled > .el-tree-node__content) {
  cursor: not-allowed;
  pointer-events: auto;
}

.source-dir-tree :deep(.el-tree-node.is-disabled > .el-tree-node__content) .create-dir-row--tree,
.create-dir-row--disabled {
  border: 1px solid rgba(148, 163, 184, 0.45);
  background: rgba(203, 213, 225, 0.7);
  color: rgb(71 85 105);
  opacity: 1;
}

.source-dir-tree :deep(.el-tree-node.is-disabled > .el-tree-node__content) .create-dir-row__icon,
.create-dir-row--disabled .create-dir-row__icon {
  color: rgb(100 116 139);
}

.source-dir-tree :deep(.el-tree-node.is-disabled > .el-tree-node__content) .create-dir-row__label,
.create-dir-row--disabled .create-dir-row__label {
  color: rgb(51 65 85);
}

.source-dir-tree :deep(.el-tree-node.is-disabled > .el-tree-node__content) .create-dir-row__path,
.create-dir-row--disabled .create-dir-row__path {
  color: rgb(71 85 105);
}

.source-dir-tree :deep(.el-tree-node.is-disabled > .el-tree-node__content:hover) .create-dir-row--tree {
  background: rgba(203, 213, 225, 0.78);
}

.create-dir-row__icon {
  flex-shrink: 0;
  color: rgb(100 116 139);
}

.create-dir-row__icon--folder {
  color: #d97706;
}

.create-dir-row__icon--snapshot {
  color: var(--color-primary);
}

.create-dir-row__icon--file {
  color: #2563eb;
}

.source-dir-tree :deep(.el-tree-node.is-disabled > .el-tree-node__content) .create-dir-row__icon,
.create-dir-row--disabled .create-dir-row__icon {
  color: rgb(100 116 139);
}

.create-dir-row__body {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  flex-direction: column;
  gap: 1px;
}

.create-dir-row__label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
  line-height: 16px;
  color: rgb(30 41 59);
}

.create-dir-row__path {
  font-size: 12px;
  line-height: 14px;
  color: rgb(100 116 139);
  word-break: break-word;
}

.create-dir-row__meta {
  flex: 0 0 auto;
  font-size: 12px;
  color: rgb(100 116 139);
  white-space: nowrap;
}

.create-dir-row__disabled-icon {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  color: rgb(71 85 105);
  cursor: help;
  pointer-events: auto;
}

.create-dir-row__action {
  flex: 0 0 auto;
  width: 24px;
  height: 24px;
  min-height: 24px;
  margin-left: -2px;
  padding: 0;
  opacity: 0.68;
  transition: opacity 0.14s ease, background-color 0.14s ease;
}

.create-dir-row--added:hover .create-dir-row__action,
.create-dir-row__action:focus-visible {
  opacity: 1;
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
  box-shadow: 0 6px 14px color-mix(in srgb, var(--color-primary) 14%, transparent);
}

.dp-create-action-btn:hover,
.dp-create-action-btn:focus-visible {
  box-shadow: 0 8px 18px color-mix(in srgb, var(--color-primary) 18%, transparent);
}

.target-picker-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(170px, 220px) auto;
  gap: 10px;
  align-items: start;
}

.target-picker-grid--with-nas-mode {
  grid-template-columns: minmax(170px, 0.7fr) minmax(220px, 1fr) minmax(170px, 0.8fr) auto;
}

.target-picker-grid--filter-policy {
  grid-template-columns: minmax(180px, 0.85fr) minmax(220px, 1.1fr) minmax(160px, 0.75fr) auto;
}

.target-picker-grid--row {
  grid-template-columns: minmax(0, 1fr) minmax(170px, 220px);
}

.target-picker-grid__search,
.target-picker-grid__type,
.target-picker-grid__target {
  width: 100%;
}

.file-filter-multi-select :deep(.el-select__wrapper) {
  flex-wrap: nowrap;
}

.file-filter-multi-select :deep(.el-select__selection) {
  min-width: 0;
  flex-wrap: nowrap;
}

.file-filter-multi-select :deep(.el-select__selected-item) {
  max-width: 100%;
}

.target-dir-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.target-dir-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
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

.target-assignment-table-meta {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.create-table-info-popover {
  display: grid;
  gap: 8px;
  max-width: 100%;
  color: rgb(71 85 105);
  font-size: 12px;
  line-height: 1.5;
}

.create-table-info-popover--dirs {
  max-height: min(360px, calc(var(--app-viewport-height) - 180px));
  overflow: auto;
}

.create-table-info-popover--selected-paths {
  gap: 0;
}

.create-table-info-popover__summary {
  color: rgb(15 23 42);
  font-weight: 650;
}

.create-table-info-popover--selected-paths .create-table-info-popover__summary {
  padding-bottom: 6px;
}

.create-table-info-popover__dir-row {
  min-width: 0;
  padding-right: 0;
}

.create-table-info-popover__dir-row:last-child {
  border-bottom: 0;
}

.create-table-info-popover__dir-row .create-dir-row__path {
  white-space: normal;
  overflow-wrap: anywhere;
}

.create-config-summary-cell {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 6px;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 1.35;
}

.create-config-select-row {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.create-config-select-hover-target {
  display: block;
  min-width: 0;
  flex: 1 1 auto;
}

.create-config-compression-select {
  width: 80%;
}

.create-compression-description {
  display: block;
  width: 100%;
  min-width: 0;
  overflow: hidden;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:global(.create-compression-description-popper) {
  max-width: min(288px, calc(100vw - 32px));
}

.create-compression-detail-popover__title {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 650;
  line-height: 20px;
}

.create-compression-detail-popover__icon {
  flex: 0 0 auto;
  color: rgb(100 116 139);
}

.create-compression-detail-popover__icon--balanced {
  color: var(--color-primary);
}

.create-compression-detail-popover__icon--high {
  color: rgb(180 83 9);
}

.create-compression-detail-popover__body {
  color: rgb(30 41 59);
  font-size: 13px;
  line-height: 1.55;
  overflow-wrap: anywhere;
  white-space: normal;
}

.create-config-refresh-btn {
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  min-height: 28px;
  box-sizing: border-box;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  color: var(--color-primary);
  line-height: 1;
  transform: translateZ(0);
  -webkit-font-smoothing: antialiased;
  backface-visibility: hidden;
}

.create-config-refresh-btn:hover,
.create-config-refresh-btn:focus-visible {
  border-color: color-mix(in srgb, var(--color-primary) 18%, transparent);
  background: color-mix(in srgb, var(--color-primary) 8%, #ffffff);
  color: var(--color-primary);
}

.create-config-refresh-btn :deep(svg) {
  display: block;
  shape-rendering: geometricPrecision;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.create-config-refresh-btn :deep(.el-icon) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.create-config-summary-line {
  display: block;
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
  color: rgb(71 85 105);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-config-summary-line--empty {
  color: rgb(148 163 184);
}

.create-config-selected-summary {
  display: inline-flex;
  max-width: 100%;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.create-config-summary-popover {
  display: grid;
  gap: 6px;
  color: rgb(71 85 105);
  font-size: 12px;
  line-height: 1.55;
}

.create-config-summary-popover div {
  overflow-wrap: anywhere;
  white-space: normal;
}

:global(.create-policy-select-popper) {
  max-width: min(560px, calc(100vw - 32px));
}

:global(.create-policy-select-popper .el-select-dropdown__item) {
  height: auto;
  min-height: 44px;
  padding: 6px 10px;
  line-height: normal;
  max-width: 100%;
  overflow: hidden;
}

:global(.create-policy-select-popper .el-select-dropdown__item.selected) {
  font-weight: 500;
}

:global(.create-policy-select-popper .el-select-dropdown__empty) {
  padding: 12px 10px;
  font-size: 13px;
  line-height: 1.5;
  white-space: normal;
  word-break: break-word;
}

:global(.create-policy-option-popper) {
  max-width: min(460px, calc(100vw - 48px));
}

.create-policy-option {
  display: flex;
  min-width: 0;
  max-width: 100%;
  flex-direction: column;
  gap: 4px;
  overflow: hidden;
}

.create-policy-option__head {
  display: flex;
  min-width: 0;
  max-width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  overflow: hidden;
}

.create-policy-option__name {
  min-width: 0;
  overflow: hidden;
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 650;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-policy-option__summary {
  min-width: 0;
  overflow: hidden;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 17px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-filter-rules-option {
  display: flex;
  min-width: 0;
  max-width: 100%;
  align-items: center;
  gap: 6px;
  overflow: hidden;
}

.create-filter-rules-option__prefix {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 56px;
  padding: 1px 7px;
  border: 1px solid rgb(254 215 170);
  border-radius: 999px;
  background: rgb(255 247 237);
  color: rgb(154 52 18);
  font-size: 12px;
  font-weight: 600;
  line-height: 17px;
  white-space: nowrap;
}

.create-filter-rules-option__list {
  display: block;
  min-width: 0;
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-filter-rules-option__rule {
  display: inline-block;
  max-width: 112px;
  min-width: 0;
  margin-right: 4px;
  padding: 2px 6px;
  overflow: hidden;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-light);
  color: var(--el-text-color-primary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.45;
  text-overflow: ellipsis;
  vertical-align: middle;
  white-space: nowrap;
}

.create-filter-rules-option__empty {
  min-width: 0;
  overflow: hidden;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 17px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.create-policy-detail-popover {
  display: grid;
  gap: 12px;
  color: rgb(71 85 105);
  font-size: 12px;
  line-height: 1.45;
}

.create-policy-detail-popover__head {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid rgb(226 232 240);
}

.create-policy-detail-popover__title {
  min-width: 0;
  overflow-wrap: anywhere;
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 650;
  line-height: 20px;
}

.create-policy-detail-popover__rows {
  display: grid;
  gap: 6px;
}

.create-policy-detail-popover__sections {
  display: grid;
  min-width: 0;
  gap: 12px;
}

.create-policy-detail-popover__row {
  display: grid;
  min-width: 0;
  grid-template-columns: minmax(92px, 0.42fr) minmax(0, 1fr);
  align-items: start;
  gap: 10px;
}

.create-policy-detail-popover__label {
  color: rgb(100 116 139);
}

.create-policy-detail-popover__value {
  min-width: 0;
  overflow-wrap: anywhere;
  color: rgb(30 41 59);
  font-size: 13px;
  line-height: 1.5;
}

.create-policy-detail-popover__value--mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
}

.create-policy-detail-popover__section {
  display: grid;
  min-width: 0;
  gap: 8px;
}

.create-policy-detail-popover__section--line {
  display: grid;
  grid-template-columns: 78px minmax(0, 1fr);
  align-items: baseline;
  column-gap: 8px;
}

.create-policy-detail-popover__section--filter-line {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  align-items: baseline;
  column-gap: 8px;
}

.create-policy-detail-popover__section-title {
  color: rgb(51 65 85);
  font-size: 12px;
  font-weight: 650;
  line-height: 16px;
}

.create-policy-detail-popover__retention-box {
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid rgb(226 232 240);
  border-radius: 8px;
  background: rgb(248 250 252);
}

.create-filter-rules-preview {
  display: flex;
  min-width: 0;
  max-height: 260px;
  flex-direction: column;
  gap: 6px;
  overflow-y: auto;
  padding: 10px 12px;
  border: 1px solid rgb(226 232 240);
  border-radius: 8px;
  background: rgb(248 250 252);
}

.create-filter-rules-preview__line {
  display: block;
  min-width: 0;
  padding: 6px 8px;
  border: 1px solid rgb(226 232 240);
  border-radius: 6px;
  background: rgb(255 255 255);
  color: rgb(15 23 42);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.create-filter-rules-preview__empty {
  margin: 0;
  color: rgb(100 116 139);
  font-size: 13px;
  line-height: 1.5;
}

:global(.create-policy-option-popper .policy-retention-detail-list) {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 8px;
}

:global(.create-policy-option-popper .policy-retention-detail-list__line) {
  display: grid;
  min-width: 0;
  grid-template-columns: 72px minmax(0, 1fr);
  align-items: start;
  column-gap: 10px;
  overflow-wrap: anywhere;
  color: rgb(15 23 42);
  font-size: 13px;
  line-height: 1.55;
}

:global(.create-policy-option-popper .policy-retention-detail-list__line--summary) {
  display: block;
  padding-bottom: 8px;
  border-bottom: 1px solid rgb(226 232 240);
  color: rgb(15 23 42);
  font-weight: 600;
}

:global(.create-policy-option-popper .policy-retention-detail-list__label) {
  color: rgb(71 85 105);
  font-weight: 600;
  white-space: nowrap;
}

:global(.create-policy-option-popper .policy-retention-detail-list__text) {
  min-width: 0;
  overflow-wrap: anywhere;
  color: rgb(15 23 42);
}

.target-select-with-meta {
  display: flex;
  min-width: 0;
  min-height: 36px;
  flex-direction: row;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  overflow: visible;
}

.target-validation-status {
  display: flex;
  width: 100%;
  align-items: flex-start;
  gap: 8px;
  box-sizing: border-box;
  margin: 0 0 12px;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.5;
}

.target-validation-status--pending {
  border-color: color-mix(in srgb, var(--color-primary) 24%, transparent);
  background: color-mix(in srgb, var(--color-primary) 7%, #fff);
  color: color-mix(in srgb, var(--color-primary) 78%, rgb(15 23 42));
}

.target-validation-status--failed {
  border-color: color-mix(in srgb, var(--color-error) 24%, transparent);
  background: var(--color-error-light);
  color: var(--color-error-text);
}

.target-validation-status__icon {
  flex: 0 0 auto;
  margin-top: 2px;
}

.target-validation-status__text {
  min-width: 0;
  overflow-wrap: anywhere;
}

.target-validation-status__spinner,
.target-connection-result__spinner {
  animation: hfl-refresh-spin 0.85s linear infinite;
}

.target-connection-result {
  display: flex;
  flex: 0 0 100%;
  align-items: flex-start;
  gap: 5px;
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.target-connection-result__icon {
  flex: 0 0 auto;
  margin-top: 1px;
}

.target-connection-result__summary {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.target-connection-result__details {
  flex: 0 0 auto;
  padding: 0;
  color: inherit;
  background: transparent;
  border: 0;
  font: inherit;
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}

.target-connection-result__details:hover {
  color: var(--color-error);
}

.target-connection-result__details:focus-visible {
  outline: 2px solid currentcolor;
  outline-offset: 2px;
  border-radius: 2px;
}

.target-connection-result--success {
  color: rgb(21 128 61);
}

.target-connection-result--pending {
  color: var(--color-primary);
}

.target-connection-result--failed {
  color: var(--color-error-text);
}

.target-assignment-issue {
  display: flex;
  flex: 0 0 100%;
  align-items: flex-start;
  gap: 5px;
  color: var(--color-error-text);
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.target-assignment-issue__icon {
  flex: 0 0 auto;
  margin-top: 1px;
}

.target-assignment-summary {
  display: flex;
  min-width: 0;
  flex: 1 1 0%;
  flex-direction: column;
  gap: 4px;
}

.target-assignment-summary__head {
  display: flex;
  min-width: 0;
  align-items: center;
}

.target-assignment-summary__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-primary);
}

.target-assignment-summary__tags {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.target-assignment-summary__detail {
  font-size: 12px;
  line-height: 1.45;
  color: rgb(100 116 139);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wizard-summary-cell {
  display: grid;
  min-width: 0;
  grid-template-columns: 8px minmax(0, 1fr);
  align-items: start;
  column-gap: 8px;
}

.wizard-summary-cell__dot {
  width: 6px;
  height: 6px;
  margin-top: 6px;
  border-radius: 999px;
  background: rgb(148 163 184);
}

.wizard-summary-cell__body {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.wizard-summary-cell__name {
  display: block;
  min-width: 0;
  overflow: hidden;
  color: rgb(30 41 59);
  font-size: 13px;
  font-weight: 650;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wizard-summary-cell__detail {
  display: block;
  min-width: 0;
  overflow: hidden;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 17px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wizard-summary-cell--inline {
  display: inline-flex;
  max-width: 100%;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.hfl-list-table .create-config-summary-cell .create-config-selected-summary .wizard-summary-cell--inline :deep(.el-tag.hfl-tag--boolean) {
  min-width: 71px;
  flex: 0 0 auto;
}

.wizard-summary-cell--inline .wizard-summary-cell__detail {
  flex: 1 1 auto;
  line-height: 20px;
}

.wizard-summary-cell--online .wizard-summary-cell__dot {
  background: var(--color-success);
}

.wizard-summary-cell--warning .wizard-summary-cell__dot {
  background: var(--color-warning);
}

.wizard-summary-cell--offline .wizard-summary-cell__dot {
  background: var(--color-error);
}

.wizard-target-repository-cell {
  display: grid;
  min-width: 0;
  grid-template-columns: 8px minmax(0, 1fr);
  align-items: start;
  column-gap: 8px;
}

.wizard-target-repository-cell--confirm {
  max-width: 100%;
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
  display: block;
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
  display: block;
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

.target-assignment-empty {
  min-width: 0;
  flex: 1 1 0%;
  overflow: hidden;
  color: rgb(148 163 184);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.target-assignment-change-btn {
  flex: 0 0 auto;
  opacity: 1;
}

.target-status-summary {
  display: inline-flex;
  max-width: 100%;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
  line-height: 1.35;
}

.target-status-summary__main {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 7px;
}

.target-status-summary__dot {
  width: 7px;
  height: 7px;
  flex: 0 0 7px;
  border-radius: 999px;
  background: rgb(148 163 184);
}

.target-status-summary__label {
  min-width: 0;
  overflow: hidden;
  color: rgb(30 41 59);
  font-size: 13px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.target-status-summary__detail {
  min-width: 0;
  overflow: hidden;
  color: rgb(100 116 139);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.target-status-summary--online .target-status-summary__dot {
  background: var(--color-success);
}

.target-status-summary--warning .target-status-summary__dot {
  background: var(--color-warning);
}

.target-status-summary--offline .target-status-summary__dot {
  background: var(--color-error);
}

.target-status-summary--unassigned .target-status-summary__label,
.target-status-summary--unknown .target-status-summary__label {
  color: rgb(71 85 105);
}

.target-capacity-cell {
  display: grid;
  gap: 6px;
  min-width: 0;
  color: rgb(71 85 105);
  font-size: 12px;
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

.policy-rate-limit-field {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(92px, auto);
  gap: 8px;
}

.policy-rate-limit-field__mode,
.policy-rate-limit-field__number {
  width: 100%;
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

.create-policy-dialog .create-policy-dialog__desc {
  margin: 0 0 14px;
  font-size: 13px;
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
  background: linear-gradient(180deg, var(--color-primary) 0%, color-mix(in srgb, var(--color-primary) 82%, #000) 100%);
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

:global(.el-dialog.dp-add-target-dialog.repository-add-dialog),
:global(.dp-add-target-dialog.repository-add-dialog .el-dialog) {
  max-width: calc(100vw - 32px);
  max-height: 84vh;
  display: flex;
  flex-direction: column;
  margin-top: 8vh !important;
}

:global(.el-dialog.dp-add-target-dialog.repository-add-dialog .el-dialog__body),
:global(.dp-add-target-dialog.repository-add-dialog .el-dialog__body) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 24px 0;
}

:global(.el-dialog.dp-add-target-dialog.repository-add-dialog .el-dialog__footer),
:global(.dp-add-target-dialog.repository-add-dialog .el-dialog__footer) {
  display: none !important;
  padding: 0 !important;
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
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

.repository-add-layout--embedded {
  display: block;
}

.repository-add-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.repository-add-main--embedded {
  gap: 0;
}

.repository-add-main--embedded :deep(.resource-add-fullscreen--embedded) {
  position: static !important;
  inset: auto !important;
  z-index: auto !important;
  display: block !important;
  width: 100% !important;
  height: auto !important;
  min-height: 0 !important;
  overflow: visible !important;
  background: transparent !important;
}

.repository-add-main--embedded :deep(.resource-add-fullscreen--embedded .fullscreen-form-page) {
  display: block !important;
  width: 100% !important;
  height: auto !important;
  min-height: 0 !important;
  padding: 0 !important;
  overflow: visible !important;
  background: transparent !important;
}

.repository-add-main--embedded :deep(.resource-add-fullscreen--embedded .fullscreen-form-layout) {
  display: block;
  width: 100%;
  min-height: 0;
}

.repository-add-main--embedded :deep(.resource-add-fullscreen--embedded .fullscreen-form-main) {
  min-height: 0 !important;
  overflow: visible !important;
  padding-right: 0 !important;
  max-height: none !important;
  border: 0 !important;
  box-shadow: none !important;
}

.repository-add-main--embedded :deep(.resource-add-fullscreen--embedded .fullscreen-form-step-stack) {
  width: 100%;
}

.repository-add-main--embedded :deep(.resource-add-fullscreen--embedded .fullscreen-form-main--wizard) {
  height: auto !important;
  overflow: visible !important;
  padding-bottom: 0 !important;
  scroll-padding: 0 !important;
}

.repository-add-main--embedded :deep(.resource-add-fullscreen--embedded .fullscreen-form-footer) {
  position: sticky;
  bottom: 0;
  z-index: 2;
  margin: 16px -24px 0;
  padding: 10px 24px 0;
  border-top: 1px solid rgba(226, 232, 240, 0.95);
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 -8px 18px rgba(15, 23, 42, 0.05);
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
  background: linear-gradient(180deg, var(--color-primary) 0%, color-mix(in srgb, var(--color-primary) 82%, #000) 100%);
  border-color: color-mix(in srgb, var(--color-primary) 82%, #000);
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
  background: var(--color-primary);
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
  background: linear-gradient(180deg, var(--color-primary) 0%, color-mix(in srgb, var(--color-primary) 82%, #000) 100%);
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
  border-color: color-mix(in srgb, var(--color-primary) 38%, transparent);
  box-shadow: 0 8px 16px color-mix(in srgb, var(--color-primary) 8%, transparent);
}

.repository-platform-btn.is-active,
.repository-region-btn.is-active {
  border-color: color-mix(in srgb, var(--color-primary) 82%, #000);
  background: linear-gradient(180deg, color-mix(in srgb, var(--color-primary) 14%, #fff) 0%, color-mix(in srgb, var(--color-primary) 8%, #fff) 100%);
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
  color: color-mix(in srgb, var(--color-primary) 82%, #000);
  background: color-mix(in srgb, var(--color-primary) 8%, #fff);
  border: 1px solid color-mix(in srgb, var(--color-primary) 18%, #fff);
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
  background: linear-gradient(135deg, color-mix(in srgb, var(--color-primary) 8%, #fff) 0%, rgba(236, 253, 245, 0.9) 100%);
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
  color: var(--color-primary);
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid color-mix(in srgb, var(--color-primary) 24%, #fff);
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
  .target-picker-grid--filter-policy,
  .target-picker-grid--with-nas-mode,
  .target-picker-grid--row,
  .target-dir-card,
  .create-recovery-plan-grid {
    grid-template-columns: 1fr;
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

  .create-confirm-card__meta {
    grid-template-columns: 1fr;
  }

  .create-confirm-summary-grid {
    grid-template-columns: 1fr;
  }

  .policy-dir-card__picker,
  .policy-batch-grid,
  .create-policy-dialog .compression-radio-group,
  .create-policy-dialog .cron-row {
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

.dp-wizard-pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
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
  --create-backup-primary: var(--color-primary);
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  border: 1px solid rgba(203, 213, 225, 0.9);
  border-radius: 8px;
  border-color: color-mix(in srgb, var(--create-backup-primary) 55%, transparent);
  box-shadow:
    inset 3px 0 0 color-mix(in srgb, var(--create-backup-primary) 85%, transparent),
    0 8px 20px rgba(15, 23, 42, 0.04);
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

.dp-wizard-scroll-card {
  display: flex;
  flex-direction: column;
  max-height: clamp(280px, 58vh, 520px);
  overflow: hidden;
}

.policy-step .policy-pick-group {
  max-height: clamp(180px, 32vh, 280px);
  overflow-y: auto;
  padding-right: 4px;
}

.dp-flow-card__sheen {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background: linear-gradient(125deg, transparent 58%, rgba(255, 255, 255, 0.2) 73%, transparent 88%);
  opacity: 0;
  transition: opacity 0.22s ease;
}

.dp-flow-card > * {
  position: relative;
  z-index: 1;
}

.dp-flow-card__pill {
  position: absolute;
  top: 12px;
  right: 14px;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 9px 3px 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.01em;
  color: var(--dp-hbr-primary-deep);
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid rgba(203, 213, 225, 0.92);
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.06);
  transition:
    color 0.18s ease,
    background 0.18s ease,
    border-color 0.18s ease,
    box-shadow 0.18s ease;
}

.dp-flow-card__pill--idle {
  color: rgb(148 163 184);
  background: rgba(248, 250, 252, 0.9);
  border-color: rgba(226, 232, 240, 0.95);
  box-shadow: none;
  font-weight: 500;
}

.dp-flow-card__icon {
  position: relative;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  margin-top: 0;
  border-radius: 12px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(248, 250, 252, 0.96) 100%);
  color: rgb(51 65 85);
  border: 1px solid rgba(203, 213, 225, 0.95);
  box-shadow:
    0 1px 2px rgba(15, 23, 42, 0.05),
    inset 0 1px 0 rgba(255, 255, 255, 0.78);
  transition:
    background 0.18s ease,
    color 0.18s ease,
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    transform 0.18s ease;
}

@media (max-width: 639.98px) {
  .dp-flow-card__pill > span {
    display: none;
  }

  .dp-flow-card__pill {
    padding: 3px;
    width: 22px;
    height: 22px;
    justify-content: center;
  }

  .dp-flow-card__pill--idle {
    display: none;
  }
}

.dp-flow-card__title {
  margin-top: 0;
  font-size: 1rem;
  font-weight: 600;
  color: rgb(15 23 42);
  margin-bottom: 5px;
  line-height: 1.3;
}

.dp-flow-card__desc {
  margin: 0;
  font-size: 0.8125rem;
  line-height: 1.5;
  color: rgb(100 116 139);
}

.dp-flow-card__meta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  padding: 4px 9px 4px 7px;
  border-radius: 999px;
  background: rgba(248, 250, 252, 0.92);
  border: 1px solid rgba(226, 232, 240, 0.95);
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
  background: rgb(148 163 184);
  transition: background 0.18s ease;
}

.dp-flow-card__meta-text {
  line-height: 1.2;
}

.dp-flow-card__rail {
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 0;
  height: 3px;
  border-radius: 999px 999px 0 0;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--color-primary) 12%, transparent), transparent);
  opacity: 0;
  transition: opacity 0.18s ease, background 0.18s ease;
}

.dp-flow-card:not(.dp-flow-card--active):hover {
  border-color: color-mix(in srgb, var(--color-primary) 38%, transparent);
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 8px 16px color-mix(in srgb, var(--color-primary) 8%, transparent);
  transform: translateY(-1px);
}

.dp-flow-card:not(.dp-flow-card--active):hover .dp-flow-card__icon {
  color: var(--dp-hbr-primary-deep);
  border-color: color-mix(in srgb, var(--color-primary) 38%, transparent);
  box-shadow:
    0 6px 12px color-mix(in srgb, var(--color-primary) 8%, transparent),
    inset 0 1px 0 rgba(255, 255, 255, 0.86);
  transform: translateY(-1px);
}

.dp-flow-card:not(.dp-flow-card--active):hover .dp-flow-card__rail {
  opacity: 1;
}

.dp-flow-card:not(.dp-flow-card--active):hover .dp-flow-card__pill--idle {
  color: var(--dp-hbr-primary-deep);
  border-color: color-mix(in srgb, var(--color-primary) 20%, transparent);
}

.dp-flow-card--active {
  border-color: var(--dp-hbr-primary-deep);
  background: linear-gradient(180deg, var(--dp-hbr-primary-tint-strong) 0%, var(--dp-hbr-primary-tint) 100%);
  box-shadow:
    0 0 0 1px color-mix(in srgb, var(--dp-hbr-primary-deep) 8%, transparent) inset,
    0 8px 18px color-mix(in srgb, var(--color-primary) 12%, transparent);
}

.dp-flow-card--active .dp-flow-card__sheen {
  opacity: 1;
}

.dp-flow-card--active .dp-flow-card__title {
  color: rgb(15 23 42);
}

.dp-flow-card--active .dp-flow-card__desc {
  color: rgb(71 85 105);
}

.dp-flow-card--active .dp-flow-card__icon {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, var(--dp-hbr-primary-tint) 100%);
  color: var(--dp-hbr-primary-deep);
  border-color: color-mix(in srgb, var(--color-primary) 28%, #fff);
  box-shadow:
    0 8px 16px color-mix(in srgb, var(--color-primary) 10%, transparent),
    inset 0 1px 0 rgba(255, 255, 255, 0.92);
}

.dp-flow-card--active .dp-flow-card__meta {
  background: rgba(255, 255, 255, 0.84);
  border-color: color-mix(in srgb, var(--color-primary) 26%, #fff);
  color: var(--dp-hbr-primary-deep);
}

.dp-flow-card--active .dp-flow-card__meta-dot {
  background: var(--color-primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-primary) 16%, transparent);
}

.dp-flow-card--active .dp-flow-card__rail {
  opacity: 1;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--color-primary) 72%, transparent), transparent);
}

.dp-flow-card--active .dp-flow-card__pill {
  background: rgba(255, 255, 255, 0.98);
  border-color: color-mix(in srgb, var(--color-primary) 26%, #fff);
  color: var(--dp-hbr-primary-deep);
  box-shadow: 0 4px 10px color-mix(in srgb, var(--color-primary) 10%, transparent);
}

.dp-flow-card--active:hover {
  border-color: var(--dp-hbr-primary-deep);
  background: linear-gradient(180deg, var(--dp-hbr-primary-tint-strong) 0%, rgba(255, 255, 255, 0.98) 100%);
  box-shadow:
    0 0 0 1px color-mix(in srgb, var(--dp-hbr-primary-deep) 8%, transparent) inset,
    0 10px 22px color-mix(in srgb, var(--color-primary) 14%, transparent);
}

.dp-flow-steps-connector {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  padding: 0.2rem 0.25rem;
  min-height: 2.5rem;
}

.dp-flow-steps-connector__rail {
  position: relative;
  height: 2px;
  width: 1.1rem;
  border-radius: 2px;
  flex-shrink: 0;
  overflow: hidden;
}

.dp-flow-steps-connector__rail--left {
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--color-primary) 35%, transparent));
}

.dp-flow-steps-connector__rail--right {
  background: linear-gradient(90deg, color-mix(in srgb, var(--color-primary) 35%, transparent), transparent);
}

.dp-flow-steps-connector__rail-pulse {
  position: absolute;
  top: 0;
  left: -40%;
  width: 40%;
  height: 100%;
  background: linear-gradient(
    90deg,
    transparent,
    rgba(255, 255, 255, 0.95),
    transparent
  );
  animation: dp-rail-pulse 2.6s ease-in-out infinite;
}

.dp-flow-steps-connector__rail--right .dp-flow-steps-connector__rail-pulse {
  animation-delay: 0.2s;
}

@keyframes dp-rail-pulse {
  0% {
    transform: translateX(0%);
    opacity: 0;
  }
  20% {
    opacity: 1;
  }
  85% {
    opacity: 0.85;
  }
  100% {
    transform: translateX(360%);
    opacity: 0;
  }
}

.dp-flow-steps-connector__badge {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  margin: 0 2px;
  border-radius: 9999px;
  background: linear-gradient(160deg, var(--color-card-bg, #ffffff) 0%, var(--color-grey-1, #eef3f9) 100%);
  border: 1px solid color-mix(in srgb, var(--color-primary) 30%, transparent);
  box-shadow:
    0 4px 12px color-mix(in srgb, var(--dp-hbr-primary-deep) 14%, transparent),
    inset 0 1px 0 rgba(255, 255, 255, 0.95);
}

.dp-flow-steps-connector__badge-ring {
  position: absolute;
  inset: -4px;
  border-radius: 9999px;
  border: 1px solid color-mix(in srgb, var(--color-primary) 25%, transparent);
  opacity: 0;
  animation: dp-badge-ring 2.4s ease-in-out infinite;
  pointer-events: none;
}

@keyframes dp-badge-ring {
  0% {
    transform: scale(0.85);
    opacity: 0;
  }
  50% {
    transform: scale(1);
    opacity: 0.6;
  }
  100% {
    transform: scale(1.15);
    opacity: 0;
  }
}

.dp-flow-steps-connector__icon {
  position: relative;
  color: var(--dp-hbr-blue-deep);
  opacity: 0.95;
  filter: drop-shadow(0 1px 1px color-mix(in srgb, var(--dp-hbr-primary-deep) 18%, transparent));
  animation: dp-chev-shift 2.4s ease-in-out infinite;
}

@keyframes dp-chev-shift {
  0%, 100% {
    transform: translateX(0);
  }
  50% {
    transform: translateX(2px);
  }
}

@media (min-width: 640px) {
  .dp-flow-steps-connector {
    min-height: auto;
    align-self: center;
    padding: 0 8px;
  }

  .dp-flow-steps-connector__rail {
    width: 1.6rem;
  }
}

@media (max-width: 639.98px) {
  .dp-flow-steps-connector {
    width: 100%;
    flex-direction: column;
    padding: 4px 0;
    min-height: auto;
  }

  .dp-flow-steps-connector__rail {
    width: 2px;
    height: 12px;
  }

  .dp-flow-steps-connector__rail--left {
    background: linear-gradient(180deg, transparent, color-mix(in srgb, var(--color-primary) 35%, transparent));
  }

  .dp-flow-steps-connector__rail--right {
    background: linear-gradient(180deg, color-mix(in srgb, var(--color-primary) 35%, transparent), transparent);
  }

  .dp-flow-steps-connector__rail-pulse {
    left: 0;
    top: -50%;
    width: 100%;
    height: 50%;
    background: linear-gradient(
      180deg,
      transparent,
      rgba(255, 255, 255, 0.95),
      transparent
    );
    animation-name: dp-rail-pulse-v;
  }

  @keyframes dp-rail-pulse-v {
    0% {
      transform: translateY(0%);
      opacity: 0;
    }
    20% {
      opacity: 1;
    }
    85% {
      opacity: 0.85;
    }
    100% {
      transform: translateY(360%);
      opacity: 0;
    }
  }

  .dp-flow-steps-connector__icon {
    transform: rotate(90deg);
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

@media (prefers-reduced-motion: reduce) {
  .dp-flow-steps-connector__rail-pulse,
  .dp-flow-steps-connector__badge-ring,
  .dp-flow-steps-connector__icon,
  .target-validation-status__spinner,
  .target-connection-result__spinner {
    animation: none;
  }
}

.dp-flow-toolbar__actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.dp-flow-search {
  flex: 1 1 auto;
  min-width: 140px;
  max-width: 280px;
  width: auto;
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

.dp-flow-type-pill {
  display: inline-flex;
  max-width: 100%;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border: 1px solid transparent;
  border-radius: 999px;
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

.restore-status-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 3px 6px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  transition: background 0.18s ease;
}

.restore-status-trigger:hover {
  background: rgb(248 250 252);
}

.restore-status-trigger:disabled {
  cursor: not-allowed;
  opacity: 0.48;
}

.restore-status-trigger__total {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.restore-task-filter-panel {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 12px;
  border-radius: var(--radius-card);
  border: 1px solid rgba(226, 232, 240, 0.95);
  background: rgb(248 250 252);
}

.restore-task-filter-panel__search {
  flex: 1 1 220px;
  min-width: 180px;
}

.restore-task-filter-panel__mode {
  width: 150px;
}

.restore-task-filter-panel__number {
  width: 120px;
}

.restore-task-filter-panel__range {
  flex: 1 1 260px;
  min-width: 240px;
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
.add-source-type-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.add-source-type-card {
  display: flex;
  position: relative;
  align-items: stretch;
  width: 100%;
  height: 100%;
  min-height: 76px;
  padding: 14px 42px 14px 14px;
  border-radius: 12px;
  border: 1.5px solid var(--color-border, #e9e9ef);
  background: var(--color-card-bg, #fff);
  text-align: left;
  cursor: pointer;
  box-shadow: none;
  transition: border-color 0.15s ease, background 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
}
.add-source-type-card:hover {
  border-color: color-mix(in srgb, var(--color-primary, #6d5ef6) 38%, var(--color-border, #e9e9ef));
  background: #fff;
  box-shadow: 0 8px 18px -16px rgba(28, 28, 38, 0.35);
  transform: translateY(-1px);
}
.add-source-type-card.is-active {
  border-color: var(--color-primary, #6d5ef6);
  background: var(--color-primary-light, #f2f0fe);
  color: var(--color-primary-hover, #5546d8);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-primary, #6d5ef6) 7%, transparent);
}
.add-source-type-card__indicator {
  position: absolute;
  top: 16px;
  right: 14px;
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-border, #e9e9ef);
  border-radius: 50%;
  background: transparent;
}
.add-source-type-card__indicator::after {
  position: absolute;
  inset: 3px;
  border-radius: 50%;
  background: transparent;
  content: '';
}
.add-source-type-card.is-active .add-source-type-card__indicator {
  border-color: var(--color-primary, #6d5ef6);
}
.add-source-type-card.is-active .add-source-type-card__indicator::after {
  background: var(--color-primary, #6d5ef6);
}
.add-source-type-card__inner {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1 1 auto;
  min-width: 0;
  color: var(--color-text-primary, #3a3a48);
}
.add-source-type-card__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: var(--color-primary-light, #f2f0fe);
  color: var(--color-primary-hover, #5546d8);
  transition: border-color 0.15s ease, background 0.15s ease, color 0.15s ease;
}
.add-source-type-card:hover .add-source-type-card__icon,
.add-source-type-card.is-active .add-source-type-card__icon {
  border-color: var(--color-primary-disabled-bg, #dcd5fb);
  background: #fff;
  color: var(--color-primary-hover, #5546d8);
}
.add-source-type-card__text {
  display: flex;
  min-width: 0;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
}
.add-source-type-card__name {
  color: var(--color-text-primary, #3a3a48);
  font-size: 14px;
  font-weight: 700;
}
.add-source-type-card__sub {
  color: var(--color-text-secondary, #70707e);
  font-size: 12px;
  line-height: 1.5;
}
.add-source-type-card.is-active .add-source-type-card__name {
  color: var(--color-primary-hover, #5546d8);
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
  color: color-mix(in srgb, var(--color-primary) 82%, #000);
  font-weight: 500;
}
.source-underline-tabs :deep(.el-tabs__active-bar) {
  height: 3px;
  border-radius: 2px 2px 0 0;
  background: linear-gradient(180deg, var(--color-primary) 0%, color-mix(in srgb, var(--color-primary) 82%, #000) 100%);
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
  background: linear-gradient(180deg, color-mix(in srgb, var(--color-primary) 6%, #fff) 0%, color-mix(in srgb, var(--color-primary) 14%, #fff) 100%);
  color: var(--color-primary);
  border: 1px solid color-mix(in srgb, var(--color-primary) 14%, transparent);
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
  background: linear-gradient(180deg, var(--color-primary) 0%, color-mix(in srgb, var(--color-primary) 82%, #000) 100%);
  border-color: color-mix(in srgb, var(--color-primary) 82%, #000);
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
  background: var(--color-primary);
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
  background: linear-gradient(180deg, var(--color-primary) 0%, color-mix(in srgb, var(--color-primary) 82%, #000) 100%);
  border-color: color-mix(in srgb, var(--color-primary) 82%, #000);
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
  background: var(--color-primary);
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
  background: linear-gradient(180deg, var(--color-primary) 0%, color-mix(in srgb, var(--color-primary) 82%, #000) 100%);
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
  color: var(--color-primary);
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
  padding: 16px !important;
  border-radius: 10px !important;
  border-color: rgba(203, 213, 225, 0.95) !important;
  background: rgba(255, 255, 255, 0.92);
  transition: all 0.18s ease;
}
.add-nas-protocol-card:hover {
  border-color: color-mix(in srgb, var(--color-primary) 38%, transparent) !important;
  box-shadow: 0 8px 16px color-mix(in srgb, var(--color-primary) 8%, transparent);
}
.add-nas-protocol-card.is-checked {
  border-color: color-mix(in srgb, var(--color-primary) 82%, #000) !important;
  background: linear-gradient(180deg, color-mix(in srgb, var(--color-primary) 14%, #fff) 0%, color-mix(in srgb, var(--color-primary) 8%, #fff) 100%);
}
.add-nas-protocol-card__inner {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  color: rgb(30 41 59);
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
  background: color-mix(in srgb, var(--color-primary) 8%, #fff);
  color: color-mix(in srgb, var(--color-primary) 82%, #000);
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
  color: color-mix(in srgb, var(--color-primary) 82%, #000);
  background: color-mix(in srgb, var(--color-primary) 8%, #fff);
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
  border-color: var(--color-warning-border) !important;
  border-radius: 6px !important;
  background: var(--color-warning-light) !important;
  color: var(--color-warning) !important;
}
.add-nas-proxy-alert :deep(.el-alert__content) {
  padding: 0 2px;
}
.add-nas-proxy-alert :deep(.el-alert__description),
.add-nas-proxy-alert :deep(.el-alert__title),
.add-nas-proxy-alert :deep(.el-alert__icon) {
  color: var(--color-warning);
}
.add-nas-proxy-alert__list {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.6;
}
.add-nas-proxy-alert__list li + li {
  margin-top: 6px;
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
  background: var(--color-primary);
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
  border: 1px solid color-mix(in srgb, var(--color-primary) 28%, transparent);
  background: color-mix(in srgb, var(--color-primary) 8%, #fff);
  color: color-mix(in srgb, var(--color-primary) 82%, #000);
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

.step0-expand-column :deep(.cell) {
  padding: 0 8px;
}

.step0-expand-detail {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) auto minmax(280px, 0.9fr);
  gap: 12px;
  align-items: stretch;
}

.step0-expand-detail__tree,
.step0-expand-detail__selected {
  display: flex;
  min-width: 0;
  min-height: 280px;
  flex-direction: column;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
  padding: 12px;
}

.step0-expand-detail__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.step0-expand-detail__transfer {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 40px;
}
</style>
