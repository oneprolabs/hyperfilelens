<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { RefreshCw, Search as SearchIcon } from 'lucide-vue-next'
import { api } from '../lib/api'
import { asList } from '../lib/parse'
import ModulePage from '../components/ModulePage.vue'
import HflTypeLabel from '../components/HflTypeLabel.vue'
import SnapshotStatusTag from '../components/SnapshotStatusTag.vue'
import TaskStatusTag from '../components/TaskStatusTag.vue'
import TaskTypeLabel from '../components/TaskTypeLabel.vue'
import ErrorState from '../components/errors/ErrorState.vue'
import { useDebouncedAction } from '../composables/useDebouncedAction'
import { operationalSearchAliases, taskAlertDisplay } from '../lib/operationalEventDisplay'
import { alertStatusTagAttrs as sharedAlertStatusTagAttrs, lifecycleStatusTagAttrs, severityStatusTagAttrs } from '../lib/statusTag'

type Task = { id: number; task_uuid: string; task_type: string; status: string; display_name?: string; created_at?: string }
type Node = { id: number; name: string; status?: string; updated_at?: string }
type Repo = { id: number; name: string; repo_type?: string; status?: string }
type Snapshot = {
  id: number
  snapshot_uid: string
  source_display_name?: string
  repository_display_name?: string
  status?: string
}
type Alert = { id: string; type?: string; title: string; status: string; severity?: string }

function includesAny(hay: string, q: string) {
  if (!q) return true
  return hay.toLowerCase().includes(q.toLowerCase())
}

const { t } = useI18n()
const route = useRoute()
const q = ref(String(route.query.q || ''))
const busy = ref(false)
const alertLoading = ref(false)
const alertError = ref<unknown>(null)
let alertRequestSequence = 0
const { schedule: scheduleAlertSearch } = useDebouncedAction(() => loadAlerts())

const tasks = ref<Task[]>([])
const nodes = ref<Node[]>([])
const repos = ref<Repo[]>([])
const snaps = ref<Snapshot[]>([])
const alerts = ref<Alert[]>([])

function nodeStatusLabel(status?: string) {
  const keys: Record<string, string> = {
    online: 'nodesPage.statusOnline',
    reconnecting: 'nodesPage.statusReconnecting',
    offline: 'nodesPage.statusOffline',
  }
  const key = keys[String(status || '')]
  return key ? t(key) : status || '—'
}

function repositoryTypeLabel(type?: string) {
  const keys: Record<string, string> = {
    s3: 'repositoriesPage.tabS3',
    nas: 'repositoriesPage.tabNas',
    proxy_fs: 'repositoriesPage.tabProxyFs',
  }
  const key = keys[String(type || '')]
  return key ? t(key) : type || '—'
}

function repositoryStatusLabel(status?: string) {
  const keys: Record<string, string> = {
    creating: 'repositoriesPage.statusCreating',
    create_failed: 'repositoriesPage.statusCreateFailed',
    created: 'repositoriesPage.statusCreated',
    removing: 'repositoriesPage.statusRemoving',
    remove_failed: 'repositoriesPage.statusRemoveFailed',
    removed: 'repositoriesPage.statusRemoved',
  }
  const key = keys[String(status || '')]
  return key ? t(key) : status || '—'
}

function alertSeverityLabel(severity?: string) {
  if (!severity) return '—'
  const key = `ops.alertsCenter.common.${severity}`
  const translated = t(key)
  return translated === key ? severity : translated
}

function alertSeverityTagAttrs(severity?: string) {
  return severityStatusTagAttrs(severity)
}

function alertStatusLabel(status?: string) {
  if (status === 'firing') return t('ops.alertsCenter.active.firing')
  if (status === 'acknowledged') return t('ops.alertsCenter.active.acknowledged')
  if (status === 'resolved') return t('ops.alerts.status.resolved')
  return status || '—'
}

function alertStatusTagAttrs(status?: string) {
  return sharedAlertStatusTagAttrs(status)
}

const side = computed(() => [
  { to: '/search', label: t('searchPage.sideGlobalSearch') },
  { to: '/insight/copilot', label: t('searchPage.sideInsight') },
])

async function load() {
  const [j, n, r, s] = await Promise.all([
    api<unknown>(`/api/v1/tasks/`),
    api<unknown>(`/api/v1/node/nodes/`),
    api<unknown>(`/api/v1/storage/repositories/`),
    api<unknown>(`/api/v1/protection/backup-source-snapshots/?page=1&page_size=100`),
    loadAlerts(),
  ])
  tasks.value = asList<Task>(j)
  nodes.value = asList<Node>(n)
  repos.value = asList<Repo>(r)
  snaps.value = asList<Snapshot>(s)
}

