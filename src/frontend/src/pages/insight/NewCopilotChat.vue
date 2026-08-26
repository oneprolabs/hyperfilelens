<script setup lang="ts">
import '../../styles/fullscreen-form-styles'
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
import { useCopilotSelectionPreview } from '../../composables/useCopilotSelectionPreview'
import { useKnowledgeSourceForm, type BackupScopePickerNode, type KnowledgeSourceType } from '../../composables/useKnowledgeSourceForm'
import { apiErrorMessage } from '../../lib/api'
import { formatBytes } from '../../lib/kopiaProgress'
import { formatLocalDateTime } from '../../lib/dateTime'
import {
  createCopilotSession,
  fetchCopilotReadiness,
  listCopilotGatewayOptions,
  type LensAdmissionPreview,
  type LensAnalysisType,
  type LensCopilotGatewayOption,
  type LensCopilotReadiness,
} from '../../lib/lensApi'

const router = useRouter()
const { n, t } = useI18n()

type SubmitBlockCode =
  | 'agent_model'
  | 'backup_source'
  | 'snapshot'
  | 'source_scope'
  | 'selection_preview'
  | 'public_gateway'
  | 'private_gateway'
  | 'analysis_type'

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
const selectedAnalysisType = ref<LensAnalysisType>('knowledge_qa')
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

const readyGateways = computed(() => gatewayOptions.value.filter(
  (row) => row.online && row.hfl_usable && row.copilot_eligible,
))
const privateGateways = computed(() => readyGateways.value.filter((row) => row.scope === 'user'))
const platformGateway = computed(() => {
  const rows = readyGateways.value.filter((row) => row.scope === 'platform')
  return rows.find((row) => row.is_platform_default) ?? rows[0] ?? null
})
const autoGateway = computed(() => platformGateway.value)
const snapshotGatewayLinkId = computed(() => {
  if (gatewayMode.value === 'auto') {
    return autoGateway.value?.gateway_link_id ?? null
  }
  return gatewayLinkId.value
})

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
} = useKnowledgeSourceForm(editingId, sourceType, { snapshotGatewayLinkId })

const sourceScopes = computed(() => backupScopeEntries.value
  .filter((row) => row.path.trim() && row.directoryId)
  .map((row) => ({
    source_path: row.path.trim(),
    backup_snapshot_directory_id: row.directoryId as number,
    path_type: row.pathType,
  })))
const previewScopes = computed(() => backupScopeEntries.value
  .filter((row) => row.path.trim() && row.directoryId)
  .map((row) => ({
    key: row.id,
    revision: row.revision,
    directoryId: row.directoryId as number,
    path: row.path.trim(),
    pathType: row.pathType,
    knownFileCount: row.knownFileCount,
    knownSizeBytes: row.knownSizeBytes,
  })))
const {
  admission: selectionAdmission,
  admissionError: selectionAdmissionError,
  admissionLoading: selectionAdmissionLoading,
  calculationStatus: selectionCalculationStatus,
  ready: selectionPreviewReady,
  stateForScope: selectionStateForScope,
  totals: selectionTotals,
} = useCopilotSelectionPreview({
  snapshotId: effectiveSnapshotId,
  gatewayLinkId: snapshotGatewayLinkId,
  gatewayMode,
  scopes: previewScopes,
  translate: t,
})
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
const supportedAnalysisTypes = computed<LensAnalysisType[]>(() => {
  if (!selectedGateway.value) return ['knowledge_qa']
  const types = selectedGateway.value.analysis_types
  return types === undefined ? ['knowledge_qa'] : types
})
const analysisTypeSupported = computed(() => supportedAnalysisTypes.value.includes(selectedAnalysisType.value))
watch(
  () => [selectedGateway.value?.gateway_link_id, selectedAnalysisType.value],
  () => {
    if (!analysisTypeSupported.value) {
      selectedAnalysisType.value = supportedAnalysisTypes.value[0] || 'knowledge_qa'
    }
  },
)
const selectedBackupSource = computed(() => backupSourceOptions.value.find(
  (row) => row.backupConfigId === selectedBackupConfigId.value,
) ?? null)
const selectedSnapshot = computed(() => snapshotsForSelectedBackupSource.value.find(
  (row) => row.id === effectiveSnapshotId.value,
) ?? null)
const selectedScopeSummary = computed(() => sourceScopes.value.length > 0
  ? selectionTotals.value
    ? `${pathCountLabel(sourceScopes.value.length)} · ${fileCountLabel(selectionTotals.value.fileCount)} · ${formatBytes(selectionTotals.value.sizeBytes)}`
    : pathCountLabel(sourceScopes.value.length)
  : '—')
