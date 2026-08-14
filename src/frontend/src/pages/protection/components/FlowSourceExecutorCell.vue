<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { flowSourceNodeParts } from '../../../lib/flowSourceDisplay'
import type { FlowSourceRow } from '../composables/useFlowSourceAggregate'

const props = defineProps<{
  row: FlowSourceRow
}>()

const { t } = useI18n()

const nodeParts = computed(() => flowSourceNodeParts(props.row))
</script>

<template>
  <span
    v-if="row.type === 'host'"
    class="protection-flow-cell-muted"
  >{{ t('protection.backupsPage.flowExecutorLocalAgent') }}</span>
  <div
    v-else
    class="protection-flow-node-cell"
  >
    <span class="source-node-name">{{ nodeParts.name }}</span>
    <span
      v-if="nodeParts.ip"
      class="source-ip-text"
    >{{ nodeParts.ip }}</span>
  </div>
</template>
