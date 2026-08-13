<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import type { ElTree } from 'element-plus'
import { ArrowLeft, ChevronDown, ChevronRight, HardDrive, Folder, FolderTree, Plus, RefreshCw } from 'lucide-vue-next'
import { apiErrorMessageI18n } from '../../lib/api'
import {
  createStorageRepository,
  storageRepositoryCreateErrorMessage,
  type StorageRepository,
} from '../../lib/storageRepositoryApi'
import { listBackupSourceDirectories, type BackupSourceDirectoryEntry, type BackupSourceDirectoryList } from '../../lib/sourceApi'
import { shouldAutoExpandRefreshedDirectory } from '../../lib/backupSourceDirectoryTree'
import { listAllNodes } from '../../lib/nodeApi'
import { proxyAgentsRoute } from '../../lib/nodeDeployRoutes'
import type { ApiNode } from '../../types/node'
import { useInlineFormValidation } from '../../composables/useInlineFormValidation'

const { t } = useI18n()
const router = useRouter()
const props = withDefaults(defineProps<{
  embedded?: boolean
}>(), {
  embedded: false,
})
const emit = defineEmits<{
  cancel: []
  created: [payload: StorageRepository]
}>()

/* ---------- form state ---------- */
const busy = ref(false)
const pageRef = ref<HTMLElement | null>(null)
const { clear: clearFieldError, errors, validate: validateInline } = useInlineFormValidation(pageRef)
const proxyNodeId = ref<number | undefined>(undefined)
const proxyNodeDir = ref('')
const repoName = ref('')
const quota = ref(0)
const enableQuotaAlert = ref(false)
const quotaAlertThreshold = ref<number | undefined>(undefined)
const useTreeSelect = ref(true)
const proxyDirTreeRef = ref<InstanceType<typeof ElTree>>()
const proxyNodesRefreshing = ref(false)
const proxyNodes = ref<ApiNode[]>([])

const availableProxyNodes = computed(() =>
  proxyNodes.value.filter((node) => node.role === 'proxy' && node.availability === 'online'),
)
const selectedProxyNodeName = computed(() =>
  availableProxyNodes.value.find((node) => node.id === proxyNodeId.value)?.name || '',
)
const dirSelectModeLabel = computed(() =>
  useTreeSelect.value ? t('repositoriesPage.dirSelectTree') : t('repositoriesPage.dirSelectInput'),
)
const managedRepositoryPathPreview = computed(() => {
  const base = proxyNodeDir.value.trim().replace(/\/+$/, '')
  return base ? `${base}/hfl-repo-<repository-id>` : ''
})
const validQuotaAlertThreshold = computed(() => {
  const value = Number(quotaAlertThreshold.value || 0)
  return value >= 1 && value <= 100
})

/* ---------- tree data for directory selection ----------
 */
type TreeNode = {
  id: string
  label: string
  children?: TreeNode[]
  pathLabel?: string
  isLeaf?: boolean
  disabled?: boolean
  loadingChildren?: boolean
  loadError?: string
  loaded?: boolean
}

type FetchRequestInit = Parameters<typeof fetch>[1]

function proxyDirLabel(path: string, fallback?: string) {
  if (fallback) return fallback
  if (!path || path === '/') return '/'
  const parts = path.split('/').filter(Boolean)
  return parts[parts.length - 1] || path
}

function createChildTreeNodeFromEntry(entry: BackupSourceDirectoryEntry): Readonly<TreeNode> {
  const path = (entry.path || '').trim()
  return Object.freeze({
    id: path,
    label: proxyDirLabel(path, entry.label),
    pathLabel: path,
    isLeaf: false,
    disabled: false,
    loaded: false,
  })
}

const treeData = ref<TreeNode[]>([])
const rootLoaded = ref(false)
const treeExpanded = ref(true)
const treeLoading = ref(false)
const treeError = ref('')
const treeRemountKey = ref(0)
const refreshingProxyDirectoryByKey = reactive<Record<string, boolean>>({})
const proxyDirectoryExpansionRevisionByKey = new Map<string, number>()

function collectTreeNodeIds(nodes: TreeNode[]): string[] {
  return nodes.flatMap((node) => [node.id, ...(node.children?.length ? collectTreeNodeIds(node.children) : [])])
}

const treeNodeIds = computed(() => collectTreeNodeIds(treeData.value))
const currentTreeNodeKey = computed(() =>
  treeNodeIds.value.includes(proxyNodeDir.value) ? proxyNodeDir.value : '',
)
const checkedTreeNodeKeys = computed(() =>
  treeNodeIds.value.includes(proxyNodeDir.value) ? [proxyNodeDir.value] : [],
)
const rootHasChildren = computed(() => treeData.value.length > 0)

/* ---------- auto-generate repository name ---------- */
function generateRepoName(nodeName: string): string {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  const hour = String(now.getHours()).padStart(2, '0')
  const minute = String(now.getMinutes()).padStart(2, '0')
  const timestamp = `${year}${month}${day}${hour}${minute}`
  return `${nodeName}-repo-${timestamp}`
}

/* ---------- watch proxy node change to auto-generate repo name and dir ---------- */
watch(proxyNodeId, async (newNodeId) => {
  proxyNodeDir.value = ''
  treeData.value = []
  rootLoaded.value = false
  treeError.value = ''
  treeRemountKey.value += 1
  if (!newNodeId) {
    return
  }
  const node = availableProxyNodes.value.find((n) => n.id === newNodeId)
  if (node && !repoName.value) {
    repoName.value = generateRepoName(node.name)
  }
  await loadRootDirectory(newNodeId)
})

watch(checkedTreeNodeKeys, (keys) => {
  proxyDirTreeRef.value?.setCheckedKeys(keys)
})

