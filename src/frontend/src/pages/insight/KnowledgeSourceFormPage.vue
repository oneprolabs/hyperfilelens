<script setup lang="ts">
import '../../styles/fullscreen-form-styles'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { lensKnowledgePath } from '../../lib/lensEngineRoutes'
import { useI18n } from 'vue-i18n'
import { ArrowLeft, File, FolderOpen, Plus, RefreshCw, TextCursorInput, Trash2 } from 'lucide-vue-next'
import { formatLocalDateTime } from '../../lib/dateTime'
import { formatBytes } from '../../lib/kopiaProgress'
import type { BackupSourceSnapshot } from '../../lib/protectionBackupConfigApi'
import KnowledgeSourceRetrievalEnhancement from '../../components/insight/KnowledgeSourceRetrievalEnhancement.vue'
import HflPopover from '../../components/HflPopover.vue'
import { useKnowledgeSourceForm, type BackupScopePickerNode, type KnowledgeSourceType } from '../../composables/useKnowledgeSourceForm'
import { routeLocationWithListRefresh } from '../../lib/listRouteRefresh'
import {
  gatewayAiPhase,
  gatewayAiPhaseLabelKey,
  gatewayAiPhaseTagType,
  gatewaySelectLine,
} from '../../lib/gatewayPickerDisplay'
import type { LensGatewayInsight } from '../../lib/lensApi'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const GATEWAY_DEPLOY_ROUTE = { path: '/node/nodes/deploy', query: { role: 'gateway' } } as const

const sourceType = ref<KnowledgeSourceType>('backup_source')

const editingId = computed(() => {
  const raw = route.params.id
  if (typeof raw !== 'string' || !raw) return null
  const id = Number.parseInt(raw, 10)
  return Number.isFinite(id) ? id : null
})

const {
  loading,
  saving,
  isEditing,
  name,
  gatewayId,
  onlineGateways,
  offlineGatewayCount,
  gatewaysForPicker,
  gatewaysRefreshing,
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
  ingestPolicy,
  canSubmit,
  isBackupSource,
  isGatewayLocal,
  gatewayBrowseLoading,
  gatewaySelectedPath,
  gatewayBrowseRoot,
  gatewayDirectoryValid,
  gatewayDirPickerOpen,
  gatewayDirTreeRevision,
  init,
  submit,
  loadSnapshots,
  refreshGateways,
  loadBackupScopePickerNode,
  setBackupScopePickerOpen,
  addBackupScopeEntry,
  removeBackupScopeEntry,
  updateBackupScopeEntryInput,
  validateBackupScopeEntry,
  validateBackupScopeEntryOnBlur,
  pickBackupScopeForEntry,
  loadGatewayDirTreeNode,
  setGatewayDirPickerOpen,
  updateGatewayDirectoryInput,
  validateGatewayDirectoryPath,
  pickGatewayDirectory,
} = useKnowledgeSourceForm(editingId, sourceType)

const gatewayDirInputRef = ref<HTMLElement | null>(null)
const gatewayDirPickerWidth = ref(360)

function syncGatewayDirPickerWidth() {
  const width = gatewayDirInputRef.value?.getBoundingClientRect().width
  if (width && width > 0) gatewayDirPickerWidth.value = Math.ceil(width)
}

watch(gatewayDirPickerOpen, (open) => {
  if (open) void nextTick(() => syncGatewayDirPickerWidth())
})

const pageTitle = computed(() =>
  isEditing.value ? t('insight.kb.editPageTitle') : t('insight.kb.addPageTitle'),
)

const pageDesc = computed(() =>
  isEditing.value ? t('insight.kb.editPageDesc') : t('insight.kb.addPageDesc'),
)

function gatewayAiLabel(row: LensGatewayInsight) {
  return t(gatewayAiPhaseLabelKey(gatewayAiPhase(row)))
}

function gatewayAiTagType(row: LensGatewayInsight): 'success' | 'warning' | 'danger' | 'info' {
  return gatewayAiPhaseTagType(gatewayAiPhase(row))
}

function openGatewayDeploy() {
  const { href } = router.resolve(GATEWAY_DEPLOY_ROUTE)
  window.open(href, '_blank', 'noopener,noreferrer')
}

function handleBack() {
  router.push(routeLocationWithListRefresh(lensKnowledgePath()))
}

