<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertCircle, Check, ChevronDown, Circle, LoaderCircle, Sparkles, TriangleAlert } from 'lucide-vue-next'
import {
  conversionPhase,
  conversionProblemItems,
  conversionWarningsForDisplay,
} from '../../../lib/conversionSummary'
import { apiErrorMessageI18n } from '../../../lib/api'
import { copilotWarningLabel } from '../../../lib/copilotDisplay'
import type { LensSessionLink } from '../../../lib/lensApi'
import { formatBytes } from '../../../lib/kopiaProgress'

const props = defineProps<{
  session: LensSessionLink
}>()

const emit = defineEmits<{
  retry: []
  delete: []
  forceDelete: []
}>()

const { t } = useI18n()

const stepPhases = [
  { key: 'validatingSelectedData', phases: ['queued', 'resolving_scope', 'reserving_capacity'] },
  { key: 'restoringSelectedData', phases: ['restoring'] },
  { key: 'convertingDocuments', phases: ['converting'] },
  { key: 'gettingCopilotReady', phases: ['creating_knowledge_source', 'creating_assistant', 'granting_assistant', 'creating_session'] },
] as const

const steps = computed(() => stepPhases.map((step) => ({
  ...step,
  label: t(`insight.copilot.lifecycleSteps.${step.key}`),
})))

const currentStep = computed(() => {
  const index = steps.value.findIndex((step) => step.phases.includes(props.session.provision_phase || ''))
  if (index === 2 && phase.value === 'succeeded') return 3
  return index < 0 ? 0 : index
})

const conversion = computed(() => props.session.document_conversion ?? null)
const conversionRecoveryPaused = computed(() => (
  ['DATASOURCE_CONVERSION_REBIND_PAUSED', 'DATASOURCE_CONVERSION_RESUME_EXHAUSTED']
    .includes(conversion.value?.error || props.session.lifecycle_error || '')
))
const countsLabel = computed(() => {
  const counts = conversion.value?.counts
  if (!counts) return ''
  const ready = counts.success + counts.unchanged
  if (!counts.total && !ready && !counts.failed && !counts.unsupported) return ''
  const parts = [t('insight.copilot.conversionReady', { count: ready })]
  if (counts.failed > 0) parts.push(t('insight.copilot.conversionFailed', { count: counts.failed }))
  if (counts.unsupported > 0) parts.push(t('insight.copilot.conversionUnsupported', { count: counts.unsupported }))
  return parts.join(' · ')
})
const allProblemItems = computed(() => conversionProblemItems(conversion.value))
const problemItems = computed(() => allProblemItems.value.slice(0, 12))
const phase = computed(() => conversionPhase(conversion.value))
const isConvertingStep = computed(() => props.session.provision_phase === 'converting')
const showConversionPanel = computed(() => {
  if (currentStep.value < 2
    || props.session.lifecycle_status === 'failed' || props.session.lifecycle_status === 'deleting') {
    return false
  }
  return Boolean(
    isConvertingStep.value
    || phase.value === 'running'
    || (conversion.value && (
      countsLabel.value
      || problemItems.value.length
      || conversionWarnings.value.length
      || props.session.provision_detail
    )),
  )
})
const showFormatHint = computed(() => problemItems.value.some((item) => item.reason === 'UNSUPPORTED_TYPE'))
const conversionWarnings = computed(() => conversionWarningsForDisplay(conversion.value, 5).map((warning) => ({
  ...warning,
  label: t(`insight.copilot.conversionWarnings.${warning.code}`, warning.label || warning.code),
})))
const problemReasonLabel = (item: { name: string; path?: string; reason: string; reason_label: string }) => {
  if (item.reason === 'UNSUPPORTED_TYPE') {
    const extension = (item.name || item.path || '').match(/\.([a-z0-9]{1,10})$/i)?.[1]
    return extension
      ? t('insight.copilot.preparationUnsupportedFormat', { format: extension.toUpperCase() })
      : t('insight.copilot.preparationUnsupportedFile')
  }
  return t(`insight.copilot.conversionReasons.${item.reason}`, item.reason_label)
}
const conversionDetail = computed(() => {
  const detail = conversion.value?.progress_message?.trim()
    || props.session.provision_detail?.trim()
    || ''
  return conversionCountsLabel.value && /^Processed \d+\/\d+ convertible files\.$/.test(detail)
    ? t('insight.copilot.documentConversionRunning')
    : detail
})
const attentionCount = computed(() => allProblemItems.value.length)
const attentionLabel = computed(() => {
  if (outcomes.value.skipped && outcomes.value.failed) return t('insight.copilot.preparationViewSkippedAndFailed')
  if (outcomes.value.failed) return t('insight.copilot.preparationViewFailedFiles')
  if (outcomes.value.skipped) return t('insight.copilot.preparationViewSkippedFiles')
  return t('insight.copilot.preparationViewFileDetails')
})
function safeCount(value?: number) {
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0
}
const outcomes = computed(() => {
  const counts = conversion.value?.counts
  const unsupportedRows = allProblemItems.value.filter((item) => item.reason === 'UNSUPPORTED_TYPE').length
  const skippedRows = allProblemItems.value.filter((item) => item.outcome === 'skipped' || item.reason === 'UNSUPPORTED_TYPE').length
  const failedRows = allProblemItems.value.filter((item) => item.outcome === 'failed' || item.reason === 'CONVERSION_FAILED').length
  const uncertainSkipped = safeCount(conversion.value?.items_truncated) > 0
    && safeCount(counts?.skipped) > safeCount(counts?.unchanged)
  const knownSkipped = allProblemItems.value.filter((item) => item.outcome === 'skipped' && item.reason !== 'UNSUPPORTED_TYPE').length
    + Math.max(unsupportedRows, safeCount(counts?.unsupported))
  return {
    ready: safeCount(counts?.success) + safeCount(counts?.unchanged),
    skipped: uncertainSkipped
      ? Math.max(skippedRows, knownSkipped)
      : Math.max(skippedRows, Math.max(0, safeCount(counts?.skipped) - safeCount(counts?.unchanged))
        + Math.max(unsupportedRows, safeCount(counts?.unsupported))),
    failed: Math.max(failedRows, safeCount(counts?.failed)),
    uncertainSkipped,
  }
})
const noUsableContent = computed(() => phase.value === 'succeeded'
  && !outcomes.value.uncertainSkipped
  && outcomes.value.ready === 0
  && (outcomes.value.skipped > 0 || outcomes.value.failed > 0 || safeCount(conversion.value?.counts.total) > 0))
