import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { loadDashboardOverview, type DashboardOverview } from './dashboardApi'
import { beginRouteRequestScope } from './routeRequestAbort'
import type { DashboardTaskStatistics, TaskStatistics } from './taskApi'

vi.mock('./api', () => ({ api: vi.fn() }))
vi.mock('../composables/useAuth', () => ({ getEffectiveOrgKey: () => 'test-org' }))

const apiMock = vi.mocked(api)
const stats = (overrides: Partial<TaskStatistics> = {}): TaskStatistics => ({
  total: 0, running: 0, success: 0, failed: 0, timeout: 0, cancelled: 0, ...overrides,
})
const summary = (): DashboardTaskStatistics => ({
  total: stats({ total: 12, success: 8 }),
  recovery_24h: stats({ total: 5, success: 2, failed: 1, timeout: 1, running: 1 }),
  days: Array.from({ length: 7 }, () => stats({ total: 3, success: 1, failed: 1, timeout: 1 })),
})

function mockOtherEndpoint(path: string) {
  if (path.includes('/subscription/licenses/current/')) {
    return { is_valid: true, organization_name: 'Test', limits: {}, usage: {} }
  }
  if (path.includes('/production-summary/')) {
    return {
      total: 3, available: 2, unavailable: 1,
      hosts: { total: 3, available: 2, unavailable: 1 },
      nas: { total: 0, available: 0, unavailable: 0 },
    }
  }
  if (path.includes('/storage/repositories/')) {
    return [{ id: 1, name: 'Shared repository', repo_type: 's3' }]
  }
  return { count: 0, results: [] }
}

describe('Dashboard statistics loading', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    beginRouteRequestScope()
    apiMock.mockReset()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('retains the alert type and original text for reactive task alert localization', async () => {
    const alert = {
      id: 1500, type: 'task', title: 'Task alert: task_failed',
      message: 'upload error: unsupported source: root@agent:/data',
    }
    apiMock.mockImplementation(async (path) => {
      if (path.includes('/dashboard-statistics/')) return summary()
      if (path.includes('/alerts/records/?status=firing')) return { count: 1, results: [alert] }
      return mockOtherEndpoint(path)
    })
    const result = await loadDashboardOverview((key) => key)
    expect(result.activeAlerts).toEqual([expect.objectContaining({
      id: '1500', type: alert.type, title: alert.title, detail: alert.message,
    })])
  })

  it('retains the event type and original text for reactive event localization', async () => {
    const event = {
      id: 1459,
      event_type: 'task.failed',
      title: 'Repository operation failed: Orphan snapshot reconciliation · repo',
      details: 'Snapshot reconciliation failed; no unconfirmed deletion will be retried.',
      severity: 'warning',
    }
    apiMock.mockImplementation(async (path) => {
      if (path.includes('/dashboard-statistics/')) return summary()
      if (path.includes('/monitors/events/')) return { count: 1, results: [event] }
      return mockOtherEndpoint(path)
    })
    const result = await loadDashboardOverview((key) => key)
    expect(result.events).toEqual([expect.objectContaining({
      id: '1459', event_type: event.event_type, title: event.title, detail: event.details,
    })])
  })

  it('uses one authorized batch request for all nine statistics windows', async () => {
    apiMock.mockImplementation(async (path) => path.includes('/dashboard-statistics/')
      ? summary()
      : mockOtherEndpoint(path))
    const result = await loadDashboardOverview((key) => key)
    const requests = apiMock.mock.calls.filter(([path]) => path.startsWith('/api/v1/tasks/'))
    expect(requests.filter(([path]) => path.includes('/statistics/'))).toHaveLength(0)
    const batched = requests.filter(([path]) => path.includes('/dashboard-statistics/'))
    expect(batched).toHaveLength(1)
    const params = new URL(batched[0]![0], 'https://hfl.test').searchParams
    const windows = JSON.parse(params.get('windows')!)
    expect(windows).toHaveLength(7)
    expect(windows[0]).toHaveProperty('finished_after')
    expect(windows[0]).toHaveProperty('finished_before')
    expect(Date.now() - Date.parse(params.get('recovery_created_after')!)).toBe(24 * 60 * 60 * 1000)
    expect(result.taskStatisticsState).toBe('ready')
    expect(result.taskStats.total).toBe(12)
    expect(result.recoveryDrill24h).toMatchObject({ running: 1, failed: 2, successRate: 50 })
    expect(result.tasks7dBuckets[0]).toMatchObject({ success: 1, fail: 2, cancel: 0 })
    expect(vi.getTimerCount()).toBe(0)
  })

  it('publishes source and storage cards before a slow statistics request resolves', async () => {
    let resolveStatistics!: (value: DashboardTaskStatistics) => void
    apiMock.mockImplementation(async (path) => path.includes('/dashboard-statistics/')
      ? new Promise<DashboardTaskStatistics>((resolve) => { resolveStatistics = resolve })
      : mockOtherEndpoint(path))
    const progress = vi.fn<(overview: DashboardOverview) => void>()
    const pending = loadDashboardOverview((key) => key, progress)
    await vi.waitFor(() => expect(progress).toHaveBeenCalledOnce())
    const partial = progress.mock.calls[0]![0]
    expect(partial.productionSources.total).toBe(3)
    expect(partial.storage.repoCount).toBe(1)
    expect(partial.taskStatisticsState).toBe('loading')
    expect(partial.tasks7dBuckets).toEqual([])
    expect(partial.isEmpty).toBe(false)
    resolveStatistics(summary())
    expect((await pending).taskStatisticsState).toBe('ready')
  })

  it('marks failed statistics unavailable rather than displaying an empty chart', async () => {
    apiMock.mockImplementation(async (path) => {
      if (path.includes('/dashboard-statistics/')) throw new Error('unavailable')
      return mockOtherEndpoint(path)
    })
    const result = await loadDashboardOverview((key) => key)
    expect(result.taskStatisticsState).toBe('unavailable')
    expect(result.tasks7dBuckets).toEqual([])
    expect(result.productionSources.total).toBe(3)
    expect(result.isEmpty).toBe(false)
  })

  it.each(['timeout', 'route change'])('cancels statistics on %s without blocking fast cards', async (reason) => {
    apiMock.mockImplementation(async (path, init) => {
      if (!path.includes('/dashboard-statistics/')) return mockOtherEndpoint(path)
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
      })
    })
    const progress = vi.fn()
    const pending = loadDashboardOverview((key) => key, progress)
    await vi.waitFor(() => expect(progress).toHaveBeenCalledOnce())
    if (reason === 'timeout') await vi.advanceTimersByTimeAsync(15_000)
    else beginRouteRequestScope()
    expect((await pending).taskStatisticsState).toBe('unavailable')
    expect(vi.getTimerCount()).toBe(0)
  })
})
