<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Info, AlertTriangle, Unlink } from 'lucide-vue-next'
import type { TargetRepositoryHealth, TargetRepositoryItem } from './TargetRepositoryPicker.vue'

const props = withDefaults(defineProps<{
  target: TargetRepositoryItem
  hideCapacity?: boolean
}>(), {
  hideCapacity: false,
})

const { n, t } = useI18n()

function repoTypeLabel(type: string): string {
  if (type === 'S3') return 'Object Storage'
  if (type === 'NAS') return 'NAS'
  if (type === 'PROXY_FS') return 'Local Disk'
  return type || 'Unknown'
}

function resolveHealth(target: TargetRepositoryItem): TargetRepositoryHealth {
  if (target.health) return target.health
  return target.status ?? 'unverified'
}

function healthLabel(health: TargetRepositoryHealth): string {
  if (health === 'online') return 'Online'
  if (health === 'offline') return 'Offline'
  if (health === 'unverified') return 'Unverified'
  return 'Unknown'
}

function healthTagType(health: TargetRepositoryHealth): 'success' | 'warning' | 'danger' | 'info' {
  if (health === 'online') return 'success'
  if (health === 'offline') return 'danger'
  if (health === 'unverified') return 'warning'
  return 'info'
}

function nasProtocolLabel(protocol?: string | null): string {
  if (!protocol) return ''
  if (protocol === 'nfs') return 'NFS'
  if (protocol === 'smb') return 'SMB'
  return protocol.toUpperCase()
}

function isBoundToProxy(target: TargetRepositoryItem): boolean {
  return target.bindNodeType === 'proxy' && !!target.bindNodeId
}

function bindNodeLabel(target: TargetRepositoryItem): string {
  if (isBoundToProxy(target)) {
    const name = target.bindNodeName || String(target.bindNodeId)
    const ip = target.bindNodeIp
    return ip ? `${name} (${ip})` : name
  }
  return t('protection.backupsPage.targetDetailBindNodeUnbound')
}

function locationForTarget(target: TargetRepositoryItem): string {
  return target.location || '—'
}

