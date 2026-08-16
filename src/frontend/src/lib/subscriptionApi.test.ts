// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchEffectiveQuotaUsage } from './subscriptionApi'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('fetchEffectiveQuotaUsage', () => {
  it('loads and unwraps the tenant effective quota contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 0,
      message: 'ok',
      data: {
        organization_id: 7,
        organization_key: 'tenant-seven',
        quota_usage: [
          {
            key: 'max_users',
            limit: 10,
            unit: 'count',
            used: 4,
            plan_key: 'pro',
            plan_limit: 50,
            override_limit: 10,
            limit_source: 'override',
            overridden: true,
            remaining: 6,
            usage_percent: 40,
            usage_status: 'ok',
          },
        ],
      },
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchEffectiveQuotaUsage()).resolves.toMatchObject({
      organization_id: 7,
      organization_key: 'tenant-seven',
      quota_usage: [
        {
          key: 'max_users',
          limit: 10,
          used: 4,
          limit_source: 'override',
          overridden: true,
          usage_status: 'ok',
        },
      ],
    })

    const url = new URL(String(fetchMock.mock.calls[0][0]), window.location.origin)
    expect(url.pathname).toBe('/api/v1/subscription/quotas/effective/')
  })
})
