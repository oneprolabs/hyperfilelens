// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useBackupSourcePipeline } from './useBackupSourcePipeline'
import {
  listBackupSelectableSources,
  revertBackupSourcePipelineStep,
  setBackupSourcePipelineStep,
} from '../lib/sourceApi'
import { clearLegacyStep2Sources, readLegacyRealStep2Sources } from './useDemoFlowStep2Sources'

vi.mock('../lib/sourceApi', () => ({
  listBackupSelectableSources: vi.fn(),
  revertBackupSourcePipelineStep: vi.fn(),
  setBackupSourcePipelineStep: vi.fn(),
}))

vi.mock('./useDemoFlowStep2Sources', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./useDemoFlowStep2Sources')>()
  return {
    ...actual,
    clearLegacyStep2Sources: vi.fn(),
    isBackupSelectableId: vi.fn((id: string) => /^agent:\d+$/.test(id) || /^nas:\d+$/.test(id)),
    readLegacyRealStep2Sources: vi.fn(() => []),
  }
})

beforeEach(() => {
  vi.mocked(listBackupSelectableSources).mockReset()
  vi.mocked(setBackupSourcePipelineStep).mockReset()
  vi.mocked(revertBackupSourcePipelineStep).mockReset()
  vi.mocked(readLegacyRealStep2Sources).mockReturnValue([])
})

afterEach(() => {
  vi.clearAllMocks()
  vi.mocked(readLegacyRealStep2Sources).mockReturnValue([])
})

type SourcePage = Awaited<ReturnType<typeof listBackupSelectableSources>>
function pendingPage() {
  let resolve!: (value: SourcePage) => void
  const promise = new Promise<SourcePage>(done => { resolve = done })
  return { promise, resolve }
}

