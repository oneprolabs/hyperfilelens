<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Copy } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import { copyTextToClipboard } from '../../lib/clipboard'
import { DETAIL_EMPTY, formatNodeDate, isDetailEmpty } from '../../lib/nodeInventoryDisplay'
import type { GatewayAiStatus } from '../../lib/lensApi'

const props = defineProps<{
  gatewayAi: GatewayAiStatus | null
}>()

const { t } = useI18n()

const aiPhase = computed(() => {
  if (!props.gatewayAi?.ai_enabled) return 'not_provisioned' as const
  const status = props.gatewayAi.sidecar_status
  if (status === 'online') return 'online' as const
  if (status === 'offline') return 'offline' as const
  if (status === 'error') return 'error' as const
  return 'pending_install' as const
})

const slStatusTagType = computed((): 'success' | 'info' | 'warning' | 'danger' => {
  const raw = (props.gatewayAi?.sl_status || props.gatewayAi?.sidecar_status || '').toLowerCase()
  if (raw === 'online') return 'success'
  if (!props.gatewayAi?.ai_enabled) return 'info'
  if (raw === 'offline' || raw === 'error') return 'danger'
  return 'info'
})

const slStatusLabel = computed(() => {
  const raw = (props.gatewayAi?.sl_status || '').trim().toLowerCase()
  if (raw === 'online') return t('insight.dataGateway.aiPhase.online')
  if (raw === 'offline') return t('insight.dataGateway.aiPhase.offline')
  if (raw === 'error') return t('insight.dataGateway.aiPhase.error')
  if (aiPhase.value === 'not_provisioned') return t('insight.dataGateway.aiPhase.not_provisioned')
  if (aiPhase.value === 'pending_install') return t('insight.dataGateway.aiPhase.pending_install')
  return raw ? raw : DETAIL_EMPTY
})

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
          class="hfl-detail-row__value hfl-detail-row__value--editable hfl-detail-row__value--mono"
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
          class="hfl-detail-row__value hfl-detail-row__value--editable hfl-detail-row__value--mono"
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
          class="hfl-detail-row__value hfl-detail-row__value--mono"
          :class="detailValueClass(textOrEmpty(gatewayAi?.sl_workspace_path || gatewayAi?.workspace_root), true)"
        >
          {{ textOrEmpty(gatewayAi?.sl_workspace_path || gatewayAi?.workspace_root) }}
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
    </div>
    <p
      v-if="aiPhase === 'not_provisioned'"
      class="dg-lens-basic__hint"
    >
      {{ t('insight.dataGateway.aiNotProvisionedHint') }}
    </p>
  </section>
</template>

<style scoped>
.dg-lens-basic__hint {
  margin: 12px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-secondary);
}
</style>
