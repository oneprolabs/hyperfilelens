<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { ExternalLink, RefreshCw } from 'lucide-vue-next'
import { apiErrorMessage } from '../../lib/api'
import { formatLocalDateTime } from '../../lib/dateTime'
import PlatformOpsDetailSection from './PlatformOpsDetailSection.vue'
import { fetchPlatformIntegrations, type PlatformIntegration } from '../lib/platformOpsApi'

const { t } = useI18n()
const integrations = ref<PlatformIntegration[]>([])
const loading = ref(false)
const hasConnections = computed(() => integrations.value.length > 0)

function integrationStatus(row: PlatformIntegration): { label: string; type: 'success' | 'warning' | 'danger' | 'info' } {
  if (!row.configured) return { label: t('platformOps.integrations.notConfigured'), type: 'info' }
  if (!row.reachable) return { label: t('platformOps.integrations.unavailable'), type: 'danger' }
  if (!row.authenticated) return { label: t('platformOps.integrations.degraded'), type: 'warning' }
  if (row.business_ready === false) return { label: t('platformOps.integrations.degraded'), type: 'warning' }
  return { label: t('platformOps.integrations.healthy'), type: 'success' }
}

function connectionRows(row: PlatformIntegration): Array<{ label: string; value: string; mono?: boolean }> {
  const rows: Array<{ label: string; value: string; mono?: boolean }> = [
    {
      label: t('platformOps.integrations.deploymentMode'),
      value: row.mode || t('platformOps.integrations.notConfigured'),
    },
    {
      label: t('platformOps.integrations.version'),
      value: row.version || t('platformOps.integrations.unknown'),
    },
  ]
  if (row.base_url) {
    rows.push({
      label: t('platformOps.integrations.serviceEndpoint'),
      value: row.base_url,
      mono: true,
    })
  }
  if (row.gateway_base_url) {
    rows.push({
      label: t('platformOps.integrations.gatewayEndpoint'),
      value: row.gateway_base_url,
      mono: true,
    })
  }
  if (row.console_url) {
    rows.push({
      label: t('platformOps.integrations.consoleUrl'),
      value: row.console_url,
      mono: true,
    })
  }
  rows.push(
    {
      label: t('platformOps.integrations.connectivity'),
      value: row.reachable
        ? t('platformOps.integrations.reachable')
        : t('platformOps.integrations.unavailable'),
    },
    {
      label: t('platformOps.integrations.authentication'),
      value: row.authenticated
        ? t('platformOps.integrations.authenticated')
        : t('platformOps.integrations.unavailable'),
    },
    {
      label: t('platformOps.integrations.lastChecked'),
      value: formatLocalDateTime(row.checked_at, '—'),
    },
  )
  return rows
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
  <PlatformOpsDetailSection :title="t('platformOps.integrations.title')">
    <div class="runtime-service-connections">
      <div class="runtime-service-connections__lead">
        <p>{{ t('platformOps.integrations.subtitle') }}</p>
        <el-button
          class="hfl-refresh-button"
          :title="t('common.refresh')"
          :aria-label="t('common.refresh')"
          :disabled="loading"
          @click="load"
        >
          <RefreshCw
            :size="16"
            :class="{ 'is-spinning': loading }"
          />
        </el-button>
      </div>

      <div
        v-loading="loading"
        class="runtime-service-connections__body"
      >
        <template v-if="hasConnections">
          <div
            v-for="row in integrations"
            :key="row.key"
            class="runtime-service-connections__group"
          >
            <div class="runtime-service-connections__group-head">
              <span class="runtime-service-connections__name">{{ row.name }}</span>
              <el-tag
                size="small"
                :type="integrationStatus(row).type"
                effect="plain"
              >
                {{ integrationStatus(row).label }}
              </el-tag>
            </div>

            <div class="hfl-detail-grid">
              <div
                v-for="item in connectionRows(row)"
                :key="`${row.key}-${item.label}`"
                class="hfl-detail-row"
              >
                <span class="hfl-detail-row__label">{{ item.label }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{
                    'hfl-detail-row__value--mono': item.mono,
                    'hfl-detail-row__empty': item.value === '—',
                  }"
                >{{ item.value }}</span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('platformOps.integrations.managedBy') }}</span>
                <span class="hfl-detail-row__value runtime-service-connections__managed">
                  <span>{{ t('platformOps.integrations.managedHint') }}</span>
                  <a
                    v-if="row.console_url"
                    :href="row.console_url"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {{ t('platformOps.integrations.openConsole', { name: row.name }) }}
                    <ExternalLink :size="14" />
                  </a>
                </span>
              </div>
            </div>
          </div>
        </template>

        <el-empty
          v-else-if="!loading"
          :description="t('platformOps.integrations.empty')"
          :image-size="72"
        />
      </div>
    </div>
  </PlatformOpsDetailSection>
</template>

<style scoped>
.runtime-service-connections__lead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.runtime-service-connections__lead p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.55;
}

.runtime-service-connections__body {
  min-height: 72px;
}

.runtime-service-connections__group + .runtime-service-connections__group {
  border-top: 1px solid var(--el-border-color-lighter);
}

.runtime-service-connections__group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  background: var(--el-fill-color-lighter);
}

.runtime-service-connections__name {
  color: var(--color-text-title);
  font-size: 13px;
  font-weight: 600;
}

.runtime-service-connections__managed {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 16px;
}

.runtime-service-connections__managed a {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--color-primary);
  text-decoration: none;
  font-size: 13px;
}
</style>