const canCreate = computed(() => Boolean(
  effectiveSnapshotId.value
  && sourceScopes.value.length > 0
  && selectedGateway.value
  && agentModelReady.value
  && analysisTypeSupported.value
  && selectionPreviewReady.value
  && !submitting.value,
))
const submitBlocker = computed<SubmitBlocker | null>(() => {
  if (!agentModelReady.value) {
    return {
      code: 'agent_model',
      message: t('insight.copilot.noDefaultAgentModel'),
    }
  }
  if (!selectedBackupSource.value) {
    return {
      code: 'backup_source',
      message: t('insight.copilot.selectBackupSourceToContinue'),
    }
  }
  if (!effectiveSnapshotId.value) {
    return {
      code: 'snapshot',
      message: t('insight.copilot.selectSnapshotToContinue'),
    }
  }
  if (sourceScopes.value.length === 0) {
    return {
      code: 'source_scope',
      message: t('insight.copilot.selectScopeToContinue'),
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
  if (!analysisTypeSupported.value) {
    return {
      code: 'analysis_type',
      message: t('insight.copilot.analysisTypeUnavailable'),
    }
  }
  if (selectionCalculationStatus.value === 'calculating') {
    return {
      code: 'selection_preview',
      message: t('insight.copilot.calculatingSelection'),
    }
  }
  if (selectionCalculationStatus.value === 'waiting') {
    return {
      code: 'selection_preview',
      message: t('insight.copilot.waitingForReader'),
    }
  }
  if (selectionCalculationStatus.value === 'error') {
    const failed = backupScopeEntries.value
      .map((row) => selectionStateForScope(row.id))
      .find((row) => row.status === 'error')
    return {
      code: 'selection_preview',
      message: failed?.error || t('insight.copilot.selectionUnavailable'),
    }
  }
  if (selectionAdmissionLoading.value) {
    return {
      code: 'selection_preview',
      message: t('insight.copilot.verifyingCapacity'),
    }
  }
  if (selectionAdmissionError.value) {
    return {
      code: 'selection_preview',
      message: selectionAdmissionError.value,
    }
  }
  const reasons = selectionAdmission.value?.admission.reasons || []
  if (reasons.includes('selection_file_limit')) {
    return {
      code: 'selection_preview',
      message: t('insight.copilot.selectionFileLimitExceeded'),
    }
  }
  if (reasons.includes('selection_size_limit')) {
    return {
      code: 'selection_preview',
      message: t('insight.copilot.selectionSizeLimitExceeded'),
    }
  }
  if (reasons.includes('organization_capacity')) {
    return {
      code: 'selection_preview',
      message: t('insight.copilot.organizationCapacityExceeded'),
    }
  }
  if (reasons.includes('organization_capacity_unavailable')) {
    return {
      code: 'selection_preview',
      message: t('insight.copilot.organizationCapacityUnavailable'),
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

function pathCountLabel(count: number): string {
  return t(
    count === 1 ? 'insight.copilot.pathCountOne' : 'insight.copilot.pathCountMany',
    { count: n(count) },
  )
}

function fileCountLabel(count: number): string {
  return t(
    count === 1 ? 'insight.copilot.fileCountOne' : 'insight.copilot.fileCountMany',
    { count: n(count) },
  )
}

function isBackupScopePickerOpen(entryId: string) {
  return openBackupScopePickerId.value === entryId
}

function handleBackupScopeNodeClick(entryId: string, data: BackupScopePickerNode) {
  pickBackupScopeForEntry(entryId, data)
}

function scopeDataSummary(entryId: string): string {
  const state = selectionStateForScope(entryId)
  if (state.status === 'covered') return t('insight.copilot.includedBy', { name: state.coveredBy })
  if (state.status === 'calculating') return t('insight.copilot.calculating')
  if (state.status === 'waiting') return t('insight.copilot.waitingReader')
  if (state.status === 'error') return t('insight.copilot.unavailable')
  if (state.summary) {
    return `${fileCountLabel(state.summary.file_count)} · ${formatBytes(state.summary.size_bytes)}`
  }
  return '—'
}

function quotaCount(value: number | undefined): string {
  if (value == null) return t('insight.copilot.unavailable')
  return value < 0 ? t('insight.copilot.unlimited') : n(value)
}

function quotaBytes(value: number | undefined): string {
  if (value == null) return t('insight.copilot.unavailable')
  return value < 0 ? t('insight.copilot.unlimited') : formatBytes(value)
}

function organizationCapacityBytes(
  value: number | null | undefined,
  capacity: LensAdmissionPreview['organization_capacity'] | undefined,
): string {
  if (!capacity || capacity.limit_available === false) return t('insight.copilot.unavailable')
  if (capacity.limit_bytes == null || capacity.limit_bytes < 0) return t('insight.copilot.unlimited')
  if (capacity.usage_incomplete) return t('insight.copilot.unavailable')
  return value == null ? t('insight.copilot.unavailable') : formatBytes(value)
}

function organizationUsedBytes(
  capacity: LensAdmissionPreview['organization_capacity'] | undefined,
): string {
  if (!capacity || capacity.limit_available === false || capacity.usage_incomplete) {
    return t('insight.copilot.unavailable')
  }
  return capacity.used_bytes == null ? t('insight.copilot.unavailable') : formatBytes(capacity.used_bytes)
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
    ElMessage.error({ message: apiErrorMessage(error, t('insight.copilot.loadChatOptionsFailed')), grouping: true })
  }
}

async function createChat() {
  if (!canCreate.value || !selectedBackupConfigId.value) return
  const scopesValid = await validateAllBackupScopeEntries(true)
  if (!scopesValid || sourceScopes.value.length === 0 || !selectionPreviewReady.value) return

  submitting.value = true
  const requestPayload = {
    backup_config_id: selectedBackupConfigId.value,
    backup_source_snapshot_id: effectiveSnapshotId.value,
    source_scopes: sourceScopes.value,
    gateway_mode: gatewayMode.value,
    gateway_link_id: gatewayMode.value === 'manual' ? gatewayLinkId.value : null,
    analysis_type: selectedAnalysisType.value,
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
    ElMessage.error({ message: apiErrorMessage(error, t('insight.copilot.startChatFailed')), grouping: true })
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
        <button
          type="button"
          class="fullscreen-form-header__back"
          :aria-label="t('insight.copilot.newChatBack')"
          @click="router.push('/insight/copilot')"
        >
          <ArrowLeft
            class="fullscreen-form-header__back-icon"
            :size="18"
          />
        </button>
        <div class="fullscreen-form-header__content">
          <h1 class="fullscreen-form-header__title">
            {{ t('insight.copilot.newChat') }}
          </h1>
          <p class="fullscreen-form-header__desc">
            {{ t('insight.copilot.newChatDescription') }}
          </p>
        </div>
      </div>

      <div
        v-loading="loading"
        class="fullscreen-form-layout"
      >
        <main class="fullscreen-form-main">
          <div class="fullscreen-form-step-stack">
            <div class="fullscreen-form-card">
              <section class="fullscreen-form-section">
                <div class="new-chat-section-head">
                  <div class="new-chat-section-head__copy">
                    <h2 class="fullscreen-form-section__title">
                      <span class="fullscreen-form-section__indicator" />{{ t('insight.copilot.detailsDataSource') }}
                    </h2>
                  </div>
                </div>
                <div class="new-chat-grid">
                  <div class="fullscreen-form-field">
                    <label
                      for="copilot-backup-source"
                      class="fullscreen-form-field__label"
                    >{{ t('insight.copilot.bindingBackupSource') }} <span class="fullscreen-form-field__required">*</span></label>
                    <ElSelect
                      id="copilot-backup-source"
                      v-model="selectedBackupConfigId"
                      filterable
                      :placeholder="t('insight.copilot.backupSourcePlaceholder')"
                    >
                      <ElOption
                        v-for="row in backupSourceOptions"
                        :key="row.backupConfigId"
                        :label="row.label"
                        :value="row.backupConfigId"
                      />
                    </ElSelect>
                    <p class="fullscreen-form-field__hint">
                      {{ t('insight.copilot.backupSourceHint') }}
                    </p>
                  </div>
                  <div class="fullscreen-form-field">
                    <label
                      for="copilot-snapshot"
                      class="fullscreen-form-field__label"
                    >{{ t('insight.copilot.bindingSnapshot') }} <span class="fullscreen-form-field__required">*</span></label>
                    <ElSelect
                      id="copilot-snapshot"
                      v-model="snapshotPickerValue"
                      :loading="snapshotLoading"
                      :disabled="!selectedBackupConfigId"
                      :placeholder="t('insight.copilot.snapshotPlaceholder')"
                    >
                      <ElOption
                        :label="t('insight.copilot.latestSnapshot')"
                        :value="SNAPSHOT_PICKER_LATEST"
                      />
                      <ElOption
                        v-for="row in snapshotsForSelectedBackupSource"
                        :key="row.id"
                        :label="snapshotOptionLabel(row)"
                        :value="row.id"
                      />
                    </ElSelect>
                    <p class="fullscreen-form-field__hint">
                      {{ t('insight.copilot.snapshotHint') }}
                    </p>
                  </div>
                </div>
                <div class="new-chat-source-divider" />
                <div class="new-chat-source-subsection">
                  <div class="new-chat-source-subsection__head">
                    <h3 class="fullscreen-form-field__label">
                      {{ t('insight.copilot.detailsFilesFolders') }} <span class="fullscreen-form-field__required">*</span>
                    </h3>
                  </div>
                  <div
                    ref="backupScopeStackRef"
                    class="new-chat-scope-stack"
                  >
                    <div
                      class="new-chat-scope-stack__header"
                      aria-hidden="true"
                    >
                      <span /><span>{{ t('insight.copilot.path') }}</span><span>{{ t('insight.copilot.selectedData') }}</span><span>{{ t('insight.copilot.actions') }}</span>
                    </div>
                    <div
                      v-for="(scopeEntry, scopeIndex) in backupScopeEntries"
                      :key="scopeEntry.id"
                      class="new-chat-scope-row"
                    >
                      <span class="new-chat-scope-row__index">{{ String(scopeIndex + 1).padStart(2, '0') }}</span>
                      <HflPopover
                        :visible="isBackupScopePickerOpen(scopeEntry.id)"
                        trigger="click"
                        placement="bottom-start"
                        :fallback-placements="['top-start', 'bottom-end', 'top-end']"
                        :width="backupScopePickerWidth"
                        popper-class="new-chat-scope-popover"
                        @update:visible="(open) => setBackupScopePickerOpen(scopeEntry.id, open)"
                      >
                        <template #reference>
                          <ElInput
                            class="new-chat-scope-input"
                            :model-value="scopeEntry.path"
                            clearable
                            :placeholder="t('insight.copilot.selectFileFolder')"
                            :disabled="!effectiveSnapshotId || snapshotDirectories.length === 0"
                            @update:model-value="updateBackupScopeEntryInput(scopeEntry.id, $event)"
                            @blur="validateBackupScopeEntryOnBlur(scopeEntry.id)"
                            @keydown.enter.prevent="validateBackupScopeEntry(scopeEntry.id)"
                          >
                            <template #prefix>
                              <TextCursorInput :size="14" />
                            </template>
                            <template #append>
                              <ElButton
                                :aria-label="t('insight.copilot.browseBackupContent')"
                                :disabled="!effectiveSnapshotId || snapshotDirectories.length === 0"
                                @click.stop="setBackupScopePickerOpen(scopeEntry.id, !isBackupScopePickerOpen(scopeEntry.id))"
                              >
                                <FolderOpen :size="16" />
                              </ElButton>
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
                                <FolderOpen
                                  v-if="data.type === 'dir'"
                                  :size="15"
                                  class="hfl-dir-tree-node__icon hfl-dir-tree-node__icon--dir"
                                />
                                <File
                                  v-else
                                  :size="15"
                                  class="hfl-dir-tree-node__icon hfl-dir-tree-node__icon--file"
                                />
                                <div class="hfl-dir-tree-node__text">
                                  <span
                                    class="hfl-dir-tree-node__label"
                                    :title="data.label"
                                  >{{ data.label }}</span><span
                                    v-if="data.path"
                                    class="hfl-dir-tree-node__path"
                                    :title="data.path"
                                  >{{ data.path }}</span>
                                </div>
                                <span
                                  v-if="data.type === 'file' && data.sizeBytes != null"
                                  class="new-chat-scope-tree__size"
                                >{{ formatBytes(data.sizeBytes) }}</span>
                              </div>
                            </template>
                          </el-tree>
                        </div>
                      </HflPopover>
                      <span
                        class="new-chat-scope-row__summary"
                        :class="{
                          'is-waiting': ['calculating', 'waiting'].includes(selectionStateForScope(scopeEntry.id).status),
                          'is-error': selectionStateForScope(scopeEntry.id).status === 'error',
                        }"
                      >{{ scopeDataSummary(scopeEntry.id) }}</span>
                      <ElButton
                        type="danger"
                        class="new-chat-scope-row__remove"
                        :disabled="backupScopeEntries.length <= 1"
                        :aria-label="t('insight.copilot.removeScope')"
                        @click="removeBackupScopeEntry(scopeEntry.id)"
                      >
                        <Trash2 :size="14" />
                      </ElButton>
                    </div>
                    <div class="new-chat-scope-stack__add">
                      <button
                        type="button"
                        :disabled="!effectiveSnapshotId || snapshotDirectories.length === 0"
                        @click="addBackupScopeEntry"
                      >
                        <CirclePlus :size="16" /> {{ t('insight.copilot.addFileFolder') }}
                      </button>
                    </div>
                  </div>
                  <p
                    v-if="!effectiveSnapshotId"
                    class="fullscreen-form-field__hint new-chat-scope-hint"
                  >
                    {{ t('insight.copilot.selectSnapshotBrowseHint') }}
                  </p>
                  <p
                    v-else-if="snapshotDirectories.length === 0"
                    class="fullscreen-form-field__hint new-chat-scope-hint new-chat-hint--warn"
                  >
                    {{ t('insight.copilot.noSnapshotEntries') }}
                  </p>
                  <p
                    v-else
                    class="fullscreen-form-field__hint new-chat-scope-hint"
                  >
                    {{ t('insight.copilot.selectScopesHint') }}
                  </p>
                  <p class="fullscreen-form-field__hint new-chat-scope-hint">
                    {{ t('insight.copilot.documentFormatHint') }}
                  </p>
                  <p class="fullscreen-form-field__hint new-chat-scope-hint">
                    {{ t('insight.copilot.dataOriginHint') }}
                  </p>
                  <div
                    v-if="sourceScopes.length"
                    class="new-chat-selection-summary"
                    aria-live="polite"
                  >
                    <div class="new-chat-selection-summary__head">
                      <strong>{{ t('insight.copilot.selectedData') }}</strong>
                      <span>{{ pathCountLabel(sourceScopes.length) }}</span>
                    </div>
                    <dl>
                      <div>
                        <dt>{{ t('insight.copilot.files') }}</dt>
                        <dd>
                          {{ selectionTotals ? n(selectionTotals.fileCount) : t('insight.copilot.calculating') }}
                          / {{ quotaCount(selectionAdmission?.selection_limits.max_files) }}
                        </dd>
                      </div>
                      <div>
                        <dt>{{ t('insight.copilot.selectedSize') }}</dt>
                        <dd>
                          {{ selectionTotals ? formatBytes(selectionTotals.sizeBytes) : t('insight.copilot.calculating') }}
                          / {{ quotaBytes(selectionAdmission?.selection_limits.max_bytes) }}
                        </dd>
                      </div>
                      <template v-if="selectionAdmission?.organization_capacity.applicable">
                        <div>
                          <dt>{{ t('insight.copilot.organizationUsed') }}</dt>
                          <dd>{{ organizationUsedBytes(selectionAdmission.organization_capacity) }}</dd>
                        </div>
                        <div>
                          <dt>{{ t('insight.copilot.availableNow') }}</dt>
                          <dd>
                            {{ organizationCapacityBytes(
                              selectionAdmission.organization_capacity.remaining_bytes,
                              selectionAdmission.organization_capacity,
                            ) }}
                          </dd>
                        </div>
                        <div>
                          <dt>{{ t('insight.copilot.availableAfterCreation') }}</dt>
                          <dd>
                            {{ organizationCapacityBytes(
                              selectionAdmission.organization_capacity.after_create_bytes,
                              selectionAdmission.organization_capacity,
                            ) }}
                          </dd>
                        </div>
                      </template>
                    </dl>
                    <p
                      v-if="submitBlocker?.code === 'selection_preview'"
                      class="new-chat-selection-summary__status"
                      :class="{ 'is-error': selectionCalculationStatus === 'error' || Boolean(selectionAdmissionError) || Boolean(selectionAdmission?.admission.reasons.length) }"
                    >
                      {{ submitBlocker.message }}
                    </p>
                  </div>
                </div>
              </section>
            </div>

            <div class="fullscreen-form-card">
              <section class="fullscreen-form-section">
                <div class="new-chat-section-head">
                  <div class="new-chat-section-head__copy">
                    <h2 class="fullscreen-form-section__title">
                      <span class="fullscreen-form-section__indicator" />{{ t('insight.copilot.analysisTypeLabel') }}
                    </h2>
                  </div>
                </div>
                <fieldset class="new-chat-analysis-options">
                  <legend class="sr-only">
                    {{ t('insight.copilot.analysisTypeLabel') }}
                  </legend>
                  <label
                    class="new-chat-analysis-option"
                    :class="{
                      'new-chat-analysis-option--selected': selectedAnalysisType === 'knowledge_qa',
                      'new-chat-analysis-option--disabled': !supportedAnalysisTypes.includes('knowledge_qa'),
                    }"
                  >
                    <input
                      v-model="selectedAnalysisType"
                      type="radio"
                      value="knowledge_qa"
                      :disabled="!supportedAnalysisTypes.includes('knowledge_qa')"
                    >
                    <span>
                      <strong>{{ t('insight.copilot.analysisTypeKnowledgeQa') }}</strong>
                      <small>{{ t('insight.copilot.analysisTypeKnowledgeQaHint') }}</small>
                    </span>
                  </label>
                  <label
                    class="new-chat-analysis-option"
                    :class="{
                      'new-chat-analysis-option--selected': selectedAnalysisType === 'code_analysis',
                      'new-chat-analysis-option--disabled': !supportedAnalysisTypes.includes('code_analysis'),
                    }"
                  >
                    <input
                      v-model="selectedAnalysisType"
                      type="radio"
                      value="code_analysis"
                      :disabled="!supportedAnalysisTypes.includes('code_analysis')"
                    >
                    <span>
                      <strong>{{ t('insight.copilot.analysisTypeCodeAnalysis') }}</strong>
                      <small>{{ t('insight.copilot.analysisTypeCodeAnalysisHint') }}</small>
                    </span>
                  </label>
                </fieldset>
              </section>
            </div>

            <div class="fullscreen-form-card">
              <section class="fullscreen-form-section">
                <div class="new-chat-section-head">
                  <div class="new-chat-section-head__copy">
                    <h2 class="fullscreen-form-section__title">
                      <span class="fullscreen-form-section__indicator" />{{ t('insight.copilot.dataPrivacy') }}
                    </h2>
                  </div>
                </div>
                <div class="new-chat-privacy-options">
                  <label
                    class="new-chat-choice"
                    :class="{ 'new-chat-choice--selected': gatewayMode === 'auto' }"
                  ><input
                    v-model="gatewayMode"
                    type="radio"
                    value="auto"
                  ><span><strong>{{ t('insight.copilot.gatewayPublicTitle') }}</strong><small>{{ t('insight.copilot.gatewayPublicDescription') }}</small></span></label>
                  <div
                    ref="privateGatewayCardRef"
                    class="new-chat-choice new-chat-choice--private"
                    :class="{ 'new-chat-choice--selected': gatewayMode === 'manual' }"
                  >
                    <label class="new-chat-choice__radio"><input
                      v-model="gatewayMode"
                      type="radio"
                      value="manual"
                    ><span><strong>{{ t('insight.copilot.gatewayPrivateTitle') }}</strong><small>{{ t('insight.copilot.gatewayPrivateDescription') }}</small></span></label>
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
                              <span class="new-chat-gateway-option__status"><span class="new-chat-gateway-option__dot" />{{ t('insight.copilot.online') }}</span>
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
                          <RefreshCw
                            :size="16"
                            :class="{ 'is-spinning': gatewayRefreshing }"
                          />
                        </ElButton>
                        <ElButton
                          class="new-chat-gateway-select-row__deploy"
                          :title="t('insight.copilot.gatewayPrivateInstallAction')"
                          :aria-label="t('insight.copilot.gatewayPrivateInstallAction')"
                          @click="openGatewayDeploy"
                        >
                          <Plus :size="14" />
                        </ElButton>
                      </div>
                      <p
                        v-if="!gatewayRefreshing && privateGateways.length === 0"
                        class="new-chat-hint new-chat-hint--warn"
                      >
                        {{ t('insight.copilot.gatewayPrivateNoOnline') }}
                      </p>
                    </div>
                  </div>
                  <div
                    v-if="publicGatewayUnavailable"
                    class="new-chat-gateway-warning"
                    role="alert"
                  >
                    <TriangleAlert
                      :size="16"
                      aria-hidden="true"
                    />
                    <span>{{ t('insight.copilot.gatewayPublicUnavailable') }}</span>
                  </div>
                </div>
              </section>
            </div>
          </div>
        </main>

        <aside class="fullscreen-form-sidebar add-form-preview-sidebar">
          <div class="add-form-preview-card">
            <div class="add-form-preview-header">
              <div class="add-form-preview-header__icon">
                <MessageSquare
                  class="add-form-preview-header__icon-lucide"
                  :size="25"
                />
              </div><div class="add-form-preview-header__info">
                <h2 class="add-form-preview-header__name">
                  {{ t('insight.copilot.newChat') }}
                </h2><p class="add-form-preview-header__type">
                  {{ t('insight.copilot.roleAi') }}
                </p>
              </div>
            </div>
            <div class="add-form-preview-body">
              <section class="add-form-preview-section">
                <h3 class="add-form-preview-section__title">
                  {{ t('insight.copilot.detailsDataSource') }}
                </h3>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('insight.copilot.bindingBackupSource') }}</span><span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !selectedBackupSource }"
                  >{{ selectedBackupSource?.label || '—' }}</span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('insight.copilot.dataOriginLabel') }}</span><span class="add-form-preview-row__value">{{ t('insight.copilot.dataOriginProtected') }}</span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('insight.copilot.bindingSnapshot') }}</span><span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !selectedSnapshot }"
                  >{{ selectedSnapshot ? snapshotOptionLabel(selectedSnapshot) : '—' }}</span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('insight.copilot.detailsFilesFolders') }}</span><span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !sourceScopes.length }"
                  >{{ selectedScopeSummary }}</span>
                </div>
              </section>
              <section class="add-form-preview-section">
                <h3 class="add-form-preview-section__title">
                  {{ t('insight.copilot.execution') }}
                </h3>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('insight.copilot.analysisTypeLabel') }}</span><span class="add-form-preview-row__value">{{ selectedAnalysisType === 'code_analysis' ? t('insight.copilot.analysisTypeCodeAnalysis') : t('insight.copilot.analysisTypeKnowledgeQa') }}</span>
                </div>
              </section>
              <section class="add-form-preview-section">
                <h3 class="add-form-preview-section__title">
                  {{ t('insight.copilot.dataPrivacy') }}
                </h3>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('insight.copilot.gatewayTypeLabel') }}</span><span class="add-form-preview-row__value">{{ gatewayMode === 'auto' ? t('insight.copilot.gatewayTypePublic') : t('insight.copilot.gatewayTypePrivate') }}</span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('insight.copilot.gatewayNameLabel') }}</span><span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !selectedGateway }"
                  >{{ selectedGateway?.name || t('insight.copilot.gatewayNotReady') }}</span>
                </div>
              </section>
              <p
                v-if="!visualModelReady"
                class="new-chat-visual-warning"
              >
                {{ t('insight.copilot.visualUnderstandingUnavailable') }}
              </p>
            </div>
          </div>
        </aside>
      </div>

      <footer class="fullscreen-form-footer">
        <p
          v-if="footerSubmitBlockReason"
          class="form-submit-hint"
        >
          {{ footerSubmitBlockReason }}
        </p>
        <ElButton @click="router.push('/insight/copilot')">
          {{ t('common.cancel') }}
        </ElButton>
        <ElButton
          type="primary"
          :loading="submitting"
          :disabled="!canCreate"
          @click="createChat"
        >
          {{ submitting ? t('insight.copilot.startingChat') : t('insight.copilot.startChat') }}
        </ElButton>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.new-copilot-chat-page { padding-bottom: 104px; }
