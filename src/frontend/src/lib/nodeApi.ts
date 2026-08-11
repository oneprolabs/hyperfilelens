import { getEffectiveOrgKey } from '../composables/useAuth'
import { api } from './api'
import { asList, extractEnrollmentToken, unwrapApiPayload } from './parse'
import { publishedAgentVersionLabel } from './agentVersion'
import type {
  NodeLifecycleKind,
  NodeOperationBatchPreview,
  NodeOperationBatchStartResult,
  NodeOperationStartResult,
} from '../types/nodeLifecycle'
import type { ApiNode, ApiNodeToken, CreateNodeTokenBody, NodeRole, NodeStatus, UpdateNodeBody } from '../types/node'

const API_BASE = import.meta.env.VITE_API_BASE?.toString() || ''

function orgKey(): string {
  return getEffectiveOrgKey()
}

/** Public API origin for enrollment scripts (same host as the console). */
export function publicApiBase(): string {
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin.replace(/\/$/, '')
  }
  return API_BASE.replace(/\/$/, '')
}

export async function listAllNodes(
  params?: { role?: NodeRole; status?: NodeStatus },
  init?: RequestInit,
): Promise<ApiNode[]> {
  const qs = new URLSearchParams()
  if (params?.role) qs.set('role', params.role)
  if (params?.status) qs.set('status', params.status)
  const path = qs.toString() ? `/api/v1/node/nodes/?${qs.toString()}` : '/api/v1/node/nodes/'
  const data = await api<unknown>(path, init)
  return asList<ApiNode>(data)
}

export async function listNodesPaged(
  params: {
    role?: NodeRole
    status?: NodeStatus
    page?: number
    page_size?: number
    search?: string
    search_field?: string
  },
  init?: RequestInit,
): Promise<{ count: number; results: ApiNode[] }> {
  const qs = new URLSearchParams()
  if (params.role) qs.set('role', params.role)
  if (params.status) qs.set('status', params.status)
  if (params.search?.trim()) qs.set('search', params.search.trim())
  if (params.search_field?.trim()) qs.set('search_field', params.search_field.trim())
  qs.set('page', String(params.page ?? 1))
  qs.set('page_size', String(params.page_size ?? 30))
  const path = `/api/v1/node/nodes/?${qs.toString()}`
  const data = await api<unknown>(path, init)
  const raw = unwrapApiPayload<Record<string, unknown>>(data)
  return {
    count: typeof raw.count === 'number' ? raw.count : asList<ApiNode>(raw).length,
    results: asList<ApiNode>(raw),
  }
}

/** @deprecated Prefer {@link listAllNodes} or {@link listNodesPaged}. */
export async function listNodes(
  params?: { role?: NodeRole; status?: NodeStatus; page?: number; page_size?: number; search?: string; search_field?: string },
  init?: RequestInit,
): Promise<ApiNode[]> {
  if (params?.page_size != null || params?.page != null) {
    const paged = await listNodesPaged(
      {
        role: params.role,
        status: params.status,
        page: params.page,
        page_size: params.page_size,
        search: params.search,
        search_field: params.search_field,
      },
      init,
    )
    return paged.results
  }
  return listAllNodes(params, init)
}

export async function getNode(nodeId: number, init?: RequestInit): Promise<ApiNode> {
  const raw = await api<unknown>(`/api/v1/node/nodes/${nodeId}/`, init)
  return unwrapApiPayload<ApiNode>(raw)
}

export type NodeApiScope = 'tenant' | 'platform'

export async function getGatewayNode(
  nodeId: number,
  scope: NodeApiScope,
  init?: RequestInit,
): Promise<ApiNode> {
  if (scope === 'tenant') return getNode(nodeId, init)
  const raw = await api<unknown>(`/api/v1/platform-ops/lens/gateways/${nodeId}`, init)
  return unwrapApiPayload<ApiNode>(raw)
}


export type NodeBindingsRepository = {
  id: number
  name: string
  status: string
  health: string
  config?: Record<string, unknown>
  nas_protocol?: string | null
  capacity_bytes?: number
  estimated_usage_bytes?: number
}

export type NodeBindingsSourceNas = {
  id: number
  name: string
  resource_type: string
  mount_status?: string
  mount_point?: string
  status?: string
  config?: Record<string, unknown>
}

