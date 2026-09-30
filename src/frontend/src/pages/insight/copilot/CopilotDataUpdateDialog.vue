<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import DangerConfirmDialog from '../../../components/DangerConfirmDialog.vue'
import { apiErrorMessage } from '../../../lib/api'
import {
  abandonCopilotChatDataUpdate,
  updateCopilotChatData,
  type LensSessionLink,
} from '../../../lib/lensApi'
import {
  listBackupSourceSnapshots,
  type BackupSourceSnapshot,
} from '../../../lib/protectionBackupConfigApi'

const props = defineProps<{
  modelValue: boolean
  session: LensSessionLink | null
  sharedCount: number
}>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  saved: [knowledgeSourceId: number, update: NonNullable<LensSessionLink['data_update']>]
}>()
const { t } = useI18n()
const snapshots = ref<BackupSourceSnapshot[]>([])
const selectedId = ref<number | null>(null)
const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
const hasMore = ref(false)
const page = ref(1)
const abandonConfirmOpen = ref(false)
let requestEpoch = 0

const retryTarget = computed(() =>
  props.session?.data_update?.status === 'failed'
    ? props.session.data_update.target_snapshot_id
    : null,
)

watch(() => [props.modelValue, props.session?.id] as const, async ([open]) => {
  const epoch = ++requestEpoch
  abandonConfirmOpen.value = false
  if (!open || !props.session?.backup_config_id) return
  loading.value = false
  snapshots.value = []
  page.value = 1
  selectedId.value = retryTarget.value ?? null
  loadError.value = ''
  await loadSnapshots(epoch)
}, { immediate: true })

async function loadSnapshots(epoch = requestEpoch) {
  if (!props.session?.backup_config_id || loading.value) return
  loading.value = true
  try {
    const result = await listBackupSourceSnapshots({
      backup_config_id: props.session.backup_config_id,
      page: page.value,
      page_size: 100,
      ordering: '-created_at',
    })
    if (epoch !== requestEpoch) return
    snapshots.value = [...snapshots.value, ...result.results.filter(
      (snapshot) => ['available', 'partial'].includes(snapshot.status)
        && (snapshot.id !== props.session?.data_update?.applied_snapshot_id || snapshot.id === retryTarget.value),
    )]
    hasMore.value = result.count > page.value * 100
    loadError.value = ''
  } catch (error) {
    if (epoch === requestEpoch) loadError.value = apiErrorMessage(error, t('errors.generic.loadFailed'))
  } finally {
    if (epoch === requestEpoch) loading.value = false
  }
}

function close() {
  if (!saving.value) emit('update:modelValue', false)
}

async function submit() {
  const session = props.session
  const snapshotId = selectedId.value
  if (!session || snapshotId == null || saving.value) return
  saving.value = true
  try {
    const update = await updateCopilotChatData(session.id, snapshotId)
    if (session.knowledge_source != null) emit('saved', session.knowledge_source, update)
    emit('update:modelValue', false)
    ElMessage.success(t('insight.copilot.dataUpdateQueued'))
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, t('errors.generic.requestFailed')))
  } finally {
    saving.value = false
  }
}

function abandon() {
  const session = props.session
  if (!session || !retryTarget.value || saving.value) return
  abandonConfirmOpen.value = true
}

async function confirmAbandon() {
  const session = props.session
  if (!session || !retryTarget.value || saving.value) return
  saving.value = true
  try {
    const update = await abandonCopilotChatDataUpdate(session.id)
    if (session.knowledge_source != null) emit('saved', session.knowledge_source, update)
    abandonConfirmOpen.value = false
    emit('update:modelValue', false)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, t('errors.generic.requestFailed')))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <ElDialog
    :model-value="modelValue"
    :title="retryTarget ? t('insight.copilot.retryDataUpdate') : t('insight.copilot.updateChatData')"
    width="min(94vw, 520px)"
    @close="close"
  >
    <p>{{ t('insight.copilot.dataUpdateScope', { count: sharedCount }) }}</p>
    <p>{{ t('insight.copilot.dataUpdateConsistency') }}</p>
    <p
      v-if="retryTarget && session?.data_update?.error"
      role="alert"
    >
      {{ session.data_update.error }}
    </p>
    <label
      class="chat-update-label"
      for="chat-data-update-snapshot"
    >
      {{ t('insight.copilot.dataUpdateSnapshot') }}
    </label>
    <ElSelect
      id="chat-data-update-snapshot"
      v-model="selectedId"
      class="chat-update-select"
      :disabled="saving || Boolean(retryTarget)"
      :placeholder="t('insight.copilot.dataUpdateSnapshot')"
    >
      <ElOption
        v-if="retryTarget && !snapshots.some((row) => row.id === retryTarget)"
        :label="`#${retryTarget}`"
        :value="retryTarget"
      />
      <ElOption
        v-for="row in snapshots"
        :key="row.id"
        :label="`${new Date(row.created_at).toLocaleString()} · #${row.id}`"
        :value="row.id"
      />
    </ElSelect>
    <p v-if="!loading && !loadError && !hasMore && !retryTarget && snapshots.length === 0">
      {{ t('insight.copilot.dataUpdateNoSnapshots') }}
    </p>
    <p
      v-if="loadError"
      role="alert"
    >
      {{ loadError }}
    </p>
    <ElButton
      v-if="hasMore && !retryTarget"
      text
      :loading="loading"
      @click="page += 1; loadSnapshots()"
    >
      {{ t('insight.copilot.dataUpdateMore') }}
    </ElButton>
    <template #footer>
      <ElButton
        v-if="retryTarget"
        type="danger"
        plain
        :disabled="saving"
        @click="abandon"
      >
        {{ t('insight.copilot.abandonDataUpdate') }}
      </ElButton>
      <ElButton
        :disabled="saving"
        @click="close"
      >
        {{ t('insight.copilot.btnCancel') }}
      </ElButton>
      <ElButton
        type="primary"
        :disabled="selectedId == null || loading"
        :loading="saving"
        @click="submit"
      >
        {{ retryTarget ? t('insight.copilot.retryDataUpdate') : t('insight.copilot.updateChatData') }}
      </ElButton>
    </template>
  </ElDialog>
  <DangerConfirmDialog
    v-model="abandonConfirmOpen"
    :title="t('insight.copilot.abandonDataUpdate')"
    :message="t('insight.copilot.abandonDataUpdateWarning')"
    :cancel-text="t('insight.copilot.btnCancel')"
    :confirm-text="t('insight.copilot.abandonDataUpdate')"
    :loading="saving"
    @confirm="confirmAbandon"
  />
</template>

<style scoped>
.chat-update-label { display: block; margin: 16px 0 8px; font-weight: 600; }
.chat-update-select { width: 100%; }
</style>