async function loadAlerts() {
  const requestId = ++alertRequestSequence
  alertLoading.value = true
  alertError.value = null
  const query = new URLSearchParams({ page_size: '20', search: q.value.trim(), search_context: 'global' })
  const aliases = operationalSearchAliases(q.value, t, 'alert')
  if (aliases.length) query.set('search_aliases', JSON.stringify(aliases))
  try {
    const response = await api<unknown>(`/api/v1/alerts/records/?${query}`)
    if (requestId === alertRequestSequence) alerts.value = asList<Alert>(response)
  } catch (error) {
    if (requestId === alertRequestSequence) alertError.value = error
  } finally {
    if (requestId === alertRequestSequence) alertLoading.value = false
  }
}

onMounted(() => {
  load().catch(() => {
    tasks.value = []
    nodes.value = []
    repos.value = []
    snaps.value = []
  })
})
onBeforeUnmount(() => { alertRequestSequence += 1 })
watch(q, () => {
  alertRequestSequence += 1
  alertLoading.value = true
  scheduleAlertSearch()
})

watch(
  () => route.query.q,
  (v) => {
    q.value = String(v || '')
  },
)

const taskRows = computed(() =>
  tasks.value
    .filter((x) => includesAny(`${x.id} ${x.task_uuid} ${x.task_type} ${x.status} ${x.display_name || ''}`, q.value))
    .slice(0, 20),
)
const nodeRows = computed(() => nodes.value.filter((x) => includesAny(`${x.id} ${x.name} ${x.status || ''}`, q.value)).slice(0, 20))
const repoRows = computed(() => repos.value.filter((x) => includesAny(`${x.id} ${x.name} ${x.repo_type || ''} ${x.status || ''}`, q.value)).slice(0, 20))
const snapRows = computed(() =>
  snaps.value
    .filter((x) => includesAny(`${x.id} ${x.snapshot_uid} ${x.source_display_name || ''} ${x.repository_display_name || ''} ${x.status || ''}`, q.value))
    .slice(0, 20),
)
const alertRows = computed(() =>
  alerts.value.map(alert => ({ ...alert, title: taskAlertDisplay(alert, t).title })),
)

