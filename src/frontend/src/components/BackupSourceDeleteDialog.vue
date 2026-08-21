<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton, ElDialog } from 'element-plus'
import BackupSourceUnregisterDialogBody from './BackupSourceUnregisterDialogBody.vue'
import {
  mergeUnregisterSubmitRisks,
  type BackupSourceUnregisterDisplayRow,
} from '../lib/backupSourceUnregisterDialog'
import type { ErrorDetailsPayload } from '../lib/errors/details'
import { toApiError } from '../lib/errors/normalizer'
import {
  bulkDeleteBackupSources,
  parseBackupSourceDeleteError,
  preflightDeleteBackupSources,
  type BackupSourceDeletePreflight,
  type BackupSourceDeleteReason,
  type BackupSourceDeleteResult,
} from '../lib/sourceApi'
import {
  notifyUnregisterFailureBatch,
  openUnregisterFailureDetails,
  previousUnregisterFailureDetails,
  unregisterFailureBannerText,
  unregisterFailureToErrorDetails,
  unregisterSyncFailuresBySource,
} from '../lib/unregisterFailureDetails'
import './backupSourceFlowActionDialog.css'

const props = defineProps<{
  modelValue: boolean
  sourceIds: string[]
  sources?: BackupSourceUnregisterDisplayRow[]
  showSnapshots?: boolean
  previousFailureDetails?: ErrorDetailsPayload | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'deleted', payload: {
    result: string
    warnings: Array<Record<string, unknown>>
    pending_removals: BackupSourceDeleteResult['pending_removals']
    task_id?: number
    task_uuid?: string
    task_ids?: number[]
    task_uuids?: string[]
    group_uuid?: string
    tasks?: BackupSourceDeleteResult['tasks']
    rejected?: BackupSourceDeleteResult['rejected']
    accepted?: boolean
  }): void
  (e: 'conflict', payload: { sourceIds: string[] }): void
}>()

const { t } = useI18n()
const force = ref(false)
const loading = ref(false)
const preflightLoading = ref(false)
const preflight = ref<BackupSourceDeletePreflight | null>(null)
const preflightError = ref(false)
const submitErrorReasons = ref<BackupSourceDeleteReason[]>([])
const lastFailureDetails = ref<ErrorDetailsPayload | null>(null)
const confirmText = ref('')
const idempotencyKey = ref('')
const frozenSourceIds = ref<string[]>([])
const frozenSources = ref<BackupSourceUnregisterDisplayRow[]>([])
let preflightRequestSeq = 0
const confirmationKeyword = computed(() => force.value ? 'FORCE DEREGISTER' : 'DEREGISTER')

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})
const dialogSourceIds = computed(() => frozenSourceIds.value)
const dialogSources = computed(() => frozenSources.value)

const title = computed(() =>
  dialogSourceIds.value.length > 1
    ? t('protection.backupsPage.titleDeleteSource')
    : t('protection.backupsPage.titleDeleteSourceSingle'),
)

const displayRisks = computed(() => mergeUnregisterSubmitRisks(preflight.value, submitErrorReasons.value))

const activePreviousFailure = computed(() =>
  lastFailureDetails.value || props.previousFailureDetails || null,
)

const previousFailureTitle = computed(() =>
  activePreviousFailure.value?.title
  || t('protection.backupsPage.unregisterPreviousFailureTitle'),
)

const previousFailureSummary = computed(() =>
  unregisterFailureBannerText(activePreviousFailure.value),
)

const deleteDisabled = computed(() => {
  if (loading.value || preflightLoading.value) return true
  if (preflightError.value || !preflight.value) return true
  if (preflight.value?.delete_disabled) return true
  if (confirmText.value !== confirmationKeyword.value) return true
  return false
})

async function loadPreflight() {
  const requestSeq = ++preflightRequestSeq
  const sourceIds = [...dialogSourceIds.value]
  if (!sourceIds.length) {
    preflight.value = null
    preflightError.value = false
    preflightLoading.value = false
    return
  }
  preflight.value = null
  preflightError.value = false
  preflightLoading.value = true
  try {
    const result = await preflightDeleteBackupSources(sourceIds, force.value)
    if (requestSeq !== preflightRequestSeq) return
    preflight.value = result
    submitErrorReasons.value = []
  } catch {
    if (requestSeq !== preflightRequestSeq) return
    preflight.value = null
    preflightError.value = true
  } finally {
    if (requestSeq === preflightRequestSeq) preflightLoading.value = false
  }
}

function resetDialogState() {
  preflightRequestSeq += 1
  force.value = false
  confirmText.value = ''
  idempotencyKey.value = ''
  preflight.value = null
  preflightError.value = false
  preflightLoading.value = false
  submitErrorReasons.value = []
  lastFailureDetails.value = null
}

