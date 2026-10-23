<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { formatLocalDateTime } from '../../lib/dateTime'
import { formatBytes } from '../../lib/kopiaProgress'
import { apiErrorMessage } from '../../lib/api'
import { DETAIL_EMPTY } from '../../lib/nodeInventoryDisplay'
import { copilotReasonLabel, copilotWarningLabel } from '../../lib/copilotDisplay'
import { lifecycleStatusTagAttrs } from '../../lib/statusTag'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import HflDetailDrawerFooter from '../../components/HflDetailDrawerFooter.vue'
import {
  fetchKnowledgeSource,
  patchKnowledgeSource,
  type LensKnowledgeSource,
} from '../../lib/lensApi'
import {
  conversionAllOk,
  conversionEmptyResult,
  conversionPhase,
  conversionProblemItems,
  conversionWarningsForDisplay,
} from '../../lib/conversionSummary'
import { getBackupSourceSnapshot, type BackupSourceSnapshot } from '../../lib/protectionBackupConfigApi'
import { defaultLensIngestPolicy, normalizeLensIngestPolicy } from '../../lib/knowledgeSourceIngestPolicy'
import KnowledgeSourceRetrievalEnhancement from '../../components/insight/KnowledgeSourceRetrievalEnhancement.vue'

const props = defineProps<{
  open: boolean
  row: LensKnowledgeSource | null
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  updated: [row: LensKnowledgeSource]
  edit: [id: number]
}>()

const { t } = useI18n()
const tab = ref<'source' | 'retrieval'>('source')
const busy = ref(false)
const loading = ref(false)
const detail = ref<LensKnowledgeSource | null>(null)
const snapshotRow = ref<BackupSourceSnapshot | null>(null)
const ingestPolicy = ref(defaultLensIngestPolicy())
const ingestPolicySnapshot = ref('')

const drawerOpen = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
})

const { drawerSize, updateDrawerWidth, bindDrawerResize, unbindDrawerResize } = useResponsiveDrawerWidth()

const activeRow = computed(() => detail.value ?? props.row)
const normalizedIngestPolicy = computed(() => normalizeLensIngestPolicy(ingestPolicy.value))
const hasIngestChanges = computed(
  () => JSON.stringify(normalizedIngestPolicy.value) !== ingestPolicySnapshot.value,
)

const isBackupSource = computed(() => Boolean(activeRow.value?.backup_source_snapshot_id))

const sourceTypeLabel = computed(() =>
  isBackupSource.value
    ? t('insight.kb.sourceTypeBackup')
    : t('insight.kb.sourceTypeGatewayLocal'),
)

const scopePaths = computed(() => {
  const row = activeRow.value
  if (!row) return []
  const scopes = row.source_scopes_json?.map((item) => item.source_path?.trim()).filter(Boolean) ?? []
  if (scopes.length > 0) return scopes as string[]
  return row.source_path?.trim() ? [row.source_path.trim()] : []
})

const backupSourceLabel = computed(() => {
  if (!snapshotRow.value) return DETAIL_EMPTY
  return (
    snapshotRow.value.source_display_name
    || snapshotRow.value.backup_config_name
    || `#${snapshotRow.value.backup_config_id}`
  )
})

const snapshotLabel = computed(() => {
  if (!snapshotRow.value) {
    const id = activeRow.value?.backup_source_snapshot_id
    return id ? String(id) : DETAIL_EMPTY
  }
  const time = snapshotRow.value.finished_at || snapshotRow.value.started_at || snapshotRow.value.created_at || ''
  const timeLabel = time ? formatLocalDateTime(time) : '—'
  return `${timeLabel} · ${formatBytes(snapshotRow.value.total_size_bytes)}`
})

const linkedVersionLabel = computed(() => {
  const row = activeRow.value
  if (!row) return DETAIL_EMPTY
  if (row.linked_version_mode === 'latest') return t('insight.kb.versionLatest')
  return t('insight.kb.versionPinned', { id: row.pinned_snapshot_id ?? DETAIL_EMPTY })
})

const statusLabel = computed(() => {
  const s = activeRow.value?.status ?? ''
  if (s === 'ready') return t('insight.kb.statusReady')
  if (s === 'syncing') return t('insight.kb.statusSyncing')
  if (s === 'degraded') return t('insight.kb.statusDegraded')
  if (s === 'error') return t('insight.kb.statusError')
  if (s === 'paused') return t('insight.kb.statusPaused')
  if (s === 'learning' || s === 'provisioning') return t('insight.kb.statusSyncing')
  return s || DETAIL_EMPTY
})

