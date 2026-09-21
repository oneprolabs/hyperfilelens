<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Check, ChevronDown } from 'lucide-vue-next'

const TIERS = ['flash', 'fast', 'balanced', 'deep', 'max'] as const

const props = defineProps<{
  modelValue?: string
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const { t } = useI18n()
const rootRef = ref<HTMLElement | null>(null)
const open = ref(false)

const tiers = computed(() =>
  TIERS.map((value) => ({
    value,
    label: t(`insight.copilot.reasoning${labelKey(value)}`),
    description: t(`insight.copilot.reasoning${labelKey(value)}Description`),
  })),
)

const selectedValue = computed(() =>
  TIERS.includes(props.modelValue as typeof TIERS[number]) ? props.modelValue : 'balanced',
)

const selectedTier = computed(() =>
  tiers.value.find((tier) => tier.value === selectedValue.value) || tiers.value[2],
)

function labelKey(value: string) {
  if (value === 'flash') return 'Flash'
  if (value === 'fast') return 'Fast'
  if (value === 'deep') return 'Deep'
  if (value === 'max') return 'Max'
  return 'Balanced'
}

function toggle() {
  if (props.disabled) return
  open.value = !open.value
}

function choose(value: string) {
  emit('update:modelValue', value)
  open.value = false
}

function onDocumentPointerDown(event: PointerEvent) {
  if (!rootRef.value?.contains(event.target as Node)) open.value = false
}

function onDocumentKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false
}

watch(open, (isOpen) => {
  if (isOpen) {
    document.addEventListener('pointerdown', onDocumentPointerDown)
    document.addEventListener('keydown', onDocumentKeydown)
    return
  }
  document.removeEventListener('pointerdown', onDocumentPointerDown)
  document.removeEventListener('keydown', onDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown)
  document.removeEventListener('keydown', onDocumentKeydown)
})
</script>

<template>
  <div
    ref="rootRef"
    class="reasoning-depth"
  >
    <button
      type="button"
      class="reasoning-depth-trigger"
      role="combobox"
      aria-haspopup="listbox"
      :aria-expanded="open"
      :aria-label="`${t('insight.copilot.reasoningDepth')}: ${selectedTier.label}`"
      :disabled="disabled"
      @click="toggle"
    >
      <span class="reasoning-depth-value">{{ selectedTier.label }}</span>
      <ChevronDown
        class="reasoning-depth-chevron"
        :class="{ 'is-open': open }"
        :size="16"
        aria-hidden="true"
      />
    </button>

    <ul
      v-if="open"
      class="reasoning-depth-menu"
      role="listbox"
      :aria-label="t('insight.copilot.reasoningDepth')"
    >
      <li
        v-for="tier in tiers"
        :key="tier.value"
        role="presentation"
      >
        <button
          type="button"
          class="reasoning-depth-option"
          role="option"
          :aria-selected="tier.value === selectedValue"
          @click="choose(tier.value)"
        >
          <span class="reasoning-depth-option-copy">
            <span class="reasoning-depth-option-label">{{ tier.label }}</span>
            <span class="reasoning-depth-option-description">{{ tier.description }}</span>
          </span>
          <Check
            v-if="tier.value === selectedValue"
            :size="16"
            aria-hidden="true"
          />
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.reasoning-depth {
  position: relative;
  flex: 0 1 auto;
  min-width: 0;
  max-width: 100%;
}

.reasoning-depth-trigger {
  display: inline-flex;
  max-width: 100%;
  min-height: 40px;
  align-items: center;
  gap: 8px;
  padding: 8px 32px 8px 12px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  color: #6b7280;
  font-size: 14px;
  line-height: 20px;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.reasoning-depth-trigger:hover:not(:disabled),
.reasoning-depth-trigger[aria-expanded='true'] {
  background: #f3f4f6;
  color: #171512;
}

.reasoning-depth-trigger:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--color-primary) 40%, transparent);
  outline-offset: 2px;
}

.reasoning-depth-trigger:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.reasoning-depth-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reasoning-depth-chevron {
  position: absolute;
  right: 12px;
  flex: 0 0 auto;
  transition: transform 0.15s ease;
}

.reasoning-depth-chevron.is-open {
  transform: rotate(180deg);
}

.reasoning-depth-menu {
  position: absolute;
  z-index: 30;
  right: 0;
  bottom: calc(100% + 8px);
  width: max-content;
  max-width: none;
  margin: 0;
  padding: 6px;
  overflow: visible;
  list-style: none;
  border: 1px solid #e5e7eb;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 12px 32px rgb(15 23 42 / 12%);
}

.reasoning-depth-option {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 16px;
  padding: 8px 12px;
  border: none;
  border-radius: 12px;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.reasoning-depth-option:hover,
.reasoning-depth-option[aria-selected='true'] {
  background: #f3f4f6;
}

.reasoning-depth-option-copy {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
}

.reasoning-depth-option-label {
  color: #171512;
  font-size: 14px;
  font-weight: 500;
  line-height: 20px;
}

.reasoning-depth-option-label,
.reasoning-depth-option-description {
  white-space: nowrap;
}

.reasoning-depth-option-description {
  color: #6b7280;
  font-size: 12px;
  font-weight: 400;
  line-height: 20px;
}

.reasoning-depth-option svg {
  flex: 0 0 auto;
  color: #171512;
}
</style>