async function onRefresh() {
  busy.value = true
  try {
    await load()
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <ModulePage :side="side">
    <template #actions>
      <ElButton
        :disabled="busy"
        @click="onRefresh"
      >
        <RefreshCw :size="16" /> {{ t('searchPage.refresh') }}
      </ElButton>
    </template>

    <div class="bg-[var(--color-card-bg,#fff)] p-4">
      <ElInput
        v-model="q"
        :placeholder="t('searchPage.searchPlaceholder')"
        clearable
      >
        <template #prefix>
          <SearchIcon
            :size="18"
            class="text-slate-400"
          />
        </template>
      </ElInput>
      <div class="mt-2 text-xs text-slate-500">
        {{ t('searchPage.searchHint') }}
      </div>
    </div>

    <div class="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-[var(--card-gap)]">
      <div class="hfl-list-panel">
        <div class="px-4 py-3 border-b border-slate-100 font-semibold text-slate-900">
          {{ t('searchPage.sectionTasks') }}
        </div>
        <el-table
          :data="taskRows"
          stripe
          :loading="busy"
        >
          <el-table-column
            prop="id"
            :label="t('searchPage.colId')"
            width="80"
          >
            <template #default="{ row }">
              #{{ row.id }}
            </template>
          </el-table-column>
          <el-table-column
            prop="display_name"
            :label="t('searchPage.colName')"
            min-width="160"
          />
          <el-table-column
            :label="t('searchPage.colType')"
            min-width="150"
          >
            <template #default="{ row }">
              <TaskTypeLabel :type="row.task_type" />
            </template>
          </el-table-column>
          <el-table-column
            :label="t('searchPage.colStatus')"
            width="120"
          >
            <template #default="{ row }">
              <TaskStatusTag :status="row.status" />
            </template>
          </el-table-column>
          <template #empty>
            <el-empty
              :description="t('searchPage.empty')"
              :image-size="80"
            />
          </template>
        </el-table>
      </div>

      <div class="hfl-list-panel">
        <div class="px-4 py-3 border-b border-slate-100 font-semibold text-slate-900">
          {{ t('searchPage.sectionNodes') }}
        </div>
        <el-table
          :data="nodeRows"
          stripe
          :loading="busy"
        >
          <el-table-column
            prop="id"
            :label="t('searchPage.colId')"
            width="80"
          >
            <template #default="{ row }">
              #{{ row.id }}
            </template>
          </el-table-column>
          <el-table-column
            prop="name"
            :label="t('searchPage.colName')"
            min-width="160"
          />
          <el-table-column
            :label="t('searchPage.colStatus')"
            width="120"
          >
            <template #default="{ row }">
              <ElTag
                v-bind="lifecycleStatusTagAttrs(row.status)"
                size="small"
              >
                {{ nodeStatusLabel(row.status) }}
              </ElTag>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty
              :description="t('searchPage.empty')"
              :image-size="80"
            />
          </template>
        </el-table>
      </div>

      <div class="hfl-list-panel">
        <div class="px-4 py-3 border-b border-slate-100 font-semibold text-slate-900">
          {{ t('searchPage.sectionRepos') }}
        </div>
        <el-table
          :data="repoRows"
          stripe
          :loading="busy"
        >
          <el-table-column
            prop="id"
            :label="t('searchPage.colId')"
            width="80"
          >
            <template #default="{ row }">
              #{{ row.id }}
            </template>
          </el-table-column>
          <el-table-column
            prop="name"
            :label="t('searchPage.colName')"
            min-width="160"
          />
          <el-table-column
            :label="t('searchPage.colType')"
            width="140"
          >
            <template #default="{ row }">
              <HflTypeLabel :label="repositoryTypeLabel(row.repo_type)" />
            </template>
          </el-table-column>
          <el-table-column
            :label="t('searchPage.colStatus')"
            width="120"
          >
            <template #default="{ row }">
              <ElTag
                v-bind="lifecycleStatusTagAttrs(row.status)"
                size="small"
              >
                {{ repositoryStatusLabel(row.status) }}
              </ElTag>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty
              :description="t('searchPage.empty')"
              :image-size="80"
            />
          </template>
        </el-table>
      </div>

      <div class="hfl-list-panel">
        <div class="px-4 py-3 border-b border-slate-100 font-semibold text-slate-900">
          {{ t('searchPage.sectionSnapshots') }}
        </div>
        <el-table
          :data="snapRows"
          stripe
          :loading="busy"
        >
          <el-table-column
            prop="id"
            :label="t('searchPage.colId')"
            width="80"
          >
            <template #default="{ row }">
              #{{ row.id }}
            </template>
          </el-table-column>
          <el-table-column
            prop="snapshot_uid"
            :label="t('searchPage.colName')"
            min-width="180"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              <span class="hfl-table-cell-mono">{{ row.snapshot_uid }}</span>
            </template>
          </el-table-column>
          <el-table-column
            prop="repository_display_name"
            :label="t('searchPage.colRepo')"
            min-width="140"
            show-overflow-tooltip
          />
          <el-table-column
            prop="source_display_name"
            :label="t('searchPage.colSource')"
            min-width="140"
            show-overflow-tooltip
          />
          <el-table-column
            :label="t('searchPage.colStatus')"
            width="120"
          >
            <template #default="{ row }">
              <SnapshotStatusTag :status="row.status" />
            </template>
          </el-table-column>
          <template #empty>
            <el-empty
              :description="t('searchPage.empty')"
              :image-size="80"
            />
          </template>
        </el-table>
      </div>

      <div class="hfl-list-panel lg:col-span-2">
        <div class="px-4 py-3 border-b border-slate-100 font-semibold text-slate-900">
          {{ t('searchPage.sectionAlerts') }}
        </div>
        <ErrorState
          v-if="alertError"
          :error="alertError"
          @retry="loadAlerts"
        />
        <el-table
          v-loading="alertLoading"
          :data="alertRows"
          stripe
        >
          <el-table-column
            prop="id"
            :label="t('searchPage.colId')"
            width="80"
          >
            <template #default="{ row }">
              #{{ row.id }}
            </template>
          </el-table-column>
          <el-table-column
            prop="title"
            :label="t('searchPage.colTitle')"
            min-width="200"
          />
          <el-table-column
            :label="t('searchPage.colSeverity')"
            width="120"
          >
            <template #default="{ row }">
              <ElTag
                v-bind="alertSeverityTagAttrs(row.severity)"
                size="small"
              >
                {{ alertSeverityLabel(row.severity) }}
              </ElTag>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('searchPage.colStatus')"
            width="120"
          >
            <template #default="{ row }">
              <ElTag
                v-bind="alertStatusTagAttrs(row.status)"
                size="small"
              >
                {{ alertStatusLabel(row.status) }}
              </ElTag>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty
              :description="t('searchPage.empty')"
              :image-size="80"
            />
          </template>
        </el-table>
      </div>
    </div>
  </ModulePage>
</template>