async function handleSubmit() {
  const ok = await submit()
  if (ok) handleBack()
}

const backupScopePickerWidth = ref(460)

function isBackupScopePickerOpen(entryId: string) {
  return openBackupScopePickerId.value === entryId
}

function handleBackupScopeNodeClick(entryId: string, data: BackupScopePickerNode) {
  pickBackupScopeForEntry(entryId, data)
}

function handleGatewayDirNodeClick(data: { path: string }) {
  pickGatewayDirectory(data.path)
}

function snapshotOptionTime(row: BackupSourceSnapshot) {
  return row.finished_at || row.started_at || row.created_at || ''
}

function snapshotOptionLabel(row: BackupSourceSnapshot) {
  const time = snapshotOptionTime(row)
  const timeLabel = time ? formatLocalDateTime(time) : '—'
  return `${timeLabel} · ${formatBytes(row.total_size_bytes)}`
}

onMounted(() => {
  void init()
})
</script>

<template>
  <div class="fullscreen-form-fullscreen resource-add-fullscreen ks-form-fullscreen">
    <div class="fullscreen-form-page">
      <header class="fullscreen-form-header">
        <button
          type="button"
          class="fullscreen-form-header__back"
          @click="handleBack"
        >
          <ArrowLeft
            class="fullscreen-form-header__back-icon"
            :size="18"
          />
        </button>
        <div class="fullscreen-form-header__content">
          <h1 class="fullscreen-form-header__title">
            {{ pageTitle }}
          </h1>
          <p class="fullscreen-form-header__desc">
            {{ pageDesc }}
          </p>
        </div>
      </header>

      <div
        v-loading="loading"
        class="fullscreen-form-layout"
      >
        <div class="fullscreen-form-main">
          <div class="fullscreen-form-step-stack">
            <section class="fullscreen-form-card fullscreen-form-section">
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.kb.sectionBasicInfo') }}
              </h3>
              <ElForm
                label-position="top"
                class="fullscreen-form-el-form"
              >
                <ElFormItem
                  :label="t('insight.kb.fieldName')"
                  required
                >
                  <ElInput
                    v-model="name"
                    maxlength="160"
                    :placeholder="t('insight.kb.fieldNamePlaceholder')"
                  />
                  <p class="ks-field-hint">
                    {{ t('insight.kb.fieldNameHint') }}
                  </p>
                </ElFormItem>
                <ElFormItem
                  :label="t('insight.kb.fieldSourceType')"
                  required
                >
                  <ElSelect
                    v-model="sourceType"
                    fit-input-width
                    popper-class="ks-source-type-select-popper"
                    style="width: 100%"
                    :disabled="isEditing"
                  >
                    <ElOption
                      :label="t('insight.kb.sourceTypeBackup')"
                      value="backup_source"
                    >
                      {{ t('insight.kb.sourceTypeBackupOption') }}
                    </ElOption>
                    <ElOption
                      :label="t('insight.kb.sourceTypeGatewayLocal')"
                      value="gateway_local"
                    >
                      {{ t('insight.kb.sourceTypeGatewayLocalOption') }}
                    </ElOption>
                  </ElSelect>
                  <p class="ks-field-hint">
                    {{ t('insight.kb.fieldSourceTypeHint') }}
                  </p>
                </ElFormItem>
              </ElForm>
            </section>

            <section
              v-if="isGatewayLocal"
              class="fullscreen-form-card fullscreen-form-section"
            >
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.kb.sourceTypeGatewayLocal') }}
              </h3>
              <p
                v-if="isEditing"
                class="ks-field-hint ks-section-desc"
              >
                {{ t('insight.kb.fieldSourceBindingReadonlyHint') }}
              </p>
              <ElForm
                label-position="top"
                class="fullscreen-form-el-form"
              >
                <ElFormItem
                  :label="t('insight.kb.colGateway')"
                  required
                >
                  <p
                    v-if="!isEditing && offlineGatewayCount > 0"
                    class="ks-field-hint ks-field-hint--spaced"
                  >
                    {{ t('insight.kb.gatewayOnlineOnlyNote', { n: offlineGatewayCount }) }}
                  </p>
                  <div class="ks-gateway-select-row">
                    <div class="ks-gateway-select-row__controls">
                      <ElSelect
                        v-model="gatewayId"
                        class="ks-gateway-select-row__select"
                        filterable
                        fit-input-width
                        :loading="gatewaysRefreshing"
                        :disabled="isEditing || onlineGateways.length === 0"
                        popper-class="ks-gateway-select-popper"
                        :placeholder="t('insight.kb.colGateway')"
                      >
                        <ElOption
                          v-for="row in gatewaysForPicker"
                          :key="row.id"
                          :label="gatewaySelectLine(row)"
                          :value="row.id"
                        >
                          <div class="ks-gateway-option">
                            <span class="ks-gateway-option__name">{{ gatewaySelectLine(row) }}</span>
                            <span class="ks-gateway-option__tags">
                              <ElTag
                                size="small"
                                type="success"
                                effect="plain"
                              >
                                {{ t('protection.sourceResources.nodeStatusOnline') }}
                              </ElTag>
                              <ElTag
                                size="small"
                                :type="gatewayAiTagType(row)"
                                effect="plain"
                              >
                                {{ gatewayAiLabel(row) }}
                              </ElTag>
                            </span>
                          </div>
                        </ElOption>
                      </ElSelect>
                      <ElButton
                        v-if="!isEditing"
                        class="hfl-refresh-button ks-gateway-select-row__refresh"
                        :title="t('insight.kb.gatewayRefresh')"
                        :aria-label="t('insight.kb.gatewayRefresh')"
                        :disabled="gatewaysRefreshing"
                        @click="refreshGateways"
                      >
                        <RefreshCw
                          :size="16"
                          :class="{ 'is-spinning': gatewaysRefreshing }"
                        />
                      </ElButton>
                      <ElButton
                        v-if="!isEditing"
                        class="fullscreen-form-icon-btn ks-gateway-select-row__deploy"
                        :title="t('nodesPage.deployGateway')"
                        :aria-label="t('nodesPage.deployGateway')"
                        @click="openGatewayDeploy"
                      >
                        <Plus :size="14" />
                      </ElButton>
                    </div>
                  </div>
                  <p
                    v-if="!isEditing && !gatewaysRefreshing && onlineGateways.length === 0"
                    class="ks-field-hint ks-field-hint--warn"
                  >
                    {{ t('insight.kb.gatewayNoOnline') }}
                  </p>
                  <p
                    v-else-if="!isEditing && onlineGateways.length > 0"
                    class="ks-field-hint"
                  >
                    {{ t('insight.kb.fieldGatewayLocalGatewayHint') }}
                  </p>
                </ElFormItem>

                <ElFormItem
                  :label="t('insight.kb.wizardStepPath')"
                  required
                >
                  <HflPopover
                    :visible="!isEditing && gatewayDirPickerOpen"
                    trigger="click"
                    placement="bottom-start"
                    :width="gatewayDirPickerWidth"
                    popper-class="ks-gateway-dir-popover"
                    @update:visible="(open) => !isEditing && setGatewayDirPickerOpen(open)"
                  >
                    <template #reference>
                      <div
                        ref="gatewayDirInputRef"
                        class="ks-dir-path-input"
                      >
                        <ElInput
                          :model-value="gatewaySelectedPath"
                          :placeholder="t('insight.kb.phSelectOrEnterGatewayDirectory')"
                          :disabled="isEditing || !gatewayId"
                          @update:model-value="updateGatewayDirectoryInput"
                          @blur="validateGatewayDirectoryPath(false)"
                        >
                          <template #prefix>
                            <TextCursorInput
                              :size="14"
                              class="ks-dir-path-input__type-icon"
                            />
                          </template>
                          <template #append>
                            <ElTooltip
                              :content="t('insight.kb.btnBrowseDirectory')"
                              placement="top"
                            >
                              <ElButton
                                class="ks-dir-path-input__btn"
                                :disabled="isEditing || !gatewayId"
                                @click.stop="setGatewayDirPickerOpen(!gatewayDirPickerOpen)"
                              >
                                <FolderOpen :size="16" />
                              </ElButton>
                            </ElTooltip>
                          </template>
                        </ElInput>
                      </div>
                    </template>
                    <div class="ks-gateway-dir-tree hfl-dir-tree-shell">
                      <el-tree
                        :key="`ks-gateway-dir-${gatewayId}-${gatewayDirTreeRevision}`"
                        v-loading="gatewayBrowseLoading"
                        class="hfl-dir-tree hfl-dir-tree--tall"
                        node-key="path"
                        lazy
                        :expand-on-click-node="false"
                        :load="loadGatewayDirTreeNode"
                        :props="{ label: 'label', children: 'children', isLeaf: 'isLeaf' }"
                        :current-node-key="gatewaySelectedPath"
                        highlight-current
                        @node-click="handleGatewayDirNodeClick"
                      >
                        <template #default="{ data }">
                          <div class="hfl-dir-tree-node">
                            <FolderOpen
                              :size="15"
                              class="hfl-dir-tree-node__icon"
                            />
                            <div class="hfl-dir-tree-node__text">
                              <span class="hfl-dir-tree-node__label">{{ data.label }}</span>
                              <span class="hfl-dir-tree-node__path">{{ data.path }}</span>
                            </div>
                          </div>
                        </template>
                      </el-tree>
                    </div>
                  </HflPopover>
                  <p
                    v-if="gatewayBrowseRoot && gatewaySelectedPath.trim() && !gatewayDirectoryValid"
                    class="ks-field-hint ks-field-hint--warn"
                  >
                    {{ t('insight.kb.gatewayDirectoryOutOfWorkspace', { root: gatewayBrowseRoot }) }}
                  </p>
                  <p class="ks-field-hint">
                    {{ t('insight.kb.fieldGatewayLocalDirectoryHint') }}
                  </p>
                </ElFormItem>
              </ElForm>
            </section>

            <section
              v-if="isBackupSource"
              class="fullscreen-form-card fullscreen-form-section"
            >
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.kb.sourceTypeBackup') }}
              </h3>
              <p
                v-if="isEditing"
                class="ks-field-hint ks-section-desc"
              >
                {{ t('insight.kb.fieldSourceBindingReadonlyHint') }}
              </p>
              <ElForm
                label-position="top"
                class="fullscreen-form-el-form"
              >
                <div class="ks-backup-form-row">
                  <ElFormItem
                    class="ks-backup-form-row__item"
                    :label="t('insight.kb.fieldBackupSource')"
                    required
                  >
                    <div class="ks-gateway-select-row">
                      <div class="ks-gateway-select-row__controls">
                        <ElSelect
                          v-model="selectedBackupConfigId"
                          class="ks-gateway-select-row__select"
                          filterable
                          fit-input-width
                          :placeholder="t('insight.kb.phSelectBackupSource')"
                          :loading="snapshotLoading"
                          :disabled="isEditing || (!snapshotLoading && backupSourceOptions.length === 0)"
                        >
                          <ElOption
                            v-for="row in backupSourceOptions"
                            :key="row.backupConfigId"
                            :label="row.label"
                            :value="row.backupConfigId"
                          />
                        </ElSelect>
                        <ElButton
                          v-if="!isEditing"
                          class="hfl-refresh-button ks-gateway-select-row__refresh"
                          :title="t('insight.kb.backupSourceRefresh')"
                          :aria-label="t('insight.kb.backupSourceRefresh')"
                          :disabled="snapshotLoading"
                          @click="loadSnapshots"
                        >
                          <RefreshCw
                            :size="16"
                            :class="{ 'is-spinning': snapshotLoading }"
                          />
                        </ElButton>
                      </div>
                    </div>
                    <p
                      v-if="!isEditing && !snapshotLoading && backupSourceOptions.length === 0"
                      class="ks-field-hint ks-field-hint--warn"
                    >
                      {{ t('insight.kb.backupSourceNoAvailable') }}
                    </p>
                    <p
                      v-else
                      class="ks-field-hint"
                    >
                      {{ t('insight.kb.fieldBackupSourceHint') }}
                    </p>
                  </ElFormItem>

                  <ElFormItem
                    class="ks-backup-form-row__item"
                    :label="t('insight.kb.fieldSnapshot')"
                    required
                  >
                    <ElSelect
                      v-model="snapshotPickerValue"
                      filterable
                      fit-input-width
                      style="width: 100%"
                      :placeholder="t('insight.kb.phSelectSnapshot')"
                      :disabled="isEditing || !selectedBackupConfigId"
                    >
                      <ElOption
                        :label="t('insight.kb.snapshotLatestOption')"
                        :value="SNAPSHOT_PICKER_LATEST"
                      />
                      <ElOption
                        v-for="row in snapshotsForSelectedBackupSource"
                        :key="row.id"
                        :label="snapshotOptionLabel(row)"
                        :value="row.id"
                      />
                    </ElSelect>
                    <p
                      v-if="!selectedBackupConfigId"
                      class="ks-field-hint"
                    >
                      {{ t('insight.kb.snapshotPickBackupFirst') }}
                    </p>
                    <p
                      v-else
                      class="ks-field-hint"
                    >
                      {{ t('insight.kb.fieldSnapshotHint') }}
                    </p>
                  </ElFormItem>
                </div>

                <ElFormItem
                  required
                  class="ks-index-scope-form-item"
                >
                  <div
                    class="ks-index-scope-stack"
                    :class="{ 'ks-index-scope-stack--readonly': isEditing }"
                  >
                    <div
                      class="ks-index-scope-stack__header"
                      aria-hidden="true"
                    >
                      <span />
                      <span class="ks-index-scope-stack__label">
                        {{ t('insight.kb.fieldRestoreScope') }}
                        <span class="ks-index-scope-stack__required">*</span>
                      </span>
                      <span v-if="!isEditing">{{ t('protection.backupsPage.colActions') }}</span>
                    </div>
                    <div
                      v-for="(scopeEntry, scopeIndex) in backupScopeEntries"
                      :key="scopeEntry.id"
                      class="ks-index-scope-row"
                    >
                      <span class="ks-index-scope-row__index">
                        {{ String(scopeIndex + 1).padStart(2, '0') }}
                      </span>
                      <div class="ks-index-scope-row__field">
                        <HflPopover
                          :visible="!isEditing && isBackupScopePickerOpen(scopeEntry.id)"
                          trigger="click"
                          placement="bottom-start"
                          :width="backupScopePickerWidth"
                          popper-class="ks-backup-scope-popover"
                          @update:visible="(open) => !isEditing && setBackupScopePickerOpen(scopeEntry.id, open)"
                        >
                          <template #reference>
                            <div class="ks-backup-scope-input">
                              <ElInput
                                :model-value="scopeEntry.path"
                                :clearable="!isEditing"
                                :placeholder="t('insight.kb.phSelectOrEnterRestoreScope')"
                                :disabled="isEditing || !effectiveSnapshotId || snapshotDirectories.length === 0"
                                @update:model-value="updateBackupScopeEntryInput(scopeEntry.id, $event)"
                                @blur="validateBackupScopeEntryOnBlur(scopeEntry.id)"
                                @keydown.enter.prevent="validateBackupScopeEntry(scopeEntry.id)"
                              >
                                <template #prefix>
                                  <TextCursorInput
                                    :size="14"
                                    class="ks-backup-scope-input__type-icon"
                                  />
                                </template>
                                <template #append>
                                  <ElTooltip
                                    :content="t('insight.kb.btnBrowseRestoreScope')"
                                    placement="top"
                                  >
                                    <ElButton
                                      class="ks-backup-scope-input__btn"
                                      :disabled="isEditing || !effectiveSnapshotId || snapshotDirectories.length === 0"
                                      @click.stop="setBackupScopePickerOpen(scopeEntry.id, !isBackupScopePickerOpen(scopeEntry.id))"
                                    >
                                      <FolderOpen :size="16" />
                                    </ElButton>
                                  </ElTooltip>
                                </template>
                              </ElInput>
                            </div>
                          </template>
                          <div class="ks-backup-scope-tree hfl-dir-tree-shell">
                            <el-tree
                              :key="`ks-backup-scope-${scopeEntry.id}-${effectiveSnapshotId}-${backupScopeTreeRevision}`"
                              v-loading="backupScopeBrowseLoading"
                              class="hfl-dir-tree hfl-dir-tree--tall"
                              node-key="id"
                              lazy
                              highlight-current
                              :expand-on-click-node="false"
                              :load="loadBackupScopePickerNode"
                              :props="{ label: 'label', children: 'children', isLeaf: 'isLeaf' }"
                              :current-node-key="scopeEntry.path"
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
                                    <span class="hfl-dir-tree-node__label">{{ data.label }}</span>
                                    <span
                                      v-if="data.path"
                                      class="hfl-dir-tree-node__path"
                                    >{{ data.path }}</span>
                                  </div>
                                </div>
                              </template>
                            </el-tree>
                          </div>
                        </HflPopover>
                      </div>
                      <div class="ks-index-scope-row__actions">
                        <ElButton
                          v-if="!isEditing"
                          type="danger"
                          size="small"
                          class="ks-index-scope-row__remove"
                          :disabled="backupScopeEntries.length <= 1"
                          :title="t('protection.backupsPage.ariaRemove')"
                          :aria-label="t('protection.backupsPage.ariaRemove')"
                          @click="removeBackupScopeEntry(scopeEntry.id)"
                        >
                          <Trash2 :size="14" />
                        </ElButton>
                      </div>
                    </div>
                    <div
                      v-if="!isEditing"
                      class="ks-index-scope-stack__add-wrap"
                    >
                      <button
                        type="button"
                        class="ks-index-scope-stack__add"
                        :disabled="!effectiveSnapshotId || snapshotDirectories.length === 0"
                        @click="addBackupScopeEntry"
                      >
                        <Plus :size="16" />
                        <span>{{ t('insight.kb.btnAddRestoreScope') }}</span>
                      </button>
                    </div>
                  </div>
                  <p
                    v-if="!effectiveSnapshotId"
                    class="ks-field-hint"
                  >
                    {{ t('insight.kb.backupScopePickSnapshotFirst') }}
                  </p>
                  <p
                    v-else-if="snapshotDirectories.length === 0"
                    class="ks-field-hint ks-field-hint--warn"
                  >
                    {{ t('insight.kb.restoreScopeNoDirectories') }}
                  </p>
                  <p
                    v-else
                    class="ks-field-hint"
                  >
                    {{ t('insight.kb.fieldRestoreScopeHint') }}
                  </p>
                </ElFormItem>

                <ElFormItem
                  :label="t('insight.kb.colGateway')"
                  required
                >
                  <p
                    v-if="!isEditing && offlineGatewayCount > 0"
                    class="ks-field-hint ks-field-hint--spaced"
                  >
                    {{ t('insight.kb.gatewayOnlineOnlyNote', { n: offlineGatewayCount }) }}
                  </p>
                  <div class="ks-gateway-select-row">
                    <div class="ks-gateway-select-row__controls">
                      <ElSelect
                        v-model="gatewayId"
                        class="ks-gateway-select-row__select"
                        filterable
                        fit-input-width
                        :loading="gatewaysRefreshing"
                        :disabled="isEditing || onlineGateways.length === 0"
                        popper-class="ks-gateway-select-popper"
                        :placeholder="t('insight.kb.colGateway')"
                      >
                        <ElOption
                          v-for="row in gatewaysForPicker"
                          :key="row.id"
                          :label="gatewaySelectLine(row)"
                          :value="row.id"
                        >
                          <div class="ks-gateway-option">
                            <span class="ks-gateway-option__name">{{ gatewaySelectLine(row) }}</span>
                            <span class="ks-gateway-option__tags">
                              <ElTag
                                size="small"
                                type="success"
                                effect="plain"
                              >
                                {{ t('protection.sourceResources.nodeStatusOnline') }}
                              </ElTag>
                              <ElTag
                                size="small"
                                :type="gatewayAiTagType(row)"
                                effect="plain"
                              >
                                {{ gatewayAiLabel(row) }}
                              </ElTag>
                            </span>
                          </div>
                        </ElOption>
                      </ElSelect>
                      <ElButton
                        v-if="!isEditing"
                        class="hfl-refresh-button ks-gateway-select-row__refresh"
                        :title="t('insight.kb.gatewayRefresh')"
                        :aria-label="t('insight.kb.gatewayRefresh')"
                        :disabled="gatewaysRefreshing"
                        @click="refreshGateways"
                      >
                        <RefreshCw
                          :size="16"
                          :class="{ 'is-spinning': gatewaysRefreshing }"
                        />
                      </ElButton>
                      <ElButton
                        v-if="!isEditing"
                        class="fullscreen-form-icon-btn ks-gateway-select-row__deploy"
                        :title="t('nodesPage.deployGateway')"
                        :aria-label="t('nodesPage.deployGateway')"
                        @click="openGatewayDeploy"
                      >
                        <Plus :size="14" />
                      </ElButton>
                    </div>
                  </div>
                  <p
                    v-if="!isEditing && !gatewaysRefreshing && onlineGateways.length === 0"
                    class="ks-field-hint ks-field-hint--warn"
                  >
                    {{ t('insight.kb.gatewayNoOnline') }}
                  </p>
                  <p
                    v-else
                    class="ks-field-hint"
                  >
                    {{ t('insight.kb.fieldGatewayHint') }}
                  </p>
                </ElFormItem>
              </ElForm>
            </section>

            <section class="fullscreen-form-card fullscreen-form-section">
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.kb.retrieval.documentContentTitle') }}
              </h3>
              <KnowledgeSourceRetrievalEnhancement
                v-model="ingestPolicy"
                section="document"
                :show-lead="true"
              />
            </section>

            <section class="fullscreen-form-card fullscreen-form-section">
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.kb.retrieval.standaloneImagesTitle') }}
              </h3>
              <KnowledgeSourceRetrievalEnhancement
                v-model="ingestPolicy"
                section="images"
              />
            </section>

            <section class="fullscreen-form-card fullscreen-form-section">
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.kb.retrieval.globalConversionLimitsTitle') }}
              </h3>
              <KnowledgeSourceRetrievalEnhancement
                v-model="ingestPolicy"
                section="limits"
              />
            </section>
          </div>

          <footer class="fullscreen-form-footer">
            <ElButton @click="handleBack">
              {{ t('common.cancel') }}
            </ElButton>
            <ElButton
              type="primary"
              :loading="saving"
              :disabled="saving || loading || !canSubmit"
              @click="handleSubmit"
            >
              {{ isEditing ? t('common.save') : t('insight.kb.dialogConfirm') }}
            </ElButton>
          </footer>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ks-form-fullscreen .fullscreen-form-page {
  display: flex;
  flex-direction: column;
  min-height: calc(var(--app-viewport-height) - var(--app-header-height));
}