watch(useTreeSelect, (enabled) => {
  if (!enabled) {
    proxyNodeDir.value = ''
  }
  treeData.value = []
  rootLoaded.value = false
  treeError.value = ''
  treeRemountKey.value += 1
  if (enabled && proxyNodeId.value) {
    void loadRootDirectory(proxyNodeId.value)
  }
})

async function loadRootDirectory(nodeId: number): Promise<void> {
  treeLoading.value = true
  treeError.value = ''
  try {
    const result = await fetchProxyDirectoryListing(nodeId, '/')
    applyProxyRootToTree(result)
  } catch (err) {
    treeError.value = apiErrorMessageI18n(err, t, t('repositoriesPage.dirTreeNodeLoadFailed'))
    ElMessage.error({ message: treeError.value, grouping: true })
  } finally {
    treeLoading.value = false
  }
}

async function loadProxyRootDirectories(nodeId = proxyNodeId.value) {
  if (!nodeId) return
  treeError.value = ''
  await loadRootDirectory(nodeId)
}

async function fetchProxyDirectoryListing(
  nodeId: number,
  path?: string,
  init?: FetchRequestInit,
): Promise<BackupSourceDirectoryList> {
  return listBackupSourceDirectories({
    source_id: `proxy:${nodeId}`,
    ...(path !== undefined ? { path } : {}),
    timeout: 10,
    limit: 200,
  }, init)
}

function normalizeProxyDirectoryChildren(parentPath: string, entries: BackupSourceDirectoryEntry[]): BackupSourceDirectoryEntry[] {
  const parent = (parentPath || '').trim() || '/'
  const seen = new Set<string>()
  const out: BackupSourceDirectoryEntry[] = []
  for (const entry of entries || []) {
    if (!entry) continue
    if (entry.is_dir === false) continue
    const entryPath = (entry.path || '').trim()
    if (!entryPath) continue
    if (entryPath === parent) continue
    if (entry.label === '.') continue

    const normalizedParent = parent === '/' ? '/' : parent.replace(/\/+$/, '')
    if (normalizedParent === '/') {
      if (!entryPath.startsWith('/') || entryPath.slice(1).includes('/')) continue
    } else {
      const prefix = `${normalizedParent}/`
      if (!entryPath.startsWith(prefix)) continue
      if (entryPath.slice(prefix.length).includes('/')) continue
    }

    if (seen.has(entryPath)) continue
    seen.add(entryPath)
    out.push(entry)
  }
  return out
}

function applyProxyRootToTree(result: BackupSourceDirectoryList) {
  const childEntries = normalizeProxyDirectoryChildren('/', result.entries)
  treeData.value = childEntries.map((e) => ({ ...createChildTreeNodeFromEntry(e) }))
  rootLoaded.value = true
  treeRemountKey.value += 1
}

function applyProxyDirectoryResult(path: string, entries: BackupSourceDirectoryEntry[]) {
  const target = findTreeNode(treeData.value, path)
  if (!target) return
  target.loadError = ''
  const realEntries = normalizeProxyDirectoryChildren(path, entries)
  target.children = realEntries.map((e) => ({ ...createChildTreeNodeFromEntry(e) }))
}

function markProxyDirectoryLoading(path: string, loading: boolean, error?: string) {
  const target = findTreeNode(treeData.value, path)
  if (!target) return
  target.loadingChildren = loading
  if (error !== undefined) target.loadError = error
}

function findTreeNode(nodes: TreeNode[], path: string): TreeNode | null {
  for (const node of nodes) {
    if (node.id === path) return node
    if (node.children?.length) {
      const found = findTreeNode(node.children, path)
      if (found) return found
    }
  }
  return null
}

function proxyDirectoryRefreshKey(nodeId: number, path: string) {
  return `proxy:${nodeId}:${path}`
}

function isProxyDirectoryRefreshing(path: string) {
  const nodeId = proxyNodeId.value
  return Boolean(nodeId && refreshingProxyDirectoryByKey[proxyDirectoryRefreshKey(nodeId, path)])
}

function onProxyDirectoryExpansionChange(data: TreeNode) {
  const nodeId = proxyNodeId.value
  if (!nodeId || !data.id) return
  const refreshKey = proxyDirectoryRefreshKey(nodeId, data.id)
  proxyDirectoryExpansionRevisionByKey.set(
    refreshKey,
    (proxyDirectoryExpansionRevisionByKey.get(refreshKey) ?? 0) + 1,
  )
}

async function refreshProxyRootDirectory() {
  const nodeId = proxyNodeId.value
  if (!nodeId) return
  const refreshKey = proxyDirectoryRefreshKey(nodeId, '/')
  if (refreshingProxyDirectoryByKey[refreshKey]) return
  const wasExpanded = treeExpanded.value
  const expansionRevisionAtStart = proxyDirectoryExpansionRevisionByKey.get(refreshKey) ?? 0

  refreshingProxyDirectoryByKey[refreshKey] = true
  try {
    const result = await fetchProxyDirectoryListing(nodeId, '/', { cache: 'no-store' })
    if (proxyNodeId.value !== nodeId) return
    applyProxyRootToTree(result)
    await nextTick()
    proxyDirTreeRef.value?.setCheckedKeys(checkedTreeNodeKeys.value)
    const hasChildren = treeData.value.length > 0
    if (!hasChildren) {
      treeExpanded.value = false
      ElMessage.info({
        message: t('repositoriesPage.dirTreeRefreshEmpty', { path: '/' }),
        grouping: true,
      })
      return
    }
    const expansionRevisionAfterRefresh = proxyDirectoryExpansionRevisionByKey.get(refreshKey) ?? 0
    if (shouldAutoExpandRefreshedDirectory({
      wasExpanded,
      hasChildren,
      expansionRevisionAtStart,
      expansionRevisionAfterRefresh,
    })) {
      treeExpanded.value = true
    }
    ElMessage.success({
      message: t('repositoriesPage.dirTreeRefreshSuccess', { path: '/' }),
      grouping: true,
    })
  } catch (err) {
    ElMessage.error({
      message: apiErrorMessageI18n(err, t, t('repositoriesPage.dirTreeLoadFailed')),
      grouping: true,
    })
  } finally {
    delete refreshingProxyDirectoryByKey[refreshKey]
  }
}

