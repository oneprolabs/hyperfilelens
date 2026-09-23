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
  { key: 'preparingFilesAndFolders', phases: ['restoring'] },
  { key: 'extractingDocumentContent', phases: ['converting'] },
  { key: 'indexingSelectedContent', phases: ['creating_knowledge_source'] },
  { key: 'gettingCopilotReady', phases: ['creating_assistant', 'granting_assistant', 'creating_session'] },
] as const

const steps = computed(() => stepPhases.map((step) => ({
  ...step,
  label: t(`insight.copilot.lifecycleSteps.${step.key}`),
})))

const currentStep = computed(() => {
  const index = steps.value.findIndex((step) => step.phases.includes(props.session.provision_phase || ''))
  return index < 0 ? 0 : index
})

const conversion = computed(() => props.session.document_conversion ?? null)
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
const showRunningMessage = computed(() => (
  (isConvertingStep.value || phase.value === 'running') && !countsLabel.value
))
const showConversionPanel = computed(() => {
  if (props.session.lifecycle_status === 'failed' || props.session.lifecycle_status === 'deleting') {
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
const showFormatHint = computed(() => (
  isConvertingStep.value || phase.value === 'running'
))
const conversionWarnings = computed(() => conversionWarningsForDisplay(conversion.value, 5).map((warning) => ({
  ...warning,
  label: t(`insight.copilot.conversionWarnings.${warning.code}`, warning.label || warning.code),
})))
const problemReasonLabel = (item: { reason: string; reason_label: string }) => t(
  `insight.copilot.conversionReasons.${item.reason}`,
  item.reason_label,
)
const conversionDetail = computed(() => (
  conversion.value?.progress_message?.trim()
  || props.session.provision_detail?.trim()
  || ''
))
const attentionCount = computed(() => allProblemItems.value.length + conversionWarnings.value.length)
const attentionLabel = computed(() => {
  if (!attentionCount.value) return t('insight.copilot.supportedDocumentFormats')
  return t('insight.copilot.itemsNeedAttention', { count: attentionCount.value })
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
  if (index < currentStep.value) return 'done'
  if (index === currentStep.value) return 'active'
  return 'pending'
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
      v-else-if="isGatewayQueued"
      class="copilot-lifecycle-card"
      role="status"
      aria-live="polite"
    >
      <span class="copilot-lifecycle-icon"><LoaderCircle
        :size="30"
        class="copilot-lifecycle-spin"
      /></span>
      <h2>{{ t('insight.copilot.gatewayQueueTitle') }}</h2>
      <p>{{ t('insight.copilot.gatewayQueueHint') }}</p>
    </div>

    <div
      v-else
      class="copilot-lifecycle-card"
      :class="{ 'has-conversion': showConversionPanel }"
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
          >
            <span>
              <Check
                v-if="stepState(index) === 'done'"
                :size="14"
              />
              <LoaderCircle
                v-else-if="stepState(index) === 'active'"
                :size="14"
                class="copilot-lifecycle-spin"
              />
              <Circle
                v-else
                :size="12"
              />
            </span>
            {{ step.label }}
          </li>
        </ol>

        <section
          v-if="showConversionPanel"
          class="copilot-conversion"
          :aria-label="t('insight.copilot.documentPreparationDetails')"
        >
          <span class="copilot-conversion__eyebrow">{{ t('insight.copilot.documentPreparation') }}</span>
          <p
            v-if="conversionDetail"
            class="copilot-conversion__detail"
          >
            {{ conversionDetail }}
          </p>
          <p
            v-else-if="showRunningMessage"
            class="copilot-conversion__detail"
          >
            {{ t('insight.copilot.documentConversionRunning') }}
          </p>
          <p
            v-if="countsLabel"
            class="copilot-conversion__counts"
          >
            {{ countsLabel }}
          </p>

          <details
            v-if="attentionCount || showFormatHint"
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
                  v-for="(item, index) in problemItems"
                  :key="`${item.name}-${index}`"
                >
                  <strong>{{ item.name }}</strong>
            <span>{{ problemReasonLabel(item) }}</span>
                </li>
              </ul>
              <p
                v-if="allProblemItems.length > problemItems.length"
                class="copilot-conversion__more"
              >
                {{ t('insight.copilot.moreFilesInChatDetails', { count: allProblemItems.length - problemItems.length }) }}
              </p>
              <ul
                v-if="conversionWarnings.length"
                class="copilot-conversion__list is-warnings"
              >
                <li
                  v-for="(warning, index) in conversionWarnings"
                  :key="`${warning.code}-${index}`"
                >
                  <span>{{ copilotWarningLabel(t, warning.code, warning.label || warning.code) }}</span>
                </li>
              </ul>
              <p
                v-if="showFormatHint"
                class="copilot-conversion__hint"
              >
                {{ t('insight.copilot.documentFormatHint') }}
              </p>
            </div>
          </details>
        </section>
      </div>

      <small>{{ t('insight.copilot.backgroundPreparationHint') }}</small>
    </div>
  </main>
</template>

<style scoped>
.copilot-lifecycle-state { display: flex; min-height: 0; flex: 1; align-items: center; justify-content: center; padding: 32px 24px; overflow-y: auto; background: var(--color-card-bg); }
.copilot-lifecycle-card { display: flex; width: min(520px, 100%); flex-direction: column; align-items: center; padding: 30px 34px; border: 1px solid var(--color-border-light); border-radius: 14px; background: var(--color-card-bg); box-shadow: 0 14px 34px rgba(29, 33, 41, .07); text-align: center; }
.copilot-lifecycle-card.has-conversion { width: min(720px, 100%); }
.copilot-lifecycle-heading { display: flex; align-items: center; justify-content: center; gap: 14px; text-align: left; }
.copilot-lifecycle-icon { display: inline-flex; width: 48px; height: 48px; flex: 0 0 48px; align-items: center; justify-content: center; border-radius: 14px; background: color-mix(in srgb, var(--color-primary) 10%, var(--color-card-bg)); color: var(--color-primary); animation: copilot-lifecycle-breathe 2.2s ease-in-out infinite; }
.copilot-lifecycle-card > .copilot-lifecycle-icon { margin-bottom: 18px; }
.copilot-lifecycle-icon.is-failed { background: #fef3f2; color: #d92d20; animation: none; }
.copilot-lifecycle-card h2 { margin: 0; color: var(--color-text-title); font-size: 20px; font-weight: 650; }
.copilot-lifecycle-card > p,.copilot-lifecycle-heading p { max-width: 430px; margin: 7px 0 0; color: var(--color-text-tertiary); font-size: 13px; line-height: 1.55; }
.copilot-lifecycle-body { display: grid; width: 100%; grid-template-columns: minmax(0, 360px); justify-content: center; margin: 24px 0 20px; }
.has-conversion .copilot-lifecycle-body { grid-template-columns: minmax(0, .92fr) minmax(0, 1.08fr); align-items: start; gap: 24px; }
.copilot-lifecycle-steps { display: grid; width: 100%; gap: 13px; margin: 0; padding: 7px 0; list-style: none; text-align: left; }
.copilot-lifecycle-steps li { display: flex; align-items: center; gap: 10px; color: var(--color-text-disabled); font-size: 13px; }
.copilot-lifecycle-steps li > span { display: inline-flex; width: 20px; height: 20px; flex-shrink: 0; align-items: center; justify-content: center; }
.copilot-lifecycle-steps li.is-done { color: var(--color-text-secondary); }.copilot-lifecycle-steps li.is-done > span { border-radius: 999px; background: #ecfdf3; color: #039855; }
.copilot-lifecycle-steps li.is-active { color: var(--color-text-title); font-weight: 600; }.copilot-lifecycle-steps li.is-active > span { color: var(--color-primary); }
.copilot-lifecycle-card small { color: var(--color-text-tertiary); font-size: 12px; }
.copilot-lifecycle-actions { display: flex; justify-content: center; gap: 10px; margin-top: 24px; }
.copilot-lifecycle-spin { animation: copilot-lifecycle-spin .9s linear infinite; }
.copilot-conversion { width: 100%; min-width: 0; padding: 7px 0 7px 24px; border-left: 1px solid var(--color-border-light); text-align: left; }
.copilot-conversion--failed { width: min(400px, 100%); margin-top: 18px; padding: 14px 16px; border: 0; border-radius: 10px; background: var(--color-grey-2); }
.copilot-conversion__eyebrow { display: block; margin-bottom: 8px; color: var(--color-text-title); font-size: 12px; font-weight: 650; }
.copilot-conversion__detail { margin: 0; color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
.copilot-conversion__counts { margin: 9px 0 0; color: var(--color-text-title); font-size: 13px; font-weight: 650; }
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
.copilot-conversion__list span { color: var(--color-text-tertiary); font-size: 11px; line-height: 1.4; }
.copilot-conversion__more { margin: 10px 0 0; color: var(--color-text-tertiary); font-size: 11px; line-height: 1.45; }
.copilot-conversion__hint { margin: 10px 0 0; color: var(--color-text-tertiary); font-size: 11px; line-height: 1.45; }
@keyframes copilot-lifecycle-spin { to { transform: rotate(360deg); } }
@keyframes copilot-lifecycle-breathe { 50% { transform: translateY(-3px) scale(1.04); box-shadow: 0 10px 26px color-mix(in srgb, var(--color-primary) 20%, transparent); } }
@media (max-width: 760px) {
  .copilot-lifecycle-state { align-items: flex-start; padding: 20px 14px; }
  .copilot-lifecycle-card,.copilot-lifecycle-card.has-conversion { width: 100%; padding: 24px 18px; }
  .has-conversion .copilot-lifecycle-body { grid-template-columns: minmax(0, 1fr); }
  .copilot-lifecycle-heading { align-items: flex-start; }
  .copilot-conversion { padding: 18px 0 0; border-top: 1px solid var(--color-border-light); border-left: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .copilot-lifecycle-icon,.copilot-lifecycle-spin { animation: none; }
  .copilot-conversion__chevron { transition: none; }
}
</style>
