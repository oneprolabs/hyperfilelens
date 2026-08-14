<script setup lang="ts">
import { computed } from 'vue'
import { aiProviderColor, aiProviderLetter } from '../../lib/aiProviderDisplay'

const props = withDefaults(
  defineProps<{
    provider: string
    size?: 'sm' | 'md' | 'lg'
  }>(),
  { size: 'md' },
)

const sizePx = computed(() => {
  if (props.size === 'sm') return 20
  if (props.size === 'lg') return 32
  return 24
})

const style = computed(() => ({
  width: `${sizePx.value}px`,
  height: `${sizePx.value}px`,
  backgroundColor: aiProviderColor(props.provider),
  fontSize: props.size === 'lg' ? '13px' : props.size === 'sm' ? '10px' : '11px',
}))
</script>

<template>
  <span
    class="ai-provider-icon"
    :style="style"
    aria-hidden="true"
  >
    {{ aiProviderLetter(provider) }}
  </span>
</template>

<style scoped>
.ai-provider-icon {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  color: #fff;
  font-weight: 700;
  line-height: 1;
}
</style>
