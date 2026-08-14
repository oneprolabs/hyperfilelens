<script setup lang="ts">
import { computed } from 'vue'
import { ElTooltip } from 'element-plus'
import { Info } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  content?: string
  placement?: InstanceType<typeof ElTooltip>['placement']
  ariaLabel?: string
  size?: number
  popperClass?: string
  triggerClass?: string
}>(), {
  content: '',
  placement: 'top',
  ariaLabel: 'More information',
  size: 14,
  popperClass: '',
  triggerClass: '',
})

const hasContent = computed(() => Boolean(props.content || props.popperClass))
</script>

<template>
  <ElTooltip
    :content="content"
    :placement="placement"
    :show-after="250"
    :popper-class="popperClass || undefined"
    :disabled="!hasContent && !$slots.content"
  >
    <template
      v-if="$slots.content"
      #content
    >
      <slot name="content" />
    </template>
    <button
      type="button"
      class="hfl-help-tip"
      :class="triggerClass"
      :aria-label="ariaLabel"
    >
      <Info
        :size="size"
        stroke-width="2.2"
        aria-hidden="true"
      />
    </button>
  </ElTooltip>
</template>

<style scoped>
.hfl-help-tip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: rgb(100 116 139);
  line-height: 1;
  vertical-align: middle;
  cursor: help;
}

.hfl-help-tip:hover,
.hfl-help-tip:focus-visible {
  color: var(--color-primary);
  background: rgb(59 130 246 / 0.10);
  outline: none;
}
</style>