async function refreshProxyDirectory(data: TreeNode) {
  const nodeId = proxyNodeId.value
  const tree = proxyDirTreeRef.value
  if (!nodeId || !tree || !data.id) return
  const target = findTreeNode(treeData.value, data.id)
  if (!target) return
  const refreshKey = proxyDirectoryRefreshKey(nodeId, data.id)
  if (refreshingProxyDirectoryByKey[refreshKey]) return
  const sourceNode = tree.getNode(data.id) as unknown as { expanded?: boolean } | null
  if (!sourceNode) return
  const wasExpanded = Boolean(sourceNode.expanded)
  const expansionRevisionAtStart = proxyDirectoryExpansionRevisionByKey.get(refreshKey) ?? 0

  refreshingProxyDirectoryByKey[refreshKey] = true
  try {
    const result = await fetchProxyDirectoryListing(nodeId, data.id, { cache: 'no-store' })
    const realEntries = normalizeProxyDirectoryChildren(data.id, result.entries)
    const children = realEntries.map((entry) => ({ ...createChildTreeNodeFromEntry(entry) }))
    if (
      proxyNodeId.value !== nodeId
      || proxyDirTreeRef.value !== tree
      || findTreeNode(treeData.value, data.id) !== target
    ) return
    target.children = children
    target.loaded = true
    target.loadError = ''
    tree.updateKeyChildren(data.id, children)
    const refreshedNode = tree.getNode(data.id) as unknown as {
      loaded?: boolean
      collapse?: () => void
      expand?: () => void
      updateLeafState?: () => void
    } | null
    if (!refreshedNode) return
    refreshedNode.loaded = true
    refreshedNode.updateLeafState?.()
    await nextTick()
    tree.setCheckedKeys(checkedTreeNodeKeys.value)
    const hasChildren = children.length > 0
    if (!hasChildren) {
      refreshedNode.collapse?.()
      ElMessage.info({
        message: t('repositoriesPage.dirTreeRefreshEmpty', { path: data.id }),
        grouping: true,
      })
      return
    }
    const expansionRevisionAfterRefresh = proxyDirectoryExpansionRevisionByKey.get(refreshKey) ?? 0
    if (shouldAutoExpandRefreshedDirectory({
      wasExpanded,
      hasChildren,
      expansionRevisionAtStart,
      expansionRevisionAfterRefresh,
    })) {
      refreshedNode.expand?.()
    }
    ElMessage.success({
      message: t('repositoriesPage.dirTreeRefreshSuccess', { path: data.id }),
      grouping: true,
    })
  } catch (err) {
    const message = apiErrorMessageI18n(err, t, t('repositoriesPage.dirTreeNodeLoadFailed'))
    target.loadError = message
    ElMessage.error({ message, grouping: true })
  } finally {
    delete refreshingProxyDirectoryByKey[refreshKey]
  }
}

async function loadProxyNodes() {
  proxyNodes.value = await listAllNodes({ role: 'proxy' }).catch(() => [])
}

async function refreshProxyNodesManually() {
  proxyNodesRefreshing.value = true
  try {
    await loadProxyNodes()
    ElMessage.success({ message: t('addNasRepo.proxyRefreshSuccess'), grouping: true })
  } finally {
    proxyNodesRefreshing.value = false
  }
}

onMounted(() => {
  void loadProxyNodes()
})

/* ---------- validation ---------- */
function validateForm(): boolean {
  return validateInline([
    { field: 'repoName', message: t('repositoriesPage.errName'), valid: !!repoName.value.trim() },
    { field: 'proxyNode', message: t('repositoriesPage.errProxyNode'), valid: !!proxyNodeId.value },
    { field: 'proxyNodeDir', message: t('repositoriesPage.errProxyNodeDir'), valid: !!proxyNodeDir.value.trim() },
    { field: 'quotaThreshold', message: t('repositoriesPage.hintQuotaAlertThreshold'), valid: !enableQuotaAlert.value || validQuotaAlertThreshold.value },
  ])
}

/* ---------- submit ---------- */
async function onSubmit() {
  if (!validateForm()) return
  busy.value = true
  try {
    const created = await createStorageRepository(buildCreatePayload())
    const accepted = String(created.status || '').toLowerCase() === 'creating'
    ElMessage.success({
      message: t(accepted ? 'repositoriesPage.msgCreateAccepted' : 'repositoriesPage.msgCreated'),
      grouping: true,
    })
    if (props.embedded) {
      emit('created', created)
    } else {
      router.push({
        path: '/node/repositories',
        query: {
          tab: 'proxy_fs',
          ...(accepted && created.active_create_task?.task_uuid
            ? {
                pending_create_id: String(created.id),
                pending_create_task: created.active_create_task.task_uuid,
              }
            : {}),
        },
      })
    }
  } catch (err) {
    ElMessage.error({ message: storageRepositoryCreateErrorMessage(err, t), grouping: true })
  } finally {
    busy.value = false
  }
}

