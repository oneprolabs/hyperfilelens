import { describe, expect, it } from 'vitest'

import {
  buildQuotaRows,
  formatQuotaBytes,
  quotaDefsForSubscription,
} from './licenseQuotaDisplay'

describe('Public Gateway capacity display', () => {
  it('uses readable MB and GB units without changing unlimited semantics', () => {
    expect(formatQuotaBytes(500 * 1024 ** 2)).toBe('500 MB')
    expect(formatQuotaBytes(1536 * 1024 ** 2)).toBe('1.5 GB')
    expect(formatQuotaBytes(0)).toBe('0 MB')
    expect(formatQuotaBytes(-1, 'No limit')).toBe('No limit')
  })

  it('marks the subscription capacity meter for byte formatting', () => {
    const definition = quotaDefsForSubscription().find(
      (item) => item.limitKey === 'max_public_gateway_capacity_bytes',
    )

    expect(definition?.formatBytes).toBe(true)
    expect(definition?.suffix).toBeUndefined()
    expect(definition?.divisor).toBeUndefined()
  })

  it('keeps subscription row builders readable for byte capacities', () => {
    const rows = buildQuotaRows(
      { public_gateway_capacity_used_bytes: 250 * 1024 ** 2 },
      { max_public_gateway_capacity_bytes: 500 * 1024 ** 2 },
      undefined,
      { subscription: true },
    )
    const capacity = rows.find((row) => row.key === 'publicGatewayCapacity')

    expect(capacity).toMatchObject({ used: 250, limit: 500, suffix: 'MB' })
  })
})
