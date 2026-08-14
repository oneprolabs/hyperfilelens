<script setup lang="ts">
import PlatformOpsBackButton from './PlatformOpsBackButton.vue'

defineProps<{
  title: string
  loading?: boolean
}>()

defineEmits<{
  back: []
}>()
</script>

<template>
  <div
    v-loading="loading"
    class="platform-ops-detail"
  >
    <div class="platform-ops-detail__head">
      <PlatformOpsBackButton @click="$emit('back')" />
      <div class="platform-ops-detail__title-group">
        <h2>{{ title }}</h2>
        <slot name="status" />
      </div>
      <div
        v-if="$slots.actions"
        class="platform-ops-detail__actions"
      >
        <slot name="actions" />
      </div>
    </div>
    <div class="hfl-detail-sections platform-ops-detail__sections">
      <slot />
    </div>
  </div>
</template>
