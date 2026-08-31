// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { useBackupSourcePipeline } from './useBackupSourcePipeline'
import {
  listBackupSelectableSources,
  revertBackupSourcePipelineStep,
  setBackupSourcePipelineStep,
} from '../lib/sourceApi'

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

afterEach(() => {
  vi.clearAllMocks()
})

describe('useBackupSourcePipeline', () => {
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

    expect(setBackupSourcePipelineStep).toHaveBeenCalledWith({ ids: ['nas:37'], step: 2 })
  })

  it('normalizes source ids before reverting the pipeline', async () => {
    vi.mocked(revertBackupSourcePipelineStep).mockResolvedValue({ updated: ['agent:27'], target_step: 1 })
    vi.mocked(listBackupSelectableSources).mockResolvedValue({ count: 0, results: [] })

    const pipeline = useBackupSourcePipeline()
    await pipeline.revertPipelineStep(['agent:27', 'agent:27', '', 'invalid'], 1)

    expect(revertBackupSourcePipelineStep).toHaveBeenCalledWith({ ids: ['agent:27'], target_step: 1 })
  })
})
