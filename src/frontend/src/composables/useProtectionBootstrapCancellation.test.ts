// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useBackupSourcePipeline } from './useBackupSourcePipeline'
import { useProtectionLoadState } from './useProtectionLoadState'
import { usePageRequestScope } from './usePageRequestScope'
import { listBackupSelectableSources } from '../lib/sourceApi'

vi.mock('vue-router', () => ({ onBeforeRouteLeave: vi.fn() }))
vi.mock('../lib/api', () => ({
  isAbortError: (error: { name?: string }) => error?.name === 'AbortError',
  apiErrorMessage: () => '',
}))
vi.mock('../lib/sourceApi', () => ({
  listBackupSelectableSources: vi.fn(),
  setBackupSourcePipelineStep: vi.fn(),
  revertBackupSourcePipelineStep: vi.fn(),
}))

const cleanups = new Set<() => void>()
type SourcePage = Awaited<ReturnType<typeof listBackupSelectableSources>>
function pendingPage() {
  let resolve!: (value: SourcePage) => void
  const promise = new Promise<SourcePage>(done => { resolve = done })
  return { resolve, promise }
}

function flow() {
  let pipeline!: ReturnType<typeof useBackupSourcePipeline>
  let state!: ReturnType<typeof useProtectionLoadState>
  let requests!: ReturnType<typeof usePageRequestScope>
  const wrapper = mount(defineComponent({
    setup() {
      pipeline = useBackupSourcePipeline()
      state = useProtectionLoadState()
      requests = usePageRequestScope()
      return () => null
    },
  }))
  const unmount = () => { cleanups.delete(unmount); wrapper.unmount() }
  cleanups.add(unmount)
  const signals: AbortSignal[] = []
  const resume = vi.fn()
  let busy = false
  async function initialize() {
    if (busy) return
    busy = true
    const scope = 'flow-bootstrap'
    const signal = requests.nextSignal(scope)
    signals.push(signal)
    // Same cancellation/settlement wiring as DataProtection.initializeFlow.
    await state.initialize(
      () => pipeline.bootstrapPipeline({ signal, migrateLegacy: false }),
      failed => {
        if (failed && requests.isCurrentSignal(scope, signal)) requests.abortScope(scope)
        busy = false
        requests.releaseSignal(scope, signal)
      },
    )
    if (!signal.aborted && !state.error.value) resume()
  }
  return {
    pipeline, state, initialize, signals, resume, unmount,
    cancel: () => requests.abortScope('flow-bootstrap'),
    busy: () => busy,
  }
}

beforeEach(() => {
  vi.mocked(listBackupSelectableSources).mockReset()
})
afterEach(() => {
  for (const cleanup of [...cleanups]) cleanup()
})

describe('Protection bootstrap request ownership', () => {
  it('releases a successful round without aborting it', async () => {
    vi.mocked(listBackupSelectableSources).mockImplementation(async params => ({
      count: params?.step === 2 ? 7 : 9, results: [],
    }))
    const page = flow()
    await page.initialize()
    expect(page.signals[0].aborted).toBe(false)
    expect(page.pipeline.pipelineStep2Count.value).toBe(7)
    expect(page.pipeline.pipelineStep3Count.value).toBe(9)
    expect(page.resume).toHaveBeenCalledOnce()
    expect(page.busy()).toBe(false)
  })

  it.each([2, 3])('cancels a failed Step %s round and ignores a late sibling after Retry', async failedStep => {
    const old = pendingPage()
    let retry = false
    vi.mocked(listBackupSelectableSources).mockImplementation(async params => {
      if (retry) return { count: params?.step === 2 ? 7 : 9, results: [] }
      if (params?.step === failedStep) throw { status: 503, message: 'Count unavailable' }
      return old.promise
    })
    const page = flow()
    await page.initialize()
    expect(page.signals[0].aborted).toBe(true)
    expect(page.state.error.value).toMatchObject({ status: 503 })
    expect(page.resume).not.toHaveBeenCalled()
    expect(page.busy()).toBe(false)
    retry = true
    await page.initialize()
    old.resolve({ count: 1, results: [] })
    await flushPromises()
    expect(page.pipeline.pipelineStep2Count.value).toBe(7)
    expect(page.pipeline.pipelineStep3Count.value).toBe(9)
    expect(page.signals[1].aborted).toBe(false)
    expect(page.state.error.value).toBeNull()
    expect(page.resume).toHaveBeenCalledOnce()
  })

  it('keeps only the final successful statistics after repeated failures and retries', async () => {
    const old = [pendingPage(), pendingPage()]
    let round = 0
    vi.mocked(listBackupSelectableSources).mockImplementation(async params => {
      if (round === 2) return { count: params?.step === 2 ? 7 : 9, results: [] }
      if (params?.step === 3) throw { status: 503, message: 'Count unavailable' }
      return old[round].promise
    })
    const page = flow()
    await page.initialize()
    round = 1
    await page.initialize()
    round = 2
    await page.initialize()
    old[1].resolve({ count: 2, results: [] })
    old[0].resolve({ count: 1, results: [] })
    await flushPromises()
    expect(page.signals.map(signal => signal.aborted)).toEqual([true, true, false])
    expect(page.pipeline.pipelineStep2Count.value).toBe(7)
    expect(page.pipeline.pipelineStep3Count.value).toBe(9)
    expect(page.resume).toHaveBeenCalledOnce()
  })

  it.each([503, 403])('preserves the original %s failure when a cancelled sibling rejects with AbortError', async status => {
    let siblingAborted = false
    vi.mocked(listBackupSelectableSources).mockImplementation((params, init) => {
      if (params?.step === 3) return Promise.reject({ status, message: 'Count unavailable' })
      return new Promise((_, reject) => {
        init?.signal?.addEventListener('abort', () => {
          siblingAborted = true
          reject(new DOMException('Aborted', 'AbortError'))
        })
      })
    })
    const page = flow()
    await page.initialize()
    await flushPromises()
    expect(siblingAborted).toBe(true)
    expect(page.state.error.value).toMatchObject({ status })
    expect(page.state.denied.value).toBe(status === 403)
    expect(page.resume).not.toHaveBeenCalled()
  })

  it.each([401, 403])('settles and cancels %s without scheduling an automatic retry', async status => {
    vi.mocked(listBackupSelectableSources).mockRejectedValue({ status, message: 'Access denied' })
    const page = flow()
    await page.initialize()
    await flushPromises()
    expect(page.signals[0].aborted).toBe(true)
    expect(page.state.denied.value).toBe(true)
    expect(listBackupSelectableSources).toHaveBeenCalledTimes(2)
    expect(page.resume).not.toHaveBeenCalled()
    expect(page.busy()).toBe(false)
  })

  it.each(['cancel', 'unmount'] as const)('aborts both pending reads on %s without showing an error', async action => {
    vi.mocked(listBackupSelectableSources).mockImplementation((_, init) => new Promise((_, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))
    const page = flow()
    const pending = page.initialize()
    if (action === 'cancel') page.cancel()
    else page.unmount()
    await pending
    expect(page.signals[0].aborted).toBe(true)
    expect(page.state.error.value).toBeNull()
    expect(page.resume).not.toHaveBeenCalled()
    expect(page.busy()).toBe(false)
  })
})
