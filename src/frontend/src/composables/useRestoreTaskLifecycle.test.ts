import { describe, expect, it, vi } from 'vitest'
import type { RestoreCreateResult } from '../lib/restoreApi'
import {
  isExplicitRestoreCancelRejection,
  runRestoreJobsSequentially,
  useRestoreTaskLifecycle,
} from './useRestoreTaskLifecycle'

function restoreResult(taskUuid: string, restoreRecordId = 1): RestoreCreateResult {
  return {
    restore_record_id: restoreRecordId,
    restore_uid: `restore-${restoreRecordId}`,
    task_id: restoreRecordId,
    task_uuid: taskUuid,
    status: 'pending',
    source_snapshot_id: restoreRecordId,
    item_count: 1,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

describe('restore task lifecycle', () => {
  it('keeps an accepted restore active across missing and stale observations', () => {
    const lifecycle = useRestoreTaskLifecycle(() => 100)
    lifecycle.accept('agent:1', restoreResult('task-1'))

    lifecycle.reconcile([])
    lifecycle.reconcile([{ taskUuid: 'older-task', status: 'success' }])

    expect(lifecycle.get('agent:1')).toMatchObject({
      taskUuid: 'task-1',
      phase: 'accepted',
      acceptedAt: 100,
    })
  })

  it('converges only when the same task UUID becomes running or terminal', () => {
    const lifecycle = useRestoreTaskLifecycle()
    lifecycle.accept('agent:1', restoreResult('task-1'))

    lifecycle.reconcile([{ taskUuid: 'task-1', status: 'running' }])
    expect(lifecycle.get('agent:1')?.phase).toBe('running')

    lifecycle.reconcile([{ taskUuid: 'task-1', status: 'success' }])
    expect(lifecycle.get('agent:1')).toBeNull()
  })

  it('does not clear stopping when a stale running status arrives', () => {
    const lifecycle = useRestoreTaskLifecycle()
    lifecycle.accept('agent:1', restoreResult('task-1'))
    lifecycle.markStopping('agent:1', 'task-1')

    lifecycle.reconcile([{ taskUuid: 'task-1', status: 'running' }])
    expect(lifecycle.get('agent:1')?.phase).toBe('stopping')

    lifecycle.reconcile([{ taskUuid: 'task-1', status: 'cancelled', stopping: true }])
    expect(lifecycle.get('agent:1')?.phase).toBe('stopping')

    lifecycle.reconcile([{ taskUuid: 'task-1', status: 'cancelled', stopping: false }])
    expect(lifecycle.get('agent:1')).toBeNull()
  })

  it('treats cancelled without Agent authority as an unknown stopping state', () => {
    const lifecycle = useRestoreTaskLifecycle()
    lifecycle.accept('agent:1', restoreResult('task-1'))

    lifecycle.reconcile([{ taskUuid: 'task-1', status: 'cancelled' }])

    expect(lifecycle.get('agent:1')?.phase).toBe('stopping')
  })

  it('returns to running only after an explicit cancel rejection', () => {
    const lifecycle = useRestoreTaskLifecycle()
    lifecycle.accept('agent:1', restoreResult('task-1'))
    lifecycle.markStopping('agent:1', 'task-1')

    // A timeout or network-unknown path performs no rollback.
    expect(lifecycle.get('agent:1')?.phase).toBe('stopping')

    lifecycle.rejectStopping('agent:1', 'task-1')
    expect(lifecycle.get('agent:1')?.phase).toBe('running')
  })

  it('tracks multiple sources independently and exposes stale accepted work', () => {
    let now = 100
    const lifecycle = useRestoreTaskLifecycle(() => now)
    lifecycle.accept('agent:1', restoreResult('task-1'))
    now = 200
    lifecycle.accept('nas:2', restoreResult('task-2', 2))
    now = 250

    expect(lifecycle.staleEntries(100).map((entry) => entry.sourceId)).toEqual(['agent:1'])
    lifecycle.reconcile([{ taskUuid: 'task-1', status: 'failed' }])
    expect(lifecycle.isActive('agent:1')).toBe(false)
    expect(lifecycle.isActive('nas:2')).toBe(true)
  })
})

describe('restore cancel rejection classification', () => {
  it('rolls back only explicit business rejections', () => {
    expect(isExplicitRestoreCancelRejection({ status: 409, errorCode: 'RESTORE.NOT_RUNNING' })).toBe(true)
    expect(isExplicitRestoreCancelRejection({ status: 422, errorCode: 'VALIDATION.ERROR' })).toBe(true)
    expect(isExplicitRestoreCancelRejection({ status: 500, errorCode: 'SERVER.ERROR' })).toBe(false)
    expect(isExplicitRestoreCancelRejection({ status: 504, errorCode: 'GATEWAY.TIMEOUT' })).toBe(false)
    expect(isExplicitRestoreCancelRejection({ status: 0, errorCode: 'NETWORK.UNAVAILABLE' })).toBe(false)
    expect(isExplicitRestoreCancelRejection({ status: 404, errorCode: 'TASK.NOT_FOUND' })).toBe(false)
  })
})

describe('restore submission batches', () => {
  it('publishes accepted state as soon as each delayed POST resolves', async () => {
    const first = deferred<RestoreCreateResult>()
    const second = deferred<RestoreCreateResult>()
    const accepted = vi.fn()
    const submission = runRestoreJobsSequentially([
      { sourceId: 'agent:1', run: () => first.promise },
      { sourceId: 'agent:2', run: () => second.promise },
    ], accepted)

    first.resolve(restoreResult('task-1'))
    await Promise.resolve()
    await Promise.resolve()
    expect(accepted).toHaveBeenCalledTimes(1)

    second.resolve(restoreResult('task-2', 2))
    await expect(submission).resolves.toMatchObject({
      succeeded: [{ sourceId: 'agent:1' }, { sourceId: 'agent:2' }],
      failed: [],
    })
  })

  it('continues after a failure and reports partial success', async () => {
    const error = new Error('first failed')
    const accepted = vi.fn()
    const result = await runRestoreJobsSequentially([
      { sourceId: 'agent:1', run: async () => { throw error } },
      { sourceId: 'agent:2', run: async () => restoreResult('task-2', 2) },
    ], accepted)

    expect(result.failed).toEqual([{ sourceId: 'agent:1', error }])
    expect(result.succeeded).toHaveLength(1)
    expect(accepted).toHaveBeenCalledWith(expect.objectContaining({ sourceId: 'agent:2' }))
  })

  it('does not submit a second restore after the same source succeeds', async () => {
    const duplicate = vi.fn(async () => restoreResult('task-duplicate', 2))
    const result = await runRestoreJobsSequentially([
      { sourceId: 'agent:1', run: async () => restoreResult('task-1') },
      { sourceId: 'agent:1', run: duplicate },
    ], () => undefined)

    expect(duplicate).not.toHaveBeenCalled()
    expect(result.skippedSourceIds).toEqual(['agent:1'])
  })

  it('keeps all-failure batches distinguishable from accepted work', async () => {
    const result = await runRestoreJobsSequentially([
      { sourceId: 'agent:1', run: async () => { throw new Error('one') } },
      { sourceId: 'agent:2', run: async () => { throw new Error('two') } },
    ], () => undefined)

    expect(result.succeeded).toHaveLength(0)
    expect(result.failed).toHaveLength(2)
  })
})