function buildCreatePayload() {
  return {
    name: repoName.value.trim(),
    repo_type: 'proxy_fs',
    bind_node_type: 'proxy',
    bind_node_id: proxyNodeId.value,
    config: {
      proxy_node_base_dir: proxyNodeDir.value.trim(),
      quota_gb: quota.value || 0,
      quota_alert_enabled: enableQuotaAlert.value,
      quota_alert_threshold: enableQuotaAlert.value ? Number(quotaAlertThreshold.value || 0) : 0,
    },
  } as const
}

function handleBack() {
  if (props.embedded) {
    emit('cancel')
    return
  }
  router.push({ path: '/node/repositories', query: { tab: 'proxy_fs' } })
}

function openProxyDeploy() {
  const { href } = router.resolve(proxyAgentsRoute())
  window.open(href, '_blank', 'noopener,noreferrer')
}

/* ---------- tree select handler ---------- */

async function onLoadTreeNode(
  node: { level: number; data?: TreeNode | TreeNode[] },
  resolve: (data: TreeNode[]) => void,
) {
  const data = node.data

  if (node.level === 0 || !data || Array.isArray(data)) {
    resolve(treeData.value)
    return
  }

  const nodeId = proxyNodeId.value
  if (!nodeId) {
    resolve([])
    return
  }

  const targetNode = findTreeNode(treeData.value, data.id)
  if (!targetNode) {
    resolve([])
    return
  }

  if (targetNode.loaded) {
    resolve(targetNode.children ?? [])
    return
  }

  targetNode.loaded = true
  markProxyDirectoryLoading(targetNode.id, true)
  try {
    const result = await fetchProxyDirectoryListing(nodeId, targetNode.id)
    const realEntries = normalizeProxyDirectoryChildren(targetNode.id, result.entries)
    applyProxyDirectoryResult(targetNode.id, realEntries)
    resolve(targetNode.children ?? [])
  } catch (err) {
    targetNode.loaded = false
    const message = apiErrorMessageI18n(err, t, t('repositoriesPage.dirTreeNodeLoadFailed'))
    markProxyDirectoryLoading(targetNode.id, false, message)
    ElMessage.error({ message: message, grouping: true })
    resolve([])
  } finally {
    markProxyDirectoryLoading(targetNode.id, false)
  }
}

function retryTreeNodeLoad(data: TreeNode) {
  if (!data) return
  const target = findTreeNode(treeData.value, data.id)
  if (target) target.loaded = false
  void onLoadTreeNode({ level: 1, data: target ?? data }, () => {})
}

function toggleTreeExpanded() {
  const nodeId = proxyNodeId.value
  if (!nodeId || !rootHasChildren.value) return
  const refreshKey = proxyDirectoryRefreshKey(nodeId, '/')
  proxyDirectoryExpansionRevisionByKey.set(
    refreshKey,
    (proxyDirectoryExpansionRevisionByKey.get(refreshKey) ?? 0) + 1,
  )
  treeExpanded.value = !treeExpanded.value
}

function isSelectableDir(data?: TreeNode | null) {
  return Boolean(data?.id) && data?.id !== '/'
}

function onTreeSelect(data: TreeNode | null) {
  if (!isSelectableDir(data)) {
    proxyDirTreeRef.value?.setCheckedKeys(proxyNodeDir.value ? [proxyNodeDir.value] : [])
    return
  }
  proxyNodeDir.value = data!.id
  proxyDirTreeRef.value?.setCheckedKeys([data!.id])
}

function onTreeCheckChange(data: TreeNode, checked: boolean) {
  if (!isSelectableDir(data)) {
    proxyDirTreeRef.value?.setCheckedKeys(proxyNodeDir.value ? [proxyNodeDir.value] : [])
    return
  }
  if (!checked) {
    if (proxyNodeDir.value === data.id) {
      proxyNodeDir.value = ''
      proxyDirTreeRef.value?.setCheckedKeys([])
    }
    return
  }
  proxyNodeDir.value = data.id
  proxyDirTreeRef.value?.setCheckedKeys([data.id])
}

watch(enableQuotaAlert, (enabled) => {
  if (!enabled) {
    quotaAlertThreshold.value = undefined
    return
  }
  if (!quotaAlertThreshold.value) quotaAlertThreshold.value = 80
})
</script>

