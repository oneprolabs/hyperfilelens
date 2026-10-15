// @vitest-environment jsdom
import { defineComponent, ref, nextTick } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { latestUnregisterFailure } from '../lib/sourceFailureDiagnostics'
import type { TaskRow } from '../lib/taskApi'
import { useUnregisterFailureHistory } from './useUnregisterFailureHistory'

vi.mock('../lib/sourceFailureDiagnostics', () => ({ latestUnregisterFailure: vi.fn() }))
const t = ((key: string) => key) as never
const task = (uuid = 'task-55') => ({
  task_uuid: uuid, task_type: 'source_unregister', status: 'failed',
  error_message: 'Uninstall failed',
}) as TaskRow

function setup() {
  const visible = ref(false)
  const ids = ref(['agent:55'])
  let history!: ReturnType<typeof useUnregisterFailureHistory>
  const wrapper = mount(defineComponent({
    setup() {
      history = useUnregisterFailureHistory(t, visible, ids, id => `source-${id}`)
      return () => null
    },
  }))
  return { wrapper, visible, ids, get history() { return history } }
}

beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  sessionStorage.clear()
})

describe('read-only unregister history', () => {
  it('loads persisted task diagnostics every time the dialog is opened', async () => {
    vi.mocked(latestUnregisterFailure).mockResolvedValue(task())
    const state = setup()
    expect(latestUnregisterFailure).not.toHaveBeenCalled()
    state.visible.value = true
    await flushPromises()
    expect(state.history.details.value?.taskUuid).toBe('task-55')
    expect(sessionStorage.length).toBe(0)
    state.visible.value = false
    await nextTick()
    expect(state.history.details.value).toBeNull()
    state.visible.value = true
    await flushPromises()
    expect(latestUnregisterFailure).toHaveBeenCalledTimes(2)
    state.wrapper.unmount()
  })

  it('merges only selected sources and bounds request concurrency', async () => {
    const resolvers: Array<(task: TaskRow) => void> = []
    vi.mocked(latestUnregisterFailure).mockImplementation(() => new Promise(resolve => resolvers.push(resolve)))
    const state = setup()
    state.ids.value = Array.from({ length: 6 }, (_, i) => `agent:${55 + i}`)
    state.visible.value = true
    await nextTick()
    expect(latestUnregisterFailure).toHaveBeenCalledTimes(4)
    resolvers.splice(0).forEach(resolve => resolve(task()))
    await flushPromises()
    expect(latestUnregisterFailure).toHaveBeenCalledTimes(6)
    resolvers.splice(0).forEach(resolve => resolve(task()))
    await flushPromises()
    expect(state.history.details.value?.entities).toHaveLength(6)
    state.wrapper.unmount()
  })

  it('discards late results after closing or changing the selection', async () => {
    let resolveOld!: (value: TaskRow) => void
    vi.mocked(latestUnregisterFailure).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    const state = setup()
    state.visible.value = true
    await nextTick()
    const signal = vi.mocked(latestUnregisterFailure).mock.calls[0]![1]!.signal!
    state.ids.value = ['nas:77']
    vi.mocked(latestUnregisterFailure).mockResolvedValue(task('task-77'))
    await flushPromises()
    expect(signal.aborted).toBe(true)
    resolveOld(task('old-task'))
    await flushPromises()
    expect(state.history.details.value?.taskUuid).toBe('task-77')
    state.wrapper.unmount()
  })

  it('does not publish responses from another organization', async () => {
    let resolve!: (value: TaskRow) => void
    localStorage.setItem('hfl_org_key', 'org-a')
    vi.mocked(latestUnregisterFailure).mockImplementation(() => new Promise(done => { resolve = done }))
    const state = setup()
    state.visible.value = true
    await nextTick()
    localStorage.setItem('hfl_org_key', 'org-b')
    resolve(task())
    await flushPromises()
    expect(state.history.details.value).toBeNull()
    state.wrapper.unmount()
  })

  it('distinguishes load failure from no history and supports read-only reload', async () => {
    vi.mocked(latestUnregisterFailure).mockRejectedValue(new Error('Forbidden'))
    const state = setup()
    state.visible.value = true
    await flushPromises()
    expect(state.history.failed.value).toBe(true)
    expect(state.history.loading.value).toBe(false)
    vi.mocked(latestUnregisterFailure).mockResolvedValue(null)
    state.history.reload()
    await flushPromises()
    expect(state.history.failed.value).toBe(false)
    expect(state.history.details.value).toBeNull()
    state.wrapper.unmount()
  })
})