function formatBytes(value: number | null | undefined): string {
  if (!value || value <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let v = value
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${n(v, { maximumFractionDigits: 2 })} ${units[i]}`
}

function usagePercent(target: TargetRepositoryItem): number | null {
  if (!target.capacityBytes || target.capacityBytes <= 0) return null
  if (!target.usedBytes || target.usedBytes < 0) return null
  return Math.min(100, Math.round((target.usedBytes / target.capacityBytes) * 100))
}

const health = computed(() => resolveHealth(props.target))
const unverifiedReason = computed(() =>
  t('protection.backupsPage.targetHealthUnverifiedReason'),
)
</script>

<template>
  <div class="target-repository-detail">
    <div class="target-repository-detail__head">
      <div class="target-repository-detail__title">{{ target.name }}</div>
      <span class="target-repository-detail__title-tags">
        <el-tag
          size="small"
          effect="plain"
          :class="`wizard-repo-type-tag wizard-repo-type-tag--${target.repoType.toLowerCase()}`"
        >
          {{ repoTypeLabel(target.repoType) }}
        </el-tag>
        <el-tag
          v-if="target.repoType === 'NAS' && target.nasProtocol"
          size="small"
          effect="plain"
          :class="`wizard-protocol-tag wizard-protocol-tag--${target.nasProtocol}`"
        >
          {{ nasProtocolLabel(target.nasProtocol) }}
        </el-tag>
      </span>
    </div>

    <ElAlert
      v-if="target.disabledReason"
      type="warning"
      :closable="false"
      class="target-repository-detail__disabled-alert"
      :aria-label="target.disabledReason"
    >
      <ol class="target-repository-detail__disabled-list">
        <li
          v-for="(item, index) in target.disabledReasonItems?.length
            ? target.disabledReasonItems
            : [target.disabledReason]"
          :key="item"
          class="target-repository-detail__disabled-item"
        >
          <span class="target-repository-detail__disabled-index">{{ index + 1 }}</span>
          <span class="target-repository-detail__disabled-text">{{ item }}</span>
        </li>
      </ol>
    </ElAlert>

    <div class="target-repository-detail__sections">
      <section class="target-repository-detail__section target-repository-detail__section--line">
        <span class="target-repository-detail__section-title">Status:</span>
        <span class="target-repository-detail__tag-row">
          <el-tag size="small" :type="healthTagType(health)" effect="light">
            {{ healthLabel(health) }}
          </el-tag>
          <el-tooltip
            v-if="health === 'unverified'"
            :content="unverifiedReason"
            placement="top"
            :show-after="300"
            teleported
          >
            <AlertTriangle :size="13" class="target-repository-detail__hint-icon" />
          </el-tooltip>
        </span>
      </section>

      <div v-if="health === 'unverified'" class="target-repository-detail__reason">
        <Info :size="13" class="target-repository-detail__reason-icon" />
        <span>{{ unverifiedReason }}</span>
      </div>

      <section class="target-repository-detail__section target-repository-detail__section--line">
        <span class="target-repository-detail__section-title">Location:</span>
        <span class="target-repository-detail__value target-repository-detail__value--mono">
          {{ locationForTarget(target) }}
        </span>
      </section>

      <section
        v-if="target.repoType === 'NAS' || target.repoType === 'PROXY_FS'"
        class="target-repository-detail__section target-repository-detail__section--line"
      >
        <span class="target-repository-detail__section-title">{{ t('protection.backupsPage.targetDetailBindNode') }}:</span>
        <span
          class="target-repository-detail__value"
          :class="{ 'target-repository-detail__value--unbound-proxy': !isBoundToProxy(target) }"
        >
          <Unlink
            v-if="!isBoundToProxy(target)"
            :size="14"
            stroke-width="2.2"
            aria-hidden="true"
          />
          <span>{{ bindNodeLabel(target) }}</span>
        </span>
      </section>

      <section
        v-if="!props.hideCapacity"
        class="target-repository-detail__section target-repository-detail__section--line"
      >
        <span class="target-repository-detail__section-title">{{ t('protection.backupsPage.targetDetailCapacity') }}:</span>
        <span class="target-repository-detail__value">
          {{ formatBytes(target.usedBytes) }} / {{ formatBytes(target.capacityBytes) }}
          <span v-if="usagePercent(target) !== null" class="target-repository-detail__usage">
            ({{ usagePercent(target) }}%)
          </span>
        </span>
      </section>
    </div>
  </div>
</template>

<style scoped>
.target-repository-detail {
  display: grid;
  gap: 12px;
  color: rgb(71 85 105);
  font-size: 12px;
  line-height: 1.45;
}

.target-repository-detail__head {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid rgb(226 232 240);
}

.target-repository-detail__title {
  min-width: 0;
  overflow-wrap: anywhere;
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 650;
  line-height: 20px;
}

.target-repository-detail__title-tags {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
}

.target-repository-detail__disabled-alert {
  border-color: var(--color-warning-border) !important;
  border-radius: 6px !important;
  background: var(--color-warning-light) !important;
}

.target-repository-detail__disabled-alert :deep(.el-alert__content) {
  min-width: 0;
  padding: 0 2px;
}

.target-repository-detail__disabled-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.target-repository-detail__disabled-item {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  align-items: flex-start;
  gap: 8px;
}

.target-repository-detail__disabled-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: 1px solid var(--color-warning-border);
  border-radius: 999px;
  background: #fff;
  color: var(--color-warning);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
}

.target-repository-detail__disabled-text {
  min-width: 0;
  color: rgb(120 75 12);
  font-size: 12px;
  line-height: 1.55;
}

.target-repository-detail__sections {
  display: grid;
  min-width: 0;
  gap: 12px;
}

.target-repository-detail__section {
  display: grid;
  min-width: 0;
  gap: 8px;
}

.target-repository-detail__section--line {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  align-items: baseline;
  column-gap: 8px;
}

.target-repository-detail__section-title {
  color: rgb(51 65 85);
  font-size: 12px;
  font-weight: 650;
  line-height: 16px;
}

.target-repository-detail__value {
  min-width: 0;
  overflow-wrap: anywhere;
  color: rgb(30 41 59);
  font-size: 13px;
  line-height: 1.5;
}

.target-repository-detail__value--mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
}

.target-repository-detail__value--unbound-proxy {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.target-repository-detail__value--unbound-proxy svg {
  flex: 0 0 auto;
  color: var(--color-warning);
}

.target-repository-detail__tag-row {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.target-repository-detail__reason {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  border: 1px solid rgba(245, 158, 11, 0.26);
  border-radius: 6px;
  background: rgba(245, 158, 11, 0.09);
  color: rgb(146 64 14);
  font-size: 12px;
  line-height: 1.45;
  padding: 7px 8px;
}

.target-repository-detail__reason-icon {
  flex: 0 0 auto;
  margin-top: 1px;
  color: rgb(180 83 9);
}

.target-repository-detail__hint-icon {
  flex: 0 0 auto;
  cursor: help;
  color: rgb(180 83 9);
}

.target-repository-detail__usage {
  margin-left: 4px;
  color: rgb(100 116 139);
}
</style>