<template>
  <div ref="pageRef" class="fullscreen-form-fullscreen resource-add-fullscreen" :class="{ 'resource-add-fullscreen--embedded': embedded }">
    <div class="fullscreen-form-page add-proxy-fs-page">
      <header v-if="!embedded" class="fullscreen-form-header">
        <button class="fullscreen-form-header__back" @click="handleBack">
          <ArrowLeft class="fullscreen-form-header__back-icon" :size="18" />
        </button>
        <div class="fullscreen-form-header__content">
          <h1 class="fullscreen-form-header__title">{{ t('repositoriesPage.addProxyFsPage') }}</h1>
          <p class="fullscreen-form-header__desc">{{ t('repositoriesPage.addProxyFsPageDesc') }}</p>
        </div>
      </header>

      <div class="fullscreen-form-layout">
        <div class="fullscreen-form-main add-proxy-fs-main">
          <div class="fullscreen-form-step-stack">
            <section
              id="proxy-fs-step-repo"
              data-step="0"
              class="fullscreen-form-card fullscreen-form-section add-proxy-fs-step-section"
            >
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('repositoriesPage.stepRepo') }}
              </h3>
              <ElForm label-position="top" class="fullscreen-form-el-form add-proxy-fs-form">
                <ElFormItem data-validation-field="repoName" :error="errors.repoName" :label="t('repositoriesPage.fieldRepoName')" required class="fullscreen-form-item--in-card">
                  <ElInput v-model="repoName" :placeholder="t('repositoriesPage.phRepoName')" @input="clearFieldError('repoName')" />
                  <div class="mt-1 text-xs text-[var(--color-text-tertiary)]">
                    {{ t('repositoriesPage.hintRepoNameAuto') }}
                  </div>
                </ElFormItem>

                <ElFormItem data-validation-field="proxyNode" :error="errors.proxyNode" required class="fullscreen-form-item--in-card add-proxy-fs-bind-form-item is-no-asterisk">
                  <template #label>
                    <div class="add-proxy-fs-proxy-label">
                      <span class="add-proxy-fs-proxy-label__title">
                        {{ t('repositoriesPage.fieldProxyNode') }}<span class="add-proxy-fs-proxy-label__required">*</span>
                      </span>
                      <ElButton type="primary" :icon="Plus" @click="openProxyDeploy">
                        {{ t('addNasRepo.deployProxy') }}
                      </ElButton>
                    </div>
                  </template>
                  <div class="add-proxy-fs-select-row">
                    <ElSelect
                      v-model="proxyNodeId"
                      class="add-proxy-fs-select-row__select"
                      :placeholder="t('repositoriesPage.phProxyNode')"
                      filterable
                      @change="clearFieldError('proxyNode')"
                    >
                      <ElOption
                        v-for="n in availableProxyNodes"
                        :key="n.id"
                        :value="n.id"
                        :label="n.ip_address ? `${n.name} (${n.ip_address})` : n.name"
                      />
                    </ElSelect>
                    <ElButton
                      class="hfl-refresh-button add-proxy-fs-select-row__refresh"
                      :title="t('addNasRepo.proxyRefresh')"
                      :aria-label="t('addNasRepo.proxyRefresh')"
                      :disabled="proxyNodesRefreshing"
                      @click="refreshProxyNodesManually"
                    >
                      <RefreshCw :size="16" :class="{ 'is-spinning': proxyNodesRefreshing }" />
                    </ElButton>
                  </div>
                </ElFormItem>

                <ElFormItem data-validation-field="proxyNodeDir" :error="errors.proxyNodeDir" :label="t('repositoriesPage.fieldProxyNodeBaseDir')" required class="fullscreen-form-item--in-card">
                  <div class="dir-selector-container">
                    <div class="dir-selector-toggle">
                      <button
                        class="dir-selector-tab"
                        :class="{ 'dir-selector-tab--active': useTreeSelect }"
                        type="button"
                        @click="useTreeSelect = true"
                      >
                        <FolderTree :size="14" class="mr-1" />
                        {{ t('repositoriesPage.dirSelectTree') }}
                      </button>
                      <button
                        class="dir-selector-tab"
                        :class="{ 'dir-selector-tab--active': !useTreeSelect }"
                        type="button"
                        @click="useTreeSelect = false"
                      >
                        <HardDrive :size="14" class="mr-1" />
                        {{ t('repositoriesPage.dirSelectInput') }}
                      </button>
                    </div>

                    <div v-if="useTreeSelect" class="dir-tree-select hfl-dir-tree-shell">
                      <div v-if="!proxyNodeId" class="dir-tree-select__empty hfl-dir-tree-empty">
                        {{ t('repositoriesPage.errProxyNode') }}
                      </div>
                      <div v-else-if="treeError" class="dir-tree-select__empty hfl-dir-tree-empty">
                        <div>{{ treeError }}</div>
                        <ElButton size="small" class="mt-2" @click="loadProxyRootDirectories()">
                          {{ t('repositoriesPage.dirTreeRetry') }}
                        </ElButton>
                      </div>
                      <template v-else-if="rootLoaded">
                        <div class="dir-tree-root-row hfl-dir-tree-node">
                          <button
                            type="button"
                            class="dir-tree-root-row__toggle"
                            :disabled="!rootHasChildren"
                            :aria-expanded="rootHasChildren ? treeExpanded : undefined"
                            @click="toggleTreeExpanded"
                          >
                            <component
                              :is="treeExpanded ? ChevronDown : ChevronRight"
                              v-if="rootHasChildren"
                              :size="14"
                              class="dir-tree-root-row__caret"
                            />
                            <span v-else class="dir-tree-root-row__caret" aria-hidden="true" />
                            <Folder :size="15" class="dir-tree-root-row__icon hfl-dir-tree-node__icon" />
                            <span class="dir-tree-root-row__text hfl-dir-tree-node__text">
                              <span class="dir-tree-root-row__label hfl-dir-tree-node__label">/</span>
                              <span class="dir-tree-root-row__path hfl-dir-tree-node__path">/</span>
                            </span>
                          </button>
                          <button
                            type="button"
                            class="hfl-dir-tree-node__refresh"
                            :class="{ 'is-refreshing': isProxyDirectoryRefreshing('/') }"
                            :disabled="isProxyDirectoryRefreshing('/')"
                            :aria-label="t('repositoriesPage.ariaRefreshDirectory', { path: '/' })"
                            :title="t('repositoriesPage.ariaRefreshDirectory', { path: '/' })"
                            @click.stop="refreshProxyRootDirectory"
                          >
                            <RefreshCw
                              :size="14"
                              :class="{ 'is-spinning': isProxyDirectoryRefreshing('/') }"
                            />
                          </button>
                        </div>
                        <div v-if="treeData.length === 0" class="dir-tree-empty-nested hfl-dir-tree-empty">
                          {{ t('repositoriesPage.dirTreeEmptyProxy') }}
                        </div>
                      </template>
                      <ElTree
                        v-show="treeExpanded"
                        :key="treeRemountKey"
                        ref="proxyDirTreeRef"
                        v-loading="treeLoading"
                        class="proxy-dir-tree hfl-dir-tree proxy-dir-tree--nested"
                        :data="treeData"
                        :props="{ children: 'children', label: 'label', isLeaf: 'isLeaf' }"
                        node-key="id"
                        show-checkbox
                        check-strictly
                        lazy
                        :load="onLoadTreeNode"
                        :check-on-click-node="false"
                        :expand-on-click-node="true"
                        :highlight-current="true"
                        :current-node-key="currentTreeNodeKey"
                        :default-checked-keys="checkedTreeNodeKeys"
                        @current-change="onTreeSelect"
                        @check-change="onTreeCheckChange"
                        @node-collapse="onProxyDirectoryExpansionChange"
                        @node-expand="onProxyDirectoryExpansionChange"
                      >
                        <template #default="{ data }">
                          <div class="tree-node-content hfl-dir-tree-node">
                            <Folder :size="15" class="tree-node-content__icon hfl-dir-tree-node__icon" />
                            <div class="tree-node-content__text hfl-dir-tree-node__text">
                              <span class="tree-node-content__label hfl-dir-tree-node__label">{{ data.label }}</span>
                              <span class="tree-node-content__path hfl-dir-tree-node__path">{{ data.pathLabel }}</span>
                            </div>
                            <span v-if="data.loadingChildren" class="tree-node-content__status">
                              {{ t('common.loading') }}
                            </span>
                            <ElButton
                              v-else-if="data.loadError"
                              text
                              size="small"
                              class="tree-node-content__retry"
                              @click.stop="retryTreeNodeLoad(data)"
                            >
                              {{ t('repositoriesPage.dirTreeRetry') }}
                            </ElButton>
                            <button
                              type="button"
                              class="hfl-dir-tree-node__refresh"
                              :class="{ 'is-refreshing': isProxyDirectoryRefreshing(data.id) }"
                              :disabled="isProxyDirectoryRefreshing(data.id) || data.loadingChildren"
                              :aria-label="t('repositoriesPage.ariaRefreshDirectory', { path: data.id })"
                              :title="t('repositoriesPage.ariaRefreshDirectory', { path: data.id })"
                              @click.stop="refreshProxyDirectory(data)"
                            >
                              <RefreshCw
                                :size="14"
                                :class="{ 'is-spinning': isProxyDirectoryRefreshing(data.id) }"
                              />
                            </button>
                          </div>
                          <div v-if="data.loadError" class="tree-node-content__error">
                            {{ t('repositoriesPage.dirTreeNodeLoadFailed') }}
                          </div>
                        </template>
                      </ElTree>
                      <div class="mt-2 text-xs text-[var(--color-text-tertiary)]">
                        {{ t('repositoriesPage.hintTreeSelect') }}
                      </div>
                    </div>

                    <ElInput
                      v-else
                      v-model="proxyNodeDir"
                      :placeholder="t('repositoriesPage.phProxyNodeDir')"
                      class="dir-input"
                      @input="clearFieldError('proxyNodeDir')"
                    />
                  </div>
                </ElFormItem>
              </ElForm>
            </section>

            <section
              id="proxy-fs-step-quota"
              data-step="1"
              class="fullscreen-form-card fullscreen-form-section add-proxy-fs-step-section"
            >
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('repositoriesPage.fieldQuota') }}
              </h3>
              <ElForm label-position="top" class="fullscreen-form-el-form add-proxy-fs-form">
                <div class="fullscreen-form-grid">
                  <div class="fullscreen-form-field repository-quota-field">
                    <label class="fullscreen-form-field__label repository-quota-head">{{ t('repositoriesPage.fieldQuota') }}</label>
                    <div class="repository-quota-control">
                      <div class="repository-quota-number repository-quota-input">
                        <ElInputNumber
                          v-model="quota"
                          class="repository-quota-number__input"
                          :placeholder="t('repositoriesPage.phQuota')"
                          :min="0"
                          controls-position="right"
                        />
                        <div class="repository-quota-number__suffix">GB</div>
                      </div>
                    </div>
                    <p class="fullscreen-form-field__hint">{{ t('repositoriesPage.hintQuota') }}</p>
                  </div>

                  <div data-validation-field="quotaThreshold" class="fullscreen-form-field repository-quota-field repository-quota-field--monitoring">
                    <div class="fullscreen-form-field__label repository-quota-head repository-quota-title-row">
                      <ElCheckbox v-model="enableQuotaAlert">{{ t('repositoriesPage.fieldQuotaAlert') }}</ElCheckbox>
                    </div>
                    <div class="repository-quota-control">
                      <div class="repository-quota-number repository-quota-input">
                        <ElInputNumber
                          v-model="quotaAlertThreshold"
                          class="repository-quota-number__input"
                          :min="1"
                          :max="100"
                          :disabled="!enableQuotaAlert"
                          controls-position="right"
                          @change="clearFieldError('quotaThreshold')"
                        />
                        <div class="repository-quota-number__suffix">%</div>
                      </div>
                    </div>
                      <p class="fullscreen-form-field__hint">{{ t('repositoriesPage.hintQuotaAlertThreshold') }}</p>
                      <p v-if="errors.quotaThreshold" class="el-form-item__error">{{ errors.quotaThreshold }}</p>
                  </div>
                </div>
              </ElForm>
            </section>
          </div>

          <div class="fullscreen-form-footer fullscreen-form-action-footer add-proxy-fs-footer">
            <div class="flex gap-2">
              <ElButton @click="handleBack">
                {{ t('repositoriesPage.btnCancel') }}
              </ElButton>
              <ElButton type="primary" :loading="busy" :disabled="busy" @click="onSubmit">
                {{ t('repositoriesPage.btnCreateRepo') }}
              </ElButton>
            </div>
          </div>
        </div>

        <aside v-if="!embedded" class="fullscreen-form-sidebar add-form-preview-sidebar">
          <div class="add-form-preview-card">
            <div class="add-form-preview-header">
              <div class="add-form-preview-header__glow" />
              <div class="add-form-preview-header__icon">
                <HardDrive class="add-form-preview-header__icon-lucide" :size="28" />
              </div>
              <div class="add-form-preview-header__info">
                <h4 class="add-form-preview-header__name">{{ repoName || t('addS3Repo.previewUnnamed') }}</h4>
                <p class="add-form-preview-header__type">{{ t('repositoriesPage.kindProxyFs') }}</p>
              </div>
            </div>

            <div class="add-form-preview-body">
              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">{{ t('addS3Repo.previewBasicInfo') }}</h5>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('repositoriesPage.fieldRepoName') }}</span>
                  <span class="add-form-preview-row__value" :class="{ 'add-form-preview-row__value--empty': !repoName }">
                    {{ repoName || '—' }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('repositoriesPage.fieldProxyNode') }}</span>
                  <span class="add-form-preview-row__value" :class="{ 'add-form-preview-row__value--empty': !selectedProxyNodeName }">
                    {{ selectedProxyNodeName || '—' }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('repositoriesPage.fieldQuota') }}</span>
                  <span class="add-form-preview-row__value" :class="{ 'add-form-preview-row__value--highlight': quota > 0 }">
                    {{ quota > 0 ? `${quota} GB` : t('addS3Repo.previewUnlimited') }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('repositoriesPage.fieldQuotaAlert') }}</span>
                  <span class="add-form-preview-row__value" :class="enableQuotaAlert ? 'add-form-preview-row__value--success' : 'add-form-preview-row__value--muted'">
                    <span v-if="enableQuotaAlert" class="add-form-preview-row__dot add-form-preview-row__dot--green" />
                    <template v-if="enableQuotaAlert">
                      {{ t('repositoriesPage.enabled') }} ({{ quotaAlertThreshold }}%)
                    </template>
                    <template v-else>
                      {{ t('repositoriesPage.disabled') }}
                    </template>
                  </span>
                </div>
              </div>

              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">{{ t('addS3Repo.previewConnection') }}</h5>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('repositoriesPage.fieldProxyNodeBaseDir') }}</span>
                  <span class="add-form-preview-row__value" :class="{ 'add-form-preview-row__value--empty': !proxyNodeDir }">
                    {{ proxyNodeDir || '—' }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('repositoriesPage.fieldManagedRepoPath') }}</span>
                  <span class="add-form-preview-row__value" :class="{ 'add-form-preview-row__value--empty': !managedRepositoryPathPreview }">
                    {{ managedRepositoryPathPreview || '—' }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.previewBinding') }}</span>
                  <span class="add-form-preview-row__value add-form-preview-row__value--primary">
                    {{ dirSelectModeLabel }}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dir-tree-root-row {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  min-width: 0;
  min-height: 26px;
  padding: 2px 6px 2px 0;
  border: 0;
  border-radius: 4px;
  background: rgba(241, 245, 249, 0.6);
  color: rgb(71 85 105);
  cursor: default;
  user-select: none;
  text-align: left;
  font: inherit;
}
.dir-tree-root-row:hover {
  background: rgba(226, 232, 240, 0.7);
}
.dir-tree-root-row__toggle {
  display: flex;
  min-width: 0;
  flex: 1 1 auto;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
  font: inherit;
}
.dir-tree-root-row__toggle:disabled {
  cursor: default;
}
.dir-tree-root-row__caret {
  flex: 0 0 14px;
  width: 14px;
  height: 14px;
  color: rgb(100 116 139);
}
.dir-tree-root-row__text {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  flex-direction: column;
}
.dir-tree-root-row__label {
  font-size: 13px;
  font-weight: 500;
  line-height: 17px;
  color: rgb(30 41 59);
}
.dir-tree-root-row__path {
  font-size: 12px;
  line-height: 15px;
  color: rgb(100 116 139);
}

.proxy-dir-tree--nested {
  border: 0;
  background: transparent;
  padding: 0;
  box-shadow: none;
}
.proxy-dir-tree--nested :deep(.el-tree-node) > .el-tree-node__content {
  padding-left: 24px;
}
.dir-tree-empty-nested {
  margin: 4px 0 0 24px;
}

.add-proxy-fs-step-section {
  scroll-margin-top: 16px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, opacity 0.2s ease;
}

.add-proxy-fs-step-section--active {
  border-color: rgba(37, 99, 235, 0.55);
  box-shadow:
    inset 3px 0 0 rgba(37, 99, 235, 0.85),
    0 8px 20px rgba(15, 23, 42, 0.04);
}

.add-proxy-fs-step-section--locked {
  opacity: 0.68;
  user-select: none;
}

.add-proxy-fs-step-section--locked :deep(.el-input),
.add-proxy-fs-step-section--locked :deep(.el-select),
.add-proxy-fs-step-section--locked :deep(.el-tree),
.add-proxy-fs-step-section--locked :deep(.el-checkbox),
.add-proxy-fs-step-section--locked :deep(.el-button),
.add-proxy-fs-step-section--locked button {
  pointer-events: none;
}

.add-proxy-fs-main {
  box-sizing: border-box;
  scroll-padding: 16px 0 84px;
}

.add-proxy-fs-step-section {
  flex: 0 0 auto;
  width: 100%;
  box-sizing: border-box;
}

.add-proxy-fs-bind-form-item :deep(.el-form-item__content) {
  width: 100%;
}

.add-proxy-fs-bind-form-item :deep(.el-form-item__label) {
  width: 100%;
  white-space: nowrap;
}

.add-proxy-fs-proxy-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}

