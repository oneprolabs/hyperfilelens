<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElTag } from 'element-plus'
import { File, FileQuestion, FileWarning, Folder, MessageSquare, Settings2, TriangleAlert } from 'lucide-vue-next'
import '../../../components/backupSourceFlowActionDialog.css'
import '../../../styles/detail-page-ui.css'
import { formatBytes } from '../../../lib/kopiaProgress'
import { formatLocalDateTime, parseLocalDateTime } from '../../../lib/dateTime'
import { copilotGatewayKind } from '../../../lib/copilotGatewayTerminology'
import { statusTagAttrs } from '../../../lib/statusTag'
import { copilotReasonLabel, copilotWarningLabel } from '../../../lib/copilotDisplay'
import {
  conversionAllOk,
  conversionEmptyResult,
  conversionPhase,
  conversionProblemItems,
  conversionWarningsForDisplay,
} from '../../../lib/conversionSummary'
import type { LensSessionLink } from '../../../lib/lensApi'

const props = defineProps<{
  session: LensSessionLink
}>()

const emit = defineEmits<{
  (event: 'edit-execution'): void
}>()

const { t } = useI18n()
const reasonLabel = (item: { reason: string; reason_label: string }) => copilotReasonLabel(t, item.reason, item.reason_label)
const warningLabel = (item: { code: string; label: string }) => copilotWarningLabel(t, item.code, item.label)
const detailsOpen = ref(false)
const problemListExpanded = ref(false)
const problemPreviewLimit = 12
watch([detailsOpen, () => props.session.id], () => {
  problemListExpanded.value = false
})
const activeRunStatuses = new Set(['queued', 'running', 'streaming'])
const isRecoveryCleanup = computed(() => (
  props.session.lifecycle_status === 'failed'
  && props.session.cleanup_intent === 'reset_for_retry'
  && ['pending', 'running'].includes(props.session.cleanup_status || '')
))
const isCleanupBlocked = computed(() => (
  props.session.lifecycle_status === 'failed'
  && props.session.cleanup_intent === 'reset_for_retry'
  && props.session.cleanup_status === 'blocked'
))

const statusLabel = computed(() => {
  if (isRecoveryCleanup.value) return t('insight.copilot.sessionRecovering')
  if (isCleanupBlocked.value) return t('insight.copilot.sessionRecoveryAttention')
  if (props.session.lifecycle_status === 'failed') return t('insight.copilot.sessionPreparationFailed')
  if (props.session.lifecycle_status === 'provisioning') return t('insight.copilot.sessionPreparing')
  if (props.session.lifecycle_status === 'deleting') return t('insight.copilot.sessionDeleting')
  if (activeRunStatuses.has(props.session.active_run_status || '')) {
    return t('insight.copilot.sessionAnswering')
  }
  return t('insight.copilot.sessionReady')
})

const statusAttrs = computed(() => {
  if (isRecoveryCleanup.value) return statusTagAttrs('info')
  if (isCleanupBlocked.value) return statusTagAttrs('warning')
  if (props.session.lifecycle_status === 'failed') return statusTagAttrs('danger')
  if (['provisioning', 'deleting'].includes(props.session.lifecycle_status)
    || activeRunStatuses.has(props.session.active_run_status || '')) {
    return statusTagAttrs('info')
  }
  return statusTagAttrs('success')
})

const sourceName = computed(() => (
  props.session.backup_source_name?.trim() || t('insight.copilot.backupSourceFallback')
))
const scopes = computed(() => props.session.source_scopes_json || [])
const selectedScopes = computed(() => scopes.value.flatMap((scope, index) => {
  const path = scope.source_path
  if (!path?.trim()) return []
  const type = scope.path_type === 'dir' ? 'folder' : scope.path_type === 'file' ? 'file' : 'path'
  return [{ key: `${scope.backup_snapshot_directory_id}-${index}`, path, type }]
}))
function contextDateTime(timestamp?: string | null) {
  if (!timestamp?.trim()) return '—'
  const value = parseLocalDateTime(timestamp)
  if (Number.isNaN(value.getTime())) return '—'
  return formatLocalDateTime(timestamp)
}
const createdAt = computed(() => contextDateTime(props.session.created_at))
const snapshotAt = computed(() => contextDateTime(props.session.snapshot_created_at))
const gatewayKind = computed(() => copilotGatewayKind(
  props.session.gateway_scope,
  props.session.gateway_selection_mode,
))
const compactGatewayType = computed(() => gatewayKind.value === 'private'
  ? t('insight.copilot.gatewayTypePrivate')
  : t('insight.copilot.gatewayTypePublic'))

