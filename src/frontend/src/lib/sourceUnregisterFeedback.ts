import type { ComposerTranslation } from 'vue-i18n'
import { claimTaskNotice } from './asyncNoticeLedger'
import { notifySuccess } from './notify'
import { sourceUnregisterTaskOutcome } from './sourceUnregisterMonitor'
import type { TaskRow } from './taskApi'

/** Only a complete, clean operation gets a success toast, never an acceptance. */
export function notifyUnregisterTaskSuccess(input: {
  t: ComposerTranslation
  tasks: TaskRow[]
  sourceName?: string
  hasRejections?: boolean
}) {
  if (input.hasRejections || !input.tasks.length) return
  if (!input.tasks.every((task) => {
    const outcome = sourceUnregisterTaskOutcome(task)
    return outcome.terminal && outcome.success && !outcome.partialSuccess
      && outcome.cleanupComplete && !outcome.pendingRemovals.length
      && !outcome.cleanupFailures.length && !outcome.cleanupWarnings.length
      && !outcome.retainedResources.length && !outcome.failedChildren.length
  })) return
  const taskUuids = input.tasks.map(task => task.task_uuid).sort()
  if (taskUuids.some(uuid => !uuid)) return
  const operationId = taskUuids.join(',')
  // Keep the ledger identity independent of the current language and source name.
  if (!claimTaskNotice({
    taskUuid: operationId,
    taskType: 'source_unregister',
    summary: 'source_unregister_completed',
    errorCode: 'SOURCE_UNREGISTER_COMPLETED',
  })) return
  const message = input.tasks.length === 1 && input.sourceName
    ? input.t('protection.backupsPage.msgDeleteSourceSuccessNamed', { name: input.sourceName })
    : input.t('protection.backupsPage.msgDeleteSourceSuccessCount', { n: input.tasks.length })
  return notifySuccess({ message, dedupeKey: `unregister-success:${operationId}` })
}
