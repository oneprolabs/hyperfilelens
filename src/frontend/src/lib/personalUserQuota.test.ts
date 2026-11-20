import { describe, expect, it } from 'vitest'
import { parseUserLimit, splitUserCapacity, validPersonalUserPolicy } from './personalUserQuota'

describe('Personal user quota policy', () => {
  it('accepts an explicit unconfigured state, never an incomplete configured policy', () => {
    expect(validPersonalUserPolicy({ configured: false, limits: {} })).toBe(true)
    expect(validPersonalUserPolicy({ configured: true, limits: {} })).toBe(false)
    expect(validPersonalUserPolicy({
      configured: true,
      limits: { max_storage_bytes: 1024, max_public_gateway_capacity_bytes: 0, ai_tokens: 0 },
    })).toBe(true)
  })

  it('rejects Unlimited, precision loss, nonintegers and unknown keys', () => {
    for (const amount of [-1, Infinity, NaN, 0.5, Number.MAX_SAFE_INTEGER + 1]) {
      expect(validPersonalUserPolicy({
        configured: true,
        limits: { max_storage_bytes: amount, max_public_gateway_capacity_bytes: 0, ai_tokens: 0 },
      })).toBe(false)
    }
    expect(validPersonalUserPolicy({
      configured: true,
      limits: { max_storage_bytes: 1, max_public_gateway_capacity_bytes: 0, ai_tokens: 0, extra: 1 },
    } as never)).toBe(false)
  })

  it('parses whole capacity units without empty-to-zero conversion or overflow', () => {
    for (const value of ['', ' ', '-1', '1.2', '1e3', '+2', 'Infinity']) {
      expect(parseUserLimit(value, 1024 ** 3)).toBeUndefined()
    }
    expect(parseUserLimit('0', 1024 ** 3)).toBe(0)
    expect(parseUserLimit(' 12 ', 1024 ** 3)).toBe(12 * 1024 ** 3)
    expect(parseUserLimit(String(Number.MAX_SAFE_INTEGER), 1024 ** 3)).toBeUndefined()
  })

  it('round-trips persisted bytes exactly instead of rounding capacity units', () => {
    for (const bytes of [0, 1, 1023, 1024 ** 2 + 1, 3 * 1024 ** 3, Number.MAX_SAFE_INTEGER]) {
      const split = splitUserCapacity(bytes)
      expect(parseUserLimit(split.value, split.unit)).toBe(bytes)
    }
  })
})
