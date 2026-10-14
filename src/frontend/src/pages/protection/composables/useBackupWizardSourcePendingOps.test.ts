// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { useBackupWizardSourcePendingOps } from './useBackupWizardSourcePendingOps'
import type { FlowSourceRow } from './useFlowSourceAggregate'

const t = ((key: string) => key) as never
const row: FlowSourceRow = {
  id: 'nas:1', name: 'backup_nfs', hostname: '', nodeName: '', nodeIp: '',
  status: 'active', registeredAt: '', type: 'nas',
}
beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
})

describe('unregister receipts survive without changing server pages', () => {
  it.each(['deleting', 'delete_waiting', 'delete_blocked', 'delete_failed'] as const)(
    'retains the %s receipt, never an inventory row, across reload',
    kind => {
      const pending = useBackupWizardSourcePendingOps({ t })
      pending.mark([row.id], { kind, taskUuid: 'task-1' }, [row])
      pending.reconcileWithCatalog(new Set(), new Set())
      expect(pending.getOp(row.id)?.taskUuid).toBe('task-1')
      expect(pending).not.toHaveProperty('injectPendingRows')
      const restored = useBackupWizardSourcePendingOps({ t })
      expect(restored.getOp(row.id)?.taskUuid).toBe('task-1')
      expect(restored.pendingDeleteTasks()).toHaveLength(1)
      expect(restored).not.toHaveProperty('injectPendingRows')
      restored.clear([row.id])
      expect(restored.isPending(row.id)).toBe(false)
    },
  )

  it('shows processing feedback and prevents a duplicate submission', () => {
    const pending = useBackupWizardSourcePendingOps({ t })
    pending.mark([row.id], { kind: 'deleting', taskUuid: 'task-1' }, [row])
    expect(pending.rowPendingLabel(row.id)).toBe('protection.backupsPage.sourcePendingDeleting')
    expect(pending.rowPendingSpinning(row.id)).toBe(true)
    expect(pending.isRowSelectable(row.id)).toBe(false)
  })

  it('does not restore unrelated reverted rows that are no longer in the catalog', () => {
    const pending = useBackupWizardSourcePendingOps({ t })
    pending.mark([row.id], { kind: 'reverting' }, [row])
    pending.reconcileWithCatalog(new Set(), new Set())
    expect(pending.isPending(row.id)).toBe(false)
  })

  it('does not enable duplicate deletion when only monitoring is unavailable', () => {
    const pending = useBackupWizardSourcePendingOps({ t })
    pending.mark([row.id], {
      kind: 'delete_failed', taskUuid: 'task-1',
      failureDetails: {
        title: 'Monitoring unavailable',
        summary: 'Monitoring unavailable', errorCode: 'TASK_MONITOR_UNAVAILABLE',
      },
    }, [row])
    expect(pending.isRowSelectable(row.id)).toBe(false)
    expect(pending.pendingDeleteTasks()).toHaveLength(1)
  })
})