function sourceLabel(sourceId: string) {
  return dialogSources.value.find((row) => row.id === sourceId)?.name || sourceId
}

function viewPreviousFailure() {
  if (!activePreviousFailure.value) return
  openUnregisterFailureDetails(activePreviousFailure.value)
}

watch(
  () => [props.modelValue, props.sourceIds.join(',')] as const,
  ([open]) => {
    if (!open) {
      resetDialogState()
      return
    }
    if (loading.value) return
    frozenSourceIds.value = [...props.sourceIds]
    frozenSources.value = (props.sources || []).map(source => ({ ...source }))
    resetDialogState()
    void loadPreflight()
  },
  { immediate: true },
)

watch(force, () => {
  confirmText.value = ''
  idempotencyKey.value = ''
  void loadPreflight()
})

function close() {
  if (loading.value) return
  visible.value = false
}

async function confirmDelete() {
  if (deleteDisabled.value || !dialogSourceIds.value.length) return
  const sourceIds = [...dialogSourceIds.value]
  const forceDelete = force.value
  const confirmation = confirmText.value
  loading.value = true
  try {
    if (!idempotencyKey.value) {
      const requestId = typeof globalThis.crypto?.randomUUID === 'function'
        ? globalThis.crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`
      idempotencyKey.value = `source-unregister:${requestId}`
    }
    const result = await bulkDeleteBackupSources(
      sourceIds,
      forceDelete,
      confirmation,
      idempotencyKey.value,
    )
    submitErrorReasons.value = []
    lastFailureDetails.value = null
    visible.value = false
    emit('deleted', {
      result: result.result,
      warnings: result.warnings || [],
      pending_removals: result.pending_removals || [],
      task_id: result.task_id,
      task_uuid: result.task_uuid,
      task_ids: result.task_ids,
      task_uuids: result.task_uuids,
      group_uuid: result.group_uuid,
      tasks: result.tasks,
      rejected: result.rejected,
      accepted: Boolean(result.accepted),
    })
  } catch (err: unknown) {
    if (toApiError(err).errorCode === 'BACKUP.ALREADY_RUNNING') {
      emit('conflict', { sourceIds })
    }
    const parsed = parseBackupSourceDeleteError(err)
    submitErrorReasons.value = parsed.reasons
    const failuresBySourceMap = unregisterSyncFailuresBySource({
      t,
      sourceIds,
      sourceName: sourceLabel,
      apiError: err,
    })
    const failuresBySource = Object.fromEntries(failuresBySourceMap)
    const primaryId = sourceIds[0]
    const details = previousUnregisterFailureDetails(
      t,
      sourceIds.flatMap((sourceId) => {
        const item = failuresBySource[sourceId]
        return item ? [item] : []
      }),
    ) || (primaryId ? failuresBySource[primaryId] : undefined)
      || unregisterFailureToErrorDetails({ t, apiError: err })
    lastFailureDetails.value = details
    notifyUnregisterFailureBatch({
      t,
      items: sourceIds.map((sourceId) => ({
        sourceId,
        sourceName: sourceLabel(sourceId),
        details: failuresBySource[sourceId],
      })),
      dedupeKey: `unregister-api-failure:${sourceIds.join(',')}`,
    })
    void loadPreflight()
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <ElDialog
    v-model="visible"
    :title="title"
    class="hfl-flow-action-dialog hfl-flow-action-dialog--delete"
    align-center
    :close-on-click-modal="!loading"
    :close-on-press-escape="!loading"
    @close="close"
  >
    <BackupSourceUnregisterDialogBody
      v-model:force="force"
      v-model:confirm-text="confirmText"
      :source-ids="dialogSourceIds"
      :sources="dialogSources"
      :show-snapshots="showSnapshots === true"
      :preflight="preflight"
      :display-risks="displayRisks"
      :preflight-loading="preflightLoading"
      :preflight-error="preflightError"
      :loading="loading"
      :previous-failure-title="previousFailureTitle"
      :previous-failure-summary="previousFailureSummary"
      :previous-failure-clickable="Boolean(activePreviousFailure)"
      @retry-preflight="loadPreflight"
      @view-previous-failure="viewPreviousFailure"
      @confirm="confirmDelete"
    />

    <template #footer>
      <ElButton
        :disabled="loading"
        @click="close"
      >
        {{ t('common.cancel') }}
      </ElButton>
      <ElButton
        type="danger"
        :loading="loading"
        :disabled="deleteDisabled"
        @click="confirmDelete"
      >
        {{ t('protection.backupsPage.btnConfirmUnregisterSource') }}
      </ElButton>
    </template>
  </ElDialog>
</template>
