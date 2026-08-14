<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import NodeBasicInfoPanel from '../../components/NodeBasicInfoPanel.vue'
import NodePerfSettingsPanel from '../../components/NodePerfSettingsPanel.vue'
import NodeMaintenancePanel from '../../components/NodeMaintenancePanel.vue'
import HflDetailDrawerFooter from '../../components/HflDetailDrawerFooter.vue'
import { getGatewayNode } from '../../lib/nodeApi'
import { fetchGatewayAiStatus, type GatewayAiStatus } from '../../lib/lensApi'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import { usePageRequestScope } from '../../composables/usePageRequestScope'
import GatewayLensBasicSection from './GatewayLensBasicSection.vue'
import type { ApiNode } from '../../types/node'

const props = defineProps<{
  modelValue: boolean
  nodeId: number | null
  gatewayScope?: 'user' | 'platform'
  initialTab?: 'basic' | 'performance' | 'maintenance'
}>()

const emit = defineEmits<{
  'update:modelValue': [boolean]
  saved: []
}>()

const { t } = useI18n()
const requests = usePageRequestScope()

const open = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const node = ref<ApiNode | null>(null)
const gatewayAi = ref<GatewayAiStatus | null>(null)
const busy = ref(false)
const saving = ref(false)
const savedInSession = ref(false)
const drawerTab = ref<'basic' | 'performance' | 'maintenance'>('basic')
const basicPanelRef = ref<InstanceType<typeof NodeBasicInfoPanel> | null>(null)
const perfPanelRef = ref<InstanceType<typeof NodePerfSettingsPanel> | null>(null)
const { drawerSize, updateDrawerWidth, bindDrawerResize, unbindDrawerResize } = useResponsiveDrawerWidth()

const perfTabActive = computed(() => open.value && drawerTab.value === 'performance')

const hasDrawerChanges = computed(() => {
  const perfChanged = perfPanelRef.value?.hasPerfChanges ?? false
  return perfChanged
})

function onNodeUpdated(updated: ApiNode) {
  node.value = updated
  savedInSession.value = true
}

function onDrawerOpened() {
  bindDrawerResize()
}

function onDrawerClosed() {
  basicPanelRef.value?.discardNameEdit()
  perfPanelRef.value?.cancelPerfEdits()
  requests.abortScope('gateway-node-detail')
  unbindDrawerResize()
  node.value = null
  gatewayAi.value = null
  drawerTab.value = 'basic'
  stopPolling()
  if (savedInSession.value) {
    emit('saved')
    savedInSession.value = false
  }
}

async function saveDrawerChanges() {
  if (!hasDrawerChanges.value || !node.value) return
  saving.value = true
  let didSave = false
  try {
    if (perfPanelRef.value?.hasPerfChanges) {
      try {
        const updated = await perfPanelRef.value.savePerfSettings(node.value)
        if (updated) {
          node.value = updated
          didSave = true
        }
      } catch {
        // Child panel surfaces validation/API errors.
      }
    }
    if (didSave) savedInSession.value = true
  } finally {
    saving.value = false
  }
}

async function refresh() {
  const id = props.nodeId
  if (id == null) {
    node.value = null
    gatewayAi.value = null
    return
  }
  const signal = requests.nextSignal('gateway-node-detail')
  busy.value = true
  try {
    const [nodeRow, aiRow] = await Promise.all([
      getGatewayNode(id, props.gatewayScope === 'platform' ? 'platform' : 'tenant', { signal }),
      fetchGatewayAiStatus(id).catch(() => null),
    ])
    node.value = nodeRow
    gatewayAi.value = aiRow
  } catch (e) {
    if (requests.isAbortError(e)) return
    node.value = null
    gatewayAi.value = null
  } finally {
    requests.releaseSignal('gateway-node-detail', signal)
    if (!signal.aborted) busy.value = false
  }
}

watch(
  () => [open.value, props.nodeId] as const,
  ([isOpen, id]) => {
    if (isOpen && id != null) {
      drawerTab.value = props.initialTab ?? 'basic'
      savedInSession.value = false
      void refresh()
    }
    if (!isOpen) {
      requests.abortScope('gateway-node-detail')
      node.value = null
      gatewayAi.value = null
      stopPolling()
    }
  },
)

let pollTimer: ReturnType<typeof setInterval> | null = null

function stopPolling() {
  if (pollTimer != null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPolling() {
  stopPolling()
  if (!open.value || props.nodeId == null) return
  pollTimer = setInterval(() => {
    if (open.value && props.nodeId != null && !busy.value) void refresh()
  }, 8000)
}

watch(
  () => [open.value, node.value?.availability] as const,
  ([isOpen, availability]) => {
    if (isOpen && availability === 'online') {
      startPolling()
    } else {
      stopPolling()
    }
  },
)

watch(open, (isOpen) => {
  if (isOpen) {
    void nextTick(() => requestAnimationFrame(() => updateDrawerWidth()))
  }
})

onUnmounted(() => {
  stopPolling()
  unbindDrawerResize()
})
</script>

<template>
  <ElDrawer
    v-model="open"
    direction="rtl"
    destroy-on-close
    :modal="true"
    :size="drawerSize"
    class="hfl-detail-drawer"
    @opened="onDrawerOpened"
    @closed="onDrawerClosed"
  >
    <template #header>
      <span class="hfl-detail-drawer__title">{{ node?.name || '—' }}</span>
    </template>

    <div
      v-loading="busy"
      class="hfl-detail-drawer__body"
    >
      <template v-if="node">
        <ElTabs
          v-model="drawerTab"
          class="hfl-detail-tabs"
        >
          <ElTabPane
            :label="t('protection.sourceResources.detailTabBasic')"
            name="basic"
          >
            <NodeBasicInfoPanel
              ref="basicPanelRef"
              :node="node"
              :node-scope="gatewayScope === 'platform' ? 'platform' : 'tenant'"
              :source-type-label="t('ops.monitorPage.sourceTypeGateway')"
              use-unified-capacity
              @node-updated="onNodeUpdated"
            >
              <template #before-runtime>
                <GatewayLensBasicSection :gateway-ai="gatewayAi" />
              </template>
            </NodeBasicInfoPanel>
          </ElTabPane>

          <ElTabPane
            :label="t('protection.sourceResources.detailTabAdvanced')"
            name="performance"
          >
            <NodePerfSettingsPanel
              ref="perfPanelRef"
              hide-actions
              :active="perfTabActive"
              :node="node"
              :node-scope="gatewayScope === 'platform' ? 'platform' : 'tenant'"
            />
          </ElTabPane>

          <ElTabPane
            :label="t('nodeLifecycle.maintenance')"
            name="maintenance"
            lazy
          >
            <NodeMaintenancePanel
              :node="node"
              :gateway-scope="gatewayScope ?? 'user'"
              :refreshing="busy"
              @refresh="refresh()"
            />
          </ElTabPane>
        </ElTabs>
      </template>

      <ElEmpty
        v-else-if="!busy"
        :description="t('nodesPage.gatewayDetailEmpty')"
        :image-size="72"
      />
    </div>

    <template
      v-if="node && drawerTab === 'performance'"
      #footer
    >
      <HflDetailDrawerFooter
        :saving="saving"
        :save-disabled="!hasDrawerChanges"
        @cancel="open = false"
        @save="saveDrawerChanges"
      />
    </template>
  </ElDrawer>
</template>
