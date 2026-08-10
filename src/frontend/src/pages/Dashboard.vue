<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  AlertCircle,
  AlertTriangle,
  ArrowUpRight,
  Calculator,
  CreditCard,
  Database,
  History,
  Server,
  ShieldCheck,
} from 'lucide-vue-next'
import DashboardIdleGearIllustration from '../components/dashboard/DashboardIdleGearIllustration.vue'
import DashboardIdleShieldIllustration from '../components/dashboard/DashboardIdleShieldIllustration.vue'
import { ElInputNumber, ElOption, ElSelect, ElTooltip } from 'element-plus'
import ErrorState from '../components/errors/ErrorState.vue'
import { formatLocalDateTime } from '../lib/dateTime'
import {
  loadDashboardOverview,
  type DashboardOverview,
  type RepoUsageRow,
  type TaskDayBucket,
} from '../lib/dashboardApi'
import type { TaskRow } from '../lib/taskApi'

const { t, te, locale } = useI18n()

const loading = ref(false)
const loadError = ref<unknown>(null)
const overview = ref<DashboardOverview | null>(null)
const chartHoveredIdx = ref<number | null>(null)
const capacityPlanAmount = ref(1)
const capacityPlanUnit = ref<'GB' | 'TB'>('TB')
const capacityPlanFactor = ref(1)
const capacityPlanSafePct = 80

const routes = {
  sources: '/protection/backup-sources?tab=host',
  policies: '/protection/policies',
  repositories: '/node/repositories',
  tasks: '/ops/task',
  attention: '/ops/attention',
  audit: '/ops/audit',
  subscription: '/node/subscription',
} as const

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let v = bytes
  let u = 0
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024
    u += 1
  }
  return `${v.toFixed(v >= 10 || u === 0 ? 0 : 1)} ${units[u]}`
}

