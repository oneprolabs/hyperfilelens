<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import ModulePage from '../../../../components/ModulePage.vue'
import PlatformOpsDetailSection from '../../../components/PlatformOpsDetailSection.vue'
import PlatformOpsRefreshButton from '../../../components/PlatformOpsRefreshButton.vue'
import { useResolvedPlatformOpsSideNav } from '../../../composables/useResolvedPlatformOpsSideNav'
import { fetchPlatformEnvironment, type PlatformEnvironmentSettings } from '../../../lib/platformOpsApi'
import { apiErrorMessage } from '../../../../lib/api'

const { t } = useI18n()
const sideNav = useResolvedPlatformOpsSideNav()

const busy = ref(false)
const payload = ref<PlatformEnvironmentSettings | null>(null)

const effectiveEntries = computed(() =>
  Object.entries(payload.value?.effective || {}).map(([key, value]) => ({
    key,
    label: humanizeKey(key),
    value: formatValue(value),
  })),
)

const sourceEntries = computed(() =>
  Object.entries(payload.value?.sources || {}).map(([key, value]) => ({
    key,
    label: humanizeKey(key),
    value: formatSource(value),
  })),
)

const health = computed(() => (payload.value?.health || {}) as Record<string, unknown>)

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'Enabled' : 'Disabled'
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function humanizeKey(key: string): string {
  return key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[._-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function formatSource(value: unknown): string {
  const source = String(value || '').trim().toLowerCase()
  if (source === 'deployment' || source === 'environment' || source === 'env') return 'Deployment environment'
  if (source === 'runtime' || source === 'database') return 'Admin Console override'
  if (source === 'default') return 'Release default'
  return humanizeKey(String(value || '')) || '—'
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
        <PlatformOpsDetailSection :title="t('platformOps.settings.environmentTitle')">
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
              <span class="hfl-detail-row__value">{{ payload.django_debug ? t('common.yes') : t('common.no') }}</span>
            </div>
          </div>
        </PlatformOpsDetailSection>

        <PlatformOpsDetailSection :title="t('platformOps.settings.environment.effectiveTitle')">
          <div
            v-if="effectiveEntries.length"
            class="hfl-detail-grid"
          >
            <div
              v-for="entry in effectiveEntries"
              :key="entry.key"
              class="hfl-detail-row hfl-detail-row--full"
            >
              <span class="hfl-detail-row__label platform-env__label"><strong>{{ entry.label }}</strong><code>{{ entry.key }}</code></span>
              <span class="hfl-detail-row__value hfl-detail-row__value--mono hfl-detail-row__value--break">{{ entry.value }}</span>
            </div>
          </div>
          <el-empty
            v-else
            :description="t('platformOps.settings.environment.emptyEffective')"
            :image-size="80"
          />
        </PlatformOpsDetailSection>

        <PlatformOpsDetailSection :title="t('platformOps.settings.environment.sourcesTitle')">
          <div
            v-if="sourceEntries.length"
            class="hfl-detail-grid"
          >
            <div
              v-for="entry in sourceEntries"
              :key="entry.key"
              class="hfl-detail-row hfl-detail-row--full"
            >
              <span class="hfl-detail-row__label platform-env__label"><strong>{{ entry.label }}</strong><code>{{ entry.key }}</code></span>
              <span class="hfl-detail-row__value">{{ entry.value }}</span>
            </div>
          </div>
          <el-empty
            v-else
            :description="t('platformOps.settings.environment.emptySources')"
            :image-size="80"
          />
        </PlatformOpsDetailSection>

        <PlatformOpsDetailSection :title="t('platformOps.settings.environment.healthTitle')">
          <div
            v-if="Object.keys(health).length"
            class="hfl-detail-grid"
          >
            <div
              v-for="(value, key) in health"
              :key="key"
              class="hfl-detail-row hfl-detail-row--full"
            >
              <span class="hfl-detail-row__label platform-env__label"><strong>{{ humanizeKey(String(key)) }}</strong><code>{{ String(key) }}</code></span>
              <span
                class="hfl-detail-row__value hfl-detail-row__value--mono"
                :class="{ 'hfl-detail-row__empty': formatValue(value) === '—' }"
              >{{ formatValue(value) }}</span>
            </div>
          </div>
          <el-empty
            v-else
            :description="t('platformOps.settings.environment.emptyHealth')"
            :image-size="80"
          />
        </PlatformOpsDetailSection>
      </div>
    </div>
  </ModulePage>
</template>

<style scoped>
.platform-env__label {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.platform-env__label strong {
  color: var(--color-text-title, #1d2129);
  font-size: 13px;
  font-weight: 600;
}

.platform-env__label code {
  overflow-wrap: anywhere;
  color: #64748b;
  font-size: 11px;
  font-weight: 400;
}

@media (max-width: 640px) {
  .platform-env :deep(.hfl-detail-row) {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }

  .platform-env :deep(.hfl-detail-row__value) {
    min-width: 0;
    text-align: left;
    overflow-wrap: anywhere;
  }
}
</style>
