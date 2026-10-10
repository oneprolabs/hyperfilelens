import { api } from './api'

export interface EmailCodeLoginData {
  user?: { id: number; email: string; username: string; is_staff?: boolean }
  roles?: string[]
  available_orgs?: Array<{ org_key: string; org_name: string; role: string }>
  message?: string
}

interface EmailCodeSendResponse {
  code: string
  data: {
    message: string
    retry_after: number
    expires_in: number
  }
}

interface EmailCodeVerifyResponse {
  code: string
  data: EmailCodeLoginData
}

export function sendEmailLoginCode(
  email: string,
  signal?: AbortSignal,
  turnstilePayload: Record<string, string> = {},
) {
  return api<EmailCodeSendResponse>('/api/v1/auth/email-code-login/send-code', {
    method: 'POST',
    body: JSON.stringify({ email, ...turnstilePayload }),
    signal,
  })
}

export function verifyEmailLoginCode(email: string, code: string, signal?: AbortSignal) {
  return api<EmailCodeVerifyResponse>('/api/v1/auth/email-code-login/verify', {
    method: 'POST',
    body: JSON.stringify({ email, code }),
    signal,
  })
}
