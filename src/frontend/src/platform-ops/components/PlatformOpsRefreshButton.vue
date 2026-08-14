<script setup lang="ts">
import { RefreshCw } from 'lucide-vue-next'

defineProps<{
  loading?: boolean
  disabled?: boolean
}>()

defineEmits<{
  click: []
}>()
</script>

<template>
  <el-button
    class="hfl-refresh-button"
    :title="$t('common.refresh')"
    :aria-label="$t('common.refresh')"
    :disabled="disabled || loading"
    @click="$emit('click')"
  >
    <RefreshCw
      :size="16"
      :class="{ 'is-spinning': loading }"
    />
  </el-button>
</template>
