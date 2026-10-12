// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import ts from 'typescript'
import { describe, expect, it, vi } from 'vitest'
import { sourceUnregisterTaskOutcome } from '../../lib/sourceUnregisterMonitor'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')
const handler = page.slice(page.indexOf('async function onBackupSourcesDeleted('), page.indexOf('function deleteSelectedSourcesFromStep1('))
const executable = ts.transpileModule(handler, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText

function setup(refresh: Promise<void>) {
  const row = { id: 'nas:1', name: 'backup_nfs' }
  const deps = {
    backupSourceDeleteIds: { value: ['nas:1'] },
    backupSourceDeleteRows: { value: [row] },
    backupSourceDeleteShowSnapshots: { value: false },
    backupSourceStep3DeleteDialogOpen: { value: false },
    flowMainStep: { value: 0 },
    flowRowsForSourceIds: () => [row],
    sourceUnregisterTaskBindings: () => [{ sourceId: 'nas:1', taskUuid: 'task-1' }],
    sourceUnregisterPendingKind: () => 'deleting',
    sourcePendingOps: { mark: vi.fn(), clear: vi.fn() },
    notifyUnregisterFailureBatch: vi.fn(),
    monitorPendingUnregister: vi.fn(),
    notifyInfo: vi.fn(),
    refreshPipelineCounts: vi.fn(() => refresh),
    refreshBackupConfigs: vi.fn(() => Promise.resolve()),
    loadBackupSelectable: vi.fn(() => Promise.resolve()),
    showApiError: vi.fn(),
    t: (key: string) => key,
  }
  const run = new Function(...Object.keys(deps), `${executable}; return onBackupSourcesDeleted;`)(...Object.values(deps)) as (payload: unknown) => Promise<void>
  return { deps, run }
}

describe('accepted unregister feedback is independent of refresh', () => {
  it('marks the original row, starts monitoring and notifies before a delayed refresh ends', async () => {
    let resolveRefresh!: () => void
    const refresh = new Promise<void>(resolve => { resolveRefresh = resolve })
    const { deps, run } = setup(refresh)
    const result = run({ accepted: true, result: 'pending', warnings: [] })
    expect(deps.sourcePendingOps.mark).toHaveBeenCalledWith(['nas:1'], expect.objectContaining({ kind: 'deleting', taskUuid: 'task-1' }), [{ id: 'nas:1', name: 'backup_nfs' }])
    expect(deps.monitorPendingUnregister).toHaveBeenCalledWith(['nas:1'], ['task-1'])
    expect(deps.notifyInfo).toHaveBeenCalledOnce()
    expect(deps.loadBackupSelectable).not.toHaveBeenCalled()
    resolveRefresh()
    await result
    expect(deps.loadBackupSelectable).toHaveBeenCalledOnce()
  })

  it('does not discard accepted task state when refreshing fails', async () => {
    const { deps, run } = setup(Promise.reject(new Error('refresh failed')))
    await run({ accepted: true, result: 'pending', warnings: [] })
    expect(deps.sourcePendingOps.clear).not.toHaveBeenCalled()
    expect(deps.monitorPendingUnregister).toHaveBeenCalledOnce()
    expect(deps.showApiError).toHaveBeenCalledWith(expect.any(Error), 'protection.backupsPage.msgDeleteSourceRefreshFailed')
  })

  it('resumes the entire operation when another page already cleared an earlier successful row', async () => {
    const start = page.indexOf('function monitorPendingUnregister(')
    const end = page.indexOf('function resumePendingUnregisterMonitors()', start)
    const monitor = ts.transpileModule(page.slice(start, end), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
    const op = { taskUuid: 'task-2', operationId: 'task-1,task-2', startedAt: 1 }
    const deps = {
      unregisterTaskPollingStopped: false,
      sourcePendingOps: {
        getOp: () => op,
        clear: vi.fn(),
        transitionToRemoving: vi.fn(),
      },
      flowRowsForSourceIds: () => [{ name: 'backup_nfs' }],
      getTask: vi.fn(async (task_uuid: string) => ({ task_uuid, status: 'success', result_payload: { result: 'completed' } })),
      sourceUnregisterTaskOutcome,
      notifyUnregisterTaskSuccess: vi.fn(),
      notifyUnregisterOperation: vi.fn(),
      loadBackupSelectable: vi.fn(async () => {}),
      refreshStep3AfterMoreAction: vi.fn(async () => {}),
      refreshTagFilterOptions: vi.fn(async () => {}),
      t: (key: string) => key,
    }
    const run = new Function(...Object.keys(deps), `${monitor}; return monitorPendingUnregister;`)(...Object.values(deps)) as (ids: string[], tasks: string[], startedAt: number) => void
    run(['nas:2'], ['task-2'], 1)
    await vi.waitFor(() => expect(deps.refreshTagFilterOptions).toHaveBeenCalledOnce())
    expect(deps.getTask).toHaveBeenCalledWith('task-1')
    expect(deps.getTask).toHaveBeenCalledWith('task-2')
    expect(deps.notifyUnregisterTaskSuccess).toHaveBeenCalledWith(expect.objectContaining({
      tasks: [
        expect.objectContaining({ task_uuid: 'task-1' }),
        expect.objectContaining({ task_uuid: 'task-2' }),
      ],
    }))
  })
})
