<script setup lang="ts">
import { computed } from 'vue'
import type { ApiNode } from '../../types/node'

const props = defineProps<{
  node: ApiNode
  versionLabel: string
  resolveVersionDisplay?: (
    node: ApiNode,
    versionLabel: string,
  ) => { upgrading: boolean; versionLabel: string; targetVersion: string }
}>()

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
