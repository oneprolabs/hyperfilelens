<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import HostSourceDetailDrawer from '../../../components/HostSourceDetailDrawer.vue'
import { apiErrorMessage } from '../../../lib/api'
import { useProxyNodeOptions } from '../../../composables/useProxyNodeOptions'
import { getSourceResource, type SourceResource } from '../../../lib/sourceApi'
import type { FlowSourceRow } from '../composables/useFlowSourceAggregate'
import NasSourceDetailDrawer from './NasSourceDetailDrawer.vue'

const hostOpen = ref(false)
const hostNodeId = ref<number | null>(null)
const nasOpen = ref(false)
const nasResource = ref<SourceResource | null>(null)
const { proxyNodes, proxyOptionsLoading, searchProxyNodes, loadMoreProxyNodes } = useProxyNodeOptions(() => nasResource.value?.bound_node ?? undefined, true)
const loading = ref(false)

async function open(source: FlowSourceRow) {
  const refId = Number(source.refId || source.id.split(':')[1])
  if (!Number.isInteger(refId) || refId <= 0) return
  if (source.type === 'host') {
    hostNodeId.value = refId
    hostOpen.value = true
    return
  }
  if (loading.value) return
  loading.value = true
  try {
    nasResource.value = await getSourceResource(refId)
    searchProxyNodes('')
    nasOpen.value = true
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err), grouping: true })
  } finally {
    loading.value = false
  }
}

function setHostOpen(value: boolean) {
  hostOpen.value = value
  if (!value) hostNodeId.value = null
}

defineExpose({ open })
</script>

<template>
  <HostSourceDetailDrawer
    :model-value="hostOpen"
    :node-id="hostNodeId"
    :source="null"
    @update:model-value="setHostOpen"
  />
  <NasSourceDetailDrawer
    v-if="nasOpen"
    v-model="nasOpen"
    :resource="nasResource"
    :proxy-nodes="proxyNodes"
    :proxy-search="searchProxyNodes"
    :proxy-load-more="loadMoreProxyNodes"
    :proxy-loading="proxyOptionsLoading"
  />
</template>