.add-proxy-fs-proxy-label__title {
  flex: 0 0 auto;
  white-space: nowrap;
}

.add-proxy-fs-proxy-label__required {
  margin-left: 4px;
  color: #ef4444;
}

.add-proxy-fs-select-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.add-proxy-fs-select-row__select {
  flex: 1 1 auto;
  min-width: 0;
  width: 100%;
}

.add-proxy-fs-select-row__refresh {
  flex: 0 0 34px;
}

/* Directory Selector */
.dir-selector-container {
  width: 100%;
}

.dir-selector-toggle {
  display: flex;
  gap: 4px;
  margin-bottom: 8px;
}

.dir-selector-tab {
  display: flex;
  padding: 6px 14px;
  background: transparent;
  border: 1px solid var(--color-border, #2A2B35);
  border-radius: 4px;
  font-size: 12px;
  color: var(--color-text-secondary, #A3A6AD);
  cursor: pointer;
  transition: all 0.15s ease;
}

.dir-selector-tab:hover {
  border-color: var(--color-border-light, #3A3B45);
  color: var(--color-text-title, #E2E2E2);
}

.dir-selector-tab--active {
  background: rgba(69, 125, 176, 0.1);
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.dir-selector-toggle :deep(.el-button) {
  border-radius: 8px;
  border-color: rgba(203, 213, 225, 0.95);
  background: #fff;
  color: rgb(71 85 105);
}

.dir-selector-toggle :deep(.el-button:hover) {
  border-color: rgba(59, 130, 246, 0.38);
  color: rgb(30 64 175);
  background: rgb(248 250 252);
}

.dir-selector-toggle :deep(.el-button--primary) {
  border-color: var(--color-primary, #6D5EF6);
  background-color: var(--color-primary, #6D5EF6);
  background-image: linear-gradient(
    var(--color-primary-gradient-start, #7E6CEF),
    var(--color-primary-gradient-end, #6D5EF6)
  );
  color: #fff;
  box-shadow: rgba(109, 94, 246, 0.32) 0 6px 16px -8px;
}

.dir-selector-toggle :deep(.el-button--primary:hover),
.dir-selector-toggle :deep(.el-button--primary:focus-visible) {
  border-color: var(--color-primary-hover-gradient-end, #7664FA);
  background-color: var(--color-primary-hover-gradient-end, #7664FA);
  background-image: linear-gradient(
    var(--color-primary-hover-gradient-start, #8876F5),
    var(--color-primary-hover-gradient-end, #7664FA)
  );
  color: #fff;
  box-shadow: rgba(109, 94, 246, 0.42) 0 8px 18px -8px;
}

.dir-tree-select {
  border: 1px solid rgba(203, 213, 225, 0.9);
  border-radius: 8px;
  padding: 6px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.92) 0%, rgba(248, 250, 252, 0.96) 100%);
}

.dir-tree-select__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 120px;
  padding: 16px;
  color: rgb(100 116 139);
  font-size: 13px;
  background: rgba(248, 250, 252, 0.88);
  border-radius: 6px;
  border: 1px dashed rgba(148, 163, 184, 0.35);
}

.proxy-dir-tree {
  max-height: 260px;
  overflow-y: auto;
  overflow-x: auto;
  border: 0;
  border-radius: 0;
  padding: 2px 0;
  background: transparent;
  box-shadow: none;
}

.proxy-dir-tree :deep(.el-tree-node__content) {
  height: auto;
  min-height: 30px;
  padding-top: 1px;
  padding-bottom: 1px;
  border-radius: 4px;
}

.proxy-dir-tree :deep(.el-tree-node__content:hover) {
  background: rgba(226, 232, 240, 0.5);
}

.proxy-dir-tree :deep(.el-tree-node.is-current > .el-tree-node__content) {
  background: linear-gradient(180deg, rgba(219, 234, 254, 0.88) 0%, rgba(191, 219, 254, 0.72) 100%);
}

.proxy-dir-tree :deep(.el-checkbox) {
  margin-right: 6px;
}

.tree-node-content {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-width: 0;
}

.tree-node-content__icon {
  flex-shrink: 0;
  color: #d97706;
}

.tree-node-content__text {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  flex-direction: column;
}

.tree-node-content__label {
  font-size: 13px;
  line-height: 17px;
  color: rgb(30 41 59);
}

.tree-node-content__path {
  font-size: 12px;
  line-height: 15px;
  color: rgb(100 116 139);
  word-break: break-word;
}

.tree-node-content__status {
  font-size: 12px;
  color: rgb(100 116 139);
}

.tree-node-content__retry {
  flex-shrink: 0;
  font-size: 12px;
  padding: 0 6px;
}

.tree-node-content__error {
  margin-top: 4px;
  font-size: 12px;
  color: var(--color-error);
}

.dir-input {
  margin-top: 0;
}

.add-proxy-fs-quota-threshold-block {
  margin-top: 8px;
}

.add-proxy-fs-quota-threshold-row {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 32px;
}

.add-proxy-fs-quota-threshold-row__label {
  flex-shrink: 0;
  font-size: 14px;
  color: var(--color-text-primary);
}

.add-proxy-fs-quota-threshold {
  display: flex;
  align-items: center;
  gap: 8px;
}

.add-proxy-fs-quota-threshold__input {
  width: 120px;
}

.add-proxy-fs-quota-threshold__unit {
  font-size: 14px;
  font-weight: 500;
  color: var(--color-text-secondary);
}

.add-proxy-fs-review-card {
  width: 100%;
}

@media (max-width: 768px) {
  .add-proxy-fs-quota-threshold-row {
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
  }

  .add-proxy-fs-quota-threshold-block {
    margin-top: 12px;
  }
}
</style>

<style src="../../styles/fullscreen-form-shell.css"></style>
<style src="../../styles/resource-add.css"></style>
