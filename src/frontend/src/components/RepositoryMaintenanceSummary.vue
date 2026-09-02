<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatBytes } from '../lib/kopiaProgress'
import { repositoryMaintenanceSummaryFromMetadata } from '../lib/repositoryMaintenanceSummary'

const props = defineProps<{
  metadata?: unknown
}>()

const { t } = useI18n()
const summary = computed(() => repositoryMaintenanceSummaryFromMetadata(props.metadata))

function count(group: Record<string, number | undefined>, key: string): number {
  return group[key] ?? 0
}

function metric(group: Record<string, number | undefined>, prefix: string): string {
  return t('ops.task.maintenanceSummary.metric', {
    count: count(group, `${prefix}_count`).toLocaleString(),
    size: formatBytes(count(group, `${prefix}_bytes`)),
  })
}
</script>

<template>
  <div
    v-if="summary"
    class="repository-maintenance-summary"
  >
    <section
      v-if="summary.content_gc"
      class="repository-maintenance-summary__section"
    >
      <strong>{{ t('ops.task.maintenanceSummary.contentGc') }}</strong>
      <dl>
        <template v-if="summary.content_gc.deleted_count !== undefined">
          <dt>{{ t('ops.task.maintenanceSummary.contentRemoved') }}</dt>
          <dd>{{ metric(summary.content_gc, 'deleted') }}</dd>
        </template>
        <template v-if="summary.content_gc.deferred_count !== undefined">
          <dt>{{ t('ops.task.maintenanceSummary.contentDeferred') }}</dt>
          <dd>{{ metric(summary.content_gc, 'deferred') }}</dd>
        </template>
        <template v-if="summary.content_gc.in_use_count !== undefined">
          <dt>{{ t('ops.task.maintenanceSummary.contentInUse') }}</dt>
          <dd>{{ metric(summary.content_gc, 'in_use') }}</dd>
        </template>
      </dl>
    </section>
    <section class="repository-maintenance-summary__section">
      <strong>{{ t('ops.task.maintenanceSummary.packGc') }}</strong>
      <dl v-if="summary.pack_gc">
        <template v-if="summary.pack_gc.deleted_count !== undefined">
          <dt>{{ t('ops.task.maintenanceSummary.packsDeleted') }}</dt>
          <dd>{{ metric(summary.pack_gc, 'deleted') }}</dd>
        </template>
        <template v-if="summary.pack_gc.retained_count !== undefined">
          <dt>{{ t('ops.task.maintenanceSummary.packsRetained') }}</dt>
          <dd>{{ metric(summary.pack_gc, 'retained') }}</dd>
        </template>
      </dl>
      <p
        v-else
        class="repository-maintenance-summary__note"
      >
        {{ t('ops.task.maintenanceSummary.packGcNotReported') }}
      </p>
    </section>
    <p
      v-if="summary.approximate"
      class="repository-maintenance-summary__note"
    >
      {{ t('ops.task.maintenanceSummary.approximate') }}
    </p>
  </div>
</template>

<style scoped>
.repository-maintenance-summary {
  display: grid;
  gap: 8px;
  margin-top: 6px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-light);
  font-size: 12px;
}

.repository-maintenance-summary__section {
  display: grid;
  gap: 4px;
}

.repository-maintenance-summary dl {
  display: grid;
  grid-template-columns: minmax(120px, auto) 1fr;
  gap: 3px 12px;
  margin: 0;
}

.repository-maintenance-summary dt {
  color: var(--el-text-color-secondary);
}

.repository-maintenance-summary dd {
  margin: 0;
  color: var(--el-text-color-primary);
}

.repository-maintenance-summary__note {
  margin: 0;
  color: var(--el-text-color-secondary);
}
</style>
