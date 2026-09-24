<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../lib/api'
import { fetchPlatformIntegrations, type PlatformIntegration } from '../lib/platformOpsApi'
import RuntimeStatusTable from './RuntimeStatusTable.vue'
import type {
  RuntimeStatusCell,
  RuntimeStatusNotice,
  RuntimeStatusRow,
} from './runtimeStatus'

type ProbeRecord = Record<string, unknown>

const { t } = useI18n()
const props = defineProps<{
  dataGateway?: ProbeRecord
}>()
const integrations = ref<PlatformIntegration[]>([])
const loading = ref(false)
const hasConnections = computed(() => integrations.value.length > 0)
const dataGateway = computed(() => props.dataGateway || {})

function integrationStatus(row: PlatformIntegration): { label: string; type: RuntimeStatusCell['type'] } {
  if (!row.configured) return { label: t('platformOps.integrations.notConfigured'), type: 'info' }
  if (!row.reachable) return { label: t('platformOps.integrations.unavailable'), type: 'danger' }
  if (!row.authenticated) return { label: t('platformOps.integrations.degraded'), type: 'warning' }
  if (row.business_ready === false) return { label: t('platformOps.integrations.degraded'), type: 'warning' }
  return { label: t('platformOps.integrations.healthy'), type: 'success' }
}

function statusCell(label: string, type: RuntimeStatusCell['type']): RuntimeStatusCell {
  return { label, type }
}

function formatDeploymentMode(mode: string): string {
  const value = mode.trim()
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : t('platformOps.integrations.notConfigured')
}

function sourceLensOverviewRow(row: PlatformIntegration): RuntimeStatusRow {
  const configured = row.configured === true
  return {
    key: `${row.key}-overview`,
    service: 'Runtime',
    runtime: statusCell(t('platformOps.settings.environment.statusNotMonitored'), 'info'),
    health: !configured
      ? statusCell(t('platformOps.settings.environment.statusNotMonitored'), 'info')
      : row.reachable
      ? statusCell(t('platformOps.integrations.healthy'), 'success')
      : statusCell(t('platformOps.settings.environment.healthUnhealthy'), 'danger'),
    availability: !configured
      ? statusCell(t('platformOps.settings.environment.statusNotConfigured'), 'info')
      : row.business_ready
      ? statusCell(t('platformOps.settings.environment.statusOperational'), 'success')
      : statusCell(t('platformOps.settings.environment.statusUnavailable'), 'danger'),
    details: [
      `${t('platformOps.settings.environment.deployment')}: ${formatDeploymentMode(row.mode || '')}`,
      `${t('platformOps.integrations.version')}: ${row.version || t('platformOps.integrations.unknown')}`,
      ...(row.console_url ? [`${t('platformOps.integrations.consoleUrl')}: ${row.console_url}`] : []),
    ],
    notices: row.warning
      ? [{ message: row.warning, level: 'warning' }]
      : [],
  }
}

function sourceLensContainerRows(row: PlatformIntegration): RuntimeStatusRow[] {
  if (row.mode !== 'bundled' || !row.configured) return []
  const aggregate = integrationStatus(row)
  const containers = [
    ['sourcelens-nginx', 'Nginx', t('platformOps.integrations.containerNginx')],
    ['sourcelens-web', 'Web', t('platformOps.integrations.containerWeb')],
    ['sourcelens-api', 'API', t('platformOps.integrations.containerApi')],
    ['sourcelens-worker', 'Worker', t('platformOps.integrations.containerWorker')],
    ['sourcelens-scheduler', 'Scheduler', t('platformOps.integrations.containerScheduler')],
    ['sourcelens-postgres', 'PostgreSQL', t('platformOps.integrations.containerPostgres')],
    ['sourcelens-redis', 'Redis', t('platformOps.integrations.containerRedis')],
  ]
  return containers.map(([service, displayName, details]) => {
    const monitored = service === 'sourcelens-api'
    const status = monitored
      ? aggregate.type === 'success'
        ? 'ok'
        : aggregate.type === 'warning'
          ? 'degraded'
          : aggregate.type === 'danger'
            ? 'error'
            : 'not_monitored'
      : 'not_monitored'
    return {
      key: `${row.key}-${service}`,
      service: displayName,
      runtime: statusCell(t('platformOps.settings.environment.statusNotMonitored'), 'info'),
      health: status === 'ok'
        ? statusCell(t('platformOps.integrations.healthy'), 'success')
        : status === 'degraded'
          ? statusCell(t('platformOps.settings.environment.healthUnhealthy'), 'warning')
          : status === 'error'
            ? statusCell(t('platformOps.settings.environment.statusUnavailable'), 'danger')
            : statusCell(t('platformOps.settings.environment.statusNotMonitored'), 'info'),
      availability: status === 'ok'
        ? statusCell(t('platformOps.settings.environment.statusOperational'), 'success')
        : status === 'degraded'
          ? statusCell(t('platformOps.settings.environment.statusDegraded'), 'warning')
          : status === 'error'
            ? statusCell(t('platformOps.settings.environment.statusUnavailable'), 'danger')
            : statusCell('—', 'info'),
      details: [details],
    }
  })
}

