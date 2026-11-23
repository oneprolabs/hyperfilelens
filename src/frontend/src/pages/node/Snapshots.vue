<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RefreshCw } from 'lucide-vue-next'
import { listBackupSourceSnapshots, type BackupSourceSnapshot } from '../../lib/protectionBackupConfigApi'
import { usePageRequestScope } from '../../composables/usePageRequestScope'
import { formatLocalDateTime } from '../../lib/dateTime'
import ErrorState from '../../components/errors/ErrorState.vue'
import ModulePage from '../../components/ModulePage.vue'
import SnapshotStatusTag from '../../components/SnapshotStatusTag.vue'
import { useProtectionSideNav } from '../../composables/useProtectionSideNav'

function fmtBytes(n?: number | null) {
  if (!n || n <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let index = 0
  let value = n
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(index >= 2 ? 1 : 0)} ${units[index]}`
}

function formatDate(value?: string | null) {
  return formatLocalDateTime(value, '—')
}

const rows = ref<BackupSourceSnapshot[]>([])
const busy = ref(false)
const page = ref(1)
const pageSize = 30
const count = ref(0)
const loadError = ref<unknown>(null)
const requests = usePageRequestScope()

const { t } = useI18n()
const protectionMenus = useProtectionSideNav()

async function load() {
  const signal = requests.nextSignal('snapshots')
  busy.value = true
  loadError.value = null
  try {
    const result = await listBackupSourceSnapshots({ page: page.value, page_size: pageSize }, { signal })
    if (signal.aborted) return
    rows.value = result.results
    count.value = result.count
  } catch (error) {
    if (!signal.aborted) loadError.value = error
  } finally {
    requests.releaseSignal('snapshots', signal)
    if (!signal.aborted) busy.value = false
  }
}

function progressLabel(row: BackupSourceSnapshot) {
  const total = Number(row.directory_count || 0)
  if (!total) return '0/0'
  const success = Number(row.successful_directory_count || 0)
  const failed = Number(row.failed_directory_count || 0)
  return `${success}/${total}${failed ? ` (${failed} failed)` : ''}`
}

watch(page, load, { immediate: true })
</script>

<template>
  <ModulePage :menus="protectionMenus">
    <template #actions>
      <ElButton
        :disabled="busy"
        @click="load"
      >
        <RefreshCw :size="16" /> {{ t('protection.snapshotsPage.refresh') }}
      </ElButton>
    </template>

    <div class="hfl-list-panel">
      <ErrorState v-if="loadError" :error="loadError" @retry="load" />
      <el-table
        v-table-column-resize="'node.snapshots'"
        :data="rows"
        stripe
        v-loading="busy"
      >
        <el-table-column
          prop="id"
          label="ID"
          width="80"
        >
          <template #default="{ row }">
            #{{ row.id }}
          </template>
        </el-table-column>
        <el-table-column
          prop="source_display_name"
          :label="t('protection.snapshotsPage.colSource')"
          min-width="180"
          show-overflow-tooltip
        />
        <el-table-column
          prop="backup_config_name"
          :label="t('protection.snapshotsPage.colBackupConfig')"
          min-width="180"
          show-overflow-tooltip
        />
        <el-table-column
          prop="repository_display_name"
          :label="t('protection.snapshotsPage.colRepo')"
          min-width="160"
          show-overflow-tooltip
        />
        <el-table-column
          prop="snapshot_uid"
          :label="t('protection.snapshotsPage.colSnapshotUid')"
          min-width="220"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span class="hfl-table-cell-mono">{{ row.snapshot_uid }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.snapshotsPage.fieldStatus')"
          width="120"
        >
          <template #default="{ row }">
            <SnapshotStatusTag :status="row.status" />
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.snapshotsPage.colDirectoryProgress')"
          min-width="140"
        >
          <template #default="{ row }">
            {{ progressLabel(row) }}
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.snapshotsPage.colSize')"
          width="120"
        >
          <template #default="{ row }">
            {{ fmtBytes(row.total_size_bytes) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="finished_at"
          :label="t('protection.snapshotsPage.colFinished')"
          min-width="180"
        >
          <template #default="{ row }">
            <span
              class="hfl-table-cell-time"
              :class="{ 'hfl-empty-mark': !(row.finished_at || row.created_at) }"
            >{{ formatDate(row.finished_at || row.created_at) }}</span>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty
            :description="t('protection.snapshotsPage.emptySnapshots')"
            :image-size="80"
          />
        </template>
      </el-table>
      <div class="hfl-list-footer">
        <ElPagination
          v-model:current-page="page"
          class="hfl-list-footer__pagination"
          :total="count"
          :page-size="pageSize"
          layout="total, prev, pager, next"
        />
      </div>
    </div>
  </ModulePage>
</template>
