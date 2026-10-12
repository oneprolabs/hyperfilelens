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

describe('unregister rows survive catalog refresh until task reconciliation', () => {
  it.each(['deleting', 'delete_waiting', 'delete_blocked', 'delete_failed'] as const)(
    'retains the %s row and its task across a missing catalog row and reload',
    kind => {
      const pending = useBackupWizardSourcePendingOps({ t })
      pending.mark([row.id], { kind, taskUuid: 'task-1' }, [row])
      pending.reconcileWithCatalog(new Set(), new Set())
      expect(pending.injectPendingRows([])).toEqual([row])
      expect(pending.injectPendingRows([row])).toEqual([row])
      const restored = useBackupWizardSourcePendingOps({ t })
      expect(restored.pendingDeleteTasks()).toHaveLength(1)
      expect(restored.injectPendingRows([])).toEqual([row])
      restored.clear([row.id])
      expect(restored.injectPendingRows([])).toEqual([])
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
    expect(pending.injectPendingRows([])).toEqual([])
    pending.reconcileWithCatalog(new Set(), new Set())
    expect(pending.isPending(row.id)).toBe(false)
  })
})
