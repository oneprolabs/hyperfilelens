<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import ModulePage from '../../../../components/ModulePage.vue'
import { apiErrorMessage } from '../../../../lib/api'
import { formatLocalDateTime } from '../../../../lib/dateTime'
import RuntimeServiceConnections from '../../../components/RuntimeServiceConnections.vue'
import RuntimeStatusTable from '../../../components/RuntimeStatusTable.vue'
import type {
  RuntimeStatusCell,
  RuntimeStatusNotice,
  RuntimeStatusRow,
} from '../../../components/runtimeStatus'
import { useResolvedPlatformOpsSideNav } from '../../../composables/useResolvedPlatformOpsSideNav'
import { fetchPlatformEnvironment, type PlatformEnvironmentSettings } from '../../../lib/platformOpsApi'

type TagType = 'success' | 'warning' | 'danger' | 'info'
type ProbeRecord = Record<string, unknown>
const { t } = useI18n()
const sideNav = useResolvedPlatformOpsSideNav()

const busy = ref(false)
const payload = ref<PlatformEnvironmentSettings | null>(null)

const health = computed(() => (payload.value?.health || {}) as ProbeRecord)
const apiProbe = computed(() => asProbe(health.value.api))
const databaseProbe = computed(() => asProbe(health.value.database))
const redisProbe = computed(() => asProbe(health.value.redis))
const celeryProbe = computed(() => asProbe(health.value.celery))
const schedulerProbe = computed(() => asProbe(health.value.scheduler))
const nginxProbe = computed(() => asProbe(health.value.nginx))
const webProbe = computed(() => asProbe(health.value.web))
const sourceLensProbe = computed(() => asProbe(health.value.sourcelens))
const dataGatewayProbe = computed(() => asProbe(health.value.data_gateway))
const checkedAt = computed(() => {
  const raw = health.value.checked_at
  return typeof raw === 'string' ? raw : ''
})
const editionLabel = computed(() => {
  const edition = String(payload.value?.edition || '').trim().toLowerCase()
  if (edition === 'community') return t('editionCommunity')
  if (edition === 'enterprise') return t('editionEnterprise')
  return edition ? `${edition.charAt(0).toUpperCase()}${edition.slice(1)}` : '—'
})

const instanceHealth = computed(() =>
  aggregateStatus([
    probeStatus(apiProbe.value),
    probeStatus(databaseProbe.value),
    probeStatus(redisProbe.value),
    probeStatus(celeryProbe.value),
    probeStatus(schedulerProbe.value),
    probeStatus(sourceLensProbe.value),
    probeStatus(dataGatewayProbe.value),
  ]),
)
const controlPlaneHealth = computed(() =>
  aggregateStatus([
    probeStatus(nginxProbe.value),
    probeStatus(webProbe.value),
    probeStatus(apiProbe.value),
    probeStatus(celeryProbe.value),
    probeStatus(schedulerProbe.value),
    probeStatus(databaseProbe.value),
    probeStatus(redisProbe.value),
  ]),
)

const controlPlaneRows = computed<RuntimeStatusRow[]>(() => [
  serviceRow('nginx', 'Nginx', nginxProbe.value, [t('platformOps.settings.environment.nginxDetail')]),
  serviceRow('web', 'Web', webProbe.value, [t('platformOps.settings.environment.webDetail')]),
  serviceRow('api', 'API', apiProbe.value, [t('platformOps.settings.environment.apiDetail')]),
  serviceRow(
    'worker',
    'Worker',
    celeryProbe.value,
    workerDetails(celeryProbe.value),
    Number(celeryProbe.value.worker_count || 0) > 0 ? 'ok' : 'error',
  ),
  serviceRow('scheduler', 'Scheduler', schedulerProbe.value, schedulerDetails(schedulerProbe.value)),
  serviceRow('postgres', 'PostgreSQL', databaseProbe.value, databaseDetails(databaseProbe.value)),
  serviceRow(
    'redis',
    'Redis',
    redisProbe.value,
    redisDetails(redisProbe.value),
    probeStatus(redisProbe.value),
    backlogNotices(celeryProbe.value.backlog),
  ),
])

