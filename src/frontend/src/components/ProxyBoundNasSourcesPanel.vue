<script setup lang="ts">
import { ref, watch } from 'vue'
import { getSourceResource, listSourceResources, type SourceResource } from '../lib/sourceApi'
import { getNode } from '../lib/nodeApi'
import { useProxyNodeOptions } from '../composables/useProxyNodeOptions'
import { usePageRequestScope } from '../composables/usePageRequestScope'
import { useDrawerTableMaxHeight } from '../composables/useDrawerTableMaxHeight'
import type { ApiNode } from '../types/node'
import NasSourceListTable from './NasSourceListTable.vue'
import HflPagination from './HflPagination.vue'
import NasSourceDetailDrawer from '../pages/protection/components/NasSourceDetailDrawer.vue'

const props = defineProps<{
  nodeId: number
  active: boolean
}>()

const requests = usePageRequestScope()
const rows = ref<SourceResource[]>([])
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)
const boundProxy = ref<ApiNode[]>([])
const loading = ref(false)
const detailOpen = ref(false)
const detail = ref<SourceResource | null>(null)
const { proxyNodes, proxyOptionsLoading, searchProxyNodes, loadMoreProxyNodes } = useProxyNodeOptions(() => detail.value?.bound_node ?? undefined, true)
const { tableMaxHeight, containerRef: tableRef } = useDrawerTableMaxHeight()

async function loadProxyNodes(signal?: AbortSignal) {
  try {
    const node = await getNode(props.nodeId, { signal })
    if (!signal?.aborted) boundProxy.value = [node]
  } catch {
    if (!signal?.aborted) boundProxy.value = []
  }
}

async function loadRows(signal?: AbortSignal) {
  const list = await listSourceResources(
    {
      bound_node_id: props.nodeId,
      resource_type: 'nas',
      page: page.value,
      page_size: pageSize.value,
    },
    { signal },
  )
  if (!signal?.aborted) {
    rows.value = list.results
    total.value = list.count
  }
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
  searchProxyNodes('')
}

function onDetailUpdated(resource: SourceResource) {
  detail.value = resource
  void refresh()
}

watch(
  () => [props.active, props.nodeId, page.value, pageSize.value] as const,
  ([active, nodeId], previous) => {
    if (!active || nodeId <= 0) {
      requests.abortScope('proxy-bound-nas')
      return
    }
    if (previous && previous[1] !== nodeId && page.value !== 1) {
      page.value = 1
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
      :proxy-nodes="boundProxy"
      :loading="loading"
      :max-height="tableMaxHeight"
      @row-click="openDetail"
    />
    <HflPagination
      v-if="total > 0"
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      @update:page-size="page = 1"
    />
    <NasSourceDetailDrawer
      v-if="detailOpen"
      v-model="detailOpen"
      :resource="detail"
      :proxy-nodes="proxyNodes"
      :proxy-search="searchProxyNodes"
      :proxy-load-more="loadMoreProxyNodes"
      :proxy-loading="proxyOptionsLoading"
      @updated="onDetailUpdated"
    />
  </div>
</template>

<style scoped>
.proxy-bound-nas-panel {
  min-height: 200px;
}
</style>
