<script setup lang="ts">
import { ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronDown } from 'lucide-vue-next'

defineProps<{ enabled: boolean; enabledLabel: string }>()
const expanded = defineModel<boolean>('expanded', { default: false })
const { t } = useI18n()
const contentId = useId()
const toggle = ref<HTMLButtonElement>()
const content = ref<HTMLDivElement>()
watch(expanded, (open) => {
  if (!open && content.value?.contains(document.activeElement)) toggle.value?.focus()
})
</script>

<template>
  <section class="danger-advanced-options">
    <button
      ref="toggle"
      type="button"
      class="danger-advanced-options__toggle"
      :aria-expanded="expanded"
      :aria-controls="contentId"
      @click="expanded = !expanded"
    >
      <ChevronDown
        :size="16"
        :class="{ 'is-collapsed': !expanded }"
        aria-hidden="true"
      />
      <span>{{ t('common.dangerAdvancedOptions') }}</span>
      <span
        v-if="enabled"
        class="danger-advanced-options__enabled"
        role="status"
      >{{ enabledLabel }}</span>
    </button>
    <div
      v-show="expanded"
      :id="contentId"
      ref="content"
      class="danger-advanced-options__content"
    >
      <slot />
    </div>
  </section>
</template>

<style scoped>
.danger-advanced-options {
  margin-top: 16px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
}
.danger-advanced-options__toggle {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  width: 100%;
  padding: 12px;
  color: var(--el-text-color-primary);
  text-align: left;
  cursor: pointer;
}
.danger-advanced-options__toggle:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}
.is-collapsed { transform: rotate(-90deg); }
.danger-advanced-options__enabled {
  margin-left: auto;
  color: var(--el-color-warning);
  font-weight: 600;
}
.danger-advanced-options__content { padding: 0 12px 12px; }
</style>
