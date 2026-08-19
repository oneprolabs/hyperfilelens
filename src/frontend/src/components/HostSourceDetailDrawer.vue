<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import NodeBasicInfoPanel from './NodeBasicInfoPanel.vue'
import NodePerfSettingsPanel from './NodePerfSettingsPanel.vue'
import NodeMaintenancePanel from './NodeMaintenancePanel.vue'
import NodeUpgradeProgress from './node-lifecycle/NodeUpgradeProgress.vue'
import NodeAuditLogSummary from './node-lifecycle/NodeAuditLogSummary.vue'
import HflDetailDrawerFooter from './HflDetailDrawerFooter.vue'
import { getNode } from '../lib/nodeApi'
import type { SourceResource } from '../lib/sourceApi'
import { useResponsiveDrawerWidth } from '../composables/useResponsiveDrawerWidth'
import { usePageRequestScope } from '../composables/usePageRequestScope'
import type { ApiNode } from '../types/node'

type NodeDisplayStatus = {
  labelKey: string
  tagType: 'success' | 'warning' | 'danger' | 'info'
  tagClass?: string
  spinning?: boolean
}

const props = defineProps<{
  modelValue: boolean
  nodeId: number | null
  source?: SourceResource | null
  resolveDisplayStatus?: (node: ApiNode) => NodeDisplayStatus
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
const busy = ref(false)
const saving = ref(false)
const savedInSession = ref(false)
const drawerTab = ref<'basic' | 'performance' | 'maintenance' | 'upgrade'>('basic')
const basicPanelRef = ref<InstanceType<typeof NodeBasicInfoPanel> | null>(null)
const perfPanelRef = ref<InstanceType<typeof NodePerfSettingsPanel> | null>(null)
const { drawerSize, updateDrawerWidth, bindDrawerResize, unbindDrawerResize } = useResponsiveDrawerWidth()

const hasUpgradeLifecycle = computed(() => {
  const lc = node.value?.lifecycle
  return lc != null && lc.kind === 'upgrade' && lc.timeline != null && lc.timeline.length > 0
})

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
  requests.abortScope('host-node-detail')
  unbindDrawerResize()
  node.value = null
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

async function refresh(options?: { silent?: boolean }) {
  const silent = options?.silent === true
  const id = props.nodeId
  if (id == null) {
    node.value = null
    return
  }
  const signal = requests.nextSignal('host-node-detail')
  if (!silent) busy.value = true
  try {
    node.value = await getNode(id, { signal })
  } catch (e) {
    if (requests.isAbortError(e)) return
    if (!silent) node.value = null
  } finally {
    requests.releaseSignal('host-node-detail', signal)
    if (!signal.aborted && !silent) busy.value = false
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
      requests.abortScope('host-node-detail')
      node.value = null
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
    if (open.value && props.nodeId != null && !busy.value) void refresh({ silent: true })
  }, 8000)
}

watch(
  () => [open.value, node.value?.availability, drawerTab.value] as const,
  ([isOpen, availability, tab]) => {
    if (isOpen && availability === 'online' && tab === 'performance') {
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
              :source="source"
              :source-type-label="t('protection.sourceResources.tabHostFileSystem')"
              :resolve-display-status="resolveDisplayStatus"
              use-backup-source-terminology
              use-unified-capacity
              @node-updated="onNodeUpdated"
            />
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
            />
          </ElTabPane>

          <ElTabPane
            v-if="hasUpgradeLifecycle"
            :label="t('nodeUpgradeProgress.title')"
            name="upgrade"
            lazy
          >
            <div class="node-upgrade-tab">
              <NodeUpgradeProgress :lifecycle="node?.lifecycle ?? null" />
              <NodeAuditLogSummary
                v-if="nodeId != null"
                :node-id="nodeId"
              />
            </div>
          </ElTabPane>

          <ElTabPane
            :label="t('nodeLifecycle.maintenance')"
            name="maintenance"
            lazy
          >
            <NodeMaintenancePanel
              :node="node"
              :refreshing="busy"
              @refresh="refresh()"
            />
          </ElTabPane>
        </ElTabs>
      </template>

      <ElEmpty
        v-else-if="!busy"
        :description="t('protection.sourceResources.hostDetailEmpty')"
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

<style scoped>
.node-upgrade-tab {
  display: flex;
  flex-direction: column;
  gap: 24px;
}
</style>
