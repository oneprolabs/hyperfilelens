<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Brain } from 'lucide-vue-next'

defineProps<{
  modelValue: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()
const { t } = useI18n()
</script>

<template>
  <button
    type="button"
    class="deep-thinking-toggle"
    role="switch"
    :aria-checked="modelValue"
    :aria-label="t('insight.copilot.deepThinking')"
    :title="t('insight.copilot.deepThinkingHint')"
    :disabled="disabled"
    @click="emit('update:modelValue', !modelValue)"
  >
    <Brain
      :size="16"
      aria-hidden="true"
    />
    <span>{{ t('insight.copilot.deepThinking') }}</span>
    <span
      class="deep-thinking-toggle__track"
      aria-hidden="true"
    ><span /></span>
  </button>
</template>

<style scoped>
.deep-thinking-toggle { display: inline-flex; align-items: center; gap: 6px; min-height: 36px; padding: 6px 8px; border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-card-bg); color: var(--color-text-secondary); font-size: 12px; cursor: pointer; }
.deep-thinking-toggle[aria-checked='true'] { color: var(--color-primary); }
.deep-thinking-toggle__track { display: inline-flex; align-items: center; width: 26px; height: 16px; padding: 2px; border-radius: 9px; background: var(--color-text-tertiary); }
.deep-thinking-toggle__track > span { width: 12px; height: 12px; border-radius: 50%; background: white; transition: transform .15s ease; }
.deep-thinking-toggle[aria-checked='true'] .deep-thinking-toggle__track { background: var(--color-primary); }
.deep-thinking-toggle[aria-checked='true'] .deep-thinking-toggle__track > span { transform: translateX(10px); }
.deep-thinking-toggle:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
.deep-thinking-toggle:disabled { opacity: .5; cursor: not-allowed; }
@media (prefers-reduced-motion: reduce) { .deep-thinking-toggle__track > span { transition: none; } }
</style>
