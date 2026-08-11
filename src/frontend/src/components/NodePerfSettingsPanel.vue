<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { updateNode, type NodeApiScope } from '../lib/nodeApi'
import { nodeCpuCores, nodeMemoryTotalBytes } from '../lib/nodeInventoryDisplay'
import {
  buildDefaultNodePerfSettings,
  mergeNodeMetadataWithPerfSettings,
  readNodePerfSettings,
  type NodePerfLogLevel,
  type NodePerfSettings,
} from '../lib/nodePerfSettings'
import type { ApiNode } from '../types/node'

const props = withDefaults(
  defineProps<{
    active?: boolean
    hideActions?: boolean
    node?: ApiNode | null
    nodeScope?: NodeApiScope
  }>(),
  { active: true, hideActions: false, node: null, nodeScope: 'tenant' },
)

const { t } = useI18n()

const HOST_CPU_FALLBACK = 12
const HOST_MEM_MB_FALLBACK = 16 * 1024

const perfBandwidth = ref(20)
const perfCpuCapCores = ref(2)
const perfMemCapMb = ref(2048)
const perfLogLevel = ref<NodePerfLogLevel>('info')
const perfLogRetentionDays = ref(30)
const perfLogSpaceMb = ref(512)

const hostCpuCores = computed(() => {
  if (!props.node) return HOST_CPU_FALLBACK
  return nodeCpuCores(props.node) ?? HOST_CPU_FALLBACK
})

const hostMemMb = computed(() => {
  if (!props.node) return HOST_MEM_MB_FALLBACK
  const bytes = nodeMemoryTotalBytes(props.node)
  if (bytes == null) return HOST_MEM_MB_FALLBACK
  return Math.max(128, Math.round(bytes / (1024 * 1024)))
})

type PerfSnapshot = NodePerfSettings

function buildPerfDefaults(): PerfSnapshot {
  return buildDefaultNodePerfSettings(hostCpuCores.value, hostMemMb.value)
}

function capturePerfSnapshot(): PerfSnapshot {
  return {
    bandwidth: perfBandwidth.value,
    cpuCores: perfCpuCapCores.value,
    memCapMb: perfMemCapMb.value,
    log: perfLogLevel.value,
    logRetention: perfLogRetentionDays.value,
    logSpaceMb: perfLogSpaceMb.value,
  }
}

const perfSnapshot = ref<PerfSnapshot>(buildPerfDefaults())
const loadedNodeId = ref<number | null>(null)

function formatMemGb(mb: number): string {
  const gb = mb / 1024
  if (Number.isInteger(gb)) return `${gb} GB`
  return `${gb.toFixed(1)} GB`
}

function quotaPercent(limit: number, total: number): number {
  if (total <= 0) return 0
  return Math.round((limit / total) * 100)
}

const perfCpuDisplay = computed(() =>
  t('nodesDetail.perfCpuCoresRatio', {
    pct: quotaPercent(perfCpuCapCores.value, hostCpuCores.value),
    limit: perfCpuCapCores.value,
    total: hostCpuCores.value,
  }),
)

const perfMemDisplay = computed(() =>
  t('nodesDetail.perfMemQuotaRatio', {
    pct: quotaPercent(perfMemCapMb.value, hostMemMb.value),
    limit: formatMemGb(perfMemCapMb.value),
    total: formatMemGb(hostMemMb.value),
  }),
)

function clampPerfValues() {
  perfCpuCapCores.value = Math.min(Math.max(1, perfCpuCapCores.value), hostCpuCores.value)
  perfMemCapMb.value = Math.min(Math.max(128, perfMemCapMb.value), hostMemMb.value)
}

function applyPerfSettings(settings: PerfSnapshot) {
  perfBandwidth.value = settings.bandwidth
  perfCpuCapCores.value = settings.cpuCores
  perfMemCapMb.value = settings.memCapMb
  perfLogLevel.value = settings.log
  perfLogRetentionDays.value = settings.logRetention
  perfLogSpaceMb.value = settings.logSpaceMb
  clampPerfValues()
}

