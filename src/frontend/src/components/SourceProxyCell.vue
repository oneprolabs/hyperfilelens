<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  nodeEnrollmentOs,
  nodeHostname,
  nodeOsDetailText,
  nodePlatformLabel,
  type NodeOsPlatformLabels,
} from '../lib/nodeInventoryDisplay'
import type { ApiNode } from '../types/node'

const props = defineProps<{
  node?: ApiNode | null
  name?: string
}>()

const { t } = useI18n()

const osPlatformLabels = computed((): NodeOsPlatformLabels => ({
  linux: t('protection.sourceResources.osPlatformLinux'),
  windows: t('protection.sourceResources.osPlatformWindows'),
  macos: t('protection.sourceResources.osPlatformMacos'),
}))

const displayName = computed(() => props.node?.name?.trim() || props.name?.trim() || '')
const displayIp = computed(() => props.node?.ip_address?.trim() || '')

const tooltipText = computed(() => {
  if (!props.node) return ''
  const lines: string[] = []
  const hostname = nodeHostname(props.node)
  if (hostname) {
    lines.push(`${t('protection.sourceResources.colHostname')}: ${hostname}`)
  }
  const platform = nodePlatformLabel(nodeEnrollmentOs(props.node), osPlatformLabels.value)
  const osDetail = nodeOsDetailText(props.node)
  const osLine = osDetail || platform
  if (osLine) {
    lines.push(`${t('protection.sourceResources.colOperatingSystem')}: ${osLine}`)
  }
  return lines.join('\n')
})
</script>

<template>
  <ElTooltip
    :content="tooltipText"
    :disabled="!tooltipText"
    placement="right"
  >
    <div class="source-proxy-cell">
      <span
        class="source-proxy-cell__name"
        :class="{ 'hfl-empty-mark': !displayName }"
      >{{ displayName || '—' }}</span>
      <span
        v-if="displayIp"
        class="source-proxy-cell__ip"
      >{{ displayIp }}</span>
    </div>
  </ElTooltip>
</template>
