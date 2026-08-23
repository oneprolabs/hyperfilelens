// @vitest-environment jsdom

import { computed, effectScope, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  cancelCopilotScopePreview: vi.fn(),
  fetchCopilotScopePreview: vi.fn(),
  previewCopilotAdmission: vi.fn(),
  startCopilotScopePreview: vi.fn(),
}))

vi.mock('../lib/lensApi', () => ({
  cancelCopilotScopePreview: mocks.cancelCopilotScopePreview,
  fetchCopilotScopePreview: mocks.fetchCopilotScopePreview,
  previewCopilotAdmission: mocks.previewCopilotAdmission,
  startCopilotScopePreview: mocks.startCopilotScopePreview,
}))

import {
  canonicalCopilotScopes,
  type CopilotSelectionScope,
  useCopilotSelectionPreview,
} from './useCopilotSelectionPreview'

function scope(key: string, path: string, directoryId = 31): CopilotSelectionScope {
  return {
    key,
    revision: 0,
    directoryId,
    path,
    pathType: 'dir',
    knownFileCount: null,
    knownSizeBytes: null,
  }
}

afterEach(() => {
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe('canonicalCopilotScopes', () => {
  it('keeps only the selected parent within one snapshot directory', () => {
    const result = canonicalCopilotScopes([
      scope('child-file', '/documents/contracts/a.pdf'),
      scope('parent', '/documents'),
      scope('child-dir', '/documents/contracts'),
    ])

    expect(result.scopes.map((row) => row.key)).toEqual(['parent'])
    expect(result.coveredBy).toEqual({
      'child-dir': '/documents',
      'child-file': '/documents',
    })
  })

  it('does not merge identical paths from different snapshot roots', () => {
    const result = canonicalCopilotScopes([
      scope('root-a', '/documents', 31),
      scope('root-b', '/documents', 32),
    ])

    expect(result.scopes.map((row) => row.key)).toEqual(['root-a', 'root-b'])
    expect(result.coveredBy).toEqual({})
  })

  it('treats the filesystem root as the parent of nested selections', () => {
    const result = canonicalCopilotScopes([
      scope('nested', '/documents/contracts'),
      scope('root', '/'),
    ])

    expect(result.scopes.map((row) => row.key)).toEqual(['root'])
    expect(result.coveredBy).toEqual({ nested: '/' })
  })
})

describe('useCopilotSelectionPreview', () => {
  it('cancels a preview task returned after its selection revision became stale', async () => {
    vi.useFakeTimers()
    let resolveStaleTask!: (value: {
      task_id: string
      status: 'pending'
    }) => void
    mocks.cancelCopilotScopePreview.mockResolvedValue(undefined)
    mocks.startCopilotScopePreview
      .mockReturnValueOnce(new Promise((resolve) => { resolveStaleTask = resolve }))
      .mockResolvedValueOnce({
        task_id: 'scope-task-current',
        status: 'success',
        summary: {
          path_type: 'dir',
          file_count: 2,
          size_bytes: 512,
          skipped_special_count: 0,
        },
      })
    mocks.previewCopilotAdmission.mockResolvedValue({
      gateway_scope: 'platform',
      selection: { file_count: 2, size_bytes: 512 },
      selection_limits: { max_files: -1, max_bytes: -1 },
      organization_capacity: { applicable: false },
      admission: { allowed: true, reasons: [] },
    })
    const scopes = ref([scope('nested', '/documents/old')])
    const vueScope = effectScope()
    const preview = vueScope.run(() => useCopilotSelectionPreview({
      snapshotId: ref(17),
      gatewayLinkId: computed(() => 23),
      gatewayMode: ref('auto'),
      scopes: computed(() => scopes.value),
    }))
    if (!preview) throw new Error('preview scope was not created')

    await vi.advanceTimersByTimeAsync(400)
    expect(mocks.startCopilotScopePreview).toHaveBeenCalledTimes(1)

    scopes.value = [scope('nested', '/documents/current')]
    await nextTick()
    await vi.advanceTimersByTimeAsync(400)
    expect(mocks.startCopilotScopePreview).toHaveBeenCalledTimes(2)

    resolveStaleTask({ task_id: 'scope-task-stale', status: 'pending' })
    await vi.advanceTimersByTimeAsync(0)
    await nextTick()

    expect(mocks.cancelCopilotScopePreview).toHaveBeenCalledWith(
      'scope-task-stale',
      expect.any(AbortSignal),
    )
    expect(preview.ready.value).toBe(true)
    vueScope.stop()
  })

  it('reuses a still-running Reader task when automatic polling resumes', async () => {
    vi.useFakeTimers()
    mocks.cancelCopilotScopePreview.mockResolvedValue(undefined)
    mocks.startCopilotScopePreview.mockResolvedValue({
      task_id: 'scope-task-1',
      status: 'pending',
    })
    mocks.fetchCopilotScopePreview.mockRejectedValue(
      Object.assign(new Error('temporarily unavailable'), { status: 503 }),
    )
    const scopes = ref([scope('nested', '/documents/contracts')])
    const vueScope = effectScope()
    const preview = vueScope.run(() => useCopilotSelectionPreview({
      snapshotId: ref(17),
      gatewayLinkId: computed(() => 23),
      gatewayMode: ref('auto'),
      scopes: computed(() => scopes.value),
    }))
    if (!preview) throw new Error('preview scope was not created')

    await vi.advanceTimersByTimeAsync(24_000)
    await nextTick()

    expect(preview.calculationStatus.value).toBe('waiting')
    expect(mocks.startCopilotScopePreview).toHaveBeenCalledTimes(1)
    expect(mocks.cancelCopilotScopePreview).not.toHaveBeenCalled()

    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(3_000)

    expect(mocks.startCopilotScopePreview).toHaveBeenCalledTimes(1)
    expect(mocks.fetchCopilotScopePreview).toHaveBeenCalled()
    expect(mocks.cancelCopilotScopePreview).not.toHaveBeenCalled()

    vueScope.stop()
    await nextTick()
    expect(mocks.cancelCopilotScopePreview).toHaveBeenCalledWith(
      'scope-task-1',
      expect.any(AbortSignal),
    )
  })

  it('resumes a waiting Reader task without requiring a browser event', async () => {
    vi.useFakeTimers()
    const transient = Object.assign(new Error('temporarily unavailable'), { status: 503 })
    mocks.cancelCopilotScopePreview.mockResolvedValue(undefined)
    mocks.startCopilotScopePreview.mockResolvedValue({
      task_id: 'scope-task-auto-resume',
      status: 'pending',
    })
    mocks.fetchCopilotScopePreview.mockRejectedValue(transient)
    mocks.previewCopilotAdmission.mockResolvedValue({
      gateway_scope: 'platform',
      selection: { file_count: 2, size_bytes: 512 },
      selection_limits: { max_files: -1, max_bytes: -1 },
      organization_capacity: { applicable: false },
      admission: { allowed: true, reasons: [] },
    })
    const vueScope = effectScope()
    const preview = vueScope.run(() => useCopilotSelectionPreview({
      snapshotId: ref(17),
      gatewayLinkId: computed(() => 23),
      gatewayMode: ref('auto'),
      scopes: computed(() => [scope('nested', '/documents/contracts')]),
    }))
    if (!preview) throw new Error('preview scope was not created')

    await vi.advanceTimersByTimeAsync(24_000)
    expect(preview.calculationStatus.value).toBe('waiting')

    mocks.fetchCopilotScopePreview.mockResolvedValue({
      task_id: 'scope-task-auto-resume',
      status: 'success',
      summary: {
        path_type: 'dir',
        file_count: 2,
        size_bytes: 512,
        skipped_special_count: 0,
      },
    })
    await vi.advanceTimersByTimeAsync(40_000)
    await nextTick()

    expect(preview.ready.value).toBe(true)
    expect(mocks.startCopilotScopePreview).toHaveBeenCalledTimes(1)
    vueScope.stop()
  })

  it('automatically rechecks temporarily unavailable organization capacity', async () => {
    vi.useFakeTimers()
    mocks.previewCopilotAdmission
      .mockResolvedValueOnce({
        gateway_scope: 'platform',
        selection: { file_count: 1, size_bytes: 256 },
        selection_limits: { max_files: -1, max_bytes: -1 },
        organization_capacity: {
          applicable: true,
          limit_available: true,
          limit_bytes: 1024,
          used_bytes: 0,
          remaining_bytes: null,
          after_create_bytes: null,
          usage_incomplete: true,
        },
        admission: { allowed: false, reasons: ['organization_capacity_unavailable'] },
      })
      .mockResolvedValueOnce({
        gateway_scope: 'platform',
        selection: { file_count: 1, size_bytes: 256 },
        selection_limits: { max_files: -1, max_bytes: -1 },
        organization_capacity: {
          applicable: true,
          limit_available: true,
          limit_bytes: 1024,
          used_bytes: 0,
          remaining_bytes: 1024,
          after_create_bytes: 768,
          usage_incomplete: false,
        },
        admission: { allowed: true, reasons: [] },
      })
    const known = scope('known', '/documents/file.txt')
    known.pathType = 'file'
    known.knownFileCount = 1
    known.knownSizeBytes = 256
    const vueScope = effectScope()
    const preview = vueScope.run(() => useCopilotSelectionPreview({
      snapshotId: ref(17),
      gatewayLinkId: computed(() => 23),
      gatewayMode: ref('auto'),
      scopes: computed(() => [known]),
    }))
    if (!preview) throw new Error('preview scope was not created')

    await vi.advanceTimersByTimeAsync(500)
    expect(preview.ready.value).toBe(false)
    await vi.advanceTimersByTimeAsync(30_500)
    await nextTick()

    expect(mocks.previewCopilotAdmission).toHaveBeenCalledTimes(2)
    expect(preview.ready.value).toBe(true)
    vueScope.stop()
  })

  it('keeps a long-running Reader task for bounded automatic recovery', async () => {
    vi.useFakeTimers()
    mocks.cancelCopilotScopePreview.mockResolvedValue(undefined)
    mocks.startCopilotScopePreview.mockResolvedValue({
      task_id: 'scope-task-long-running',
      status: 'pending',
    })
    mocks.fetchCopilotScopePreview.mockResolvedValue({
      task_id: 'scope-task-long-running',
      status: 'running',
    })
    const vueScope = effectScope()
    const preview = vueScope.run(() => useCopilotSelectionPreview({
      snapshotId: ref(17),
      gatewayLinkId: computed(() => 23),
      gatewayMode: ref('auto'),
      scopes: computed(() => [scope('nested', '/documents/contracts')]),
    }))
    if (!preview) throw new Error('preview scope was not created')

    await vi.advanceTimersByTimeAsync(122_000)
    await nextTick()

    expect(preview.calculationStatus.value).toBe('waiting')
    expect(mocks.startCopilotScopePreview).toHaveBeenCalledTimes(1)
    expect(mocks.cancelCopilotScopePreview).not.toHaveBeenCalled()
    vueScope.stop()
  })

  it('shows Unlimited limits after known metadata is admitted', async () => {
    vi.useFakeTimers()
    mocks.previewCopilotAdmission.mockResolvedValue({
      gateway_scope: 'platform',
      selection: { file_count: 4, size_bytes: 1024 },
      selection_limits: { max_files: -1, max_bytes: -1 },
      organization_capacity: {
        applicable: true,
        limit_bytes: -1,
        used_bytes: 0,
        remaining_bytes: null,
        after_create_bytes: null,
      },
      admission: { allowed: true, reasons: [] },
    })
    const known = scope('known', '/documents/contracts')
    known.knownFileCount = 4
    known.knownSizeBytes = 1024
    const vueScope = effectScope()
    const preview = vueScope.run(() => useCopilotSelectionPreview({
      snapshotId: ref(17),
      gatewayLinkId: computed(() => 23),
      gatewayMode: ref('auto'),
      scopes: computed(() => [known]),
    }))
    if (!preview) throw new Error('preview scope was not created')

    await vi.advanceTimersByTimeAsync(400)
    await nextTick()

    expect(preview.ready.value).toBe(true)
    expect(preview.totals.value).toEqual({ fileCount: 4, sizeBytes: 1024 })
    expect(preview.admission.value?.selection_limits).toEqual({
      max_files: -1,
      max_bytes: -1,
    })
    expect(mocks.startCopilotScopePreview).not.toHaveBeenCalled()
    vueScope.stop()
  })

  it('recovers admission preview from bounded transient failures', async () => {
    vi.useFakeTimers()
    const transient = Object.assign(new Error('temporarily unavailable'), { status: 503 })
    mocks.previewCopilotAdmission
      .mockRejectedValueOnce(transient)
      .mockRejectedValueOnce(transient)
      .mockResolvedValue({
        gateway_scope: 'platform',
        selection: { file_count: 1, size_bytes: 256 },
        selection_limits: { max_files: -1, max_bytes: -1 },
        organization_capacity: { applicable: false },
        admission: { allowed: true, reasons: [] },
      })
    const known = scope('known', '/documents/file.txt')
    known.pathType = 'file'
    known.knownFileCount = 1
    known.knownSizeBytes = 256
    const vueScope = effectScope()
    const preview = vueScope.run(() => useCopilotSelectionPreview({
      snapshotId: ref(17),
      gatewayLinkId: computed(() => 23),
      gatewayMode: ref('auto'),
      scopes: computed(() => [known]),
    }))
    if (!preview) throw new Error('preview scope was not created')

    await vi.advanceTimersByTimeAsync(7_500)
    await nextTick()

    expect(mocks.previewCopilotAdmission).toHaveBeenCalledTimes(3)
    expect(preview.admissionError.value).toBe('')
    expect(preview.ready.value).toBe(true)
    vueScope.stop()
  })

  it('surfaces field details when admission validation is rejected', async () => {
    vi.useFakeTimers()
    mocks.previewCopilotAdmission.mockRejectedValue({
      status: 400,
      message: 'Validation failed',
      code: 'VALIDATION.FAILED',
      errorCode: 'VALIDATION.FAILED',
      fields: {
        gateway_link_id: ['HFL Gateway Agent is offline or not routable.'],
      },
    })
    const known = scope('known', '/documents/file.txt')
    known.pathType = 'file'
    known.knownFileCount = 1
    known.knownSizeBytes = 256
    const vueScope = effectScope()
    const preview = vueScope.run(() => useCopilotSelectionPreview({
      snapshotId: ref(17),
      gatewayLinkId: computed(() => 23),
      gatewayMode: ref('auto'),
      scopes: computed(() => [known]),
    }))
    if (!preview) throw new Error('preview scope was not created')

    await vi.advanceTimersByTimeAsync(400)
    await nextTick()

    expect(preview.admissionError.value).toBe('HFL Gateway Agent is offline or not routable.')
    expect(preview.ready.value).toBe(false)
    vueScope.stop()
  })

  it('reuses a completed data summary across a new selection revision', async () => {
    vi.useFakeTimers()
    mocks.startCopilotScopePreview.mockResolvedValue({
      task_id: 'scope-task-complete',
      status: 'success',
      summary: {
        path_type: 'dir',
        file_count: 8,
        size_bytes: 2048,
        skipped_special_count: 0,
      },
    })
    mocks.previewCopilotAdmission.mockResolvedValue({
      gateway_scope: 'platform',
      selection: { file_count: 8, size_bytes: 2048 },
      selection_limits: { max_files: -1, max_bytes: -1 },
      organization_capacity: { applicable: false },
      admission: { allowed: true, reasons: [] },
    })
    const scopes = ref([scope('nested', '/documents/contracts')])
    const vueScope = effectScope()
    const preview = vueScope.run(() => useCopilotSelectionPreview({
      snapshotId: ref(17),
      gatewayLinkId: computed(() => 23),
      gatewayMode: ref('auto'),
      scopes: computed(() => scopes.value),
    }))
    if (!preview) throw new Error('preview scope was not created')

    await vi.advanceTimersByTimeAsync(400)
    expect(preview.ready.value).toBe(true)

    scopes.value = [{ ...scopes.value[0], revision: 1 }]
    await nextTick()
    await vi.advanceTimersByTimeAsync(400)

    expect(preview.ready.value).toBe(true)
    expect(mocks.startCopilotScopePreview).toHaveBeenCalledTimes(1)
    expect(mocks.previewCopilotAdmission).toHaveBeenCalledTimes(2)
    vueScope.stop()
  })

  it('starts a fresh bounded budget after an external recovery signal', async () => {
    vi.useFakeTimers()
    mocks.cancelCopilotScopePreview.mockResolvedValue(undefined)
    mocks.startCopilotScopePreview.mockResolvedValue({
      task_id: null,
      status: 'waiting',
      retryable: true,
    })
    mocks.previewCopilotAdmission.mockResolvedValue({
      gateway_scope: 'platform',
      selection: { file_count: 2, size_bytes: 512 },
      selection_limits: { max_files: -1, max_bytes: -1 },
      organization_capacity: { applicable: false },
      admission: { allowed: true, reasons: [] },
    })
    const vueScope = effectScope()
    const preview = vueScope.run(() => useCopilotSelectionPreview({
      snapshotId: ref(17),
      gatewayLinkId: computed(() => 23),
      gatewayMode: ref('auto'),
      scopes: computed(() => [scope('nested', '/documents/contracts')]),
    }))
    if (!preview) throw new Error('preview scope was not created')

    await vi.advanceTimersByTimeAsync(480_000)
    expect(preview.calculationStatus.value).toBe('error')
    expect(mocks.startCopilotScopePreview).toHaveBeenCalledTimes(4)

    mocks.startCopilotScopePreview.mockResolvedValue({
      task_id: 'scope-task-recovered',
      status: 'success',
      summary: {
        path_type: 'dir',
        file_count: 2,
        size_bytes: 512,
        skipped_special_count: 0,
      },
    })
    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(1_000)
    await nextTick()

    expect(mocks.startCopilotScopePreview).toHaveBeenCalledTimes(5)
    expect(preview.ready.value).toBe(true)
    vueScope.stop()
  })
})