const controlPlaneOverviewRow = computed<RuntimeStatusRow>(() => {
  const details = [
    `${t('platformOps.settings.environment.deployment')}: ${t('platformOps.settings.environment.dockerCompose')}`,
    `${t('platformOps.integrations.version')}: ${payload.value?.app_version || '—'}`,
    `${t('platformOps.integrations.consoleUrl')}: ${typeof window === 'undefined' ? '—' : window.location.origin}`,
  ]
  return {
    key: 'control-plane-runtime',
    service: 'Runtime',
    runtime: statusCell(t('platformOps.settings.environment.statusNotMonitored'), 'info'),
    health: healthCell(controlPlaneHealth.value),
    availability: availabilityCell(controlPlaneHealth.value),
    details,
    notices: controlPlaneNotices(),
  }
})

function asProbe(value: unknown): ProbeRecord {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as ProbeRecord
  }
  return {}
}

function probeStatus(probe: ProbeRecord): string {
  return String(probe.status || '').trim().toLowerCase() || 'unknown'
}

function statusCell(label: string, type: RuntimeStatusCell['type']): RuntimeStatusCell {
  return { label, type }
}

function serviceRow(
  key: string,
  service: string,
  probe: ProbeRecord,
  details: string[],
  healthStatus = probeStatus(probe),
  notices: RuntimeStatusNotice[] = [],
): RuntimeStatusRow {
  const probeNotice = noticeForProbe(probe)
  return {
    key,
    service,
    runtime: runtimeCell(probe),
    health: healthCell(healthStatus),
    availability: availabilityCell(probeStatus(probe)),
    details: probeNotice ? [] : details.filter(Boolean),
    notices: probeNotice ? [probeNotice] : notices,
  }
}

function noticeForProbe(probe: ProbeRecord): RuntimeStatusNotice | null {
  const error = String(probe.error || '').trim()
  if (error) return { message: error, level: 'error' }
  const message = String(probe.message || '').trim()
  if (!message) return null
  return {
    message,
    level: probeStatus(probe) === 'degraded' ? 'warning' : 'info',
  }
}

function runtimeCell(probe: ProbeRecord): RuntimeStatusCell {
  void probe
  return { label: t('platformOps.settings.environment.statusNotMonitored'), type: 'info' }
}

function healthCell(status: string): RuntimeStatusCell {
  if (status === 'ok') return { label: t('platformOps.integrations.healthy'), type: 'success' }
  if (status === 'error') return { label: t('platformOps.settings.environment.healthUnhealthy'), type: 'danger' }
  if (status === 'degraded') return { label: t('platformOps.settings.environment.healthUnhealthy'), type: 'warning' }
  return { label: t('platformOps.settings.environment.statusNotMonitored'), type: 'info' }
}

function availabilityCell(status: string): RuntimeStatusCell {
  if (status === 'ok' || status === 'ready') {
    return { label: t('platformOps.settings.environment.statusOperational'), type: 'success' }
  }
  if (status === 'degraded') return { label: t('platformOps.settings.environment.statusDegraded'), type: 'warning' }
  if (status === 'error') return { label: t('platformOps.settings.environment.statusUnavailable'), type: 'danger' }
  if (status === 'not_configured') return { label: t('platformOps.settings.environment.statusNotConfigured'), type: 'info' }
  return { label: '—', type: 'info' }
}

function statusTone(status: string): TagType {
  if (status === 'ok' || status === 'ready') return 'success'
  if (status === 'degraded') return 'warning'
  if (status === 'error') return 'danger'
  return 'info'
}

function statusLabel(status: string): string {
  if (status === 'ok') return t('platformOps.settings.environment.statusHealthy')
  if (status === 'ready') return t('platformOps.settings.environment.statusReady')
  if (status === 'not_configured') return t('platformOps.settings.environment.statusNotConfigured')
  if (status === 'degraded') return t('platformOps.settings.environment.statusDegraded')
  if (status === 'error') return t('platformOps.settings.environment.statusUnavailable')
  return t('platformOps.settings.environment.statusUnknown')
}

function aggregateStatus(statuses: string[]): string {
  const relevant = statuses.filter((status) => status !== 'not_configured')
  if (!relevant.length) return 'unknown'
  if (relevant.some((status) => status === 'error')) return 'error'
  if (relevant.some((status) => status === 'degraded' || status === 'unknown')) return 'degraded'
  if (relevant.every((status) => status === 'ok' || status === 'ready')) return 'ok'
  return 'unknown'
}

