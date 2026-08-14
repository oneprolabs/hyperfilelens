<script setup lang="ts">
import { ArrowLeft } from 'lucide-vue-next'

defineEmits<{
  click: []
}>()
</script>

<template>
  <button
    type="button"
    class="fullscreen-form-header__back platform-ops-detail__back"
    :aria-label="$t('common.back')"
    :title="$t('common.back')"
    @click="$emit('click')"
  >
    <ArrowLeft
      class="fullscreen-form-header__back-icon"
      :size="18"
    />
  </button>
</template>
