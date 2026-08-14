<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { LoaderCircle } from 'lucide-vue-next'
import { resolveGatewayDisplayStatus, type GatewayAiPhase } from '../../lib/gatewayDisplayStatus'
import type { ApiNode } from '../../types/node'

export type { GatewayAiPhase }

const props = defineProps<{
  node: ApiNode
  aiPhase: GatewayAiPhase
  resolveDisplayStatus: (node: ApiNode) => {
    labelKey: string
    tagType: 'success' | 'warning' | 'danger' | 'info'
    tagClass?: string
    spinning?: boolean
  }
}>()

const { t, te } = useI18n()

const display = computed(() =>
  resolveGatewayDisplayStatus(props.node, props.aiPhase, props.resolveDisplayStatus),
)

const label = computed(() => {
  const key = display.value.labelKey
  return te(key) ? t(key) : key
})
</script>

<template>
  <div class="dg-composite-status">
    <el-tag
      :type="display.tagType"
      size="small"
      :class="['dg-composite-status__tag', display.tagClass]"
    >
      <LoaderCircle
        v-if="display.spinning"
        :size="12"
        class="dg-composite-status__icon"
        aria-hidden="true"
      />
      <span class="dg-composite-status__label">{{ label }}</span>
    </el-tag>
  </div>
</template>

<style scoped>
.dg-composite-status__tag :deep(.el-tag__content) {
  display: inline-flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.dg-composite-status__icon {
  flex-shrink: 0;
  animation: dg-composite-status-spin 0.8s linear infinite;
}


@keyframes dg-composite-status-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
