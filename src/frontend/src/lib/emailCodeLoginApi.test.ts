import { beforeEach, describe, expect, it, vi } from 'vitest'
import { sendEmailLoginCode, verifyEmailLoginCode } from './emailCodeLoginApi'

const { api } = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('./api', () => ({ api }))

describe('email code login API', () => {
  beforeEach(() => api.mockReset())

  it('includes verification only when supplied for sending', () => {
    const signal = new AbortController().signal
    sendEmailLoginCode('person@example.com', signal, { turnstile_token: 'token' })
    expect(api).toHaveBeenCalledWith('/api/v1/auth/email-code-login/send-code', {
      method: 'POST',
      body: JSON.stringify({ email: 'person@example.com', turnstile_token: 'token' }),
      signal,
    })
    sendEmailLoginCode('person@example.com', signal)
    expect(api).toHaveBeenLastCalledWith('/api/v1/auth/email-code-login/send-code', {
      method: 'POST',
      body: JSON.stringify({ email: 'person@example.com' }),
      signal,
    })
  })

  it('keeps code verification free of a second Turnstile token', () => {
    verifyEmailLoginCode('person@example.com', '123456')
    expect(api).toHaveBeenCalledWith('/api/v1/auth/email-code-login/verify', {
      method: 'POST',
      body: JSON.stringify({ email: 'person@example.com', code: '123456' }),
      signal: undefined,
    })
  })
})
