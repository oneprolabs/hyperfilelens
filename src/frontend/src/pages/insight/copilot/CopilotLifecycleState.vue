<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertCircle, Check, ChevronDown, Circle, LoaderCircle, Sparkles, TriangleAlert } from 'lucide-vue-next'
import {
  conversionCountsLabel,
  conversionPhase,
  conversionProblemItems,
  conversionWarningsForDisplay,
} from '../../../lib/conversionSummary'
import { apiErrorMessageI18n } from '../../../lib/api'
import type { LensSessionLink } from '../../../lib/lensApi'

const props = defineProps<{
  session: LensSessionLink
}>()

const emit = defineEmits<{
  retry: []
  delete: []
}>()

const { t } = useI18n()

const steps = [
  { label: 'Validating Selected Data', phases: ['queued', 'resolving_scope', 'reserving_capacity'] },
  { label: 'Preparing Files and Folders', phases: ['restoring'] },
  { label: 'Extracting Document Content', phases: ['converting'] },
  { label: 'Indexing Selected Content', phases: ['creating_knowledge_source'] },
  { label: 'Getting AI Copilot Ready', phases: ['creating_assistant', 'granting_assistant', 'creating_session'] },
]

const currentStep = computed(() => {
  const index = steps.findIndex((step) => step.phases.includes(props.session.provision_phase || ''))
  return index < 0 ? 0 : index
})

const conversion = computed(() => props.session.document_conversion ?? null)
const countsLabel = computed(() => conversionCountsLabel(conversion.value))
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
const conversionWarnings = computed(() => conversionWarningsForDisplay(conversion.value, 5))
const conversionDetail = computed(() => (
  conversion.value?.progress_message?.trim()
  || props.session.provision_detail?.trim()
  || ''
))
const attentionCount = computed(() => allProblemItems.value.length + conversionWarnings.value.length)
const attentionLabel = computed(() => {
  if (!attentionCount.value) return 'Supported document formats'
  return `${attentionCount.value} ${attentionCount.value === 1 ? 'item needs' : 'items need'} attention`
})
const showFailedConversionPanel = computed(() => Boolean(
  conversion.value
  && (countsLabel.value || problemItems.value.length || conversion.value.error || conversionWarnings.value.length),
))
const genericLifecycleError = 'Something went wrong while preparing the selected data. Try again, or delete this chat and create a new one.'
const lifecycleErrorMessage = computed(() => {
  const message = props.session.lifecycle_error_message?.trim() || genericLifecycleError
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
      <h2>Preparing Chat for Retry</h2>
      <p>The previous preparation attempt stopped. Temporary resources are being removed safely before you can try again.</p>
      <div class="copilot-lifecycle-actions">
        <ElButton @click="emit('delete')">
          Delete Chat
        </ElButton>
      </div>
    </div>

    <div
      v-else-if="isCleanupBlocked"
      class="copilot-lifecycle-card is-failed"
    >
      <span class="copilot-lifecycle-icon is-failed"><TriangleAlert :size="30" /></span>
      <h2>Chat Cleanup Needs Attention</h2>
      <p>Active processing could not be confirmed as stopped. Temporary data remains protected so cleanup can be retried safely.</p>
      <div class="copilot-lifecycle-actions">
        <ElButton @click="emit('delete')">
          {{ isDeleteCleanupBlocked ? 'Retry Delete' : 'Delete Chat' }}
        </ElButton>
      </div>
    </div>

    <div
      v-else-if="session.lifecycle_status === 'failed'"
      class="copilot-lifecycle-card is-failed"
    >
      <span class="copilot-lifecycle-icon is-failed"><AlertCircle :size="30" /></span>
      <h2>We Couldn't Prepare This Chat</h2>
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
            <span>{{ item.reason_label }}</span>
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
            <span>{{ warning.label || warning.code }}</span>
          </li>
        </ul>
      </div>
      <div class="copilot-lifecycle-actions">
        <ElButton @click="emit('delete')">
          Delete Chat
        </ElButton>
        <ElButton
          v-if="lifecycleErrorRetryable"
          type="primary"
          @click="emit('retry')"
        >
          Try Again
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
      <h2>Deleting Chat</h2>
      <p>The chat and its temporary data are being removed.</p>
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
          <h2>Preparing Your Chat</h2>
          <p>Your selected data is being prepared for AI Copilot.</p>
        </div>
      </div>

      <div class="copilot-lifecycle-body">
        <ol
          class="copilot-lifecycle-steps"
          aria-label="Chat preparation progress"
        >
          <li
            v-for="(step, index) in steps"
            :key="step.label"
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
          aria-label="Document preparation details"
        >
          <span class="copilot-conversion__eyebrow">Document preparation</span>
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
                  <span>{{ item.reason_label }}</span>
                </li>
              </ul>
              <p
                v-if="allProblemItems.length > problemItems.length"
                class="copilot-conversion__more"
              >
                {{ allProblemItems.length - problemItems.length }} more files are available in Chat Details.
              </p>
              <ul
                v-if="conversionWarnings.length"
                class="copilot-conversion__list is-warnings"
              >
                <li
                  v-for="(warning, index) in conversionWarnings"
                  :key="`${warning.code}-${index}`"
                >
                  <span>{{ warning.label || warning.code }}</span>
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

      <small>You can leave this page. Preparation will continue in the background.</small>
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
