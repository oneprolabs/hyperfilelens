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
    class="deep-thinking-toggle copilot-option-toggle"
    :aria-pressed="modelValue"
    :aria-label="t('insight.copilot.deepThinking')"
    :disabled="disabled"
    @click="emit('update:modelValue', !modelValue)"
  >
    <Brain
      :size="14"
      aria-hidden="true"
    />
    <span>{{ t('insight.copilot.deepThinking') }}</span>
  </button>
</template>

<style scoped src="./copilotOptionToggle.css" />
