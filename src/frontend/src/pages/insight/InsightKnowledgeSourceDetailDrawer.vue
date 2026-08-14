<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { formatLocalDateTime } from '../../lib/dateTime'
import { formatBytes } from '../../lib/kopiaProgress'
import { apiErrorMessage } from '../../lib/api'
import { DETAIL_EMPTY } from '../../lib/nodeInventoryDisplay'
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
  conversionCountsLabel,
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
const isGatewayLocal = computed(() => !isBackupSource.value)

const sourceTypeLabel = computed(() =>
  isBackupSource.value
    ? t('insight.kb.sourceTypeBackup')
    : t('insight.kb.sourceTypeGatewayLocal'),
)

const scopePaths = computed(() => {
  const row = activeRow.value
  if (!row) return []
  const scopes = row.source_scopes_json?.map((item) => item.source_path?.trim()).filter(Boolean) ?? []
  if (scopes.length > 0) return scopes
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
const conversionSummaryLabel = computed(() => conversionCountsLabel(documentConversion.value))
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
            <div class="hfl-detail-sections ks-kb-detail-sections">
              <section class="hfl-detail-section ks-kb-detail-card">
                <h4 class="ks-kb-detail-card__title">
                  <span class="ks-kb-detail-card__indicator" />
                  {{ t('insight.kb.sectionBasicInfo') }}
                </h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldName') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(activeRow.name)"
                    >
                      {{ displayValue(activeRow.name) }}
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldSourceType') }}</span>
                    <span class="hfl-detail-row__value">{{ sourceTypeLabel }}</span>
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
                    <span class="hfl-detail-row__label">{{ t('insight.kb.colLearnStatus') }}</span>
                    <span class="hfl-detail-row__value">
                      <ElTag
                        v-bind="lifecycleStatusTagAttrs(activeRow.status)"
                        size="small"
                      >{{ statusLabel }}</ElTag>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldIngestSummary') }}</span>
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

              <section class="hfl-detail-section ks-kb-detail-card">
                <h4 class="ks-kb-detail-card__title">
                  <span class="ks-kb-detail-card__indicator" />
                  {{ t('insight.kb.retrieval.conversionResultsTitle') }}
                </h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.copilot.documentConversionSummary') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(conversionSummaryDisplay)"
                    >
                      {{ conversionSummaryDisplay }}
                    </span>
                  </div>
                  <div
                    v-if="conversionOk"
                    class="hfl-detail-row"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.copilot.documentConversionTitle') }}</span>
                    <span class="hfl-detail-row__value">{{ t('insight.kb.retrieval.conversionResultsOk') }}</span>
                  </div>
                  <div
                    v-else-if="conversionEmpty"
                    class="hfl-detail-row"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.copilot.documentConversionTitle') }}</span>
                    <span class="hfl-detail-row__value">{{ t('insight.copilot.documentConversionEmpty') }}</span>
                  </div>
                  <div
                    v-else-if="documentConversion && conversionFailed"
                    class="hfl-detail-row"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.copilot.documentConversionTitle') }}</span>
                    <span class="hfl-detail-row__value">{{ documentConversion.error || t('insight.copilot.documentConversionPartial') }}</span>
                  </div>
                  <div
                    v-else-if="documentConversion && !conversionRunning && conversionSummaryLabel && !conversionOk && conversionProblems.length === 0"
                    class="hfl-detail-row"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.copilot.documentConversionTitle') }}</span>
                    <span class="hfl-detail-row__value">{{ t('insight.copilot.documentConversionPartial') }}</span>
                  </div>
                  <div
                    v-if="conversionProblems.length"
                    class="hfl-detail-row ks-kb-detail-row--stack"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.copilot.documentConversionTitle') }}</span>
                    <ul class="ks-kb-conversion-list">
                      <li
                        v-for="(item, index) in conversionProblems"
                        :key="`${item.name}-${index}`"
                      >
                        <strong>{{ item.name }}</strong>
                        <span>{{ item.reason_label }}</span>
                      </li>
                    </ul>
                  </div>
                  <div
                    v-if="conversionWarnings.length"
                    class="hfl-detail-row ks-kb-detail-row--stack"
                  >
                    <span class="hfl-detail-row__label">{{ t('insight.copilot.documentConversionWarnings') }}</span>
                    <ul class="ks-kb-conversion-list">
                      <li
                        v-for="(warning, index) in conversionWarnings"
                        :key="`${warning.code}-${index}`"
                      >
                        <span>{{ warning.label || warning.code }}</span>
                      </li>
                    </ul>
                  </div>
                </div>
              </section>

              <section
                v-if="isBackupSource"
                class="hfl-detail-section ks-kb-detail-card"
              >
                <h4 class="ks-kb-detail-card__title">
                  <span class="ks-kb-detail-card__indicator" />
                  {{ t('insight.kb.sourceTypeBackup') }}
                </h4>
                <div class="hfl-detail-grid">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldBackupSource') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(backupSourceLabel)"
                    >
                      {{ backupSourceLabel }}
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldSnapshot') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(snapshotLabel)"
                    >
                      {{ snapshotLabel }}
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.colLinkedVersion') }}</span>
                    <span class="hfl-detail-row__value">{{ linkedVersionLabel }}</span>
                  </div>
                  <div class="hfl-detail-row ks-kb-detail-row--stack">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldRestoreScope') }}</span>
                    <div class="ks-kb-detail-scope-list">
                      <div
                        v-for="(path, index) in scopePaths"
                        :key="`${index}-${path}`"
                        class="ks-kb-detail-scope-list__item"
                      >
                        <span class="ks-kb-detail-scope-list__index">
                          {{ String(index + 1).padStart(2, '0') }}
                        </span>
                        <span class="ks-kb-detail-scope-list__path">{{ path }}</span>
                      </div>
                      <span
                        v-if="scopePaths.length === 0"
                        class="hfl-detail-row__empty"
                      >{{ DETAIL_EMPTY }}</span>
                    </div>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.colGateway') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(activeRow.gateway_name)"
                    >
                      {{ displayValue(activeRow.gateway_name) }}
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.fieldWorkspaceRestore') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(activeRow.workspace_path_on_lensnode, true)"
                    >
                      {{ displayValue(activeRow.workspace_path_on_lensnode) }}
                    </span>
                  </div>
                </div>
              </section>

              <section
                v-if="isGatewayLocal"
                class="hfl-detail-section ks-kb-detail-card"
              >
                <h4 class="ks-kb-detail-card__title">
                  <span class="ks-kb-detail-card__indicator" />
                  {{ t('insight.kb.sourceTypeGatewayLocal') }}
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
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('insight.kb.wizardStepPath') }}</span>
                    <span
                      class="hfl-detail-row__value"
                      :class="detailValueClass(activeRow.source_path, true)"
                    >
                      {{ displayValue(activeRow.source_path) }}
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

              <section class="hfl-detail-section ks-kb-detail-card">
                <h4 class="ks-kb-detail-card__title">
                  <span class="ks-kb-detail-card__indicator" />
                  {{ t('insight.kb.tabDiagnostics') }}
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
            <div class="hfl-detail-sections ks-kb-detail-sections">
              <section class="hfl-detail-section ks-kb-detail-card">
                <h4 class="ks-kb-detail-card__title">
                  <span class="ks-kb-detail-card__indicator" />
                  {{ t('insight.kb.retrieval.documentContentTitle') }}
                </h4>
                <KnowledgeSourceRetrievalEnhancement
                  v-model="ingestPolicy"
                  section="document"
                  :show-lead="true"
                />
              </section>

              <section class="hfl-detail-section ks-kb-detail-card">
                <h4 class="ks-kb-detail-card__title">
                  <span class="ks-kb-detail-card__indicator" />
                  {{ t('insight.kb.retrieval.standaloneImagesTitle') }}
                </h4>
                <KnowledgeSourceRetrievalEnhancement
                  v-model="ingestPolicy"
                  section="images"
                />
              </section>

              <section class="hfl-detail-section ks-kb-detail-card">
                <h4 class="ks-kb-detail-card__title">
                  <span class="ks-kb-detail-card__indicator" />
                  {{ t('insight.kb.retrieval.globalConversionLimitsTitle') }}
                </h4>
                <KnowledgeSourceRetrievalEnhancement
                  v-model="ingestPolicy"
                  section="limits"
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
.ks-kb-detail-sections {
  gap: 16px;
}

