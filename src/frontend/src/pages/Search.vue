<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { RefreshCw, Search as SearchIcon } from 'lucide-vue-next'
import { api } from '../lib/api'
import { asList, asPagination, unwrapApiPayload } from '../lib/parse'
import HflPagination from '../components/HflPagination.vue'
import ModulePage from '../components/ModulePage.vue'
import HflTypeLabel from '../components/HflTypeLabel.vue'
import SnapshotStatusTag from '../components/SnapshotStatusTag.vue'
import TaskStatusTag from '../components/TaskStatusTag.vue'
import TaskTypeLabel from '../components/TaskTypeLabel.vue'
import ErrorState from '../components/errors/ErrorState.vue'
import { useDebouncedAction } from '../composables/useDebouncedAction'
import { usePageRequestScope } from '../composables/usePageRequestScope'
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

const { t } = useI18n()
const route = useRoute()
const q = ref(String(route.query.q || ''))
const busy = ref(false)
const alertLoading = ref(false)
const alertError = ref<unknown>(null)
const searchError = ref<unknown>(null)
const requests = usePageRequestScope()
const { schedule, runNow } = useDebouncedAction(() => void load())

const tasks = ref<Task[]>([])
const nodes = ref<Node[]>([])
const repos = ref<Repo[]>([])
const snaps = ref<Snapshot[]>([])
const alerts = ref<Alert[]>([])
const pageSize = 20
const sections = {
  tasks: { path: '/api/v1/tasks/', rows: tasks },
  nodes: { path: '/api/v1/node/nodes/', rows: nodes },
  repos: { path: '/api/v1/storage/repositories/', rows: repos },
  snaps: { path: '/api/v1/protection/backup-source-snapshots/', rows: snaps },
}
type SearchSection = keyof typeof sections | 'alerts'
const pagination = reactive({
  tasks: { page: 1, total: 0 }, nodes: { page: 1, total: 0 }, repos: { page: 1, total: 0 },
  snaps: { page: 1, total: 0 }, alerts: { page: 1, total: 0 },
})

function resultCount(response: unknown, size: number) {
  const payload = unwrapApiPayload<{ count?: number }>(response)
  return asPagination(response)?.total ?? payload?.count ?? size
}

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

async function load(section?: keyof typeof sections) {
  const signal = requests.nextSignal('search')
  busy.value = true
  searchError.value = null
  async function loadSection<Row>(key: keyof typeof sections, path: string, rows: { value: Row[] }) {
    const query = new URLSearchParams({ page: String(pagination[key].page), page_size: String(pageSize) })
    if (q.value.trim()) query.set('search', q.value.trim())
    try {
      const response = await api<unknown>(`${path}?${query}`, { signal })
      if (!signal.aborted) {
        rows.value = asList<Row>(response)
        pagination[key].total = resultCount(response, rows.value.length)
      }
    } catch (error) {
      if (signal.aborted) return
      rows.value = []
      searchError.value ??= error
    }
  }

  try {
    await Promise.all([
      !section || section === 'tasks' ? loadSection('tasks', sections.tasks.path, tasks) : null,
      !section || section === 'nodes' ? loadSection('nodes', sections.nodes.path, nodes) : null,
      !section || section === 'repos' ? loadSection('repos', sections.repos.path, repos) : null,
      !section || section === 'snaps' ? loadSection('snaps', sections.snaps.path, snaps) : null,
      !section ? loadAlerts() : null,
    ])
  } finally {
    requests.releaseSignal('search', signal)
    if (!signal.aborted) busy.value = false
  }
}

