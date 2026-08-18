<script setup lang="ts">
import { ref, watch } from 'vue'
import { getSourceResource, listSourceResources, type SourceResource } from '../lib/sourceApi'
import { listAllNodes } from '../lib/nodeApi'
import { usePageRequestScope } from '../composables/usePageRequestScope'
import { useDrawerTableMaxHeight } from '../composables/useDrawerTableMaxHeight'
import type { ApiNode } from '../types/node'
import NasSourceListTable from './NasSourceListTable.vue'
import NasSourceDetailDrawer from '../pages/protection/components/NasSourceDetailDrawer.vue'

const props = defineProps<{
  nodeId: number
  active: boolean
}>()

const requests = usePageRequestScope()
const rows = ref<SourceResource[]>([])
const proxyNodes = ref<ApiNode[]>([])
const loading = ref(false)
const detailOpen = ref(false)
const detail = ref<SourceResource | null>(null)
const { tableMaxHeight, containerRef: tableRef } = useDrawerTableMaxHeight()

async function loadProxyNodes(signal?: AbortSignal) {
  try {
    proxyNodes.value = await listAllNodes({ role: 'proxy' }, { signal })
  } catch {
    proxyNodes.value = []
  }
}

async function loadRows(signal?: AbortSignal) {
  const list = await listSourceResources(
    {
      bound_node_id: props.nodeId,
      resource_type: 'nas',
      page: 1,
      page_size: 500,
    },
    { signal },
  )
  rows.value = list.results
}

async function refresh() {
  const signal = requests.nextSignal('proxy-bound-nas')
  loading.value = true
  try {
    await Promise.all([loadRows(signal), loadProxyNodes(signal)])
  } catch (e) {
    if (requests.isAbortError(e)) return
    rows.value = []
  } finally {
    requests.releaseSignal('proxy-bound-nas', signal)
    if (!signal.aborted) loading.value = false
  }
}

async function openDetail(row: SourceResource) {
  if (row.id > 0) {
    try {
      detail.value = await getSourceResource(row.id)
    } catch {
      detail.value = row
    }
  } else {
    detail.value = row
  }
  detailOpen.value = true
}

function onDetailUpdated(resource: SourceResource) {
  detail.value = resource
  void refresh()
}

watch(
  () => [props.active, props.nodeId] as const,
  ([active, nodeId]) => {
    if (!active || nodeId <= 0) {
      requests.abortScope('proxy-bound-nas')
      return
    }
    void refresh()
  },
  { immediate: true },
)
</script>

<template>
  <div class="proxy-bound-nas-panel">
    <NasSourceListTable
      ref="tableRef"
      :rows="rows"
      :proxy-nodes="proxyNodes"
      :loading="loading"
      :max-height="tableMaxHeight"
      @row-click="openDetail"
    />
    <NasSourceDetailDrawer
      v-if="detailOpen"
      v-model="detailOpen"
      :resource="detail"
      :proxy-nodes="proxyNodes"
      @updated="onDetailUpdated"
    />
  </div>
</template>

<style scoped>
.proxy-bound-nas-panel {
  min-height: 200px;
}
</style>
