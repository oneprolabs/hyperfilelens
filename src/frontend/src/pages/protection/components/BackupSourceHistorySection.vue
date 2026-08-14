<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Folder, File } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import type { ElTable } from 'element-plus'
import DangerConfirmDialog, { type DangerConfirmItem } from '../../../components/DangerConfirmDialog.vue'
import TaskStatusTag from '../../../components/TaskStatusTag.vue'
import type { DemoBackup, DemoFsNode, DemoSnapshotDir } from '../../../composables/useProtectionDemoStore'
import { useProtectionDemoStore } from '../../../composables/useProtectionDemoStore'
import { useResponsiveDrawerWidth } from '../../../composables/useResponsiveDrawerWidth'
import { useDrawerScrollReset } from '../../../composables/useDrawerScrollReset'
import { formatLocalDateTime } from '../../../lib/dateTime'
import {
  buildHistoryTasksForBackups,
  buildSnapshotRowsForBackups,
  fmtBackupBytes,
  type BackupHistoryTask,
  type SourceSnapshotRow,
} from '../composables/useBackupHistoryData'

const props = defineProps<{
  mode: 'snapshots' | 'tasks'
  backups: DemoBackup[]
}>()

const { t } = useI18n()
const store = useProtectionDemoStore()

const TABLE_HEADER_STYLE: Record<string, string> = {
  background: 'rgba(248, 250, 252, 0.96)',
  color: 'rgb(71 85 105)',
  fontWeight: '600',
  whiteSpace: 'nowrap',
}

type DrawerTreeRow = {
  label: string
  type: 'file' | 'dir'
  children?: DrawerTreeRow[]
}

function nodesToDrawerTree(nodes: DemoFsNode[]): DrawerTreeRow[] {
  return nodes.map((n) => ({
    label: n.name,
    type: n.type,
    children: n.children?.length ? nodesToDrawerTree(n.children) : undefined,
  }))
}

const showBackupName = computed(() => props.backups.length > 1)

const snapshotRows = computed(() => buildSnapshotRowsForBackups(props.backups))

function hostNamesForBackup(backup: DemoBackup) {
  return backup.sources.map((s) => store.getHost(s.hostId)?.name ?? s.hostId).join(', ')
}

const taskRows = computed(() =>
  buildHistoryTasksForBackups(t, props.backups, hostNamesForBackup),
)

const snapshotTableRef = ref<InstanceType<typeof ElTable>>()
const snapshotDrawerOpen = ref(false)
const activeSnapshotRow = ref<SourceSnapshotRow | null>(null)
const activeSnapshot = computed(() => activeSnapshotRow.value?.snapshot ?? null)
const deleteSnapshotDialogOpen = ref(false)
const deleteSnapshotLoading = ref(false)
const pendingDeleteSnapshotRow = ref<SourceSnapshotRow | null>(null)

const taskTableRef = ref<InstanceType<typeof ElTable>>()
const taskDrawerOpen = ref(false)
const activeTask = ref<BackupHistoryTask | null>(null)
const { drawerScrollAnchorRef, resetDrawerScroll } = useDrawerScrollReset()
const { drawerSize, updateDrawerWidth, bindDrawerResize, unbindDrawerResize } = useResponsiveDrawerWidth()
const { drawerSize: nestedDrawerSize } = useResponsiveDrawerWidth(2)

const dirTreeDrawerOpen = ref(false)
const dirTreeDrawerTitle = ref('')
const dirTreeDrawerRows = ref<DrawerTreeRow[]>([])

const deleteSnapshotItems = computed<DangerConfirmItem[]>(() => {
  const row = pendingDeleteSnapshotRow.value
  if (!row) return []
  return [{
    key: `${row.backupId}:${row.snapshot.id}`,
    name: row.snapshot.id,
    status: { label: t('protection.backupsPage.snapshotStatusAvailable'), tone: 'success' },
    description: `${row.snapshot.startTime} - ${row.snapshot.endTime} / ${fmtBackupBytes(row.snapshot.sizeBytes)}`,
  }]
})

function snapshotRowClassName({ row }: { row: SourceSnapshotRow }) {
  return activeSnapshotRow.value?.snapshot.id === row.snapshot.id &&
    activeSnapshotRow.value?.backupId === row.backupId
    ? 'snapshot-row--active'
    : ''
}

