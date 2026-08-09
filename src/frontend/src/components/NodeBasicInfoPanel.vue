<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Check, Copy, Pencil, X } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import { updateNode } from '../lib/nodeApi'
import { copyTextToClipboard } from '../lib/clipboard'
import {
  DETAIL_EMPTY,
  formatNodeBytes,
  formatNodeDate,
  formatNodeOsName,
  isDetailEmpty,
  nodeArch,
  nodeCpuCores,
  nodeDiskUsageParts,
  nodeDiskUsagePercent,
  nodeExternalId,
  nodeHostname,
  nodeInstallDirs,
  nodeMacAddress,
  nodeMemoryTotalBytes,
  nodeDiskCount,
  nodeUptimeSeconds,
} from '../lib/nodeInventoryDisplay'
import type { SourceResource } from '../lib/sourceApi'
import type { ApiNode } from '../types/node'
import HflCapacityCell from './HflCapacityCell.vue'
import NodeLifecycleStatusCell from './node-lifecycle/NodeLifecycleStatusCell.vue'

type NodeDisplayStatus = {
  labelKey: string
  tagType: 'success' | 'warning' | 'danger' | 'info'
  tagClass?: string
  spinning?: boolean
}

const props = defineProps<{
  node: ApiNode
  sourceTypeLabel: string
  source?: SourceResource | null
  useUnifiedCapacity?: boolean
  resolveDisplayStatus?: (node: ApiNode) => NodeDisplayStatus
  showRepositoryServerAddress?: boolean
}>()

const emit = defineEmits<{
  'node-updated': [ApiNode]
}>()

const { t } = useI18n()

const editingName = ref(false)
const nameDraft = ref('')
const savingName = ref(false)

const externalId = computed(() => nodeExternalId(props.node))
const hostname = computed(() => nodeHostname(props.node))
const installDirs = computed(() => nodeInstallDirs(props.node))
const hostIp = computed(() => props.node.ip_address?.trim() || DETAIL_EMPTY)
const repositoryServerAddress = computed(() =>
  props.node.effective_repository_server_address?.trim() || DETAIL_EMPTY,
)
const repositoryServerAddressSource = computed(() =>
  props.node.repository_server_address_source === 'proxy_override'
    ? t('nodesPage.repositoryServerAddressCustom')
    : t('nodesPage.repositoryServerAddressAuto'),
)
const osName = computed(() => formatNodeOsName(props.node))
const arch = computed(() => nodeArch(props.node) || DETAIL_EMPTY)
const cpuText = computed(() => {
  const cores = nodeCpuCores(props.node)
  return cores != null ? t('protection.sourceResources.cpuCoresValue', { n: cores }) : DETAIL_EMPTY
})
const memoryText = computed(() => {
  const total = nodeMemoryTotalBytes(props.node)
  return total != null ? formatNodeBytes(total) : DETAIL_EMPTY
})
const diskCountText = computed(() => {
  const count = nodeDiskCount(props.node)
  return count != null ? t('protection.sourceResources.diskCountValue', { n: count }) : DETAIL_EMPTY
})
const macText = computed(() => nodeMacAddress(props.node) || DETAIL_EMPTY)

const versionText = computed(() => {
  const fromSource = props.source?.config?.agent_version
  if (typeof fromSource === 'string' && fromSource.trim()) return fromSource.trim()
  return props.node.version?.trim() || DETAIL_EMPTY
})

const availabilityLabel = computed(() =>
  props.node.availability === 'online'
    ? t('protection.sourceResources.nodeStatusOnline')
    : t('protection.sourceResources.nodeStatusOffline'),
)
const availabilityTagType = computed(() =>
  props.node.availability === 'online' ? 'success' : 'danger',
)

const capacityParts = computed(() => {
  if (props.source?.total_size) {
    const used = Number(props.source.used_size || 0)
    const total = Number(props.source.total_size || 0)
    const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0
    return { used, total, pct }
  }
  const { used, total } = nodeDiskUsageParts(props.node)
  if (!total) return { used: 0, total: 0, pct: 0 }
  const pct = nodeDiskUsagePercent(props.node)
  return { used, total, pct }
})

const uptimeLabel = computed(() => {
  if (props.node.availability !== 'online') return ''
  const seconds = nodeUptimeSeconds(props.node, true)
  if (seconds == null) return ''
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  let duration = ''
  if (days > 0) duration = t('protection.sourceResources.uptimeDay', { n: days })
  else if (hours > 0) duration = t('protection.sourceResources.uptimeHour', { n: hours })
  else duration = t('protection.sourceResources.uptimeMinute', { n: Math.max(1, minutes) })
  return t('protection.sourceResources.statusRunningFor', { duration })
})

