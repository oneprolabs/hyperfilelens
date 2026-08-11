<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getNodeBindings, type NodeBindings, type NodeBindingsRepository } from '../lib/nodeApi'
import { formatNodeBytes } from '../lib/nodeInventoryDisplay'
import { usePageRequestScope } from '../composables/usePageRequestScope'
import HflCapacityCell from './HflCapacityCell.vue'

const props = defineProps<{
  nodeId: number
  active: boolean
}>()

type StoragePoolRow = {
  key: string
  mountPoint: string
  repositoryNames: string[]
  totalBytes: number
  usedBytes: number
  availableBytes: number
}

const { t } = useI18n()
const requests = usePageRequestScope()
const bindings = ref<NodeBindings | null>(null)
const loading = ref(false)

const repositories = computed(() => [
  ...(bindings.value?.target_nas_repositories ?? []),
  ...(bindings.value?.standalone_disk_repositories ?? []),
])

const storagePools = computed<StoragePoolRow[]>(() => {
  const pools = new Map<string, StoragePoolRow>()
  for (const repository of repositories.value) {
    const totalBytes = Math.max(0, Number(repository.storage_total_bytes || 0))
    const poolKey = String(repository.storage_pool_key || '').trim()
    if (!poolKey || totalBytes <= 0) continue
    const existing = pools.get(poolKey)
    if (existing) {
      existing.repositoryNames.push(repository.name)
      existing.totalBytes = Math.max(existing.totalBytes, totalBytes)
      existing.usedBytes = Math.max(
        existing.usedBytes,
        Math.max(0, Number(repository.storage_used_bytes || 0)),
      )
      existing.availableBytes = Math.min(
        existing.availableBytes,
        Math.max(0, Number(repository.storage_available_bytes || 0)),
      )
      continue
    }
    pools.set(poolKey, {
      key: poolKey,
      mountPoint: String(repository.storage_mount_point || '').trim(),
      repositoryNames: [repository.name],
      totalBytes,
      usedBytes: Math.max(0, Number(repository.storage_used_bytes || 0)),
      availableBytes: Math.max(0, Number(repository.storage_available_bytes || 0)),
    })
  }
  return [...pools.values()]
})

function repositoryTypeLabel(repository: NodeBindingsRepository) {
  return repository.repo_type === 'nas'
    ? t('repositoriesPage.tabNas')
    : t('repositoriesPage.tabProxyFs')
}

function repositoryLocation(repository: NodeBindingsRepository) {
  const config = repository.config ?? {}
  if (repository.repo_type === 'nas') {
    const server = String(config.server_address || config.server || config.nfs_host || '').trim()
    const share = String(config.share_path || config.nfs_export || '').trim()
    return [server, share].filter(Boolean).join(':') || '—'
  }
  return String(
    repository.storage_mount_point
      || config.proxy_node_dir
      || config.repo_dir
      || '',
  ).trim() || '—'
}

function repositoryHealthType(repository: NodeBindingsRepository): 'success' | 'danger' | 'warning' | 'info' {
  if (repository.health === 'online') return 'success'
  if (repository.health === 'offline') return 'danger'
  if (repository.health === 'unverified') return 'warning'
  return 'info'
}

function repositoryHealthLabel(repository: NodeBindingsRepository) {
  if (repository.health === 'online') return t('repositoriesPage.healthOnline')
  if (repository.health === 'offline') return t('repositoriesPage.healthOffline')
  return t('repositoriesPage.healthUnverified')
}

async function refresh() {
  const signal = requests.nextSignal('proxy-storage-repositories')
  loading.value = true
  try {
    bindings.value = await getNodeBindings(props.nodeId, { signal })
  } catch (error) {
    if (requests.isAbortError(error)) return
    bindings.value = null
  } finally {
    requests.releaseSignal('proxy-storage-repositories', signal)
    if (!signal.aborted) loading.value = false
  }
}

watch(
  () => [props.active, props.nodeId] as const,
  ([active, nodeId]) => {
    if (!active || nodeId <= 0) {
      requests.abortScope('proxy-storage-repositories')
      return
    }
    void refresh()
  },
  { immediate: true },
)
</script>

