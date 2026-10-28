// @vitest-environment jsdom
import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Repositories from './Repositories.vue'
import { getStorageRepository, syncStorageRepositoryUsage } from '../../lib/storageRepositoryApi'
import type { StorageRepository } from '../../lib/storageRepositoryApi'

vi.mock('vue-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vue-router')>()),
  useRoute: () => ({ path: '/node/repositories', query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  onBeforeRouteLeave: vi.fn(),
}))
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { get: vi.fn().mockResolvedValue({ results: [], count: 0 }) },
}))
vi.mock('../../lib/storageRepositoryApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/storageRepositoryApi')>()),
  getStorageRepository: vi.fn(),
  syncStorageRepositoryUsage: vi.fn(),
}))

let wrapper: VueWrapper | undefined
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  vi.stubGlobal('ResizeObserver', class { observe = vi.fn(); unobserve = vi.fn(); disconnect = vi.fn() })
  vi.stubGlobal('requestAnimationFrame', vi.fn())
  vi.mocked(getStorageRepository).mockReset()
  vi.mocked(syncStorageRepositoryUsage).mockReset()
  vi.mocked(syncStorageRepositoryUsage).mockResolvedValue({ queued: true } as never)
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  vi.clearAllMocks()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function mountState() {
  wrapper = shallowMount(Repositories, {
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: {}, missingWarn: false, fallbackWarn: false })],
      config: { warnHandler: () => {} },
    },
  })
  await flushPromises()
  return (wrapper.vm.$ as unknown as { setupState: Record<string, unknown> }).setupState
}