function formatGB(bytes: number): string {
  if (!bytes) return '0 GB'
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(bytes >= 10 * 1024 ** 3 ? 0 : 1)} GB`
}

function formatPct(value: number): string {
  return `${Math.min(999, Math.max(0, Math.round(value * 10) / 10))}%`
}

function repoCapacityLabel(repo: RepoUsageRow) {
  if (repo.capacityMode === 'known') return formatBytes(repo.capacityBytes)
  if (repo.capacityMode === 'pending') return t('dashboard.ribbon.usagePending')
  if (repo.capacityMode === 'unavailable') return t('repositoriesPage.capacityUnavailable')
  return t('dashboard.unlimited')
}

function repoHealthKind(repo: RepoUsageRow): 'online' | 'offline' | 'unverified' {
  const raw = String(repo.health || '').trim().toLowerCase()
  if (raw === 'offline' || raw === 'down' || raw === '0' || raw === 'false') return 'offline'
  if (raw === 'unverified' || raw === 'pending' || raw === 'pending_verification') return 'unverified'
  const status = String(repo.status || '').trim().toLowerCase()
  if (status === 'create_failed' || status === 'remove_failed' || status === 'offline' || status === 'failed') return 'offline'
  if (status === 'creating' || status === 'removing') return 'unverified'
  return 'online'
}

function repoHealthLabel(repo: RepoUsageRow) {
  const kind = repoHealthKind(repo)
  if (kind === 'offline') return t('repositoriesPage.healthOffline')
  if (kind === 'unverified') return t('repositoriesPage.healthUnverified')
  return t('repositoriesPage.healthOnline')
}

function formatTime(iso?: string) {
  return formatLocalDateTime(iso, '—', locale.value)
}

function taskTypeLabel(code: string) {
  const key = `ops.task.taskType.${code}` as const
  return te(key) ? t(key) : t('ops.task.unknownValue')
}

function formatQuotaLimit(limit: number) {
  if (limit < 0) return t('dashboard.unlimited')
  return String(limit)
}

function quotaPct(used: number, limit: number) {
  if (limit < 0) return 0
  if (!limit) return 0
  return Math.min(100, Math.round((used / limit) * 100))
}

const storagePct = computed(() => {
  const s = overview.value?.storage
  if (!s?.capacityBytes) return null
  return Math.min(100, Math.round((s.usedBytes / s.capacityBytes) * 1000) / 10)
})

const capacityPlanInputBytes = computed(() => {
  const amount = Number(capacityPlanAmount.value) || 0
  const factor = Number(capacityPlanFactor.value) || 1
  const unitBytes = capacityPlanUnit.value === 'TB' ? 1024 ** 4 : 1024 ** 3
  return Math.max(0, amount) * unitBytes * Math.max(0, factor)
})

const capacityPlanProjectedBytes = computed(() => {
  const used = overview.value?.storage.usedBytes ?? 0
  return used + capacityPlanInputBytes.value
})

const capacityPlanPct = computed(() => {
  const s = overview.value?.storage
  if (!s || s.capacityMode !== 'known' || s.capacityBytes <= 0) return null
  return (capacityPlanProjectedBytes.value / s.capacityBytes) * 100
})

const capacityPlanRemainingBytes = computed(() => {
  const s = overview.value?.storage
  if (!s || s.capacityMode !== 'known' || s.capacityBytes <= 0) return null
  return s.capacityBytes - capacityPlanProjectedBytes.value
})

const capacityPlanStatus = computed((): 'ok' | 'warning' | 'danger' | 'neutral' => {
  const s = overview.value?.storage
  if (!s || s.capacityMode !== 'known') return 'neutral'
  const pct = capacityPlanPct.value ?? 0
  if (pct >= 100) return 'danger'
  if (pct >= capacityPlanSafePct) return 'warning'
  return 'ok'
})

const capacityPlanStatusLabel = computed(() => {
  const s = overview.value?.storage
  if (!s || s.capacityMode === 'empty') return t('dashboard.capacityPlanner.statusNoCapacity')
  if (s.capacityMode === 'pending') return t('dashboard.capacityPlanner.statusPending')
  if (s.capacityMode === 'unlimited') return t('dashboard.capacityPlanner.statusUnlimited')
  const pct = capacityPlanPct.value ?? 0
  if (pct >= 100) return t('dashboard.capacityPlanner.statusOver')
  if (pct >= capacityPlanSafePct) return t('dashboard.capacityPlanner.statusNear')
  return t('dashboard.capacityPlanner.statusReady')
})

const capacityPlanUnavailableLabel = computed(() => {
  const mode = overview.value?.storage.capacityMode
  if (mode === 'empty') return t('dashboard.capacityPlanner.noCapacity')
  if (mode === 'pending') return t('dashboard.capacityPlanner.pendingShort')
  if (mode === 'unlimited') return t('dashboard.capacityPlanner.notEvaluated')
  return t('dashboard.capacityPlanner.notAvailable')
})

const sourceAvailabilityPct = computed(() => {
  const total = overview.value?.productionSources.total ?? 0
  if (!total) return 0
  const available = overview.value?.productionSources.available ?? 0
  return Math.min(100, Math.round((available / total) * 1000) / 10)
})

const infraSourceMainValue = computed(() => {
  const available = overview.value?.productionSources.available ?? 0
  if (available > 0) return t('dashboard.ribbon.sourceAvailable', { n: available })
  return t('dashboard.ribbon.sourceAvailableEmpty')
})

const storageTotalCapacityLabel = computed(() => {
  const s = overview.value?.storage
  if (!s || s.capacityMode === 'empty') return '—'
  if (s.capacityMode === 'known') return formatGB(s.capacityBytes)
  if (s.capacityMode === 'unlimited') return t('dashboard.unlimited')
  return t('dashboard.ribbon.usagePending')
})

const capacityPlanPrimaryMetric = computed(() => {
  const mode = overview.value?.storage.capacityMode
  if (mode === 'known') {
    return {
      label: t('dashboard.capacityPlanner.projectedRate'),
      value: capacityPlanPct.value == null ? capacityPlanUnavailableLabel.value : formatPct(capacityPlanPct.value),
    }
  }
  return {
    label: t('dashboard.capacityPlanner.estimatedAdd'),
    value: formatBytes(capacityPlanInputBytes.value),
  }
})

const capacityPlanSecondaryMetric = computed(() => {
  const mode = overview.value?.storage.capacityMode
  if (mode === 'known') {
    return {
      label: t('dashboard.capacityPlanner.remaining'),
      value: capacityPlanRemainingBytes.value == null
        ? capacityPlanUnavailableLabel.value
        : formatBytes(Math.max(0, capacityPlanRemainingBytes.value)),
    }
  }
  if (mode === 'unlimited') {
    return {
      label: t('dashboard.capacityPlanner.projectedUsage'),
      value: t('dashboard.unlimited'),
    }
  }
  return {
    label: t('dashboard.capacityPlanner.projectedUsage'),
    value: capacityPlanUnavailableLabel.value,
  }
})

const visibleStorageRepos = computed(() => overview.value?.topRepos.slice(0, 1) ?? [])

const hiddenStorageRepoCount = computed(() => {
  const total = overview.value?.storage.repoCount ?? 0
  return Math.max(0, total - visibleStorageRepos.value.length)
})

function formatRelativeTime(iso?: string) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const diffDays = Math.floor((Date.now() - d.getTime()) / (24 * 60 * 60 * 1000))
  if (diffDays <= 0) return t('dashboard.timeToday')
  return t('dashboard.timeDaysAgo', { n: diffDays })
}

const pipelineStep1Chip = computed(() => {
  const n = overview.value?.productionSources.total ?? 0
  if (n > 0) return t('dashboard.pipeline.step1Chip', { n })
  return t('dashboard.pipeline.step1ChipEmpty')
})

type StoragePipelineState = 'no_repos' | 'used' | 'ready'

const storagePipelineState = computed((): StoragePipelineState => {
  const s = overview.value?.storage
  if (!s?.repoCount) return 'no_repos'
  if (s.usedBytes > 0) return 'used'
  return 'ready'
})

const pipelineStep2Chip = computed(() => {
  const s = overview.value?.storage
  const state = storagePipelineState.value
  if (state === 'no_repos') return t('dashboard.pipeline.step2NoRepos')
  if (state === 'used' && s) return `${formatBytes(s.usedBytes)} ${t('dashboard.pipeline.step2Used')}`
  if (s) return t('dashboard.pipeline.step2Ready', { n: s.repoCount })
  return t('dashboard.pipeline.step2NoRepos')
})

const pipelineStep3Chip = computed(() => {
  const recovery = overview.value?.recovery
  if (!recovery || recovery.backupPoliciesEnabled === 0) {
    return t('dashboard.pipeline.step3ChipNoPolicy')
  }
  if (recovery.recoveryConfigCount === 0 && recovery.restorePlansEnabled === 0) {
    return t('dashboard.pipeline.step3ChipNoRecoveryPlan')
  }
  const last = recovery.lastRestore
  if (!last) return t('dashboard.pipeline.step3ChipNotDrilled')
  const time = formatRelativeTime(last.at)
  if (last.status === 'success') return t('dashboard.pipeline.step3ChipLastSuccess', { time })
  if (last.status === 'failed' || last.status === 'timeout') {
    return t('dashboard.pipeline.step3ChipLastFailed', { time })
  }
  if (last.status === 'cancelled') return t('dashboard.pipeline.step3ChipLastCancelled', { time })
  return t('dashboard.pipeline.step3ChipNotDrilled')
})

const storageRibbonBadge = computed(() => {
  const s = overview.value?.storage
  if (!s?.repoCount) return t('dashboard.ribbon.storageEmpty')
  if (storagePct.value !== null && storagePct.value >= 80) return t('dashboard.ribbon.capacityAlert')
  return ''
})

const storageRibbonBadgeVisible = computed(() => Boolean(storageRibbonBadge.value))

const storageRibbonBadgeClass = computed(() => {
  if (!overview.value?.storage?.repoCount) return 'ribbon-card__badge--amber'
  if (storagePct.value !== null && storagePct.value >= 80) return 'ribbon-card__badge--amber ribbon-card__badge--pulse'
  return 'ribbon-card__badge--amber'
})

const storageUsedProgressLabel = computed(() => {
  const s = overview.value?.storage
  const used = formatBytes(s?.usedBytes ?? 0)
  return `${t('dashboard.ribbon.usedOccupied')} ${used}`
})

const slaRibbonBadge = computed(() => {
  const firing = overview.value?.alertFiring ?? 0
  if (firing > 0) return t('dashboard.ribbon.slaAlerts', { n: firing })
  const success = overview.value?.tasks24h?.success ?? 0
  const failed = overview.value?.tasks24h?.failed ?? 0
  if (success + failed === 0) return ''
  const rate = overview.value?.tasks24h?.successRate
  if (rate != null && rate < 90) return t('dashboard.ribbon.slaAtRisk')
  return ''
})

const slaRibbonBadgeVisible = computed(() => Boolean(slaRibbonBadge.value))

const slaRibbonBadgeClass = computed(() => 'ribbon-card__badge--amber')

const quotaItems = computed(() =>
  (overview.value?.quotaRows ?? []).map((row) => ({
    key: row.key,
    label: t(row.labelKey),
    used: row.used,
    limit: row.limit,
    suffix: row.suffix,
  })),
)

const chartBuckets = computed(() => overview.value?.tasks7dBuckets ?? [])

const chartScaleMax = computed(() => {
  const dailyTotals = chartBuckets.value.map((d) => d.success + d.fail + d.cancel)
  const rawMax = Math.max(...dailyTotals, 0)
  if (rawMax <= 0) return 10
  if (rawMax <= 4) return 4
  if (rawMax <= 8) return 8
  if (rawMax <= 12) return 12
  if (rawMax <= 20) return 20
  if (rawMax <= 30) return 30
  if (rawMax <= 40) return 40
  if (rawMax <= 60) return 60
  if (rawMax <= 100) return 100
  return Math.ceil(rawMax / 20) * 20
})

const chartYTicks = computed(() => {
  const scaleMax = chartScaleMax.value
  return [
    scaleMax,
    Math.round(scaleMax * 0.75),
    Math.round(scaleMax * 0.5),
    Math.round(scaleMax * 0.25),
    0,
  ]
})

function attentionLevel(kind: string): 'critical' | 'warning' | 'info' {
  if (kind === 'task' || kind === 'alert') return 'critical'
  if (kind === 'node' || kind === 'source') return 'warning'
  return 'info'
}

function attentionClass(kind: string) {
  const level = attentionLevel(kind)
  if (level === 'critical') return 'attention-item--critical'
  if (level === 'warning') return 'attention-item--warning'
  return 'attention-item--info'
}

function taskProgressValue(progress: TaskRow['progress']): number {
  const n = Number(progress)
  return Number.isFinite(n) ? Math.min(100, Math.max(0, n)) : 0
}

function runningTaskLabel(row: TaskRow) {
  return (row.display_name || '').trim() || taskTypeLabel(row.task_type)
}

function dayTotal(day: TaskDayBucket) {
  return day.success + day.fail + day.cancel
}

function dayHeightPercent(day: TaskDayBucket) {
  const total = dayTotal(day)
  if (!total) return 0
  return (total / chartScaleMax.value) * 100
}

function daySuccessRate(day: TaskDayBucket) {
  const total = dayTotal(day)
  if (!total) return 'N/A'
  return `${((day.success / total) * 100).toFixed(1)}%`
}

function quotaBarClass(pct: number) {
  return pct >= 80 ? 'quota-bar--warning' : 'quota-bar--normal'
}

function quotaPctClass(pct: number) {
  return pct >= 80 ? 'quota-pct--warning' : 'quota-pct--normal'
}

async function refresh() {
  loading.value = true
  loadError.value = null
  try {
    overview.value = await loadDashboardOverview(t)
  } catch (e) {
    loadError.value = e
    overview.value = null
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div v-loading="loading" class="dashboard-page">
    <ErrorState
      v-if="loadError && !overview"
      :error="loadError"
      class="dashboard-page__error"
      @retry="refresh"
    />
    <template v-else>
    <!-- DataFlowPipeline -->
    <section class="pipeline">
      <div class="pipeline__accent" aria-hidden="true" />
      <div class="pipeline__head">
        <div>
          <h3 class="pipeline__title">{{ t('dashboard.pipeline.title') }}</h3>
          <p class="pipeline__subtitle">{{ t('dashboard.pipeline.subtitle') }}</p>
        </div>
      </div>

      <div class="pipeline__flow">
        <!-- Step 1 -->
        <div class="pipeline__step">
          <div class="pipeline__orb pipeline__orb--indigo">
            <div class="pipeline__orbit pipeline__orbit--indigo" />
            <div class="pipeline__orb-glow pipeline__orb-glow--indigo" />
            <div class="pipeline__orb-core">
              <Server class="pipeline__orb-icon" />
            </div>
            <span class="pipeline__orb-pulse">
              <span class="pipeline__orb-pulse-ring" />
              <span class="pipeline__orb-pulse-dot" />
            </span>
          </div>
          <div class="pipeline__step-text">
            <h4>{{ t('dashboard.pipeline.step1Title') }}</h4>
            <p>{{ t('dashboard.pipeline.step1Desc') }}</p>
            <div class="pipeline__chip pipeline__chip--indigo">
              <span>{{ pipelineStep1Chip }}</span>
            </div>
          </div>
        </div>

        <div class="pipeline__connector">
          <div class="pipeline__connector-line">
            <div class="pipeline__connector-flow pipeline__connector-flow--indigo" />
          </div>
          <div class="pipeline__connector-badge">
            <span class="pipeline__connector-dot" />
            <span>{{ t('dashboard.pipeline.connectorBackup') }}</span>
          </div>
        </div>

        <!-- Step 2 -->
        <div class="pipeline__step">
          <div class="pipeline__orb pipeline__orb--emerald">
            <div class="pipeline__orbit pipeline__orbit--emerald" />
            <div class="pipeline__orb-glow pipeline__orb-glow--emerald" />
            <div class="pipeline__orb-core pipeline__orb-core--emerald">
              <Database class="pipeline__orb-icon pipeline__orb-icon--emerald" />
            </div>
            <span class="pipeline__orb-pulse">
              <span class="pipeline__orb-pulse-ring pipeline__orb-pulse-ring--emerald" />
              <span class="pipeline__orb-pulse-dot pipeline__orb-pulse-dot--emerald" />
            </span>
          </div>
          <div class="pipeline__step-text">
            <h4>{{ t('dashboard.pipeline.step2Title') }}</h4>
            <p>{{ t('dashboard.pipeline.step2Desc') }}</p>
            <div class="pipeline__chip pipeline__chip--emerald">
              <span>{{ pipelineStep2Chip }}</span>
            </div>
          </div>
        </div>

        <div class="pipeline__connector">
          <div class="pipeline__connector-line">
            <div class="pipeline__connector-flow pipeline__connector-flow--emerald" />
          </div>
          <div class="pipeline__connector-badge pipeline__connector-badge--emerald">
            <span class="pipeline__connector-dot pipeline__connector-dot--emerald" />
            <span>{{ t('dashboard.pipeline.connectorVerify') }}</span>
          </div>
        </div>

        <!-- Step 3 -->
        <div class="pipeline__step">
          <div class="pipeline__orb pipeline__orb--indigo">
            <div class="pipeline__orbit pipeline__orbit--indigo pipeline__orbit--fast" />
            <div class="pipeline__orb-glow pipeline__orb-glow--violet" />
            <div class="pipeline__orb-core">
              <ShieldCheck class="pipeline__orb-icon" />
            </div>
            <span class="pipeline__orb-pulse">
              <span class="pipeline__orb-pulse-ring" />
              <span class="pipeline__orb-pulse-dot pipeline__orb-pulse-dot--indigo" />
            </span>
          </div>
          <div class="pipeline__step-text">
            <h4>{{ t('dashboard.pipeline.step3Title') }}</h4>
            <p>{{ t('dashboard.pipeline.step3Desc') }}</p>
            <div class="pipeline__chip pipeline__chip--indigo">
              <span>{{ pipelineStep3Chip }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 3 Ribbon Metric Cards -->
    <div class="ribbon-grid">
      <!-- Production source -->
      <RouterLink :to="routes.sources" class="ribbon-card ribbon-card--indigo">
        <div class="ribbon-card__glow ribbon-card__glow--indigo" />
        <div>
          <div class="ribbon-card__head">
            <div>
              <h3>{{ t('dashboard.ribbon.infraTitle') }}</h3>
            </div>
            <div class="ribbon-card__icon ribbon-card__icon--indigo">
              <Server class="w-5 h-5" />
            </div>
          </div>
          <div class="ribbon-card__value-row">
            <span class="ribbon-card__value">{{ sourceAvailabilityPct }}%</span>
          </div>
        </div>
        <div class="ribbon-card__progress-block">
          <div class="ribbon-card__progress-meta">
            <span>{{ infraSourceMainValue }}</span>
          </div>
          <div class="ribbon-track">
            <div class="ribbon-fill ribbon-fill--indigo" :style="{ width: `${sourceAvailabilityPct}%` }" />
          </div>
        </div>
        <div class="ribbon-card__stats">
          <div>
            <span>{{ t('dashboard.ribbon.sourceTotal') }}</span>
            <strong>{{ overview?.productionSources.total ?? 0 }}{{ t('dashboard.unitCount') ? ` ${t('dashboard.unitCount')}` : '' }}</strong>
          </div>
          <div>
            <span>{{ t('dashboard.ribbon.sourceAvailableLabel') }}</span>
            <strong class="text-emerald-600">{{ overview?.productionSources.available ?? 0 }}{{ t('dashboard.unitCount') ? ` ${t('dashboard.unitCount')}` : '' }}</strong>
          </div>
          <div>
            <span>{{ t('dashboard.ribbon.sourceUnavailableLabel') }}</span>
            <strong :class="(overview?.productionSources.unavailable ?? 0) > 0 ? 'ribbon-stat--danger' : 'ribbon-stat--muted'">
              <span class="ribbon-stat-dot" :class="(overview?.productionSources.unavailable ?? 0) > 0 ? 'ribbon-stat-dot--danger' : 'ribbon-stat-dot--muted'" />
              {{ overview?.productionSources.unavailable ?? 0 }}{{ t('dashboard.unitCount') ? ` ${t('dashboard.unitCount')}` : '' }}
            </strong>
          </div>
        </div>
      </RouterLink>

      <!-- Target storage -->
      <RouterLink :to="routes.repositories" class="ribbon-card ribbon-card--emerald">
        <div class="ribbon-card__glow ribbon-card__glow--emerald" />
        <div>
          <div class="ribbon-card__head">
            <div>
              <h3>{{ t('dashboard.ribbon.storageTitle') }}</h3>
            </div>
            <div class="ribbon-card__icon ribbon-card__icon--emerald">
              <Database class="w-5 h-5" />
            </div>
          </div>
          <div class="ribbon-card__value-row">
            <span class="ribbon-card__value">{{ storagePct != null ? `${storagePct}%` : '0%' }}</span>
            <span
              v-if="storageRibbonBadgeVisible"
              class="ribbon-card__badge"
              :class="storageRibbonBadgeClass"
            >
              {{ storageRibbonBadge }}
            </span>
          </div>
        </div>
        <div class="ribbon-card__progress-block">
          <div class="ribbon-card__progress-meta">
            <span>{{ storageUsedProgressLabel }}</span>
          </div>
          <div class="ribbon-track">
            <div class="ribbon-fill ribbon-fill--indigo" :style="{ width: `${storagePct ?? 0}%` }" />
          </div>
        </div>
        <div class="ribbon-card__stats">
          <div>
            <span>{{ t('dashboard.ribbon.repositories') }}</span>
            <strong class="text-emerald-600">{{ overview?.storage.repoCount ?? 0 }}</strong>
          </div>
          <div>
            <span>{{ t('dashboard.ribbon.used') }}</span>
            <strong class="text-emerald-600">{{ formatGB(overview?.storage.usedBytes ?? 0) }}</strong>
          </div>
          <div>
            <span>{{ t('dashboard.ribbon.capacityTotal') }}</span>
            <strong :class="{ 'hfl-empty-mark': storageTotalCapacityLabel === '—' }">{{ storageTotalCapacityLabel }}</strong>
          </div>
        </div>
      </RouterLink>

      <!-- Recovery drill -->
      <RouterLink :to="routes.tasks" class="ribbon-card ribbon-card--indigo">
        <div class="ribbon-card__glow ribbon-card__glow--indigo" />
        <div>
          <div class="ribbon-card__head">
            <div>
              <h3>{{ t('dashboard.ribbon.slaTitle') }}</h3>
            </div>
            <div class="ribbon-card__icon ribbon-card__icon--indigo">
              <ShieldCheck class="w-5 h-5" />
            </div>
          </div>
          <div class="ribbon-card__value-row">
            <span class="ribbon-card__value">
              {{ overview?.tasks24h?.successRate != null ? `${overview.tasks24h.successRate}%` : '0%' }}
            </span>
            <span v-if="slaRibbonBadgeVisible" class="ribbon-card__badge" :class="slaRibbonBadgeClass">
              {{ slaRibbonBadge }}
            </span>
          </div>
        </div>
        <div class="ribbon-card__progress-block">
          <div class="ribbon-card__progress-meta">
            <span>{{ t('dashboard.ribbon.tasks24hSuccessRate') }}</span>
          </div>
          <div class="ribbon-track">
            <div
              class="ribbon-fill ribbon-fill--indigo"
              :style="{ width: `${overview?.tasks24h?.successRate ?? 0}%` }"
            />
          </div>
        </div>
        <div class="ribbon-card__stats">
          <div>
            <span>{{ t('dashboard.ribbon.succeeded') }}</span>
            <strong class="text-emerald-600">
              <span class="ribbon-stat-dot ribbon-stat-dot--success" />
              {{ overview?.tasks24h?.success ?? 0 }}{{ t('dashboard.unitCount') ? ` ${t('dashboard.unitCount')}` : '' }}
            </strong>
          </div>
          <div>
            <span>{{ t('dashboard.ribbon.running') }}</span>
            <strong class="ribbon-stat--primary">
              <span class="ribbon-stat-pulse">
                <span class="ribbon-stat-pulse__ring" />
                <span class="ribbon-stat-pulse__dot" />
              </span>
              {{ overview?.taskStats.running ?? 0 }}{{ t('dashboard.unitCount') ? ` ${t('dashboard.unitCount')}` : '' }}
            </strong>
          </div>
          <div>
            <span>{{ t('dashboard.ribbon.failed') }}</span>
            <strong :class="(overview?.tasks24h?.failed ?? 0) > 0 ? 'ribbon-stat--danger' : 'ribbon-stat--muted'">
              <span class="ribbon-stat-dot" :class="(overview?.tasks24h?.failed ?? 0) > 0 ? 'ribbon-stat-dot--danger ribbon-stat-dot--pulse' : 'ribbon-stat-dot--muted'" />
              {{ overview?.tasks24h?.failed ?? 0 }}{{ t('dashboard.unitCount') ? ` ${t('dashboard.unitCount')}` : '' }}
            </strong>
          </div>
        </div>
      </RouterLink>
    </div>

    <!-- 3-Column Cockpit -->
    <div class="cockpit-grid">
      <!-- Column 1: Attention -->
      <div class="cockpit-stack">
        <section class="panel panel--fixed panel--attention cockpit-attention">
          <div class="panel-head panel-head--border">
            <div class="panel-head__left">
              <span class="panel-head-icon" aria-hidden="true">
                <AlertTriangle :size="14" />
              </span>
              <h3 class="panel-title">{{ t('dashboard.attentionTitle') }}</h3>
              <span v-if="overview?.attentionCount" class="attention-count">{{ overview.attentionCount }}</span>
            </div>
            <RouterLink :to="routes.attention" class="panel-link">
              {{ t('dashboard.viewAllAttention') }}
              <ArrowUpRight class="panel-link__arrow" />
            </RouterLink>
          </div>
          <div class="panel-body panel-body--attention">
            <el-empty
              v-if="!overview?.attention.length"
              :description="t('dashboard.attentionEmptyDesc')"
              :image-size="72"
              class="dashboard-panel-empty dashboard-panel-empty--attention"
            >
              <template #image>
                <DashboardIdleShieldIllustration />
              </template>
            </el-empty>
            <div v-else class="attention-list scrollbar">
              <div
                v-for="item in overview.attention"
                :key="item.id"
                class="attention-item"
                :class="attentionClass(item.kind)"
              >
                <div class="attention-item__content">
                  <AlertCircle class="attention-item__icon" />
                  <div>
                    <p class="attention-item__title">{{ item.title }}</p>
                    <span v-if="item.detail" class="attention-item__detail">{{ item.detail }}</span>
                    <span v-if="item.at" class="attention-item__time">{{ formatTime(item.at) }}</span>
                  </div>
                </div>
                <div class="attention-item__actions">
                  <RouterLink :to="item.to" class="attention-item__solve">
                    {{ t('dashboard.openAttentionItem') }}
                  </RouterLink>
                </div>
              </div>
            </div>
            <div
              v-if="overview && overview.attention.length < overview.attentionCount"
              class="attention-list__summary"
            >
              <span>{{ t('dashboard.attentionPreview', { shown: overview.attention.length, total: overview.attentionCount }) }}</span>
            </div>
          </div>
        </section>
      </div>

      <!-- Column 2: Storage + Quota -->
      <div class="cockpit-stack">
        <section class="panel panel--fixed panel--storage-capacity cockpit-storage">
          <div class="panel-head panel-head--border">
            <div class="panel-head__left">
              <span class="panel-head-icon" aria-hidden="true">
                <Database :size="14" />
              </span>
              <h4 class="panel-title panel-title--sm">{{ t('dashboard.storageTitle') }}</h4>
            </div>
            <div class="storage-capacity-head__actions">
              <RouterLink :to="routes.repositories" class="panel-link">
                {{ t('dashboard.viewRepos') }}
                <ArrowUpRight class="panel-link__arrow" />
              </RouterLink>
            </div>
          </div>

          <div class="storage-capacity-body">
            <section class="storage-capacity-repos">
              <div v-if="!overview?.topRepos.length" class="storage-empty">
                <p>{{ t('dashboard.noRepos') }}</p>
              </div>
	              <div v-else class="storage-list">
	                <div v-for="repo in visibleStorageRepos" :key="repo.id" class="storage-item">
	                  <div class="storage-item__head">
	                    <div class="storage-item__name">
	                      <span class="storage-item__name-main">
	                        <span class="storage-item__health" :class="`storage-item__health--${repoHealthKind(repo)}`" aria-hidden="true" />
                        <span class="storage-item__name-text">{{ repo.name }}</span>
                      </span>
                      <span class="storage-item__health-label" :class="`storage-item__health-label--${repoHealthKind(repo)}`">
	                        {{ repoHealthLabel(repo) }}
	                      </span>
	                    </div>
	                    <span
	                      class="storage-item__bytes"
	                      :class="{ 'storage-item__bytes--unlimited': repo.capacityMode !== 'known' }"
	                    >
	                      <span class="storage-item__used">{{ formatBytes(repo.usedBytes) }}</span>
                      <span class="storage-item__capacity" :class="{ 'hfl-empty-mark': repoCapacityLabel(repo) === '—' }">{{ repoCapacityLabel(repo) === '—' ? '—' : `/ ${repoCapacityLabel(repo)}` }}</span>
	                      <span v-if="repo.capacityMode === 'known' && repo.pct !== null" class="storage-item__pct">
	                        {{ repo.pct }}%
	                      </span>
	                      <RouterLink v-if="hiddenStorageRepoCount > 0" :to="routes.repositories" class="storage-more-inline">
	                        +{{ hiddenStorageRepoCount }} repos
	                      </RouterLink>
	                    </span>
	                  </div>
	                  <div v-if="repo.capacityMode === 'known'" class="storage-track">
                    <div class="storage-fill" :style="{ width: `${repo.pct ?? 0}%` }" />
                  </div>
                </div>
              </div>
            </section>

            <section class="storage-capacity-planner">
              <div class="storage-capacity-planner__title">
                <span class="storage-capacity-planner__title-main">
                  <Calculator :size="13" />
                  <span>{{ t('dashboard.capacityPlanner.title') }}</span>
                </span>
                <span class="capacity-planner__status-chip" :class="`capacity-planner__status-chip--${capacityPlanStatus}`">
                  {{ capacityPlanStatusLabel }}
                </span>
              </div>
              <div class="capacity-planner__form">
                <div class="capacity-planner__field">
                  <label
                    class="capacity-planner__label"
                    for="dashboard-capacity-plan-amount"
                  >
                    {{ t('dashboard.capacityPlanner.plannedAdd') }}
                  </label>
                  <div class="capacity-planner__controls">
                    <ElInputNumber
                      id="dashboard-capacity-plan-amount"
                      v-model="capacityPlanAmount"
                      class="capacity-planner__amount"
                      name="dashboard_capacity_plan_amount"
                      :aria-label="t('dashboard.capacityPlanner.plannedAdd')"
                      :min="0"
                      :step="0.1"
                      :precision="1"
                      controls-position="right"
                    />
                    <ElSelect
                      v-model="capacityPlanUnit"
                      class="capacity-planner__unit"
                      :aria-label="t('dashboard.capacityPlanner.unit')"
                      :teleported="false"
                    >
                      <ElOption label="GB" value="GB" />
                      <ElOption label="TB" value="TB" />
                    </ElSelect>
                  </div>
                </div>

                <div class="capacity-planner__field">
                  <label
                    class="capacity-planner__label"
                    for="dashboard-capacity-plan-factor"
                  >
                    {{ t('dashboard.capacityPlanner.factor') }}
                  </label>
                  <ElInputNumber
                    id="dashboard-capacity-plan-factor"
                    v-model="capacityPlanFactor"
                    class="capacity-planner__factor"
                    name="dashboard_capacity_plan_factor"
                    :aria-label="t('dashboard.capacityPlanner.factor')"
                    :min="0"
                    :step="0.1"
                    :precision="1"
                    controls-position="right"
                  />
                </div>
              </div>

              <div class="capacity-planner__metrics">
                <div class="capacity-planner__metric">
                  <span>{{ capacityPlanPrimaryMetric.label }}</span>
                  <strong>{{ capacityPlanPrimaryMetric.value }}</strong>
                </div>
                <div class="capacity-planner__metric">
                  <span>{{ capacityPlanSecondaryMetric.label }}</span>
                  <strong>{{ capacityPlanSecondaryMetric.value }}</strong>
                </div>
              </div>
            </section>
          </div>
        </section>

        <section v-if="quotaItems.length" class="panel panel--fixed panel--quota cockpit-quota">
          <div class="panel-head panel-head--border">
            <div class="panel-head__left">
              <span class="panel-head-icon" aria-hidden="true">
                <CreditCard :size="14" />
              </span>
              <h3 class="panel-title">{{ t('dashboard.quotaTitle') }}</h3>
            </div>
            <RouterLink :to="routes.subscription" class="panel-link">
              {{ t('dashboard.viewLicense') }}
              <ArrowUpRight class="panel-link__arrow" />
            </RouterLink>
          </div>
          <div class="quota-grid scrollbar">
            <div v-for="q in quotaItems" :key="q.key" class="quota-item">
              <div class="quota-item__head">
                <ElTooltip :content="q.label" placement="top" :enterable="true" :show-after="200">
                  <span class="quota-item__label">{{ q.label }}</span>
                </ElTooltip>
                <span class="quota-item__nums">
                  {{ q.used }} <span class="quota-item__limit">/ {{ formatQuotaLimit(q.limit) }}</span>
                  <span v-if="q.suffix" class="quota-item__unit">{{ q.suffix }}</span>
                  <span class="quota-pct" :class="quotaPctClass(quotaPct(q.used, q.limit))">
                    {{ quotaPct(q.used, q.limit) }}%
                  </span>
                </span>
              </div>
              <div class="quota-track">
                <div
                  class="quota-bar"
                  :class="quotaBarClass(quotaPct(q.used, q.limit))"
                  :style="{ width: `${quotaPct(q.used, q.limit)}%` }"
                />
              </div>
            </div>
          </div>
        </section>
      </div>

      <!-- Column 3: Running Tasks + Chart -->
      <div class="cockpit-stack">
        <section class="panel panel--fixed cockpit-running">
          <div class="panel-head panel-head--border">
            <div class="panel-head__left">
              <span class="panel-head-icon" aria-hidden="true">
                <History :size="14" />
              </span>
              <h4 class="panel-title panel-title--sm">{{ t('dashboard.runningTasksTitle') }}</h4>
            </div>
            <RouterLink :to="routes.tasks" class="panel-link">
              {{ t('dashboard.linkTasks') }}
              <ArrowUpRight class="panel-link__arrow" />
            </RouterLink>
          </div>
          <el-empty
            v-if="!overview?.runningTasks.length"
            :description="t('dashboard.noRunningBackupTasks')"
            :image-size="72"
            class="dashboard-panel-empty dashboard-panel-empty--running"
          >
            <template #image>
              <DashboardIdleGearIllustration />
            </template>
          </el-empty>
          <div v-else class="running-list scrollbar">
            <div v-for="task in overview.runningTasks" :key="task.id" class="running-item">
              <div class="running-item__head">
                <span class="running-item__name">{{ runningTaskLabel(task) }}</span>
                <span class="running-item__pct">{{ taskProgressValue(task.progress) }}%</span>
              </div>
              <div class="running-track">
                <div class="running-fill" :style="{ width: `${taskProgressValue(task.progress)}%` }" />
              </div>
              <div class="running-item__meta">
                <span>{{ taskTypeLabel(task.task_type) }}</span>
                <span :class="{ 'hfl-empty-mark': !(task.started_at || task.created_at) }">{{ formatTime(task.started_at || task.created_at) }}</span>
              </div>
            </div>
          </div>
        </section>

        <section class="panel panel--fixed panel--chart cockpit-chart">
          <div class="panel-head panel-head--border panel-head--chart">
            <div class="panel-head__left">
              <span class="panel-head-icon" aria-hidden="true">
                <History :size="14" />
              </span>
              <h3 class="panel-title">{{ t('dashboard.chartTasks7d') }}</h3>
            </div>
            <div class="chart-status">
              <div v-if="chartHoveredIdx !== null && chartBuckets[chartHoveredIdx]" class="chart-inline-tip">
                <span class="chart-inline-tip__date">{{ chartBuckets[chartHoveredIdx].label }}:</span>
                <span class="chart-inline-tip__success">
                  {{ t('dashboard.chartSuccess') }} {{ chartBuckets[chartHoveredIdx].success }}
                </span>
                <span class="chart-inline-tip__sep" aria-hidden="true">|</span>
                <span class="chart-inline-tip__fail">
                  {{ t('dashboard.chartFailed') }} {{ chartBuckets[chartHoveredIdx].fail }}
                </span>
                <template v-if="chartBuckets[chartHoveredIdx].cancel > 0">
                  <span class="chart-inline-tip__sep" aria-hidden="true">|</span>
                  <span class="chart-inline-tip__cancel">
                    {{ t('dashboard.chartCancelled') }} {{ chartBuckets[chartHoveredIdx].cancel }}
                  </span>
                </template>
                <span class="chart-inline-tip__rate">
                  {{ t('dashboard.chartSuccessRate') }} {{ daySuccessRate(chartBuckets[chartHoveredIdx]) }}
                </span>
              </div>
              <RouterLink v-else :to="routes.tasks" class="panel-link">
                {{ t('dashboard.linkTasks') }}
                <ArrowUpRight class="panel-link__arrow" />
              </RouterLink>
            </div>
          </div>

          <div class="chart-area">
            <div class="chart-plot">
              <div
                v-for="(tick, idx) in chartYTicks"
                :key="idx"
                class="chart-y-row"
                :style="{ top: `${(idx / 4) * 100}%` }"
              >
                <span class="chart-y-label">{{ tick }}</span>
                <div class="chart-y-line" />
              </div>
              <div class="chart-bars">
                <div
                  v-for="(day, idx) in chartBuckets"
                  :key="day.label"
                  class="chart-col"
                  @mouseenter="chartHoveredIdx = idx"
                  @mouseleave="chartHoveredIdx = null"
                >
                  <div class="chart-col__pillar" :style="{ height: `${dayHeightPercent(day)}%` }">
                    <div
                      v-if="dayTotal(day) > 0"
                      class="chart-pillar"
                      :class="{ 'chart-pillar--hover': chartHoveredIdx === idx }"
                    >
                      <div
                        v-if="day.cancel > 0"
                        class="chart-seg chart-seg--cancel"
                        :style="{ height: `${(day.cancel / dayTotal(day)) * 100}%` }"
                      />
                      <div
                        v-if="day.fail > 0"
                        class="chart-seg chart-seg--fail"
                        :style="{ height: `${(day.fail / dayTotal(day)) * 100}%` }"
                      />
                      <div
                        v-if="day.success > 0"
                        class="chart-seg chart-seg--success"
                        :style="{ height: `${(day.success / dayTotal(day)) * 100}%` }"
                      />
                    </div>
                    <div v-else class="chart-zero" />
                  </div>
                  <span class="chart-x-label" :class="{ 'chart-x-label--active': chartHoveredIdx === idx }">
                    {{ day.label }}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div class="chart-legend">
            <div class="chart-legend__item">
              <span class="chart-legend__swatch chart-legend__swatch--success" />
              <span>{{ t('dashboard.chartSuccess') }}</span>
            </div>
            <div class="chart-legend__item">
              <span class="chart-legend__swatch chart-legend__swatch--fail" />
              <span>{{ t('dashboard.chartFailed') }}</span>
            </div>
            <div class="chart-legend__item">
              <span class="chart-legend__swatch chart-legend__swatch--cancel" />
              <span>{{ t('dashboard.chartCancelled') }}</span>
            </div>
          </div>
        </section>
      </div>
    </div>
    </template>
  </div>
</template>

<style scoped>
.dashboard-page__error {
  min-height: 360px;
  background: #fff;
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 8px;
}

.dashboard-page {
  --dashboard-primary: var(--color-primary, #5c5cff);
  --dashboard-primary-hover: var(--color-primary-hover, #4848e6);
  --dashboard-primary-strong: color-mix(in srgb, var(--dashboard-primary) 86%, #000);
  --dashboard-primary-soft: color-mix(in srgb, var(--dashboard-primary) 10%, #fff);
  --dashboard-primary-tint: color-mix(in srgb, var(--dashboard-primary) 18%, #fff);
  --dashboard-primary-border: color-mix(in srgb, var(--dashboard-primary) 20%, #fff);
  --dashboard-primary-glow: color-mix(in srgb, var(--dashboard-primary) 8%, transparent);
  --dashboard-primary-mid: color-mix(in srgb, var(--dashboard-primary) 76%, #fff);
  --dashboard-primary-pulse: color-mix(in srgb, var(--dashboard-primary) 62%, #fff);
  --dashboard-success: var(--color-success);
  --dashboard-success-soft: color-mix(in srgb, var(--dashboard-success) 10%, #fff);
  --dashboard-success-strong: color-mix(in srgb, var(--dashboard-success) 70%, #000);
  --dashboard-success-border: color-mix(in srgb, var(--dashboard-success) 24%, #fff);
  --dashboard-success-mid: color-mix(in srgb, var(--dashboard-success) 68%, #fff);
  --dashboard-success-glow: color-mix(in srgb, var(--dashboard-success) 10%, transparent);
  --dashboard-warning: var(--color-warning);
  --dashboard-warning-soft: var(--color-warning-light);
  --dashboard-warning-strong: color-mix(in srgb, var(--dashboard-warning) 72%, #000);
  --dashboard-warning-border: color-mix(in srgb, var(--dashboard-warning) 28%, #fff);
  --dashboard-warning-mid: color-mix(in srgb, var(--dashboard-warning) 70%, #fff);
  --dashboard-warning-glow: color-mix(in srgb, var(--dashboard-warning) 10%, transparent);
  --dashboard-error: var(--color-error);
  --dashboard-error-soft: var(--color-error-light);
  --dashboard-error-strong: color-mix(in srgb, var(--dashboard-error) 72%, #000);
  --dashboard-error-border: color-mix(in srgb, var(--dashboard-error) 24%, #fff);
  --dashboard-error-mid: color-mix(in srgb, var(--dashboard-error) 68%, #fff);
  --dashboard-error-glow: color-mix(in srgb, var(--dashboard-error) 10%, transparent);
  --dashboard-info: var(--color-info, var(--dashboard-primary));
  --dashboard-info-soft: var(--color-info-light, var(--dashboard-primary-soft));
  --dashboard-info-strong: color-mix(in srgb, var(--dashboard-info) 72%, #000);
  --dashboard-info-border: color-mix(in srgb, var(--dashboard-info) 22%, #fff);
  box-sizing: border-box;
  width: 100%;
  min-height: calc(var(--app-viewport-height) - var(--topnav-height, var(--app-header-height)) - 2 * var(--page-gutter));
  padding: var(--page-gutter);
  background: #f2f3f5;
  color: #1d2129;
  font-family: var(--font-sans);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

/* ---- DataFlowPipeline ---- */
.pipeline {
  position: relative;
  background: #fff;
  border: 1px solid #dde1e6;
  border-radius: 1rem;
  padding: 1.5rem 2rem;
  box-shadow: 0 2px 12px rgba(31, 35, 41, 0.02);
  overflow: hidden;
}

.pipeline__accent {
  position: absolute;
  inset-inline: 0;
  top: 0;
  height: 3px;
  background: linear-gradient(to right, var(--dashboard-primary-mid), var(--dashboard-success), var(--dashboard-primary));
  opacity: 0.9;
}

.pipeline__head {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding-bottom: 1.25rem;
  margin-bottom: 1.25rem;
  border-bottom: 1px solid #f3f4f6;
}

@media (min-width: 640px) {
  .pipeline__head {
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
  }
}

.pipeline__title {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
  color: #1d2129;
}

.pipeline__subtitle {
  margin: 0.125rem 0 0;
  font-size: 13px;
  font-weight: 400;
  line-height: 1.6;
  color: #86909c;
}

.pipeline__flow {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2rem;
  padding: 0.5rem 0;
}

@media (min-width: 1024px) {
  .pipeline__flow {
    flex-direction: row;
    justify-content: space-between;
    gap: 0;
  }
}

.pipeline__step {
  flex: 1;
  max-width: 280px;
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.pipeline__orb {
  position: relative;
  width: 6rem;
  height: 6rem;
  margin-bottom: 1rem;
  display: flex;
  align-items: center;
  justify-content: center;
}

.pipeline__orbit {
  position: absolute;
  inset: 0;
  border-radius: 9999px;
  border: 2px dashed var(--dashboard-primary-border);
  animation: spin 20s linear infinite;
}

.pipeline__orbit--emerald {
  border-color: var(--dashboard-success-border);
  animation-duration: 24s;
}

.pipeline__orbit--fast {
  animation-duration: 18s;
}

.pipeline__orb-glow {
  position: absolute;
  inset: 0.5rem;
  border-radius: 9999px;
  background: linear-gradient(to top right, var(--dashboard-primary-glow), color-mix(in srgb, var(--dashboard-primary) 5%, transparent));
  filter: blur(2px);
}

.pipeline__orb-glow--emerald {
  background: linear-gradient(to top right, var(--dashboard-success-glow), color-mix(in srgb, var(--dashboard-success) 5%, transparent));
}

.pipeline__orb-glow--violet {
  background: linear-gradient(to top right, var(--dashboard-primary-glow), color-mix(in srgb, var(--dashboard-primary) 5%, transparent));
}

.pipeline__orb-core {
  position: absolute;
  inset: 0.5rem;
  background: #fff;
  border-radius: 9999px;
  border: 1px solid var(--dashboard-primary-border);
  box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.3s;
}

.pipeline__orb-core--emerald {
  border-color: var(--dashboard-success-border);
}

.pipeline__orb:hover .pipeline__orb-core {
  transform: scale(1.05);
}

.pipeline__orb-icon {
  width: 2.25rem;
  height: 2.25rem;
  color: var(--dashboard-primary);
  animation: pulse 2s infinite;
}

.pipeline__orb-icon--emerald {
  color: var(--dashboard-success-strong);
  animation: none;
}

.pipeline__orb-pulse {
  position: absolute;
  bottom: 0.5rem;
  right: 0.5rem;
  display: flex;
  width: 0.75rem;
  height: 0.75rem;
}

.pipeline__orb-pulse-ring {
  position: absolute;
  inset: 0;
  border-radius: 9999px;
  background: var(--dashboard-primary-pulse);
  opacity: 0.75;
  animation: ping 1s cubic-bezier(0, 0, 0.2, 1) infinite;
}

.pipeline__orb-pulse-ring--emerald {
  background: var(--dashboard-success-mid);
}

.pipeline__orb-pulse-dot {
  position: relative;
  width: 0.75rem;
  height: 0.75rem;
  border-radius: 9999px;
  background: var(--dashboard-primary);
}

.pipeline__orb-pulse-dot--emerald {
  background: var(--dashboard-success);
}

.pipeline__orb-pulse-dot--indigo {
  background: var(--dashboard-primary-mid);
}

.pipeline__step-text {
  text-align: center;
}

.pipeline__step-code {
  font-size: 11px;
  font-weight: 600;
  color: var(--dashboard-primary-mid);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-family: var(--font-mono);
}

.pipeline__step-code--emerald {
  color: var(--dashboard-success);
}

.pipeline__step-text h4 {
  margin: 0.125rem 0 0;
  font-size: 14px;
  font-weight: 500;
  color: #1d2129;
}

.pipeline__step-text p {
  margin: 0.25rem 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: #86909c;
}

.pipeline__chip {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  margin-top: 0.75rem;
  padding: 0.25rem 0.625rem;
  border-radius: 9999px;
  font-size: 12px;
  font-weight: 600;
}

.pipeline__chip--indigo {
  background: var(--dashboard-primary-soft);
  color: var(--dashboard-primary-strong);
  border: 1px solid var(--dashboard-primary-border);
}

.pipeline__chip--emerald {
  background: var(--dashboard-success-soft);
  color: var(--dashboard-success-strong);
  border: 1px solid var(--dashboard-success-border);
}

.pipeline__connector {
  flex: 1;
  min-width: 120px;
  width: 100%;
  position: relative;
  height: 2.5rem;
  display: flex;
  align-items: center;
  justify-content: center;
}

.pipeline__connector-line {
  position: absolute;
  inset-inline: 0;
  height: 2px;
  background: #f3f4f6;
  overflow: hidden;
}

.pipeline__connector-flow {
  position: absolute;
  inset-block: 0;
  width: 5rem;
  background: linear-gradient(to right, transparent, var(--dashboard-primary-pulse), transparent);
  animation: flow 3.5s linear infinite;
}

.pipeline__connector-flow--emerald {
  background: linear-gradient(to right, transparent, var(--dashboard-success-mid), transparent);
  animation-delay: 1s;
}

.pipeline__connector-badge {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.25rem 0.875rem;
  background: #f8f9fc;
  border: 1px solid rgba(229, 231, 235, 0.9);
  border-radius: 9999px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  font-size: 12px;
  font-weight: 600;
  color: #4e5969;
}

.pipeline__connector-dot {
  width: 0.375rem;
  height: 0.375rem;
  border-radius: 9999px;
  background: var(--dashboard-primary-mid);
  animation: pulse 2s infinite;
}

.pipeline__connector-dot--emerald {
  background: var(--dashboard-success);
}

@media (min-width: 768px) and (max-width: 1023.98px) {
  .pipeline {
    padding: 1.25rem 1.5rem;
  }

  .pipeline__head {
    padding-bottom: 0.875rem;
    margin-bottom: 0.875rem;
  }

  .pipeline__flow {
    flex-direction: row;
    justify-content: space-between;
    gap: 0;
    padding: 0.25rem 0;
  }

  .pipeline__step {
    max-width: 190px;
    min-width: 0;
  }

  .pipeline__orb {
    width: 4rem;
    height: 4rem;
    margin-bottom: 0.625rem;
  }

  .pipeline__orbit,
  .pipeline__orb-glow,
  .pipeline__orb-pulse {
    display: none;
  }

  .pipeline__orb-core {
    inset: 0;
    border: 0;
    border-radius: 0.75rem;
    background: var(--dashboard-primary-soft);
    box-shadow: none;
  }

  .pipeline__orb-core--emerald {
    background: var(--dashboard-success-soft);
  }

  .pipeline__orb-icon {
    width: 1.5rem;
    height: 1.5rem;
    animation: none;
  }

  .pipeline__step-text p {
    font-size: 12px;
    line-height: 1.4;
  }

  .pipeline__chip {
    margin-top: 0.5rem;
    padding: 0.1875rem 0.5rem;
    font-size: 12px;
  }

  .pipeline__connector {
    flex: 0.6 1 80px;
    min-width: 48px;
  }

  .pipeline__connector-badge {
    padding: 0.1875rem 0.5rem;
    font-size: 12px;
  }

  .pipeline__connector-flow,
  .pipeline__connector-dot {
    animation: none;
  }
}

@media (max-width: 767.98px) {
  .pipeline {
    padding: 1rem;
    border-radius: 0.75rem;
    box-shadow: none;
  }

  .pipeline__accent {
    height: 2px;
    background: var(--dashboard-primary);
  }

  .pipeline__head {
    padding-bottom: 0.75rem;
    margin-bottom: 0;
  }

  .pipeline__flow {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 24px minmax(0, 1fr) 24px minmax(0, 1fr);
    align-items: stretch;
    gap: 4px;
    padding: 0;
  }

  .pipeline__step {
    display: flex;
    max-width: none;
    align-items: center;
    gap: 0;
    padding: 0.5rem 0;
  }

  .pipeline__orb {
    width: 48px;
    height: 48px;
    margin-bottom: 0.5rem;
    justify-self: center;
  }

  .pipeline__orbit,
  .pipeline__orb-glow,
  .pipeline__orb-pulse {
    display: none;
  }

  .pipeline__orb-core {
    inset: 0;
    border: 0;
    border-radius: 0.5rem;
    background: var(--dashboard-primary-soft);
    box-shadow: none;
  }

  .pipeline__orb-core--emerald {
    background: var(--dashboard-success-soft);
  }

  .pipeline__orb-icon {
    width: 20px;
    height: 20px;
    animation: none;
  }

  .pipeline__step-text {
    width: 100%;
    min-width: 0;
    text-align: center;
  }

  .pipeline__step-text h4 {
    margin-top: 0;
    font-size: 13px;
    font-weight: 600;
    line-height: 1.35;
    overflow-wrap: anywhere;
  }

  .pipeline__step-text p {
    margin-top: 0.125rem;
    font-size: 12px;
    line-height: 1.4;
    overflow-wrap: anywhere;
  }

  .pipeline__chip {
    justify-content: center;
    width: 100%;
    max-width: 100%;
    min-height: 38px;
    box-sizing: border-box;
    margin-top: 0.375rem;
    padding: 0.1875rem 0.375rem;
    border-radius: 0.375rem;
    font-size: 12px;
    line-height: 1.25;
    text-align: center;
    white-space: normal;
    overflow-wrap: anywhere;
  }

  .pipeline__connector {
    display: flex;
    min-width: 0;
    width: 100%;
    height: 48px;
    align-items: center;
    justify-content: center;
    align-self: start;
    margin-top: 0.5rem;
  }

  .pipeline__connector-line {
    position: relative;
    inset: auto;
    width: 100%;
    height: 1px;
    overflow: visible;
    background: var(--color-border, #dde1e6);
  }

  .pipeline__connector-line::after {
    position: absolute;
    top: 50%;
    right: 0;
    width: 6px;
    height: 6px;
    border-top: 1px solid var(--color-text-tertiary, #a6a6b0);
    border-right: 1px solid var(--color-text-tertiary, #a6a6b0);
    content: '';
    transform: translateY(-50%) rotate(45deg);
  }

  .pipeline__connector-flow,
  .pipeline__connector-badge {
    display: none;
  }

  .pipeline__connector-dot {
    animation: none;
  }
}

@media (max-width: 479.98px) {
  .pipeline__step-text p {
    display: none;
  }
}

@media (max-width: 1023.98px) and (prefers-reduced-motion: reduce) {
  .pipeline__orbit,
  .pipeline__orb-icon,
  .pipeline__orb-pulse-ring,
  .pipeline__connector-flow,
  .pipeline__connector-dot {
    animation: none;
  }
}

/* ---- Ribbon Cards ---- */
.ribbon-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 1.5rem;
}

@media (min-width: 768px) {
  .ribbon-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

.ribbon-card {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 220px;
  padding: 1.25rem;
  border-radius: 0.75rem;
  border: 1px solid #dde1e6;
  background: #fff;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  text-decoration: none;
  color: inherit;
  overflow: hidden;
  transition: box-shadow 0.3s;
}

.ribbon-card:hover {
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
}

.ribbon-card--indigo {
  border-left: 4px solid var(--dashboard-primary);
}

.ribbon-card--emerald {
  border-left: 4px solid var(--dashboard-success);
}

.ribbon-card--amber {
  border-left: 4px solid var(--dashboard-warning);
}

.ribbon-card__glow {
  position: absolute;
  top: 0;
  right: 0;
  width: 8rem;
  height: 8rem;
  border-bottom-left-radius: 100%;
  pointer-events: none;
}

.ribbon-card__glow--indigo {
  background: linear-gradient(to bottom left, color-mix(in srgb, var(--dashboard-primary) 5%, transparent), transparent);
}

.ribbon-card__glow--emerald {
  background: linear-gradient(to bottom left, color-mix(in srgb, var(--dashboard-success) 5%, transparent), transparent);
}

.ribbon-card__glow--amber {
  background: linear-gradient(to bottom left, color-mix(in srgb, var(--dashboard-warning) 5%, transparent), transparent);
}

.ribbon-card__head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.ribbon-card__head h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
  color: #1d2129;
}

.ribbon-card__icon {
  padding: 0.625rem;
  border-radius: 0.5rem;
  transition: transform 0.3s;
}

.ribbon-card:hover .ribbon-card__icon {
  transform: scale(1.1);
}

.ribbon-card__icon--indigo {
  background: var(--dashboard-primary-soft);
  color: var(--dashboard-primary);
}

.ribbon-card__icon--emerald {
  background: var(--dashboard-success-soft);
  color: var(--dashboard-success-strong);
}

.ribbon-card__icon--amber {
  background: var(--dashboard-warning-soft);
  color: var(--dashboard-warning-strong);
}

.ribbon-card__value-row {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  margin-top: 1rem;
}

.ribbon-card__value {
  font-size: 24px;
  font-weight: 600;
  letter-spacing: -0.01em;
  font-variant-numeric: tabular-nums;
  color: #1d2129;
}

.ribbon-card__badge {
  font-size: 12px;
  padding: 0.125rem 0.5rem;
  border-radius: 9999px;
  font-weight: 600;
}

.ribbon-card__badge--indigo {
  background: var(--dashboard-primary-soft);
  color: var(--dashboard-primary-strong);
  border: 1px solid var(--dashboard-primary-border);
}

.ribbon-card__badge--amber {
  background: var(--dashboard-warning-soft);
  color: var(--dashboard-warning-strong);
  border: 1px solid var(--dashboard-warning-border);
}

.ribbon-card__badge--emerald {
  background: var(--dashboard-success-soft);
  color: var(--dashboard-success-strong);
  border: 1px solid var(--dashboard-success-border);
}

.ribbon-card__badge--pulse {
  animation: pulse 2s infinite;
}

.ribbon-card__progress-block {
  margin-top: 1rem;
}

.ribbon-card__progress-meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #86909c;
  font-weight: 500;
  margin-bottom: 0.375rem;
}

.ribbon-card__meta-accent--indigo {
  color: var(--dashboard-primary);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.ribbon-card__meta-accent--emerald {
  color: var(--dashboard-success-strong);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.ribbon-card__meta-accent--amber {
  color: var(--dashboard-warning-strong);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.ribbon-track {
  height: 0.375rem;
  border-radius: 9999px;
  background: #f3f4f6;
  overflow: hidden;
}

.ribbon-fill {
  height: 100%;
  border-radius: 9999px;
  transition: width 1s;
}

.ribbon-fill--indigo-cyan {
  background: linear-gradient(to right, var(--dashboard-primary-mid), #06b6d4);
}

.ribbon-fill--emerald {
  background: linear-gradient(to right, var(--dashboard-success), var(--dashboard-success-mid));
}

.ribbon-fill--amber {
  background: linear-gradient(to right, var(--dashboard-warning), var(--dashboard-warning-mid));
}

.ribbon-fill--indigo {
  background: linear-gradient(to right, var(--dashboard-primary-mid), var(--dashboard-primary));
}

.ribbon-card__stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.5rem;
  margin-top: 1rem;
  padding-top: 0.75rem;
  border-top: 1px solid #f1f5f9;
}

.ribbon-card__stats > div {
  display: flex;
  flex-direction: column;
}

.ribbon-card__stats > div:not(:first-child) {
  border-left: 1px solid #f1f5f9;
  padding-left: 0.5rem;
}

.ribbon-card__stats span:first-child {
  font-size: 12px;
  color: #86909c;
  font-weight: 500;
  line-height: 1.3;
}

.ribbon-card__stats strong {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  margin-top: 0.125rem;
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: #1d2129;
}

.ribbon-stat--danger {
  color: var(--dashboard-error) !important;
}

.ribbon-stat--muted {
  color: #86909c !important;
}

.ribbon-stat--slate {
  color: #86909c !important;
}

.ribbon-stat-dot {
  width: 0.375rem;
  height: 0.375rem;
  border-radius: 9999px;
  flex-shrink: 0;
}

.ribbon-stat-dot--success {
  background: var(--dashboard-success);
}

.ribbon-stat-dot--danger {
  background: var(--dashboard-error);
}

.ribbon-stat-dot--muted {
  background: #cbd5e1;
}

.ribbon-stat-dot--pulse {
  animation: pulse 2s infinite;
}

.ribbon-stat-pulse {
  position: relative;
  display: flex;
  width: 0.375rem;
  height: 0.375rem;
}

.ribbon-stat-pulse__ring {
  position: absolute;
  inset: 0;
  border-radius: 9999px;
  background: var(--dashboard-primary-pulse);
  opacity: 0.75;
  animation: ping 1s infinite;
}

.ribbon-stat-pulse__dot {
  position: relative;
  width: 0.375rem;
  height: 0.375rem;
  border-radius: 9999px;
  background: var(--dashboard-primary);
}

.ribbon-stat--primary {
  color: var(--dashboard-primary) !important;
}

/* ---- Cockpit ---- */
.cockpit-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 1.5rem;
  align-items: stretch;
}

@media (min-width: 1024px) {
  .cockpit-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    grid-template-rows: 282px 250px;
  }

  .cockpit-stack {
    display: contents;
  }

  .cockpit-attention {
    grid-column: 1;
    grid-row: 1 / span 2;
  }

  .cockpit-storage {
    grid-column: 2;
    grid-row: 1;
  }

  .cockpit-quota {
    grid-column: 2;
    grid-row: 2;
  }

  .cockpit-running {
    grid-column: 3;
    grid-row: 1;
  }

  .cockpit-chart {
    grid-column: 3;
    grid-row: 2;
  }
}

.cockpit-stack {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

@media (min-width: 1024px) {
  .cockpit-stack {
    display: contents;
  }
}

.panel {
  display: flex;
  flex-direction: column;
  padding: 1.25rem;
  border-radius: 0.75rem;
  border: 1px solid #eaecef;
  background: #fff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
  min-width: 0;
}

.panel--attention {
  overflow: hidden;
}

@media (min-width: 1024px) {
  .panel--fixed {
    min-height: 250px;
    height: 250px;
    overflow: hidden;
  }

  .panel--storage-capacity {
    min-height: 282px;
    height: 282px;
  }

  .cockpit-attention {
    height: 100%;
    min-height: 100%;
  }

  .cockpit-running {
    min-height: 282px;
    height: 282px;
  }
}

@media (max-width: 1023px) {
  .panel--attention {
    max-height: min(480px, 58vh);
  }
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  flex-shrink: 0;
}

.panel-head--border {
  padding-bottom: 0.75rem;
  border-bottom: 1px solid #f2f4f7;
  margin-bottom: 1rem;
}

.panel-head--chart {
  flex-wrap: wrap;
  gap: 0.75rem;
}

.panel-head__left {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.panel-head-icon {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: var(--dashboard-primary);
}

.panel-title {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
  color: #1d2129;
}

.panel-title--sm {
  font-size: 13px;
  letter-spacing: 0;
}

.panel-link {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 13px;
  font-weight: 500;
  color: var(--dashboard-primary);
  text-decoration: none;
}

.panel-link__arrow {
  width: 0.875rem;
  height: 0.875rem;
  flex-shrink: 0;
  transition: transform 0.2s ease;
}

.panel-link:hover {
  color: var(--dashboard-primary-hover);
}

.panel-link:hover .panel-link__arrow {
  transform: translate(0.125rem, -0.125rem);
}

.panel-body--attention {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

/* ---- Attention ---- */
.attention-count {
  background: var(--dashboard-error-soft);
  color: var(--dashboard-error);
  font-size: 12px;
  padding: 0.125rem 0.5rem;
  border-radius: 9999px;
  font-weight: 600;
}

.dashboard-panel-empty {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.5rem 0;
}

.dashboard-panel-empty :deep(.el-empty__image) {
  width: auto;
  height: auto;
}

.dashboard-panel-empty--attention :deep(.el-empty__image) {
  width: auto;
  height: auto;
}

.dashboard-panel-empty--running :deep(.el-empty__image) {
  width: auto;
  height: auto;
}

.dashboard-panel-empty :deep(.el-empty__description) {
  margin-top: 12px;
}

.dashboard-panel-empty :deep(.el-empty__description p) {
  margin: 0;
  max-width: 18rem;
  font-size: 13px;
  line-height: 1.5;
  color: var(--el-text-color-secondary, #86909c);
}

.attention-list {
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.attention-list__summary {
  flex-shrink: 0;
  padding-top: 0.625rem;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.attention-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.875rem;
  border-radius: 0.5rem;
  border: 1px solid;
  font-size: 13px;
  line-height: 1.5;
}

.attention-item--critical {
  background: color-mix(in srgb, var(--dashboard-error-soft) 72%, transparent);
  border-color: var(--dashboard-error-border);
  color: var(--dashboard-error-strong);
}

.attention-item--warning {
  background: color-mix(in srgb, var(--dashboard-warning-soft) 72%, transparent);
  border-color: var(--dashboard-warning-border);
  color: var(--dashboard-warning-strong);
}

.attention-item--info {
  background: color-mix(in srgb, var(--dashboard-info-soft) 72%, transparent);
  border-color: var(--dashboard-info-border);
  color: var(--dashboard-info-strong);
}

.attention-item__content {
  display: flex;
  gap: 0.625rem;
  min-width: 0;
}

.attention-item__icon {
  width: 1rem;
  height: 1rem;
  flex-shrink: 0;
  margin-top: 0.125rem;
}

.attention-item--critical .attention-item__icon {
  color: var(--dashboard-error);
}

.attention-item--warning .attention-item__icon {
  color: var(--dashboard-warning);
}

.attention-item__title {
  margin: 0;
  font-weight: 600;
}

.attention-item__detail,
.attention-item__time {
  display: block;
  margin-top: 0.125rem;
  font-size: 12px;
  color: #86909c;
}

.attention-item__actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
}

.attention-item__solve {
  padding: 0.25rem 0.625rem;
  font-size: 12px;
  font-weight: 500;
  border-radius: 4px;
  background: #fff;
  border: 1px solid #e5e7eb;
  color: #374151;
  text-decoration: none;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}

.attention-item__solve:hover {
  background: #f9fafb;
}

/* ---- Storage ---- */
.panel--storage-capacity {
  overflow: hidden;
}

.storage-capacity-head__actions {
  display: inline-flex;
  min-width: 0;
  flex-shrink: 0;
  align-items: center;
  justify-content: flex-end;
  gap: 0.625rem;
}

.storage-capacity-head__actions .capacity-planner__status-chip {
  max-width: 118px;
}

.storage-capacity-body {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  gap: 0.625rem;
}

.storage-capacity-planner {
  display: flex;
  flex-shrink: 0;
  min-width: 0;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.625rem;
  border: 1px solid #eef2f7;
  border-radius: 8px;
  background: #fafbfc;
}

.storage-capacity-planner__title {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  color: #4e5969;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.2;
}

.storage-capacity-planner__title-main {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 0.375rem;
}

.storage-capacity-planner__title .capacity-planner__status-chip {
  max-width: 124px;
}

.storage-capacity-planner__title svg {
  flex-shrink: 0;
  color: var(--dashboard-primary);
}

.storage-capacity-repos {
  display: flex;
  flex: 1 1 72px;
  flex-direction: column;
}

.storage-empty {
  text-align: center;
  padding: 0.5rem 0;
  color: #86909c;
  font-size: 13px;
}

.storage-list {
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.storage-item {
  font-size: 13px;
  min-width: 0;
}

.storage-item__head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) max-content;
  align-items: center;
  gap: 0.75rem;
  font-weight: 500;
}

.storage-item__name {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 13px;
  color: #1d2129;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.storage-item__name-main {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 0.375rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.storage-item__health {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 9999px;
  background: var(--dashboard-success);
  flex-shrink: 0;
}

.storage-item__health--offline {
  background: var(--dashboard-error);
}

.storage-item__health--unverified {
  background: var(--dashboard-warning);
}

.storage-item__name-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.storage-item__health-label {
  display: inline-flex;
  min-height: 18px;
  align-items: center;
  padding: 0 0.375rem;
  border: 1px solid var(--dashboard-success-border);
  border-radius: 999px;
  background: var(--dashboard-success-soft);
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  line-height: 16px;
  color: var(--dashboard-success-strong);
}

.storage-item__health-label--offline {
  border-color: var(--dashboard-error-border);
  background: var(--dashboard-error-soft);
  color: var(--dashboard-error);
}

.storage-item__health-label--unverified {
  border-color: var(--dashboard-warning-border);
  background: var(--dashboard-warning-soft);
  color: var(--dashboard-warning-strong);
}

.storage-item__bytes {
  display: inline-flex;
  align-items: baseline;
  flex-wrap: nowrap;
  justify-content: flex-end;
  gap: 0.25rem;
  min-width: 0;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: #1d2129;
  text-align: right;
  white-space: nowrap;
}

.storage-item__used {
  color: #1d2129;
}

.storage-item__capacity {
  font-weight: 500;
  color: #86909c;
}

.storage-item__bytes--unlimited .storage-item__capacity {
  color: #64748b;
}

.storage-item__pct {
  font-size: 12px;
  font-weight: 600;
  color: var(--dashboard-primary);
}

.storage-track {
  height: 0.375rem;
  border-radius: 9999px;
  background: #f3f4f6;
  overflow: hidden;
}

.storage-fill {
  height: 100%;
  border-radius: 9999px;
  background: var(--dashboard-primary);
  transition: width 0.7s;
}

.storage-more-inline {
  display: inline-flex;
  align-items: baseline;
  margin-left: 0.25rem;
  color: var(--dashboard-primary);
  font-size: 12px;
  font-weight: 600;
  text-decoration: none;
}

.storage-more-inline:hover {
  color: var(--dashboard-primary-hover);
}

/* ---- Capacity planner ---- */
.panel--capacity-planner {
  overflow: hidden;
}

.capacity-planner__status-chip {
  flex-shrink: 0;
  max-width: 44%;
  min-height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0.125rem 0.5rem;
  border-radius: 9999px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 600;
}

.capacity-planner__status-chip--ok {
  background: var(--dashboard-success-soft);
  color: var(--dashboard-success-strong);
}

.capacity-planner__status-chip--warning {
  background: var(--dashboard-warning-soft);
  color: var(--dashboard-warning-strong);
}

.capacity-planner__status-chip--danger {
  background: var(--dashboard-error-soft);
  color: var(--dashboard-error);
}

.capacity-planner__status-chip--neutral {
  background: #f2f3f5;
  color: #4e5969;
}

.capacity-planner__hint {
  flex-shrink: 0;
  max-width: 44%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: #86909c;
}

.capacity-planner__body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
}

.capacity-planner__form {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(96px, 0.55fr);
  align-items: end;
  gap: 0.625rem;
}

.panel--storage-capacity .capacity-planner__form {
  grid-template-columns: minmax(0, 1fr) 96px;
  gap: 0.625rem;
}

.capacity-planner__field {
  min-width: 0;
}

.capacity-planner__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 500;
  color: #4e5969;
}

.panel--storage-capacity .capacity-planner__label {
  font-size: 11px;
}

.capacity-planner__controls {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 72px;
  gap: 0.5rem;
  min-width: 0;
}

.panel--storage-capacity .capacity-planner__controls {
  grid-template-columns: minmax(86px, 1fr) 72px;
  gap: 0.5rem;
}

.capacity-planner__amount,
.capacity-planner__unit,
.capacity-planner__factor {
  width: 100%;
}

.capacity-planner__amount :deep(.el-input__wrapper),
.capacity-planner__unit :deep(.el-select__wrapper),
.capacity-planner__factor :deep(.el-input__wrapper) {
  min-height: 30px;
}

.panel--storage-capacity .capacity-planner__amount :deep(.el-input__wrapper),
.panel--storage-capacity .capacity-planner__unit :deep(.el-select__wrapper),
.panel--storage-capacity .capacity-planner__factor :deep(.el-input__wrapper) {
  min-height: 28px;
}

.capacity-planner__summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  min-height: 28px;
  padding: 0.25rem 0.5rem;
  border-radius: 8px;
  background: var(--dashboard-success-soft);
  color: var(--dashboard-success-strong);
}

.capacity-planner__summary span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 500;
}

.capacity-planner__summary strong {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.capacity-planner__metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.5rem;
}

.panel--storage-capacity .capacity-planner__metrics {
  gap: 0.5rem;
}

.capacity-planner__metric {
  min-width: 0;
  padding: 0.375rem 0.5rem;
  border: 1px solid #f2f4f7;
  border-radius: 8px;
  background: #fafbfc;
}

.panel--storage-capacity .capacity-planner__metric {
  padding: 0.375rem 0.5rem;
  background: #fff;
}

.capacity-planner__metric span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: #86909c;
}

.panel--storage-capacity .capacity-planner__metric span {
  font-size: 11px;
}

.capacity-planner__metric strong {
  display: block;
  margin-top: 0.125rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: #1d2129;
}

.panel--storage-capacity .capacity-planner__metric strong {
  font-size: 12px;
}

@media (max-width: 767px) {
  .capacity-planner__form {
    grid-template-columns: 1fr;
    gap: 0.5rem;
  }
}

/* ---- Quota ---- */
.panel--quota .quota-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 1.25rem 2rem;
  flex: 1;
  align-content: center;
  overflow-y: auto;
}

@media (min-width: 480px) {
  .panel--quota .quota-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.quota-item__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  min-width: 0;
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 0.375rem;
  gap: 0.5rem;
}

.quota-item__label {
  flex: 1 1 auto;
  min-width: 0;
  color: #4e5969;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.quota-item__nums {
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
  color: #1d2129;
  font-size: 12px;
  white-space: nowrap;
}

.quota-item__limit {
  color: #86909c;
  font-weight: 400;
}

.quota-item__unit {
  font-size: 12px;
  color: #86909c;
  margin-left: 0.125rem;
}

.quota-pct {
  margin-left: 0.5rem;
  font-size: 12px;
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
  font-variant-numeric: tabular-nums;
}

.quota-pct--normal {
  background: #f3f4f6;
  color: #4b5563;
}

.quota-pct--warning {
  background: color-mix(in srgb, var(--dashboard-warning-soft) 78%, transparent);
  color: var(--dashboard-warning-strong);
  font-weight: 600;
}

.quota-track {
  height: 0.5rem;
  border-radius: 9999px;
  background: #f3f4f6;
  overflow: hidden;
}

.quota-bar {
  height: 100%;
  border-radius: 9999px;
  transition: width 1s;
}

.quota-bar--normal {
  background: linear-gradient(to right, var(--dashboard-primary), var(--dashboard-primary-mid));
}

.quota-bar--warning {
  background: linear-gradient(to right, var(--dashboard-warning), var(--dashboard-warning-mid));
}

/* ---- Running Jobs ---- */
.running-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.running-item {
  font-size: 13px;
  border-bottom: 1px dashed #f3f4f6;
  padding-bottom: 0.75rem;
}

.running-item:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.running-item__head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 0.25rem;
  font-weight: 500;
}

.running-item__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 160px;
  color: #1d2129;
}

.running-item__pct {
  font-variant-numeric: tabular-nums;
  color: var(--dashboard-primary);
  font-weight: 600;
}

.running-track {
  height: 0.375rem;
  border-radius: 9999px;
  background: #f3f4f6;
  overflow: hidden;
  margin-bottom: 0.375rem;
}

.running-fill {
  height: 100%;
  border-radius: 9999px;
  background: linear-gradient(to right, var(--dashboard-primary), var(--dashboard-primary-mid));
  transition: width 0.5s;
}

.running-item__meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #86909c;
}

/* ---- Chart ---- */
.chart-status {
  min-height: 1.5rem;
  display: flex;
  align-items: center;
}

.chart-inline-tip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  font-size: 12px;
  background: #f8fafc;
  border: 1px solid rgba(226, 232, 240, 0.6);
  padding: 0.25rem 0.625rem;
  border-radius: 0.375rem;
  font-variant-numeric: tabular-nums;
}

@media (min-width: 768px) {
  .chart-inline-tip {
    font-size: 13px;
  }
}

.chart-inline-tip__date {
  color: #86909c;
  font-weight: 600;
}

.chart-inline-tip__success {
  color: var(--dashboard-success);
  font-weight: 600;
}

.chart-inline-tip__fail {
  color: var(--dashboard-error);
  font-weight: 600;
}

.chart-inline-tip__sep {
  color: #c9cdd4;
  font-weight: 500;
  user-select: none;
}

.chart-inline-tip__cancel {
  color: #86909c;
  font-weight: 600;
}

.chart-inline-tip__rate {
  background: var(--dashboard-primary-soft);
  color: var(--dashboard-primary-strong);
  border: 1px solid var(--dashboard-primary-border);
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
}

.chart-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  padding-left: 1.75rem;
  padding-right: 0.25rem;
  margin-top: 0.25rem;
  min-height: 0;
}

.chart-plot {
  position: relative;
  height: 115px;
  width: 100%;
}

.chart-y-row {
  position: absolute;
  inset-inline: 0;
  display: flex;
  align-items: center;
  pointer-events: none;
}

.chart-y-label {
  position: absolute;
  left: -1.75rem;
  width: 1.5rem;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  color: #86909c;
  text-align: right;
  padding-right: 0.25rem;
}

.chart-y-line {
  flex: 1;
  border-top: 1px dashed #f2f4f7;
  height: 0;
}

.chart-bars {
  position: absolute;
  inset: 0;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
}

.chart-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
  justify-content: flex-end;
  cursor: pointer;
}

.chart-col__pillar {
  width: 24px;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  position: relative;
  z-index: 1;
}

.chart-pillar {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  overflow: hidden;
  border-radius: 4px 4px 0 0;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  transition: all 0.3s;
}

.chart-pillar--hover {
  box-shadow: 0 0 12px color-mix(in srgb, var(--dashboard-success) 25%, transparent);
  filter: saturate(1.1);
  transform: scaleX(1.05);
}

.chart-seg {
  width: 100%;
}

.chart-seg--cancel {
  background: linear-gradient(to top, #64748b, #94a3b8);
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

.chart-seg--fail {
  background: linear-gradient(to top, var(--dashboard-error), var(--dashboard-error-mid));
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

.chart-seg--success {
  background: linear-gradient(to top, var(--dashboard-success), var(--dashboard-success-mid));
}

.chart-zero {
  width: 1rem;
  height: 3px;
  background: rgba(226, 232, 240, 0.8);
  border-radius: 9999px;
  margin: 0 auto;
}

.chart-x-label {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  color: #86909c;
  margin-top: 0.5rem;
  transition: color 0.2s;
  user-select: none;
}

.chart-x-label--active {
  color: var(--dashboard-primary);
  font-weight: 600;
}

.chart-legend {
  margin-top: 0.75rem;
  padding-top: 0.625rem;
  border-top: 1px solid #f2f4f7;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1.5rem;
  font-size: 12px;
  font-weight: 500;
  color: #86909c;
}

.chart-legend__item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.chart-legend__swatch {
  width: 0.75rem;
  height: 0.75rem;
  border-radius: 3px;
}

.chart-legend__swatch--success {
  background: linear-gradient(to top right, var(--dashboard-success), var(--dashboard-success-mid));
  box-shadow: 0 1px 2px color-mix(in srgb, var(--dashboard-success) 20%, transparent);
}

.chart-legend__swatch--fail {
  background: linear-gradient(to top right, var(--dashboard-error), var(--dashboard-error-mid));
  box-shadow: 0 1px 2px color-mix(in srgb, var(--dashboard-error) 20%, transparent);
}

.chart-legend__swatch--cancel {
  background: linear-gradient(to top right, #64748b, #94a3b8);
  box-shadow: 0 1px 2px rgba(100, 116, 139, 0.2);
}

@keyframes ping {
  75%,
  100% {
    transform: scale(2);
    opacity: 0;
  }
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.6;
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

@keyframes flow {
  from {
    transform: translateX(-100%);
  }
  to {
    transform: translateX(400%);
  }
}
</style>
