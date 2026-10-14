<script setup lang="ts">
import { onBeforeUnmount, ref, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronDown } from 'lucide-vue-next'
import { ElButton } from 'element-plus'
import HflPagination from './HflPagination.vue'
import { listStorageRepositoryAssociatedSources, type StorageRepositoryAssociatedSource } from '../lib/storageRepositoryApi'

const props = defineProps<{ repositoryId: number }>()
const { t } = useI18n()
const contentId = useId()
const expanded = ref(false)
const loading = ref(false)
const failed = ref(false)
const page = ref(1)
const total = ref(0)
const countLoaded = ref(false)
const rows = ref<StorageRepositoryAssociatedSource[]>([])
const pageSize = 10
const cache = new Map<number, { count: number; results: StorageRepositoryAssociatedSource[] }>()
let requestId = 0
let controller: AbortController | undefined

async function loadPage(value: number) {
  page.value = value
  const id = ++requestId
  controller?.abort()
  failed.value = false
  const cached = cache.get(value)
  if (cached) {
    rows.value = cached.results
    total.value = cached.count
    countLoaded.value = true
    loading.value = false
    return
  }
  controller = new AbortController()
  loading.value = true
  rows.value = []
  try {
    const result = await listStorageRepositoryAssociatedSources(
      props.repositoryId, { page: value, page_size: pageSize }, { signal: controller.signal },
    )
    if (id !== requestId) return
    cache.set(value, result)
    total.value = result.count
    countLoaded.value = true
    rows.value = result.results
  } catch {
    if (id === requestId) failed.value = true
  } finally {
    if (id === requestId) loading.value = false
  }
}

function toggle() {
  expanded.value = !expanded.value
  if (expanded.value && !loading.value && (!cache.has(page.value) || failed.value)) void loadPage(page.value)
}

onBeforeUnmount(() => {
  requestId += 1
  controller?.abort()
})
</script>

<template>
  <div class="repository-blocked-associations">
    <button
      type="button"
      class="repository-blocked-associations__toggle"
      :aria-expanded="expanded"
      :aria-controls="contentId"
      @click="toggle"
    >
      <ChevronDown
        :size="14"
        :class="{ 'is-collapsed': !expanded }"
        aria-hidden="true"
      />
      <span class="repository-blocked-associations__toggle-label">{{ countLoaded ? t('repositoriesPage.cleanupViewAssociationsCount', { n: total }) : t('repositoriesPage.cleanupViewAssociations') }}</span>
    </button>
    <div
      v-show="expanded"
      :id="contentId"
      v-loading="loading"
      :aria-busy="loading"
      class="repository-blocked-associations__body"
    >
      <div
        v-if="failed"
        role="alert"
      >
        <p>{{ t('repositoriesPage.cleanupBlockedAssociationUnavailable') }}</p>
        <ElButton
          link
          type="primary"
          @click="loadPage(page)"
        >
          {{ t('common.retry') }}
        </ElButton>
      </div>
      <p v-else-if="!loading && !rows.length">
        {{ t('repositoriesPage.cleanupBlockedAssociationEmpty') }}
      </p>
      <ul v-else>
        <li
          v-for="association in rows"
          :key="association.backup_config_id"
        >
          <span class="repository-blocked-associations__config">{{ association.backup_config_name }}</span>
          <span>{{ t('repositoriesPage.cleanupBlockedSourceName', { name: association.source_name }) }}</span>
        </li>
      </ul>
      <HflPagination
        v-if="!failed && total > pageSize"
        :current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        @update:current-page="loadPage"
      />
    </div>
  </div>
</template>

<style scoped>
.repository-blocked-associations__toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
  max-width: 100%;
  margin: 6px 0 0 -4px;
  padding: 4px;
  border-radius: 4px;
  background: transparent;
  color: var(--el-color-primary);
  font-size: 12px;
  line-height: 20px;
  text-align: left;
  cursor: pointer;
  transition: color 150ms ease;
}
.repository-blocked-associations__toggle:hover {
  color: var(--el-color-primary-dark-2);
}
.repository-blocked-associations__toggle-label {
  text-decoration: underline;
  text-decoration-color: transparent;
  text-decoration-thickness: 1px;
  text-underline-offset: 3px;
  transition: text-decoration-color 150ms ease;
}
.repository-blocked-associations__toggle:hover .repository-blocked-associations__toggle-label,
.repository-blocked-associations__toggle:focus-visible .repository-blocked-associations__toggle-label {
  text-decoration-color: currentColor;
}
.repository-blocked-associations__toggle > svg {
  flex-shrink: 0;
}
.repository-blocked-associations__toggle:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}
.is-collapsed { transform: rotate(-90deg); }
@media (prefers-reduced-motion: reduce) {
  .repository-blocked-associations__toggle,
  .repository-blocked-associations__toggle-label {
    transition: none;
  }
}
.repository-blocked-associations__body {
  min-height: 48px;
  margin-top: 8px;
  padding: 2px 0 2px 12px;
  border-left: 2px solid var(--el-border-color-lighter);
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}
.repository-blocked-associations__body ul { display: grid; gap: 10px; margin: 0 0 10px; padding: 0; list-style: none; }
.repository-blocked-associations__body li { display: grid; gap: 2px; overflow-wrap: anywhere; }
.repository-blocked-associations__body p { margin: 0; }
.repository-blocked-associations__config { color: var(--el-text-color-regular); font-size: 13px; }
</style>