const dataContext = computed(() => props.session.data_context ?? null)
const gatewayName = computed(() => (
  dataContext.value?.gateway_name?.trim() || props.session.gateway_name?.trim() || ''
))
const gatewayLabel = computed(() => [compactGatewayType.value, gatewayName.value].filter(Boolean).join(' '))
const conversion = computed(() => props.session.document_conversion ?? null)
const conversionRows = computed(() => {
  const counts = conversion.value?.counts
  if (!counts) return []
  const ready = counts.success + counts.unchanged
  const incompleteCacheDetails = (conversion.value?.items_truncated || 0) > 0 && counts.skipped > counts.unchanged
  const skipped = incompleteCacheDetails
    ? conversionProblemItems(conversion.value).filter(item => item.outcome === 'skipped' && item.reason !== 'UNSUPPORTED_TYPE').length
    : Math.max(0, counts.skipped - counts.unchanged)
  if (!counts.total && !ready && !counts.failed && !counts.unsupported && !skipped) return []
  return [
    { key: 'ready', label: t('insight.copilot.detailsConversionReady'), count: ready, minimum: incompleteCacheDetails },
    ...(counts.failed > 0 ? [{ key: 'failed', label: t('insight.copilot.detailsConversionFailed'), count: counts.failed }] : []),
    ...(skipped > 0 ? [{ key: 'skipped', label: t('insight.copilot.detailsConversionSkipped'), count: skipped, minimum: incompleteCacheDetails }] : []),
    ...(counts.unsupported > 0 ? [{ key: 'unsupported', label: t('insight.copilot.detailsConversionUnsupported'), count: counts.unsupported }] : []),
  ]
})
const allProblemItems = computed(() => conversionProblemItems(conversion.value))
const problemItems = computed(() => problemListExpanded.value
  ? allProblemItems.value : allProblemItems.value.slice(0, problemPreviewLimit))
const remainingProblemCount = computed(() => Math.max(0, allProblemItems.value.length - problemPreviewLimit))
const partialProblemDetails = computed(() => (
  (conversion.value?.items_truncated || 0) > 0
  || (conversion.value?.counts.failed || 0) + (conversion.value?.counts.unsupported || 0) > allProblemItems.value.length
))
const conversionOk = computed(() => conversionAllOk(conversion.value))
const conversionEmpty = computed(() => conversionEmptyResult(conversion.value))
const conversionFailed = computed(() => conversionPhase(conversion.value) === 'failed')
const conversionRunning = computed(() => conversionPhase(conversion.value) === 'running')
const conversionWarnings = computed(() => conversionWarningsForDisplay(conversion.value, 8).map((warning) => ({
  ...warning,
  label: warningLabel(warning),
})))

</script>

