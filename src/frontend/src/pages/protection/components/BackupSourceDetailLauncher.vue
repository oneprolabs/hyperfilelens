<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { defineLazyDrawer, useDrawerMounted, useDrawerDetailRequest } from '../../../composables/useLazyDrawer'
import { apiErrorMessage } from '../../../lib/api'
import { useProxyNodeOptions } from '../../../composables/useProxyNodeOptions'
import { getSourceResource, type SourceResource } from '../../../lib/sourceApi'
import type { FlowSourceRow } from '../composables/useFlowSourceAggregate'
import { NasSourceDetailDrawer } from './lazyNasSourceDetailDrawer'

const hostOpen = ref(false)
const HostSourceDetailDrawer = defineLazyDrawer(() => import('../../../components/HostSourceDetailDrawer.vue'))
const hostMounted = useDrawerMounted(() => hostOpen.value)
const hostNodeId = ref<number | null>(null)
const nasOpen = ref(false)
const nasResource = ref<SourceResource | null>(null)
const { proxyNodes, proxyOptionsLoading, searchProxyNodes, loadMoreProxyNodes } = useProxyNodeOptions(() => nasResource.value?.bound_node ?? undefined, true)
const { detailLoading, loadDetails } = useDrawerDetailRequest(nasOpen)

async function open(source: FlowSourceRow) {
  const refId = Number(source.refId || source.id.split(':')[1])
  if (!Number.isInteger(refId) || refId <= 0) return
  if (source.type === 'host') {
    hostNodeId.value = refId
    hostOpen.value = true
    return
  }
  if (detailLoading.value) return
  nasResource.value = null
  await loadDetails(signal => getSourceResource(refId, { signal }), resource => {
    nasResource.value = resource
    searchProxyNodes('')
  }, err => ElMessage.error({ message: apiErrorMessage(err), grouping: true }))
}

function setHostOpen(value: boolean) {
  hostOpen.value = value
  if (!value) hostNodeId.value = null
}

defineExpose({ open })
</script>

<template>
  <HostSourceDetailDrawer
    v-if="hostMounted"
    :model-value="hostOpen"
    :node-id="hostNodeId"
    :source="null"
    @update:model-value="setHostOpen"
  />
  <NasSourceDetailDrawer
    v-if="nasOpen"
    v-model="nasOpen"
    :detail-loading="detailLoading"
    :resource="nasResource"
    :proxy-nodes="proxyNodes"
    :proxy-search="searchProxyNodes"
    :proxy-load-more="loadMoreProxyNodes"
    :proxy-loading="proxyOptionsLoading"
  />
</template>
