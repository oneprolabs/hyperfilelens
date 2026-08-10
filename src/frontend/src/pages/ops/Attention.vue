<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { RefreshCw } from 'lucide-vue-next'
import ModulePage from '../../components/ModulePage.vue'
import HflTablePanel from '../../components/HflTablePanel.vue'
import HflPagination from '../../components/HflPagination.vue'
import { useOpsMenus } from '../../composables/useOpsMenus'
import { api, apiErrorMessage } from '../../lib/api'
import { formatLocalDateTime } from '../../lib/dateTime'
import { asList, unwrapApiPayload } from '../../lib/parse'

type AttentionKind = 'task' | 'alert' | 'node' | 'source' | 'audit'

type AttentionItem = {
  id: string
  kind: AttentionKind
  title: string
  detail: string
  at?: string | null
  to: string
}

const { t } = useI18n()
const router = useRouter()
const opsMenus = useOpsMenus()
const loading = ref(false)
const loadError = ref<string | null>(null)
const items = ref<AttentionItem[]>([])
const filters = reactive({ kind: '' })
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })
const filteredItems = computed(() => items.value)

function itemKindLabel(kind: AttentionKind) {
  return t(`ops.attention.${kind}`)
}

function itemTagType(kind: AttentionKind): 'danger' | 'warning' | 'info' {
  if (kind === 'task' || kind === 'alert') return 'danger'
  if (kind === 'node' || kind === 'source') return 'warning'
  return 'info'
}

function itemTime(item: AttentionItem) {
  return formatLocalDateTime(item.at, '—')
}

async function loadItems() {
  loading.value = true
  loadError.value = null
  try {
    const query = new URLSearchParams({ page: String(pagination.page), page_size: String(pagination.pageSize) })
    if (filters.kind) query.set('type', filters.kind)
    const data = unwrapApiPayload<Record<string, unknown>>(await api<unknown>(`/api/v1/monitors/attention/?${query}`))
    items.value = asList<AttentionItem>(data).map((item) => ({ ...item, at: (item as AttentionItem & { occurred_at?: string }).occurred_at || item.at }))
    pagination.count = Number(data.count) || 0
  } catch (error) {
    loadError.value = apiErrorMessage(error)
  } finally {
    loading.value = false
  }
}

onMounted(loadItems)
watch(() => filters.kind, () => { pagination.page = 1; void loadItems() })
watch(() => [pagination.page, pagination.pageSize], () => void loadItems())
</script>

<template>
  <ModulePage :title="t('ops.attention.title')" :menus="opsMenus" body-fill>
    <div class="attention-page">
      <p class="attention-page__subtitle">{{ t('ops.attention.subtitle') }}</p>
      <p v-if="loadError" class="attention-page__error">{{ t('ops.attention.loadFailed') }}: {{ loadError }}</p>
      <HflTablePanel fill>
        <template #toolbar>
          <el-select v-model="filters.kind" clearable :placeholder="t('ops.attention.allTypes')" style="width: 160px">
            <el-option v-for="kind in ['task', 'alert', 'node', 'source', 'audit']" :key="kind" :value="kind" :label="itemKindLabel(kind as AttentionKind)" />
          </el-select>
        </template>
        <template #toolbar-utility>
          <el-button class="hfl-refresh-button" :disabled="loading" :title="t('ops.task.btnRefresh')" :aria-label="t('ops.task.btnRefresh')" @click="loadItems">
            <RefreshCw :size="16" :class="{ 'is-spinning': loading }" />
          </el-button>
        </template>
        <template #table="{ tableMaxHeight }">
          <el-table v-table-column-resize="'ops.attention'" v-table-overflow-title v-loading="loading" :data="filteredItems" row-key="id" class="hfl-list-table" stripe :max-height="tableMaxHeight">
            <el-table-column :label="t('ops.attention.type')" width="120" fixed="left">
              <template #default="{ row }">
                <el-tag :type="itemTagType(row.kind)" size="small">{{ itemKindLabel(row.kind) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('ops.alertsCenter.common.title')" min-width="260">
              <template #default="{ row }">
                <div class="attention-page__title">{{ row.title }}</div>
              </template>
            </el-table-column>
            <el-table-column :label="t('ops.attention.details')" min-width="280">
              <template #default="{ row }">
                <span :class="row.detail ? '' : 'hfl-empty-mark'">{{ row.detail || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('ops.attention.occurredAt')" width="180">
              <template #default="{ row }">{{ itemTime(row) }}</template>
            </el-table-column>
            <el-table-column :label="t('ops.attention.action')" width="100" fixed="right">
              <template #default="{ row }">
                <el-button class="hfl-table-no-tooltip" link type="primary" @click="router.push(row.to)">{{ t('ops.attention.open') }}</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty v-if="!loading" :description="t('ops.attention.empty')" :image-size="72" />
            </template>
          </el-table>
        </template>
        <template #footer>
          <HflPagination
            v-model:current-page="pagination.page"
            v-model:page-size="pagination.pageSize"
            class="hfl-list-footer__pagination"
            layout="total, sizes, prev, pager, next"
            :total="pagination.count"
          />
        </template>
      </HflTablePanel>
    </div>
  </ModulePage>
</template>

<style scoped>
.attention-page {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  gap: 12px;
}

.attention-page__subtitle {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 14px;
}

.attention-page__error {
  margin: 0;
  color: var(--el-color-danger);
  font-size: 14px;
}

.attention-page__title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}
</style>