function resolveNodeDisplayStatus(node: ApiNode): NodeDisplayStatus {
  if (props.resolveDisplayStatus) return props.resolveDisplayStatus(node)
  if (node.status === 'reconnecting') {
    return {
      labelKey: 'protection.sourceResources.nodeStatusReconnecting',
      tagType: 'info',
      spinning: true,
    }
  }
  // Status is the lifecycle state. Connectivity is rendered separately from availability.
  const status = node.status === 'online' || node.status === 'offline' ? 'active' : node.status
  return {
    labelKey: `nodeLifecycle.state.${status}`,
    tagType: status === 'active'
      ? 'success'
      : status === 'failed' || status === 'upgrade_failed' || status === 'deregistration_failed'
        ? 'danger'
        : 'info',
  }
}

async function copyText(value: string) {
  if (!value || isDetailEmpty(value)) return
  try {
    await copyTextToClipboard(value)
    ElMessage.success({ message: t('common.copied'), grouping: true })
  } catch {
    ElMessage.error({ message: t('protection.sourceResources.copyFailed'), grouping: true })
  }
}

function beginNameEdit() {
  nameDraft.value = props.node.name
  editingName.value = true
}

const hasNameChanges = computed(() => {
  if (!editingName.value) return false
  const name = nameDraft.value.trim()
  return name !== '' && name !== props.node.name
})

function discardNameEdit() {
  editingName.value = false
  nameDraft.value = ''
}

async function saveName(): Promise<boolean> {
  const name = nameDraft.value.trim()
  if (!name) {
    ElMessage.warning({ message: t('protection.sourceResources.renamePlaceholder'), grouping: true })
    return false
  }
  if (name === props.node.name) {
    discardNameEdit()
    return false
  }
  savingName.value = true
  try {
    const updated = await updateNode(props.node.id, { name })
    emit('node-updated', updated)
    ElMessage.success({ message: t('protection.sourceResources.renameSuccess'), grouping: true })
    discardNameEdit()
    return true
  } catch {
    ElMessage.error({ message: t('protection.sourceResources.renameFailed'), grouping: true })
    return false
  } finally {
    savingName.value = false
  }
}

defineExpose({ hasNameChanges, saveName, discardNameEdit, beginNameEdit })

function detailValueClass(text: string, monoWhenPresent = false) {
  return {
    'hfl-detail-row__empty': isDetailEmpty(text),
    'hfl-detail-row__value--mono': monoWhenPresent && !isDetailEmpty(text),
  }
}
</script>

