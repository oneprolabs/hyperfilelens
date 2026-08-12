// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from 'vitest'
import {
  markWizardPendingBySourceIds,
  readWizardPendingSourceOps,
  WIZARD_PENDING_STORAGE_KEY,
} from './backupWizardPendingStorage'
import { useBackupWizardSourcePendingOps } from './useBackupWizardSourcePendingOps'

describe('backup wizard pending source storage', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
  })

  it('persists the parent unregister task needed to resume reconciliation', () => {
    markWizardPendingBySourceIds(['nas:42'], {
      kind: 'deleting',
      taskId: 17,
      taskUuid: 'task-uuid-17',
      startedAt: 1234,
    })

    expect(readWizardPendingSourceOps().get('nas:42')).toEqual({
      kind: 'deleting',
      taskId: 17,
      taskUuid: 'task-uuid-17',
      startedAt: 1234,
    })
    expect(window.sessionStorage.getItem(WIZARD_PENDING_STORAGE_KEY)).toContain('task-uuid-17')
  })

  it('allows a terminal asynchronous failure to replace deleting state', () => {
    markWizardPendingBySourceIds(['agent:9'], {
      kind: 'deleting',
      taskUuid: 'task-uuid-9',
    })
    markWizardPendingBySourceIds(['agent:9'], {
      kind: 'delete_failed',
      taskUuid: 'task-uuid-9',
    })

    expect(readWizardPendingSourceOps().get('agent:9')?.kind).toBe('delete_failed')
  })

  it('persists structured failure details across refresh', () => {
    markWizardPendingBySourceIds(['agent:9'], {
      kind: 'delete_failed',
      taskUuid: 'task-uuid-9',
      errorMessage: 'Agent uninstall callback failed',
      failureDetails: {
        title: 'Source deregistration failed',
        summary: 'Deregistration failed for agent-9.',
        reasons: ['Agent uninstall callback failed'],
        resolutions: ['Retry Strict Cleanup'],
        rawDetail: { task_uuid: 'task-uuid-9' },
      },
    })

    const op = readWizardPendingSourceOps().get('agent:9')
    expect(op?.failureDetails?.rawDetail).toEqual({ task_uuid: 'task-uuid-9' })
    expect(op?.failureDetails?.reasons).toEqual(['Agent uninstall callback failed'])
  })

  it('reconciles a failed task whose terminal details were not persisted', () => {
    markWizardPendingBySourceIds(['nas:42'], {
      kind: 'delete_failed',
      taskUuid: 'task-uuid-42',
      startedAt: 1234,
    })

    const pending = useBackupWizardSourcePendingOps({
      t: ((key: string) => key) as never,
    })

    expect(pending.pendingDeleteTasks()).toEqual([{
      sourceId: 'nas:42',
      taskUuid: 'task-uuid-42',
      startedAt: 1234,
    }])
  })
})