const syncPhaseLabel = computed(() => {
  const phase = activeRow.value?.sync_phase || ''
  if (!phase) return DETAIL_EMPTY
  const key = `insight.kb.syncPhase.${phase}`
  const translated = t(key)
  return translated === key ? phase : translated
})

const documentConversion = computed(() => activeRow.value?.document_conversion ?? null)
const conversionSummaryLabel = computed(() => {
  const counts = documentConversion.value?.counts
  if (!counts) return ''
  const ready = counts.success + counts.unchanged
  if (!counts.total && !ready && !counts.failed && !counts.unsupported) return ''
  const parts = [t('insight.copilot.conversionReady', { count: ready })]
  if (counts.failed > 0) parts.push(t('insight.copilot.conversionFailed', { count: counts.failed }))
  if (counts.unsupported > 0) parts.push(t('insight.copilot.conversionUnsupported', { count: counts.unsupported }))
  return parts.join(' · ')
})
const conversionProblems = computed(() => conversionProblemItems(documentConversion.value).slice(0, 20))
const conversionOk = computed(() => conversionAllOk(documentConversion.value))
const conversionEmpty = computed(() => conversionEmptyResult(documentConversion.value))
const conversionRunning = computed(() => conversionPhase(documentConversion.value) === 'running')
const conversionFailed = computed(() => conversionPhase(documentConversion.value) === 'failed')
const conversionWarnings = computed(() => conversionWarningsForDisplay(documentConversion.value, 8))
const conversionSummaryDisplay = computed(() => {
  if (!documentConversion.value) return t('insight.kb.retrieval.conversionResultsEmpty')
  if (conversionSummaryLabel.value) return conversionSummaryLabel.value
  if (conversionRunning.value) return t('insight.copilot.documentConversionRunning')
  if (conversionEmpty.value) return t('insight.copilot.documentConversionEmpty')
  return t('insight.kb.retrieval.conversionResultsEmpty')
})
const conversionResultDisplay = computed(() => {
  if (conversionOk.value) return t('insight.kb.retrieval.conversionResultsOk')
  if (conversionEmpty.value) return t('insight.copilot.documentConversionEmpty')
  if (documentConversion.value && conversionFailed.value) {
    return documentConversion.value.error || t('insight.copilot.documentConversionPartial')
  }
  if (
    documentConversion.value
    && !conversionRunning.value
    && conversionSummaryLabel.value
    && !conversionOk.value
    && conversionProblems.value.length === 0
  ) {
    return t('insight.copilot.documentConversionPartial')
  }
  return ''
})

const conversionResultStatus = computed(() => {
  const failedCount = conversionProblems.value.length
  if (failedCount > 0) {
    return t('insight.kb.conversionFailedFilesCount', { n: failedCount }, failedCount)
  }
  return conversionResultDisplay.value
})

const conversionFailedFiles = computed(() =>
  conversionProblems.value.map((item) => {
    const name = item.name?.trim() || ''
    const reason = copilotReasonLabel(t, item.reason, item.reason_label)
    return {
      name: name || reason,
      reason,
      title: name ? `${name} · ${reason}` : reason,
    }
  }),
)

const conversionFailedFilesTitle = computed(() =>
  conversionFailedFiles.value.map((item) => item.title).join('\n'),
)

const conversionWarningLines = computed(() =>
  conversionWarnings.value.map((warning) =>
    copilotWarningLabel(t, warning.code, warning.label || warning.code),
  ),
)

function detailValueClass(text: string | number | null | undefined, mono = false) {
  const value = text == null || text === '' ? DETAIL_EMPTY : String(text)
  const empty = value === DETAIL_EMPTY
  return {
    'hfl-detail-row__empty': empty,
    'hfl-detail-row__value--mono': mono && !empty,
  }
}

function displayValue(text: string | number | null | undefined) {
  if (text == null || text === '') return DETAIL_EMPTY
  return String(text)
}

function organizationLabel(row: LensKnowledgeSource | null | undefined) {
  return row?.organization_name?.trim() || ''
}

function organizationId(row: LensKnowledgeSource | null | undefined) {
  return Number(row?.organization_id || 0) || null
}

