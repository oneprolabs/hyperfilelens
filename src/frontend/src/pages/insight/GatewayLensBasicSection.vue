<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Copy } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import HflCapacityCell from '../../components/HflCapacityCell.vue'
import { copyTextToClipboard } from '../../lib/clipboard'
import { DETAIL_EMPTY, formatNodeBytes, formatNodeDate, isDetailEmpty } from '../../lib/nodeInventoryDisplay'
import type { GatewayAiStatus } from '../../lib/lensApi'
import type { PlatformGatewayCapacity } from '../../platform-ops/lib/platformOpsApi'

const props = defineProps<{
  gatewayAi: GatewayAiStatus | null
  workspaceCapacity?: PlatformGatewayCapacity | null
}>()

const { t } = useI18n()

const aiPhase = computed(() => {
  if (!props.gatewayAi) return 'not_provisioned' as const
  if (!props.gatewayAi.ai_enabled && !props.gatewayAi.sl_lensnode_uuid) {
    return 'not_provisioned' as const
  }
  const status = (props.gatewayAi.sl_status || props.gatewayAi.sidecar_status || '').toLowerCase()
  if (status === 'online') return 'online' as const
  if (status === 'offline') return 'offline' as const
  if (status === 'error' || status === 'not_deployed') return 'error' as const
  if (!props.gatewayAi.ai_enabled) return 'not_provisioned' as const
  return 'pending_install' as const
})

const slStatusTagType = computed((): 'success' | 'info' | 'warning' | 'danger' => {
  switch (aiPhase.value) {
    case 'online':
      return 'success'
    case 'offline':
    case 'error':
      return 'danger'
    case 'pending_install':
      return 'warning'
    default:
      return 'info'
  }
})

const slStatusLabel = computed(() => t(`insight.dataGateway.aiPhase.${aiPhase.value}`))

function textOrEmpty(value: string | null | undefined) {
  const trimmed = value?.trim()
  return trimmed || DETAIL_EMPTY
}

function formatSlDate(value: string | null | undefined) {
  if (!value?.trim()) return DETAIL_EMPTY
  return formatNodeDate(value)
}

const tasksLabel = computed(() => {
  const tasks = props.gatewayAi?.sl_tasks ?? []
  if (tasks.length === 0) return DETAIL_EMPTY
  return tasks.map((task) => task.title?.trim() || task.name?.trim()).filter(Boolean).join(', ')
})

const workspaceText = computed(() =>
  textOrEmpty(props.gatewayAi?.sl_workspace_path || props.gatewayAi?.workspace_root),
)

const showWorkspaceQuota = computed(() => props.workspaceCapacity != null)

const workspaceQuotaTotalBytes = computed(() => {
  const cap = props.workspaceCapacity
  if (!cap || cap.unlimited || cap.capacity_bytes < 0) return 0
  return Number(cap.capacity_bytes)
})

const workspaceQuotaKnownTotal = computed(() => {
  const cap = props.workspaceCapacity
  return Boolean(cap && !cap.unlimited && cap.capacity_bytes >= 0)
})

const workspaceQuotaUnlimitedLabel = computed(() => {
  const cap = props.workspaceCapacity
  if (!cap) return undefined
  if (cap.unlimited || cap.capacity_bytes < 0) {
    return t('platformOps.engineGateway.capacityUnlimited')
  }
  return undefined
})

async function copyText(value: string) {
  if (isDetailEmpty(value)) return
  try {
    await copyTextToClipboard(value)
    ElMessage.success({ message: t('common.copied'), grouping: true })
  } catch {
    ElMessage.error({ message: t('protection.sourceResources.copyFailed'), grouping: true })
  }
}

function detailValueClass(text: string, monoWhenPresent = false) {
  return {
    'hfl-detail-row__empty': isDetailEmpty(text),
    'hfl-detail-row__value--mono': monoWhenPresent && !isDetailEmpty(text),
  }
}
</script>

