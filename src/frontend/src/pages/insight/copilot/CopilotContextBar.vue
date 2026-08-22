<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { MessageSquare, Settings2, TriangleAlert } from 'lucide-vue-next'
import { formatBytes } from '../../../lib/kopiaProgress'
import { formatLocalDateTime } from '../../../lib/dateTime'
import { copilotGatewayKind } from '../../../lib/copilotGatewayTerminology'
import {
  conversionAllOk,
  conversionCountsLabel,
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
const router = useRouter()
const detailsOpen = ref(false)
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
  if (isRecoveryCleanup.value) return 'Recovering…'
  if (isCleanupBlocked.value) return 'Recovery Blocked'
  if (props.session.lifecycle_status === 'failed') return 'Failed'
  if (props.session.lifecycle_status === 'provisioning') return 'Preparing…'
  if (props.session.lifecycle_status === 'deleting') return 'Deleting…'
  if (activeRunStatuses.has(props.session.active_run_status || '')) return 'Answering…'
  return 'Ready'
})

const statusClass = computed(() => ({
  'is-failed': props.session.lifecycle_status === 'failed' && !isRecoveryCleanup.value,
  'is-preparing': props.session.lifecycle_status === 'provisioning' || isRecoveryCleanup.value,
  'is-deleting': props.session.lifecycle_status === 'deleting',
  'is-answering': activeRunStatuses.has(props.session.active_run_status || ''),
}))

const sourceName = computed(() => props.session.backup_source_name?.trim() || 'Backup Source')
const scopes = computed(() => props.session.source_scopes_json || [])
const firstPath = computed(() => scopes.value[0]?.source_path?.trim() || 'No files selected')
const additionalPathCount = computed(() => Math.max(0, scopes.value.length - 1))
const createdShort = computed(() => {
  if (!props.session.created_at) return 'Unavailable'
  const value = new Date(props.session.created_at)
  if (Number.isNaN(value.getTime())) return 'Unavailable'
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(value)
})
const gatewayKind = computed(() => copilotGatewayKind(
  props.session.gateway_scope,
  props.session.gateway_selection_mode,
))
const gatewayType = computed(() => gatewayKind.value === 'private'
  ? t('insight.copilot.gatewayPrivateTitle')
  : t('insight.copilot.gatewayPublicTitle'))
const compactGatewayType = computed(() => gatewayKind.value === 'private'
  ? t('insight.copilot.gatewayTypePrivate')
  : t('insight.copilot.gatewayTypePublic'))

const dataContext = computed(() => props.session.data_context ?? null)
const originLabel = computed(() => dataContext.value?.origin_label || t('insight.copilot.dataOriginProtected'))
const processingLabel = computed(() => {
  const location = dataContext.value?.processing_location_label || gatewayType.value
  const name = dataContext.value?.gateway_name || props.session.gateway_name
  return name ? `${location} · ${name}` : location
})
const conversion = computed(() => props.session.document_conversion ?? null)
const conversionLabel = computed(() => conversionCountsLabel(conversion.value))
const problemItems = computed(() => conversionProblemItems(conversion.value).slice(0, 12))
const conversionOk = computed(() => conversionAllOk(conversion.value))
const conversionEmpty = computed(() => conversionEmptyResult(conversion.value))
const conversionFailed = computed(() => conversionPhase(conversion.value) === 'failed')
const conversionRunning = computed(() => conversionPhase(conversion.value) === 'running')
const conversionWarnings = computed(() => conversionWarningsForDisplay(conversion.value, 8))

function openRestore() {
  const path = dataContext.value?.restore_path
  if (!path) return
  detailsOpen.value = false
  void router.push(path)
}

