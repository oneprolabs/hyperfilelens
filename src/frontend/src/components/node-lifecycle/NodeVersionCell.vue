<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { TriangleAlert } from 'lucide-vue-next'
import { ElTooltip } from 'element-plus'
import type { ApiNode } from '../../types/node'

const props = withDefaults(defineProps<{
  node: ApiNode
  versionLabel: string
  targetVersion?: string | null
  updateAvailable?: boolean
  showUpdateHint?: boolean
  resolveVersionDisplay?: (
    node: ApiNode,
    versionLabel: string,
  ) => { upgrading: boolean; versionLabel: string; targetVersion: string }
}>(), {
  targetVersion: null,
  showUpdateHint: true,
  resolveVersionDisplay: undefined,
})

const { t } = useI18n()

const display = computed(() => {
  if (props.resolveVersionDisplay) {
    return props.resolveVersionDisplay(props.node, props.versionLabel)
  }
  const lc = props.node.lifecycle
  const upgrading =
    lc?.kind === 'upgrade' &&
    ['upgrading', 'restarting', 'verifying', 'queued'].includes(lc.state)
  return {
    upgrading,
    versionLabel: props.versionLabel,
    targetVersion: lc?.target_version || '',
  }
})

const showsUpdate = computed(() =>
  props.showUpdateHint !== false
  && !display.value.upgrading
  && props.updateAvailable === true
  && Boolean(props.targetVersion),
)

const updateLabel = computed(() => t('nodesPage.latestVersionTip', {
  version: props.targetVersion || '—',
}))
</script>

<template>
  <div
    class="node-version-cell"
    :class="{ 'node-version-cell--upgrading': display.upgrading }"
  >
    <template v-if="display.upgrading && display.targetVersion">
      <span class="node-version-cell__from">{{ display.versionLabel }}</span>
      <span class="node-version-cell__arrow">→</span>
      <span class="node-version-cell__to">{{ display.targetVersion }}</span>
    </template>
    <span
      v-else
      class="node-version-cell__value"
    >{{ display.versionLabel }}</span>
    <ElTooltip
      v-if="showsUpdate"
      :content="updateLabel"
      placement="top"
    >
      <span
        class="node-version-cell__hint hfl-table-no-tooltip"
        tabindex="0"
        :aria-label="updateLabel"
      >
        <TriangleAlert
          :size="14"
          stroke-width="2.1"
          aria-hidden="true"
        />
        <span>{{ t('nodesPage.versionUpgradeAvailable') }}</span>
      </span>
    </ElTooltip>
  </div>
</template>

<style scoped>
.node-version-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.node-version-cell--upgrading {
  color: rgb(180 83 9);
}

.node-version-cell__from,
.node-version-cell__to,
.node-version-cell__value {
  font-variant-numeric: tabular-nums;
}

.node-version-cell__arrow {
  display: none;
}

.node-version-cell__hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  color: var(--el-color-warning-dark-2);
  font-size: 12px;
  line-height: 1.2;
  cursor: default;
}

.node-version-cell__hint > span:last-child {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-version-cell--upgrading {
  flex-direction: row;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.node-version-cell--upgrading .node-version-cell__arrow {
  display: inline;
  opacity: 0.7;
}
</style>
