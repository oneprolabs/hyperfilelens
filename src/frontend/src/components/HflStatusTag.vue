<script setup lang="ts">
import { computed } from 'vue'
import { statusTagAttrs, type StatusTagTone } from '../lib/statusTag'

const props = withDefaults(defineProps<{
  label: string
  tone: StatusTagTone
  size?: 'large' | 'default' | 'small'
}>(), {
  size: 'small',
})

const tagAttrs = computed(() => statusTagAttrs(props.tone))
const tagClass = computed(() => tagAttrs.value.class || '')
</script>

<template>
  <span class="hfl-status-tag">
    <ElTag
      :type="tagAttrs.type"
      :class="tagClass"
      :size="size"
      effect="light"
    >
      {{ label }}
    </ElTag>
  </span>
</template>
