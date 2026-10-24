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
  has_runtime_override?: boolean
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
  turnstile_source?: 'runtime' | 'deployment' | 'default' | 'extension'
  turnstile_site_key: string
  turnstile_secret_configured: boolean
  turnstile_ip_allowlist?: string[]
  google_client_id: string
  google_client_secret_configured: boolean
  google_oauth_enabled: boolean
  google_oauth_redirect_uri: string
  has_runtime_override?: boolean
  iam: {
    registration_verification_code_minutes: number
    registration_token_expiry_minutes: number
    password_reset_verification_code_minutes: number
    password_reset_timeout_minutes: number
    login_verification_code_minutes: number
  }
}

export interface PlatformEnvironmentSettings {
  app_version: string | null
  agent_version: string | null
  edition?: string
  django_debug: boolean
  effective: Record<string, unknown>
  sources: Record<string, string>
  health: Record<string, unknown>
}

export interface PlatformExternalAccessSettings {
  external_access_url: string
  effective_url: string
  source: 'deployment' | 'runtime' | 'default'
  has_runtime_override?: boolean
  suggested_url: string
  editable: boolean
  product_website_url?: string
  default_product_website_url?: string
}

/** Deployment-managed services shown in Runtime Environment. */
export interface SourceLensRuntimeProbe {
  queue_lengths?: Record<string, number>
  checked_at?: string
  health_status: string
  availability_status: string
  notices: Array<{
    code: string
    level: 'info' | 'warning' | 'error'
    params: Record<string, string | number>
  }>
}

export interface SourceLensRuntimeMonitor {
  status: string
  health_status: string
  checked_at: string
  components: Record<string, SourceLensRuntimeProbe>
}

export interface PlatformIntegration {
  key: string
  name: string
  category: string
  mode: string
  version: string
  base_url: string
  gateway_base_url: string
  console_url: string
  configured: boolean
  reachable: boolean
  authenticated: boolean
  business_ready?: boolean
  status?: string
  warning?: string
  managed_by: string
  checked_at: string
  runtime_monitor?: SourceLensRuntimeMonitor
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

export async function fetchPlatformExternalAccess() {
  return get<PlatformExternalAccessSettings>(
    '/api/v1/platform-ops/platform/settings/external-access',
  )
}

export async function patchPlatformExternalAccess(
  externalAccessUrl: string | null | undefined,
  productWebsiteUrl?: string,
) {
  return send<PlatformExternalAccessSettings>(
    '/api/v1/platform-ops/platform/settings/external-access',
    {
      method: 'PATCH',
      body: JSON.stringify({
        ...(externalAccessUrl !== undefined ? { external_access_url: externalAccessUrl } : {}),
        ...(productWebsiteUrl !== undefined ? { product_website_url: productWebsiteUrl } : {}),
      }),
    },
  )
}

export async function fetchPlatformIntegrations() {
  return get<{ integrations: PlatformIntegration[] }>('/api/v1/instance-settings/integrations')
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

/** Platform-owned notification channel (Admin Console Alerting). */
export type PlatformNotificationChannel = {
  id: number
  owner_scope?: string
  organization_id?: number | null
  organization_key?: string
  organization_name?: string
  name: string
  channel_type: string
  is_active: boolean
  target?: string
  config?: Record<string, unknown>
  delivery_count?: number
  created_at?: string
  updated_at?: string
}

export async function fetchPlatformNotificationChannel(channelId: number) {
  return get<PlatformNotificationChannel>(
    `/api/v1/platform-ops/platform/notification-channels/${channelId}`,
  )
}

export async function createPlatformNotificationChannel(body: Record<string, unknown>) {
  return send<PlatformNotificationChannel>(
    '/api/v1/platform-ops/platform/notification-channels',
    { method: 'POST', body: JSON.stringify(body) },
  )
}

export async function updatePlatformNotificationChannel(
  channelId: number,
  body: Record<string, unknown>,
) {
  return send<PlatformNotificationChannel>(
    `/api/v1/platform-ops/platform/notification-channels/${channelId}`,
    { method: 'PATCH', body: JSON.stringify(body) },
  )
}

export async function deletePlatformNotificationChannel(channelId: number) {
  return send<void>(
    `/api/v1/platform-ops/platform/notification-channels/${channelId}`,
    { method: 'DELETE' },
  )
}

export async function testPlatformNotificationChannelConfig(body: Record<string, unknown>) {
  return send<{ status: string; error?: string }>(
    '/api/v1/platform-ops/platform/notification-channels/test-config',
    { method: 'POST', body: JSON.stringify(body) },
  )
}

export async function fetchPlatformAlertPolicy(policyId: string) {
  return get<Record<string, unknown>>(
    `/api/v1/platform-ops/platform/alert-policies/${policyId}`,
  )
}

export async function createPlatformAlertPolicy(body: Record<string, unknown>) {
  return send<Record<string, unknown>>(
    '/api/v1/platform-ops/platform/alert-policies',
    { method: 'POST', body: JSON.stringify(body) },
  )
}

export async function updatePlatformAlertPolicy(policyId: string, body: Record<string, unknown>) {
  return send<Record<string, unknown>>(
    `/api/v1/platform-ops/platform/alert-policies/${policyId}`,
    { method: 'PATCH', body: JSON.stringify(body) },
  )
}

export async function deletePlatformAlertPolicy(policyId: string) {
  return send<void>(
    `/api/v1/platform-ops/platform/alert-policies/${policyId}`,
    { method: 'DELETE' },
  )
}

export async function fetchPlatformAlertMetadataResources(resourceType: string) {
  const data = await get<{ results: Array<{ id: string; name: string; status?: string }> }>(
    `/api/v1/platform-ops/platform/alert-policies/metadata/resources?resource_type=${encodeURIComponent(resourceType)}`,
  )
  return data.results || []
}

export async function listPlatformNotificationChannelOptions() {
  const data = await get<{ results: Array<{ id: number; name: string; channel_type: string; is_active: boolean }> }>(
    '/api/v1/platform-ops/platform/notification-channels?page=1&page_size=200&enabled=true',
  )
  return data.results || []
}
