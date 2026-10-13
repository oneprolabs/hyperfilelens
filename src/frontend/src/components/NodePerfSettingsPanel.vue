<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { updateNode, type NodeApiScope } from '../lib/nodeApi'
import { apiErrorMessageI18n } from '../lib/api'
import { nodeCpuCores, nodeMemoryTotalBytes } from '../lib/nodeInventoryDisplay'
import {
  buildDefaultNodePerfSettings,
  DEFAULT_KOPIA_CACHE_MB,
  MAX_KOPIA_CACHE_MB,
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
const perfCpuCapCores = ref<number | undefined>(2)
const perfMemCapMb = ref<number | undefined>(2048)
const kopiaCacheMb = ref(DEFAULT_KOPIA_CACHE_MB)
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

const supportsKopiaCache = computed(() => props.node?.role === 'agent' || props.node?.role === 'proxy')
const performanceStatus = computed(() => {
  const metadata = props.node?.metadata
  const policy = metadata?.performance_policy as { revision?: number } | undefined
  const report = metadata?.performance_status as {
    revision?: number
    items?: Record<string, { state: string; reason: string }>
  } | undefined
  if (!policy?.revision) return metadata?.perf_settings ? { pending: true, legacy: true, stale: false, items: [] } : null
  const timestamp = Date.parse(String((report as { reported_at?: string } | undefined)?.reported_at || ''))
  return {
    legacy: false,
    stale: Number.isFinite(timestamp) && Date.now() - timestamp > 120_000,
    pending: report?.revision !== policy.revision,
    items: report?.revision === policy.revision ? Object.entries(report.items || {}).filter(([key, item]) =>
      key in performanceItemLabels && item && typeof item === 'object'
      && ['applied', 'pending', 'unsupported', 'degraded', 'monitoring', 'disabled'].includes(item.state),
    ) : [],
  }
})
const performanceItemLabels: Record<string, string> = {
  cpu: 'perfCpuCap', memory: 'perfMemSoft', bandwidth: 'perfBandwidth',
  cache: 'perfKopiaCache', logLevel: 'perfLogLevel', retention: 'perfLogRetention', logSpace: 'perfLogSpace',
}

type PerfSnapshot = NodePerfSettings

function buildPerfDefaults(): PerfSnapshot {
  return buildDefaultNodePerfSettings(hostCpuCores.value, hostMemMb.value)
}

function capturePerfSnapshot(): PerfSnapshot {
  return {
    bandwidth: perfBandwidth.value,
    cpuCores: perfCpuCapCores.value ?? Number.NaN,
    memCapMb: perfMemCapMb.value ?? Number.NaN,
    kopiaCacheMb: kopiaCacheMb.value,
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

function quotaError(value: number | undefined, min: number, max: number): string {
  return typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max
    ? ''
    : t('nodesDetail.perfQuotaRangeError', { min, max })
}

const cpuQuotaError = computed(() => quotaError(perfCpuCapCores.value, 1, hostCpuCores.value))
const memoryQuotaError = computed(() => quotaError(perfMemCapMb.value, 128, hostMemMb.value))
const quotasValid = computed(() => !cpuQuotaError.value && !memoryQuotaError.value)
const otherInputError = computed(() => {
  const inputs: Array<[number | undefined, number, number, string]> = [
    [perfBandwidth.value, 1, 9999, 'perfBandwidth'],
    [perfLogRetentionDays.value, 1, 365, 'perfLogRetention'],
    [perfLogSpaceMb.value, 64, 8192, 'perfLogSpace'],
  ]
  if (supportsKopiaCache.value) inputs.push([kopiaCacheMb.value, 0, MAX_KOPIA_CACHE_MB, 'perfKopiaCache'])
  for (const [value, min, max, label] of inputs) {
    const message = quotaError(value, min, max)
    if (message) return `${t(`nodesDetail.${label}`)}: ${message}`
  }
  return ''
})

const perfCpuDisplay = computed(() => cpuQuotaError.value ? '—'
  : t('nodesDetail.perfCpuCoresRatio', {
      pct: quotaPercent(perfCpuCapCores.value!, hostCpuCores.value),
      limit: perfCpuCapCores.value,
      total: hostCpuCores.value,
    }))

const perfMemDisplay = computed(() => memoryQuotaError.value ? '—'
  : t('nodesDetail.perfMemQuotaRatio', {
      pct: quotaPercent(perfMemCapMb.value!, hostMemMb.value),
      limit: formatMemGb(perfMemCapMb.value!),
      total: formatMemGb(hostMemMb.value),
    }))

function clampPerfValues() {
  if (typeof perfCpuCapCores.value === 'number' && Number.isFinite(perfCpuCapCores.value)) {
    perfCpuCapCores.value = Math.min(Math.max(1, perfCpuCapCores.value), hostCpuCores.value)
  }
  if (typeof perfMemCapMb.value === 'number' && Number.isFinite(perfMemCapMb.value)) {
    perfMemCapMb.value = Math.min(Math.max(128, perfMemCapMb.value), hostMemMb.value)
  }
  const rawCacheSize = kopiaCacheMb.value as unknown
  const parsedCacheSize = rawCacheSize == null ? Number.NaN : Number(rawCacheSize)
  if (Number.isFinite(parsedCacheSize)) {
    kopiaCacheMb.value = Math.min(MAX_KOPIA_CACHE_MB, Math.max(0, Math.round(parsedCacheSize)))
  }
}

function applyPerfSettings(settings: PerfSnapshot) {
  perfBandwidth.value = settings.bandwidth
  perfCpuCapCores.value = settings.cpuCores
  perfMemCapMb.value = settings.memCapMb
  kopiaCacheMb.value = settings.kopiaCacheMb
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
  if (!quotasValid.value || otherInputError.value) {
    const label = t(cpuQuotaError.value ? 'nodesDetail.perfCpuCap' : 'nodesDetail.perfMemSoft')
    const message = otherInputError.value || `${label}: ${cpuQuotaError.value || memoryQuotaError.value}`
    ElMessage.warning({ message, grouping: true })
    throw new Error('invalid resource quota')
  }
  clampPerfValues()
  const settings = capturePerfSnapshot()
  try {
    const updated = await updateNode(
      node.id,
      { metadata: { perf_settings: supportsKopiaCache.value ? settings : {
        bandwidth: settings.bandwidth,
        cpuCores: settings.cpuCores,
        memCapMb: settings.memCapMb,
        log: settings.log,
        logRetention: settings.logRetention,
        logSpaceMb: settings.logSpaceMb,
      } } },
      props.nodeScope,
    )
    perfSnapshot.value = capturePerfSnapshot()
    ElMessage.success({ message: t('nodesDetail.perfSaveSuccess'), grouping: true })
    return updated
  } catch (error) {
    ElMessage.error({ message: apiErrorMessageI18n(error, t, t('nodesDetail.saveFailed')), grouping: true })
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
    a.kopiaCacheMb === b.kopiaCacheMb &&
    a.log === b.log &&
    a.logRetention === b.logRetention &&
    a.logSpaceMb === b.logSpaceMb
  )
}

const hasPerfChanges = computed(() => {
  const metadata = props.node?.metadata
  const policy = metadata?.performance_policy as { revision?: number } | undefined
  const report = metadata?.performance_status as { observed_revision?: number } | undefined
  const needsReconcile = Number(report?.observed_revision || 0) > Number(policy?.revision || 0)
  const needsActivation = Boolean(metadata?.perf_settings) && !policy?.revision
  return needsActivation || needsReconcile || !perfSnapshotsEqual(capturePerfSnapshot(), perfSnapshot.value)
})

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
    <div
      v-if="performanceStatus"
      class="node-perf-status"
      role="status"
    >
      <p>
        {{ t(performanceStatus.legacy ? 'nodesDetail.perfLegacy' : performanceStatus.pending
          ? 'nodesDetail.perfPending' : performanceStatus.stale ? 'nodesDetail.perfStale' : 'nodesDetail.perfReported') }}
      </p>
      <ul v-if="!performanceStatus.pending">
        <li
          v-for="[key, item] in performanceStatus.items"
          :key="key"
        >
          <strong>{{ t(`nodesDetail.${performanceItemLabels[key] || 'perfResSection'}`) }}:</strong>
          {{ t(`nodesDetail.perfState.${item.state}`) }}
          <span v-if="item.reason"> — {{ item.reason }}</span>
        </li>
      </ul>
    </div>
    <section class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">
        {{ t('nodesDetail.perfResSection') }}
      </h4>
      <p class="node-perf-scope">
        {{ t('nodesDetail.perfResourceScope') }}
        <span v-if="node?.role === 'gateway'">{{ t('nodesDetail.perfGatewayScope') }}</span>
      </p>
      <div class="hfl-detail-config-grid">
        <div class="hfl-detail-config-cell">
          <div class="hfl-detail-config-cell__label">
            {{ t('nodesDetail.perfCpuCap') }}
          </div>
          <div class="hfl-detail-config-cell__control">
            <div class="hfl-detail-form-input hfl-detail-form-input--narrow">
              <ElInputNumber
                v-model="perfCpuCapCores"
                :min="1"
                :max="hostCpuCores"
                :precision="0"
                :step="1"
                :controls="false"
                :aria-label="t('nodesDetail.perfCpuCap')"
                class="hfl-detail-form-input__num"
              />
              <div class="hfl-detail-form-input__suffix">
                {{ t('nodesDetail.unitCores') }}
              </div>
            </div>
          </div>
          <p class="hfl-detail-config-cell__metric">
            {{ perfCpuDisplay }}
          </p>
          <p
            v-if="cpuQuotaError"
            class="node-perf-quota-error"
            role="alert"
          >
            {{ cpuQuotaError }}
          </p>
          <p class="hfl-detail-config-cell__hint">
            {{ t('nodesDetail.perfDescCpu') }}
          </p>
        </div>

        <div class="hfl-detail-config-cell">
          <div class="hfl-detail-config-cell__label">
            {{ t('nodesDetail.perfMemSoft') }}
          </div>
          <div class="hfl-detail-config-cell__control">
            <div class="hfl-detail-form-input hfl-detail-form-input--narrow">
              <ElInputNumber
                v-model="perfMemCapMb"
                :min="128"
                :max="hostMemMb"
                :precision="0"
                :step="1"
                :controls="false"
                :aria-label="t('nodesDetail.perfMemSoft')"
                class="hfl-detail-form-input__num"
              />
              <div class="hfl-detail-form-input__suffix">
                {{ t('nodesDetail.unitMbPlain') }}
              </div>
            </div>
          </div>
          <p class="hfl-detail-config-cell__metric">
            {{ perfMemDisplay }}
          </p>
          <p
            v-if="memoryQuotaError"
            class="node-perf-quota-error"
            role="alert"
          >
            {{ memoryQuotaError }}
          </p>
          <p class="hfl-detail-config-cell__hint">
            {{ t('nodesDetail.perfDescMem') }}
          </p>
        </div>

        <div class="hfl-detail-config-cell">
          <div class="hfl-detail-config-cell__label">
            {{ t('nodesDetail.perfBandwidth') }}
          </div>
          <div class="hfl-detail-config-cell__control">
            <div class="hfl-detail-form-input hfl-detail-form-input--bandwidth">
              <ElInputNumber
                v-model="perfBandwidth"
                :min="1"
                :max="9999"
                :precision="0"
                :controls="false"
                class="hfl-detail-form-input__num"
              />
              <div class="hfl-detail-form-input__suffix">
                {{ t('nodesDetail.unitMb') }}
              </div>
            </div>
          </div>
          <p class="hfl-detail-config-cell__hint">
            {{ t('nodesDetail.perfDescBandwidth') }}
          </p>
        </div>
      </div>
    </section>

    <section
      v-if="supportsKopiaCache"
      class="hfl-detail-section"
    >
      <h4 class="hfl-detail-section__title">
        {{ t('nodesDetail.perfCacheSection') }}
      </h4>
      <div class="hfl-detail-config-grid">
        <div class="hfl-detail-config-cell">
          <div class="hfl-detail-config-cell__label">
            {{ t('nodesDetail.perfKopiaCache') }}
          </div>
          <div class="hfl-detail-config-cell__control">
            <div class="hfl-detail-form-input hfl-detail-form-input--narrow">
              <ElInputNumber
                v-model="kopiaCacheMb"
                :min="0"
                :max="MAX_KOPIA_CACHE_MB"
                :step="256"
                :precision="0"
                controls-position="right"
                class="hfl-detail-form-input__num"
              />
              <div class="hfl-detail-form-input__suffix">
                {{ t('nodesDetail.unitMbPlain') }}
              </div>
            </div>
          </div>
          <p class="hfl-detail-config-cell__hint">
            {{ t('nodesDetail.perfDescKopiaCache') }}
          </p>
        </div>
      </div>
    </section>

    <section class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">
        {{ t('nodesDetail.perfLogSection') }}
      </h4>
      <div class="hfl-detail-config-grid">
        <div class="hfl-detail-config-cell">
          <div class="hfl-detail-config-cell__label">
            {{ t('nodesDetail.perfLogLevel') }}
          </div>
          <div class="hfl-detail-config-cell__control">
            <ElSelect
              v-model="perfLogLevel"
              class="hfl-detail-config-cell__select"
              teleported
            >
              <ElOption
                value="debug"
                :label="t('nodesDetail.perfLogLevelDebug')"
              />
              <ElOption
                value="info"
                :label="t('nodesDetail.perfLogLevelInfo')"
              />
              <ElOption
                value="warning"
                :label="t('nodesDetail.perfLogLevelWarning')"
              />
              <ElOption
                value="error"
                :label="t('nodesDetail.perfLogLevelError')"
              />
            </ElSelect>
          </div>
          <p class="hfl-detail-config-cell__hint">
            {{ t('nodesDetail.perfDescLogLevel') }}
          </p>
        </div>

        <div class="hfl-detail-config-cell">
          <div class="hfl-detail-config-cell__label">
            {{ t('nodesDetail.perfLogRetention') }}
          </div>
          <div class="hfl-detail-config-cell__control">
            <div class="hfl-detail-form-input hfl-detail-form-input--narrow">
              <ElInputNumber
                v-model="perfLogRetentionDays"
                :min="1"
                :max="365"
                :precision="0"
                controls-position="right"
                class="hfl-detail-form-input__num"
              />
              <div class="hfl-detail-form-input__suffix">
                {{ t('nodesDetail.unitDays') }}
              </div>
            </div>
          </div>
          <p class="hfl-detail-config-cell__hint">
            {{ t('nodesDetail.perfDescLogRetention') }}
          </p>
        </div>

        <div class="hfl-detail-config-cell">
          <div class="hfl-detail-config-cell__label">
            {{ t('nodesDetail.perfLogSpace') }}
          </div>
          <div class="hfl-detail-config-cell__control">
            <div class="hfl-detail-form-input hfl-detail-form-input--narrow">
              <ElInputNumber
                v-model="perfLogSpaceMb"
                :min="64"
                :max="8192"
                :step="64"
                :precision="0"
                controls-position="right"
                class="hfl-detail-form-input__num"
              />
              <div class="hfl-detail-form-input__suffix">
                {{ t('nodesDetail.unitMbPlain') }}
              </div>
            </div>
          </div>
          <p class="hfl-detail-config-cell__hint">
            {{ t('nodesDetail.perfDescLogSpace') }}
          </p>
        </div>
      </div>
    </section>
    <p
      v-if="otherInputError"
      class="node-perf-quota-error"
      role="alert"
    >
      {{ otherInputError }}
    </p>
  </div>

  <div
    v-if="!hideActions"
    class="node-perf-drawer-inline-actions"
  >
    <p class="hfl-detail-row__hint">
      {{ t('nodesDetail.perfApplyFooter') }}
    </p>
    <div class="node-perf-drawer-inline-actions__btns">
      <ElButton
        text
        @click="cancelPerfEdits"
      >
        {{ t('nodesDetail.perfCancelEdits') }}
      </ElButton>
      <ElButton
        plain
        @click="resetPerfDefaults"
      >
        {{ t('nodesDetail.perfResetDefaults') }}
      </ElButton>
      <ElButton
        type="primary"
        :disabled="!node || !hasPerfChanges || !quotasValid || Boolean(otherInputError)"
        @click="node && savePerfSettings(node)"
      >
        {{ t('nodesDetail.save') }}
      </ElButton>
    </div>
  </div>
</template>

<style scoped>
.hfl-detail-config-cell__metric {
  margin: 0;
  text-align: left;
}

.node-perf-quota-error {
  margin: 0;
  color: var(--el-color-danger);
  font-size: 12px;
}

.node-perf-status {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.node-perf-status ul {
  padding-left: 20px;
}

.node-perf-status li {
  overflow-wrap: anywhere;
}

.node-perf-scope {
  margin: 0;
  padding: 12px 16px 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

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