function dataGatewayStatus(): RuntimeStatusCell {
  const status = String(dataGateway.value.status || '').trim().toLowerCase()
  if (status === 'ok') return statusCell(t('platformOps.settings.environment.statusOperational'), 'success')
  if (status === 'degraded') return statusCell(t('platformOps.settings.environment.statusDegraded'), 'warning')
  if (status === 'error') return statusCell(t('platformOps.settings.environment.statusUnavailable'), 'danger')
  return statusCell(t('platformOps.settings.environment.statusNotConfigured'), 'info')
}

function dataGatewayOverviewRows(): RuntimeStatusRow[] {
  const value = dataGateway.value
  const configured = value.configured === true
  return [{
    key: 'data-gateway-overview',
    service: 'Runtime',
    runtime: !configured
      ? statusCell(t('platformOps.settings.environment.statusNotMonitored'), 'info')
      : statusCell(
      value.agent_online ? t('platformOps.settings.environment.online') : t('platformOps.settings.environment.offline'),
      value.agent_online ? 'success' : 'danger',
    ),
    health: !configured
      ? statusCell(t('platformOps.settings.environment.statusNotMonitored'), 'info')
      : statusCell(
      value.endpoint_reachable ? t('platformOps.integrations.healthy') : t('platformOps.settings.environment.statusNotMonitored'),
      value.endpoint_reachable ? 'success' : 'info',
    ),
    availability: dataGatewayStatus(),
    details: dataGatewayDetails(),
    notices: dataGatewayNotices(),
  }]
}

function dataGatewayComponentRows(): RuntimeStatusRow[] {
  const value = dataGateway.value
  const configured = value.configured === true
  return [
    {
      key: 'data-gateway-agent',
      service: 'Agent',
      runtime: !configured
        ? statusCell(t('platformOps.settings.environment.statusNotMonitored'), 'info')
        : statusCell(
        value.agent_online ? t('platformOps.settings.environment.online') : t('platformOps.settings.environment.offline'),
        value.agent_online ? 'success' : 'danger',
      ),
      health: statusCell(t('platformOps.settings.environment.statusNotMonitored'), 'info'),
      availability: statusCell(
        !configured
          ? t('platformOps.settings.environment.statusNotConfigured')
          : value.agent_online ? t('platformOps.settings.environment.statusOperational') : t('platformOps.settings.environment.statusUnavailable'),
        !configured ? 'info' : value.agent_online ? 'success' : 'danger',
      ),
      details: [
        t('platformOps.settings.environment.platformGatewayRole'),
      ],
      notices: !configured || value.agent_online
        ? []
        : [{
          message: t('platformOps.settings.environment.gatewayAgentOffline'),
          level: 'error',
        }],
    },
    {
      key: 'data-gateway-lensnode',
      service: 'LensNode',
      runtime: !configured
        ? statusCell(t('platformOps.settings.environment.statusNotMonitored'), 'info')
        : statusCell(
        value.lensnode_online ? t('platformOps.settings.environment.runtimeRunning') : t('platformOps.settings.environment.runtimeStopped'),
        value.lensnode_online ? 'success' : 'danger',
      ),
      health: statusCell(t('platformOps.settings.environment.statusNotMonitored'), 'info'),
      availability: statusCell(
        !configured
          ? t('platformOps.settings.environment.statusNotConfigured')
          : value.copilot_ready ? t('platformOps.settings.environment.statusOperational') : t('platformOps.settings.environment.statusUnavailable'),
        !configured ? 'info' : value.copilot_ready ? 'success' : 'danger',
      ),
      details: [
        t('platformOps.settings.environment.gatewayLensNodeDetail'),
      ],
      notices: !configured || value.lensnode_online
        ? []
        : [{
          message: t('platformOps.settings.environment.gatewayLensNodeOffline'),
          level: 'error',
        }],
    },
  ]
}

