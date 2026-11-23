import { flushPromises } from '@vue/test-utils'
import { effectScope, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { listRestoreRecords, type RestoreRecord } from '../../../lib/restoreApi'
import { useRestoreTaskDrawerPage } from './useRestoreTaskDrawerPage'

vi.mock('../../../lib/restoreApi', () => ({ listRestoreRecords: vi.fn() }))

const loadRecords = vi.mocked(listRestoreRecords)
const result = (id: number, count = 1) => ({ count, results: [{ id } as RestoreRecord] })
const source = (id: number) => ({ source_type: 'agent' as const, source_ref_id: id, backup_config_ids: '12' })

function deferredResult() {
  let resolve!: (value: ReturnType<typeof result>) => void
  const promise = new Promise<ReturnType<typeof result>>((done) => { resolve = done })
  return { promise, resolve }
}

function setup() {
  const selected = ref<ReturnType<typeof source> | null>(null)
  const scope = effectScope()
  const running = scope.run(() => useRestoreTaskDrawerPage(selected, 'running', 10))!
  return { selected, running, stop: () => scope.stop() }
}

afterEach(() => loadRecords.mockReset())

describe('restore task drawer running page', () => {
  it('loads filtered pages with loading state and uses the server total', async () => {
    const first = deferredResult()
    const second = deferredResult()
    loadRecords.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const { selected, running, stop } = setup()
    await running.load()
    expect(loadRecords).not.toHaveBeenCalled()
    selected.value = source(1)
    await nextTick()
    expect(running.loading.value).toBe(true)
    expect(loadRecords).toHaveBeenLastCalledWith({ ...source(1), page: 1, page_size: 10, running_only: true }, expect.anything())
    first.resolve(result(12, 11))
    await flushPromises()
    expect(running.loading.value).toBe(false)
    expect(running.total.value).toBe(11)
    running.pager.page = 2
    await nextTick()
    expect(running.loading.value).toBe(true)
    expect(loadRecords).toHaveBeenLastCalledWith({ ...source(1), page: 2, page_size: 10, running_only: true }, expect.anything())
    second.resolve(result(1, 11))
    await flushPromises()
    expect(running.loading.value).toBe(false)
    expect(running.records.value.map((record) => record.id)).toEqual([1])
    stop()
  })

  it('aborts stale requests and allows retry on the new source', async () => {
    const stale = deferredResult()
    loadRecords.mockReturnValueOnce(stale.promise).mockRejectedValueOnce(new Error('Offline')).mockResolvedValueOnce(result(3))
    const { selected, running, stop } = setup()
    selected.value = source(1)
    await nextTick()
    selected.value = source(2)
    await flushPromises()
    expect(loadRecords.mock.calls[0]?.[1]?.signal?.aborted).toBe(true)
    expect(running.error.value).toBe(true)
    expect(running.loading.value).toBe(false)
    await running.load()
    stale.resolve(result(2))
    await flushPromises()
    expect(running.error.value).toBe(false)
    expect(running.records.value.map((record) => record.id)).toEqual([3])
    selected.value = null
    expect(running.records.value).toEqual([])
    stop()
  })
})
