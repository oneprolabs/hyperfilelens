<script setup lang="ts">
import { computed } from 'vue'
import { Archive, CloudCog, Database, Download, RotateCcw, Settings2, Trash2, Unplug } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import HflTypeLabel from './HflTypeLabel.vue'

const props = withDefaults(defineProps<{
  type?: string | null
  operationType?: string | null
  emphasis?: 'primary' | 'secondary'
}>(), {
  type: '',
  operationType: '',
  emphasis: 'primary',
})

const { t } = useI18n()

const typeIcons = {
  backup: Archive,
  restore: RotateCcw,
  insight_workspace_restore: RotateCcw,
  snapshot_download: Download,
  snapshot_delete: Trash2,
  backup_config_reset: Settings2,
  backup_config_provision: CloudCog,
  source_unregister: Unplug,
  repository_operation: Database,
  storage_provider_validation: CloudCog,
} as const

const icon = computed(() => {
  const type = props.type
  return type && type in typeIcons ? typeIcons[type as keyof typeof typeIcons] : null
})

const label = computed(() => {
  if (!props.type) return t('ops.task.emptyMark')
  const key = `ops.task.taskType.${props.type}`
  const translated = t(key)
  return translated === key ? props.type : translated
})

const repositoryOperationKeys: Record<string, string> = {
  'maintenance.quick': 'ops.task.operationType.maintenanceQuick',
  'maintenance.full': 'ops.task.operationType.maintenanceFull',
  'cleanup.target': 'ops.task.operationType.cleanupTarget',
  'cleanup.repository': 'ops.task.operationType.cleanupRepository',
  check: 'ops.task.operationType.check',
}

function enumDisplayLabel(value: string) {
  return value
    .trim()
    .split(/[._-]+/)
    .filter(Boolean)
    .map(part => `${part.charAt(0).toUpperCase()}${part.slice(1).toLowerCase()}`)
    .join(' ')
}

const operationLabel = computed(() => {
  if (props.type !== 'repository_operation' || !props.operationType) return ''
  const fallback = enumDisplayLabel(props.operationType)
  const key = repositoryOperationKeys[props.operationType]
  if (!key) return fallback
  const translated = t(key)
  return translated === key ? fallback : translated
})
</script>

<template>
  <div
    v-if="operationLabel"
    class="hfl-task-type-stack"
  >
    <HflTypeLabel
      :icon="icon"
      :label="label"
      :emphasis="emphasis"
    />
    <span
      class="hfl-task-type-stack__operation"
      :title="operationLabel"
    >
      {{ operationLabel }}
    </span>
  </div>
  <HflTypeLabel
    v-else
    :icon="icon"
    :label="label"
    :emphasis="emphasis"
  />
</template>

<style scoped>
.hfl-task-type-stack {
  display: inline-flex;
  min-width: 0;
  max-width: 100%;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  vertical-align: middle;
}

.hfl-task-type-stack__operation {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  padding-left: 18px;
  overflow: hidden;
  color: var(--color-text-tertiary, #64748b);
  font-size: 12px;
  font-weight: 400;
  line-height: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
