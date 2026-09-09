<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { aiProviderColor, aiProviderLetter } from '../../lib/aiProviderDisplay'
import { aiProviderIconUrl } from '../../lib/aiProviderIcons'

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

const imageFailed = ref(false)
const iconUrl = computed(() => imageFailed.value ? '' : aiProviderIconUrl(props.provider))

const style = computed(() => ({
  width: `${sizePx.value}px`,
  height: `${sizePx.value}px`,
  backgroundColor: iconUrl.value ? 'transparent' : aiProviderColor(props.provider),
  fontSize: props.size === 'lg' ? '13px' : props.size === 'sm' ? '10px' : '11px',
}))

watch(() => props.provider, () => {
  imageFailed.value = false
})
</script>

<template>
  <span
    class="ai-provider-icon"
    :style="style"
    aria-hidden="true"
  >
    <img
      v-if="iconUrl"
      :src="iconUrl"
      alt=""
      class="ai-provider-icon__image"
      loading="lazy"
      decoding="async"
      @error="imageFailed = true"
    >
    <template v-else>
      {{ aiProviderLetter(provider) }}
    </template>
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

.ai-provider-icon__image {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
</style>
