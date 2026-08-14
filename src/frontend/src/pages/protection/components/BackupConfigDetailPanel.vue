<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useProtectionDemoStore } from '../../../composables/useProtectionDemoStore'
import {
  getBackupConfig,
  type BackupConfigDetail,
  type CompressionLevel,
} from '../../../lib/protectionBackupConfigApi'

const props = defineProps<{
  backupId: string
}>()

const { t } = useI18n()
const store = useProtectionDemoStore()
const realConfig = ref<BackupConfigDetail | null>(null)
const loading = ref(false)

const backup = computed(() => store.getBackup(props.backupId))
const policy = computed(() =>
  backup.value?.policyId ? store.getPolicy(backup.value.policyId) : undefined,
)
const globalFilter = computed(() =>
  backup.value?.globalFilterId ? store.getGlobalFilter(backup.value.globalFilterId) : undefined,
)

function compressionLevelLabel(level: CompressionLevel) {
  if (level === 'none') return t('protection.backupsPage.compressionNoneTitle')
  if (level === 'high') return t('protection.backupsPage.compressionHighTitle')
  return t('protection.backupsPage.compressionBalancedTitle')
}

watch(
  () => props.backupId,
  async (id) => {
    const numericId = Number(id)
    if (!Number.isFinite(numericId) || numericId <= 0) {
      realConfig.value = null
      return
    }
    loading.value = true
    try {
      realConfig.value = await getBackupConfig(numericId)
    } catch {
      realConfig.value = null
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)
</script>

<template>
  <div
    v-if="loading"
    class="p-4"
    aria-busy="true"
  >
    <el-skeleton
      :rows="7"
      animated
    />
  </div>

  <div
    v-else-if="realConfig"
    class="backup-config-detail-panel"
  >
    <el-descriptions
      :column="1"
      border
      size="default"
      class="backup-config-detail-panel__desc"
    >
      <el-descriptions-item :label="t('protection.backupDetail.labelName')">
        {{ realConfig.name }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('protection.backupDetail.labelRemark')">
        <span :class="{ 'hfl-empty-mark': !realConfig.remark }">{{ realConfig.remark || t('protection.backupDetail.durationDash') }}</span>
      </el-descriptions-item>
      <el-descriptions-item :label="t('protection.backupDetail.labelSources')">
        {{ realConfig.source_type }}:{{ realConfig.source_ref_id }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('protection.backupDetail.labelTarget')">
        #{{ realConfig.repository_id }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('protection.backupDetail.labelPolicyName')">
        {{ realConfig.backup_policy_id ? `#${realConfig.backup_policy_id}` : t('protection.backupDetail.noPolicy') }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('protection.backupDetail.labelGlobalFilterName')">
        {{ realConfig.file_filter_rule_id ? `#${realConfig.file_filter_rule_id}` : t('protection.backupDetail.noGlobalFilter') }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('protection.backupDetail.labelCompression')">
        {{ compressionLevelLabel(realConfig.compression_level) }}
      </el-descriptions-item>
    </el-descriptions>

    <div class="mt-4 hfl-list-panel">
      <div class="px-4 py-3 border-b border-slate-100 text-sm font-medium text-slate-800">
        {{ t('protection.backupDetail.panelDirList') }}
      </div>
      <el-table
        v-table-overflow-title
        :data="realConfig.directories"
        stripe
        size="small"
        class="hfl-list-table"
      >
        <el-table-column
          :label="t('protection.backupDetail.colBackupDir')"
          min-width="260"
          prop="path"
        >
          <template #default="{ row }">
            <span class="hfl-table-cell-mono">{{ row.path }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('protection.backupDetail.colSnapSize')"
          width="130"
          prop="estimated_size_bytes"
        />
      </el-table>
    </div>
  </div>

  <div
    v-else-if="backup"
    class="backup-config-detail-panel"
  >
    <el-descriptions
      :column="1"
      border
      size="default"
      class="backup-config-detail-panel__desc"
    >
      <el-descriptions-item :label="t('protection.backupDetail.labelName')">
        {{ backup.name }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('protection.backupDetail.labelRemark')">
        <span :class="{ 'hfl-empty-mark': !backup.remark }">{{ backup.remark || t('protection.backupDetail.durationDash') }}</span>
      </el-descriptions-item>
      <el-descriptions-item :label="t('protection.backupDetail.labelSnapshotCount')">
        {{ backup.snapshots.length }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('protection.backupDetail.labelLatestEnd')">
        <span :class="{ 'hfl-empty-mark': !backup.latestSnapshotAt }">{{ backup.latestSnapshotAt ?? t('protection.backupDetail.durationDash') }}</span>
      </el-descriptions-item>
      <el-descriptions-item :label="t('protection.backupDetail.labelSources')">
        <ul class="list-none m-0 p-0 space-y-2">
          <li
            v-for="(s, i) in backup.sources"
            :key="i"
            class="border border-slate-100 rounded-md px-3 py-2 bg-[var(--color-grey-1,#f8fafc)]"
          >
            <div class="text-sm font-medium text-slate-800">
              {{ s.path }}
            </div>
            <div class="text-xs text-slate-500 mt-1">
              {{
                t('protection.backupDetail.hostLine', {
                  name: store.getHost(s.hostId)?.name ?? s.hostId,
                  hostname: store.getHost(s.hostId)?.hostname ?? t('protection.backupDetail.durationDash'),
                })
              }}
            </div>
          </li>
        </ul>
      </el-descriptions-item>
      <el-descriptions-item :label="t('protection.backupDetail.labelTarget')">
        {{ store.getTarget(backup.targetId)?.name }}
        <span class="text-slate-400 text-sm ml-2">{{ store.getTarget(backup.targetId)?.location }}</span>
      </el-descriptions-item>
      <el-descriptions-item :label="t('protection.backupDetail.labelPolicyName')">
        <template v-if="policy">
          {{ policy.name }}
        </template>
        <span
          v-else
          class="text-slate-400"
        >{{ t('protection.backupDetail.noPolicy') }}</span>
      </el-descriptions-item>
      <template v-if="policy">
        <el-descriptions-item :label="t('protection.backupDetail.labelFreq')">
          {{ policy.backupFrequencyDesc }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('protection.backupDetail.labelCron')">
          <code class="rounded bg-slate-100 px-2 py-0.5 text-sm text-slate-800">{{ policy.schedule }}</code>
        </el-descriptions-item>
        <el-descriptions-item :label="t('protection.backupDetail.labelRetention')">
          {{ policy.retentionDesc }}
        </el-descriptions-item>
      </template>
      <el-descriptions-item :label="t('protection.backupDetail.labelCompression')">
        {{ compressionLevelLabel(backup.compressionLevel ?? 'balanced') }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('protection.backupDetail.labelGlobalFilterName')">
        <template v-if="globalFilter">
          {{ globalFilter.name }}
        </template>
        <span
          v-else
          class="text-slate-400"
        >{{ t('protection.backupDetail.noGlobalFilter') }}</span>
      </el-descriptions-item>
      <el-descriptions-item
        v-if="globalFilter"
        :label="t('protection.backupDetail.labelGlobalFilterSummary')"
      >
        {{ globalFilter.summary }}
      </el-descriptions-item>
    </el-descriptions>

    <p class="mt-4 mb-0 text-xs leading-relaxed text-slate-500">
      {{ t('protection.backupsPage.flowBackupConfigDetailSnapshotsHint') }}
    </p>
  </div>

  <el-empty
    v-else
    :description="t('protection.backupDetail.notFound')"
    :image-size="72"
  />
</template>

<style scoped>
.backup-config-detail-panel__desc {
  max-width: 100%;
}
</style>
