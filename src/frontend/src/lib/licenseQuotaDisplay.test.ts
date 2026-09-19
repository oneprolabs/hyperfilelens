import { describe, expect, it } from 'vitest'

import {
  buildQuotaRows,
  formatQuotaBytes,
  quotaDefsForDashboard,
  quotaDefsForSubscription,
  quotaUsagePercent,
} from './licenseQuotaDisplay'

describe('Byte quota display', () => {
  it('uses readable MB and GB units without changing unlimited semantics', () => {
    expect(formatQuotaBytes(500 * 1024 ** 2)).toBe('500 MB')
    expect(formatQuotaBytes(1536 * 1024 ** 2)).toBe('1.5 GB')
    expect(formatQuotaBytes(1024 ** 4)).toBe('1 TB')
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

    const storage = quotaDefsForSubscription().find(
      (item) => item.limitKey === 'max_storage_bytes',
    )
    expect(storage?.formatBytes).toBe(true)
  })

  it('keeps subscription row builders readable for byte capacities', () => {
    const rows = buildQuotaRows(
      { public_gateway_capacity_used_bytes: 250 * 1024 ** 2 },
      { max_public_gateway_capacity_bytes: 500 * 1024 ** 2 },
      undefined,
      { subscription: true },
    )
    const capacity = rows.find((row) => row.key === 'publicGatewayCapacity')

    expect(capacity).toMatchObject({
      used: 250 * 1024 ** 2,
      limit: 500 * 1024 ** 2,
      usedDisplay: '250 MB',
      limitDisplay: '500 MB',
    })
  })

  it('formats backup storage with the same automatic unit selection', () => {
    const rows = buildQuotaRows(
      { storage_used_bytes: 3 * 1024 ** 2 },
      { max_storage_bytes: 1024 ** 4 },
      undefined,
      { subscription: true },
    )
    const storage = rows.find((row) => row.key === 'storage')

    expect(storage).toMatchObject({
      usedDisplay: '3 MB',
      limitDisplay: '1 TB',
    })
  })

  it('keeps all quota meters available to the dashboard finite-limit filter', () => {
    expect(quotaDefsForDashboard().map((item) => item.key)).toEqual(
      quotaDefsForSubscription().map((item) => item.key),
    )
  })

  it('treats non-zero usage against a zero limit as fully used', () => {
    expect(quotaUsagePercent(1, 0)).toBe(100)
    expect(quotaUsagePercent(0, 0)).toBe(0)
  })
})
