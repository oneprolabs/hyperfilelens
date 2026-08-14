<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowUp, Square } from 'lucide-vue-next'

const props = defineProps<{
  modelValue: string
  sending?: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  send: []
  stop: []
  attach: []
}>()

const { t } = useI18n()
const fieldRef = ref<HTMLTextAreaElement | null>(null)

function resizeField() {
  const el = fieldRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`
}

function onInput(event: Event) {
  const value = (event.target as HTMLTextAreaElement).value
  emit('update:modelValue', value)
  resizeField()
}

watch(
  () => props.modelValue,
  () => {
    nextTick(resizeField)
  },
)
</script>

<template>
  <footer class="copilot-composer">
    <div
      class="copilot-input-shell"
      :class="{ 'is-disabled': disabled }"
    >
      <textarea
        ref="fieldRef"
        :value="modelValue"
        rows="1"
        :placeholder="t('insight.copilot.inputPlaceholder')"
        :disabled="disabled"
        class="copilot-input-field"
        @input="onInput"
        @keydown.enter.exact.prevent="emit('send')"
      />
      <button
        v-if="sending"
        type="button"
        class="copilot-send-btn copilot-send-btn--stop"
        :title="t('common.stop')"
        @click="emit('stop')"
      >
        <Square :size="14" />
      </button>
      <button
        v-else
        type="button"
        class="copilot-send-btn"
        :class="{ 'is-active': modelValue.trim() && !disabled }"
        :disabled="disabled || !modelValue.trim()"
        :title="t('insight.copilot.send')"
        @click="emit('send')"
      >
        <ArrowUp
          :size="18"
          :stroke-width="2.25"
        />
      </button>
    </div>

    <p class="copilot-disclaimer">
      {{ t('insight.copilot.disclaimer') }}
    </p>
  </footer>
</template>

<style scoped>
.copilot-composer {
  padding: 12px 28px 14px;
  background: var(--color-grey-1);
  border-top: 1px solid var(--color-border-light);
}

.copilot-input-shell {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  min-height: 52px;
  padding: 6px 6px 6px 20px;
  background: var(--color-card-bg, #fff);
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 999px;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.copilot-input-shell:focus-within:not(.is-disabled) {
  border-color: var(--color-border-strong, #cbd5e1);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-primary, #6366f1) 8%, transparent);
}

.copilot-input-shell.is-disabled {
  opacity: 0.65;
}

.copilot-input-field {
  flex: 1;
  min-width: 0;
  min-height: 38px;
  max-height: 160px;
  padding: 8px 0;
  margin: 0;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font: inherit;
  font-size: 15px;
  line-height: 1.45;
  color: var(--color-text-primary);
}

.copilot-input-field::placeholder {
  color: var(--color-text-tertiary, #94a3b8);
}

.copilot-input-field:disabled {
  cursor: not-allowed;
}

.copilot-send-btn {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  margin-bottom: 1px;
  padding: 0;
  border: none;
  border-radius: 999px;
  background: var(--color-grey-2, #f1f5f9);
  color: var(--color-text-disabled, #cbd5e1);
  cursor: not-allowed;
  transition: background 0.15s ease, color 0.15s ease, transform 0.1s ease;
}

.copilot-send-btn.is-active {
  background: var(--color-grey-3, #e2e8f0);
  color: var(--color-text-secondary, #64748b);
  cursor: pointer;
}

.copilot-send-btn.is-active:hover {
  background: var(--color-grey-4, #cbd5e1);
  color: var(--color-text-primary, #334155);
}

.copilot-send-btn.is-active:active {
  transform: scale(0.96);
}

.copilot-send-btn--stop {
  background: color-mix(in srgb, var(--color-danger, #ef4444) 12%, transparent);
  color: var(--color-danger, #ef4444);
  cursor: pointer;
}

.copilot-send-btn--stop:hover {
  background: color-mix(in srgb, var(--color-danger, #ef4444) 18%, transparent);
}

.copilot-disclaimer {
  margin: 10px 4px 0;
  font-size: 11px;
  color: var(--color-text-tertiary);
  text-align: center;
}
</style>