export type NodeBindings = {
  proxy_id: number
  target_nas_repositories: NodeBindingsRepository[]
  standalone_disk_repositories: NodeBindingsRepository[]
  source_nas_resources: NodeBindingsSourceNas[]
  totals: {
    target_nas_repositories: number
    standalone_disk_repositories: number
    source_nas_resources: number
  }
}

export async function getNodeBindings(nodeId: number, init?: RequestInit): Promise<NodeBindings> {
  const raw = await api<unknown>(`/api/v1/node/nodes/${nodeId}/bindings/`, init)
  return unwrapApiPayload<NodeBindings>(raw)
}

export async function updateNode(
  nodeId: number,
  body: UpdateNodeBody,
  scope: NodeApiScope = 'tenant',
): Promise<ApiNode> {
  const path = scope === 'platform'
    ? `/api/v1/platform-ops/lens/gateways/${nodeId}`
    : `/api/v1/node/nodes/${nodeId}/`
  const raw = await api<unknown>(path, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
  return unwrapApiPayload<ApiNode>(raw)
}

export const NODE_LIFECYCLE_MAX_CONCURRENT = 5
export type NodeLifecycleScope = 'tenant' | 'platform'

function nodeLifecyclePath(scope: NodeLifecycleScope, relative: string): string {
  const clean = relative.replace(/^\/+|\/+$/g, '')
  if (scope === 'platform') {
    return `/api/v1/platform-ops/lens/gateways/${clean}`
  }
  return `/api/v1/node/nodes/${clean}/`
}

export class NodeLifecycleApiError extends Error {
  code: string
  blockers?: Array<Record<string, unknown>>

  constructor(message: string, code: string, blockers?: Array<Record<string, unknown>>) {
    super(message)
    this.name = 'NodeLifecycleApiError'
    this.code = code
    this.blockers = blockers
  }
}

function parseLifecycleError(raw: unknown): NodeLifecycleApiError {
  const payload = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const message = String(payload.error || payload.detail || 'Operation failed')
  const code = String(payload.code || 'lifecycle_rejected')
  const blockers = Array.isArray(payload.blockers) ? payload.blockers : undefined
  return new NodeLifecycleApiError(message, code, blockers)
}

async function postLifecycle<T>(path: string, body: unknown): Promise<T> {
  try {
    const raw = await api<unknown>(path, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    return unwrapApiPayload<T>(raw)
  } catch (e) {
    const err = e as { detail?: unknown; message?: string }
    const payload = err?.detail ?? e
    if (payload && typeof payload === 'object') {
      throw parseLifecycleError(payload)
    }
    throw e
  }
}

export async function startNodeOperation(
  nodeId: number,
  kind: NodeLifecycleKind,
  options?: { force?: boolean; scope?: NodeLifecycleScope },
): Promise<NodeOperationStartResult> {
  return postLifecycle(nodeLifecyclePath(options?.scope ?? 'tenant', `${nodeId}/operations`), {
    kind,
    force: Boolean(options?.force),
  })
}

export async function previewNodeOperationsBatch(params: {
  kind: NodeLifecycleKind
  nodeIds: number[]
  maxConcurrent?: number
  scope?: NodeLifecycleScope
}): Promise<NodeOperationBatchPreview> {
  return postLifecycle(nodeLifecyclePath(params.scope ?? 'tenant', 'operations/preview'), {
    kind: params.kind,
    node_ids: params.nodeIds,
    max_concurrent: params.maxConcurrent ?? NODE_LIFECYCLE_MAX_CONCURRENT,
  })
}

export async function startNodeOperationsBatch(params: {
  kind: NodeLifecycleKind
  nodeIds: number[]
  maxConcurrent?: number
  force?: boolean
  scope?: NodeLifecycleScope
}): Promise<NodeOperationBatchStartResult> {
  return postLifecycle(nodeLifecyclePath(params.scope ?? 'tenant', 'operations/batch'), {
    kind: params.kind,
    node_ids: params.nodeIds,
    max_concurrent: params.maxConcurrent ?? NODE_LIFECYCLE_MAX_CONCURRENT,
    force: Boolean(params.force),
  })
}

export type NodeLifecycleWatchEntry = Pick<
  ApiNode,
  'id' | 'status' | 'availability' | 'routable' | 'version' | 'lifecycle'
> & {
  is_deleted?: boolean
}

/** Poll lifecycle state for nodes in an active upgrade/remove batch (read-only). */
export async function fetchLifecycleWatch(
  nodeIds: number[],
  scope: NodeLifecycleScope = 'tenant',
): Promise<NodeLifecycleWatchEntry[]> {
  const ids = [...new Set(nodeIds.filter((id) => Number.isFinite(id) && id > 0))]
  if (ids.length === 0) return []
  const raw = await postLifecycle<{ nodes: NodeLifecycleWatchEntry[] }>(
    nodeLifecyclePath(scope, 'lifecycle-watch'),
    { node_ids: ids },
  )
  return Array.isArray(raw.nodes) ? raw.nodes : []
}

export async function deleteNode(nodeId: number): Promise<void> {
  await api<unknown>(`/api/v1/node/nodes/${nodeId}/`, { method: 'DELETE' })
}

export async function createNodeToken(body: CreateNodeTokenBody): Promise<ApiNodeToken> {
  const raw = await api<unknown>('/api/v1/node/node-tokens/', {
    method: 'POST',
    body: JSON.stringify(body),
  })
  const token = extractEnrollmentToken(raw)
  if (!token) {
    throw new Error('Enrollment token missing in API response')
  }
  const row = unwrapApiPayload<ApiNodeToken>(raw)
  return { ...row, token }
}

export async function getNodeToken(tokenId: number): Promise<ApiNodeToken> {
  const raw = await api<unknown>(`/api/v1/node/node-tokens/${tokenId}/`)
  return unwrapApiPayload<ApiNodeToken>(raw)
}

/** Create enrollment token for deploy / install one-liners. */
export async function createEnrollmentToken(params: {
  role: NodeRole
  note?: string
}): Promise<{ token: string; tokenId: number; tlsVerify: boolean }> {
  const org = orgKey()
  if (!org) {
    throw new Error('Missing organization key')
  }
  const raw = await api<unknown>('/api/v1/node/node-tokens/', {
    method: 'POST',
    body: JSON.stringify({
      role: params.role,
      note: params.note,
    }),
  })
  const token = extractEnrollmentToken(raw)
  if (!token) {
    throw new Error('Enrollment token missing in API response')
  }
  const row = unwrapApiPayload<ApiNodeToken>(raw)
  return {
    token,
    tokenId: row.id,
    tlsVerify: typeof row.tls_verify === 'boolean' ? row.tls_verify : true,
  }
}

export type EnrollmentOs = 'linux' | 'windows' | 'macos'

export type MinimalInstallerArtifact = {
  filename: string
  sha256: string
  size: number
}

export type MinimalInstallerManifest = {
  schema_version: number
  artifacts: Record<string, MinimalInstallerArtifact>
}

export async function fetchMinimalInstallerManifest(
  apiBase = publicApiBase(),
): Promise<MinimalInstallerManifest> {
  const base = apiBase.replace(/\/$/, '')
  const response = await fetch(`${base}/api/v1/node/enrollment/installer-metadata`, {
    cache: 'no-store',
  })
  if (!response.ok) {
    throw new Error('Minimal installer metadata is unavailable')
  }
  // Public fetch bypasses api(); still peel the standard { code, data } envelope.
  const payload = unwrapApiPayload<MinimalInstallerManifest>(await response.json())
  const expected = [
    'linux-amd64',
    'linux-arm64',
    'darwin-amd64',
    'darwin-arm64',
    'windows-amd64',
  ]
  const artifacts = payload?.artifacts
  const valid = payload?.schema_version === 1
    && artifacts
    && typeof artifacts === 'object'
    && Object.keys(artifacts).length === expected.length
    && expected.every((key) => {
      const artifact = artifacts[key]
      const extension = key.startsWith('windows-') ? 'zip' : 'tar.gz'
      const filenamePattern = new RegExp(
        `^[A-Za-z0-9._-]+/hfl-installer-${key}\\.${extension.replaceAll('.', '\\.')}$`,
      )
      return artifact
        && filenamePattern.test(artifact.filename)
        && /^[a-f0-9]{64}$/i.test(artifact.sha256)
        && Number.isSafeInteger(artifact.size)
        && artifact.size > 0
    })
  if (!valid) {
    throw new Error('Minimal installer metadata is invalid')
  }
  return payload
}

export function enrollmentDownloadType(os: EnrollmentOs): string {
  if (os === 'windows') return 'windows'
  if (os === 'macos') return 'macos'
  return 'linux'
}

/** Signed download URL for platform-specific enrollment installer. */
export function buildEnrollmentDownloadUrl(params: {
  org: string
  role: NodeRole
  token: string
  apiBase?: string
  os: EnrollmentOs
}): string {
  const type = enrollmentDownloadType(params.os)
  const qs = new URLSearchParams({
    type,
    org: params.org,
    role: params.role,
    token: params.token,
    api_base: params.apiBase ?? publicApiBase(),
  })
  const base = (params.apiBase ?? publicApiBase()).replace(/\/$/, '')
  return `${base}/api/v1/node/enrollment/bootstrap?${qs.toString()}`
}

/** Data Gateway bootstrap URL (agent + LensNode sidecar, Linux only). */
export function buildGatewayEnrollmentDownloadUrl(params: {
  org: string
  token: string
  apiBase?: string
}): string {
  const qs = new URLSearchParams({
    org: params.org,
    token: params.token,
    api_base: params.apiBase ?? publicApiBase(),
  })
  const base = (params.apiBase ?? publicApiBase()).replace(/\/$/, '')
  return `${base}/api/v1/node/enrollment/bootstrap-gateway?${qs.toString()}`
}

/** Escape a URL for use inside a PowerShell single-quoted string. */
function psSingleQuoted(value: string): string {
  return `'${value.replace(/'/g, "''")}'`
}

/**
 * Windows short command: download the rendered bootstrap stub, then run it.
 * Arch/checksum/download of the slim installer stays inside the bootstrap script.
 */
function buildWindowsEnrollmentInstallCommand(url: string, tlsVerify: boolean): string {
  const bootstrapPath =
    "[System.IO.Path]::Combine([System.IO.Path]::GetTempPath(),'hfl-bootstrap.ps1')"
  const psBody = [
    '[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12',
    ...(tlsVerify
      ? []
      : [
          "Write-Warning 'TLS certificate verification is disabled. Use only on a trusted private network.'",
          '[Net.ServicePointManager]::ServerCertificateValidationCallback={[bool]1}',
        ]),
    `(New-Object Net.WebClient).DownloadFile(${psSingleQuoted(url)},${bootstrapPath})`,
    `& (${bootstrapPath})`,
  ].join(';')
  return `powershell -NoProfile -ExecutionPolicy Bypass -Command "${psBody}"`
}

/**
 * POSIX one-liner: curl the rendered bootstrap stub into sudo bash.
 * The bootstrap script downloads one slim enroll helper and runs install.
 *
 * --chdir avoids getcwd noise when the caller is sitting in a deleted install dir.
 * No --progress-bar here: the bootstrap stub is tiny; real download progress comes later.
 */
function buildPosixEnrollmentInstallCommand(url: string, tlsVerify: boolean): string {
  const tlsOptions = tlsVerify
    ? "--proto '=https' --tlsv1.2"
    : '-k'
  return `curl ${tlsOptions} --fail --show-error --location '${url}' | sudo bash -c 'cd / || cd /tmp; exec bash -s'`
}

/** Short copy-paste command for the target host. Shown on deploy pages only. */
export function buildEnrollmentInstallCommand(params: {
  org: string
  role: NodeRole
  token: string
  apiBase?: string
  os: EnrollmentOs
  tlsVerify?: boolean
}): string {
  const url = buildEnrollmentDownloadUrl({ ...params, os: params.os })
  const tlsVerify = params.tlsVerify !== false
  if (params.os === 'windows') {
    return buildWindowsEnrollmentInstallCommand(url, tlsVerify)
  }
  return buildPosixEnrollmentInstallCommand(url, tlsVerify)
}

/** Short copy-paste command for Data Gateway hosts (Linux). */
export function buildGatewayEnrollmentInstallCommand(params: {
  org: string
  token: string
  apiBase?: string
  tlsVerify?: boolean
}): string {
  const url = buildGatewayEnrollmentDownloadUrl(params)
  return buildPosixEnrollmentInstallCommand(url, params.tlsVerify !== false)
}

/** Create gateway token + build copy-paste install command. */
export async function issueGatewayEnrollmentInstall(params: {
  note?: string
  orgKey?: string
}): Promise<{ token: string; tokenId: number; command: string; tlsVerify: boolean; expiresAt: string | null }> {
  const org = params.orgKey || orgKey()
  if (!org) {
    throw new Error('Missing organization key')
  }
  const row = await createNodeToken({
    role: 'gateway',
    note: params.note ?? 'deploy:gateway',
  })
  const command = buildGatewayEnrollmentInstallCommand({
    org,
    token: row.token,
    tlsVerify: row.tls_verify,
  })
  return {
    token: row.token,
    tokenId: row.id,
    command,
    tlsVerify: row.tls_verify,
    expiresAt: row.expires_at ?? null,
  }
}

export async function issuePlatformGatewayEnrollmentInstall(params?: {
  note?: string
}): Promise<{
  token: string
  tokenId: number
  command: string
  tlsVerify: boolean
  expiresAt: string | null
  orgKey: string
  apiBase: string
}> {
  const raw = await api<unknown>('/api/v1/platform-ops/lens/gateways/enrollment', {
    method: 'POST',
    body: JSON.stringify({
      note: params?.note ?? 'deploy:platform-gateway',
    }),
  })
  const payload = unwrapApiPayload<{
    token: string
    token_id: number
    org_key: string
    api_base: string
    tls_verify: boolean
    expires_at?: string | null
  }>(raw)
  try {
    if (
      !payload.token ||
      !payload.org_key ||
      !payload.api_base ||
      typeof payload.tls_verify !== 'boolean'
    ) {
      throw new Error('Public Data Gateway enrollment response is incomplete')
    }
    return {
      token: payload.token,
      tokenId: payload.token_id,
      command: buildGatewayEnrollmentInstallCommand({
        org: payload.org_key,
        token: payload.token,
        apiBase: payload.api_base,
        tlsVerify: payload.tls_verify,
      }),
      tlsVerify: payload.tls_verify,
      expiresAt: payload.expires_at ?? null,
      orgKey: payload.org_key,
      apiBase: payload.api_base,
    }
  } catch (error) {
    if (Number.isInteger(payload.token_id) && payload.token_id > 0) {
      await revokePlatformGatewayEnrollment(payload.token_id).catch(() => undefined)
    }
    throw error
  }
}

export async function revokePlatformGatewayEnrollment(tokenId: number): Promise<void> {
  await api(`/api/v1/platform-ops/lens/gateways/enrollment/${tokenId}`, {
    method: 'DELETE',
  })
}

export async function revokeEnrollmentToken(tokenId: number): Promise<void> {
  await api(`/api/v1/node/node-tokens/${tokenId}/`, { method: 'DELETE' })
}

export async function auditPlatformGatewayEnrollmentCopy(tokenId: number): Promise<void> {
  await api(`/api/v1/platform-ops/lens/gateways/enrollment/${tokenId}/copied`, {
    method: 'POST',
  })
}

/** Create token + build copy-paste install command (does not download script body). */
export async function issueEnrollmentInstall(params: {
  role: NodeRole
  os: EnrollmentOs
  note?: string
}): Promise<{ token: string; tokenId: number; command: string; tlsVerify: boolean; expiresAt: string | null }> {
  const org = orgKey()
  if (!org) {
    throw new Error('Missing organization key')
  }
  const row = await createNodeToken({ role: params.role, note: params.note })
  const command = buildEnrollmentInstallCommand({
    org,
    role: params.role,
    token: row.token,
    os: params.os,
    tlsVerify: row.tls_verify,
  })
  return {
    token: row.token,
    tokenId: row.id,
    command,
    tlsVerify: row.tls_verify,
    expiresAt: row.expires_at ?? null,
  }
}

import { formatAppTime } from './dateTime'

export function formatLogTime(d = new Date()): string {
  return formatAppTime(d, '')
}

export interface NodeTaskRecord {
  id: string
  status: string
  kind?: string
  result?: Record<string, unknown>
  message?: Record<string, unknown> | string
}

export function formatNodeTaskFailure(
  outcome: NodeTaskRecord & { timed_out?: boolean },
  fallback: string,
): string {
  const raw = outcome.message
  if (raw && typeof raw === 'object') {
    const err = String(raw.error || raw.message || '').trim()
    if (err) return err
  }
  if (typeof raw === 'string' && raw.trim()) return raw.trim()
  return fallback
}

/** Dispatch a runtime task to a connected Agent (WSS task.command). */
export async function dispatchNodeTask(params: {
  nodeId: number
  kind: string
  payload?: Record<string, unknown>
}): Promise<NodeTaskRecord> {
  const raw = await api<unknown>('/api/v1/node/node-tasks/', {
    method: 'POST',
    body: JSON.stringify({
      node_id: params.nodeId,
      kind: params.kind,
      payload: params.payload ?? {},
    }),
  })
  return unwrapApiPayload<NodeTaskRecord>(raw)
}

/** Poll task until terminal status or timeout. */
export async function waitForNodeTask(taskId: string, timeoutSec = 120): Promise<NodeTaskRecord & { timed_out?: boolean }> {
  const raw = await api<unknown>(`/api/v1/node/node-tasks/${taskId}/wait/?timeout=${timeoutSec}`)
  return unwrapApiPayload(raw) as NodeTaskRecord & { timed_out?: boolean }
}

export interface AgentReleaseInfo {
  version: string
  platform: string
  arch: string
  download_url: string
  expires_in?: number
  tls_verify?: boolean
}

/** Resolve a signed package URL for an existing node without minting install credentials. */
export async function fetchNodeMaintenanceRelease(params: {
  nodeId: number
  scope?: NodeLifecycleScope
}): Promise<AgentReleaseInfo> {
  const path = params.scope === 'platform'
    ? `/api/v1/platform-ops/lens/gateways/${params.nodeId}/maintenance-release`
    : `/api/v1/node/nodes/${params.nodeId}/maintenance-release/`
  const raw = await api<unknown>(path, { method: 'POST', body: JSON.stringify({}) })
  const data = unwrapApiPayload<AgentReleaseInfo>(raw)
  if (!data.download_url) throw new Error('Release download_url missing in API response')
  return data
}

/** Resolve signed agent package download URL (enrollment token required). */
export async function fetchAgentRelease(params: {
  role: NodeRole
  token: string
  os: EnrollmentOs
  arch?: 'amd64' | 'arm64'
  orgKey?: string
  apiBase?: string
}): Promise<AgentReleaseInfo> {
  const org = params.orgKey || orgKey()
  if (!org) throw new Error('Missing organization key')
  const arch = params.arch ?? 'amd64'
  const platform = params.os === 'windows' ? 'windows' : params.os === 'macos' ? 'darwin' : 'linux'
  const qs = new URLSearchParams({
    org,
    role: params.role,
    token: params.token,
    platform,
    arch,
    api_base: params.apiBase || publicApiBase(),
  })
  const raw = await api<unknown>(`/api/v1/node/enrollment/agent/release?${qs.toString()}`)
  const data = unwrapApiPayload<AgentReleaseInfo>(raw)
  if (!data.download_url) {
    throw new Error('Release download_url missing in API response')
  }
  return data
}

/** Published agent semver from media/agent-releases (console upgrade target). */
export async function fetchLatestAgentVersion(init?: RequestInit): Promise<string | null> {
  const raw = await api<unknown>('/api/v1/node/agent-release/latest', init)
  const data = unwrapApiPayload<{ version?: string }>(raw)
  return publishedAgentVersionLabel(data.version) || null
}

/** @deprecated Use startNodeOperation(nodeId, 'upgrade') with useNodeLifecycleOps. */
export async function upgradeNodeRemote(nodeId: number) {
  const result = await startNodeOperation(nodeId, 'upgrade')
  return { task: { id: result.task_id || result.operation_id }, outcome: { status: result.state } }
}

/** @deprecated Use startNodeOperation(nodeId, 'remove') with useNodeLifecycleOps. */
export async function removeAgentNode(nodeId: number) {
  await startNodeOperation(nodeId, 'remove')
}
