<script setup lang="ts">
import { onMounted, ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../lib/api'
import { getEffectiveOrgKey } from '../../composables/useAuth'
import { formatLocalDateTime } from '../../lib/dateTime'
import { asList } from '../../lib/parse'
import ModulePage from '../../components/ModulePage.vue'
import HflTablePanel from '../../components/HflTablePanel.vue'
import { useNodeSideNav } from '../../composables/useNodeSideNav'
import { booleanStatusTag } from '../../lib/statusTag'

const { t } = useI18n()
const nodeMenus = useNodeSideNav()

interface Org {
  id: number
  key: string
  name: string
  is_active: boolean
  member_count: number
  created_at?: string
  owner_email?: string
}

const rows = ref<Org[]>([])
const busy = ref(false)
const currentPage = ref(1)
const pageSize = ref(30)

const paginatedRows = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return rows.value.slice(start, start + pageSize.value)
})

const totalCount = computed(() => rows.value.length)

function formatCreatedAt(iso?: string): string {
  return formatLocalDateTime(iso, '—')
}

async function load() {
  busy.value = true
  try {
    const orgsData = await api<unknown>('/api/v1/iam/orgs/')
    const list = asList<Org>(orgsData)
    const orgKey = getEffectiveOrgKey()
    const current = orgKey ? list.find(o => o.key === orgKey) : undefined
    rows.value = current ? [current] : list.length > 0 ? [list[0]] : []
  } catch {
    rows.value = []
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  load()
})

watch(pageSize, () => {
  currentPage.value = 1
})
</script>

<template>
  <ModulePage
    :menus="nodeMenus"
    body-fill
  >
    <HflTablePanel fill>
      <template #table="{ tableMaxHeight }">
        <el-table
          v-table-column-resize="'settings.organizations'"
          v-loading="busy"
          :data="paginatedRows"
          stripe
          row-key="id"
          class="hfl-list-table"
          :max-height="tableMaxHeight"
        >
          <el-table-column
            prop="name"
            :label="t('settings.org.colName')"
            min-width="140"
          />
          <el-table-column
            :label="t('settings.org.colOwner')"
            min-width="160"
          >
            <template #default="{ row }">
              <span :class="{ 'hfl-empty-mark': !row.owner_email }">{{ row.owner_email || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('settings.org.colMembers')"
            width="100"
          >
            <template #default="{ row }">
              {{ row.member_count ?? 0 }}
            </template>
          </el-table-column>
          <el-table-column
            :label="t('settings.org.colPlan')"
            min-width="140"
          >
            <template #default>
              {{ t('settings.org.planDefault') }}
            </template>
          </el-table-column>
          <el-table-column
            :label="t('settings.org.colStatus')"
            width="100"
          >
            <template #default="{ row }">
              <el-tag
                v-bind="booleanStatusTag(row.is_active)"
                size="small"
              >
                {{ row.is_active ? t('settings.org.statusNormal') : t('settings.org.statusInactive') }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('settings.org.colCreated')"
            width="170"
          >
            <template #default="{ row }">
              <span
                class="hfl-table-cell-time"
                :class="{ 'hfl-empty-mark': !row.created_at }"
              >{{ formatCreatedAt(row.created_at) }}</span>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty :description="t('settings.org.empty')" />
          </template>
        </el-table>
      </template>

      <template #footer>
        <HflPagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          class="hfl-list-footer__pagination"
          layout="total, sizes, prev, pager, next"
          :total="totalCount"
          :page-sizes="[20, 30, 50, 100]"
        />
      </template>
    </HflTablePanel>
  </ModulePage>
</template>

<style scoped>
:deep(.el-table .el-table__row) {
  cursor: default;
}
</style>
