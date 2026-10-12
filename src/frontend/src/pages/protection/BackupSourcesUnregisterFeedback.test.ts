// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import ts from 'typescript'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import { en } from '../../locales/en'
import { sourceUnregisterPendingKind, sourceUnregisterTaskOutcome } from '../../lib/sourceUnregisterMonitor'
import { notifyUnregisterTaskSuccess } from '../../lib/sourceUnregisterFeedback'
import { resetToastStoreForTests, toastState } from '../../lib/toast/store'
import type { SourcePendingOp } from './composables/backupWizardPendingStorage'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupSources.vue'), 'utf8')
const handler = page.slice(page.indexOf('async function reconcilePendingSourceDeleteTasks()'), page.indexOf('type SourcePendingDisplayStatus'))
const executable = ts.transpileModule(handler, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const t = createI18n({ legacy: false, locale: 'en', messages: { en } }).global.t

beforeEach(() => {
  resetToastStoreForTests()
  sessionStorage.clear()
  localStorage.clear()
})

function setup() {
  const ops = new Map<string, SourcePendingOp>([
    ['nas:1', { kind: 'deleting', taskUuid: 'task-1', operationId: 'task-1,task-2', startedAt: Date.now() }],
    ['nas:2', { kind: 'deleting', taskUuid: 'task-2', operationId: 'task-1,task-2', startedAt: Date.now() }],
  ])
  const statuses = new Map([['task-1', 'success'], ['task-2', 'running']])
  const deps = {
    sourceDeleteTaskPollInFlight: false,
    pendingSourceOps: { value: ops },
    refreshPendingSourceOps: vi.fn(),
    sourceDeleteTaskNeedsReconcile: (op: SourcePendingOp) => Boolean(op.taskUuid),
    getTask: vi.fn(async (uuid: string) => ({ task_uuid: uuid, status: statuses.get(uuid), result_payload: { result: 'completed' } })),
    readWizardPendingSourceOps: () => ops,
    sourcePendingDisplayName: (id: string) => id,
    SOURCE_DELETE_TASK_TIMEOUT_MS: 900000,
    SOURCE_DELETE_BLOCKED_TASK_POLL_MS: 30000,
    SOURCE_DELETE_TASK_POLL_MS: 2000,
    sourceUnregisterTaskOutcome,
    sourceUnregisterPendingKind,
    markWizardPendingBySourceIds: (ids: string[], op: SourcePendingOp) => ids.forEach(id => ops.set(id, op)),
    clearWizardPendingBySourceIds: (ids: string[]) => ids.forEach(id => ops.delete(id)),
    notifyUnregisterOperation: vi.fn(),
    notifyUnregisterTaskSuccess,
    load: vi.fn(async () => {}),
    schedulePendingSourceDeletePoll: vi.fn(),
    t,
  }
  const run = new Function(...Object.keys(deps), `${executable}; return reconcilePendingSourceDeleteTasks;`)(...Object.values(deps)) as () => Promise<void>
  return { ops, statuses, deps, run }
}

describe('source list asynchronous batch completion', () => {
  it('includes targets that succeeded on an earlier poll in the final success result', async () => {
    const { ops, statuses, deps, run } = setup()
    await run()
    expect(ops.has('nas:1')).toBe(false)
    expect(ops.has('nas:2')).toBe(true)
    expect(toastState.items).toHaveLength(0)
    statuses.set('task-2', 'success')
    await run()
    expect(deps.getTask).toHaveBeenCalledTimes(4)
    expect(ops.size).toBe(0)
    expect(toastState.items).toHaveLength(1)
    expect(toastState.items[0].message).toBe('2 backup source(s) deregistered.')
    await run()
    expect(toastState.items).toHaveLength(1)
  })

  it('does not announce completion when an earlier target is no longer observable', async () => {
    const { statuses, deps, run } = setup()
    await run()
    statuses.set('task-2', 'success')
    deps.getTask.mockImplementation(async uuid => {
      if (uuid === 'task-1') throw new Error('task unavailable')
      return { task_uuid: uuid, status: 'success', result_payload: { result: 'completed' } }
    })
    await run()
    expect(toastState.items).toHaveLength(0)
  })

  it('does not announce full success for an operation containing rejected targets', async () => {
    const { ops, statuses, run } = setup()
    ops.forEach(op => { op.operationHasRejections = true })
    statuses.set('task-2', 'success')
    await run()
    expect(toastState.items).toHaveLength(0)
  })

  it('ignores a stale success if another submission replaced the pending task', async () => {
    const { ops, deps, run } = setup()
    deps.getTask.mockImplementation(async uuid => {
      ops.set('nas:1', { kind: 'deleting', taskUuid: 'new-task', startedAt: Date.now() })
      return { task_uuid: uuid, status: 'success', result_payload: { result: 'completed' } }
    })
    await run()
    expect(toastState.items).toHaveLength(0)
    expect(ops.get('nas:1')?.taskUuid).toBe('new-task')
  })
})
