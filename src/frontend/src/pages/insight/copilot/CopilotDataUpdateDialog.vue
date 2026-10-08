<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElTag } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { Search } from 'lucide-vue-next'
import DangerConfirmDialog from '../../../components/DangerConfirmDialog.vue'
import '../../../components/backupSourceFlowActionDialog.css'
import '../../../styles/snapshot-picker.css'
import { apiErrorMessage } from '../../../lib/api'
import { compareSnapshotsNewestFirst, isNewerSnapshot, snapshotOptionLabel } from '../../../lib/snapshotPicker'
import { lifecycleStatusTagAttrs } from '../../../lib/statusTag'
import {
  abandonCopilotChatDataUpdate,
  updateCopilotChatData,
  type LensSessionLink,
} from '../../../lib/lensApi'
import {
  listBackupSourceSnapshots,
  getBackupSourceSnapshot,
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
const selectedSnapshot = ref<BackupSourceSnapshot | null>(null)
const appliedSnapshot = ref<BackupSourceSnapshot | null>(null)
const snapshotSearchText = ref('')
const selectedId = ref<number | null>(null)
const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
const snapshotCount = ref(0)
const loadedSnapshotCount = ref(0)
const hasMore = computed(() => loadedSnapshotCount.value < snapshotCount.value)
const page = ref(0)
const abandonConfirmOpen = ref(false)
let requestEpoch = 0
let searchTimer: ReturnType<typeof setTimeout> | undefined
let searchQuery = ''
let autoSelectPending = false
let appliedGeneration = 0
let appliedRequest: Promise<BackupSourceSnapshot | null> | null = null
const SNAPSHOT_PAGE_SIZE = 30

const retryTarget = computed(() =>
  props.session?.data_update?.status === 'failed'
    ? props.session.data_update.target_snapshot_id
    : null,
)

const appliedSnapshotId = computed(() =>
  props.session?.data_update?.applied_snapshot_id ?? props.session?.backup_source_snapshot_id,
)
const pickerSnapshots = computed(() => {
  const rows = [...snapshots.value]
  if (selectedSnapshot.value && !rows.some((row) => row.id === selectedSnapshot.value?.id)) {
    rows.unshift(selectedSnapshot.value)
  }
  if (appliedSnapshot.value && !rows.some((row) => row.id === appliedSnapshot.value?.id)) {
    rows.push(appliedSnapshot.value)
  }
  return rows.sort(compareSnapshotsNewestFirst)
})
watch(selectedId, (id) => {
  if (id == null) selectedSnapshot.value = null
  else selectedSnapshot.value = snapshots.value.find((row) => row.id === id) ?? selectedSnapshot.value
})

watch(() => [
  props.modelValue, props.session?.id, props.session?.backup_config_id,
  appliedSnapshotId.value, retryTarget.value,
] as const, async ([open]) => {
  const epoch = ++requestEpoch
  const generation = ++appliedGeneration
  clearTimeout(searchTimer)
  loading.value = false
  appliedSnapshot.value = null
  appliedRequest = null
  abandonConfirmOpen.value = false
  if (!open || !props.session?.backup_config_id) return
  snapshots.value = []
  selectedSnapshot.value = null
  snapshotSearchText.value = ''
  searchQuery = ''
  autoSelectPending = true
  page.value = 0
  snapshotCount.value = 0
  loadedSnapshotCount.value = 0
  selectedId.value = retryTarget.value ?? null
  loadError.value = ''
  const configId = props.session.backup_config_id
  const appliedId = appliedSnapshotId.value
  if (appliedId != null) {
    appliedRequest = getBackupSourceSnapshot(appliedId).then((detail) => {
      if (generation !== appliedGeneration || detail.id !== appliedId || detail.backup_config_id !== configId) return null
      appliedSnapshot.value = detail
      return detail
    }).catch(() => null)
  }
  await Promise.all([appliedRequest, loadSnapshots(false, epoch)])
}, { immediate: true })

async function loadSnapshots(append = false, epoch = requestEpoch) {
  const configId = props.session?.backup_config_id
  if (!props.modelValue || !configId || loading.value || (append && !hasMore.value)) return
  const target = retryTarget.value
  const appliedId = appliedSnapshotId.value
  const nextPage = append ? page.value + 1 : 1
  const query = searchQuery
  loading.value = true
  loadError.value = ''
  try {
    if (target) {
      // Retry always uses its pinned target, even when it is not on page one.
      const detail = target === appliedId && appliedRequest
        ? await appliedRequest
        : await getBackupSourceSnapshot(target)
      if (epoch !== requestEpoch) return
      if (detail?.id === target && detail.backup_config_id === configId) selectedSnapshot.value = detail
      return
    }
    const result = await listBackupSourceSnapshots({
      backup_config_id: configId,
      exclude_snapshot_id: appliedId ?? undefined,
      snapshot_uid: query || undefined,
      status: 'available,partial',
      page: nextPage,
      page_size: SNAPSHOT_PAGE_SIZE,
      ordering: 'picker_latest',
    })
    if (epoch !== requestEpoch) return
    let applied: BackupSourceSnapshot | null = null
    let latest = result.results.find((row) => row.status === 'available')
    if (autoSelectPending && !query && !latest && result.results.length) {
      try {
        const available = await listBackupSourceSnapshots({
          backup_config_id: configId,
          exclude_snapshot_id: appliedId ?? undefined,
          status: 'available',
          page: 1,
          page_size: 1,
          ordering: 'picker_latest',
        })
        latest = available.results.find((row) => row.status === 'available')
      } catch {
        // Require an explicit choice if the complete-snapshot lookup fails.
      }
      if (epoch !== requestEpoch) return
    }
    if (autoSelectPending && !query && latest && appliedId != null) {
      applied = appliedRequest ? await appliedRequest : appliedSnapshot.value
      if (epoch !== requestEpoch) return
    }
    const rows = result.results.filter((row) =>
      ['available', 'partial'].includes(row.status) && row.id !== appliedId,
    )
    snapshots.value = append
      ? [...snapshots.value, ...rows.filter((row) => !snapshots.value.some((existing) => existing.id === row.id))]
      : rows
    loadedSnapshotCount.value = append
      ? loadedSnapshotCount.value + result.results.length
      : result.results.length
    snapshotCount.value = result.results.length ? result.count : loadedSnapshotCount.value
    page.value = nextPage
    if (autoSelectPending && !query) {
      if (latest && applied && applied.id === appliedId && applied.backup_config_id === configId
        && isNewerSnapshot(latest, applied)) {
        selectedSnapshot.value = latest
        selectedId.value = latest.id
      }
      autoSelectPending = false
    }
    loadError.value = ''
  } catch (error) {
    if (epoch === requestEpoch) loadError.value = apiErrorMessage(error, t('errors.generic.loadFailed'))
  } finally {
    if (epoch === requestEpoch) loading.value = false
  }
}

function searchSnapshots(value: string) {
  const query = value.trim()
  if (query === searchQuery) return
  clearTimeout(searchTimer)
  const epoch = ++requestEpoch
  searchQuery = query
  loading.value = false
  autoSelectPending = false
  snapshots.value = []
  page.value = 0
  loadedSnapshotCount.value = 0
  snapshotCount.value = 0
  loadError.value = ''
  searchTimer = setTimeout(() => { void loadSnapshots(false, epoch) }, 250)
}

onBeforeUnmount(() => {
  clearTimeout(searchTimer)
  requestEpoch += 1
  appliedGeneration += 1
  appliedRequest = null
})

function close() {
  if (!saving.value) emit('update:modelValue', false)
}

async function submit() {
  const session = props.session
  const snapshotId = selectedId.value
  if (!session || snapshotId == null || saving.value
    || (retryTarget.value != null && snapshotId !== retryTarget.value)
    || (snapshotId === appliedSnapshotId.value && snapshotId !== retryTarget.value)) return
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
    class="hfl-flow-action-dialog hfl-flow-action-dialog--form chat-data-update-dialog"
    :title="retryTarget ? t('insight.copilot.retryDataUpdate') : t('insight.copilot.updateChatData')"
    width="min(680px, calc(100vw - 32px))"
    align-center
    append-to-body
    @close="close"
  >
    <div class="hfl-flow-action-dialog__body chat-update-body">
      <div class="chat-update-description">
        <p>{{ t('insight.copilot.dataUpdateScope', { count: sharedCount }) }}</p>
        <p>{{ t('insight.copilot.dataUpdateConsistency') }}</p>
      </div>
      <p
        v-if="retryTarget && session?.data_update?.error"
        class="chat-update-error"
        role="alert"
      >
        {{ session.data_update.error }}
      </p>
      <div class="chat-update-field">
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
          popper-class="chat-update-snapshot-popper"
          :loading="loading"
          :disabled="saving"
          :placeholder="t('insight.copilot.dataUpdateSnapshotPlaceholder')"
        >
          <template #label="{ label }">
            <span class="hfl-snapshot-choice">
              <span class="hfl-snapshot-choice__label">{{ label }}</span>
              <ElTag
                v-if="selectedSnapshot && ['available', 'partial'].includes(selectedSnapshot.status)"
                :type="lifecycleStatusTagAttrs(selectedSnapshot.status).type"
                class="hfl-snapshot-choice__status"
                :class="selectedSnapshot.status === 'partial' ? 'hfl-snapshot-choice__partial' : 'hfl-snapshot-choice__available'"
                size="small"
                effect="plain"
              >
                {{ t(selectedSnapshot.status === 'partial' ? 'insight.copilot.snapshotPartial' : 'insight.copilot.snapshotAvailable') }}
              </ElTag>
            </span>
          </template>
          <ElOption
            v-if="appliedSnapshotId != null && !appliedSnapshot && retryTarget !== appliedSnapshotId"
            class="hfl-snapshot-option--applied"
            :label="`#${appliedSnapshotId}`"
            :value="appliedSnapshotId"
            disabled
          >
            <span class="hfl-snapshot-choice">
              <span class="hfl-snapshot-choice__label">#{{ appliedSnapshotId }}</span>
              <ElTag
                class="hfl-snapshot-choice__status"
                :class="{ 'hfl-tag--neutral': Boolean(retryTarget) }"
                :type="retryTarget ? 'info' : 'primary'"
                size="small"
                effect="plain"
              >
                {{ t(retryTarget ? 'insight.copilot.snapshotLastApplied' : 'insight.copilot.snapshotCurrent') }}
              </ElTag>
            </span>
          </ElOption>
          <template
            v-if="!retryTarget"
            #header
          >
            <ElInput
              v-model="snapshotSearchText"
              class="chat-update-search"
              clearable
              inputmode="text"
              :placeholder="t('insight.copilot.searchSnapshotId')"
              :aria-label="t('insight.copilot.searchSnapshotId')"
              @input="searchSnapshots"
            >
              <template #prefix>
                <Search
                  :size="16"
                  class="hfl-list-search__icon"
                  aria-hidden="true"
                />
              </template>
            </ElInput>
          </template>
          <ElOption
            v-if="retryTarget && !pickerSnapshots.some((row) => row.id === retryTarget)"
            :label="`#${retryTarget}`"
            :value="retryTarget"
          >
            <span class="hfl-snapshot-choice">
              <span class="hfl-snapshot-choice__label">#{{ retryTarget }}</span>
              <ElTag
                v-if="retryTarget === appliedSnapshotId"
                class="hfl-snapshot-choice__status hfl-tag--neutral"
                type="info"
                size="small"
                effect="plain"
              >
                {{ t('insight.copilot.snapshotLastApplied') }}
              </ElTag>
            </span>
          </ElOption>
          <ElOption
            v-for="row in pickerSnapshots"
            :key="row.id"
            :label="snapshotOptionLabel(row)"
            :value="row.id"
            :class="{ 'hfl-snapshot-option--applied': row.id === appliedSnapshotId }"
            :disabled="retryTarget ? row.id !== retryTarget : row.id === appliedSnapshotId"
          >
            <span class="hfl-snapshot-choice">
              <span class="hfl-snapshot-choice__label">{{ snapshotOptionLabel(row) }}</span>
              <ElTag
                v-if="['available', 'partial'].includes(row.status)"
                :type="lifecycleStatusTagAttrs(row.status).type"
                class="hfl-snapshot-choice__status"
                :class="row.status === 'partial' ? 'hfl-snapshot-choice__partial' : 'hfl-snapshot-choice__available'"
                size="small"
                effect="plain"
              >
                {{ t(row.status === 'partial' ? 'insight.copilot.snapshotPartial' : 'insight.copilot.snapshotAvailable') }}
              </ElTag>
              <ElTag
                v-if="row.id === appliedSnapshotId"
                class="hfl-snapshot-choice__role"
                :class="{ 'hfl-tag--neutral': Boolean(retryTarget) }"
                :type="retryTarget ? 'info' : 'primary'"
                size="small"
                effect="plain"
              >
                {{ t(retryTarget ? 'insight.copilot.snapshotLastApplied' : 'insight.copilot.snapshotCurrent') }}
              </ElTag>
            </span>
          </ElOption>
          <template #footer>
            <div
              v-if="!retryTarget"
              class="chat-update-picker-footer"
            >
              <span>{{ loadedSnapshotCount }} / {{ snapshotCount }}</span>
              <button
                v-if="hasMore || loadError"
                type="button"
                :disabled="loading"
                @click.stop="loadSnapshots(page > 0)"
              >
                {{ t(loadError ? 'common.retry' : 'insight.copilot.loadMore') }}
              </button>
            </div>
          </template>
        </ElSelect>
        <p
          v-if="selectedSnapshot?.status === 'partial' || (!selectedSnapshot && snapshots.length && snapshots.every((row) => row.status === 'partial'))"
          class="chat-update-hint"
        >
          {{ t('insight.copilot.snapshotPartialHint') }}
        </p>
      </div>
      <p
        v-if="page > 0 && !snapshotSearchText.trim() && !loading && !loadError && !hasMore && !retryTarget && snapshots.length === 0"
        class="chat-update-hint"
      >
        {{ t('insight.copilot.dataUpdateNoSnapshots') }}
      </p>
      <p
        v-if="loadError"
        class="chat-update-error"
        role="alert"
      >
        {{ loadError }}
      </p>
    </div>
    <template #footer>
      <div class="chat-update-footer">
        <ElButton
          v-if="retryTarget"
          class="chat-update-abandon"
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
          :disabled="selectedId == null || loading || (retryTarget != null && selectedId !== retryTarget) || (selectedId === appliedSnapshotId && selectedId !== retryTarget)"
          :loading="saving"
          @click="submit"
        >
          {{ retryTarget ? t('insight.copilot.retryDataUpdate') : t('insight.copilot.updateChatData') }}
        </ElButton>
      </div>
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
:global(.chat-data-update-dialog.hfl-flow-action-dialog--form.el-dialog .el-dialog__header) { padding: 20px 24px 16px; border-bottom: 1px solid var(--el-border-color-extra-light); }
:global(.chat-data-update-dialog.hfl-flow-action-dialog--form.el-dialog .el-dialog__body) { padding: 16px 24px 18px; }
:global(.chat-data-update-dialog.hfl-flow-action-dialog--form.el-dialog .el-dialog__footer) { padding: 12px 24px 16px; }
.chat-update-body { gap: 16px; }
.chat-update-description { padding: 12px 14px; border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-grey-2); color: var(--color-text-secondary); font-size: 13px; line-height: 1.6; }
.chat-update-description p { margin: 0; }
.chat-update-description p + p { margin-top: 6px; }
.chat-update-field { display: grid; gap: 6px; }
.chat-update-label { display: block; margin: 0; color: var(--color-text-primary); font-size: 13px; font-weight: 600; }
.chat-update-select { width: 100%; }
:global(.chat-update-snapshot-popper .el-select-dropdown__item.is-disabled.hfl-snapshot-option--applied) { color: var(--color-text-secondary); opacity: 1; cursor: not-allowed; }
.chat-update-hint, .chat-update-error { margin: 0; font-size: 13px; line-height: 1.6; }
.chat-update-hint { color: var(--color-text-secondary); }
.chat-update-error { color: var(--color-error-text); }
.chat-update-search { width: 100%; }
.chat-update-search :deep(.el-input__wrapper) { border-radius: 8px; box-shadow: 0 0 0 1px rgba(203, 213, 225, .95) inset; }
.chat-update-search :deep(.el-input__wrapper.is-focus) { box-shadow: 0 0 0 1px var(--color-primary, #6d5ef6) inset; }
.chat-update-picker-footer { display: flex; align-items: center; justify-content: space-between; padding: 4px 10px; color: var(--color-text-tertiary); font-size: 12px; }
.chat-update-picker-footer button { padding: 0; border: 0; background: transparent; color: var(--color-primary); font: inherit; cursor: pointer; }
.chat-update-picker-footer button:disabled { cursor: default; opacity: .5; }
.chat-update-footer { display: flex; width: 100%; align-items: center; justify-content: flex-end; gap: 8px; }
.chat-update-footer :deep(.el-button + .el-button) { margin-left: 0; }
.chat-update-abandon { margin-right: auto; }
@media (max-width: 767.98px) {
  :global(.chat-data-update-dialog.hfl-flow-action-dialog--form.el-dialog .el-dialog__header) { padding: 16px 16px 12px; }
  :global(.chat-data-update-dialog.hfl-flow-action-dialog--form.el-dialog .el-dialog__body) { padding: 14px 16px 16px; }
  :global(.chat-data-update-dialog.hfl-flow-action-dialog--form.el-dialog .el-dialog__footer) { padding: 10px 16px; }
  .chat-update-footer { flex-wrap: wrap; }
  .chat-update-footer :deep(.el-button) { width: 100%; min-height: 44px; margin: 0; }
}
</style>