function openBackupDetail() {
  const path = dataContext.value?.backup_detail_path
  if (!path) return
  detailsOpen.value = false
  void router.push(path)
}
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
          {{ session.title }}
        </h1>
        <span
          class="copilot-context-bar__status"
          :class="statusClass"
        ><i />{{ statusLabel }}</span>
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

    <div
      class="copilot-context-bar__summary"
      :title="`${originLabel} · ${sourceName} · ${firstPath} · ${processingLabel} · Created ${createdShort}`"
    >
      <span class="copilot-context-bar__origin">{{ originLabel }}</span><em>·</em>
      <span class="copilot-context-bar__source">{{ sourceName }}</span><em>·</em>
      <button
        type="button"
        class="copilot-context-bar__path"
        @click="detailsOpen = true"
      >
        {{ firstPath }}<b v-if="additionalPathCount"> +{{ additionalPathCount }}</b>
      </button><em>·</em>
      <span class="copilot-context-bar__gateway">{{ processingLabel }}</span><em>·</em>
      <span class="copilot-context-bar__created">Created {{ createdShort }}</span>
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
      <span>Visual understanding is unavailable. Images and scanned PDFs may not be searchable.</span>
    </div>
  </header>

  <ElDialog
    v-model="detailsOpen"
    :title="t('insight.copilot.chatDetailsTitle')"
    width="560px"
    append-to-body
  >
    <div class="copilot-details">
      <section>
        <h3>{{ t('insight.copilot.detailsDataSource') }}</h3>
        <dl><dt>{{ t('insight.copilot.dataOriginLabel') }}</dt><dd>{{ originLabel }}</dd></dl>
        <dl><dt>{{ t('insight.kb.fieldBackupSource') }}</dt><dd>{{ sourceName }}</dd></dl>
        <dl>
          <dt>{{ t('insight.kb.fieldSnapshot') }}</dt><dd :class="{ 'hfl-empty-mark': !session.snapshot_created_at }">
            {{ session.snapshot_created_at ? formatLocalDateTime(session.snapshot_created_at) : '—' }}
          </dd>
        </dl>
        <dl>
          <dt>{{ t('insight.copilot.snapshotSizeLabel') }}</dt><dd :class="{ 'hfl-empty-mark': session.snapshot_size_bytes == null }">
            {{ session.snapshot_size_bytes != null ? formatBytes(session.snapshot_size_bytes) : '—' }}
          </dd>
        </dl>
        <div
          v-if="dataContext?.restore_path || dataContext?.backup_detail_path"
          class="copilot-details__actions"
        >
          <ElButton
            v-if="dataContext?.restore_path"
            size="small"
            @click="openRestore"
          >
            {{ t('insight.copilot.openSnapshotRestore') }}
          </ElButton>
          <ElButton
            v-if="dataContext?.backup_detail_path"
            size="small"
            @click="openBackupDetail"
          >
            {{ t('insight.copilot.openBackupDetail') }}
          </ElButton>
        </div>
      </section>
      <section>
        <h3>{{ t('insight.copilot.detailsFilesFolders') }}</h3>
        <ol>
          <li
            v-for="(scope, index) in scopes"
            :key="`${scope.backup_snapshot_directory_id}-${index}`"
          >
            {{ scope.source_path }}
          </li>
        </ol>
      </section>
      <section>
        <h3>{{ t('insight.copilot.detailsProcessingLocation') }}</h3>
        <dl><dt>{{ t('insight.copilot.gatewayTypeLabel') }}</dt><dd>{{ compactGatewayType }}</dd></dl>
        <dl>
          <dt>{{ t('insight.copilot.gatewayNameLabel') }}</dt><dd :class="{ 'hfl-empty-mark': !session.gateway_name }">
            {{ session.gateway_name || '—' }}
          </dd>
        </dl>
        <p class="copilot-details__note">
          {{ t('insight.copilot.processingLocationNote') }}
        </p>
      </section>
      <section v-if="conversion">
        <h3>{{ t('insight.copilot.documentConversionTitle') }}</h3>
        <dl v-if="conversionLabel">
          <dt>{{ t('insight.copilot.documentConversionSummary') }}</dt><dd>{{ conversionLabel }}</dd>
        </dl>
        <p
          v-if="conversion?.error"
          class="copilot-details__note"
        >
          {{ conversion.error }}
        </p>
        <ul
          v-if="problemItems.length"
          class="copilot-details__problems"
        >
          <li
            v-for="(item, index) in problemItems"
            :key="`${item.name}-${index}`"
          >
            <strong>{{ item.name }}</strong>
            <span>{{ item.reason_label }}</span>
          </li>
        </ul>
        <ul
          v-if="conversionWarnings.length"
          class="copilot-details__problems"
        >
          <li
            v-for="(warning, index) in conversionWarnings"
            :key="`${warning.code}-${index}`"
          >
            <span>{{ warning.label || warning.code }}</span>
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
          v-else-if="!problemItems.length && conversionRunning"
          class="copilot-details__note"
        >
          {{ t('insight.copilot.documentConversionRunning') }}
        </p>
        <p
          v-else-if="!problemItems.length && (conversionFailed || (conversionLabel && !conversionOk))"
          class="copilot-details__note"
        >
          {{ t('insight.copilot.documentConversionPartial') }}
        </p>
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
.copilot-context-bar__status { display: inline-flex; min-height: 20px; flex-shrink: 0; align-items: center; gap: 5px; padding: 1px 7px; border: 1px solid #abefc6; border-radius: 999px; background: #ecfdf3; color: #027a48; font-size: 11px; font-weight: 600; line-height: 16px; }
.copilot-context-bar__status i { width: 6px; height: 6px; flex: 0 0 6px; border-radius: 999px; background: currentColor; }
.copilot-context-bar__status.is-preparing { border-color: #d9d2ff; background: #f2f0ff; color: #654cf0; }
.copilot-context-bar__status.is-deleting { border-color: var(--color-border-light); background: var(--color-grey-3); color: var(--color-text-tertiary); }
.copilot-context-bar__status.is-answering { border-color: #b2ccff; background: #eef4ff; color: #165dff; }
.copilot-context-bar__status.is-answering i,.copilot-context-bar__status.is-preparing i { animation: copilot-status-pulse 1.4s ease-in-out infinite; }
.copilot-context-bar__status.is-failed { border-color: #fecdca; background: #fef3f2; color: #d92d20; }
.copilot-context-bar__summary { display: flex; min-width: 0; align-items: center; gap: 6px; overflow: hidden; color: var(--color-text-tertiary); font-size: 12px; line-height: 18px; white-space: nowrap; }
.copilot-context-bar__summary > span { overflow: hidden; text-overflow: ellipsis; }
.copilot-context-bar__summary em { flex-shrink: 0; color: var(--color-text-disabled); font-style: normal; }
.copilot-context-bar__origin,.copilot-context-bar__source { max-width: 16%; flex: 0 1 auto; color: var(--color-text-secondary); }
.copilot-context-bar__path { min-width: 60px; max-width: 36%; flex: 0 1 auto; overflow: hidden; padding: 0; border: 0; background: transparent; color: var(--color-text-secondary); font: inherit; text-align: left; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.copilot-context-bar__path:hover { color: var(--color-primary); }
.copilot-context-bar__path b { font-weight: 600; }
.copilot-context-bar__gateway,.copilot-context-bar__created { flex: 0 0 auto; }
.copilot-context-bar__visual-warning { display: flex; align-items: center; gap: 6px; color: #b54708; font-size: 11px; line-height: 16px; }
.copilot-context-bar__visual-warning svg { flex: 0 0 auto; }
.copilot-details { display: grid; gap: 20px; }
.copilot-details section + section { padding-top: 18px; border-top: 1px solid var(--color-border-light); }
.copilot-details h3 { margin: 0 0 12px; color: var(--color-text-tertiary); font-size: 11px; font-weight: 600; }
.copilot-details dl { display: grid; grid-template-columns: 130px minmax(0, 1fr); gap: 14px; margin: 0 0 10px; }
.copilot-details dt { color: var(--color-text-tertiary); font-size: 12px; }
.copilot-details dd { overflow-wrap: anywhere; margin: 0; color: var(--color-text-title); font-size: 13px; font-weight: 500; }
.copilot-details ol { display: grid; gap: 8px; margin: 0; padding-left: 30px; }
.copilot-details li { padding-left: 4px; overflow-wrap: anywhere; color: var(--color-text-secondary); font-family: var(--font-mono); font-size: 12px; }
.copilot-details__actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.copilot-details__note { margin: 8px 0 0; color: var(--color-text-tertiary); font-size: 12px; line-height: 1.5; }
.copilot-details__problems { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.copilot-details__problems li { display: grid; gap: 2px; }
.copilot-details__problems strong { overflow-wrap: anywhere; color: var(--color-text-title); font-size: 12px; }
.copilot-details__problems span { color: var(--color-text-tertiary); font-size: 11px; }
@keyframes copilot-status-pulse { 50% { opacity: .58; } }
@media (max-width: 760px) { .copilot-context-bar__origin,.copilot-context-bar__source { max-width: 22%; }.copilot-context-bar__gateway { max-width: 24%; }.copilot-context-bar__created { display: none; } }
</style>