const conversionResultLabel = computed(() => {
  if (!conversion.value || !['succeeded', 'failed'].includes(phase.value)) return ''
  const parts: string[] = []
  if (noUsableContent.value) parts.push(t('insight.copilot.preparationNoUsableContent'))
  else if (outcomes.value.ready) parts.push(t(outcomes.value.uncertainSkipped
    ? 'insight.copilot.preparationDocumentsAvailableMinimum' : 'insight.copilot.preparationDocumentsAvailable',
  { count: outcomes.value.ready }))
  if (outcomes.value.skipped) parts.push(t(outcomes.value.uncertainSkipped
    ? 'insight.copilot.preparationFilesSkippedMinimum' : 'insight.copilot.preparationFilesSkipped',
  { count: outcomes.value.skipped }))
  if (outcomes.value.failed) parts.push(t('insight.copilot.preparationFilesFailed', { count: outcomes.value.failed }))
  return parts.length
    ? parts.join(t('insight.copilot.preparationResultSeparator'))
    : t(conversion.value.empty_result ? 'insight.copilot.preparationNoConversionNeeded' : 'insight.copilot.preparationProcessingFinished')
})
const showFailedConversionPanel = computed(() => Boolean(
  conversion.value
  && (countsLabel.value || problemItems.value.length || conversion.value.error || conversionWarnings.value.length),
))
const genericLifecycleError = computed(() => t('insight.copilot.genericLifecycleError'))
const lifecycleErrorMessage = computed(() => {
  const message = props.session.lifecycle_error_message?.trim() || genericLifecycleError.value
  const errorCode = props.session.lifecycle_error_code?.trim()
  if (!errorCode) return message
  return apiErrorMessageI18n(
    {
      status: 500,
      message,
      code: errorCode,
      errorCode,
      retryable: props.session.lifecycle_error_retryable,
      meta: props.session.lifecycle_error_meta,
    },
    t,
    genericLifecycleError,
  )
})
const lifecycleErrorRetryable = computed(() => (
  !props.session.lifecycle_error_code?.trim()
  || props.session.lifecycle_error_retryable !== false
))
const isRecoveryCleanup = computed(() => (
  props.session.lifecycle_status === 'failed'
  && props.session.cleanup_intent === 'reset_for_retry'
  && ['pending', 'running'].includes(props.session.cleanup_status || '')
))
const isCleanupBlocked = computed(() => (
  (
    (props.session.lifecycle_status === 'failed' && props.session.cleanup_intent === 'reset_for_retry')
    || (props.session.lifecycle_status === 'deleting' && props.session.cleanup_intent === 'delete_session')
  )
  && props.session.cleanup_status === 'blocked'
))
const isDeleteCleanupBlocked = computed(() => (
  isCleanupBlocked.value && props.session.lifecycle_status === 'deleting'
))
const isForceDeleteAvailable = computed(() => (
  props.session.lifecycle_status === 'deleting'
  && props.session.cleanup_intent === 'delete_session'
  && props.session.force_delete_available === true
))
const isCleanupSafetyBlocked = computed(() => (
  [
    'restore_still_running',
    'conversion_still_running',
    'remote_task_state_unknown',
    'active_cleanup_lease',
    'active_run',
  ].includes(props.session.force_delete_reason || '')
))
const isGatewayQueued = computed(() => (
  props.session.lifecycle_status === 'provisioning'
  && props.session.provision_phase === 'queued'
  && Number(props.session.queue_position || 0) > 0
))

