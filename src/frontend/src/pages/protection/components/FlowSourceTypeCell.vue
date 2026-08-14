<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { flowSourceKindLabel } from '../../../lib/flowSourceDisplay'
import type { FlowSourceRow } from '../composables/useFlowSourceAggregate'

const props = defineProps<{
  row: FlowSourceRow
}>()

const { t } = useI18n()

const label = computed(() =>
  flowSourceKindLabel(props.row, {
    host: t('protection.backupsPage.sourceTypeHost'),
    nas: t('protection.backupsPage.sourceTypeNas'),
  }),
)
</script>

<template>
  <el-tag
    size="small"
    effect="plain"
    :type="row.type === 'host' ? 'info' : 'success'"
  >
    {{ label }}
  </el-tag>
</template>