function snapshotRowKey(row: SourceSnapshotRow) {
  return `${row.backupId}:${row.snapshot.id}`
}

function taskRowKey(row: BackupHistoryTask) {
  return `${row.backupId}:${row.id}`
}

function formatDate(value?: string | null) {
  return formatLocalDateTime(value, t('protection.backupDetail.durationDash'))
}

function snapshotDirRowKey(row: DemoSnapshotDir) {
  return `${row.hostId}:${row.path}`
}

function snapshotDirKind(row: DemoSnapshotDir) {
  return row.pathType === 'file' ? 'file' : 'dir'
}

function snapshotDirIcon(row: DemoSnapshotDir) {
  return snapshotDirKind(row) === 'file' ? File : Folder
}

function openSnapshotDrawer(row: SourceSnapshotRow) {
  activeSnapshotRow.value = row
  snapshotDrawerOpen.value = true
  nextTick(() => {
    requestAnimationFrame(() => updateDrawerWidth())
  })
}

function onSnapshotDrawerOpened() {
  bindDrawerResize()
}

function onSnapshotDrawerClosed() {
  unbindDrawerResize()
  activeSnapshotRow.value = null
  dirTreeDrawerOpen.value = false
}

function openTaskDrawer(row: BackupHistoryTask) {
  activeTask.value = row
  taskDrawerOpen.value = true
  nextTick(() => {
    requestAnimationFrame(() => updateDrawerWidth())
  })
}

function onTaskDrawerOpened() {
  bindDrawerResize()
  resetDrawerScroll()
}

function onTaskDrawerClosed() {
  unbindDrawerResize()
  activeTask.value = null
}

function openDirTree(row: DemoSnapshotDir) {
  const snap = activeSnapshot.value
  if (!snap) return
  const tree = snap.treeByPath[row.path] ?? []
  dirTreeDrawerTitle.value = t('protection.backupDetail.drawerDirTitle', { path: row.path })
  dirTreeDrawerRows.value = nodesToDrawerTree(tree)
  dirTreeDrawerOpen.value = true
}

function onDeleteSnapshot(row: SourceSnapshotRow) {
  pendingDeleteSnapshotRow.value = row
  deleteSnapshotDialogOpen.value = true
}

function confirmDeleteSnapshot() {
  const row = pendingDeleteSnapshotRow.value
  if (!row || deleteSnapshotLoading.value) return
  deleteSnapshotLoading.value = true
  try {
    store.removeSnapshot(row.backupId, row.snapshot.id)
    ElMessage.success({ message: t('protection.backupDetail.msgSnapshotDeleted'), grouping: true })
    if (
      activeSnapshotRow.value?.backupId === row.backupId &&
      activeSnapshotRow.value?.snapshot.id === row.snapshot.id
    ) {
      snapshotDrawerOpen.value = false
    }
    deleteSnapshotDialogOpen.value = false
    pendingDeleteSnapshotRow.value = null
  } finally {
    deleteSnapshotLoading.value = false
  }
}

function closeDeleteSnapshotDialog() {
  if (deleteSnapshotLoading.value) return
  deleteSnapshotDialogOpen.value = false
}

onBeforeUnmount(() => {
  unbindDrawerResize()
})
</script>