describe('useBackupSourcePipeline', () => {
  it('bootstraps configuration-only Wizard counts without requesting Source inventory', async () => {
    vi.mocked(listBackupSelectableSources).mockResolvedValue({ count: 5, results: [] })
    const pipeline = useBackupSourcePipeline({ canViewSources: () => false, configurationScope: true })
    await pipeline.bootstrapPipeline({ migrateLegacy: false })
    expect(listBackupSelectableSources).toHaveBeenCalledExactlyOnceWith({
      step: 3, page: 1, page_size: 1, scope: 'backup_configurations',
    }, { signal: undefined })
    expect(pipeline.pipelineStep2Count.value).toBe(0)
    expect(pipeline.pipelineStep3Count.value).toBe(5)
    expect(setBackupSourcePipelineStep).not.toHaveBeenCalled()
  })

  it('uses inventory scope for Step 2 and configuration scope for Step 3 when both grants exist', async () => {
    vi.mocked(listBackupSelectableSources).mockImplementation(async params => ({
      count: params?.step === 2 ? 2 : 3, results: [],
    }))
    const pipeline = useBackupSourcePipeline({ canViewSources: () => true, configurationScope: true })
    await pipeline.refreshPipelineCounts()
    expect(listBackupSelectableSources).toHaveBeenCalledWith({ step: 2, page: 1, page_size: 1 }, { signal: undefined })
    expect(listBackupSelectableSources).toHaveBeenCalledWith({
      step: 3, page: 1, page_size: 1, scope: 'backup_configurations',
    }, { signal: undefined })
    expect(pipeline.pipelineStep2Count.value).toBe(2)
    expect(pipeline.pipelineStep3Count.value).toBe(3)
  })

  it('does not restore inventory counts after Source permission is revoked while a request is pending', async () => {
    const pending = pendingPage()
    let allowed = true
    vi.mocked(listBackupSelectableSources).mockReturnValueOnce(pending.promise)
    const pipeline = useBackupSourcePipeline({ canViewSources: () => allowed, configurationScope: true })
    const request = pipeline.refreshPipelineStep2Count()
    allowed = false
    pending.resolve({ count: 99, results: [] })
    await request
    expect(pipeline.pipelineStep2Count.value).toBe(0)
  })

  it.each([2, 3] as const)('ignores an aborted Step %s result even when the transport completes', async step => {
    const pending = pendingPage()
    vi.mocked(listBackupSelectableSources).mockReturnValueOnce(pending.promise)
    const pipeline = useBackupSourcePipeline()
    const controller = new AbortController()
    const refresh = step === 2 ? pipeline.refreshPipelineStep2Count : pipeline.refreshPipelineStep3Count
    const request = refresh(controller.signal)
    controller.abort()
    pending.resolve({ count: 99, results: [] })
    await request
    expect(pipeline.pipelineStep2Count.value).toBe(0)
    expect(pipeline.pipelineStep3Count.value).toBe(0)
  })

  it.each([2, 3] as const)('keeps the newest Step %s count when unsignalled responses arrive out of order', async step => {
    const older = pendingPage()
    const newer = pendingPage()
    vi.mocked(listBackupSelectableSources)
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise)
    const pipeline = useBackupSourcePipeline()
    const refresh = step === 2 ? pipeline.refreshPipelineStep2Count : pipeline.refreshPipelineStep3Count
    const first = refresh()
    const second = refresh()
    newer.resolve({ count: 7, results: [] })
    await second
    older.resolve({ count: 1, results: [] })
    await first
    expect(step === 2 ? pipeline.pipelineStep2Count.value : pipeline.pipelineStep3Count.value).toBe(7)
  })

  it('does not start an already cancelled bootstrap or discard legacy migration state', async () => {
    const pipeline = useBackupSourcePipeline()
    const controller = new AbortController()
    controller.abort()
    await pipeline.bootstrapPipeline({ signal: controller.signal })
    expect(listBackupSelectableSources).not.toHaveBeenCalled()
    expect(setBackupSourcePipelineStep).not.toHaveBeenCalled()
    expect(clearLegacyStep2Sources).not.toHaveBeenCalled()
  })

  it('passes one lifecycle signal through legacy writes and both count refreshes', async () => {
    vi.mocked(readLegacyRealStep2Sources).mockReturnValue(['agent:7'])
    vi.mocked(setBackupSourcePipelineStep).mockResolvedValue({ updated: ['agent:7'], step: 2 })
    vi.mocked(listBackupSelectableSources).mockResolvedValue({ count: 2, results: [] })
    const controller = new AbortController()
    await useBackupSourcePipeline().bootstrapPipeline({ signal: controller.signal })
    expect(setBackupSourcePipelineStep).toHaveBeenCalledWith(
      { ids: ['agent:7'], step: 2 }, { signal: controller.signal },
    )
    expect(listBackupSelectableSources).toHaveBeenCalledTimes(4)
    expect(vi.mocked(listBackupSelectableSources).mock.calls.every(([, init]) => init?.signal === controller.signal)).toBe(true)
    expect(clearLegacyStep2Sources).toHaveBeenCalledOnce()
  })

  it('keeps cancelled migration state retryable and does not start counts after a late write result', async () => {
    vi.mocked(readLegacyRealStep2Sources).mockReturnValue(['agent:7'])
    const controller = new AbortController()
    vi.mocked(setBackupSourcePipelineStep).mockImplementationOnce(async () => {
      controller.abort()
      return { updated: ['agent:7'], step: 2 }
    })
    const pipeline = useBackupSourcePipeline()
    await pipeline.bootstrapPipeline({ signal: controller.signal })
    expect(listBackupSelectableSources).not.toHaveBeenCalled()
    expect(clearLegacyStep2Sources).not.toHaveBeenCalled()

    vi.mocked(setBackupSourcePipelineStep).mockResolvedValue({ updated: ['agent:7'], step: 2 })
    vi.mocked(listBackupSelectableSources).mockResolvedValue({ count: 2, results: [] })
    const retry = new AbortController()
    await pipeline.bootstrapPipeline({ signal: retry.signal })
    expect(clearLegacyStep2Sources).toHaveBeenCalledOnce()
    expect(pipeline.pipelineStep2Count.value).toBe(2)
    expect(pipeline.pipelineStep3Count.value).toBe(2)
  })
  it('does not write legacy source state when opening the Wizard read-only', async () => {
    vi.mocked(readLegacyRealStep2Sources).mockReturnValue(['agent:7'])
    vi.mocked(listBackupSelectableSources).mockResolvedValue({ count: 0, results: [] })
    const controller = new AbortController()
    await useBackupSourcePipeline().bootstrapPipeline({ migrateLegacy: false, signal: controller.signal })
    expect(setBackupSourcePipelineStep).not.toHaveBeenCalled()
    expect(clearLegacyStep2Sources).not.toHaveBeenCalled()
    expect(listBackupSelectableSources).toHaveBeenCalledTimes(2)
    expect(vi.mocked(listBackupSelectableSources).mock.calls.every(([, init]) => init?.signal === controller.signal)).toBe(true)
  })

  it('does not let a stale legacy write 403 block authorized reads', async () => {
    vi.mocked(readLegacyRealStep2Sources).mockReturnValue(['agent:7'])
    vi.mocked(setBackupSourcePipelineStep).mockRejectedValue({ status: 403, code: 'AUTH.FORBIDDEN' })
    vi.mocked(listBackupSelectableSources).mockResolvedValue({ count: 2, results: [] })
    const pipeline = useBackupSourcePipeline()
    await pipeline.bootstrapPipeline()
    expect(clearLegacyStep2Sources).toHaveBeenCalledOnce()
    expect(pipeline.pipelineStep2Count.value).toBe(2)
    expect(pipeline.pipelineStep3Count.value).toBe(2)
  })

  it('propagates count authorization failures for the page to settle without retries', async () => {
    vi.mocked(listBackupSelectableSources).mockRejectedValue({ status: 403, code: 'AUTH.FORBIDDEN' })
    await expect(useBackupSourcePipeline().bootstrapPipeline()).rejects.toMatchObject({ status: 403 })
    expect(listBackupSelectableSources).toHaveBeenCalledTimes(2)
  })
  it('loads step 2 and step 3 totals without filtering step 3 by online status', async () => {
    vi.mocked(listBackupSelectableSources).mockImplementation(async (params) => ({
      count: params?.step === 2 ? 4 : 7,
      results: [],
    }))

    const pipeline = useBackupSourcePipeline()
    await pipeline.bootstrapPipeline()

    expect(listBackupSelectableSources).toHaveBeenCalledTimes(2)
    expect(listBackupSelectableSources).toHaveBeenCalledWith(
      { step: 2, page: 1, page_size: 1 },
      { signal: undefined },
    )
    expect(listBackupSelectableSources).toHaveBeenCalledWith(
      { step: 3, page: 1, page_size: 1 },
      { signal: undefined },
    )
    expect(pipeline.pipelineStep2Count.value).toBe(4)
    expect(pipeline.pipelineStep3Count.value).toBe(7)
  })

  it('normalizes source ids before advancing the pipeline', async () => {
    vi.mocked(setBackupSourcePipelineStep).mockResolvedValue({ updated: ['nas:37'], step: 2 })
    vi.mocked(listBackupSelectableSources).mockResolvedValue({ count: 0, results: [] })

    const pipeline = useBackupSourcePipeline()
    await pipeline.setPipelineStep(['nas:37', 'nas:37', '', 'invalid'], 2)

    expect(setBackupSourcePipelineStep).toHaveBeenCalledWith({ ids: ['nas:37'], step: 2 }, undefined)
  })

  it('normalizes source ids before reverting the pipeline', async () => {
    vi.mocked(revertBackupSourcePipelineStep).mockResolvedValue({ updated: ['agent:27'], target_step: 1 })
    vi.mocked(listBackupSelectableSources).mockResolvedValue({ count: 0, results: [] })

    const pipeline = useBackupSourcePipeline()
    await pipeline.revertPipelineStep(['agent:27', 'agent:27', '', 'invalid'], 1)

    expect(revertBackupSourcePipelineStep).toHaveBeenCalledWith({ ids: ['agent:27'], target_step: 1 })
  })
})