function deferred<Value>() {
  let resolve!: (value: Value) => void
  let reject!: (error: Error) => void
  const promise = new Promise<Value>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const previous = {
  id: 7, name: 'Repository', repo_type: 's3', organization_id: 1,
  status: 'created', health: 'online', config: {}, estimated_usage_bytes: 24, capacity_bytes: 0,
  usage_probe_status: 'success', metrics_last_attempt_at: '2026-10-27T01:00:00Z',
} satisfies StorageRepository

function openRepository(state: Record<string, unknown>, id = 7) {
  const openDetail = state.openDetail as (row: unknown) => void
  openDetail({ ...previous, id, kind: 's3' })
}

function refresh(state: Record<string, unknown>) {
  return (state.refreshRepositoryUsage as () => Promise<void>)()
}

describe('repository usage refresh', () => {
  it.each([
    ['nas', undefined, 'pending', 'backingStorageNotCollected'],
    ['nas', undefined, 'failed', 'backingStorageNotCollected'],
    ['nas', 'proxy', 'pending', 'backingStorageNotYetCollected'],
    ['nas', 'proxy', 'failed', 'backingStorageTemporarilyUnavailable'],
    ['proxy_fs', undefined, 'pending', 'backingStorageNotYetCollected'],
    ['proxy_fs', undefined, 'failed', 'backingStorageTemporarilyUnavailable'],
  ])('distinguishes backing space for %s (%s, %s)', async (kind, binding, status, key) => {
    const state = await mountState()
    const row = { ...previous, kind, bind_node_type: binding, capacity_probe_status: status }
    const display = state.backingStorageDisplayLabel as (repository: unknown) => string
    const tooltip = state.backingStoragePopoverRows as (repository: unknown) => { label: string, value: string }[]
    expect(display(row)).toBe(`repositoriesPage.${key}`)
    expect(tooltip(row)).toContainEqual({
      label: 'repositoriesPage.availableSpace', value: `repositoriesPage.${key}`,
    })
    expect(tooltip(row).some(item => item.label === 'repositoriesPage.available')).toBe(false)
  })

  it('preserves a measured zero available space rather than marking it unavailable', async () => {
    const state = await mountState()
    const row = {
      ...previous, kind: 'proxy_fs', capacity_probe_status: 'success',
      storage_total_bytes: 1024, storage_used_bytes: 1024, storage_available_bytes: 0,
    }
    const tooltip = state.backingStoragePopoverRows as (repository: unknown) => { label: string, value: string }[]
    const format = state.fmtBytes as (bytes: number) => string
    expect(tooltip(row)).toContainEqual({ label: 'repositoriesPage.availableSpace', value: format(0) })
  })

  it.each(['s3', 'nas', 'proxy_fs'])('preserves an explicit pending status for %s even with stored usage', async (kind) => {
    const state = await mountState()
    const map = state.mapApiToRow as (repository: unknown) => { usage_probe_status: string }
    expect(map({ ...previous, repo_type: kind, usage_probe_status: 'pending' }).usage_probe_status).toBe('pending')
  })

  it.each(['s3', 'nas', 'proxy_fs'])('refreshes %s and waits for a new measurement instead of old success', async (kind) => {
    const state = await mountState()
    state.detailRow = { id: 7, kind, usage_probe_status: 'success' }
    const previous = {
      id: 7, name: 'Repository', repo_type: kind,
      status: 'created', health: 'online', config: {}, estimated_usage_bytes: 24,
      usage_probe_status: 'success', metrics_last_attempt_at: '2026-10-27T01:00:00Z',
    }
    vi.mocked(getStorageRepository)
      .mockResolvedValueOnce(previous as never)
      .mockResolvedValueOnce(previous as never)
      .mockResolvedValueOnce({ ...previous, metrics_last_attempt_at: '2026-10-27T02:00:00Z' } as never)

    await (state.refreshRepositoryUsage as () => Promise<void>)()
    expect(syncStorageRepositoryUsage).toHaveBeenCalledWith(7, true)
    await vi.advanceTimersByTimeAsync(1000)
    expect(state.usageRefreshInProgress).toBe(true)
    await vi.advanceTimersByTimeAsync(2000)
    expect(state.usageRefreshInProgress).toBe(false)
    expect(state.usageRefreshError).toBe('')
  })

  it.each(['resolve', 'reject'])('ignores a baseline request that finishes after close and reopen: %s', async (outcome) => {
    const state = await mountState()
    openRepository(state)
    const baseline = deferred<StorageRepository>()
    vi.mocked(getStorageRepository).mockReturnValueOnce(baseline.promise)
    const pending = refresh(state)
    state.drawerDetailOpen = false
    openRepository(state)
    if (outcome === 'resolve') baseline.resolve(previous)
    else baseline.reject(new Error('Old baseline failed'))
    await pending
    expect(syncStorageRepositoryUsage).not.toHaveBeenCalled()
    expect(state.usageRefreshInProgress).toBe(false)
    expect(state.usageRefreshError).toBe('')
  })

  it.each([
    [7, 'resolve'], [7, 'reject'], [8, 'resolve'], [8, 'reject'],
  ])('does not let an old enqueue response affect repository %s: %s', async (id, outcome) => {
    const state = await mountState()
    openRepository(state)
    vi.mocked(getStorageRepository).mockResolvedValue(previous)
    const queued = deferred<Awaited<ReturnType<typeof syncStorageRepositoryUsage>>>()
    vi.mocked(syncStorageRepositoryUsage).mockReturnValueOnce(queued.promise)
    const pending = refresh(state)
    await flushPromises()
    expect(syncStorageRepositoryUsage).toHaveBeenCalledTimes(1)
    state.drawerDetailOpen = false
    openRepository(state, Number(id))
    await refresh(state)
    if (outcome === 'resolve') queued.resolve({ queued: true })
    else queued.reject(new Error('Old enqueue failed'))
    await pending
    expect(state.usageRefreshInProgress).toBe(true)
    expect(state.usageRefreshError).toBe('')
    await vi.advanceTimersByTimeAsync(1000)
    expect(getStorageRepository).toHaveBeenCalledTimes(3)
  })

  it.each(['resolve', 'reject'])('ignores an in-flight poll after reopening the same repository: %s', async (outcome) => {
    const state = await mountState()
    openRepository(state)
    const poll = deferred<StorageRepository>()
    vi.mocked(getStorageRepository)
      .mockResolvedValueOnce(previous)
      .mockReturnValueOnce(poll.promise)
      .mockResolvedValue(previous)
    await refresh(state)
    await vi.advanceTimersByTimeAsync(1000)
    state.drawerDetailOpen = false
    openRepository(state)
    await refresh(state)
    if (outcome === 'resolve') {
      poll.resolve({ ...previous, usage_probe_status: 'failed', usage_last_error: 'Old poll error', metrics_last_attempt_at: '2026-10-27T02:00:00Z' })
    } else poll.reject(new Error('Old poll failed'))
    await flushPromises()
    expect(state.usageRefreshInProgress).toBe(true)
    expect(state.usageRefreshError).toBe('')
    expect((state.detailRow as { usage_probe_status: string }).usage_probe_status).toBe('success')
    await vi.advanceTimersByTimeAsync(2000)
    expect(getStorageRepository).toHaveBeenCalledTimes(4)
  })

  it('invalidates pending work on unmount', async () => {
    const state = await mountState()
    openRepository(state)
    const baseline = deferred<StorageRepository>()
    vi.mocked(getStorageRepository).mockReturnValueOnce(baseline.promise)
    const pending = refresh(state)
    wrapper!.unmount()
    wrapper = undefined
    baseline.resolve(previous)
    await pending
    expect(syncStorageRepositoryUsage).not.toHaveBeenCalled()
  })
})
