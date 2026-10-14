// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import ts from 'typescript'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { sourceUnregisterPendingKind, sourceUnregisterTaskOutcome } from '../../lib/sourceUnregisterMonitor'
import { useBackupWizardSourcePendingOps } from './composables/useBackupWizardSourcePendingOps'
import { computed, ref } from 'vue'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')
const start = page.indexOf('function monitorPendingUnregister(')
const end = page.indexOf('function resumePendingUnregisterMonitors()', start)
const monitor = ts.transpileModule(page.slice(start, end), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const t = ((key: string) => key) as never
function setup() {
  const pending = useBackupWizardSourcePendingOps({ t })
  for (const [id, uuid] of [['agent:11', 'one'], ['agent:26', 'two'], ['agent:27', 'three']]) {
    pending.mark([id], { kind: 'deleting', taskUuid: uuid, startedAt: Date.now(), operationId: 'one,two,three' })
  }
  const deps = {
    unregisterTaskPollingStopped: false, sourcePendingOps: pending,
    flowRowsForSourceIds: (ids: string[]) => ids.map(id => ({ id, name: id })),
    getTask: vi.fn(async (task_uuid: string) => ({ id: 1, task_uuid, status: 'success' })),
    sourceUnregisterPendingKind, sourceUnregisterTaskOutcome,
    unregisterFailureToErrorDetails: vi.fn(() => ({ summary: 'Warning' })),
    unregisterFailureSummaryLine: () => 'Warning',
    notifyUnregisterTaskSuccess: vi.fn(), notifyUnregisterOperation: vi.fn(),
    loadBackupSelectable: vi.fn(async () => {}),
    refreshStep3AfterMoreAction: vi.fn(async () => {}), refreshTagFilterOptions: vi.fn(async () => {}),
    pageRequests: { isAbortError: () => false }, showApiError: vi.fn(),
    UNREGISTER_TASK_POLL_TIMEOUT_MS: 900_000, UNREGISTER_BLOCKED_TASK_POLL_MS: 30_000,
    UNREGISTER_TASK_POLL_MS: 2000, unregisterTaskPollTimers: new Set<number>(), t,
  }
  const run = new Function(...Object.keys(deps), `${monitor}; return monitorPendingUnregister;`)(...Object.values(deps))
  return { deps, pending, run: () => run(['agent:11', 'agent:26', 'agent:27'], ['one', 'two', 'three']) }
}
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

describe('independent source unregister monitoring', () => {
  it('clears terminal siblings immediately and never marks them deleting again', async () => {
    const { deps, pending, run } = setup()
    deps.getTask.mockImplementation(async task_uuid => ({ id: 1, task_uuid, status: task_uuid === 'two' ? 'running' : 'success' }))
    run()
    await vi.advanceTimersByTimeAsync(0)
    expect(pending.isPending('agent:11')).toBe(false)
    expect(pending.isPending('agent:27')).toBe(false)
    expect(pending.getOp('agent:26')?.kind).toBe('deleting')
    expect(deps.refreshTagFilterOptions).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(2000)
    expect(deps.getTask.mock.calls.map(args => args[0])).toEqual(['one', 'two', 'three', 'two'])
    expect(pending.isPending('agent:11')).toBe(false)
    expect(deps.notifyUnregisterTaskSuccess).not.toHaveBeenCalled()
  })
  it('does not let an unreadable task suppress successful or failed siblings', async () => {
    const { deps, pending, run } = setup()
    deps.getTask.mockImplementation(async task_uuid => {
      if (task_uuid === 'one') throw new Error('404')
      return { id: 1, task_uuid, status: task_uuid === 'two' ? 'success' : 'failed' }
    })
    run()
    await vi.advanceTimersByTimeAsync(0)
    expect(pending.isPending('agent:26')).toBe(false)
    expect(pending.getOp('agent:27')?.kind).toBe('delete_failed')
    expect(pending.getOp('agent:11')?.kind).toBe('deleting')
    expect(deps.refreshTagFilterOptions).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(deps.getTask.mock.calls.map(args => args[0])).toEqual(['one', 'two', 'three', 'one'])
  })
  it('never overwrites a replacement operation from a late response', async () => {
    const { deps, pending, run } = setup()
    let resolve!: (value: { id: number; task_uuid: string; status: string }) => void
    deps.getTask.mockImplementation(task_uuid => task_uuid === 'one'
      ? new Promise(done => { resolve = done }) : Promise.resolve({ id: 1, task_uuid, status: 'success' }))
    run()
    pending.mark(['agent:11'], { kind: 'deleting', taskUuid: 'replacement', startedAt: Date.now() + 1 })
    resolve({ id: 1, task_uuid: 'one', status: 'success' })
    await vi.advanceTimersByTimeAsync(0)
    expect(pending.getOp('agent:11')?.taskUuid).toBe('replacement')
  })

  it('keeps monitoring failures distinct and clears them when task reads recover', async () => {
    const { deps, pending, run } = setup()
    deps.getTask.mockImplementation(async task_uuid => {
      if (task_uuid === 'one') throw new Error('unavailable')
      return { id: 1, task_uuid, status: 'success' }
    })
    run()
    await vi.advanceTimersByTimeAsync(0)
    vi.setSystemTime(Date.now() + 900_001)
    await vi.advanceTimersByTimeAsync(30_000)
    expect(pending.getOp('agent:11')?.failureDetails?.errorCode).toBe('TASK_MONITOR_UNAVAILABLE')
    expect(pending.isPending('agent:26')).toBe(false)
    deps.getTask.mockImplementation(async task_uuid => ({ id: 1, task_uuid, status: 'running' }))
    await vi.advanceTimersByTimeAsync(30_000)
    expect(pending.getOp('agent:11')?.kind).toBe('deleting')
    expect(pending.getOp('agent:11')?.failureDetails).toBeUndefined()
  })
})

describe('Wizard server-page authority', () => {
  it.each([0, 1, 2])('never inserts cached removals into step %s or changes its total', step => {
    const first = page.indexOf('const filteredBackupSelectableRows')
    const last = page.indexOf('const paginatedStep2SourceList', first)
    const code = ts.transpileModule(page.slice(first, last), {
      compilerOptions: { target: ts.ScriptTarget.ES2022 },
    }).outputText
    const rows = step === 2 ? [{ id: 'agent:36', name: 'zjb' }] : []
    const deps = {
      computed,
      flowMainStep: ref(step),
      backupSelectableRows: ref(rows), backupSelectableCount: ref(51),
      step2SelectableRows: ref(rows), step2PendingSourceList: ref(rows),
      step3SourceList: ref(rows), debouncedTaskSearchQuery: ref(''),
      sourcePendingOps: { injectPendingRows: () => { throw new Error('cached rows must not enter a page') } },
      flowCommonFiltersMatch: () => { throw new Error('a server page must not be filtered again') },
      flowTaskSearchMatches: () => true, flowStep3FiltersMatch: () => true,
    }
    const result = new Function(...Object.keys(deps), `${code};
      return {
        rows: [filteredBackupSelectableRows, filteredStep2SourceList, filteredStep3SourceList][flowMainStep.value].value,
        total: flowStep0DisplayTotal.value,
      };`)(...Object.values(deps))
    expect(result.rows).toEqual(rows)
    expect(result.total).toBe(51)
  })
})
