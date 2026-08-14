<script setup lang="ts">
import { computed } from 'vue'
import { ClipboardList, FileSearch, HardDrive, Monitor, ShieldCheck, Share2 } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import AgentPlatformBrandIcon from './agent-deploy/AgentPlatformBrandIcon.vue'
import S3PlatformBrandIcon from './S3PlatformBrandIcon.vue'
import { nasMountProtocolIcon } from '../lib/resourceIcons'
import type { EnrollmentOs } from '../lib/nodeApi'

type SummaryKind = 'host' | 'nas' | 's3' | 'standalone' | 'policy' | 'filter' | 'task'

const props = withDefaults(defineProps<{
  name: string
  kind: SummaryKind
  platform?: EnrollmentOs | null
  protocol?: string | null
  s3Platform?: string | null
  summary?: string | null
  clickable?: boolean
  showIcon?: boolean
}>(), {
  platform: null,
  protocol: null,
  s3Platform: null,
  summary: null,
  clickable: true,
  showIcon: true,
})

const emit = defineEmits<{
  open: []
}>()

const { t } = useI18n()

const mainIcon = computed(() => {
  if (props.kind === 'nas') return Share2
  if (props.kind === 'standalone') return HardDrive
  if (props.kind === 'policy') return ShieldCheck
  if (props.kind === 'filter') return FileSearch
  if (props.kind === 'task') return ClipboardList
  return Monitor
})

const protocolLabel = computed(() => {
  if (props.protocol === 'smb') return t('repositoriesPage.protocolSmb')
  if (props.protocol === 'nfs') return t('repositoriesPage.protocolNfs')
  return ''
})

const platformLabel = computed(() => {
  if (props.platform === 'windows') return t('protection.sourceResources.osPlatformWindows')
  if (props.platform === 'macos') return t('protection.sourceResources.osPlatformMacos')
  if (props.platform === 'linux') return t('protection.sourceResources.osPlatformLinux')
  return ''
})

function onClick() {
  if (props.clickable) emit('open')
}
</script>

<template>
  <button
    v-if="clickable"
    type="button"
    class="backup-source-cell backup-source-cell--summary backup-source-cell--clickable"
    @click.stop="onClick"
  >
    <div
      v-if="showIcon"
      class="backup-source-cell__icon-col"
    >
      <S3PlatformBrandIcon
        v-if="kind === 's3'"
        :platform="s3Platform"
        :size="16"
        icon-class="resource-name-summary-cell__s3-icon"
        lucide-class="resource-name-summary-cell__main-icon text-slate-500"
      />
      <component
        :is="mainIcon"
        v-else
        :size="16"
        class="resource-name-summary-cell__main-icon"
        :class="{
          'text-slate-500': kind === 'host' || kind === 'standalone' || kind === 'policy' || kind === 'filter' || kind === 'task',
          'text-emerald-500': kind === 'nas',
        }"
      />
      <ElTooltip
        v-if="kind === 'host' && platform && platformLabel"
        :content="platformLabel"
        placement="top"
      >
        <span class="source-os-cell__icon-wrap flow-source-trait-icon">
          <AgentPlatformBrandIcon :os="platform" />
        </span>
      </ElTooltip>
      <ElTooltip
        v-else-if="kind === 'nas' && protocol && protocolLabel"
        :content="protocolLabel"
        placement="top"
      >
        <span
          class="repo-protocol-pill repo-protocol-pill--icon-only"
          :class="`repo-protocol-pill--${protocol || 'nfs'}`"
        >
          <component
            :is="nasMountProtocolIcon(protocol)"
            :size="12"
            stroke-width="2.25"
          />
        </span>
      </ElTooltip>
    </div>
    <span class="backup-source-cell__body">
      <span class="hfl-table-name-link hfl-table-name-link--single">
        {{ name }}
      </span>
      <span
        v-if="summary"
        class="backup-source-cell__type"
      >
        {{ summary }}
      </span>
    </span>
  </button>
  <span
    v-else
    class="backup-source-cell backup-source-cell--summary"
  >
    <span
      v-if="showIcon"
      class="backup-source-cell__icon-col"
    >
      <S3PlatformBrandIcon
        v-if="kind === 's3'"
        :platform="s3Platform"
        :size="16"
        icon-class="resource-name-summary-cell__s3-icon"
        lucide-class="resource-name-summary-cell__main-icon text-slate-500"
      />
      <component
        :is="mainIcon"
        v-else
        :size="16"
        class="resource-name-summary-cell__main-icon text-slate-500"
      />
    </span>
    <span class="backup-source-cell__body">
      <span class="hfl-table-name-link hfl-table-name-link--single">
        {{ name }}
      </span>
      <span
        v-if="summary"
        class="backup-source-cell__type"
      >
        {{ summary }}
      </span>
    </span>
  </span>
</template>