async function loadDetail() {
  if (!props.row) {
    detail.value = null
    snapshotRow.value = null
    ingestPolicy.value = defaultLensIngestPolicy()
    ingestPolicySnapshot.value = ''
    return
  }
  loading.value = true
  try {
    const row = await fetchKnowledgeSource(props.row.id)
    detail.value = row
    ingestPolicy.value = normalizeLensIngestPolicy(row.ingest_policy)
    ingestPolicySnapshot.value = JSON.stringify(ingestPolicy.value)
    snapshotRow.value = null
    if (row.backup_source_snapshot_id) {
      try {
        snapshotRow.value = await getBackupSourceSnapshot(row.backup_source_snapshot_id)
      } catch {
        snapshotRow.value = null
      }
    }
  } catch (err) {
    detail.value = props.row
    ingestPolicy.value = normalizeLensIngestPolicy(props.row.ingest_policy)
    ingestPolicySnapshot.value = JSON.stringify(ingestPolicy.value)
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.loadFailed')), grouping: true })
  } finally {
    loading.value = false
  }
}

async function saveIngest() {
  if (!activeRow.value) return
  busy.value = true
  try {
    const updated = await patchKnowledgeSource(activeRow.value.id, {
      ingest_policy: normalizedIngestPolicy.value,
    })
    detail.value = updated
    ingestPolicy.value = normalizeLensIngestPolicy(updated.ingest_policy)
    ingestPolicySnapshot.value = JSON.stringify(ingestPolicy.value)
    ElMessage.success({ message: t('insight.kb.saveSuccess'), grouping: true })
    emit('updated', updated)
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.requestFailed')), grouping: true })
  } finally {
    busy.value = false
  }
}

function onDrawerOpened() {
  bindDrawerResize()
}

function onDrawerClosed() {
  unbindDrawerResize()
  tab.value = 'source'
  detail.value = null
  snapshotRow.value = null
  ingestPolicy.value = defaultLensIngestPolicy()
  ingestPolicySnapshot.value = ''
}

watch(
  () => [props.open, props.row?.id] as const,
  ([isOpen]) => {
    if (isOpen) {
      tab.value = 'source'
      void loadDetail()
    }
  },
)

watch(drawerOpen, (isOpen) => {
  if (isOpen) {
    void nextTick(() => requestAnimationFrame(() => updateDrawerWidth()))
  }
})

onUnmounted(() => {
  unbindDrawerResize()
})
</script>

