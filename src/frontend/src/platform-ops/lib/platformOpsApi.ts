import { api } from '../../lib/api'
import { unwrapApiPayload } from '../../lib/parse'
import type { GatewayChatWorkload } from '../../lib/lensApi'

async function get<T>(path: string, init?: RequestInit): Promise<T> {
  return unwrapApiPayload<T>(await api<unknown>(path, init))
}

async function send<T>(path: string, init: RequestInit): Promise<T> {
  return unwrapApiPayload<T>(await api<unknown>(path, init))
}

export interface PlatformEmailSettings {
  enterprise_identity_enabled?: boolean
  backend: string
  host: string
  port: number
  use_tls: boolean
  use_ssl: boolean
  host_user: string
  password_configured: boolean
  password_hint: string
  from_email: string
  delivery_configured: boolean
  configuration_error: string
  managed_by_deployment: boolean
  source: 'deployment' | 'runtime' | 'default'
  sources?: Record<string, string>
}

export interface PlatformIdentitySettings {
  /** False on Community empty socket; EE identity controls stay off. */
  enterprise_identity_enabled: boolean
  email_signup_enabled: boolean
  email_code_login_enabled: boolean
  platform_ops_enabled: boolean
  platform_ops_allowed_cidrs: string[]
  platform_ops_source?: 'deployment' | 'runtime' | 'default'
  turnstile_enabled: boolean
  turnstile_site_key: string
  turnstile_secret_configured: boolean
  google_client_id: string
  google_client_secret_configured: boolean
  google_oauth_enabled: boolean
  google_oauth_redirect_uri: string
  iam: {
    registration_verification_code_minutes: number
    registration_token_expiry_hours: number
    password_reset_verification_code_minutes: number
    password_reset_timeout_seconds: number
    login_verification_code_minutes: number
  }
}

export interface PlatformEnvironmentSettings {
  app_version: string | null
  agent_version: string | null
  django_debug: boolean
  effective: Record<string, unknown>
  sources: Record<string, string>
  health: Record<string, unknown>
}

/** Legacy Admin Console paths; OSS also serves `/api/v1/instance-settings/*`. */
export async function fetchPlatformEmailSettings() {
  return get<PlatformEmailSettings>('/api/v1/platform-ops/platform/settings/email')
}

export async function patchPlatformEmailSettings(body: Record<string, unknown>) {
  return send<PlatformEmailSettings>('/api/v1/platform-ops/platform/settings/email', {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export async function testPlatformEmail(recipient: string) {
  return send<{ ok: boolean; recipient?: string; error?: string }>(
    '/api/v1/platform-ops/platform/settings/email/test',
    { method: 'POST', body: JSON.stringify({ recipient }) },
  )
}

export async function fetchPlatformIdentitySettings() {
  return get<PlatformIdentitySettings>('/api/v1/platform-ops/platform/settings/identity')
}

export async function patchPlatformIdentitySettings(body: Record<string, unknown>) {
  return send<PlatformIdentitySettings>('/api/v1/platform-ops/platform/settings/identity', {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export async function fetchPlatformEnvironment() {
  return get<PlatformEnvironmentSettings>('/api/v1/platform-ops/platform/settings/environment')
}

/** Per–Public Gateway infrastructure capacity (EE Platform Ops). */
export type PlatformGatewayCapacity = {
  gateway_link_id: number
  gateway_id: number
  gateway_name: string
  capacity_bytes: number
  unlimited: boolean
  used_bytes: number
  used_incomplete: boolean
  limit_bytes: number | null
}

export async function fetchPublicGatewayCapacities(options?: { signal?: AbortSignal }) {
  return get<{ results: PlatformGatewayCapacity[] }>(
    '/api/v1/platform-ops/lens/gateways/capacity',
    { signal: options?.signal },
  )
}

export async function patchPublicGatewayCapacity(gatewayId: number, capacity_bytes: number) {
  return send<PlatformGatewayCapacity>(
    `/api/v1/platform-ops/lens/gateways/${gatewayId}/capacity`,
    {
      method: 'PATCH',
      body: JSON.stringify({ capacity_bytes }),
    },
  )
}

export async function fetchPublicGatewayChatWorkload(
  gatewayId: number,
  options?: { signal?: AbortSignal },
) {
  return get<GatewayChatWorkload>(
    `/api/v1/platform-ops/lens/gateways/${gatewayId}/chat-workload`,
    { signal: options?.signal },
  )
}

export async function patchPublicGatewayChatWorkload(
  gatewayId: number,
  settings: Pick<GatewayChatWorkload, 'chat_prepare_concurrency' | 'chat_queue_capacity'>,
) {
  return send<GatewayChatWorkload>(
    `/api/v1/platform-ops/lens/gateways/${gatewayId}/chat-workload`,
    {
      method: 'PATCH',
      body: JSON.stringify(settings),
    },
  )
}