function loadFromNode(node: ApiNode) {
  const defaults = buildPerfDefaults()
  applyPerfSettings(readNodePerfSettings(node, defaults))
  perfSnapshot.value = capturePerfSnapshot()
}

function applyPerfDefaults() {
  applyPerfSettings(buildPerfDefaults())
}

async function savePerfSettings(node: ApiNode): Promise<ApiNode | null> {
  if (!hasPerfChanges.value) return null
  clampPerfValues()
  const settings = capturePerfSnapshot()
  try {
    const updated = await updateNode(
      node.id,
      { metadata: mergeNodeMetadataWithPerfSettings(node, settings) },
      props.nodeScope,
    )
    perfSnapshot.value = capturePerfSnapshot()
    ElMessage.success({ message: t('nodesDetail.saveSuccess'), grouping: true })
    return updated
  } catch {
    ElMessage.error({ message: t('nodesDetail.saveFailed'), grouping: true })
    throw new Error('save perf settings failed')
  }
}

function resetPerfDefaults() {
  applyPerfDefaults()
}

function cancelPerfEdits() {
  applyPerfSettings(perfSnapshot.value)
}

function perfSnapshotsEqual(a: PerfSnapshot, b: PerfSnapshot): boolean {
  return (
    a.bandwidth === b.bandwidth &&
    a.cpuCores === b.cpuCores &&
    a.memCapMb === b.memCapMb &&
    a.log === b.log &&
    a.logRetention === b.logRetention &&
    a.logSpaceMb === b.logSpaceMb
  )
}

const hasPerfChanges = computed(() => !perfSnapshotsEqual(capturePerfSnapshot(), perfSnapshot.value))

watch(
  () => [props.active, props.node?.id] as const,
  ([isActive, id]) => {
    if (!isActive || id == null) return
    if (loadedNodeId.value === id) return
    loadedNodeId.value = id
    loadFromNode(props.node!)
  },
  { immediate: true },
)

watch(
  () => props.node?.id,
  (id) => {
    if (id == null) loadedNodeId.value = null
  },
)

watch([hostCpuCores, hostMemMb], () => {
  clampPerfValues()
})

defineExpose({ hasPerfChanges, savePerfSettings, cancelPerfEdits, resetPerfDefaults })
</script>