<template>
  <section class="hfl-detail-section">
    <h4 class="hfl-detail-section__title">
      {{ t('insight.dataGateway.detailSectionAiEngine') }}
    </h4>
    <div class="hfl-detail-grid">
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colStatus') }}</span>
        <span class="hfl-detail-row__value">
          <el-tag
            :type="slStatusTagType"
            size="small"
          >
            {{ slStatusLabel }}
          </el-tag>
        </span>
      </div>
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('insight.dataGateway.fieldSlName') }}</span>
        <span
          class="hfl-detail-row__value hfl-detail-row__value--editable"
          :class="detailValueClass(textOrEmpty(gatewayAi?.sl_name), true)"
        >
          <span class="hfl-detail-row__text">{{ textOrEmpty(gatewayAi?.sl_name) }}</span>
          <ElButton
            v-if="!isDetailEmpty(textOrEmpty(gatewayAi?.sl_name))"
            text
            circle
            size="small"
            class="hfl-detail-row__edit"
            :title="t('common.copy')"
            @click="copyText(textOrEmpty(gatewayAi?.sl_name))"
          >
            <Copy :size="13" />
          </ElButton>
        </span>
      </div>
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('insight.dataGateway.fieldSlNodeId') }}</span>
        <span
          class="hfl-detail-row__value hfl-detail-row__value--editable"
          :class="detailValueClass(textOrEmpty(gatewayAi?.sl_lensnode_uuid), true)"
        >
          <span class="hfl-detail-row__text">{{ textOrEmpty(gatewayAi?.sl_lensnode_uuid) }}</span>
          <ElButton
            v-if="!isDetailEmpty(textOrEmpty(gatewayAi?.sl_lensnode_uuid))"
            text
            circle
            size="small"
            class="hfl-detail-row__edit"
            :title="t('common.copy')"
            @click="copyText(textOrEmpty(gatewayAi?.sl_lensnode_uuid))"
          >
            <Copy :size="13" />
          </ElButton>
        </span>
      </div>
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('insight.dataGateway.fieldSlWorkspace') }}</span>
        <span
          class="hfl-detail-row__value"
          :class="detailValueClass(workspaceText, true)"
        >
          {{ workspaceText }}
        </span>
      </div>
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('insight.dataGateway.fieldSlLastHeartbeat') }}</span>
        <span
          class="hfl-detail-row__value"
          :class="{ 'hfl-detail-row__empty': formatSlDate(gatewayAi?.sl_last_heartbeat_at) === DETAIL_EMPTY }"
        >
          {{ formatSlDate(gatewayAi?.sl_last_heartbeat_at) }}
        </span>
      </div>
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('insight.dataGateway.fieldSlEngineVersion') }}</span>
        <span
          class="hfl-detail-row__value"
          :class="detailValueClass(textOrEmpty(gatewayAi?.sl_agent_version))"
        >
          {{ textOrEmpty(gatewayAi?.sl_agent_version) }}
        </span>
      </div>
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('insight.dataGateway.fieldSlRegistered') }}</span>
        <span
          class="hfl-detail-row__value"
          :class="{ 'hfl-detail-row__empty': formatSlDate(gatewayAi?.sl_registered_at) === DETAIL_EMPTY }"
        >
          {{ formatSlDate(gatewayAi?.sl_registered_at) }}
        </span>
      </div>
      <div class="hfl-detail-row">
        <span class="hfl-detail-row__label">{{ t('insight.dataGateway.fieldSlTasks') }}</span>
        <span
          class="hfl-detail-row__value"
          :class="{ 'hfl-detail-row__empty': tasksLabel === DETAIL_EMPTY }"
        >
          {{ tasksLabel }}
        </span>
      </div>
      <div
        v-if="showWorkspaceQuota"
        class="hfl-detail-row hfl-detail-row--full"
      >
        <span class="hfl-detail-row__label">{{ t('platformOps.engineGateway.colCapacity') }}</span>
        <span
          class="hfl-detail-row__value"
          :class="{ 'hfl-detail-row__value--stacked': workspaceQuotaKnownTotal }"
        >
          <HflCapacityCell
            :used-bytes="workspaceCapacity!.used_bytes"
            :total-bytes="workspaceQuotaTotalBytes"
            :known-total="workspaceQuotaKnownTotal"
            variant="compact"
            :format-bytes="formatNodeBytes"
            :unlimited-total-label="workspaceQuotaUnlimitedLabel"
          />
          <span
            v-if="workspaceCapacity?.used_incomplete"
            class="dg-lens-basic__quota-incomplete"
          >
            {{ t('platformOps.engineGateway.capacityUsedIncomplete') }}
          </span>
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.dg-lens-basic__quota-incomplete {
  display: block;
  margin-top: 2px;
  color: var(--el-color-warning);
  font-size: 11px;
  line-height: 1.3;
}
</style>
