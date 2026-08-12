<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowLeft,
  CirclePlus,
  File,
  FolderOpen,
  MessageSquare,
  Plus,
  RefreshCw,
  TextCursorInput,
  Trash2,
  TriangleAlert,
} from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import HflPopover from '../../components/HflPopover.vue'
import { useKnowledgeSourceForm, type BackupScopePickerNode, type KnowledgeSourceType } from '../../composables/useKnowledgeSourceForm'
import { apiErrorMessage } from '../../lib/api'
import { formatBytes } from '../../lib/kopiaProgress'
import { formatLocalDateTime } from '../../lib/dateTime'
import {
  createCopilotSession,
  fetchCopilotReadiness,
  listCopilotGatewayOptions,
  type LensCopilotGatewayOption,
  type LensCopilotReadiness,
} from '../../lib/lensApi'

const router = useRouter()
const { t } = useI18n()

type SubmitBlockCode =
  | 'agent_model'
  | 'backup_source'
  | 'snapshot'
  | 'source_scope'
  | 'public_gateway'
  | 'private_gateway'

type SubmitBlocker = {
  code: SubmitBlockCode
  message: string
}

const sourceType = ref<KnowledgeSourceType>('backup_source')
const editingId = ref<number | null>(null)
const submitting = ref(false)
const gatewayRefreshing = ref(false)
const gatewayOptionsResolved = ref(false)
const gatewayOptions = ref<LensCopilotGatewayOption[]>([])
const readiness = ref<LensCopilotReadiness | null>(null)
const gatewayMode = ref<'auto' | 'manual'>('auto')
const gatewayLinkId = ref<number | null>(null)
const backupScopePickerWidth = ref(460)
const backupScopeStackRef = ref<HTMLElement | null>(null)
const privateGatewayCardRef = ref<HTMLElement | null>(null)
let backupScopeResizeObserver: ResizeObserver | null = null
let createIdempotencyKey: string | null = null
let createRequestFingerprint = ''

