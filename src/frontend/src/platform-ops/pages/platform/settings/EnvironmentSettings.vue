<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import ModulePage from '../../../../components/ModulePage.vue'
import { apiErrorMessage } from '../../../../lib/api'
import { formatLocalDateTime } from '../../../../lib/dateTime'
import PlatformOpsDetailSection from '../../../components/PlatformOpsDetailSection.vue'
import PlatformOpsRefreshButton from '../../../components/PlatformOpsRefreshButton.vue'
import RuntimeServiceConnections from '../../../components/RuntimeServiceConnections.vue'
import { useResolvedPlatformOpsSideNav } from '../../../composables/useResolvedPlatformOpsSideNav'
import { fetchPlatformEnvironment, type PlatformEnvironmentSettings } from '../../../lib/platformOpsApi'

type TagType = 'success' | 'warning' | 'danger' | 'info'
type ProbeRecord = Record<string, unknown>
type DetailRow = {
  key: string
  label: string
  value: string
  kind?: 'text' | 'status' | 'mono'
  full?: boolean
}

const { t } = useI18n()
const sideNav = useResolvedPlatformOpsSideNav()

const busy = ref(false)
const payload = ref<PlatformEnvironmentSettings | null>(null)

const health = computed(() => (payload.value?.health || {}) as ProbeRecord)
const apiProbe = computed(() => asProbe(health.value.api))
const databaseProbe = computed(() => asProbe(health.value.database))
const redisProbe = computed(() => asProbe(health.value.redis))
const celeryProbe = computed(() => asProbe(health.value.celery))
const sourceLensProbe = computed(() => asProbe(health.value.sourcelens))
const gatewayProbe = computed(() => asProbe(health.value.gateway))
const checkedAt = computed(() => {
  const raw = health.value.checked_at
  return typeof raw === 'string' ? raw : ''
})

const instanceHealth = computed(() =>
  aggregateStatus([
    probeStatus(apiProbe.value),
    probeStatus(databaseProbe.value),
    probeStatus(redisProbe.value),
    probeStatus(celeryProbe.value),
    probeStatus(sourceLensProbe.value),
    probeStatus(gatewayProbe.value),
  ]),
)