.new-copilot-chat-page .fullscreen-form-main { scroll-padding: 16px 0 32px; }
.new-copilot-chat-page .fullscreen-form-step-stack { padding-bottom: 28px; }
.new-chat-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 24px; }
.new-chat-grid :deep(.el-select), .new-chat-gateway-select { width: 100%; }
.new-chat-section-head { margin-bottom: 24px; }
.new-chat-section-head .fullscreen-form-section__title { display: flex; align-items: center; gap: 8px; margin: 0; }
.new-chat-analysis-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin: 0; padding: 0; border: 0; }
.new-chat-analysis-option { display: flex; min-height: 88px; align-items: flex-start; gap: 12px; padding: 16px; border: 1px solid #e5e6eb; border-radius: 10px; background: #fff; cursor: pointer; transition: border-color .16s ease, background-color .16s ease, box-shadow .16s ease; }
.new-chat-analysis-option:hover:not(.new-chat-analysis-option--disabled) { border-color: #8aaeff; background: #f7faff; }
.new-chat-analysis-option--selected { border-color: #165dff; background: #f5f8ff; box-shadow: 0 0 0 1px rgba(22, 93, 255, .12); }
.new-chat-analysis-option--disabled { cursor: not-allowed; opacity: .55; }
.new-chat-analysis-option input { width: 16px; height: 16px; margin-top: 2px; accent-color: #165dff; }
.new-chat-analysis-option span { display: flex; min-width: 0; flex-direction: column; gap: 6px; }
.new-chat-analysis-option strong { color: #1d2129; font-size: 14px; }
.new-chat-analysis-option small { color: #86909c; font-size: 12px; line-height: 1.5; }
.new-chat-source-divider { height: 1px; margin: 26px 0 22px; background: #f2f3f5; }
.new-chat-source-subsection__head { margin-bottom: 12px; }
.new-chat-source-subsection__head h3 { margin: 0; }
.new-chat-scope-stack { overflow: visible; border: 1px solid #e5e6eb; border-radius: 8px; background: #fff; }
.new-chat-scope-stack__header, .new-chat-scope-row { display: grid; grid-template-columns: 34px minmax(0, 1fr) minmax(150px, .55fr) 48px; gap: 8px; align-items: center; padding: 8px 16px 8px 10px; }
.new-chat-scope-stack__header { color: #86909c; font-size: 12px; font-weight: 700; background: #f7f8fa; border-radius: 8px 8px 0 0; }
.new-chat-scope-row { border-top: 1px solid #f2f3f5; }
.new-chat-scope-row__index { color: #86909c; font-size: 12px; font-weight: 700; text-align: center; }
.new-chat-scope-row__summary { min-width: 0; overflow: hidden; color: #4e5969; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.new-chat-scope-row__summary.is-waiting { color: #86909c; }
.new-chat-scope-row__summary.is-error { color: var(--color-danger-text, #c45656); }
.new-chat-scope-row__remove { width: 34px; height: 34px; padding: 0; justify-self: center; }
.new-chat-scope-tree { min-width: 100%; }
.new-chat-scope-tree__size { flex: 0 0 auto; color: #86909c; font-size: 12px; font-variant-numeric: tabular-nums; }
.new-chat-scope-stack__add { display: flex; justify-content: center; padding: 8px 48px 10px; border-top: 1px solid #f2f3f5; }
.new-chat-scope-stack__add button { display: inline-flex; width: 70%; min-height: 32px; align-items: center; justify-content: center; gap: 8px; margin: 0; padding: 0 12px; border: 1px dashed rgba(148, 163, 184, .8); border-radius: 8px; background: rgba(248, 250, 252, .72); color: #165dff; font-size: 13px; font-weight: 600; cursor: pointer; transition: border-color .16s ease, background .16s ease; }
.new-chat-scope-stack__add button:hover:not(:disabled) { border-color: #165dff; background: rgba(239, 246, 255, .82); }
.new-chat-scope-stack__add button:disabled { cursor: not-allowed; opacity: .55; }
.new-chat-scope-hint { margin-top: 8px; }
.new-chat-selection-summary { margin-top: 14px; padding: 12px 14px; border: 1px solid #e5e6eb; border-radius: 8px; background: #f7f8fa; }
.new-chat-selection-summary__head { display: flex; align-items: center; justify-content: space-between; gap: 12px; color: #1d2129; font-size: 13px; }
.new-chat-selection-summary__head span { color: #86909c; font-size: 12px; }
.new-chat-selection-summary dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 18px; margin: 10px 0 0; }
.new-chat-selection-summary dl div { display: flex; min-width: 0; align-items: center; justify-content: space-between; gap: 10px; }
.new-chat-selection-summary dt { color: #86909c; font-size: 12px; }
.new-chat-selection-summary dd { margin: 0; overflow: hidden; color: #1d2129; font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; text-overflow: ellipsis; white-space: nowrap; }
.new-chat-selection-summary__status { margin: 10px 0 0; color: #4e5969; font-size: 12px; line-height: 1.5; }
.new-chat-selection-summary__status.is-error { color: var(--color-danger-text, #c45656); }
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
.new-chat-gateway-select-row__deploy { flex: 0 0 34px; width: 34px; height: 34px; min-width: 34px; padding: 0; }
.new-chat-gateway-option { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; }
.new-chat-gateway-option__name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.new-chat-gateway-option__status { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 6px; color: #00b42a; font-size: 12px; font-weight: 600; }
.new-chat-gateway-option__dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; box-shadow: 0 0 0 3px rgba(0, 180, 42, .12); }
:global(.new-chat-gateway-select-popper .el-select-dropdown__wrap) { max-height: 220px; }
:global(.new-chat-scope-popover.el-popper) {
  box-sizing: border-box;
  max-width: calc(100vw - 24px);
  padding: 8px;
}
:global(.new-chat-scope-popover .hfl-dir-tree) {
  max-height: min(36vh, 300px);
  overflow-x: hidden;
}
:global(.new-chat-scope-popover .el-tree-node__content) { min-width: 0; }
:global(.new-chat-scope-popover .hfl-dir-tree-node__text) { overflow: hidden; }
:global(.new-chat-scope-popover .hfl-dir-tree-node__label),
:global(.new-chat-scope-popover .hfl-dir-tree-node__path) {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  overflow-wrap: normal;
}
@media (max-width: 900px) {
  .new-chat-grid { grid-template-columns: 1fr; }
  .new-chat-analysis-options { grid-template-columns: 1fr; }
  .new-chat-scope-stack__header { display: none; }
  .new-chat-scope-row { grid-template-columns: 28px minmax(0, 1fr) 40px; }
  .new-chat-scope-row__summary { grid-row: 2; grid-column: 2 / 4; }
  .new-chat-scope-row__remove { grid-row: 1; grid-column: 3; }
  .new-chat-selection-summary dl { grid-template-columns: 1fr; }
}
</style>
