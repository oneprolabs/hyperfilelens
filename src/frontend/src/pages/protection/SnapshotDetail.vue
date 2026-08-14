<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowLeft, Folder, File } from 'lucide-vue-next'
import ModulePage from '../../components/ModulePage.vue'
import { useProtectionSideNav } from '../../composables/useProtectionSideNav'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import {
  useProtectionDemoStore,
  type DemoFsNode,
  type DemoSnapshotDir,
} from '../../composables/useProtectionDemoStore'

const { t } = useI18n()
const route = useRoute()
const store = useProtectionDemoStore()
const { drawerSize } = useResponsiveDrawerWidth()

const backupId = computed(() => String(route.params.backupId || ''))
const snapshotId = computed(() => String(route.params.snapshotId || ''))

const backup = computed(() => store.getBackup(backupId.value))
const snapshot = computed(() => backup.value?.snapshots.find((s) => s.id === snapshotId.value))

const protectionMenus = useProtectionSideNav()

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

const drawerOpen = ref(false)
const drawerTitle = ref('')
const drawerTreeRows = ref<DrawerTreeRow[]>([])

function fmtBytes(n: number) {
  if (!n || n <= 0) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let i = 0
  let v = n
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v.toFixed(i >= 2 ? 1 : 0)} ${u[i]}`
}
function openDirTree(row: DemoSnapshotDir) {
  const snap = snapshot.value
  if (!snap) return
  const tree = snap.treeByPath[row.path] ?? []
  drawerTitle.value = t('protection.snapshotDetail.drawerDirTitle', { path: row.path })
  drawerTreeRows.value = nodesToDrawerTree(tree)
  drawerOpen.value = true
}
</script>

<template>
  <ModulePage
    :title="t('protection.moduleTitle')"
    :menus="protectionMenus"
  >
    <div
      v-if="backup && snapshot"
      class="space-y-4"
    >
      <div class="flex flex-wrap items-center gap-3">
        <RouterLink :to="`/protection/backups/${backupId}`">
          <ElButton text>
            <ArrowLeft
              :size="16"
              class="inline mr-1 align-text-bottom"
            />
            {{ t('protection.snapshotDetail.backToBackupDetail') }}
          </ElButton>
        </RouterLink>
        <span class="text-slate-400">|</span>
        <span class="text-slate-600 text-sm">{{ t('protection.snapshotDetail.crumbBackup', { name: backup.name }) }}</span>
        <span class="text-slate-400">|</span>
        <span class="text-slate-800 font-medium">{{ t('protection.snapshotDetail.crumbSnapshot', { id: snapshot.id }) }}</span>
      </div>

      <p class="text-sm text-slate-500 m-0">
        {{
          t('protection.snapshotDetail.metaLine', {
            end: snapshot.endTime,
            files: snapshot.fileCount,
            dirs: snapshot.dirCount,
          })
        }}
      </p>

      <div class="hfl-list-panel">
        <div class="px-4 py-3 border-b border-slate-100 text-sm font-medium text-slate-800">
          {{ t('protection.snapshotDetail.panelTitle') }}
        </div>
        <el-table
          v-table-overflow-title
          :data="snapshot.dirs"
          stripe
        >
          <el-table-column
            :label="t('protection.snapshotDetail.colBackupDir')"
            min-width="280"
          >
            <template #default="{ row }">
              <button
                type="button"
                class="backup-path-link reset-btn text-left w-full font-medium text-[var(--el-color-primary)] hover:underline"
                @click="openDirTree(row)"
              >
                <span class="block hfl-table-cell-mono truncate">{{ row.path }}</span>
                <span class="block text-xs text-slate-500 mt-1">
                  {{
                    t('protection.snapshotDetail.hostSubline', {
                      name: row.hostName,
                      hostname: store.getHost(row.hostId)?.hostname || t('protection.backupDetail.durationDash'),
                    })
                  }}
                </span>
              </button>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.snapshotDetail.colDirSize')"
            width="120"
          >
            <template #default="{ row }">
              {{ fmtBytes(row.sizeBytes) }}
            </template>
          </el-table-column>
          <el-table-column
            :label="t('protection.snapshotDetail.colFiles')"
            width="110"
            prop="fileCount"
          />
          <el-table-column
            :label="t('protection.snapshotDetail.colInnerDirs')"
            width="120"
            prop="innerDirCount"
          />
          <template #empty>
            <el-empty
              :description="t('protection.snapshotDetail.emptyDirs')"
              :image-size="64"
            />
          </template>
        </el-table>
      </div>

      <ElDrawer
        v-model="drawerOpen"
        destroy-on-close
        :title="drawerTitle"
        class="snapshot-dir-contents-drawer"
        direction="rtl"
        :size="drawerSize"
      >
        <el-tree
          class="hfl-dir-tree hfl-dir-tree--tall"
          :data="drawerTreeRows"
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
                >{{ t('protection.snapshotDetail.typeDir') }}</span>
                <span
                  v-else
                  class="hfl-dir-tree-node__path"
                >{{ t('protection.snapshotDetail.typeFile') }}</span>
              </span>
            </span>
          </template>
        </el-tree>
        <p class="text-xs text-slate-400 mt-4">
          {{ t('protection.snapshotDetail.treeDemoHint') }}
        </p>
      </ElDrawer>
    </div>

    <div
      v-else
      class="py-16 text-center"
    >
      <el-empty :description="t('protection.snapshotDetail.notFoundSnap')">
        <RouterLink
          v-if="backup"
          :to="`/protection/backups/${backupId}`"
        >
          <ElButton type="primary">
            {{ t('protection.snapshotDetail.backToBackup') }}
          </ElButton>
        </RouterLink>
        <RouterLink
          v-else
          to="/protection/backups"
        >
          <ElButton type="primary">
            {{ t('protection.snapshotDetail.backToDataProtection') }}
          </ElButton>
        </RouterLink>
      </el-empty>
    </div>
  </ModulePage>
</template>

<style scoped>
.reset-btn {
  border: none;
  padding: 0;
  margin: 0;
  background: none;
  font: inherit;
  cursor: pointer;
}
</style>

<style>
.snapshot-dir-contents-drawer.el-drawer.rtl {
  width: min(760px, 92vw);
  max-width: 100%;
}
.snapshot-dir-contents-drawer .el-drawer__body {
  overflow: auto;
}
</style>