const healthRows = computed<DetailRow[]>(() => {
  const rows: DetailRow[] = [
    ...probeDetailRows('api', t('platformOps.settings.environment.probeApi'), apiProbe.value),
    ...probeDetailRows('database', t('platformOps.settings.environment.probeDatabase'), databaseProbe.value, [
      'latency',
      'engine',
      'host',
    ]),
    ...probeDetailRows('redis', t('platformOps.settings.environment.probeRedis'), redisProbe.value, ['latency']),
    ...probeDetailRows('celery', t('platformOps.settings.environment.probeCelery'), celeryProbe.value, [
      'workers',
      'activeTasks',
      'backlog',
    ]),
    ...probeDetailRows(
      'sourcelens',
      t('platformOps.settings.environment.probeSourceLens'),
      sourceLensProbe.value,
      ['endpoint', 'warning'],
    ),
    ...probeDetailRows(
      'gateway',
      t('platformOps.settings.environment.probeGateway'),
      gatewayProbe.value,
      ['endpoint', 'warning'],
    ),
  ]
  rows.push({
    key: 'checked-at',
    label: t('platformOps.settings.environment.checkedAt'),
    value: formatLocalDateTime(checkedAt.value, '—'),
    full: true,
  })
  return rows
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

function statusTone(status: string): TagType {
  if (status === 'ok') return 'success'
  if (status === 'degraded') return 'warning'
  if (status === 'error') return 'danger'
  return 'info'
}

function statusLabel(status: string): string {
  if (status === 'ok') return t('platformOps.settings.environment.statusHealthy')
  if (status === 'degraded') return t('platformOps.settings.environment.statusDegraded')
  if (status === 'error') return t('platformOps.settings.environment.statusUnavailable')
  return t('platformOps.settings.environment.statusUnknown')
}

function aggregateStatus(statuses: string[]): string {
  if (!statuses.length) return 'unknown'
  if (statuses.some((status) => status === 'error')) return 'error'
  if (statuses.some((status) => status === 'degraded' || status === 'unknown')) return 'degraded'
  if (statuses.every((status) => status === 'ok')) return 'ok'
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

function formatBacklog(value: unknown): string {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const status = String((value as ProbeRecord).status || '').trim().toLowerCase()
    if (status) return statusLabel(status)
  }
  return ''
}

function backlogWarnings(value: unknown): string[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  const warnings = (value as ProbeRecord).warnings
  if (!Array.isArray(warnings)) return []
  return warnings.map((item) => String(item || '').trim()).filter(Boolean)
}

function probeDetailRows(
  key: string,
  title: string,
  probe: ProbeRecord,
  extras: Array<
    'latency' | 'engine' | 'host' | 'workers' | 'activeTasks' | 'backlog' | 'endpoint' | 'warning'
  > = [],
): DetailRow[] {
  if (!Object.keys(probe).length) return []
  const rows: DetailRow[] = [
    {
      key: `${key}-status`,
      label: `${title} · ${t('platformOps.settings.environment.status')}`,
      value: probeStatus(probe),
      kind: 'status',
    },
  ]
  const error = String(probe.error || probe.message || '').trim()
  if (error) {
    rows.push({
      key: `${key}-detail`,
      label: `${title} · ${t('platformOps.settings.environment.detail')}`,
      value: error,
      full: true,
    })
  }
  for (const extra of extras) {
    if (extra === 'latency' && probe.latency_ms !== undefined && probe.latency_ms !== null) {
      rows.push({
        key: `${key}-latency`,
        label: `${title} · ${t('platformOps.settings.environment.latency')}`,
        value: formatLatency(probe.latency_ms),
      })
    }
    if (extra === 'engine') {
      const engine = formatText(probe.engine)
      if (engine !== '—') {
        rows.push({
          key: `${key}-engine`,
          label: `${title} · ${t('platformOps.settings.environment.engine')}`,
          value: engine,
        })
      }
    }
    if (extra === 'host') {
      const host = formatHost(probe)
      if (host) {
        rows.push({
          key: `${key}-host`,
          label: `${title} · ${t('platformOps.settings.environment.host')}`,
          value: host,
          kind: 'mono',
        })
      }
    }
    if (extra === 'workers' && probe.worker_count !== undefined && probe.worker_count !== null) {
      rows.push({
        key: `${key}-workers`,
        label: `${title} · ${t('platformOps.settings.environment.workers')}`,
        value: formatNumber(probe.worker_count),
      })
    }
    if (extra === 'activeTasks' && probe.active_tasks !== undefined && probe.active_tasks !== null) {
      rows.push({
        key: `${key}-active-tasks`,
        label: `${title} · ${t('platformOps.settings.environment.activeTasks')}`,
        value: formatNumber(probe.active_tasks),
      })
    }
    if (extra === 'backlog' && probe.backlog !== undefined && probe.backlog !== null) {
      const backlog = formatBacklog(probe.backlog)
      if (backlog) {
        rows.push({
          key: `${key}-backlog`,
          label: `${title} · ${t('platformOps.settings.environment.backlog')}`,
          value: backlog,
          kind: 'status',
        })
      }
      backlogWarnings(probe.backlog).forEach((warning, index) => {
        rows.push({
          key: `${key}-backlog-warning-${index}`,
          label: `${title} · ${t('platformOps.settings.environment.backlogWarning')}`,
          value: warning,
          full: true,
        })
      })
    }
    if (extra === 'endpoint') {
      const endpoint = String(probe.base_url || '').trim()
      if (endpoint) {
        rows.push({
          key: `${key}-endpoint`,
          label: `${title} · ${t('platformOps.settings.environment.endpoint')}`,
          value: endpoint,
          kind: 'mono',
          full: true,
        })
      } else if (probe.configured === false) {
        rows.push({
          key: `${key}-endpoint`,
          label: `${title} · ${t('platformOps.settings.environment.endpoint')}`,
          value: t('platformOps.settings.environment.notConfigured'),
          full: true,
        })
      }
    }
    if (extra === 'warning') {
      const warning = String(probe.warning || '').trim()
      if (warning) {
        rows.push({
          key: `${key}-warning`,
          label: `${title} · ${t('platformOps.settings.environment.detail')}`,
          value: warning,
          full: true,
        })
      }
    }
  }
  return rows
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
      class="platform-env"
    >
      <div class="platform-env__toolbar">
        <PlatformOpsRefreshButton
          :loading="busy"
          @click="load"
        />
      </div>

      <div
        v-if="payload"
        class="hfl-detail-sections"
      >
        <PlatformOpsDetailSection :title="t('platformOps.settings.environment.summaryTitle')">
          <div class="hfl-detail-grid">
            <div class="hfl-detail-row">
              <span class="hfl-detail-row__label">{{ t('platformOps.settings.environment.appVersion') }}</span>
              <span
                class="hfl-detail-row__value hfl-detail-row__value--mono"
                :class="{ 'hfl-detail-row__empty': !payload.app_version }"
              >{{ payload.app_version || '—' }}</span>
            </div>
            <div class="hfl-detail-row">
              <span class="hfl-detail-row__label">{{ t('platformOps.settings.environment.agentVersion') }}</span>
              <span
                class="hfl-detail-row__value hfl-detail-row__value--mono"
                :class="{ 'hfl-detail-row__empty': !payload.agent_version }"
              >{{ payload.agent_version || '—' }}</span>
            </div>
            <div class="hfl-detail-row">
              <span class="hfl-detail-row__label">{{ t('platformOps.settings.environment.djangoDebug') }}</span>
              <span class="hfl-detail-row__value">
                <el-tag
                  size="small"
                  :type="payload.django_debug ? 'warning' : 'info'"
                  effect="plain"
                >
                  {{ payload.django_debug ? t('platformOps.settings.environment.debugOn') : t('platformOps.settings.environment.debugOff') }}
                </el-tag>
              </span>
            </div>
            <div class="hfl-detail-row">
              <span class="hfl-detail-row__label">{{ t('platformOps.settings.environment.instanceHealth') }}</span>
              <span class="hfl-detail-row__value">
                <el-tag
                  size="small"
                  :type="statusTone(instanceHealth)"
                  effect="plain"
                >
                  {{ statusLabel(instanceHealth) }}
                </el-tag>
              </span>
            </div>
          </div>
        </PlatformOpsDetailSection>

        <PlatformOpsDetailSection :title="t('platformOps.settings.environment.healthTitle')">
          <div class="hfl-detail-grid">
            <div
              v-for="row in healthRows"
              :key="row.key"
              class="hfl-detail-row"
              :class="{ 'hfl-detail-row--full': row.full }"
            >
              <span class="hfl-detail-row__label">{{ row.label }}</span>
              <span
                class="hfl-detail-row__value"
                :class="{
                  'hfl-detail-row__value--mono': row.kind === 'mono',
                  'hfl-detail-row__empty': row.value === '—',
                }"
              >
                <el-tag
                  v-if="row.kind === 'status'"
                  size="small"
                  :type="statusTone(row.value)"
                  effect="plain"
                >
                  {{ statusLabel(row.value) }}
                </el-tag>
                <template v-else>
                  {{ row.value }}
                </template>
              </span>
            </div>
          </div>
        </PlatformOpsDetailSection>

        <RuntimeServiceConnections />
      </div>
    </div>
  </ModulePage>
</template>

<style scoped>
.platform-env {
  width: 100%;
  min-height: 100%;
  overflow-y: auto;
}

.platform-env__toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}
</style>
