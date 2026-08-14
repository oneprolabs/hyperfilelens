<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { LoaderCircle, X } from 'lucide-vue-next'
import type { LifecycleQueueSnapshot } from '../../types/nodeLifecycle'

const props = defineProps<{
  snapshot: LifecycleQueueSnapshot | null
}>()

const emit = defineEmits<{
  cancelQueued: []
}>()

const { t } = useI18n()

function snapshotList<K extends keyof LifecycleQueueSnapshot>(key: K) {
  const value = props.snapshot?.[key]
  return Array.isArray(value) ? value : []
}

const visible = computed(() => {
  if (!props.snapshot) return false
  return totalCount.value > 0
})

const runningCount = computed(() => snapshotList('running').length)
const queuedCount = computed(() => snapshotList('queued').length)
const completedCount = computed(() => snapshotList('completed').length)
const failedCount = computed(() => snapshotList('failed').length)
const skippedCount = computed(() => snapshotList('skipped').length)
const totalCount = computed(
  () =>
    runningCount.value +
    queuedCount.value +
    completedCount.value +
    failedCount.value +
    skippedCount.value,
)

const summary = computed(() => {
  const s = props.snapshot
  if (!s) return ''
  const done = completedCount.value + failedCount.value + skippedCount.value
  if (s.kind === 'upgrade') {
    return t('nodeLifecycle.bannerUpgrade', {
      running: runningCount.value,
      queued: queuedCount.value,
      done,
      total: totalCount.value,
      failed: failedCount.value,
    })
  }
  return t('nodeLifecycle.bannerRemove', {
    running: runningCount.value,
    queued: queuedCount.value,
    done,
    total: totalCount.value,
    failed: failedCount.value,
  })
})
</script>

<template>
  <div
    v-if="visible"
    class="node-lifecycle-banner"
  >
    <LoaderCircle
      :size="16"
      class="node-lifecycle-banner__icon is-spinning"
    />
    <span class="node-lifecycle-banner__text">{{ summary }}</span>
    <button
      v-if="queuedCount > 0"
      type="button"
      class="node-lifecycle-banner__action"
      @click="emit('cancelQueued')"
    >
      <X :size="14" />
      {{ t('nodeLifecycle.cancelQueued') }}
    </button>
  </div>
</template>

<style scoped>
.node-lifecycle-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 12px;
  padding: 10px 14px;
  border-radius: 10px;
  border: 1px solid rgb(199 210 254);
  background: rgb(238 242 255);
  color: rgb(67 56 202);
  font-size: 13px;
}

.node-lifecycle-banner__icon.is-spinning {
  animation: node-lifecycle-banner-spin 1s linear infinite;
}

.node-lifecycle-banner__text {
  flex: 1;
  min-width: 0;
}

.node-lifecycle-banner__action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font-size: 12px;
}

@keyframes node-lifecycle-banner-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
