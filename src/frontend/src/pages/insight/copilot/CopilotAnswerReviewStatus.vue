<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { CopilotDisplayMessage } from './types'

const props = defineProps<{ message: CopilotDisplayMessage }>()
const { t } = useI18n()
const expanded = ref(false)
const detail = computed(() =>
  (props.message.thinking?.termination_detail || {}) as Record<string, unknown>,
)
const status = computed(() => {
  const review = (detail.value.assessment as Record<string, unknown> | undefined)?.review
  return ['not_requested', 'supported', 'unsupported', 'inconclusive'].includes(String(review))
    ? String(review)
    : ''
})
const outcome = computed(() =>
  ['partial', 'blocked'].includes(String(props.message.thinking?.outcome))
    ? String(props.message.thinking?.outcome)
    : '',
)
const reviewLabelKey = computed(() => ({
  not_requested: 'reviewNotRequested',
  supported: 'reviewSupported',
  unsupported: 'reviewUnsupported',
  inconclusive: 'reviewInconclusive',
}[status.value] || ''))
const details = computed(() => {
  const result: string[] = []
  const assessment = detail.value.assessment as Record<string, unknown> | undefined
  if (assessment?.coverage === 'complete') {
    result.push(t('insight.copilot.reviewEvidenceComplete'))
  } else if (assessment?.coverage === 'incomplete') {
    result.push(t('insight.copilot.reviewEvidenceIncomplete'))
  }
  for (const warning of Array.isArray(detail.value.warnings) ? detail.value.warnings : []) {
    if (warning === 'evidence_incomplete' && assessment?.coverage !== 'incomplete') {
      result.push(t('insight.copilot.reviewEvidenceIncomplete'))
    }
    if (warning === 'review_material_incomplete') result.push(t('insight.copilot.reviewMaterialIncomplete'))
    if (warning === 'review_unavailable') result.push(t('insight.copilot.reviewUnavailable'))
    if (warning === 'review_inconclusive') result.push(t('insight.copilot.reviewWarningInconclusive'))
  }
  return result
})
</script>

<template>
  <div
    v-if="status || outcome"
    class="copilot-answer-review"
    :class="`is-${status || outcome}`"
    role="status"
  >
    <template v-if="status">
      <button
        type="button"
        class="copilot-answer-review__button"
        :aria-expanded="details.length ? expanded : undefined"
        @click="details.length && (expanded = !expanded)"
      >
        <span
          class="copilot-answer-review__dot"
          aria-hidden="true"
        />
        <span>
          {{ t('insight.copilot.answerReview') }}:
          {{ t(`insight.copilot.${reviewLabelKey}`) }}
        </span>
        <span
          v-if="details.length"
          aria-hidden="true"
        >{{ expanded ? '⌃' : '⌄' }}</span>
      </button>
    </template>
    <span
      v-if="outcome"
      class="copilot-answer-review__outcome"
      :class="`is-${outcome}`"
    >
      <span
        class="copilot-answer-review__dot"
        aria-hidden="true"
      />
      {{ t(`insight.copilot.outcome${outcome.charAt(0).toUpperCase()}${outcome.slice(1)}`) }}
    </span>
    <div
      v-if="expanded && details.length"
      class="copilot-answer-review__details"
    >
      <p
        v-for="item in details"
        :key="item"
      >
        {{ item }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.copilot-answer-review { display: inline-flex; flex-direction: column; align-items: flex-start; gap: 4px; margin-top: 8px; color: #2e7d55; font-size: 12px; }
.copilot-answer-review.is-not_requested { color: var(--color-text-tertiary); }
.copilot-answer-review.is-unsupported, .copilot-answer-review.is-inconclusive, .copilot-answer-review.is-partial, .copilot-answer-review.is-blocked { color: #8a5a00; }
.copilot-answer-review__button { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border: 1px solid currentColor; border-radius: 999px; background: transparent; color: inherit; font: inherit; cursor: pointer; }
.copilot-answer-review__outcome { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border: 1px solid currentColor; border-radius: 999px; font: inherit; }
.copilot-answer-review__outcome.is-partial, .copilot-answer-review__outcome.is-blocked { color: #8a5a00; }
.copilot-answer-review__dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.copilot-answer-review__details { padding: 6px 10px; border: 1px solid currentColor; border-radius: 8px; opacity: .85; }
.copilot-answer-review__details p { margin: 0; }
</style>