<template>
  <div class="backup-source-history-section">
    <div v-if="mode === 'snapshots'">
      <el-table
        ref="snapshotTableRef"
        v-table-overflow-title
        :data="snapshotRows"
        stripe
        :row-key="snapshotRowKey"
        max-height="calc(var(--app-viewport-height) - 260px)"
        :header-cell-style="TABLE_HEADER_STYLE"
        class="hfl-list-table"
        :row-class-name="snapshotRowClassName"
      >
        <el-table-column
          v-if="showBackupName"
          :label="t('protection.backupsPage.flowBackupColBackupName')"
          min-width="140"
          prop="backupName"
        />
        <el-table-column
          :label="t('protection.backupDetail.colSnapStart')"
          min-width="200"
        >
          <template #default="{ row }">
            <button
              type="button"
              class="hfl-table-name-link hfl-table-name-link--single hfl-table-cell-time"
              @click="openSnapshotDrawer(row)"
            >
              {{ formatDate(row.snapshot.startTime) }}
            </button>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupDetail.colSnapEnd')"
          min-width="200"
        >
          <template #default="{ row }">
            <span
              class="hfl-table-cell-time"
              :class="{ 'hfl-empty-mark': !row.snapshot.endTime }"
            >{{ formatDate(row.snapshot.endTime) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupDetail.colSnapSize')"
          width="110"
          align="right"
        >
          <template #default="{ row }">
            {{ fmtBackupBytes(row.snapshot.sizeBytes) }}
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupDetail.colSnapId')"
          min-width="200"
        >
          <template #default="{ row }">
            <span class="hfl-table-cell-mono">{{ row.snapshot.id }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupDetail.colFileCount')"
          width="90"
          align="right"
        >
          <template #default="{ row }">
            {{ row.snapshot.fileCount }}
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupDetail.colDirCount')"
          width="90"
          align="right"
        >
          <template #default="{ row }">
            {{ row.snapshot.dirCount }}
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupDetail.colActions')"
          width="100"
          fixed="right"
          align="right"
        >
          <template #default="{ row }">
            <ElButton
              link
              type="danger"
              size="small"
              @click.stop="onDeleteSnapshot(row)"
            >
              {{ t('protection.backupDetail.btnDeleteSnapshot') }}
            </ElButton>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty
            :description="t('protection.backupDetail.emptySnapshots')"
            :image-size="64"
          />
        </template>
      </el-table>
    </div>

    <div v-else>
      <el-table
        ref="taskTableRef"
        v-table-overflow-title
        :data="taskRows"
        stripe
        :row-key="taskRowKey"
        max-height="calc(var(--app-viewport-height) - 260px)"
        :header-cell-style="TABLE_HEADER_STYLE"
        class="hfl-list-table"
      >
        <el-table-column
          v-if="showBackupName"
          :label="t('protection.backupsPage.flowBackupColBackupName')"
          min-width="130"
          prop="backupName"
        />
        <el-table-column
          :label="t('protection.backupDetail.colTaskId')"
          min-width="220"
        >
          <template #default="{ row }">
            <button
              type="button"
              class="hfl-table-name-link hfl-table-cell-mono hfl-table-name-link--single"
              @click="openTaskDrawer(row)"
            >
              {{ row.id }}
            </button>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupDetail.colTaskType')"
          width="120"
          prop="type"
        />
        <el-table-column
          :label="t('protection.backupDetail.colTaskStatus')"
          width="96"
        >
          <template #default="{ row }">
            <TaskStatusTag :status="row.status" />
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupDetail.colStart')"
          min-width="170"
          prop="startTime"
        >
          <template #default="{ row }">
            <span
              class="hfl-table-cell-time"
              :class="{ 'hfl-empty-mark': !row.startTime }"
            >{{ formatDate(row.startTime) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupDetail.colEnd')"
          min-width="170"
          prop="endTime"
        >
          <template #default="{ row }">
            <span
              class="hfl-table-cell-time"
              :class="{ 'hfl-empty-mark': !row.endTime }"
            >{{ formatDate(row.endTime) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupDetail.colDuration')"
          width="100"
          prop="duration"
        />
        <el-table-column
          :label="t('protection.backupDetail.colTrigger')"
          width="100"
          prop="trigger"
        />
        <el-table-column
          :label="t('protection.backupDetail.colLinkedSnap')"
          min-width="120"
          prop="snapshotId"
        >
          <template #default="{ row }">
            <span class="hfl-table-cell-mono">{{ row.snapshotId }}</span>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty
            :description="t('protection.backupDetail.emptyTasks')"
            :image-size="64"
          />
        </template>
      </el-table>
    </div>

    <ElDrawer
      v-model="snapshotDrawerOpen"
      direction="rtl"
      destroy-on-close
      append-to-body
      :modal="true"
      :z-index="3200"
      :size="drawerSize"
      class="snapshot-detail-drawer"
      @opened="onSnapshotDrawerOpened"
      @closed="onSnapshotDrawerClosed"
    >
      <template #header>
        <span class="snapshot-drawer-title">
          {{ t('protection.backupDetail.drawerSnapTitle', { id: activeSnapshot?.id || '' }) }}
        </span>
        <span
          v-if="activeSnapshotRow && showBackupName"
          class="ml-2 text-sm font-normal text-slate-500"
        >
          · {{ activeSnapshotRow.backupName }}
        </span>
      </template>
      <div
        v-if="activeSnapshot"
        class="snapshot-drawer-body"
      >
        <p class="text-sm text-slate-500 m-0 mb-3">
          {{
            t('protection.backupDetail.snapMetaLine', {
              start: activeSnapshot.startTime,
              end: activeSnapshot.endTime,
              size: fmtBackupBytes(activeSnapshot.sizeBytes),
            })
          }}
        </p>
        <div>
          <div class="px-4 py-3 border-b border-slate-100 text-sm font-medium text-slate-800">
            {{ t('protection.backupDetail.panelDirList') }}
          </div>
          <el-table
            v-table-column-resize="'protection.backupSourceHistory.dirs'"
            v-table-overflow-title
            :data="activeSnapshot.dirs"
            stripe
            :row-key="snapshotDirRowKey"
            max-height="calc(var(--app-viewport-height) - 250px)"
            :header-cell-style="TABLE_HEADER_STYLE"
            class="hfl-list-table"
          >
            <el-table-column
              :label="t('protection.backupDetail.colBackupDir')"
              min-width="260"
            >
              <template #default="{ row }">
                <button
                  type="button"
                  class="hfl-table-name-link backup-source-history-dir-link"
                  @click="openDirTree(row)"
                >
                  <span class="backup-source-history-dir-link__parent">
                    <component
                      :is="snapshotDirIcon(row)"
                      :size="15"
                      class="backup-source-history-dir-link__icon"
                      :class="`backup-source-history-dir-link__icon--${snapshotDirKind(row)}`"
                    />
                    <span class="backup-source-history-dir-link__path hfl-table-cell-mono">{{ row.path }}</span>
                  </span>
                  <span class="backup-source-history-dir-link__child">
                    <span
                      class="backup-source-history-dir-link__branch"
                      aria-hidden="true"
                    />
                    <span class="backup-source-history-dir-link__host">
                      {{
                        t('protection.backupDetail.hostSubline', {
                          name: row.hostName,
                          hostname: store.getHost(row.hostId)?.hostname || t('protection.backupDetail.durationDash'),
                        })
                      }}
                    </span>
                  </span>
                </button>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupDetail.colDirSize')"
              width="110"
              align="right"
            >
              <template #default="{ row }">
                {{ fmtBackupBytes(row.sizeBytes) }}
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.backupDetail.colFileCountDirs')"
              width="100"
              prop="fileCount"
              align="right"
            />
            <el-table-column
              :label="t('protection.backupDetail.colInnerDirs')"
              width="110"
              prop="innerDirCount"
              align="right"
            />
            <template #empty>
              <el-empty
                :description="t('protection.backupDetail.emptyDirs')"
                :image-size="64"
              />
            </template>
          </el-table>
        </div>
      </div>
    </ElDrawer>

    <ElDrawer
      v-model="taskDrawerOpen"
      direction="rtl"
      destroy-on-close
      append-to-body
      :modal="true"
      :z-index="3200"
      :size="drawerSize"
      class="task-detail-drawer"
      @opened="onTaskDrawerOpened"
      @closed="onTaskDrawerClosed"
    >
      <template #header>
        <span class="snapshot-drawer-title">
          {{ t('protection.backupDetail.drawerTaskTitle', { id: activeTask?.id || '' }) }}
        </span>
        <span
          v-if="activeTask && showBackupName"
          class="ml-2 text-sm font-normal text-slate-500"
        >
          · {{ activeTask.backupName }}
        </span>
      </template>
      <div
        v-if="activeTask"
        ref="drawerScrollAnchorRef"
        class="snapshot-drawer-body"
      >
        <el-descriptions
          :column="1"
          border
        >
          <el-descriptions-item :label="t('protection.backupDetail.labelTaskId')">
            {{ activeTask.id }}
          </el-descriptions-item>
          <el-descriptions-item
            v-if="showBackupName"
            :label="t('protection.backupDetail.labelName')"
          >
            {{ activeTask.backupName }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('protection.backupDetail.labelTaskType')">
            {{ activeTask.type }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('protection.backupDetail.labelStatus')">
            <TaskStatusTag :status="activeTask.status" />
          </el-descriptions-item>
          <el-descriptions-item :label="t('protection.backupDetail.labelStart')">
            {{ activeTask.startTime }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('protection.backupDetail.labelEnd')">
            {{ activeTask.endTime }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('protection.backupDetail.labelDuration')">
            {{ activeTask.duration }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('protection.backupDetail.labelTrigger')">
            {{ activeTask.trigger }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('protection.backupDetail.labelExecutor')">
            {{ activeTask.executor }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('protection.backupDetail.labelLinkedSnap')">
            {{ activeTask.snapshotId ?? t('protection.backupDetail.noLinkedSnap') }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('protection.backupDetail.labelDetail')">
            {{ activeTask.detail }}
          </el-descriptions-item>
        </el-descriptions>
      </div>
    </ElDrawer>

    <ElDrawer
      v-model="dirTreeDrawerOpen"
      destroy-on-close
      append-to-body
      :title="dirTreeDrawerTitle"
      class="snapshot-dir-contents-drawer"
      direction="rtl"
      :size="nestedDrawerSize"
      :z-index="3300"
    >
      <el-tree
        class="hfl-dir-tree hfl-dir-tree--tall"
        :data="dirTreeDrawerRows"
        default-expand-all
        :props="{ children: 'children', label: 'label' }"
      >
        <template #default="{ node, data }">
          <span class="hfl-dir-tree-node">
            <component
              :is="data.type === 'dir' ? Folder : File"
              :size="16"
              class="hfl-dir-tree-node__icon"
              :class="data.type === 'dir' ? '' : 'text-slate-500'"
            />
            <span class="hfl-dir-tree-node__text">
              <span
                class="hfl-dir-tree-node__label"
                :title="node.label"
              >{{ node.label }}</span>
              <span
                v-if="data.type === 'dir'"
                class="hfl-dir-tree-node__path"
              >{{ t('protection.backupDetail.typeDir') }}</span>
              <span
                v-else
                class="hfl-dir-tree-node__path"
              >{{ t('protection.backupDetail.typeFile') }}</span>
            </span>
          </span>
        </template>
      </el-tree>
      <p class="text-xs text-slate-400 mt-4">
        {{ t('protection.backupDetail.treeDemoHint') }}
      </p>
    </ElDrawer>
    <DangerConfirmDialog
      v-model="deleteSnapshotDialogOpen"
      :title="t('protection.backupDetail.titleDeleteSnapshot')"
      :message="t('protection.backupDetail.msgDeleteSnapshotConfirm')"
      :items="deleteSnapshotItems"
      :items-heading="t('protection.backupDetail.labelLinkedSnap')"
      :item-name-label="t('protection.backupsPage.snapshotBrowserName')"
      :item-status-label="t('protection.backupsPage.colStatus')"
      :item-details-label="t('protection.backupDetail.labelStart')"
      confirm-mode="keyword"
      :confirm-keyword="t('common.deleteKeyword')"
      :confirm-keyword-hint="t('common.deleteKeywordHint')"
      :confirm-keyword-placeholder="t('common.deleteKeyword')"
      :cancel-text="t('common.cancel')"
      :confirm-text="t('protection.backupsPage.btnConfirmDelete')"
      :loading="deleteSnapshotLoading"
      level="high"
      width="560px"
      @confirm="confirmDeleteSnapshot"
      @cancel="closeDeleteSnapshotDialog"
    />
  </div>
</template>

<style scoped>
.backup-source-history-dir-link {
  display: flex;
  width: 100%;
  min-width: 0;
  flex-direction: column;
  align-items: stretch;
  text-align: left;
}

.backup-source-history-dir-link__parent,
.backup-source-history-dir-link__child {
  display: flex;
  min-width: 0;
  align-items: center;
}

.backup-source-history-dir-link__parent {
  gap: 7px;
  font-weight: 650;
}

.backup-source-history-dir-link__icon {
  flex: 0 0 auto;
}

.backup-source-history-dir-link__icon--dir {
  color: #d97706;
}

.backup-source-history-dir-link__icon--file {
  color: #2563eb;
}

.backup-source-history-dir-link__path,
.backup-source-history-dir-link__host {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.backup-source-history-dir-link__child {
  margin-top: 4px;
  padding-left: 21px;
  gap: 6px;
  color: rgb(100 116 139);
  font-size: 12px;
}

.backup-source-history-dir-link__branch {
  width: 9px;
  height: 12px;
  flex: 0 0 auto;
  border-left: 1px solid rgb(203 213 225);
  border-bottom: 1px solid rgb(203 213 225);
  border-bottom-left-radius: 3px;
  transform: translateY(-3px);
}
</style>