.ks-section-desc {
  margin: 0 0 14px;
  font-size: 13px;
  line-height: 1.55;
  color: var(--color-text-secondary);
}

.ks-field-hint {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.45;
  color: var(--color-text-secondary);
}

.ks-field-hint--warn {
  color: var(--color-warning, #e6a23c);
}

.ks-field-hint--spaced {
  margin-bottom: 8px;
}

.ks-type-option {
  font-size: 13px;
  line-height: 1.45;
  font-weight: 400;
  color: var(--color-text-regular, #606266);
}

.ks-type-option__line {
  margin: 0;
}

.ks-type-option__line + .ks-type-option__line {
  margin-top: 4px;
}

.ks-type-option__key {
  font-weight: 500;
  color: var(--color-text-title, #303133);
}

.ks-type-option__key::after {
  content: ' · ';
  font-weight: 400;
  color: var(--color-text-secondary, #909399);
}

:global(.ks-source-type-select-popper.el-popper) {
  max-width: min(560px, calc(100vw - 32px));
}

:global(.ks-source-type-select-popper .el-select-dropdown__item) {
  height: auto;
  min-height: 34px;
  padding-top: 8px;
  padding-bottom: 8px;
  line-height: 1.45;
  font-weight: 400;
  white-space: normal;
}

:global(.ks-source-type-select-popper .el-select-dropdown__item.is-selected),
:global(.ks-source-type-select-popper .el-select-dropdown__item.is-hovering) {
  font-weight: 400;
}

.ks-index-scope-stack {
  display: flex;
  flex-direction: column;
  width: 100%;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 8px;
  background: #fff;
  overflow: visible;
}

.ks-index-scope-stack__header,
.ks-index-scope-row,
.ks-index-scope-stack__add-wrap {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) 44px;
  gap: 8px;
  align-items: center;
}

.ks-index-scope-stack--readonly .ks-index-scope-stack__header,
.ks-index-scope-stack--readonly .ks-index-scope-row {
  grid-template-columns: 34px minmax(0, 1fr);
}

.ks-index-scope-stack__header {
  padding: 8px 10px;
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 700;
  line-height: 18px;
  background: rgb(248 250 252);
  border-radius: 8px 8px 0 0;
}

.ks-index-scope-stack__label {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.ks-index-scope-stack__required {
  color: var(--color-error);
  font-weight: 800;
  line-height: 1;
}

.ks-index-scope-row {
  padding: 8px 10px;
  border-top: 1px solid rgba(226, 232, 240, 0.92);
}

.ks-index-scope-row__index {
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 700;
  text-align: center;
}

.ks-index-scope-row__field {
  min-width: 0;
}

.ks-index-scope-row__actions {
  display: flex;
  justify-content: center;
}

.ks-index-scope-row__remove {
  width: 32px;
  height: 32px;
  padding: 0;
}

.ks-index-scope-stack__add-wrap {
  padding: 8px 10px 10px;
  border-top: 1px solid rgba(226, 232, 240, 0.92);
}

.ks-index-scope-stack__add {
  display: flex;
  grid-column: 2 / span 1;
  width: 100%;
  min-height: 32px;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin: 0;
  border: 1px dashed rgba(148, 163, 184, 0.8);
  border-radius: 8px;
  background: rgb(248 250 252);
  color: rgb(71 85 105);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.ks-index-scope-stack__add:hover:not(:disabled) {
  border-color: rgb(59 130 246);
  color: rgb(29 78 216);
  background: rgb(239 246 255);
}

.ks-index-scope-stack__add:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.ks-index-scope-form-item :deep(.el-form-item__label) {
  display: none;
}

.ks-index-scope-form-item :deep(.el-form-item__content) {
  width: 100%;
}

.ks-backup-form-row {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
  gap: 0 20px;
  margin-bottom: 2px;
}

.ks-backup-form-row__item {
  min-width: 0;
}

.ks-backup-form-row :deep(.el-form-item) {
  margin-bottom: 18px;
}

@media (max-width: 900px) {
  .ks-backup-form-row {
    grid-template-columns: 1fr;
    gap: 0;
  }
}

.ks-gateway-select-row {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  width: 100%;
}

.ks-gateway-select-row__controls {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.ks-gateway-select-row__select {
  flex: 1 1 auto;
  min-width: 0;
  width: 100%;
}

.ks-gateway-select-row__refresh {
  flex: 0 0 34px;
}

.ks-gateway-select-row__refresh .is-spinning {
  animation: ks-spin 1s linear infinite;
}

.ks-gateway-select-row__deploy {
  flex: 0 0 40px;
}

.ks-gateway-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}

.ks-gateway-option__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ks-gateway-option__tags {
  display: inline-flex;
  flex-shrink: 0;
  gap: 6px;
}

.ks-dir-path-input {
  width: 100%;
}

.ks-dir-path-input__type-icon {
  color: rgb(100 116 139);
}

.ks-dir-path-input__btn {
  width: 36px;
  padding: 0;
}

.ks-gateway-dir-tree {
  min-width: 100%;
}

:global(.ks-gateway-dir-popover.el-popper) {
  padding: 10px 10px 10px 12px;
  box-sizing: border-box;
}

.ks-backup-scope-input {
  width: 100%;
}

.ks-backup-scope-input__type-icon {
  color: rgb(100 116 139);
}

.ks-backup-scope-input__btn {
  width: 36px;
  padding: 0;
}

.ks-backup-scope-tree {
  min-width: 100%;
}

:global(.ks-backup-scope-popover.el-popper) {
  padding: 10px 10px 10px 12px;
  box-sizing: border-box;
}

.ks-dir-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 360px;
  overflow: auto;
}

.ks-dir-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid rgb(226 232 240);
  border-radius: 8px;
  background: #fff;
  text-align: left;
  cursor: pointer;
}

.ks-dir-row--active {
  border-color: rgb(59 130 246);
  background: rgb(239 246 255);
}

.ks-dir-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ks-form-loading {
  display: flex;
  justify-content: center;
  padding: 24px;
}

.ks-spin {
  animation: ks-spin 1s linear infinite;
}

.ks-readonly-grid {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: 10px 16px;
  margin: 0;
  font-size: 13px;
}

.ks-readonly-grid dt {
  margin: 0;
  color: var(--color-text-secondary);
}

.ks-readonly-grid dd {
  margin: 0;
  word-break: break-all;
}

@keyframes ks-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