function newCreateIdempotencyKey(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `chat-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

const {
  loading,
  snapshotLoading,
  selectedBackupConfigId,
  snapshotPickerValue,
  backupSourceOptions,
  snapshotsForSelectedBackupSource,
  SNAPSHOT_PICKER_LATEST,
  effectiveSnapshotId,
  snapshotDirectories,
  backupScopeEntries,
  openBackupScopePickerId,
  backupScopeTreeRevision,
  backupScopeBrowseLoading,
  loadSnapshots,
  loadBackupScopePickerNode,
  setBackupScopePickerOpen,
  addBackupScopeEntry,
  removeBackupScopeEntry,
  updateBackupScopeEntryInput,
  validateBackupScopeEntry,
  validateBackupScopeEntryOnBlur,
  validateAllBackupScopeEntries,
  pickBackupScopeForEntry,
} = useKnowledgeSourceForm(editingId, sourceType)

const sourceScopes = computed(() => backupScopeEntries.value
  .filter((row) => row.path.trim() && row.directoryId)
  .map((row) => ({
    source_path: row.path.trim(),
    backup_snapshot_directory_id: row.directoryId as number,
    path_type: row.pathType,
  })))
const readyGateways = computed(() => gatewayOptions.value.filter(
  (row) => row.online && row.hfl_usable && row.copilot_eligible,
))
const privateGateways = computed(() => readyGateways.value.filter((row) => row.scope === 'user'))
const platformGateway = computed(() => readyGateways.value.find((row) => row.scope === 'platform') ?? null)
const autoGateway = computed(() => platformGateway.value)
const agentModelReady = computed(() => Boolean(readiness.value?.default_agent_model_ref))
const visualModelReady = computed(() => Boolean(readiness.value?.default_multimodal_model_ref))
const selectedGateway = computed(() => gatewayMode.value === 'auto'
  ? autoGateway.value
  : privateGateways.value.find((row) => row.gateway_link_id === gatewayLinkId.value) ?? null)
const publicGatewayUnavailable = computed(() => (
  gatewayOptionsResolved.value
  && gatewayMode.value === 'auto'
  && !autoGateway.value
))
const selectedBackupSource = computed(() => backupSourceOptions.value.find(
  (row) => row.backupConfigId === selectedBackupConfigId.value,
) ?? null)
const selectedSnapshot = computed(() => snapshotsForSelectedBackupSource.value.find(
  (row) => row.id === effectiveSnapshotId.value,
) ?? null)
const selectedScopeSummary = computed(() => sourceScopes.value.length > 0
  ? `${sourceScopes.value.length} selected`
  : '—')
const canCreate = computed(() => Boolean(
  effectiveSnapshotId.value
  && sourceScopes.value.length > 0
  && selectedGateway.value
  && agentModelReady.value
  && !submitting.value,
))
const submitBlocker = computed<SubmitBlocker | null>(() => {
  if (!agentModelReady.value) {
    return {
      code: 'agent_model',
      message: 'No default Agent model is configured. Contact your administrator.',
    }
  }
  if (!selectedBackupSource.value) {
    return {
      code: 'backup_source',
      message: 'Select a backup source to continue.',
    }
  }
  if (!effectiveSnapshotId.value) {
    return {
      code: 'snapshot',
      message: 'Select a snapshot to continue.',
    }
  }
  if (sourceScopes.value.length === 0) {
    return {
      code: 'source_scope',
      message: 'Select at least one file or folder to continue.',
    }
  }
  if (!selectedGateway.value) {
    if (gatewayMode.value === 'manual') {
      return {
        code: 'private_gateway',
        message: t('insight.copilot.gatewayPrivateRequired'),
      }
    }
    if (gatewayOptionsResolved.value) {
      return {
        code: 'public_gateway',
        message: t('insight.copilot.gatewayPublicUnavailable'),
      }
    }
  }
  return null
})
const footerSubmitBlockReason = computed(() => (
  submitBlocker.value?.code === 'public_gateway'
    ? ''
    : submitBlocker.value?.message ?? ''
))

function snapshotOptionLabel(row: { finished_at?: string | null; started_at?: string | null; created_at: string; total_size_bytes: number }) {
  const time = row.finished_at || row.started_at || row.created_at
  return `${time ? formatLocalDateTime(time) : '—'} · ${formatBytes(row.total_size_bytes)}`
}

function isBackupScopePickerOpen(entryId: string) {
  return openBackupScopePickerId.value === entryId
}

function handleBackupScopeNodeClick(entryId: string, data: BackupScopePickerNode) {
  pickBackupScopeForEntry(entryId, data)
}

function syncBackupScopePickerWidth() {
  const input = backupScopeStackRef.value?.querySelector<HTMLElement>('.new-chat-scope-input')
  if (input) backupScopePickerWidth.value = Math.round(input.getBoundingClientRect().width)
}

async function refreshGatewayOptions(showFeedback = true) {
  if (gatewayRefreshing.value) return
  gatewayRefreshing.value = true
  try {
    gatewayOptions.value = await listCopilotGatewayOptions()
    if (
      gatewayLinkId.value !== null
      && !privateGateways.value.some((row) => row.gateway_link_id === gatewayLinkId.value)
    ) {
      gatewayLinkId.value = null
    }
    if (showFeedback) {
      ElMessage.success({ message: t('insight.copilot.gatewayPrivateRefreshSuccess'), grouping: true })
    }
  } catch (error) {
    if (!showFeedback) throw error
    ElMessage.error({ message: apiErrorMessage(error, t('insight.copilot.gatewayPrivateRefreshFailed')), grouping: true })
  } finally {
    gatewayOptionsResolved.value = true
    gatewayRefreshing.value = false
  }
}

function openGatewayDeploy() {
  const { href } = router.resolve({ path: '/node/nodes/deploy', query: { role: 'gateway' } })
  window.open(href, '_blank', 'noopener,noreferrer')
}

async function ensurePrivateGatewayVisible() {
  await nextTick()
  privateGatewayCardRef.value?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}

async function load() {
  try {
    await Promise.all([
      loadSnapshots(),
      refreshGatewayOptions(false),
      fetchCopilotReadiness().then((row) => {
        readiness.value = row
      }),
    ])
  } catch (error) {
    ElMessage.error({ message: apiErrorMessage(error, 'Unable to load chat options.'), grouping: true })
  }
}

async function createChat() {
  if (!effectiveSnapshotId.value || !selectedBackupConfigId.value || !selectedGateway.value || !agentModelReady.value) return
  const scopesValid = await validateAllBackupScopeEntries(true)
  if (!scopesValid || sourceScopes.value.length === 0) return

  submitting.value = true
  const requestPayload = {
    backup_config_id: selectedBackupConfigId.value,
    backup_source_snapshot_id: effectiveSnapshotId.value,
    source_scopes: sourceScopes.value,
    gateway_mode: gatewayMode.value,
    gateway_link_id: gatewayMode.value === 'manual' ? gatewayLinkId.value : null,
  }
  const requestFingerprint = JSON.stringify(requestPayload)
  if (!createIdempotencyKey || createRequestFingerprint !== requestFingerprint) {
    createIdempotencyKey = newCreateIdempotencyKey()
    createRequestFingerprint = requestFingerprint
  }
  try {
    const session = await createCopilotSession({
      idempotency_key: createIdempotencyKey,
      ...requestPayload,
    })
    await router.replace({ path: '/insight/copilot', query: { session: String(session.id) } })
  } catch (error) {
    const status = Number((error as { status?: number })?.status || 0)
    if (status >= 400 && status < 500 && status !== 408 && status !== 429) {
      createIdempotencyKey = null
      createRequestFingerprint = ''
    }
    ElMessage.error({ message: apiErrorMessage(error, 'Unable to start chat.'), grouping: true })
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  void load()
  syncBackupScopePickerWidth()
  if (backupScopeStackRef.value) {
    backupScopeResizeObserver = new ResizeObserver(syncBackupScopePickerWidth)
    backupScopeResizeObserver.observe(backupScopeStackRef.value)
  }
})

watch(gatewayMode, (mode) => {
  if (mode === 'manual') void ensurePrivateGatewayVisible()
})

onBeforeUnmount(() => backupScopeResizeObserver?.disconnect())
</script>

<template>
  <div class="fullscreen-form-fullscreen resource-add-fullscreen">
    <div class="fullscreen-form-page new-copilot-chat-page">
      <div class="fullscreen-form-header">
        <button type="button" class="fullscreen-form-header__back" aria-label="Back to Copilot" @click="router.push('/insight/copilot')">
          <ArrowLeft class="fullscreen-form-header__back-icon" :size="18" />
        </button>
        <div class="fullscreen-form-header__content">
          <h1 class="fullscreen-form-header__title">New Chat</h1>
          <p class="fullscreen-form-header__desc">Choose the backup data you want AI Copilot to analyze.</p>
        </div>
      </div>

      <div v-loading="loading" class="fullscreen-form-layout">
        <main class="fullscreen-form-main">
          <div class="fullscreen-form-step-stack">
            <div class="fullscreen-form-card"><section class="fullscreen-form-section">
              <div class="new-chat-section-head">
                <div class="new-chat-section-head__copy">
                  <h2 class="fullscreen-form-section__title"><span class="fullscreen-form-section__indicator" />Data Source</h2>
                </div>
              </div>
              <div class="new-chat-grid">
                <div class="fullscreen-form-field">
                  <label for="copilot-backup-source" class="fullscreen-form-field__label">Backup Source <span class="fullscreen-form-field__required">*</span></label>
                  <ElSelect id="copilot-backup-source" v-model="selectedBackupConfigId" filterable placeholder="Select a backup source">
                    <ElOption v-for="row in backupSourceOptions" :key="row.backupConfigId" :label="row.label" :value="row.backupConfigId" />
                  </ElSelect>
                  <p class="fullscreen-form-field__hint">Choose the backup source that contains the data you want to analyze.</p>
                </div>
                <div class="fullscreen-form-field">
                  <label for="copilot-snapshot" class="fullscreen-form-field__label">Snapshot <span class="fullscreen-form-field__required">*</span></label>
                  <ElSelect id="copilot-snapshot" v-model="snapshotPickerValue" :loading="snapshotLoading" :disabled="!selectedBackupConfigId" placeholder="Select a snapshot">
                    <ElOption label="Latest available snapshot" :value="SNAPSHOT_PICKER_LATEST" />
                    <ElOption v-for="row in snapshotsForSelectedBackupSource" :key="row.id" :label="snapshotOptionLabel(row)" :value="row.id" />
                  </ElSelect>
                  <p class="fullscreen-form-field__hint">Choose the backup snapshot you want to analyze.</p>
                </div>
              </div>
              <div class="new-chat-source-divider" />
              <div class="new-chat-source-subsection">
                <div class="new-chat-source-subsection__head">
                  <h3 class="fullscreen-form-field__label">Files and Folders <span class="fullscreen-form-field__required">*</span></h3>
                </div>
                <div ref="backupScopeStackRef" class="new-chat-scope-stack">
                  <div class="new-chat-scope-stack__header" aria-hidden="true"><span /><span>Path</span><span>Actions</span></div>
                  <div v-for="(scopeEntry, scopeIndex) in backupScopeEntries" :key="scopeEntry.id" class="new-chat-scope-row">
                    <span class="new-chat-scope-row__index">{{ String(scopeIndex + 1).padStart(2, '0') }}</span>
                    <HflPopover
                      :visible="isBackupScopePickerOpen(scopeEntry.id)"
                      trigger="click"
                      placement="bottom-start"
                      :width="backupScopePickerWidth"
                      popper-class="ks-backup-scope-popover"
                      @update:visible="(open) => setBackupScopePickerOpen(scopeEntry.id, open)"
                    >
                      <template #reference>
                        <ElInput
                          class="new-chat-scope-input"
                          :model-value="scopeEntry.path"
                          clearable
                          placeholder="Select a file or folder"
                          :disabled="!effectiveSnapshotId || snapshotDirectories.length === 0"
                          @update:model-value="updateBackupScopeEntryInput(scopeEntry.id, $event)"
                          @blur="validateBackupScopeEntryOnBlur(scopeEntry.id)"
                          @keydown.enter.prevent="validateBackupScopeEntry(scopeEntry.id)"
                        >
                          <template #prefix><TextCursorInput :size="14" /></template>
                          <template #append>
                            <ElButton aria-label="Browse backup content" :disabled="!effectiveSnapshotId || snapshotDirectories.length === 0" @click.stop="setBackupScopePickerOpen(scopeEntry.id, !isBackupScopePickerOpen(scopeEntry.id))"><FolderOpen :size="16" /></ElButton>
                          </template>
                        </ElInput>
                      </template>
                      <div class="new-chat-scope-tree hfl-dir-tree-shell">
                        <el-tree
                          :key="`copilot-scope-${scopeEntry.id}-${effectiveSnapshotId}-${backupScopeTreeRevision}`"
                          v-loading="backupScopeBrowseLoading"
                          class="hfl-dir-tree hfl-dir-tree--tall"
                          node-key="id"
                          lazy
                          highlight-current
                          :expand-on-click-node="false"
                          :load="loadBackupScopePickerNode"
                          :props="{ label: 'label', children: 'children', isLeaf: 'isLeaf' }"
                          @node-click="(data) => handleBackupScopeNodeClick(scopeEntry.id, data)"
                        >
                          <template #default="{ data }">
                            <div class="hfl-dir-tree-node">
                              <FolderOpen v-if="data.type === 'dir'" :size="15" class="hfl-dir-tree-node__icon hfl-dir-tree-node__icon--dir" />
                              <File v-else :size="15" class="hfl-dir-tree-node__icon hfl-dir-tree-node__icon--file" />
                              <div class="hfl-dir-tree-node__text"><span class="hfl-dir-tree-node__label">{{ data.label }}</span><span v-if="data.path" class="hfl-dir-tree-node__path">{{ data.path }}</span></div>
                            </div>
                          </template>
                        </el-tree>
                      </div>
                    </HflPopover>
                    <ElButton type="danger" size="small" :disabled="backupScopeEntries.length <= 1" aria-label="Remove scope" @click="removeBackupScopeEntry(scopeEntry.id)"><Trash2 :size="14" /></ElButton>
                  </div>
                  <div class="new-chat-scope-stack__add"><button type="button" :disabled="!effectiveSnapshotId || snapshotDirectories.length === 0" @click="addBackupScopeEntry"><CirclePlus :size="16" /> Add File or Folder</button></div>
                </div>
                <p v-if="!effectiveSnapshotId" class="fullscreen-form-field__hint new-chat-scope-hint">Select a snapshot to browse its files and folders.</p>
                <p v-else-if="snapshotDirectories.length === 0" class="fullscreen-form-field__hint new-chat-scope-hint new-chat-hint--warn">No files or folders are available in this snapshot.</p>
                <p v-else class="fullscreen-form-field__hint new-chat-scope-hint">Select one or more files or folders for this chat.</p>
                <p class="fullscreen-form-field__hint new-chat-scope-hint">{{ t('insight.copilot.documentFormatHint') }}</p>
                <p class="fullscreen-form-field__hint new-chat-scope-hint">{{ t('insight.copilot.dataOriginHint') }}</p>
              </div>
            </section></div>

            <div class="fullscreen-form-card"><section class="fullscreen-form-section">
              <div class="new-chat-section-head">
                <div class="new-chat-section-head__copy">
                  <h2 class="fullscreen-form-section__title"><span class="fullscreen-form-section__indicator" />Data Privacy</h2>
                </div>
              </div>
              <div class="new-chat-privacy-options">
                <label class="new-chat-choice" :class="{ 'new-chat-choice--selected': gatewayMode === 'auto' }"><input v-model="gatewayMode" type="radio" value="auto"><span><strong>{{ t('insight.copilot.gatewayPublicTitle') }}</strong><small>{{ t('insight.copilot.gatewayPublicDescription') }}</small></span></label>
                <div ref="privateGatewayCardRef" class="new-chat-choice new-chat-choice--private" :class="{ 'new-chat-choice--selected': gatewayMode === 'manual' }">
                  <label class="new-chat-choice__radio"><input v-model="gatewayMode" type="radio" value="manual"><span><strong>{{ t('insight.copilot.gatewayPrivateTitle') }}</strong><small>{{ t('insight.copilot.gatewayPrivateDescription') }}</small></span></label>
                  <div class="new-chat-choice__control">
                    <div class="new-chat-gateway-select-row">
                      <ElSelect
                        v-model="gatewayLinkId"
                        class="new-chat-gateway-select"
                        filterable
                        :loading="gatewayRefreshing"
                        :no-data-text="t('insight.copilot.gatewayPrivateNoOnline')"
                        placement="top-start"
                        :fallback-placements="['bottom-start', 'top-end', 'bottom-end']"
                        :placeholder="t('insight.copilot.gatewayPrivateSelectPlaceholder')"
                        popper-class="new-chat-gateway-select-popper"
                        @change="gatewayMode = 'manual'"
                        @visible-change="(visible) => visible && ensurePrivateGatewayVisible()"
                      >
                        <ElOption
                          v-for="row in privateGateways"
                          :key="row.gateway_link_id"
                          :label="row.name"
                          :value="row.gateway_link_id"
                        >
                          <div class="new-chat-gateway-option">
                            <span class="new-chat-gateway-option__name">{{ row.name }}</span>
                            <span class="new-chat-gateway-option__status"><span class="new-chat-gateway-option__dot" />Online</span>
                          </div>
                        </ElOption>
                      </ElSelect>
                      <ElButton
                        class="hfl-refresh-button new-chat-gateway-select-row__refresh"
                        :title="t('insight.copilot.gatewayPrivateRefreshAction')"
                        :aria-label="t('insight.copilot.gatewayPrivateRefreshAction')"
                        :disabled="gatewayRefreshing"
                        @click="refreshGatewayOptions()"
                      >
                        <RefreshCw :size="16" :class="{ 'is-spinning': gatewayRefreshing }" />
                      </ElButton>
                      <ElButton
                        class="fullscreen-form-icon-btn new-chat-gateway-select-row__deploy"
                        :title="t('insight.copilot.gatewayPrivateInstallAction')"
                        :aria-label="t('insight.copilot.gatewayPrivateInstallAction')"
                        @click="openGatewayDeploy"
                      >
                        <Plus :size="14" />
                      </ElButton>
                    </div>
                    <p v-if="!gatewayRefreshing && privateGateways.length === 0" class="new-chat-hint new-chat-hint--warn">{{ t('insight.copilot.gatewayPrivateNoOnline') }}</p>
                  </div>
                </div>
                <div
                  v-if="publicGatewayUnavailable"
                  class="new-chat-gateway-warning"
                  role="alert"
                >
                  <TriangleAlert :size="16" aria-hidden="true" />
                  <span>{{ t('insight.copilot.gatewayPublicUnavailable') }}</span>
                </div>
              </div>
            </section></div>
          </div>
        </main>

        <aside class="fullscreen-form-sidebar add-form-preview-sidebar">
          <div class="add-form-preview-card">
            <div class="add-form-preview-header"><div class="add-form-preview-header__icon"><MessageSquare class="add-form-preview-header__icon-lucide" :size="25" /></div><div class="add-form-preview-header__info"><h2 class="add-form-preview-header__name">New Chat</h2><p class="add-form-preview-header__type">AI Copilot</p></div></div>
            <div class="add-form-preview-body">
              <section class="add-form-preview-section">
                <h3 class="add-form-preview-section__title">Data Source</h3>
                <div class="add-form-preview-row"><span class="add-form-preview-row__label">Backup Source</span><span class="add-form-preview-row__value" :class="{ 'add-form-preview-row__value--empty': !selectedBackupSource }">{{ selectedBackupSource?.label || '—' }}</span></div>
                <div class="add-form-preview-row"><span class="add-form-preview-row__label">{{ t('insight.copilot.dataOriginLabel') }}</span><span class="add-form-preview-row__value">{{ t('insight.copilot.dataOriginProtected') }}</span></div>
                <div class="add-form-preview-row"><span class="add-form-preview-row__label">Snapshot</span><span class="add-form-preview-row__value" :class="{ 'add-form-preview-row__value--empty': !selectedSnapshot }">{{ selectedSnapshot ? snapshotOptionLabel(selectedSnapshot) : '—' }}</span></div>
                <div class="add-form-preview-row"><span class="add-form-preview-row__label">Files and Folders</span><span class="add-form-preview-row__value" :class="{ 'add-form-preview-row__value--empty': !sourceScopes.length }">{{ selectedScopeSummary }}</span></div>
              </section>
              <section class="add-form-preview-section">
                <h3 class="add-form-preview-section__title">Data Privacy</h3>
                <div class="add-form-preview-row"><span class="add-form-preview-row__label">{{ t('insight.copilot.gatewayTypeLabel') }}</span><span class="add-form-preview-row__value">{{ gatewayMode === 'auto' ? t('insight.copilot.gatewayTypePublic') : t('insight.copilot.gatewayTypePrivate') }}</span></div>
                <div class="add-form-preview-row"><span class="add-form-preview-row__label">{{ t('insight.copilot.gatewayNameLabel') }}</span><span class="add-form-preview-row__value" :class="{ 'add-form-preview-row__value--empty': !selectedGateway }">{{ selectedGateway?.name || t('insight.copilot.gatewayNotReady') }}</span></div>
              </section>
              <p v-if="!visualModelReady" class="new-chat-visual-warning">
                Visual understanding is unavailable. Text documents remain searchable, but images and scanned PDFs may not be readable.
              </p>
            </div>
          </div>
        </aside>
      </div>

      <footer class="fullscreen-form-footer">
        <p v-if="footerSubmitBlockReason" class="form-submit-hint">{{ footerSubmitBlockReason }}</p>
        <ElButton @click="router.push('/insight/copilot')">Cancel</ElButton>
        <ElButton type="primary" :loading="submitting" :disabled="!canCreate" @click="createChat">
          {{ submitting ? 'Starting Chat…' : 'Start Chat' }}
        </ElButton>
      </footer>
    </div>
  </div>
</template>

<style src="../../styles/fullscreen-form-shell.css"></style>
<style src="../../styles/resource-add.css"></style>
<style scoped>
.new-copilot-chat-page { padding-bottom: 104px; }
.new-copilot-chat-page .fullscreen-form-main { scroll-padding: 16px 0 32px; }
.new-copilot-chat-page .fullscreen-form-step-stack { padding-bottom: 28px; }
.new-chat-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 24px; }
.new-chat-grid :deep(.el-select), .new-chat-gateway-select { width: 100%; }
.new-chat-section-head { margin-bottom: 24px; }
.new-chat-section-head .fullscreen-form-section__title { display: flex; align-items: center; gap: 8px; margin: 0; }
.new-chat-source-divider { height: 1px; margin: 26px 0 22px; background: #f2f3f5; }
.new-chat-source-subsection__head { margin-bottom: 12px; }
.new-chat-source-subsection__head h3 { margin: 0; }
.new-chat-scope-stack { overflow: visible; border: 1px solid #e5e6eb; border-radius: 8px; background: #fff; }
.new-chat-scope-stack__header, .new-chat-scope-row { display: grid; grid-template-columns: 34px minmax(0, 1fr) 38px; gap: 8px; align-items: center; padding: 8px 10px; }
.new-chat-scope-stack__header { color: #86909c; font-size: 12px; font-weight: 700; background: #f7f8fa; border-radius: 8px 8px 0 0; }
.new-chat-scope-row { border-top: 1px solid #f2f3f5; }
.new-chat-scope-row__index { color: #86909c; font-size: 12px; font-weight: 700; text-align: center; }
.new-chat-scope-tree { min-width: 100%; }
.new-chat-scope-stack__add { display: flex; justify-content: center; padding: 8px 48px 10px; border-top: 1px solid #f2f3f5; }
.new-chat-scope-stack__add button { display: inline-flex; width: 70%; min-height: 32px; align-items: center; justify-content: center; gap: 8px; margin: 0; padding: 0 12px; border: 1px dashed rgba(148, 163, 184, .8); border-radius: 8px; background: rgba(248, 250, 252, .72); color: #165dff; font-size: 13px; font-weight: 600; cursor: pointer; transition: border-color .16s ease, background .16s ease; }
.new-chat-scope-stack__add button:hover:not(:disabled) { border-color: #165dff; background: rgba(239, 246, 255, .82); }
.new-chat-scope-stack__add button:disabled { cursor: not-allowed; opacity: .55; }
.new-chat-scope-hint { margin-top: 8px; }
.new-chat-hint { margin: 10px 0 0; color: #86909c; font-size: 12px; line-height: 1.5; }
.new-chat-hint--warn { color: #d46b08; }
.new-chat-gateway-warning { display: flex; width: 100%; box-sizing: border-box; align-items: flex-start; gap: 8px; margin-top: 10px; padding: 10px 12px; border: 1px solid color-mix(in srgb, var(--color-warning) 35%, var(--color-card-bg)); border-radius: 8px; background: color-mix(in srgb, var(--color-warning) 10%, var(--color-card-bg)); color: var(--color-warning-text); font-size: 12px; line-height: 1.5; }
.new-chat-gateway-warning svg { flex: 0 0 auto; margin-top: 1px; color: var(--color-warning); }
.new-chat-gateway-warning span { min-width: 0; overflow-wrap: anywhere; }
.new-chat-visual-warning { margin: 14px 0 0; padding: 9px 10px; border: 1px solid #ffe7ba; border-radius: 8px; background: #fffbe6; color: #ad6800; font-size: 12px; line-height: 1.5; }
.new-chat-choice { display: flex; align-items: flex-start; gap: 12px; margin-top: 12px; padding: 13px; border: 1px solid #e5e6eb; border-radius: 8px; cursor: pointer; transition: border-color .15s, background .15s; }
.new-chat-choice--selected { border-color: #165dff; background: #f2f6ff; }
.new-chat-choice input { accent-color: #165dff; }
.new-chat-choice span { display: grid; flex: 1; gap: 3px; }
.new-chat-choice strong { color: #1d2129; font-size: 13px; }.new-chat-choice small { color: #86909c; font-size: 12px; }
.new-chat-privacy-options { margin-top: -12px; }
.new-chat-choice--private { display: block; cursor: default; }
.new-chat-choice__radio { display: flex; align-items: flex-start; gap: 12px; cursor: pointer; }
.new-chat-choice__control { margin: 14px 0 0 26px; padding-top: 14px; border-top: 1px solid #dbe5ff; }
.new-chat-gateway-select-row { display: flex; align-items: center; gap: 8px; width: 100%; }
.new-chat-gateway-select { flex: 1 1 auto; min-width: 0; }
.new-chat-gateway-select-row__refresh { flex: 0 0 34px; }
.new-chat-gateway-select-row__deploy { flex: 0 0 34px; }
:deep(.new-chat-gateway-select-row__deploy.el-button) { width: 34px; height: 34px; min-width: 34px; padding: 0; }
.new-chat-gateway-option { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; }
.new-chat-gateway-option__name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.new-chat-gateway-option__status { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 6px; color: #00b42a; font-size: 12px; font-weight: 600; }
.new-chat-gateway-option__dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; box-shadow: 0 0 0 3px rgba(0, 180, 42, .12); }
:global(.new-chat-gateway-select-popper .el-select-dropdown__wrap) { max-height: 220px; }
@media (max-width: 900px) { .new-chat-grid { grid-template-columns: 1fr; } }
</style>
