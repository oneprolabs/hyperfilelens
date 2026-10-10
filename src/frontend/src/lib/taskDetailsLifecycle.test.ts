// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { refreshTaskDetails } from './taskDetailsLifecycle'
import { closeErrorDetails, errorDetailsState, openErrorDetails, replaceErrorDetails } from './errors/details'
import type { ComposerTranslation } from 'vue-i18n'
const { getTask } = vi.hoisted(() => ({ getTask: vi.fn() }))
vi.mock('./taskApi', () => ({ getTask }))
const t = ((key: string) => key) as ComposerTranslation
const snapshot = { title: 'Old failure', summary: 'Old failure', taskUuid: 'task-1', taskType: 'backup', reasons: ['Original analysis'], resolutions: ['Original remedy'] }
beforeEach(() => { getTask.mockReset(); closeErrorDetails() })
describe('current task detail lifecycle', () => {
  it.each(['success', 'running', 'pending', 'cancelled'])('removes obsolete diagnostics for %s', async status => {
    getTask.mockResolvedValue({ task_uuid: 'task-1', task_type: 'backup', status })
    const result = await refreshTaskDetails(snapshot, t)
    expect(getTask).toHaveBeenCalledWith('task-1')
    expect(result.summary).toBe(`ops.task.status.${status}`)
    expect(result.reasons).toBeUndefined()
    expect(result.resolutions).toBeUndefined()
  })
  it('reopens using the current contract without changing its analysis or remedy', async () => {
    getTask.mockResolvedValue({ task_uuid: 'task-1', task_type: 'backup', status: 'failed', error_details: {
      severity: 'error', summary: 'Current failure', reasons: [{ detail: 'Original analysis' }], suggestions: [{ detail: 'Original remedy' }],
    } })
    const result = await refreshTaskDetails(snapshot, t)
    expect(result.summary).toBe('Current failure')
    expect(result.reasons).toEqual(snapshot.reasons)
    expect(result.resolutions).toEqual(snapshot.resolutions)
  })
  it('keeps a snapshot but identifies loss of visibility', async () => {
    getTask.mockRejectedValue(new Error('offline'))
    expect(await refreshTaskDetails(snapshot, t)).toEqual({ ...snapshot, lifecycleMessage: 'feedback.errorDetails.monitorUnavailable' })
  })
  it('marks old unrefreshable snapshots stale rather than asserting task failure', async () => {
    const result = await refreshTaskDetails({ ...snapshot, taskUuid: undefined, capturedAt: Date.now() - 86400001 }, t)
    expect(result.lifecycleMessage).toBe('feedback.errorDetails.snapshotExpired')
    expect(getTask).not.toHaveBeenCalled()
  })
  it('ignores a response from a replaced or closed details panel', () => {
    openErrorDetails(snapshot)
    const revision = errorDetailsState.revision
    openErrorDetails({ title: 'New', summary: 'New' })
    replaceErrorDetails(snapshot, revision)
    expect(errorDetailsState.current?.summary).toBe('New')
    closeErrorDetails()
    replaceErrorDetails(snapshot, errorDetailsState.revision)
    expect(errorDetailsState.current).toBeNull()
  })
  it('follows an explicit replacement without showing the obsolete failure', async () => {
    getTask.mockResolvedValueOnce({ task_uuid: 'task-1', replacement_task_uuid: 'task-2', status: 'failed' })
      .mockResolvedValueOnce({ task_uuid: 'task-2', task_type: 'backup', status: 'success' })
    const result = await refreshTaskDetails(snapshot, t)
    expect(result.taskUuid).toBe('task-2')
    expect(result.lifecycleMessage).toBe('feedback.errorDetails.taskReplaced')
    expect(result.reasons).toBeUndefined()
  })
  it.each([403, 404, 410])('identifies inaccessible tasks (%s) separately from failures', async status => {
    getTask.mockRejectedValue({ status })
    const result = await refreshTaskDetails(snapshot, t)
    expect(result.lifecycleMessage).toBe('feedback.errorDetails.taskUnavailable')
    expect(result.reasons).toEqual(snapshot.reasons)
  })

})