function stepState(index: number) {
  if (reusedData.value && (index === 1 || index === 2)) return 'skipped'
  if (index === 1 && index === currentStep.value && restore.value?.status === 'success') return 'done'
  if (index === 2 && index <= currentStep.value && (noUsableContent.value || phase.value === 'failed')) return 'warning'
  if (index < currentStep.value) return 'done'
  if (index === currentStep.value) return 'active'
  return 'pending'
}

const reusedData = computed(() => props.session.preparation_progress?.reused_data === true)
const restore = computed(() => props.session.preparation_progress?.restore ?? null)
const conversionQueued = computed(() => ['STARTING', 'PENDING', 'RECEIVED', 'RETRY']
  .includes(conversion.value?.status?.toUpperCase() || ''))
function validPercent(value?: number | null) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null
}
const restorePercent = computed(() => {
  const value = restore.value
  if (!value || !['running', 'success'].includes(value.status)
    || !['transferring', 'finalizing', 'done'].includes(value.phase)) return null
  return validPercent(value.progress_percent)
})
const conversionPercent = computed(() => conversionQueued.value || phase.value !== 'running'
  ? null : validPercent(conversion.value?.progress_percent))
function stepPercent(index: number) {
  if (stepState(index) !== 'active') return null
  if (index === 1) return restorePercent.value
  if (index === 2) return conversionPercent.value
  return null
}
const restoreBytesLabel = computed(() => {
  const value = restore.value
  if (value?.bytes_done == null || value.bytes_total == null
    || value.bytes_done < 0 || value.bytes_total <= 0) return ''
  return t('insight.copilot.preparationRestoredBytes', {
    done: formatBytes(value.bytes_done), total: formatBytes(value.bytes_total),
  })
})
const restoreEtaLabel = computed(() => {
  const value = restore.value
  if (value?.status !== 'running' || value.phase !== 'transferring'
    || restorePercent.value == null || value.eta_seconds == null
    || !Number.isFinite(value.eta_seconds) || value.eta_seconds <= 0) return ''
  const seconds = Math.ceil(value.eta_seconds)
  return seconds < 60
    ? t('insight.copilot.preparationEtaSeconds', { count: seconds })
    : t('insight.copilot.preparationEtaMinutes', { count: Math.ceil(seconds / 60) })
})
const restoreDetail = computed(() => {
  if (restore.value?.phase === 'finalizing') return t('insight.copilot.preparationRestoreFinalizing')
  if (restore.value?.status === 'pending' || restore.value?.phase === 'queued') return t('insight.copilot.preparationRestoreQueued')
  if (restore.value?.phase === 'estimating') return t('insight.copilot.preparationRestoreEstimating')
  return restoreBytesLabel.value ? '' : t('insight.copilot.preparationRestoring')
})
const conversionCountsLabel = computed(() => {
  const counts = conversion.value?.progress_counts
  if (!counts || !Number.isFinite(counts.candidates) || counts.candidates <= 0
    || !Number.isFinite(counts.processed) || !Number.isFinite(counts.unsupported)) return ''
  return t('insight.copilot.preparationProcessedDocuments', {
    done: Math.min(counts.candidates, Math.max(0, counts.processed - counts.unsupported)),
    total: counts.candidates,
  })
})
const assistantPreparationLabel = computed(() => {
  const state = props.session.preparation_progress?.assistant_state
  if (state === 'retrying') return t('insight.copilot.preparationServiceRetrying')
  if (state === 'waiting' || (!state && props.session.provision_phase === 'converting')) {
    return t('insight.copilot.preparationWaitingForSetup')
  }
  if (state === 'opening_session' || (!state && ['granting_assistant', 'creating_session'].includes(props.session.provision_phase || ''))) {
    return t('insight.copilot.preparationOpeningChat')
  }
  if (state === 'configuring' || (!state && ['creating_knowledge_source', 'creating_assistant'].includes(props.session.provision_phase || ''))) {
    return t('insight.copilot.preparationConfiguringAssistant')
  }
  return t('insight.copilot.preparationFinishingChat')
})
function stepResult(index: number) {
  if (stepState(index) === 'skipped' || !['done', 'warning'].includes(stepState(index))) return ''
  if (index === 0) return t('insight.copilot.preparationDataVerified')
  if (index === 1) {
    const bytes = restore.value?.bytes_done
    return restore.value?.status === 'success' && typeof bytes === 'number' && Number.isFinite(bytes) && bytes > 0
      ? t('insight.copilot.preparationRestoreCompleteBytes', { size: formatBytes(bytes) })
      : t('insight.copilot.preparationRestoreComplete')
  }
  return ''
}
function stepLabel(index: number) {
  return index === 2 && index <= currentStep.value && noUsableContent.value
    ? t('insight.copilot.preparationProcessingFinished')
    : steps.value[index]?.label
}
</script>

