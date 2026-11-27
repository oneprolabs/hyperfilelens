import { ref } from 'vue'
import {
  listBackupSelectableSources,
  revertBackupSourcePipelineStep,
  setBackupSourcePipelineStep,
  type BackupPipelineStep,
} from '../lib/sourceApi'
import { apiErrorMessage } from '../lib/api'
import { toApiError } from '../lib/errors/normalizer'
import { getRouteRequestSignal } from '../lib/routeRequestAbort'
import { withRequestDeadline } from '../lib/requestDeadline'
import {
  clearLegacyStep2Sources,
  isBackupSelectableId,
  normalizeSourceIdList,
  readLegacyRealStep2Sources,
} from './useDemoFlowStep2Sources'

function isPipelineMoveBackwardsError(err: unknown) {
  return apiErrorMessage(err, '').includes('pipeline step cannot move backwards')
}

/** Real backup-selectable sources by backend-persisted pipeline step. */
export function useBackupSourcePipeline(options: {
  canViewSources?: () => boolean
  configurationScope?: boolean
} = {}) {
  const pipelineStep2Count = ref(0)
  const pipelineStep3Count = ref(0)
  let step2CountRequest = 0
  let step3CountRequest = 0

  const acceptStep2Count = (count: number) => {
    step2CountRequest += 1
    pipelineStep2Count.value = count
  }
  const acceptStep3Count = (count: number) => {
    step3CountRequest += 1
    pipelineStep3Count.value = count
  }

  async function refreshPipelineStep2Count(signal?: AbortSignal) {
    if (signal?.aborted) return
    const request = ++step2CountRequest
    if (options.canViewSources?.() === false) {
      pipelineStep2Count.value = 0
      return
    }
    const list = await withRequestDeadline(requestSignal => listBackupSelectableSources(
      { step: 2, page: 1, page_size: 1 }, { signal: requestSignal },
    ), signal ?? getRouteRequestSignal()).catch(error => {
      if (!signal?.aborted) throw error
    })
    if (!list) return
    if (signal?.aborted || request !== step2CountRequest) return
    if (options.canViewSources?.() === false) {
      pipelineStep2Count.value = 0
      return
    }
    pipelineStep2Count.value = list.count
  }

  async function refreshPipelineStep3Count(signal?: AbortSignal) {
    if (signal?.aborted) return
    const request = ++step3CountRequest
    const list = await withRequestDeadline(requestSignal => listBackupSelectableSources({
      step: 3, page: 1, page_size: 1,
      ...(options.configurationScope ? { scope: 'backup_configurations' as const } : {}),
    }, { signal: requestSignal }), signal ?? getRouteRequestSignal()).catch(error => {
      if (!signal?.aborted) throw error
    })
    if (!list) return
    if (signal?.aborted || request !== step3CountRequest) return
    pipelineStep3Count.value = list.count
  }

  async function refreshPipelineCounts(signal?: AbortSignal) {
    await Promise.all([
      refreshPipelineStep2Count(signal),
      refreshPipelineStep3Count(signal),
    ])
  }

  async function setPipelineStep(ids: string[], step: BackupPipelineStep, signal?: AbortSignal): Promise<string[]> {
    if (signal?.aborted) return []
    const realIds = normalizeSourceIdList(ids.filter(isBackupSelectableId))
    if (!realIds.length) return []
    const result = await setBackupSourcePipelineStep({ ids: realIds, step }, signal ? { signal } : undefined)
    if (!signal?.aborted) await refreshPipelineCounts(signal)
    return result.updated
  }

  async function revertPipelineStep(ids: string[], targetStep: 1 | 2) {
    const realIds = normalizeSourceIdList(ids.filter(isBackupSelectableId))
    if (!realIds.length) return []
    const result = await revertBackupSourcePipelineStep({ ids: realIds, target_step: targetStep })
    await refreshPipelineCounts()
    return result.updated
  }

  async function bootstrapPipeline(options: { signal?: AbortSignal; migrateLegacy?: boolean; refreshCounts?: boolean } = {}) {
    if (options.signal?.aborted) return
    // Opening a read-only page must not attempt a source-management write.
    const legacyReal = options.migrateLegacy === false ? [] : readLegacyRealStep2Sources()
    let migrated = false
    try {
      if (legacyReal.length) {
        await withRequestDeadline(signal => setBackupSourcePipelineStep({ ids: legacyReal, step: 2 }, { signal }),
          options.signal ?? getRouteRequestSignal())
      }
      migrated = true
    } catch (err) {
      if (options.signal?.aborted) return
      // Legacy browser storage is not an authorization grant. A stale source
      // may no longer be manageable even though permitted reads still work.
      if (!isPipelineMoveBackwardsError(err) && toApiError(err).status !== 403) throw err
      migrated = true
    } finally {
      // A cancelled migration can be retried on the next page visit. The
      // server remains authoritative if the write completed before abort.
      if (migrated && options.migrateLegacy !== false && !options.signal?.aborted) clearLegacyStep2Sources()
    }
    if (options.signal?.aborted) return
    if (options.refreshCounts !== false) await refreshPipelineCounts(options.signal)
  }

  return {
    pipelineStep2Count,
    pipelineStep3Count,
    acceptStep2Count,
    acceptStep3Count,
    refreshPipelineStep2Count,
    refreshPipelineStep3Count,
    refreshPipelineCounts,
    setPipelineStep,
    revertPipelineStep,
    bootstrapPipeline,
  }
}