async function loadAlerts() {
  const signal = requests.nextSignal('alerts')
  alertLoading.value = true
  alertError.value = null
  const query = new URLSearchParams({ page: String(pagination.alerts.page), page_size: String(pageSize), search: q.value.trim(), search_context: 'global' })
  const aliases = operationalSearchAliases(q.value, t, 'alert')
  if (aliases.length) query.set('search_aliases', JSON.stringify(aliases))
  try {
    const response = await api<unknown>(`/api/v1/alerts/records/?${query}`, { signal })
    if (!signal.aborted) {
      alerts.value = asList<Alert>(response)
      pagination.alerts.total = resultCount(response, alerts.value.length)
    }
  } catch (error) {
    if (!signal.aborted) alertError.value = error
  } finally {
    requests.releaseSignal('alerts', signal)
    if (!signal.aborted) alertLoading.value = false
  }
}

async function changePage(section: SearchSection, page: number) {
  if (busy.value || alertLoading.value) return
  pagination[section].page = page
  if (section === 'alerts') {
    await loadAlerts()
  } else {
    await load(section)
  }
}

onMounted(runNow)
watch(q, () => {
  requests.abortScope('search')
  requests.abortScope('alerts')
  busy.value = true
  alertLoading.value = true
  for (const state of Object.values(pagination)) {
    state.page = 1
    state.total = 0
  }
  schedule()
})

watch(
  () => route.query.q,
  (v) => {
    q.value = String(v || '')
  },
)

const alertRows = computed(() =>
  alerts.value.map(alert => ({ ...alert, title: taskAlertDisplay(alert, t).title })),
)

</script>

<template>
  <ModulePage :side="side">
    <template #actions>
      <ElButton
        :disabled="busy"
        @click="runNow"
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

    <ErrorState v-if="searchError" :error="searchError" @retry="runNow" />
    <div class="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-[var(--card-gap)]">
      <div class="hfl-list-panel">
        <div class="px-4 py-3 border-b border-slate-100 font-semibold text-slate-900">
          {{ t('searchPage.sectionTasks') }}
        </div>
        <el-table
          v-loading="busy"
          :data="tasks"
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
        <HflPagination
          v-if="pagination.tasks.total > pageSize"
          :current-page="pagination.tasks.page"
          :page-size="pageSize"
          :total="pagination.tasks.total"
          layout="prev, pager, next"
          @update:current-page="changePage('tasks', $event)"
        />
      </div>

      <div class="hfl-list-panel">
        <div class="px-4 py-3 border-b border-slate-100 font-semibold text-slate-900">
          {{ t('searchPage.sectionNodes') }}
        </div>
        <el-table
          v-loading="busy"
          :data="nodes"
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
        <HflPagination
          v-if="pagination.nodes.total > pageSize"
          :current-page="pagination.nodes.page"
          :page-size="pageSize"
          :total="pagination.nodes.total"
          layout="prev, pager, next"
          @update:current-page="changePage('nodes', $event)"
        />
      </div>

      <div class="hfl-list-panel">
        <div class="px-4 py-3 border-b border-slate-100 font-semibold text-slate-900">
          {{ t('searchPage.sectionRepos') }}
        </div>
        <el-table
          v-loading="busy"
          :data="repos"
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
        <HflPagination
          v-if="pagination.repos.total > pageSize"
          :current-page="pagination.repos.page"
          :page-size="pageSize"
          :total="pagination.repos.total"
          layout="prev, pager, next"
          @update:current-page="changePage('repos', $event)"
        />
      </div>

      <div class="hfl-list-panel">
        <div class="px-4 py-3 border-b border-slate-100 font-semibold text-slate-900">
          {{ t('searchPage.sectionSnapshots') }}
        </div>
        <el-table
          v-loading="busy"
          :data="snaps"
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
        <HflPagination
          v-if="pagination.snaps.total > pageSize"
          :current-page="pagination.snaps.page"
          :page-size="pageSize"
          :total="pagination.snaps.total"
          layout="prev, pager, next"
          @update:current-page="changePage('snaps', $event)"
        />
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
        <HflPagination
          v-if="pagination.alerts.total > pageSize"
          :current-page="pagination.alerts.page"
          :page-size="pageSize"
          :total="pagination.alerts.total"
          layout="prev, pager, next"
          @update:current-page="changePage('alerts', $event)"
        />
      </div>
    </div>
  </ModulePage>
</template>