<template>
  <main class="copilot-lifecycle-state">
    <div
      v-if="isRecoveryCleanup"
      class="copilot-lifecycle-card"
    >
      <span class="copilot-lifecycle-icon"><LoaderCircle
        :size="30"
        class="copilot-lifecycle-spin"
      /></span>
      <h2>{{ t('insight.copilot.preparingChatForRetry') }}</h2>
      <p>{{ t('insight.copilot.retryPreparationDetail') }}</p>
      <div class="copilot-lifecycle-actions">
        <ElButton @click="emit('delete')">
          {{ t('insight.copilot.deleteChat') }}
        </ElButton>
      </div>
    </div>

    <div
      v-else-if="isCleanupBlocked"
      class="copilot-lifecycle-card is-failed"
    >
      <span class="copilot-lifecycle-icon is-failed"><TriangleAlert :size="30" /></span>
      <h2>{{ t(isDeleteCleanupBlocked ? 'insight.copilot.chatCouldNotBeDeleted' : 'insight.copilot.chatCleanupPaused') }}</h2>
      <p v-if="isForceDeleteAvailable">
        {{ t('insight.copilot.forceDeleteConfirmMessage') }}
      </p>
      <p v-else-if="isCleanupSafetyBlocked">
        {{ t('insight.copilot.cleanupSafetyDetail') }}
      </p>
      <p v-else>
        {{ t('insight.copilot.cleanupBlockedDetail') }}
      </p>
      <div class="copilot-lifecycle-actions">
        <ElButton @click="emit('delete')">
          {{ t(isDeleteCleanupBlocked ? 'insight.copilot.retryDelete' : 'insight.copilot.deleteChat') }}
        </ElButton>
        <ElButton
          v-if="isForceDeleteAvailable"
          type="danger"
          @click="emit('forceDelete')"
        >
          {{ t('insight.copilot.forceDelete') }}
        </ElButton>
      </div>
    </div>

    <div
      v-else-if="session.lifecycle_status === 'failed'"
      class="copilot-lifecycle-card is-failed"
    >
      <span class="copilot-lifecycle-icon is-failed"><AlertCircle :size="30" /></span>
      <h2>{{ t('insight.copilot.couldNotPrepareChat') }}</h2>
      <p>{{ lifecycleErrorMessage }}</p>
      <p v-if="session.cleanup_intent === 'none' && session.knowledge_source">
        {{ t(conversionRecoveryPaused ? 'insight.copilot.retainedConversionRetryHint' : 'insight.copilot.retainedRetryHint') }}
      </p>
      <div
        v-if="showFailedConversionPanel"
        class="copilot-conversion copilot-conversion--failed"
      >
        <p
          v-if="countsLabel"
          class="copilot-conversion__counts"
        >
          {{ countsLabel }}
        </p>
        <p
          v-if="conversion?.error"
          class="copilot-conversion__detail"
        >
          {{ conversion.error }}
        </p>
        <ul
          v-if="problemItems.length"
          class="copilot-conversion__list"
        >
          <li
            v-for="(item, index) in problemItems"
            :key="`${item.name}-${index}`"
          >
            <strong>{{ item.name }}</strong>
            <span>{{ problemReasonLabel(item) }}</span>
          </li>
        </ul>
        <ul
          v-if="conversionWarnings.length"
          class="copilot-conversion__list"
        >
          <li
            v-for="(warning, index) in conversionWarnings"
            :key="`${warning.code}-${index}`"
          >
            <span>{{ copilotWarningLabel(t, warning.code, warning.label || warning.code) }}</span>
          </li>
        </ul>
      </div>
      <div class="copilot-lifecycle-actions">
        <ElButton
          type="danger"
          @click="emit('delete')"
        >
          {{ t('insight.copilot.deleteChat') }}
        </ElButton>
        <ElButton
          v-if="lifecycleErrorRetryable"
          type="primary"
          @click="emit('retry')"
        >
          {{ t('insight.copilot.tryAgain') }}
        </ElButton>
      </div>
    </div>

    <div
      v-else-if="session.lifecycle_status === 'deleting'"
      class="copilot-lifecycle-card"
    >
      <span class="copilot-lifecycle-icon"><LoaderCircle
        :size="30"
        class="copilot-lifecycle-spin"
      /></span>
      <h2>{{ t('insight.copilot.deletingChat') }}</h2>
      <p v-if="isForceDeleteAvailable">
        {{ t('insight.copilot.forceDeleteConfirmMessage') }}
      </p>
      <p v-else-if="isCleanupSafetyBlocked">
        {{ t('insight.copilot.cleanupSafetyDetail') }}
      </p>
      <p v-else>
        {{ t('insight.copilot.deletingChatDetail') }}
      </p>
      <div
        v-if="isForceDeleteAvailable"
        class="copilot-lifecycle-actions"
      >
        <ElButton
          type="danger"
          @click="emit('forceDelete')"
        >
          {{ t('insight.copilot.forceDelete') }}
        </ElButton>
      </div>
    </div>

    <div
      v-else
      class="copilot-lifecycle-card copilot-lifecycle-card--preparing"
    >
      <div
        class="copilot-lifecycle-heading"
        role="status"
        aria-live="polite"
      >
        <span class="copilot-lifecycle-icon"><Sparkles :size="25" /></span>
        <div>
          <h2>{{ t('insight.copilot.preparingYourChat') }}</h2>
          <p>{{ t('insight.copilot.selectedDataPreparing') }}</p>
        </div>
      </div>

      <div class="copilot-lifecycle-body">
        <ol
          class="copilot-lifecycle-steps"
          :aria-label="t('insight.copilot.chatPreparationProgress')"
        >
          <li
            v-for="(step, index) in steps"
            :key="step.key"
            :class="`is-${stepState(index)}`"
            :aria-current="stepState(index) === 'active' ? 'step' : undefined"
          >
            <div class="copilot-lifecycle-step__heading">
              <span class="copilot-lifecycle-step__icon">
                <TriangleAlert
                  v-if="stepState(index) === 'warning'"
                  :size="14"
                  aria-hidden="true"
                />
                <Check
                  v-else-if="['done', 'skipped'].includes(stepState(index))"
                  :size="14"
                  aria-hidden="true"
                />
                <LoaderCircle
                  v-else-if="stepState(index) === 'active'"
                  :size="14"
                  class="copilot-lifecycle-spin"
                  aria-hidden="true"
                />
                <Circle
                  v-else
                  :size="12"
                  aria-hidden="true"
                />
              </span>
              <span>{{ stepLabel(index) }}</span>
              <span
                v-if="stepState(index) === 'skipped'"
                class="copilot-lifecycle-step__note"
              >{{ t('insight.copilot.preparationAlreadyPrepared') }}</span>
              <span
                v-if="stepPercent(index) != null"
                class="copilot-lifecycle-step__percent"
              >{{ Math.floor(stepPercent(index)!) }}%</span>
            </div>
            <p
              v-if="stepResult(index)"
              class="copilot-lifecycle-step__result"
            >
              {{ stepResult(index) }}
            </p>
            <div
              v-if="stepPercent(index) != null"
              class="copilot-lifecycle-step__progress"
              role="progressbar"
              :aria-label="step.label"
              :aria-valuenow="stepPercent(index)!"
              :aria-valuemin="0"
              :aria-valuemax="100"
            >
              <span :style="{ width: `${stepPercent(index)}%` }" />
            </div>
            <div
              v-if="index === 0 && stepState(index) === 'active'"
              class="copilot-lifecycle-step__detail"
            >
              <p v-if="isGatewayQueued">
                {{ t('insight.copilot.gatewayQueueHint') }}
              </p>
              <p v-else>
                {{ t('insight.copilot.preparationValidatingData') }}
              </p>
            </div>
            <div
              v-if="index === 1 && stepState(index) === 'active'"
              class="copilot-lifecycle-step__detail"
            >
              <p v-if="restoreDetail">
                {{ restoreDetail }}
              </p>
              <p v-if="restoreBytesLabel">
                {{ restoreBytesLabel }}
              </p>
              <p v-if="restoreEtaLabel">
                {{ restoreEtaLabel }}
              </p>
            </div>
            <div
              v-if="index === 3 && stepState(index) === 'active'"
              class="copilot-lifecycle-step__detail"
            >
              <p>{{ assistantPreparationLabel }}</p>
            </div>
            <section
              v-if="index === 2 && !reusedData && showConversionPanel && (['active', 'warning'].includes(stepState(index)) || phase === 'succeeded' || attentionCount || conversionWarnings.length)"
              class="copilot-conversion"
              :aria-label="t('insight.copilot.documentPreparationDetails')"
            >
              <p
                v-if="conversionResultLabel"
                class="copilot-conversion__result"
              >
                {{ conversionResultLabel }}
              </p>
              <p
                v-if="stepState(index) === 'active' && conversionQueued"
                class="copilot-conversion__detail"
              >
                {{ t('insight.copilot.preparationConversionQueued') }}
              </p>
              <p
                v-else-if="stepState(index) === 'active' && conversionDetail"
                class="copilot-conversion__detail"
              >
                {{ conversionDetail }}
              </p>
              <p
                v-else-if="stepState(index) === 'active'"
                class="copilot-conversion__detail"
              >
                {{ t('insight.copilot.documentConversionRunning') }}
              </p>
              <p
                v-if="stepState(index) === 'active' && conversionCountsLabel"
                class="copilot-conversion__counts"
              >
                {{ conversionCountsLabel }}
              </p>

              <details
                v-if="attentionCount"
                class="copilot-conversion__details"
              >
                <summary>
                  <span
                    class="copilot-conversion__summary-icon"
                    :class="{ 'has-attention': attentionCount }"
                  >
                    <TriangleAlert
                      v-if="attentionCount"
                      :size="14"
                    />
                    <Circle
                      v-else
                      :size="12"
                    />
                  </span>
                  <span>{{ attentionLabel }}</span>
                  <ChevronDown
                    :size="15"
                    class="copilot-conversion__chevron"
                  />
                </summary>
                <div class="copilot-conversion__details-body">
                  <ul
                    v-if="problemItems.length"
                    class="copilot-conversion__list"
                  >
                    <li
                      v-for="(item, itemIndex) in problemItems"
                      :key="`${item.name}-${itemIndex}`"
                    >
                      <span class="copilot-conversion__file-name">{{ item.name }}</span>
                      <span>{{ problemReasonLabel(item) }}</span>
                    </li>
                  </ul>
                  <p
                    v-if="allProblemItems.length > problemItems.length"
                    class="copilot-conversion__more"
                  >
                    {{ t('insight.copilot.moreFilesInChatDetails', { count: allProblemItems.length - problemItems.length }) }}
                  </p>
                  <p
                    v-if="showFormatHint"
                    class="copilot-conversion__hint"
                  >
                    {{ t('insight.copilot.preparationConvertUnsupportedHint') }}
                  </p>
                </div>
              </details>
              <ul
                v-if="conversionWarnings.length"
                class="copilot-conversion__list is-warnings"
              >
                <li
                  v-for="(warning, warningIndex) in conversionWarnings"
                  :key="`${warning.code}-${warningIndex}`"
                >
                  <span>{{ copilotWarningLabel(t, warning.code, warning.label || warning.code) }}</span>
                </li>
              </ul>
            </section>
          </li>
        </ol>
      </div>

      <small>{{ t('insight.copilot.backgroundPreparationHint') }}</small>
    </div>
  </main>
