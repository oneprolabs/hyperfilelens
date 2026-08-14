<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import AgentPlatformBrandIcon from '../../../components/agent-deploy/AgentPlatformBrandIcon.vue'
import { flowSourceProtocolLabel } from '../../../lib/flowSourceDisplay'
import { nasMountProtocolIcon } from '../../../lib/resourceIcons'
import type { EnrollmentOs } from '../../../lib/nodeApi'
import type { FlowSourceRow } from '../composables/useFlowSourceAggregate'

const props = defineProps<{
  row: FlowSourceRow
}>()

const { t } = useI18n()

const protocolLabel = computed(() =>
  flowSourceProtocolLabel(props.row, {
    protocolSmb: t('repositoriesPage.protocolSmb'),
    protocolNfs: t('repositoriesPage.protocolNfs'),
  }),
)

const hostPlatformLabel = computed(() => {
  if (props.row.type !== 'host' || !props.row.platform) return ''
  if (props.row.platform === 'windows') return t('protection.sourceResources.osPlatformWindows')
  if (props.row.platform === 'macos') return t('protection.sourceResources.osPlatformMacos')
  return t('protection.sourceResources.osPlatformLinux')
})
</script>

<template>
  <span
    v-if="row.type === 'host' && row.platform"
    class="source-os-cell source-os-cell--compact"
  >
    <span class="source-os-cell__icon-wrap">
      <AgentPlatformBrandIcon :os="row.platform as EnrollmentOs" />
    </span>
    <span class="source-os-cell__platform">{{ hostPlatformLabel }}</span>
  </span>
  <span
    v-else-if="row.type === 'host'"
    class="hfl-empty-mark"
  >{{ t('protection.backupDetail.durationDash') }}</span>
  <span
    v-else
    class="repo-protocol-pill"
    :class="`repo-protocol-pill--${row.protocol || 'nfs'}`"
  >
    <component
      :is="nasMountProtocolIcon(row.protocol)"
      :size="12"
      stroke-width="2.25"
    />
    {{ protocolLabel }}
  </span>
</template>
