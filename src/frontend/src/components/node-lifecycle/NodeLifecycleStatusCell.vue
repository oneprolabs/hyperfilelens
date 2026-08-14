<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { LoaderCircle } from 'lucide-vue-next'
import type { ApiNode } from '../../types/node'

const props = defineProps<{
  node: ApiNode
  resolveDisplayStatus: (node: ApiNode) => {
    labelKey: string
    tagType: 'success' | 'warning' | 'danger' | 'info'
    tagClass?: string
    spinning?: boolean
  }
}>()

const { t, te } = useI18n()

const display = computed(() => props.resolveDisplayStatus(props.node))

const label = computed(() => {
  const key = display.value.labelKey
  return te(key) ? t(key) : key
})
</script>

<template>
  <div class="node-lifecycle-status">
    <el-tag
      :type="display.tagType"
      size="small"
      :class="['node-lifecycle-status__tag', display.tagClass]"
    >
      <LoaderCircle
        v-if="display.spinning"
        :size="12"
        class="node-lifecycle-status__icon"
        aria-hidden="true"
      />
      <span class="node-lifecycle-status__label">{{ label }}</span>
    </el-tag>
  </div>
</template>

<style scoped>
.node-lifecycle-status__tag :deep(.el-tag__content) {
  display: inline-flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.node-lifecycle-status__icon {
  flex-shrink: 0;
  animation: node-lifecycle-spin 0.8s linear infinite;
}


@keyframes node-lifecycle-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