function dataGatewayDetails(): string[] {
  const value = dataGateway.value
  return [
    `${t('platformOps.settings.environment.deployment')}: ${String(value.deployment || '—')}`,
    ...(value.name ? [`${t('platformOps.settings.environment.gatewayName')}: ${value.name}`] : []),
    `${t('platformOps.settings.environment.agentVersion')}: ${String(value.agent_version || '—')}`,
  ]
}

function dataGatewayNotices(): RuntimeStatusNotice[] {
  const value = dataGateway.value
  if (!value.configured) {
    return [{
      message: t('platformOps.settings.environment.gatewayNotConfigured'),
      level: 'info',
    }]
  }
  if (value.warning) {
    return [{ message: String(value.warning), level: 'warning' }]
  }
  if (!value.agent_online) {
    return [{
      message: t('platformOps.settings.environment.gatewayAgentOffline'),
      level: 'error',
    }]
  }
  if (!value.lensnode_online) {
    return [{
      message: t('platformOps.settings.environment.gatewayLensNodeOffline'),
      level: 'error',
    }]
  }
  return []
}

async function load() {
  loading.value = true
  try {
    integrations.value = (await fetchPlatformIntegrations()).integrations || []
  } catch (error) {
    ElMessage.error({ message: apiErrorMessage(error, t('platformOps.integrations.loadFailed')), grouping: true })
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
<template>
  <div class="runtime-service-connections">
    <div
      v-loading="loading"
      class="runtime-service-connections__body"
    >
      <template v-if="hasConnections">
        <div
          v-for="row in integrations"
          :key="row.key"
          class="platform-settings__panel runtime-service-connections__group"
        >
          <header class="platform-settings__panel-head">
            <h3>{{ row.name }}</h3>
          </header>
          <RuntimeStatusTable :rows="[sourceLensOverviewRow(row)]" />
          <div
            v-if="sourceLensContainerRows(row).length"
            class="runtime-service-connections__containers"
          >
            <RuntimeStatusTable
              :rows="sourceLensContainerRows(row)"
              hide-header
            />
          </div>
        </div>
      </template>

      <div
        v-if="Object.keys(dataGateway).length"
        class="platform-settings__panel runtime-service-connections__group"
      >
        <header class="platform-settings__panel-head">
          <h3>{{ t('platformOps.settings.environment.dataGatewayTitle') }}</h3>
        </header>
        <RuntimeStatusTable :rows="dataGatewayOverviewRows()" />
        <div class="runtime-service-connections__containers">
          <RuntimeStatusTable
            :rows="dataGatewayComponentRows()"
            hide-header
          />
        </div>
      </div>

      <el-empty
        v-else-if="!loading && !Object.keys(dataGateway).length"
        :description="t('platformOps.integrations.empty')"
        :image-size="72"
      />
    </div>
  </div>
</template>

<style scoped>
.runtime-service-connections__body {
  min-height: 72px;
}

.runtime-service-connections__group + .runtime-service-connections__group {
  margin-top: 16px;
}

.runtime-service-connections__containers {
  border-top: 1px solid #e5e6eb;
  background: rgba(248, 250, 252, 0.62);
}

</style>