<template>
  <div class="hfl-detail-sections">
    <section class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">{{ t('protection.sourceResources.detailSectionIdentity') }}</h4>
      <div class="hfl-detail-grid">
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.fieldSourceName') }}</span>
          <span class="hfl-detail-row__value hfl-detail-row__value--editable">
            <template v-if="editingName">
              <ElInput
                v-model="nameDraft"
                size="small"
                class="hfl-detail-inline-edit__input"
                :disabled="savingName"
                :placeholder="t('protection.sourceResources.renamePlaceholder')"
                @keyup.enter="saveName"
              />
              <span class="hfl-detail-inline-edit__actions">
                <ElButton text circle size="small" :title="t('common.save')" :disabled="savingName" @click="saveName"><Check :size="14" /></ElButton>
                <ElButton text circle size="small" :title="t('common.cancel')" :disabled="savingName" @click="discardNameEdit"><X :size="14" /></ElButton>
              </span>
            </template>
            <template v-else>
              <span class="hfl-detail-row__text">{{ node.name }}</span>
              <ElButton text circle size="small" class="hfl-detail-row__edit" :title="t('protection.sourceResources.editBtn')" @click="beginNameEdit">
                <Pencil :size="13" />
              </ElButton>
            </template>
          </span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.fieldSourceType') }}</span>
          <span class="hfl-detail-row__value">{{ sourceTypeLabel }}</span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.fieldHostname') }}</span>
          <span class="hfl-detail-row__value">{{ hostname }}</span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.fieldSourceId') }}</span>
          <span class="hfl-detail-row__value hfl-detail-row__value--editable hfl-detail-row__value--mono">
            <span class="hfl-detail-row__text">{{ externalId }}</span>
            <ElButton text circle size="small" class="hfl-detail-row__edit" :title="t('common.copy')" @click="copyText(externalId)">
              <Copy :size="13" />
            </ElButton>
          </span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colHostIp') }}</span>
          <span class="hfl-detail-row__value hfl-detail-row__value--editable hfl-detail-row__value--mono">
            <span class="hfl-detail-row__text">{{ hostIp }}</span>
            <ElButton
              v-if="!isDetailEmpty(hostIp)"
              text
              circle
              size="small"
              class="hfl-detail-row__edit"
              :title="t('common.copy')"
              @click="copyText(hostIp)"
            >
              <Copy :size="13" />
            </ElButton>
          </span>
        </div>
        <div v-if="showRepositoryServerAddress" class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('nodesPage.repositoryServerAddress') }}</span>
          <span class="hfl-detail-row__value hfl-detail-row__value--editable hfl-detail-row__value--mono">
            <span class="hfl-detail-row__text">{{ repositoryServerAddress }}</span>
            <ElTag
              v-if="!isDetailEmpty(repositoryServerAddress)"
              size="small"
              :type="node.repository_server_address_source === 'proxy_override' ? 'warning' : 'info'"
            >
              {{ repositoryServerAddressSource }}
            </ElTag>
          </span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.fieldInstallPath') }}</span>
          <span class="hfl-detail-row__value hfl-detail-row__value--stacked hfl-detail-row__value--mono hfl-detail-install-path__value">
            <span class="hfl-detail-row__text">{{ installDirs.installDir }}</span>
            <span class="hfl-detail-row__text">{{ installDirs.dataDir }}</span>
          </span>
        </div>
      </div>
    </section>

    <section class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">{{ t('protection.sourceResources.detailSectionSpecs') }}</h4>
      <div class="hfl-detail-grid">
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colOperatingSystem') }}</span>
          <span class="hfl-detail-row__value">{{ osName }}</span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.fieldArch') }}</span>
          <span class="hfl-detail-row__value" :class="detailValueClass(arch, true)">{{ arch }}</span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colCpu') }}</span>
          <span class="hfl-detail-row__value" :class="detailValueClass(cpuText)">{{ cpuText }}</span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colMemory') }}</span>
          <span class="hfl-detail-row__value" :class="detailValueClass(memoryText)">{{ memoryText }}</span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colDiskCount') }}</span>
          <span class="hfl-detail-row__value" :class="detailValueClass(diskCountText)">{{ diskCountText }}</span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.fieldMacAddress') }}</span>
          <span class="hfl-detail-row__value" :class="detailValueClass(macText, true)">{{ macText }}</span>
        </div>
        <div class="hfl-detail-row hfl-detail-row--full">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colCapacity') }}</span>
          <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__value--stacked': capacityParts.total > 0 }">
            <HflCapacityCell
              v-if="useUnifiedCapacity"
              :used-bytes="capacityParts.used"
              :total-bytes="capacityParts.total"
              variant="compact"
              :format-bytes="formatNodeBytes"
              :empty-label="DETAIL_EMPTY"
            />
            <template v-else>
              <span v-if="capacityParts.total > 0" class="hfl-detail-row__text repo-usage-cell__numbers">
                <span class="repo-usage-cell__used">{{ formatNodeBytes(capacityParts.used) }}</span>
                <span class="repo-usage-cell__capacity">/ {{ formatNodeBytes(capacityParts.total) }}</span>
                <span class="repo-usage-cell__percent">{{ capacityParts.pct }}%</span>
              </span>
              <span v-else class="hfl-detail-row__text hfl-detail-row__empty">{{ DETAIL_EMPTY }}</span>
              <div v-if="capacityParts.total > 0" class="hfl-detail-capacity-bar">
                <div class="hfl-detail-capacity-bar__fill" :style="{ width: `${capacityParts.pct}%` }" />
              </div>
            </template>
          </span>
        </div>
      </div>
    </section>

    <slot name="before-runtime" />

    <section class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">{{ t('protection.sourceResources.detailSectionRuntime') }}</h4>
      <div class="hfl-detail-grid">
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colStatus') }}</span>
          <div class="hfl-detail-row__value node-basic-info-status">
            <NodeLifecycleStatusCell
              :node="node"
              :resolve-display-status="resolveNodeDisplayStatus"
            />
            <span v-if="uptimeLabel" class="node-basic-info-status__meta">({{ uptimeLabel }})</span>
          </div>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colVersion') }}</span>
          <span class="hfl-detail-row__value">{{ versionText }}</span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colAvailability') }}</span>
          <span class="hfl-detail-row__value">
            <ElTag :type="availabilityTagType" size="small">{{ availabilityLabel }}</ElTag>
          </span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.fieldAvailabilityUpdatedAt') }}</span>
          <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__empty': !node.availability_updated_at }">{{ formatNodeDate(node.availability_updated_at) }}</span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colRegistered') }}</span>
          <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__empty': !node.created_at }">{{ formatNodeDate(node.created_at) }}</span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('nodesPage.proxyDetailLastSeen') }}</span>
          <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__empty': !node.last_seen_at }">{{ formatNodeDate(node.last_seen_at) }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.node-basic-info-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.node-basic-info-status__meta {
  color: var(--color-text-tertiary, #64748b);
  font-size: 12px;
}
</style>
