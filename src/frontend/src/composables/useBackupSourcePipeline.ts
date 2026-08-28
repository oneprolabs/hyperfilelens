import { ref } from 'vue'
import {
  listBackupSelectableSources,
  revertBackupSourcePipelineStep,
  setBackupSourcePipelineStep,
  type BackupPipelineStep,
} from '../lib/sourceApi'
import { apiErrorMessage } from '../lib/api'
import {
  clearLegacyStep2Sources,
  isBackupSelectableId,
  readLegacyRealStep2Sources,
} from './useDemoFlowStep2Sources'

function isPipelineMoveBackwardsError(err: unknown) {
  return apiErrorMessage(err, '').includes('pipeline step cannot move backwards')
}

/** Real backup-selectable sources by backend-persisted pipeline step. */
export function useBackupSourcePipeline() {
  const pipelineStep2Count = ref(0)
  const pipelineStep3Count = ref(0)

  async function refreshPipelineStep2Count(signal?: AbortSignal) {
    const list = await listBackupSelectableSources({ step: 2, page: 1, page_size: 1 }, { signal })
    pipelineStep2Count.value = list.count
  }

  async function refreshPipelineStep3Count(signal?: AbortSignal) {
    const list = await listBackupSelectableSources({ step: 3, page: 1, page_size: 1 }, { signal })
    pipelineStep3Count.value = list.count
  }

  async function refreshPipelineCounts(signal?: AbortSignal) {
    await Promise.all([
      refreshPipelineStep2Count(signal),
      refreshPipelineStep3Count(signal),
    ])
  }

  async function setPipelineStep(ids: string[], step: BackupPipelineStep) {
    const realIds = normalizeSourceIdList(ids.filter(isBackupSelectableId))
    if (!realIds.length) return []
    const result = await setBackupSourcePipelineStep({ ids: realIds, step })
    await refreshPipelineCounts()
    return result.updated
  }

  async function revertPipelineStep(ids: string[], targetStep: 1 | 2) {
    const realIds = normalizeSourceIdList(ids.filter(isBackupSelectableId))
    if (!realIds.length) return []
    const result = await revertBackupSourcePipelineStep({ ids: realIds, target_step: targetStep })
    await refreshPipelineCounts()
    return result.updated
  }

  async function bootstrapPipeline() {
    const legacyReal = readLegacyRealStep2Sources()
    try {
      if (legacyReal.length) {
        await setPipelineStep(legacyReal, 2)
      }
    } catch (err) {
      if (!isPipelineMoveBackwardsError(err)) throw err
    } finally {
      clearLegacyStep2Sources()
    }
    await refreshPipelineCounts()
  }

  return {
    pipelineStep2Count,
    pipelineStep3Count,
    refreshPipelineStep2Count,
    refreshPipelineStep3Count,
    refreshPipelineCounts,
    setPipelineStep,
    revertPipelineStep,
    bootstrapPipeline,
  }
}
