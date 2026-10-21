import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { fetchMonitorNodes, fetchNodeMonitor } from './monitorApi'

vi.mock('./api', () => ({ api: vi.fn() }))

describe('Host Monitoring request cancellation', () => {
  beforeEach(() => { vi.mocked(api).mockReset() })

  it('forwards the abort signal for the scoped node directory', async () => {
    const signal = new AbortController().signal
    vi.mocked(api).mockResolvedValue({ items: [{ id: 7 }] })
    expect(await fetchMonitorNodes('agent', { signal })).toEqual([{ id: 7 }])
    expect(api).toHaveBeenCalledExactlyOnceWith('/api/v1/monitors/nodes/?role=agent', { signal })
  })

  it('forwards the abort signal and time range for node metrics', async () => {
    const signal = new AbortController().signal
    const payload = { host: {}, range: {}, current: {}, series: [] }
    vi.mocked(api).mockResolvedValue(payload)
    expect(await fetchNodeMonitor(7, { hours: 24 }, { signal })).toEqual(payload)
    expect(api).toHaveBeenCalledExactlyOnceWith('/api/v1/monitors/nodes/7/?hours=24', { signal })
  })

  it('retains callers that do not supply a request init', async () => {
    vi.mocked(api).mockResolvedValue({ items: [] })
    expect(await fetchMonitorNodes('gateway')).toEqual([])
    expect(api).toHaveBeenLastCalledWith('/api/v1/monitors/nodes/?role=gateway', undefined)
  })
})