<template>
  <header class="copilot-context-bar">
    <div class="copilot-context-bar__top">
      <div class="copilot-context-bar__identity">
        <MessageSquare
          :size="16"
          class="copilot-context-bar__icon"
          aria-hidden="true"
        />
        <h1 :title="session.title">
          <button
            type="button"
            class="copilot-context-bar__title"
            :title="`${session.title}: ${t('insight.copilot.chatDetailsTitle')}`"
            :aria-label="`${session.title}: ${t('insight.copilot.chatDetailsTitle')}`"
            aria-haspopup="dialog"
            :aria-expanded="detailsOpen"
            @click="detailsOpen = true"
          >
            {{ session.title }}
          </button>
        </h1>
        <ElTag
          v-bind="statusAttrs"
          class="copilot-context-bar__status"
          size="small"
          effect="light"
        >
          {{ statusLabel }}
        </ElTag>
      </div>
      <button
        v-if="session.lifecycle_status === 'ready'"
        type="button"
        class="copilot-context-bar__settings"
        :title="t('insight.copilot.executionSettingsTitle')"
        @click="emit('edit-execution')"
      >
        <Settings2
          :size="15"
          aria-hidden="true"
        />
        <span>{{ t('insight.copilot.executionSettingsShort') }}</span>
      </button>
    </div>

    <div class="copilot-context-bar__summary">
      <span
        class="copilot-context-bar__field copilot-context-bar__source"
      >
        <span class="copilot-context-bar__label">{{ t('insight.kb.fieldBackupSource') }}:</span>
        <span class="copilot-context-bar__value">{{ sourceName }}</span>
      </span>
      <span
        class="copilot-context-bar__field copilot-context-bar__snapshot"
      >
        <span class="copilot-context-bar__label">{{ t('insight.copilot.contextSnapshotLabel') }}:</span>
        <time
          v-if="snapshotAt !== '—'"
          :datetime="session.snapshot_created_at || undefined"
        >{{ snapshotAt }}</time>
        <span
          v-else
          class="hfl-empty-mark"
        >—</span>
      </span>
      <span class="copilot-context-bar__field copilot-context-bar__gateway">
        <span class="copilot-context-bar__label">{{ t('insight.copilot.contextGatewayLabel') }}:</span>
        <span class="copilot-context-bar__value">{{ gatewayLabel }}</span>
      </span>
      <span class="copilot-context-bar__field copilot-context-bar__created">
        {{ t('insight.copilot.contextCreatedAt', { time: createdAt }) }}
      </span>
    </div>
    <div
      v-if="session.lifecycle_status === 'ready' && !session.multimodal_model_ref"
      class="copilot-context-bar__visual-warning"
      role="status"
      aria-live="polite"
    >
      <TriangleAlert
        :size="13"
        aria-hidden="true"
      />
      <span>{{ t('insight.copilot.visualUnderstandingUnavailable') }}</span>
    </div>
  </header>

  <ElDialog
    v-model="detailsOpen"
    :title="t('insight.copilot.chatDetailsTitle')"
    class="hfl-flow-action-dialog hfl-flow-action-dialog--form copilot-details-dialog"
    width="min(680px, calc(100vw - 32px))"
    align-center
    append-to-body
  >
    <div class="hfl-detail-sections copilot-details">
      <section class="hfl-detail-section hfl-detail-card">
        <h3 class="hfl-detail-card__title">
          {{ t('insight.copilot.detailsDataSource') }}
        </h3>
        <div class="copilot-details__section-body">
          <dl><dt>{{ t('insight.kb.fieldBackupSource') }}</dt><dd>{{ sourceName }}</dd></dl>
          <dl>
            <dt>{{ t('insight.copilot.contextSnapshotLabel') }}</dt><dd :class="{ 'hfl-empty-mark': snapshotAt === '—' }">
              {{ snapshotAt }}
            </dd>
          </dl>
          <dl>
            <dt>{{ t('insight.copilot.snapshotSizeLabel') }}</dt><dd :class="{ 'hfl-empty-mark': session.snapshot_size_bytes == null }">
              {{ session.snapshot_size_bytes != null ? formatBytes(session.snapshot_size_bytes) : '—' }}
            </dd>
          </dl>
        </div>
      </section>
      <section class="hfl-detail-section hfl-detail-card">
        <h3 class="hfl-detail-card__title">
          {{ t('insight.copilot.detailsFilesFolders') }}
        </h3>
        <div class="copilot-details__section-body">
          <ul
            v-if="selectedScopes.length"
            class="copilot-details__scopes"
          >
            <li
              v-for="scope in selectedScopes"
              :key="scope.key"
              class="copilot-details__scope"
            >
              <span
                class="copilot-details__scope-icon"
                role="img"
                :aria-label="t(`insight.copilot.detailsScope${scope.type === 'folder' ? 'Folder' : scope.type === 'file' ? 'File' : 'Path'}`)"
              >
                <component
                  :is="scope.type === 'folder' ? Folder : scope.type === 'file' ? File : FileQuestion"
                  :size="16"
                  aria-hidden="true"
                />
              </span>
              <span class="copilot-details__scope-path">{{ scope.path }}</span>
            </li>
          </ul>
          <p
            v-else
            class="copilot-details__note"
          >
            {{ t('insight.copilot.detailsScopesNotRecorded') }}
          </p>
        </div>
      </section>
      <section class="hfl-detail-section hfl-detail-card">
        <h3 class="hfl-detail-card__title">
          {{ t('insight.copilot.detailsProcessingLocation') }}
        </h3>
        <div class="copilot-details__section-body">
          <dl><dt>{{ t('insight.copilot.gatewayTypeLabel') }}</dt><dd>{{ compactGatewayType }}</dd></dl>
          <dl>
            <dt>{{ t('insight.copilot.detailsGatewayName') }}</dt><dd :class="{ 'hfl-empty-mark': !gatewayName }">
              {{ gatewayName || '—' }}
            </dd>
          </dl>
        </div>
      </section>
      <section
        v-if="conversion"
        class="hfl-detail-section hfl-detail-card"
      >
        <h3 class="hfl-detail-card__title">
          {{ t('insight.copilot.documentConversionTitle') }}
        </h3>
        <div class="copilot-details__section-body">
          <dl
            v-for="row in conversionRows"
            :key="row.key"
            class="copilot-details__conversion-count"
            :class="`is-${row.key}`"
          >
            <dt>{{ row.label }}</dt><dd>{{ t(row.minimum ? 'insight.copilot.detailsConversionCountMinimum' : 'insight.copilot.detailsConversionCount', { count: row.count }, row.count) }}</dd>
          </dl>
          <p
            v-if="conversion?.error"
            class="copilot-details__note"
          >
            {{ conversion.error }}
          </p>
          <ul
            v-if="problemItems.length"
            :id="`copilot-detail-problems-${session.id}`"
            class="copilot-details__problems"
          >
            <li
              v-for="(item, index) in problemItems"
              :key="`${item.name}-${index}`"
              class="copilot-details__problem-item"
            >
              <FileWarning
                :size="16"
                aria-hidden="true"
              />
              <div class="copilot-details__problem-copy">
                <span class="copilot-details__problem-file">{{ item.name }}</span>
                <span class="copilot-details__problem-reason">{{ reasonLabel(item) }}</span>
              </div>
            </li>
          </ul>
          <button
            v-if="remainingProblemCount"
            type="button"
            class="copilot-details__show-more"
            :aria-expanded="problemListExpanded"
            :aria-controls="`copilot-detail-problems-${session.id}`"
            @click="problemListExpanded = !problemListExpanded"
          >
            {{ problemListExpanded
              ? t('insight.copilot.detailsShowFewerProblemFiles')
              : t('insight.copilot.detailsShowMoreProblemFiles', { count: remainingProblemCount }, remainingProblemCount) }}
          </button>
          <p
            v-if="partialProblemDetails"
            class="copilot-details__note copilot-details__partial-problems"
            role="status"
          >
            {{ t('insight.copilot.detailsPartialProblemFiles') }}
          </p>
          <ul
            v-if="conversionWarnings.length"
            class="copilot-details__warnings"
          >
            <li
              v-for="(warning, index) in conversionWarnings"
              :key="`${warning.code}-${index}`"
            >
              <TriangleAlert
                :size="15"
                aria-hidden="true"
              />
              <span>{{ warningLabel(warning) }}</span>
            </li>
          </ul>
          <p
            v-if="!problemItems.length && conversionOk"
            class="copilot-details__note"
          >
            {{ t('insight.copilot.documentConversionOk') }}
          </p>
          <p
            v-else-if="!problemItems.length && conversionEmpty"
            class="copilot-details__note"
          >
            {{ t('insight.copilot.documentConversionEmpty') }}
          </p>
          <p
            v-else-if="conversionRunning"
            class="copilot-details__note"
          >
            {{ t('insight.copilot.documentConversionRunning') }}
          </p>
          <p
            v-else-if="!problemItems.length && (conversionFailed || (conversionRows.length && !conversionOk))"
            class="copilot-details__note"
          >
            {{ t('insight.copilot.documentConversionPartial') }}
          </p>
        </div>
      </section>
    </div>
  </ElDialog>
