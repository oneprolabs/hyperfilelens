import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { listPolicies, listRecords, policyStatistics, recordStatistics } from './alertApi'

vi.mock('./api', () => ({ api: vi.fn() }))

describe('alertApi', () => {
  beforeEach(() => {
    vi.mocked(api).mockReset()
    vi.mocked(api).mockResolvedValue({ data: { count: 0, results: [] } })
  })

  it('uses the canonical plural alert API for records and statistics', async () => {
    await listRecords()
    await recordStatistics()

    expect(api).toHaveBeenNthCalledWith(1, '/api/v1/alerts/records/')
    expect(api).toHaveBeenNthCalledWith(2, '/api/v1/alerts/records/stats/')
  })

  it('uses the canonical plural alert API for rules and statistics', async () => {
    await listPolicies()
    await policyStatistics()

    expect(api).toHaveBeenNthCalledWith(1, '/api/v1/alerts/policies/')
    expect(api).toHaveBeenNthCalledWith(2, '/api/v1/alerts/policies/stats/')
  })
})
