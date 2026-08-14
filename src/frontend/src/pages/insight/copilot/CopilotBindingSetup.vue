<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../../lib/api'
import {
  ensureCopilotBinding,
  listCopilotGatewayOptions,
  type LensChatBinding,
  type LensCopilotGatewayOption,
} from '../../../lib/lensApi'
import {
  getBackupSourceSnapshot,
  listBackupConfigs,
  listBackupSourceSnapshots,
  type BackupConfig,
  type BackupSourceSnapshot,
  type BackupSourceSnapshotDirectory,
} from '../../../lib/protectionBackupConfigApi'

const emit = defineEmits<{
  ready: [binding: LensChatBinding]
}>()

const { t } = useI18n()

const loading = ref(false)
const submitting = ref(false)
const backupConfigs = ref<BackupConfig[]>([])
const snapshots = ref<BackupSourceSnapshot[]>([])
const snapshotDetail = ref<BackupSourceSnapshot | null>(null)
const gatewayOptions = ref<LensCopilotGatewayOption[]>([])

const backupConfigId = ref<number | null>(null)
const snapshotId = ref<number | null>(null)
const directoryId = ref<number | null>(null)
const gatewayLinkId = ref<number | null>(null)

const snapshotDirectories = computed((): BackupSourceSnapshotDirectory[] => {
  return snapshotDetail.value?.directories ?? []
})

const selectedDirectory = computed(() =>
  snapshotDirectories.value.find((row) => row.id === directoryId.value) ?? null,
)

async function loadOptions() {
  loading.value = true
  try {
    const [configs, snapRows, gateways] = await Promise.all([
      listBackupConfigs({ page_size: 200 }).then((page) => page.results),
      listBackupSourceSnapshots({ page_size: 200 }).then((page) => page.results),
      listCopilotGatewayOptions().catch(() => [] as LensCopilotGatewayOption[]),
    ])
    backupConfigs.value = configs
    snapshots.value = snapRows.filter((row) => row.status === 'available' || row.status === 'partial')
    gatewayOptions.value = gateways
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.loadFailed')), grouping: true })
  } finally {
    loading.value = false
  }
}

async function loadSnapshotDetail(id: number) {
  snapshotDetail.value = await getBackupSourceSnapshot(id)
  const first = snapshotDetail.value?.directories?.[0]
  directoryId.value = first?.id ?? null
}

watch(backupConfigId, (id) => {
  snapshotId.value = null
  directoryId.value = null
  snapshotDetail.value = null
  if (id == null) return
  const latest = snapshots.value
    .filter((row) => row.backup_config_id === id)
    .sort((a, b) => Date.parse(b.finished_at || b.created_at) - Date.parse(a.finished_at || a.created_at))[0]
  snapshotId.value = latest?.id ?? null
})

watch(snapshotId, (id) => {
  directoryId.value = null
  snapshotDetail.value = null
  if (id != null) void loadSnapshotDetail(id)
})

async function submit() {
  if (backupConfigId.value == null || snapshotId.value == null || directoryId.value == null) {
    ElMessage.warning({ message: t('insight.copilot.bindingRequired'), grouping: true })
    return
  }
  submitting.value = true
  try {
    const binding = await ensureCopilotBinding({
      backup_config_id: backupConfigId.value,
      backup_source_snapshot_id: snapshotId.value,
      backup_snapshot_directory_id: directoryId.value,
      source_path: selectedDirectory.value?.path || '',
      gateway_link_id: gatewayLinkId.value,
    })
    ElMessage.success({ message: t('insight.copilot.bindingSuccess'), grouping: true })
    emit('ready', binding)
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.saveFailed')), grouping: true })
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  void loadOptions()
})
</script>

<template>
  <div class="copilot-binding-setup">
    <header class="copilot-binding-setup__header">
      <h2>{{ t('insight.copilot.bindingTitle') }}</h2>
      <p>{{ t('insight.copilot.bindingDesc') }}</p>
    </header>

    <el-form
      v-loading="loading"
      label-position="top"
      class="copilot-binding-setup__form"
    >
      <el-form-item :label="t('insight.copilot.bindingBackupSource')">
        <el-select
          v-model="backupConfigId"
          filterable
          style="width: 100%"
        >
          <el-option
            v-for="row in backupConfigs"
            :key="row.id"
            :label="row.name"
            :value="row.id"
          />
        </el-select>
      </el-form-item>

      <el-form-item :label="t('insight.copilot.bindingSnapshot')">
        <el-select
          v-model="snapshotId"
          filterable
          :disabled="backupConfigId == null"
          style="width: 100%"
        >
          <el-option
            v-for="row in snapshots.filter((item) => item.backup_config_id === backupConfigId)"
            :key="row.id"
            :label="row.snapshot_uid"
            :value="row.id"
          />
        </el-select>
      </el-form-item>

      <el-form-item :label="t('insight.copilot.bindingDirectory')">
        <el-select
          v-model="directoryId"
          filterable
          :disabled="snapshotId == null || snapshotDirectories.length === 0"
          style="width: 100%"
        >
          <el-option
            v-for="row in snapshotDirectories"
            :key="row.id"
            :label="row.path"
            :value="row.id"
          />
        </el-select>
      </el-form-item>

      <el-form-item :label="t('insight.copilot.bindingGateway')">
        <el-select
          v-model="gatewayLinkId"
          clearable
          style="width: 100%"
        >
          <el-option
            :label="t('insight.copilot.bindingGatewayAuto')"
            :value="null"
          />
          <el-option
            v-for="row in gatewayOptions"
            :key="row.gateway_link_id"
            :label="`${row.name}${row.is_platform_default ? ` (${t('insight.copilot.gatewayPublicTitle')})` : ''}`"
            :value="row.gateway_link_id"
          />
        </el-select>
      </el-form-item>

      <el-button
        type="primary"
        :loading="submitting"
        @click="submit"
      >
        {{ submitting ? t('insight.copilot.bindingSubmitting') : t('insight.copilot.bindingSubmit') }}
      </el-button>
    </el-form>
  </div>
</template>

<style scoped>
.copilot-binding-setup {
  max-width: 560px;
  margin: 0 auto;
  padding: 2rem 1.5rem;
}

.copilot-binding-setup__header h2 {
  margin: 0 0 0.5rem;
  font-size: 1.25rem;
  font-weight: 600;
}

.copilot-binding-setup__header p {
  margin: 0 0 1.5rem;
  color: var(--el-text-color-secondary);
}

.copilot-binding-setup__form :deep(.el-form-item) {
  margin-bottom: 1rem;
}
</style>
