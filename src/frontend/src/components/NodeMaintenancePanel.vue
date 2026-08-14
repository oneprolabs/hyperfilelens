<script setup lang="ts">
import { computed } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import NodeLifecycleWizard from './NodeLifecycleWizard.vue'
import { getEffectiveOrgKey } from '../composables/useAuth'
import { debouncedNodeStatus } from '../composables/useNodeConnectionDisplay'
import { copyTextToClipboard } from '../lib/clipboard'
import { formatNodeDate, nodeEnrollmentOs } from '../lib/nodeInventoryDisplay'
import type { ApiNode } from '../types/node'
import { ElMessage } from 'element-plus'

const props = withDefaults(
  defineProps<{
    node: ApiNode
    gatewayScope?: 'user' | 'platform'
    refreshing?: boolean
  }>(),
  {
    gatewayScope: 'user',
    refreshing: false,
  },
)

const emit = defineEmits<{
  refresh: []
}>()

const { t } = useI18n()
const offline = computed(() => debouncedNodeStatus(props.node) !== 'online')
const initialTab = offline.value ? 'service' : 'upgrade'
const initialServiceAction = offline.value ? 'restart' : 'status'
const orgKey = computed(() => (
  props.gatewayScope === 'platform' ? '__platform_lens__' : getEffectiveOrgKey()
))

async function copyCommand(command: string) {
  if (!command) return
  try {
    await copyTextToClipboard(command)
    ElMessage.success({ message: t('nodesDeploy.copied'), grouping: true })
  } catch {
    ElMessage.error({ message: t('nodesDeploy.copyFailed'), grouping: true })
  }
}
</script>

<template>
  <div class="node-maintenance-panel">
    <ElAlert
      v-if="offline"
      type="warning"
      :closable="false"
      show-icon
      class="node-maintenance-panel__offline"
    >
      <template #title>
        {{ t('nodeLifecycle.offlineMaintenanceTitle') }}
      </template>
      <div class="node-maintenance-panel__offline-body">
        <span>
          {{ node.last_seen_at
            ? t('nodeLifecycle.lastHeartbeatAt', { time: formatNodeDate(node.last_seen_at) })
            : t('nodeLifecycle.lastHeartbeatUnknown') }}
        </span>
        <ElButton
          link
          type="primary"
          :loading="refreshing"
          :aria-label="t('nodeLifecycle.refreshNodeStatus')"
          @click="emit('refresh')"
        >
          <RefreshCw
            v-if="!refreshing"
            :size="14"
            aria-hidden="true"
          />
          {{ t('nodeLifecycle.refreshNodeStatus') }}
        </ElButton>
      </div>
    </ElAlert>

    <NodeLifecycleWizard
      maintenance-only
      role-locked
      :node-id="node.id"
      :org-key="orgKey"
      :role="node.role"
      :os="nodeEnrollmentOs(node)"
      :gateway-scope="gatewayScope"
      :initial-tab="initialTab"
      :initial-service-action="initialServiceAction"
      @copy="copyCommand"
    />
  </div>
</template>

<style scoped>
.node-maintenance-panel {
  display: grid;
  gap: 16px;
}

.node-maintenance-panel__offline {
  align-items: flex-start;
}

.node-maintenance-panel__offline-body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}

@media (max-width: 640px) {
  .node-maintenance-panel__offline-body {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