.ks-kb-detail-card {
  padding: 0;
}

.ks-kb-detail-card__title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  padding: 14px 16px 0;
  color: rgb(15 23 42);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
}

.ks-kb-detail-card__indicator {
  width: 3px;
  height: 16px;
  flex: 0 0 auto;
  border-radius: 999px;
  background: linear-gradient(
    180deg,
    var(--color-primary) 0%,
    color-mix(in srgb, var(--color-primary) 82%, #000) 100%
  );
}

.ks-kb-detail-card :deep(.hfl-detail-grid) {
  padding: 12px 16px 16px;
}

.ks-kb-detail-card :deep(.ks-retrieval-stack) {
  padding: 0 16px 16px;
}

.ks-kb-detail-row--stack {
  align-items: flex-start;
}

.ks-kb-detail-scope-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
  min-width: 0;
}

.ks-kb-conversion-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.ks-kb-conversion-list li {
  display: grid;
  gap: 2px;
}

.ks-kb-conversion-list strong {
  overflow-wrap: anywhere;
  color: rgb(15 23 42);
  font-size: 12px;
}

.ks-kb-conversion-list span {
  color: rgb(100 116 139);
  font-size: 11px;
  line-height: 1.4;
}

.ks-kb-detail-scope-list__item {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  padding: 8px 10px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 8px;
  background: rgb(248 250 252);
}

.ks-kb-detail-scope-list__index {
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 700;
  line-height: 1.5;
}

.ks-kb-detail-scope-list__path {
  color: rgb(30 41 59);
  font-size: 13px;
  line-height: 1.45;
  word-break: break-all;
}
</style>
