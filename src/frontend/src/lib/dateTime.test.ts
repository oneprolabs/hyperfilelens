import { describe, expect, it } from 'vitest'
import {
  formatAppDate,
  formatAppDateTime,
  formatAppDateTimeUtc,
  formatAppTime,
  formatLocalDateTime,
  parseLocalDateTime,
} from './dateTime'

describe('dateTime', () => {
  it('parses space-separated datetime strings', () => {
    const parsed = parseLocalDateTime('2025-10-31 15:03:42')
    expect(parsed.getFullYear()).toBe(2025)
    expect(parsed.getMonth()).toBe(9)
    expect(parsed.getDate()).toBe(31)
  })

  it('formats local datetime as YYYY-MM-DD HH:mm:ss', () => {
    const date = new Date(2025, 9, 31, 15, 3, 42)
    expect(formatAppDateTime(date)).toBe('2025-10-31 15:03:42')
  })

  it.each([
    [0, 15, 0, 15, '2026-01-15 00:15:42'],
    [6, 15, 23, 45, '2026-07-15 23:45:42'],
  ] as const)('converts API timestamps to browser time in month %s', (month, day, hour, minute, expected) => {
    const utc = new Date(2026, month, day, hour, minute, 42).toISOString()
    for (const value of [utc, utc.replace('Z', '+00:00')]) {
      expect(formatAppDateTime(value)).toBe(expected)
      expect(formatLocalDateTime(value)).toBe(expected)
    }
  })

  it.each([
    ['Asia/Shanghai', '2026-10-16T07:51:00Z', '2026-10-16 15:51:00'],
    ['Asia/Shanghai', '2026-10-16T23:51:00Z', '2026-10-17 07:51:00'],
    ['America/New_York', '2026-01-15T01:51:00Z', '2026-01-14 20:51:00'],
    ['America/New_York', '2026-07-15T01:51:00Z', '2026-07-14 21:51:00'],
  ])('preserves explicit %s conversion for %s', (timeZone, value, expected) => {
    expect(formatAppDateTime(value, '—', { timeZone })).toBe(expected)
  })

  it('formats date-only values as YYYY-MM-DD', () => {
    expect(formatAppDate('2026-12-31T00:00:00Z', '—', { timeZone: 'UTC' })).toBe('2026-12-31')
  })

  it('formats time-only values as HH:mm:ss', () => {
    const date = new Date(2025, 9, 31, 5, 7, 9)
    expect(formatAppTime(date)).toBe('05:07:09')
  })

  it('formats UTC datetime with suffix', () => {
    expect(formatAppDateTimeUtc('2025-10-31T15:03:42Z')).toBe('2025-10-31 15:03:42 UTC')
  })

  it('keeps formatLocalDateTime aligned with formatAppDateTime', () => {
    const date = new Date(2025, 5, 24, 17, 32, 14)
    expect(formatLocalDateTime(date)).toBe(formatAppDateTime(date))
    expect(formatLocalDateTime(date, '—', 'en-US', { hour12: true })).toBe(formatAppDateTime(date))
  })

  it('returns fallback for empty values', () => {
    expect(formatAppDateTime(null, '—')).toBe('—')
    expect(formatAppDate(undefined, '—')).toBe('—')
  })

  it('respects custom timezone for profile-style display', () => {
    expect(formatAppDateTime('2025-10-31T15:03:42Z', '—', { timeZone: 'UTC' })).toBe('2025-10-31 15:03:42')
  })
})