function formatText(value: unknown): string {
  const text = String(value ?? '').trim()
  return text || '—'
}

function formatNumber(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function formatLatency(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return t('platformOps.settings.environment.latencyMs', { n: value })
  }
  return '—'
}

function formatHost(probe: ProbeRecord): string {
  const host = String(probe.host || '').trim()
  const port = String(probe.port || '').trim()
  if (host && port) return `${host}:${port}`
  return host || ''
}

function formatDatabaseEngine(value: unknown): string {
  const engine = String(value || '').toLowerCase()
  if (engine.includes('postgres')) return 'PostgreSQL'
  return formatText(value)
}

function backlogNotices(value: unknown): RuntimeStatusNotice[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  const warnings = (value as ProbeRecord).warnings
  if (!Array.isArray(warnings)) return []
  return warnings
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .map((message) => ({ message, level: 'warning' as const }))
}

function workerDetails(probe: ProbeRecord): string[] {
  const workers = formatNumber(probe.worker_count)
  const activeTasks = formatNumber(probe.active_tasks)
  const details = [
    t('platformOps.settings.environment.workerSummary', {
      workers,
      active: activeTasks,
    }),
  ]
  return details
}

function controlPlaneNotices(): RuntimeStatusNotice[] {
  const notices: RuntimeStatusNotice[] = []
  const probes: Array<[string, ProbeRecord]> = [
    ['Nginx', nginxProbe.value],
    ['Web', webProbe.value],
    ['API', apiProbe.value],
    ['PostgreSQL', databaseProbe.value],
    ['Redis', redisProbe.value],
    ['Scheduler', schedulerProbe.value],
  ]
  probes.forEach(([name, probe]) => {
    const notice = noticeForProbe(probe)
    if (notice) notices.push({ ...notice, message: `${name}: ${notice.message}` })
  })
  return notices
}

function schedulerDetails(probe: ProbeRecord): string[] {
  if (probe.heartbeat_age_seconds === undefined) return []
  return [t('platformOps.settings.environment.heartbeatAgo', { n: formatNumber(probe.heartbeat_age_seconds) })]
}

function databaseDetails(probe: ProbeRecord): string[] {
  const details: string[] = []
  const engine = formatDatabaseEngine(probe.engine)
  const host = formatHost(probe)
  if (engine !== '—' && host) {
    details.push(t('platformOps.settings.environment.databaseSummary', { engine, endpoint: host }))
  }
  else if (engine !== '—') details.push(engine)
  else if (host) details.push(host)
  if (probe.latency_ms !== undefined) {
    details.push(t('platformOps.settings.environment.queryLatency', {
      value: formatLatency(probe.latency_ms),
    }))
  }
  return details.filter((value) => value !== '—')
}

function redisDetails(probe: ProbeRecord): string[] {
  const details = [t('platformOps.settings.environment.redisRoles')]
  if (probe.latency_ms !== undefined) {
    details.push(t('platformOps.settings.environment.pingLatency', {
      value: formatLatency(probe.latency_ms),
    }))
  }
  return details
}

