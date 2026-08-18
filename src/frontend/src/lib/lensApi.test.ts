// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import type { LensIngestPolicy } from './lensApi'
import {
  browseCopilotSnapshotDirectory,
  createKnowledgeSource,
  patchKnowledgeSource,
  setLensApiScope,
  setLensDefaultAgentModel,
  setLensDefaultMultimodalModel,
  testSavedLensModel,
} from './lensApi'

const ingestPolicy: LensIngestPolicy = {
  document: true,
  embedded_image: true,
  image: true,
  document_model_ref: 'document-model',
  vision_model_ref: 'vision-model',
  max_images: 20,
  max_file_size_mb: 100,
  max_pages: 200,
  pdf_extract_images: true,
  pdf_extract_images_on_text_pages: false,
  pdf_render_scanned_pages: true,
  pdf_max_pages: 200,
  pdf_max_images_per_page: 10,
  pdf_render_dpi: 144,
  pdf_min_text_chars: 80,
  pdf_min_image_area_ratio: 0.1,
}

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return {
    ...actual,
    api: vi.fn(),
  }
})

vi.mock('../composables/useAuth', () => ({
  getEffectiveOrgKey: vi.fn(() => 'tenant-a'),
}))

afterEach(() => {
  setLensApiScope('tenant')
  vi.clearAllMocks()
})

describe('Insight snapshot browsing', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('polls the bounded Insight task and returns its result', async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({ task_id: 'browse-1', status: 'pending' })
      .mockResolvedValueOnce({
        task_id: 'browse-1',
        status: 'success',
        has_more: true,
        entries: [{ name: 'reports', path: 'reports', type: 'dir' }],
      })

    const request = browseCopilotSnapshotDirectory(31, {
      backupSourceSnapshotId: 71,
      gatewayLinkId: 17,
      path: 'docs',
      limit: 10,
    })
    await vi.runAllTimersAsync()

    await expect(request).resolves.toMatchObject({
      has_more: true,
      entries: [{ path: 'reports' }],
    })
    expect(api).toHaveBeenNthCalledWith(
      1,
      '/api/v1/lens/copilot/snapshot-browse/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          directory_id: 31,
          backup_source_snapshot_id: 71,
          gateway_link_id: 17,
          path: 'docs',
          limit: 10,
        }),
      }),
    )
  })

  it('stops polling a task that never reaches a terminal state', async () => {
    vi.mocked(api).mockResolvedValue({ task_id: 'browse-stuck', status: 'pending' })

    const request = browseCopilotSnapshotDirectory(31, {
      backupSourceSnapshotId: 71,
      gatewayLinkId: 17,
    })
    const rejection = expect(request).rejects.toThrow('Snapshot browsing timed out')
    await vi.runAllTimersAsync()

    await rejection
    expect(api).toHaveBeenCalledTimes(241)
  })

  it('honors a signal that is already aborted before polling', async () => {
    vi.mocked(api).mockResolvedValue({ task_id: 'browse-1', status: 'pending' })
    const controller = new AbortController()
    controller.abort()

    await expect(
      browseCopilotSnapshotDirectory(31, {
        backupSourceSnapshotId: 71,
        gatewayLinkId: 17,
      }, controller.signal),
    ).rejects.toMatchObject({ name: 'AbortError' })
    expect(api).toHaveBeenCalledTimes(1)
  })
})

describe('saved AI model connectivity', () => {
  it('uses the Admin Console test-call route without sending credentials', async () => {
    setLensApiScope('platform')
    vi.mocked(api).mockResolvedValue({ ok: true })

    await testSavedLensModel('model-uuid')

    expect(api).toHaveBeenCalledWith(
      '/api/v1/platform-ops/lens/models/model-uuid/test-call',
      expect.objectContaining({
        method: 'POST',
        body: '{}',
      }),
    )
  })
})

describe('AI model role defaults', () => {
  it('updates the tenant Agent default through org settings', async () => {
    vi.mocked(api).mockResolvedValue({
      default_agent_model_ref: 'agent-uuid',
      default_multimodal_model_ref: null,
    })

    await setLensDefaultAgentModel('agent-uuid')

    expect(api).toHaveBeenCalledWith(
      '/api/v1/lens/settings/',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ default_agent_model_ref: 'agent-uuid' }),
      }),
    )
  })

  it('updates the platform multimodal default through Admin Console settings', async () => {
    setLensApiScope('platform')
    vi.mocked(api).mockResolvedValue({
      default_agent_model_ref: null,
      default_multimodal_model_ref: 'multimodal-uuid',
    })

    await setLensDefaultMultimodalModel('multimodal-uuid')

    expect(api).toHaveBeenCalledWith(
      '/api/v1/platform-ops/lens/settings',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({
          default_multimodal_model_ref: 'multimodal-uuid',
        }),
      }),
    )
  })
})

describe('knowledge source ingest policy', () => {
  it.each([
    ['create', () => createKnowledgeSource({
      name: 'Documents',
      gateway: 42,
      source_path: '/documents',
      ingest_policy: ingestPolicy,
    })],
    ['patch', () => patchKnowledgeSource(7, { ingest_policy: ingestPolicy })],
  ])('keeps deployment-owned model references out of %s requests', async (_operation, request) => {
    vi.mocked(api).mockResolvedValue({ id: 7 })

    await request()

    const options = vi.mocked(api).mock.calls[0]?.[1]
    const body = JSON.parse(String(options?.body))
    expect(body.ingest_policy).not.toHaveProperty('document_model_ref')
    expect(body.ingest_policy).not.toHaveProperty('vision_model_ref')
    expect(body.ingest_policy.document).toBe(true)
    expect(ingestPolicy.document_model_ref).toBe('document-model')
    expect(ingestPolicy.vision_model_ref).toBe('vision-model')
  })
})