</template>

<style scoped>
.copilot-context-bar { display: flex; min-width: 0; flex-direction: column; gap: 5px; padding: 9px 18px 10px; border-bottom: 1px solid var(--color-border-light); background: var(--color-card-bg); }
.copilot-context-bar__top { display: flex; min-width: 0; align-items: center; justify-content: space-between; gap: 12px; }
.copilot-context-bar__identity { display: flex; min-width: 0; align-items: center; gap: 8px; }
.copilot-context-bar__settings { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 5px; padding: 4px 8px; border: 1px solid var(--color-border-light); border-radius: 6px; background: var(--color-card-bg); color: var(--color-text-tertiary); font-size: 11px; cursor: pointer; }
.copilot-context-bar__settings:hover { border-color: var(--color-primary); color: var(--color-primary); }
.copilot-context-bar__icon { flex-shrink: 0; color: var(--color-primary); }
.copilot-context-bar__identity h1 { min-width: 0; overflow: hidden; margin: 0; color: var(--color-text-title); font-size: 14px; font-weight: 650; line-height: 20px; text-overflow: ellipsis; white-space: nowrap; }
.copilot-context-bar__title { display: block; min-width: 0; max-width: 100%; overflow: hidden; padding: 0; border: 0; background: transparent; color: inherit; font: inherit; text-align: left; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.copilot-context-bar__title:hover { color: var(--color-primary); }
.copilot-context-bar__title:focus-visible { outline: 2px solid var(--color-primary); outline-offset: -2px; border-radius: 3px; }
.copilot-context-bar__status { flex-shrink: 0; }
.copilot-context-bar__summary { display: flex; min-width: 0; flex-wrap: wrap; align-items: center; gap: 6px 12px; color: var(--color-text-secondary); font-size: 12px; line-height: 20px; }
.copilot-context-bar__field { position: relative; display: inline-flex; min-width: 0; max-width: 100%; align-items: center; gap: 5px; white-space: nowrap; }
.copilot-context-bar__field + .copilot-context-bar__field { padding-left: 12px; }
.copilot-context-bar__field + .copilot-context-bar__field::before { position: absolute; top: 50%; left: 0; width: 1px; height: 12px; background: var(--color-border); content: ''; transform: translateY(-50%); }
.copilot-context-bar__source { padding-left: 0; }
.copilot-context-bar__label { flex-shrink: 0; font-weight: 500; }
.copilot-context-bar__value { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.copilot-context-bar__source,.copilot-context-bar__gateway { max-width: min(320px, 100%); }
.copilot-context-bar__created { color: var(--color-text-tertiary); }
.copilot-context-bar__visual-warning { display: flex; align-items: center; gap: 6px; color: #b54708; font-size: 11px; line-height: 16px; }
.copilot-context-bar__visual-warning svg { flex: 0 0 auto; }
.copilot-details { min-width: 0; gap: 16px; padding-bottom: 0; }
.copilot-details section { min-width: 0; }
.copilot-details .hfl-detail-section { border-color: var(--color-border); }
.copilot-details .hfl-detail-card__title { padding: 14px 16px 0; color: var(--color-text-title); font-size: 13px; font-weight: 600; }
.copilot-details__section-body { min-width: 0; padding: 10px 16px 14px; }
.copilot-details dl { display: grid; grid-template-columns: minmax(144px, 38%) minmax(0, 1fr); align-items: start; gap: 24px; margin: 0; padding: 5px 0; line-height: var(--hfl-flow-action-line-height); }
.copilot-details dt { color: var(--color-text-secondary); font-size: 13px; }
.copilot-details dd { min-width: 0; overflow-wrap: anywhere; margin: 0; color: var(--color-text-title); font-size: var(--hfl-flow-action-font-size); font-weight: 400; font-variant-numeric: tabular-nums; }
.copilot-details__scopes { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.copilot-details__scope { display: flex; min-width: 0; align-items: flex-start; gap: 8px; padding: 10px 12px; border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-grey-2); }
.copilot-details__scope-icon { display: flex; flex: 0 0 16px; margin-top: 3px; color: var(--color-primary); }
.copilot-details__scope-path { min-width: 0; overflow-wrap: anywhere; color: var(--color-text-primary); font-family: var(--font-mono); font-size: 13px; line-height: var(--hfl-flow-action-line-height); }
.copilot-details__note { overflow-wrap: anywhere; margin: 8px 0 0; color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
.copilot-details__conversion-count.is-ready dt { color: var(--color-success-text); }
.copilot-details__conversion-count.is-failed dt { color: var(--color-error-text); }
.copilot-details__conversion-count.is-unsupported dt,.copilot-details__conversion-count.is-skipped dt { color: var(--color-warning-text); }
.copilot-details__problems { margin: 10px 0 0; padding: 0; list-style: none; }
.copilot-details__problem-item { display: flex; min-width: 0; align-items: flex-start; gap: 8px; padding: 12px 0; border-top: 1px solid var(--color-border-light); }
.copilot-details__problem-item:last-child { padding-bottom: 0; }
.copilot-details__problem-item > svg { flex: 0 0 16px; margin-top: 3px; color: var(--color-warning-text); }
.copilot-details__problem-copy { display: grid; min-width: 0; gap: 2px; }
.copilot-details__problem-file { overflow-wrap: anywhere; color: var(--color-text-primary); font-size: 13px; font-weight: 400; line-height: var(--hfl-flow-action-line-height); }
.copilot-details__problem-reason { overflow-wrap: anywhere; color: var(--color-text-secondary); font-size: 12px; line-height: 20px; }
.copilot-details__show-more { margin-top: 10px; padding: 4px 0; border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: 13px; cursor: pointer; }
.copilot-details__show-more:hover { color: var(--color-primary-hover); }
.copilot-details__show-more:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 3px; border-radius: 3px; }
.copilot-details__warnings { display: grid; gap: 8px; margin: 10px 0 0; padding: 0; list-style: none; }
.copilot-details__warnings li { display: flex; align-items: flex-start; gap: 7px; color: var(--color-warning-text); font-size: 12px; line-height: 20px; }
.copilot-details__warnings svg { flex: 0 0 15px; margin-top: 2px; }
.copilot-details__warnings span { min-width: 0; overflow-wrap: anywhere; }
:global(.copilot-details-dialog.el-dialog) { max-width: calc(100vw - 32px); max-height: calc(var(--app-viewport-height, 100vh) - var(--app-safe-top, 0px) - var(--app-safe-bottom, 0px) - 32px); }
:global(.copilot-details-dialog.hfl-flow-action-dialog--form.el-dialog .el-dialog__header) { padding: 20px 24px 16px; border-bottom: 1px solid var(--el-border-color-extra-light); }
:global(.copilot-details-dialog.hfl-flow-action-dialog--form.el-dialog .el-dialog__body) { min-height: 0; flex: 1 1 auto; overflow-x: hidden; overflow-y: auto; padding: 16px 24px 18px; }
@media (max-width: 767.98px) {
  :global(.copilot-details-dialog.hfl-flow-action-dialog--form.el-dialog .el-dialog__header) { padding: 16px 16px 12px; }
  :global(.copilot-details-dialog.hfl-flow-action-dialog--form.el-dialog .el-dialog__body) { padding: 14px 16px 16px; }
}
@media (max-width: 479px) {
  .copilot-details dl { grid-template-columns: 80px minmax(0, 1fr); gap: 16px; }
  .copilot-details .hfl-detail-card__title { padding-right: 12px; padding-left: 12px; }
  .copilot-details__section-body { padding-right: 12px; padding-left: 12px; }
}
</style>