async function load() {
  busy.value = true
  try {
    payload.value = await fetchPlatformEnvironment()
  } catch (err) {
    payload.value = null
    ElMessage.error({ message: apiErrorMessage(err, t('platformOps.settings.loadFailed')), grouping: true })
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <ModulePage
    :menus="sideNav"
    body-fill
  >
    <div
      v-loading="busy"
      class="platform-settings platform-settings--stacked platform-settings--runtime"
    >
      <div
        v-if="payload"
        class="platform-settings__stack"
      >
        <section class="platform-settings__panel">
          <header class="platform-settings__panel-head">
            <h3>{{ t('platformOps.settings.environment.overviewTitle') }}</h3>
          </header>
          <div class="platform-settings__overview-grid">
            <div class="platform-settings__overview-item">
              <span class="platform-settings__overview-label">{{ t('platformOps.settings.environment.appVersion') }}</span>
              <span
                class="platform-settings__value platform-settings__value--mono"
                :class="{ 'platform-settings__value--empty': !payload.app_version }"
              >{{ payload.app_version || '—' }}</span>
            </div>
            <div class="platform-settings__overview-item">
              <span class="platform-settings__overview-label">{{ t('platformOps.settings.environment.instanceHealth') }}</span>
              <el-tag
                size="small"
                :type="statusTone(instanceHealth)"
                effect="plain"
              >
                {{ statusLabel(instanceHealth) }}
              </el-tag>
            </div>
            <div class="platform-settings__overview-item">
              <span class="platform-settings__overview-label">{{ t('platformOps.settings.environment.agentVersion') }}</span>
              <span
                class="platform-settings__value platform-settings__value--mono"
                :class="{ 'platform-settings__value--empty': !payload.agent_version }"
              >{{ payload.agent_version || '—' }}</span>
            </div>
            <div class="platform-settings__overview-item">
              <span class="platform-settings__overview-label">{{ t('platformOps.settings.environment.djangoDebug') }}</span>
              <el-tag
                size="small"
                :type="payload.django_debug ? 'warning' : 'info'"
                effect="plain"
              >
                {{ payload.django_debug ? t('platformOps.settings.environment.debugOn') : t('platformOps.settings.environment.debugOff') }}
              </el-tag>
            </div>
            <div class="platform-settings__overview-item">
              <span class="platform-settings__overview-label">{{ t('platformOps.settings.environment.edition') }}</span>
              <span class="platform-settings__value">{{ editionLabel }}</span>
            </div>
            <div class="platform-settings__overview-item">
              <span class="platform-settings__overview-label">{{ t('platformOps.settings.environment.checkedAt') }}</span>
              <span class="platform-settings__value">{{ formatLocalDateTime(checkedAt, '—') }}</span>
            </div>
          </div>
        </section>

        <section class="platform-settings__panel">
          <header class="platform-settings__panel-head">
            <h3>{{ t('platformOps.settings.environment.controlPlaneTitle') }}</h3>
          </header>
          <RuntimeStatusTable :rows="[controlPlaneOverviewRow, ...controlPlaneRows]" />
        </section>

        <RuntimeServiceConnections :data-gateway="dataGatewayProbe" />
      </div>
    </div>
  </ModulePage>
</template>

<style scoped>
.platform-settings--runtime {
  width: 100%;
  min-height: 100%;
  overflow-y: auto;
}

.platform-settings--runtime .platform-settings__value {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  color: var(--color-text-title, #1c1c26);
  font-size: 13px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.platform-settings--runtime .platform-settings__value--mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", sans-serif;
}

.platform-settings--runtime .platform-settings__value--empty {
  color: var(--color-text-secondary, #70707e);
  font-family: inherit;
  font-weight: 400;
}

.platform-settings--runtime .platform-settings__overview-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.platform-settings--runtime .platform-settings__overview-item {
  display: grid;
  grid-template-columns: minmax(9rem, 1fr) minmax(0, 1.15fr);
  gap: 12px;
  align-items: center;
  min-width: 0;
  min-height: 64px;
  padding: 14px 24px;
  border-bottom: 1px solid #e5e6eb;
  transition: background-color 0.12s ease;
}

.platform-settings--runtime .platform-settings__overview-item:hover {
  background-color: rgba(15, 23, 42, 0.08);
}

.platform-settings--runtime .platform-settings__overview-item:nth-child(even) {
  border-left: 1px solid #e5e6eb;
}

.platform-settings--runtime .platform-settings__overview-label {
  min-width: 0;
  color: var(--color-text-title, #1c1c26);
  font-size: 13px;
  line-height: 1.35;
}

.platform-settings--runtime .platform-settings__overview-item > .el-tag {
  justify-self: start;
  width: auto;
  min-width: 0;
  background: transparent !important;
  font-size: 12px;
  font-weight: 500;
}

.platform-settings--runtime .platform-settings__overview-item > .el-tag--info {
  border-color: #bfdbfe;
  color: #2563eb;
}

.platform-settings--runtime .platform-settings__overview-item > .el-tag--success {
  border-color: #86efac;
  color: #15803d;
}

.platform-settings--runtime .platform-settings__overview-item > .el-tag--warning {
  border-color: #fcd34d;
  color: #b45309;
}

.platform-settings--runtime .platform-settings__overview-item > .el-tag--danger {
  border-color: #fca5a5;
  color: #b91c1c;
}

@media (max-width: 720px) {
  .platform-settings--runtime .platform-settings__overview-grid {
    grid-template-columns: 1fr;
  }

  .platform-settings--runtime .platform-settings__overview-item:nth-child(even) {
    border-left: 0;
  }
}

</style>
