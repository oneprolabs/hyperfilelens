// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from 'vitest'
import {
  clearSharedSessionNotice,
  consumeSessionNotice,
  SESSION_NOTICE_TTL_MS,
  storeSessionNotice,
} from './sessionNotice'

describe('session notice handoff', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    clearSharedSessionNotice()
  })

  it('consumes a verified notice only once', () => {
    expect(storeSessionNotice('TOKEN_REUSED', window.sessionStorage, 1_000)).toBe(true)

    expect(consumeSessionNotice(window.sessionStorage, 1_500)).toBe('TOKEN_REUSED')
    expect(consumeSessionNotice(window.sessionStorage, 1_500)).toBeNull()
  })

  it('rejects expired, future, unknown, and malformed notices', () => {
    expect(storeSessionNotice('TOKEN_REUSED', window.sessionStorage, 1_000)).toBe(true)
    expect(consumeSessionNotice(window.sessionStorage, 1_000 + SESSION_NOTICE_TTL_MS + 1)).toBeNull()
    clearSharedSessionNotice()

    expect(storeSessionNotice('TOKEN_REUSED', window.sessionStorage, 2_000)).toBe(true)
    expect(consumeSessionNotice(window.sessionStorage, 1_999)).toBeNull()
    clearSharedSessionNotice()

    expect(storeSessionNotice('FORGED_REASON', window.sessionStorage, 3_000)).toBe(false)
    expect(consumeSessionNotice(window.sessionStorage, 3_000)).toBeNull()

    window.sessionStorage.setItem('hyperfilelens:auth-session-notice', '{not-json')
    expect(consumeSessionNotice(window.sessionStorage, 3_000)).toBeNull()
    expect(window.sessionStorage.getItem('hyperfilelens:auth-session-notice')).toBeNull()
  })

  it('fails closed when storage is unavailable', () => {
    expect(storeSessionNotice('TOKEN_REUSED', null)).toBe(false)
    expect(consumeSessionNotice(null, 1_000)).toBeNull()
  })

  it('preserves the first security cause across a later expiry fallback', () => {
    expect(storeSessionNotice('TOKEN_REUSED', window.sessionStorage, 1_000)).toBe(true)
    window.sessionStorage.clear()

    expect(storeSessionNotice('REFRESH_EXPIRED', window.sessionStorage, 1_500)).toBe(true)
    expect(consumeSessionNotice(window.sessionStorage, 1_600)).toBe('TOKEN_REUSED')
  })

  it('uses the newest reason when security causes have equal priority', () => {
    expect(storeSessionNotice('TOKEN_REUSED', window.sessionStorage, 1_000)).toBe(true)
    window.sessionStorage.clear()

    expect(storeSessionNotice('ACCOUNT_DISABLED', window.sessionStorage, 1_500)).toBe(true)
    expect(consumeSessionNotice(window.sessionStorage, 1_600)).toBe('ACCOUNT_DISABLED')
  })
})
