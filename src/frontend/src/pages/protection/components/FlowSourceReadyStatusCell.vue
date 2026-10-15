<script setup lang="ts">
import { LoaderCircle } from 'lucide-vue-next'
import StatusDetailAction from '../../../components/StatusDetailAction.vue'

const props = defineProps<{
  label: string
  tag: 'success' | 'warning' | 'danger' | 'info' | 'neutral'
  spinning?: boolean
  neutralAsDanger?: boolean
  clickable?: boolean
  loading?: boolean
}>()

const emit = defineEmits<{
  (e: 'click', event: MouseEvent): void
}>()


function onActivate(event: MouseEvent) {
  if (!props.clickable || props.loading) return
  emit('click', event)
}
</script>

<template>
  <StatusDetailAction
    class="flow-source-status-cell hfl-table-no-tooltip"
    :label="label"
    :clickable="clickable"
    :loading="loading"
    :failure="tag === 'danger' || tag === 'neutral' && neutralAsDanger"
    @click="onActivate"
  >
    <el-tag
      size="small"
      :type="tag === 'neutral' && neutralAsDanger ? 'danger' : tag === 'neutral' ? undefined : tag"
      class="flow-source-status-tag"
      :class="{ 'hfl-tag--neutral': tag === 'neutral' && !neutralAsDanger }"
    >
      <LoaderCircle
        v-if="spinning"
        :size="12"
        class="flow-source-status-tag__icon"
        aria-hidden="true"
      />
      <span class="flow-source-status-tag__label">{{ label }}</span>
    </el-tag>
  </StatusDetailAction>
</template>

<style scoped>
.flow-source-status-cell {
  display: inline-flex;
  justify-content: center;
  max-width: 100%;
  overflow: visible;
}

.flow-source-status-tag {
  max-width: none;
}

.flow-source-status-tag :deep(.el-tag__content) {
  display: inline-flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.flow-source-status-tag__icon {
  flex-shrink: 0;
  animation: flow-source-status-spin 0.8s linear infinite;
}

@keyframes flow-source-status-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
