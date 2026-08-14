<script setup lang="ts">
import { computed } from 'vue'
import { Database } from 'lucide-vue-next'
import { normalizeS3StoragePlatform, s3PlatformIconUrl } from '../lib/s3PlatformDisplay'

const props = withDefaults(defineProps<{
  platform?: string | null
  size?: number
  alt?: string
  iconClass?: string
  lucideClass?: string
}>(), {
  platform: null,
  size: 18,
  alt: '',
  iconClass: '',
  lucideClass: '',
})

const usesDatabaseIcon = computed(() => {
  const platform = normalizeS3StoragePlatform(props.platform)
  return platform === 'other' || platform === 'custom'
})
</script>

<template>
  <Database
    v-if="usesDatabaseIcon"
    :class="lucideClass"
    :size="size"
    stroke-width="2"
  />
  <img
    v-else
    :src="s3PlatformIconUrl(platform)"
    :alt="alt"
    :class="iconClass"
    :width="size"
    :height="size"
    loading="lazy"
    decoding="async"
  >
</template>
