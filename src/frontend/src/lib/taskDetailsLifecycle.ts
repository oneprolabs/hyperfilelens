import type { ComposerTranslation } from 'vue-i18n'
import type { ErrorDetailsPayload } from './errors/details'

export async function refreshTaskDetails(snapshot: ErrorDetailsPayload, t: ComposerTranslation): Promise<ErrorDetailsPayload> {
  if (!snapshot.taskUuid || !snapshot.taskType) return snapshot.capturedAt && Date.now() - snapshot.capturedAt > 24 * 60 * 60 * 1000
    ? { ...snapshot, lifecycleMessage: t('feedback.errorDetails.snapshotExpired') }
    : snapshot
  try {
    const { getTask } = await import('./taskApi')
    let task = await getTask(snapshot.taskUuid)
    const visited = new Set([task.task_uuid])
    while (task.replacement_task_uuid && !visited.has(task.replacement_task_uuid) && visited.size < 8) {
      task = await getTask(task.replacement_task_uuid)
      visited.add(task.task_uuid)
    }
    const lifecycleMessage = visited.size > 1 ? t('feedback.errorDetails.taskReplaced') : undefined
    if (task.status !== 'cancelled' && (task.error_details || ['failed', 'timeout', 'partial'].includes(task.status))) {
      const { buildTaskFailureErrorDetails } = await import('./backupTaskFailureLogic')
      const { unregisterFailureToErrorDetails } = await import('./unregisterFailureDetails')
      const details = task.task_type !== 'source_unregister'
        ? buildTaskFailureErrorDetails({ task, t })
        : unregisterFailureToErrorDetails({ task, t })
      return { ...details, lifecycleMessage }
    }
    // A recovered or replaced operation must not retain the old diagnosis.
    return {
      title: t(`ops.task.status.${task.status}`),
      summary: t(`ops.task.status.${task.status}`),
      taskUuid: task.task_uuid,
      taskType: task.task_type,
      severity: 'warning',
      lifecycleMessage,
    }
  } catch (error) {
    const status = Number((error as { status?: number })?.status)
    if ([403, 404, 410].includes(status)) return { ...snapshot, lifecycleMessage: t('feedback.errorDetails.taskUnavailable') }
    return { ...snapshot, lifecycleMessage: t(snapshot.capturedAt && Date.now() - snapshot.capturedAt > 24 * 60 * 60 * 1000 ? 'feedback.errorDetails.snapshotExpired' : 'feedback.errorDetails.monitorUnavailable') }
  }
}
