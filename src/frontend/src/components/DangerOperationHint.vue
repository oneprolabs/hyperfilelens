<script setup lang="ts">
import { Info } from 'lucide-vue-next'
defineProps<{ text: string }>()
</script>

<template>
  <div class="danger-operation-hint">
    <Info
      :size="15"
      aria-hidden="true"
    />
    <p>{{ text }}</p>
  </div>
</template>

<style scoped>
.danger-operation-hint {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 12px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 20px;
}
.danger-operation-hint > svg {
  flex: 0 0 15px;
  margin-top: 2px;
}
.danger-operation-hint > p {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