<template>
  <div class="hfl-detail-sections node-perf-drawer-sections">
    <section class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">{{ t('nodesDetail.perfResSection') }}</h4>
      <div class="hfl-detail-config-grid">
        <div class="hfl-detail-config-cell">
          <div class="hfl-detail-config-cell__label">{{ t('nodesDetail.perfCpuCap') }}</div>
          <div class="hfl-detail-config-cell__slider-row">
            <ElSlider
              v-model="perfCpuCapCores"
              :min="1"
              :max="hostCpuCores"
              :step="1"
              :show-tooltip="false"
              class="hfl-detail-config-cell__slider"
            />
            <span class="hfl-detail-config-cell__metric">{{ perfCpuDisplay }}</span>
          </div>
          <p class="hfl-detail-config-cell__hint">{{ t('nodesDetail.perfDescCpu') }}</p>
        </div>

        <div class="hfl-detail-config-cell">
          <div class="hfl-detail-config-cell__label">{{ t('nodesDetail.perfMemSoft') }}</div>
          <div class="hfl-detail-config-cell__slider-row">
            <ElSlider
              v-model="perfMemCapMb"
              :min="128"
              :max="hostMemMb"
              :step="256"
              :show-tooltip="false"
              class="hfl-detail-config-cell__slider"
            />
            <span class="hfl-detail-config-cell__metric">{{ perfMemDisplay }}</span>
          </div>
          <p class="hfl-detail-config-cell__hint">{{ t('nodesDetail.perfDescMem') }}</p>
        </div>

        <div class="hfl-detail-config-cell">
          <div class="hfl-detail-config-cell__label">{{ t('nodesDetail.perfBandwidth') }}</div>
          <div class="hfl-detail-config-cell__control">
            <div class="hfl-detail-form-input hfl-detail-form-input--bandwidth">
              <ElInputNumber
                v-model="perfBandwidth"
                :min="1"
                :max="9999"
                :controls="false"
                class="hfl-detail-form-input__num"
              />
              <div class="hfl-detail-form-input__suffix">
                {{ t('nodesDetail.unitMb') }}
              </div>
            </div>
          </div>
          <p class="hfl-detail-config-cell__hint">{{ t('nodesDetail.perfDescBandwidth') }}</p>
        </div>
      </div>
    </section>

    <section class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">{{ t('nodesDetail.perfLogSection') }}</h4>
      <div class="hfl-detail-config-grid">
        <div class="hfl-detail-config-cell">
          <div class="hfl-detail-config-cell__label">{{ t('nodesDetail.perfLogLevel') }}</div>
          <div class="hfl-detail-config-cell__control">
            <ElSelect v-model="perfLogLevel" class="hfl-detail-config-cell__select" teleported>
              <ElOption value="debug" :label="t('nodesDetail.perfLogLevelDebug')" />
              <ElOption value="info" :label="t('nodesDetail.perfLogLevelInfo')" />
              <ElOption value="warning" :label="t('nodesDetail.perfLogLevelWarning')" />
              <ElOption value="error" :label="t('nodesDetail.perfLogLevelError')" />
            </ElSelect>
          </div>
          <p class="hfl-detail-config-cell__hint">{{ t('nodesDetail.perfDescLogLevel') }}</p>
        </div>

        <div class="hfl-detail-config-cell">
          <div class="hfl-detail-config-cell__label">{{ t('nodesDetail.perfLogRetention') }}</div>
          <div class="hfl-detail-config-cell__control">
            <div class="hfl-detail-form-input hfl-detail-form-input--narrow">
              <ElInputNumber
                v-model="perfLogRetentionDays"
                :min="1"
                :max="365"
                controls-position="right"
                class="hfl-detail-form-input__num"
              />
              <div class="hfl-detail-form-input__suffix">
                {{ t('nodesDetail.unitDays') }}
              </div>
            </div>
          </div>
          <p class="hfl-detail-config-cell__hint">{{ t('nodesDetail.perfDescLogRetention') }}</p>
        </div>

        <div class="hfl-detail-config-cell">
          <div class="hfl-detail-config-cell__label">{{ t('nodesDetail.perfLogSpace') }}</div>
          <div class="hfl-detail-config-cell__control">
            <div class="hfl-detail-form-input hfl-detail-form-input--narrow">
              <ElInputNumber
                v-model="perfLogSpaceMb"
                :min="64"
                :max="8192"
                :step="64"
                controls-position="right"
                class="hfl-detail-form-input__num"
              />
              <div class="hfl-detail-form-input__suffix">
                {{ t('nodesDetail.unitMbPlain') }}
              </div>
            </div>
          </div>
          <p class="hfl-detail-config-cell__hint">{{ t('nodesDetail.perfDescLogSpace') }}</p>
        </div>
      </div>
    </section>
  </div>

  <div v-if="!hideActions" class="node-perf-drawer-inline-actions">
    <p class="hfl-detail-row__hint">{{ t('nodesDetail.perfApplyFooter') }}</p>
    <div class="node-perf-drawer-inline-actions__btns">
      <ElButton text @click="cancelPerfEdits">{{ t('nodesDetail.perfCancelEdits') }}</ElButton>
      <ElButton plain @click="resetPerfDefaults">{{ t('nodesDetail.perfResetDefaults') }}</ElButton>
      <ElButton
        type="primary"
        :disabled="!node || !hasPerfChanges"
        @click="node && savePerfSettings(node)"
      >
        {{ t('nodesDetail.save') }}
      </ElButton>
    </div>
  </div>
</template>

<style scoped>
.node-perf-drawer-sections {
  padding-bottom: 4px;
}

.node-perf-drawer-inline-actions {
  margin-top: 12px;
  padding-top: 14px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.node-perf-drawer-inline-actions__btns {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}
</style>
