<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ metadata?: unknown }>()
const { t } = useI18n()
const summary = computed(() => {
  const data = props.metadata as Record<string, unknown> | undefined
  if (!data || data.event_type !== 'orphan_snapshot_summary' || !data.summary || typeof data.summary !== 'object') return []
  const values = data.summary as Record<string, unknown>
  return ['scanned', 'discovered', 'deleted', 'grace', 'deferred', 'failed', 'absent', 'managed']
    .map(key => ({ key, count: typeof values[key] === 'number' ? values[key] : 0 }))
})
const result = computed(() => {
  if (!props.metadata || typeof props.metadata !== 'object') return null
  const data = props.metadata as Record<string, unknown>
  if (data.event_type !== 'orphan_snapshot_result' || typeof data.snapshot_id !== 'string') return null
  const source = data.source && typeof data.source === 'object'
    ? data.source as Record<string, unknown> : {}
  const text = (value: unknown) => typeof value === 'string' && value ? value : '—'
  return {
    id: data.snapshot_id,
    outcome: text(data.outcome),
    source: [text(source.name), text(source.host), text(source.user)].join(' · '),
    path: text(source.path),
    reason: text(data.reason),
  }
})
</script>

<template>
  <dl v-if="summary.length">
    <template
      v-for="item in summary"
      :key="item.key"
    >
      <dt>{{ t(`ops.task.orphanSnapshots.${item.key}`) }}</dt>
      <dd>{{ item.count }}</dd>
    </template>
  </dl>
  <section
    v-if="result"
    class="orphan-snapshot-result"
  >
    <strong>{{ t(`ops.task.orphanSnapshots.${result.outcome}`) }}</strong>
    <dl>
      <dt>{{ t('ops.task.orphanSnapshots.source') }}</dt>
      <dd>{{ result.source }}</dd>
      <dt>{{ t('ops.task.orphanSnapshots.path') }}</dt>
      <dd>{{ result.path }}</dd>
      <dt>{{ t('ops.task.orphanSnapshots.snapshotId') }}</dt>
      <dd><code>{{ result.id }}</code></dd>
    </dl>
    <p v-if="result.reason !== '—'">
      {{ result.reason }}
    </p>
  </section>
</template>

<style scoped>
.orphan-snapshot-result { margin-block: 8px; overflow-wrap: anywhere; }
dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 12px; }
dt { color: var(--el-text-color-secondary); }
dd { margin: 0; }
</style>