</template>

<style scoped>
.copilot-lifecycle-state { display: flex; min-height: 0; flex: 1; align-items: center; justify-content: center; padding: 32px 24px; overflow-y: auto; background: var(--color-card-bg); }
.copilot-lifecycle-card { display: flex; width: min(520px, 100%); flex-direction: column; align-items: center; padding: 30px 34px; border: 1px solid var(--color-border-light); border-radius: 14px; background: var(--color-card-bg); box-shadow: 0 14px 34px rgba(29, 33, 41, .07); text-align: center; }
.copilot-lifecycle-heading { display: flex; align-items: center; justify-content: center; gap: 14px; text-align: left; }
.copilot-lifecycle-icon { display: inline-flex; width: 48px; height: 48px; flex: 0 0 48px; align-items: center; justify-content: center; border-radius: 14px; background: color-mix(in srgb, var(--color-primary) 10%, var(--color-card-bg)); color: var(--color-primary); animation: copilot-lifecycle-breathe 2.2s ease-in-out infinite; }
.copilot-lifecycle-card > .copilot-lifecycle-icon { margin-bottom: 18px; }
.copilot-lifecycle-icon.is-failed { background: #fef3f2; color: #d92d20; animation: none; }
.copilot-lifecycle-card h2 { margin: 0; color: var(--color-text-title); font-size: 20px; font-weight: 650; }
.copilot-lifecycle-card > p,.copilot-lifecycle-heading p { max-width: 430px; margin: 7px 0 0; color: var(--color-text-tertiary); font-size: 13px; line-height: 1.55; }
.copilot-lifecycle-body { width: 100%; margin: 24px 0 20px; }
.copilot-lifecycle-steps { display: grid; width: 100%; gap: 18px; margin: 0; padding: 7px 0; list-style: none; text-align: left; }
.copilot-lifecycle-steps li { min-width: 0; color: var(--color-text-secondary); font-size: 13px; }
.copilot-lifecycle-step__heading { display: flex; align-items: center; gap: 10px; }
.copilot-lifecycle-step__icon { display: inline-flex; width: 20px; height: 20px; flex: 0 0 20px; align-items: center; justify-content: center; color: var(--color-text-tertiary); }
.copilot-lifecycle-steps li.is-done .copilot-lifecycle-step__icon,.copilot-lifecycle-steps li.is-skipped .copilot-lifecycle-step__icon { border-radius: 999px; background: var(--color-success-light); color: var(--color-success-text); }
.copilot-lifecycle-steps li.is-active .copilot-lifecycle-step__heading { color: var(--color-text-title); font-weight: 600; }
.copilot-lifecycle-steps li.is-active .copilot-lifecycle-step__icon { color: var(--color-primary); }
.copilot-lifecycle-steps li.is-warning .copilot-lifecycle-step__icon { color: var(--color-warning-text); }
.copilot-lifecycle-step__percent { margin-left: auto; font-variant-numeric: tabular-nums; }
.copilot-lifecycle-step__note { margin-left: auto; font-size: 11px; }
.copilot-lifecycle-step__progress { height: 4px; margin: 10px 0 0 30px; overflow: hidden; border-radius: 2px; background: var(--color-grey-3); }
.copilot-lifecycle-step__progress > span { display: block; height: 100%; border-radius: inherit; background: var(--color-primary); }
.copilot-lifecycle-step__detail { margin: 9px 0 0 30px; font-size: 12px; line-height: 1.5; }
.copilot-lifecycle-step__detail p { margin: 3px 0 0; }
.copilot-lifecycle-step__result { margin: 5px 0 0 30px; font-size: 12px; line-height: 1.5; }
.copilot-lifecycle-card small { color: var(--color-text-tertiary); font-size: 12px; }
.copilot-lifecycle-actions { display: flex; justify-content: center; gap: 10px; margin-top: 24px; }
.copilot-lifecycle-spin { animation: copilot-lifecycle-spin .9s linear infinite; }
.copilot-conversion { min-width: 0; margin: 9px 0 0 30px; text-align: left; }
.copilot-conversion--failed { width: min(400px, 100%); margin: 18px 0 0; padding: 14px 16px; border: 0; border-radius: 10px; background: var(--color-grey-2); }
.copilot-conversion__eyebrow { display: block; margin-bottom: 8px; color: var(--color-text-title); font-size: 12px; font-weight: 650; }
.copilot-conversion__detail { margin: 0; overflow-wrap: anywhere; color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
.copilot-conversion__counts { margin: 9px 0 0; color: var(--color-text-title); font-size: 13px; font-weight: 650; }
.copilot-conversion__result { margin: 0; color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
.copilot-conversion__details { margin-top: 10px; }
.copilot-conversion__details summary { display: flex; min-height: 40px; align-items: center; gap: 8px; padding: 9px 0 0; color: var(--color-text-secondary); font-size: 12px; font-weight: 600; list-style: none; cursor: pointer; }
.copilot-conversion__details summary::-webkit-details-marker { display: none; }
.copilot-conversion__details summary:focus-visible { border-radius: 4px; outline: 2px solid color-mix(in srgb, var(--color-primary) 45%, transparent); outline-offset: 2px; }
.copilot-conversion__summary-icon { display: inline-flex; align-items: center; color: var(--color-text-tertiary); }
.copilot-conversion__summary-icon.has-attention { color: #b54708; }
.copilot-conversion__chevron { margin-left: auto; transition: transform .2s ease; }
.copilot-conversion__details[open] .copilot-conversion__chevron { transform: rotate(180deg); }
.copilot-conversion__details-body { max-height: 230px; padding: 5px 4px 2px 22px; overflow-y: auto; }
.copilot-conversion__list { display: grid; gap: 9px; margin: 0; padding: 0; list-style: none; }
.copilot-conversion__list.is-warnings { margin-top: 10px; }
.copilot-conversion__list li { display: grid; gap: 2px; }
.copilot-conversion__list strong { overflow-wrap: anywhere; color: var(--color-text-title); font-size: 12px; font-weight: 600; }
.copilot-conversion__list span { overflow-wrap: anywhere; color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
.copilot-conversion__list .copilot-conversion__file-name { color: var(--color-text-title); font-weight: 500; }
.copilot-conversion__more { margin: 10px 0 0; color: var(--color-text-tertiary); font-size: 11px; line-height: 1.45; }
.copilot-conversion__hint { margin: 10px 0 0; color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
@keyframes copilot-lifecycle-spin { to { transform: rotate(360deg); } }
@keyframes copilot-lifecycle-breathe { 50% { transform: translateY(-3px) scale(1.04); box-shadow: 0 10px 26px color-mix(in srgb, var(--color-primary) 20%, transparent); } }
@media (max-width: 760px) {
  .copilot-lifecycle-state { align-items: flex-start; padding: 20px 14px; }
  .copilot-lifecycle-card { width: 100%; padding: 24px 18px; }
  .copilot-lifecycle-heading { align-items: flex-start; }
}
@media (prefers-reduced-motion: reduce) {
  .copilot-lifecycle-icon,.copilot-lifecycle-spin { animation: none; }
  .copilot-conversion__chevron { transition: none; }
}
</style>