<template>
  <div
    v-loading="loading"
    class="proxy-storage-panel"
  >
    <section class="proxy-storage-panel__section">
      <div class="proxy-storage-panel__section-head">
        <div>
          <h4>{{ t('protection.sourceResources.storagePoolsTitle') }}</h4>
          <p>{{ t('protection.sourceResources.storagePoolsHint') }}</p>
        </div>
        <ElTag
          size="small"
          effect="plain"
        >
          {{ t('protection.sourceResources.storagePoolCount', { n: storagePools.length }) }}
        </ElTag>
      </div>

      <ElTable
        v-if="storagePools.length"
        :data="storagePools"
        size="small"
        class="proxy-storage-panel__table"
      >
        <ElTableColumn
          :label="t('protection.sourceResources.storageMountPoint')"
          min-width="160"
        >
          <template #default="{ row }">
            <span class="proxy-storage-panel__mono">{{ row.mountPoint || '—' }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          :label="t('protection.sourceResources.associatedRepositories')"
          min-width="180"
        >
          <template #default="{ row }">
            <span>{{ row.repositoryNames.join(', ') }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          :label="t('repositoriesPage.detailFieldPhysicalUsage')"
          min-width="190"
        >
          <template #default="{ row }">
            <HflCapacityCell
              :used-bytes="row.usedBytes"
              :total-bytes="row.totalBytes"
              :format-bytes="formatNodeBytes"
              variant="compact"
            />
          </template>
        </ElTableColumn>
        <ElTableColumn
          :label="t('protection.sourceResources.storageAvailable')"
          min-width="110"
        >
          <template #default="{ row }">
            {{ formatNodeBytes(row.availableBytes) }}
          </template>
        </ElTableColumn>
      </ElTable>
      <ElEmpty
        v-else-if="!loading"
        :description="t('protection.sourceResources.storagePoolsEmpty')"
        :image-size="64"
      />
    </section>

    <section class="proxy-storage-panel__section">
      <div class="proxy-storage-panel__section-head">
        <div>
          <h4>{{ t('protection.sourceResources.associatedRepositories') }}</h4>
          <p>{{ t('protection.sourceResources.associatedRepositoriesHint') }}</p>
        </div>
        <ElTag
          size="small"
          effect="plain"
        >
          {{ t('protection.sourceResources.repositoryCount', { n: repositories.length }) }}
        </ElTag>
      </div>

      <ElTable
        v-if="repositories.length"
        :data="repositories"
        size="small"
        class="proxy-storage-panel__table"
      >
        <ElTableColumn
          :label="t('repositoriesPage.colListName')"
          min-width="150"
          prop="name"
        />
        <ElTableColumn
          :label="t('protection.sourceResources.repositoryType')"
          min-width="105"
        >
          <template #default="{ row }">
            {{ repositoryTypeLabel(row) }}
          </template>
        </ElTableColumn>
        <ElTableColumn
          :label="t('repositoriesPage.colLocation')"
          min-width="170"
        >
          <template #default="{ row }">
            <span class="proxy-storage-panel__mono">{{ repositoryLocation(row) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          :label="t('repositoriesPage.colRepositoryUsage')"
          min-width="190"
        >
          <template #default="{ row }">
            <HflCapacityCell
              :used-bytes="Number(row.estimated_usage_bytes || 0)"
              :total-bytes="Number(row.planned_limit_bytes || 0)"
              :format-bytes="formatNodeBytes"
              :unlimited-total-label="t('repositoriesPage.noConfiguredLimit')"
              variant="compact"
            />
          </template>
        </ElTableColumn>
        <ElTableColumn
          :label="t('repositoriesPage.colStatus')"
          min-width="100"
          align="center"
        >
          <template #default="{ row }">
            <ElTag
              :type="repositoryHealthType(row)"
              size="small"
            >
              {{ repositoryHealthLabel(row) }}
            </ElTag>
          </template>
        </ElTableColumn>
      </ElTable>
      <ElEmpty
        v-else-if="!loading"
        :description="t('protection.sourceResources.associatedRepositoriesEmpty')"
        :image-size="64"
      />
    </section>
  </div>
</template>

<style scoped>
.proxy-storage-panel {
  display: grid;
  gap: 22px;
  min-height: 240px;
}

.proxy-storage-panel__section {
  min-width: 0;
}

.proxy-storage-panel__section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.proxy-storage-panel__section-head h4 {
  margin: 0;
  color: rgb(30 41 59);
  font-size: 14px;
  font-weight: 650;
}

.proxy-storage-panel__section-head p {
  margin: 4px 0 0;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 1.5;
}

.proxy-storage-panel__table {
  width: 100%;
}

.proxy-storage-panel__mono {
  color: rgb(51 65 85);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  overflow-wrap: anywhere;
}
</style>