<template>
  <ElDrawer
    v-model="drawerOpen"
    direction="rtl"
    destroy-on-close
    :modal="true"
    :size="drawerSize"
    class="hfl-detail-drawer ks-kb-detail-drawer"
    @opened="onDrawerOpened"
    @closed="onDrawerClosed"
  >
    <template #header>
      <span class="hfl-detail-drawer__title">{{ activeRow?.name || DETAIL_EMPTY }}</span>
    </template>

    <div
      v-loading="loading || busy"
      class="hfl-detail-drawer__body"
    >
      <template v-if="activeRow">
        <ElTabs
          v-model="tab"
          class="hfl-detail-tabs"
        >
          <ElTabPane
            :label="t('insight.kb.tabSource')"
            name="source"
          >
            <div class="hfl-detail-sections">
              <section class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">
                  {{ t('insight.kb.sectionGeneral') }}
                </h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.colName') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(activeRow.name)"
                    >
                      {{ displayValue(activeRow.name) }}
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.colSourceType') }}</span>
                    <span class="hfl-detail-row__value">{{ sourceTypeLabel }}</span>
                  </div>
                  <div
                    v-if="organizationLabel(activeRow) || organizationId(activeRow)"
                    class="hfl-detail-row"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.kb.colOrganization') }}</span>
                    <span class="hfl-detail-row__value">
                      <RouterLink
                        v-if="organizationId(activeRow) && organizationLabel(activeRow)"
                        :to="`/platform-ops/orgs/${organizationId(activeRow)}`"
                        class="hfl-table-name-link"
                      >
                        {{ organizationLabel(activeRow) }}
                      </RouterLink>
                      <span
                        v-else
                        :class="{ 'hfl-detail-row__empty': !organizationLabel(activeRow) }"
                      >{{ organizationLabel(activeRow) || DETAIL_EMPTY }}</span>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.colLearnStatus') }}</span>
                    <span class="hfl-detail-row__value">
                      <ElTag
                        v-bind="lifecycleStatusTagAttrs(activeRow.status)"
                        size="small"
                      >{{ statusLabel }}</ElTag>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldScanEnabled') }}</span>
                    <span class="hfl-detail-row__value">
                      <span
                        class="hfl-state-pill"
                        :class="activeRow.scan_enabled ? 'hfl-state-pill--enabled' : 'hfl-state-pill--disabled'"
                      >
                        {{ activeRow.scan_enabled ? t('insight.kb.scanEnabledOn') : t('insight.kb.scanEnabledOff') }}
                      </span>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.colRetrieval') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(activeRow.ingest_summary)"
                    >
                      {{ displayValue(activeRow.ingest_summary) }}
                    </span>
                  </div>
                  <div
                    v-if="activeRow.status === 'syncing'"
                    class="hfl-detail-row"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldSyncPhase') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(syncPhaseLabel)"
                    >
                      {{ syncPhaseLabel }}
                    </span>
                  </div>
                </div>
              </section>

              <section class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">
                  {{ t('insight.kb.sectionSource') }}
                </h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.colGateway') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(activeRow.gateway_name)"
                    >
                      {{ displayValue(activeRow.gateway_name) }}
                    </span>
                  </div>
                  <div
                    v-if="isBackupSource"
                    class="hfl-detail-row"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldBackupSource') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(backupSourceLabel)"
                    >
                      {{ backupSourceLabel }}
                    </span>
                  </div>
                  <div
                    v-if="isBackupSource"
                    class="hfl-detail-row"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldSnapshot') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(snapshotLabel)"
                    >
                      {{ snapshotLabel }}
                    </span>
                  </div>
                  <div
                    v-if="isBackupSource"
                    class="hfl-detail-row"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldSnapshotMode') }}</span>
                    <span class="hfl-detail-row__value">{{ linkedVersionLabel }}</span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.colSourcePath') }}</span>
                    <span
                      v-if="scopePaths.length <= 1"
                      class="hfl-detail-row__value"
                      :class="detailValueClass(scopePaths[0], true)"
                      :title="scopePaths[0] || undefined"
                    >
                      {{ displayValue(scopePaths[0]) }}
                    </span>
                    <span
                      v-else
                      class="hfl-detail-row__value hfl-detail-row__value--stacked hfl-detail-row__value--mono"
                      :title="scopePaths.join('\n')"
                    >
                      <span
                        v-for="(path, index) in scopePaths"
                        :key="`${index}-${path}`"
                        class="hfl-detail-row__text"
                      >{{ path }}</span>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldWorkspace') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(activeRow.workspace_path_on_lensnode, true)"
                    >
                      {{ displayValue(activeRow.workspace_path_on_lensnode) }}
                    </span>
                  </div>
                </div>
              </section>

              <section class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">
                  {{ t('insight.kb.sectionConversion') }}
                </h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldConversionSummary') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(conversionSummaryDisplay)"
                    >
                      {{ conversionSummaryDisplay }}
                    </span>
                  </div>
                  <div
                    v-if="conversionResultStatus"
                    class="hfl-detail-row"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldConversionResult') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(conversionResultStatus)"
                    >
                      {{ conversionResultStatus }}
                    </span>
                  </div>
                  <div
                    v-if="conversionFailedFiles.length === 1"
                    class="hfl-detail-row"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldConversionFailedFiles') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(conversionFailedFiles[0]?.name, true)"
                      :title="conversionFailedFilesTitle"
                    >
                      {{ conversionFailedFiles[0]?.name }}
                    </span>
                  </div>
                  <div
                    v-else-if="conversionFailedFiles.length > 1"
                    class="hfl-detail-row"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldConversionFailedFiles') }}</span>
                    <span
                      class="hfl-detail-row__value hfl-detail-row__value--stacked hfl-detail-row__value--mono"
                      :title="conversionFailedFilesTitle"
                    >
                      <span
                        v-for="(item, index) in conversionFailedFiles"
                        :key="`failed-${index}`"
                        class="hfl-detail-row__text"
                      >{{ item.name }}</span>
                    </span>
                  </div>
                  <div
                    v-if="conversionWarningLines.length === 1"
                    class="hfl-detail-row"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldConversionWarnings') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(conversionWarningLines[0])"
                      :title="conversionWarningLines[0]"
                    >
                      {{ conversionWarningLines[0] }}
                    </span>
                  </div>
                  <div
                    v-else-if="conversionWarningLines.length > 1"
                    class="hfl-detail-row"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldConversionWarnings') }}</span>
                    <span
                      class="hfl-detail-row__value hfl-detail-row__value--stacked"
                      :title="conversionWarningLines.join('\n')"
                    >
                      <span
                        v-for="(line, index) in conversionWarningLines"
                        :key="`warn-${index}`"
                        class="hfl-detail-row__text"
                      >{{ line }}</span>
                    </span>
                  </div>
                </div>
              </section>

              <section class="hfl-detail-section">
                <h4 class="hfl-detail-section__title">
                  {{ t('insight.kb.sectionTechnical') }}
                </h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldSnapshotId') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(activeRow.backup_source_snapshot_id)"
                    >
                      {{ displayValue(activeRow.backup_source_snapshot_id) }}
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldDirectoryId') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(activeRow.backup_snapshot_directory_id)"
                    >
                      {{ displayValue(activeRow.backup_snapshot_directory_id) }}
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldAssistantUuid') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(activeRow.sl_assistant_uuid, true)"
                    >
                      {{ displayValue(activeRow.sl_assistant_uuid) }}
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldLensnodeUuid') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(activeRow.sl_lensnode_uuid, true)"
                    >
                      {{ displayValue(activeRow.sl_lensnode_uuid) }}
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldStatusDetail') }}</span>
                    <span
                      class="hfl-detail-row__value hfl-detail-row__value--break"
                      :class="detailValueClass(activeRow.status_detail)"
                    >
                      {{ displayValue(activeRow.status_detail) }}
                    </span>
                  </div>
                </div>
              </section>
            </div>
          </ElTabPane>

          <ElTabPane
            :label="t('insight.kb.tabIngest')"
            name="retrieval"
          >
            <div class="ks-retrieval-panels">
              <p class="ks-retrieval-panels__lead">
                {{ t('insight.kb.retrieval.stepDesc') }}
              </p>

              <section class="ks-retrieval-panel">
                <div class="ks-retrieval-panel__header">
                  <h4 class="ks-retrieval-panel__title">
                    {{ t('insight.kb.retrieval.documentContentTitle') }}
                  </h4>
                  <p class="ks-retrieval-panel__desc">
                    {{ t('insight.kb.retrieval.documentContentHint') }}
                  </p>
                </div>
                <KnowledgeSourceRetrievalEnhancement
                  v-model="ingestPolicy"
                  section="document"
                  compact
                />
              </section>

              <section class="ks-retrieval-panel">
                <div class="ks-retrieval-panel__header">
                  <h4 class="ks-retrieval-panel__title">
                    {{ t('insight.kb.retrieval.standaloneImagesTitle') }}
                  </h4>
                  <p class="ks-retrieval-panel__desc">
                    {{ t('insight.kb.retrieval.standaloneImagesHint') }}
                  </p>
                </div>
                <KnowledgeSourceRetrievalEnhancement
                  v-model="ingestPolicy"
                  section="images"
                  compact
                />
              </section>

              <section class="ks-retrieval-panel">
                <div class="ks-retrieval-panel__header">
                  <h4 class="ks-retrieval-panel__title">
                    {{ t('insight.kb.retrieval.globalConversionLimitsTitle') }}
                  </h4>
                  <p class="ks-retrieval-panel__desc">
                    {{ t('insight.kb.retrieval.globalConversionLimitsHint') }}
                  </p>
                </div>
                <KnowledgeSourceRetrievalEnhancement
                  v-model="ingestPolicy"
                  section="limits"
                  compact
                />
              </section>
            </div>
          </ElTabPane>
        </ElTabs>
      </template>
    </div>

    <template
      v-if="activeRow && tab === 'retrieval'"
      #footer
    >
      <HflDetailDrawerFooter
        :saving="busy"
        :save-disabled="!hasIngestChanges"
        @cancel="drawerOpen = false"
        @save="saveIngest"
      />
    </template>
  </ElDrawer>
</template>

<style scoped>
.ks-retrieval-panels {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.ks-retrieval-panels__lead {
  margin: 0;
  color: var(--color-text-secondary, #64748b);
  font-size: 12px;
  line-height: 1.5;
}

.ks-retrieval-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px 16px;
  border: 1px solid var(--color-border, rgba(226, 232, 240, 0.95));
  border-radius: 12px;
  background: var(--color-card-bg, #fff);
}

.ks-retrieval-panel__header {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ks-retrieval-panel__title {
  margin: 0;
  color: var(--color-text-title, #0f172a);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.35;
}

.ks-retrieval-panel__desc {
  margin: 0;
  color: var(--color-text-secondary, #64748b);
  font-size: 12px;
  line-height: 1.45;
}

html[data-theme='dark'] .ks-retrieval-panel {
  border-color: #3b4658;
  background: #151a22;
}

html[data-theme='dark'] .ks-retrieval-panel__title {
  color: #e5e7eb;
}
</style>
