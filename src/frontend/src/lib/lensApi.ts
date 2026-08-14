import { getEffectiveOrgKey } from '../composables/useAuth'
import { api, isAbortError } from './api'
import type { DocumentConversion, SessionDataContext } from './conversionSummary'
import type { BackupSnapshotBrowserEntry } from './protectionBackupConfigApi'
import { asList, unwrapApiPayload } from './parse'

const API_BASE = import.meta.env.VITE_API_BASE?.toString() || ''
const COPILOT_SNAPSHOT_BROWSE_POLL_MS = 500
const COPILOT_SNAPSHOT_BROWSE_MAX_POLLS = 240

export type LensApiScope = 'tenant' | 'platform'

let lensApiScope: LensApiScope = 'tenant'

export function setLensApiScope(scope: LensApiScope) {
  lensApiScope = scope
}

export function getLensApiScope(): LensApiScope {
  return lensApiScope
}

function lensUrl(relative: string): string {
  const clean = relative.replace(/^\//, '')
  if (lensApiScope === 'platform') {
    // platform-ops routes are registered without a trailing slash.
    const noTrailing = clean.replace(/\/+$/, '')
    return `/api/v1/platform-ops/lens/${noTrailing}`
  }
  return `/api/v1/lens/${clean}`
}

function lensHeaders(extra?: Record<string, string>): Record<string, string> {
  if (lensApiScope === 'platform') {
    return { ...(extra || {}) }
  }
  return orgHeaders(extra)
}

function orgHeaders(extra?: Record<string, string>): Record<string, string> {
  return {
    'X-Org-Key': getEffectiveOrgKey(),
    ...(extra || {}),
  }
}

function lensPayload<T>(raw: unknown): T {
  return unwrapApiPayload<T>(raw)
}

function lensList<T>(raw: unknown): T[] {
  return asList<T>(unwrapApiPayload(raw))
}

export type LensHealth = {
  app: string
  status: string
  lens: {
    configured: boolean
    reachable: boolean
    authenticated?: boolean
    base_url?: string
  }
}

export type LensLlmConfig = {
  uuid: string
  provider?: string
  name?: string
  config?: {
    model?: string
    api_base?: string
    api_key?: string
  }
  is_active?: boolean
  is_default?: boolean
  deployment_managed?: boolean
  deployment_role?: '' | 'agent' | 'multimodal'
  is_deployment_history?: boolean
  is_default_agent?: boolean
  is_default_multimodal?: boolean
  order?: number
  scope?: string
}

export type LensCopilotReadiness = {
  active_models: LensLlmConfig[]
  default_agent_model_ref: string | null
  default_multimodal_model_ref: string | null
}

export type LensCopilotUsageSummary = {
  estimated_cost: number | null
  cost_currency: string
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number
  reasoning_tokens: number
  model_calls: number
  q_and_a_requests: number
  average_cost_per_q_and_a: number | null
}

export type LensCopilotUsageTrend = {
  bucket: string | null
  total_calls: number
  total_prompt_tokens: number
  total_completion_tokens: number
  total_tokens: number
  total_cached_tokens: number
  total_reasoning_tokens: number
  total_cost: number | null
}

export type LensCopilotUsageItem = {
  run_uuid: string
  time: string
  chat_id: number | null
  chat_title: string
  chat_available: boolean
  backup_source_name: string
  scope_summary: string
  question: string
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number
  reasoning_tokens: number
  total_tokens: number
  model_calls: number
  estimated_cost: number | null
  cost_currency: string
  status: string
}

export type LensCopilotUsageOverview = {
  period: { start_date: string; end_date: string }
  summary: LensCopilotUsageSummary
  trend: LensCopilotUsageTrend[]
  by_backup_source: Array<{
    backup_source_name: string
    q_and_a_requests: number
    model_calls: number
    total_tokens: number
    estimated_cost: number | null
  }>
  backup_sources?: string[]
  results: LensCopilotUsageItem[]
  total: number
  page: number
  page_size: number
  data_freshness: {
    last_source_sync_at: string | null
    pending_runs: number
  }
}

export type LensCopilotUsageDetail = LensCopilotUsageItem & {
  snapshot_created_at: string | null
  source_scopes: Array<{
    source_path: string
    backup_snapshot_directory_id?: number
    path_type?: 'dir' | 'file' | 'unknown'
  }>
  gateway_mode: 'auto' | 'manual' | string
  gateway_name: string
  started_at: string | null
  finished_at: string | null
  error: string
  call_details: Array<{
    call: number
    prompt_tokens: number
    completion_tokens: number
    cached_tokens: number
    reasoning_tokens: number
    total_tokens: number
    estimated_cost: number | null
  }>
}

export type LensIngestPolicy = {
  document: boolean
  embedded_image: boolean
  image: boolean
  document_model_ref?: string | null
  vision_model_ref?: string | null
  max_images: number
  max_file_size_mb: number
  max_pages: number
  pdf_extract_images: boolean
  pdf_extract_images_on_text_pages: boolean
  pdf_render_scanned_pages: boolean
  pdf_max_pages: number
  pdf_max_images_per_page: number
  pdf_render_dpi: number
  pdf_min_text_chars: number
  pdf_min_image_area_ratio: number
}

export type LensKnowledgeSource = {
  id: number
  name: string
  gateway: number
  gateway_name: string
  backup_source_snapshot_id: number | null
  backup_snapshot_directory_id: number | null
  source_path: string
  source_scopes_json?: KnowledgeSourceScope[]
  mount_path_on_gateway: string
  workspace_path_on_lensnode: string
  linked_version_mode: 'latest' | 'pinned'
  pinned_snapshot_id: number | null
  sl_assistant_uuid: string | null
  sl_datasource_uuid: string | null
  sl_lensnode_uuid: string | null
  status: 'syncing' | 'ready' | 'degraded' | 'error' | 'paused' | string
  status_detail: string
  sync_phase?: string
  sync_state_json?: Record<string, unknown>
  document_conversion?: DocumentConversion | null
  last_restore_record_id?: number | null
  ingest_policy?: LensIngestPolicy
  ingest_summary?: string
  scan_enabled: boolean
  created_at: string
  updated_at: string
}

export type LensGatewayInsight = {
  id: number
  name: string
  role: string
  status: string
  ip_address: string | null
  ai_enabled: boolean
  sl_lensnode_uuid: string | null
  lensnode_status: string | null
  knowledge_source_count: number
  workspace_root: string
  sidecar_status: string
  scope?: string
  origin?: 'user' | 'platform' | 'external' | 'system' | string
  gateway_link_id?: number | null
  managed_by_hfl?: boolean
  hfl_agent_online?: boolean
  hfl_sidecar_online?: boolean
  hfl_usable?: boolean
  copilot_eligible?: boolean
  sl_runtime_status?: string
  owner_user_id?: number | null
  owner_username?: string
  owner_organization_id?: number | null
  is_platform_default?: boolean
  organization?: number
  metadata?: Record<string, unknown>
  version?: string | null
  os_name?: string
  created_at?: string
  updated_at?: string
  routable?: boolean
  lifecycle?: unknown
  workload?: unknown
  sl_name?: string
  sl_status?: string
  sl_workspace_path?: string
  sl_agent_version?: string
  sl_last_heartbeat_at?: string | null
  sl_registered_at?: string | null
  sl_tasks?: { name: string; title: string }[]
}

export type SlLensnodeTask = {
  name: string
  title: string
}

export type LensAssistant = {
  uuid: string
  name: string
  slug: string
  status: string
  visibility_scope?: 'user' | 'organization'
  lensnode_uuid?: string | null
  selected_task?: string
  selected_dir?: string
  agent_model_ref?: string | null
  multimodal_model_ref?: string | null
  supports_document_attachments?: boolean
  agent_rounds?: string
  knowledge_source_id?: number | null
  knowledge_source_name?: string | null
  knowledge_source_status?: string | null
  gateway_name?: string | null
}

export type LensCopilotAssistant = LensAssistant & {
  gateway_name?: string
  source_path?: string
}

export type LensSessionLink = {
  id: number
  title: string
  knowledge_source: number | null
  knowledge_source_name: string | null
  sl_session_uuid: string | null
  sl_assistant_uuid: string | null
  assistant_name?: string | null
  selected_task?: string | null
  agent_model_ref: string | null
  multimodal_model_ref?: string | null
  backup_config_id: number | null
  backup_source_name: string | null
  backup_source_snapshot_id: number | null
  snapshot_created_at: string | null
  snapshot_size_bytes: number | null
  source_scopes_json: Array<{
    backup_snapshot_directory_id: number
    source_path: string
    path_type?: 'dir' | 'file' | 'unknown'
  }>
  gateway_link: number | null
  gateway_selection_mode: 'auto' | 'manual' | string
  gateway_name: string | null
  gateway_scope: string | null
  status: string
  lifecycle_status?: 'provisioning' | 'ready' | 'failed' | 'deleting' | 'deleted' | string
  lifecycle_error?: string
  provision_phase?: string
  provision_detail?: string
  document_conversion?: DocumentConversion | null
  data_context?: SessionDataContext | null
  last_message_at: string | null
  last_assistant_message_at: string | null
  last_viewed_at: string | null
  has_unread: boolean
  pinned_at?: string | null
  active_run_uuid?: string | null
  active_run_status?: string | null
  created_at: string
  updated_at: string
}

export type LensCopilotActiveRun = {
  uuid: string
  status: string
  partial_content?: string
  thinking?: LensChatThinkingStep[]
  error?: string
  started_at?: string | null
  elapsed_anchor_at?: string | null
}

export type LensCopilotResponseState = {
  status: 'idle' | 'submitting' | 'running'
  started_at?: string | null
  question?: string
}

export type LensCopilotSyncResponse = {
  session_id: number
  messages: LensChatMessage[]
  active_run: LensCopilotActiveRun | null
  response_state?: LensCopilotResponseState
  run_outcomes: LensCopilotRunOutcome[]
  last_assistant_message_at?: string | null
  has_unread?: boolean
}

export type LensCopilotRunOutcome = {
  run_uuid: string
  status: 'failed' | 'cancelled' | string
  error_code: string
  message: string
  finished_at?: string | null
}

export type LensRun = {
  uuid: string
  status: string
  error?: string
}

export type LensChatThinkingStep = {
  agent_event?: string
  activity?: string
  message?: string
}

export type LensChatAttachment = {
  uuid: string
  url?: string
  kind: 'image' | 'document' | string
  mime_type?: string
  width?: number | null
  height?: number | null
  byte_size?: number
  original_name?: string
  order?: number
  expires_at?: string
}

export type LensChatMessage = {
  uuid?: string
  role: 'user' | 'assistant' | 'system'
  content?: string
  sequence?: number
  run?: string
  created_at?: string
  thinking?: {
    duration_seconds?: number | null
    steps?: LensChatThinkingStep[]
  }
  attachments?: LensChatAttachment[]
}

export async function fetchLensHealth(): Promise<LensHealth> {
  // Bridge connectivity is global — always use the tenant lens_bridge health endpoint,
  // even when Engine UI is in platform API scope.
  return api<LensHealth>('/api/v1/lens/health', { headers: orgHeaders() })
}

export async function listLensModels(): Promise<LensLlmConfig[]> {
  const raw = await api(lensUrl('models/'), { headers: lensHeaders() })
  return lensList<LensLlmConfig>(raw)
}

export async function fetchCopilotReadiness(): Promise<LensCopilotReadiness> {
  const raw = await api(lensUrl('copilot/readiness/'), { headers: lensHeaders() })
  return lensPayload<LensCopilotReadiness>(raw)
}

export type LensOrgSettings = {
  default_agent_model_ref: string | null
  default_multimodal_model_ref: string | null
}

export async function fetchLensOrgSettings(): Promise<LensOrgSettings> {
  const raw = await api(lensUrl('settings/'), { headers: lensHeaders() })
  return lensPayload<LensOrgSettings>(raw)
}

export async function setLensDefaultAgentModel(modelRef: string | null): Promise<LensOrgSettings> {
  const raw = await api(lensUrl('settings/'), {
    method: 'PATCH',
    headers: lensHeaders(),
    body: JSON.stringify({ default_agent_model_ref: modelRef }),
  })
  return lensPayload<LensOrgSettings>(raw)
}

export async function setLensDefaultMultimodalModel(modelRef: string | null): Promise<LensOrgSettings> {
  const raw = await api(lensUrl('settings/'), {
    method: 'PATCH',
    headers: lensHeaders(),
    body: JSON.stringify({ default_multimodal_model_ref: modelRef }),
  })
  return lensPayload<LensOrgSettings>(raw)
}

export async function fetchLensModelProviders(): Promise<unknown> {
  const raw = await api(lensUrl('models/providers/'), { headers: lensHeaders() })
  return lensPayload(raw)
}

export async function fetchLensModelCatalog(): Promise<unknown> {
  const raw = await api(lensUrl('models/catalog/'), { headers: lensHeaders() })
  return lensPayload(raw)
}

export async function fetchLensModelDetail(uuid: string): Promise<LensLlmConfig> {
  const raw = await api(lensUrl(`models/${uuid}/`), { headers: lensHeaders() })
  return lensPayload<LensLlmConfig>(raw)
}

export async function createLensModel(body: Record<string, unknown>): Promise<LensLlmConfig> {
  const raw = await api(lensUrl('models/'), {
    method: 'POST',
    headers: lensHeaders(),
    body: JSON.stringify(body),
  })
  return lensPayload<LensLlmConfig>(raw)
}

export async function updateLensModel(uuid: string, body: Record<string, unknown>): Promise<LensLlmConfig> {
  const raw = await api(lensUrl(`models/${uuid}/`), {
    method: 'PUT',
    headers: lensHeaders(),
    body: JSON.stringify(body),
  })
  return lensPayload<LensLlmConfig>(raw)
}

export async function patchLensModel(uuid: string, body: Record<string, unknown>): Promise<LensLlmConfig> {
  const raw = await api(lensUrl(`models/${uuid}/`), {
    method: 'PATCH',
    headers: lensHeaders(),
    body: JSON.stringify(body),
  })
  return lensPayload<LensLlmConfig>(raw)
}

export async function deleteLensModel(uuid: string): Promise<void> {
  await api(lensUrl(`models/${uuid}/`), {
    method: 'DELETE',
    headers: lensHeaders(),
  })
}

export async function testLensModel(body: Record<string, unknown>): Promise<unknown> {
  const raw = await api(lensUrl('models/test/'), {
    method: 'POST',
    headers: lensHeaders(),
    body: JSON.stringify(body),
  })
  return lensPayload(raw)
}

export async function testSavedLensModel(uuid: string): Promise<unknown> {
  const raw = await api(lensUrl(`models/${uuid}/test-call/`), {
    method: 'POST',
    headers: lensHeaders(),
    body: JSON.stringify({}),
  })
  return lensPayload(raw)
}

export async function listKnowledgeSources(): Promise<LensKnowledgeSource[]> {
  const raw = await api(lensUrl('knowledge-sources/'), { headers: lensHeaders() })
  return lensList<LensKnowledgeSource>(raw)
}

export type KnowledgeSourceScope = {
  source_path: string
  backup_snapshot_directory_id: number
}

function knowledgeSourceRequestBody<
  T extends { ingest_policy?: LensIngestPolicy },
>(body: T): T {
  if (!body.ingest_policy) return body
  const tenantPolicy = { ...body.ingest_policy }
  delete tenantPolicy.document_model_ref
  delete tenantPolicy.vision_model_ref
  return { ...body, ingest_policy: tenantPolicy } as T
}

export async function createKnowledgeSource(body: {
  name: string
  gateway: number
  source_path: string
  backup_source_snapshot_id?: number | null
  backup_snapshot_directory_id?: number | null
  source_scopes?: KnowledgeSourceScope[]
  linked_version_mode?: 'latest' | 'pinned'
  pinned_snapshot_id?: number | null
  scan_enabled?: boolean
  ingest_policy?: LensIngestPolicy
}): Promise<LensKnowledgeSource> {
  const raw = await api(lensUrl('knowledge-sources/'), {
    method: 'POST',
    headers: lensHeaders(),
    body: JSON.stringify(knowledgeSourceRequestBody(body)),
  })
  return lensPayload<LensKnowledgeSource>(raw)
}

export async function fetchKnowledgeSource(id: number): Promise<LensKnowledgeSource> {
  const raw = await api(lensUrl(`knowledge-sources/${id}/`), { headers: lensHeaders() })
  return lensPayload<LensKnowledgeSource>(raw)
}

export async function patchKnowledgeSource(
  id: number,
  body: {
    name?: string
    linked_version_mode?: 'latest' | 'pinned'
    pinned_snapshot_id?: number | null
    scan_enabled?: boolean
    ingest_policy?: LensIngestPolicy
  },
): Promise<LensKnowledgeSource> {
  const raw = await api(lensUrl(`knowledge-sources/${id}/`), {
    method: 'PATCH',
    headers: lensHeaders(),
    body: JSON.stringify(knowledgeSourceRequestBody(body)),
  })
  return lensPayload<LensKnowledgeSource>(raw)
}

export async function syncKnowledgeSource(id: number): Promise<LensKnowledgeSource> {
  const raw = await api(lensUrl(`knowledge-sources/${id}/sync/`), {
    method: 'POST',
    headers: lensHeaders(),
  })
  return lensPayload<LensKnowledgeSource>(raw)
}

export async function deleteKnowledgeSource(id: number): Promise<void> {
  await api(lensUrl(`knowledge-sources/${id}/`), {
    method: 'DELETE',
    headers: lensHeaders(),
  })
}

export async function listLensGateways(): Promise<LensGatewayInsight[]> {
  const raw = await api(lensUrl('gateways/'), { headers: lensHeaders() })
  return lensList<LensGatewayInsight>(raw)
}

export async function enableGatewayAi(
  gatewayId: number,
  name?: string,
): Promise<GatewayAiStatus> {
  const raw = await api(lensUrl(`gateways/${gatewayId}/enable-ai/`), {
    method: 'POST',
    headers: lensHeaders(),
    body: JSON.stringify(name ? { name } : {}),
  })
  return lensPayload<GatewayAiStatus>(raw)
}

export type GatewayAiStatus = {
  gateway_id: number
  ai_enabled?: boolean
  sl_lensnode_uuid: string | null
  sidecar_status: string
  workspace_root: string
  sl_name?: string
  sl_status?: string
  sl_workspace_path?: string
  sl_agent_version?: string
  sl_last_heartbeat_at?: string | null
  sl_registered_at?: string | null
  sl_tasks?: SlLensnodeTask[]
  lensnode_token?: string | null
}

export async function fetchGatewayAiStatus(gatewayId: number): Promise<GatewayAiStatus> {
  const raw = await api(lensUrl(`gateways/${gatewayId}/ai/`), { headers: lensHeaders() })
  return lensPayload<GatewayAiStatus>(raw)
}

export type GatewayDirectoryBrowserEntry = {
  name: string
  path: string
  type: 'dir' | 'file' | string
  size_bytes: number
  modified_at?: string | null
  downloadable: boolean
  has_children?: boolean | null
}

export type GatewayDirectoryBrowseResult = {
  gateway_id: number
  path: string
  root_path: string
  parent_path: string
  entries: GatewayDirectoryBrowserEntry[]
  has_more: boolean
  next_cursor: string
}

export async function browseGatewayDirectory(
  gatewayId: number,
  params?: { path?: string; limit?: number },
): Promise<GatewayDirectoryBrowseResult> {
  const qs = new URLSearchParams()
  if (params?.path) qs.set('path', params.path)
  if (params?.limit != null) qs.set('limit', String(params.limit))
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  const raw = await api(lensUrl(`gateways/${gatewayId}/browse/${suffix}`), { headers: lensHeaders() })
  return lensPayload<GatewayDirectoryBrowseResult>(raw)
}

export async function listLensAssistants(): Promise<LensAssistant[]> {
  const raw = await api(lensUrl('assistants/'), { headers: lensHeaders() })
  return lensList<LensAssistant>(raw)
}

export async function fetchLensAssistant(uuid: string): Promise<Record<string, unknown>> {
  const raw = await api(lensUrl(`assistants/${uuid}/`), { headers: lensHeaders() })
  return lensPayload<Record<string, unknown>>(raw)
}

export type LensAssistantKnowledgeSourceOption = {
  id: number
  name: string
  gateway_name: string
  status: string
  lensnode_uuid: string
  workspace_path_on_lensnode: string
  scope_paths: string[]
  indexed_paths: string[]
  sl_assistant_uuid: string | null
}

export type LensAssistantFormOptions = {
  lensnodes: { uuid: string; name?: string; dirs?: { path: string }[] }[]
  gateways: {
    gateway_id: number
    gateway_name: string
    lensnode_uuid: string
    workspace_root: string
    tasks: { name: string; title: string }[]
  }[]
  knowledge_sources: LensAssistantKnowledgeSourceOption[]
  skills: { uuid: string; name: string; slug: string; enabled: boolean }[]
  mcps: { uuid: string; name: string; transport: string; endpoint: string; enabled: boolean }[]
}

export async function fetchLensAssistantFormOptions(): Promise<LensAssistantFormOptions> {
  const raw = await api(lensUrl('assistants/form-options/'), { headers: lensHeaders() })
  return lensPayload<LensAssistantFormOptions>(raw)
}

export async function createLensAssistant(body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const raw = await api(lensUrl('assistants/'), {
    method: 'POST',
    headers: lensHeaders(),
    body: JSON.stringify(body),
  })
  return lensPayload<Record<string, unknown>>(raw)
}

export async function updateLensAssistant(
  uuid: string,
  body: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const raw = await api(lensUrl(`assistants/${uuid}/`), {
    method: 'PATCH',
    headers: lensHeaders(),
    body: JSON.stringify(body),
  })
  return lensPayload<Record<string, unknown>>(raw)
}

export async function deleteLensAssistant(uuid: string): Promise<void> {
  await api(lensUrl(`assistants/${uuid}/`), {
    method: 'DELETE',
    headers: lensHeaders(),
  })
}

export type LensSkillDefinition = {
  content?: string
  description?: string
  markdown?: string
  skill_md?: string
}

export type LensSkill = {
  uuid: string
  name: string
  slug: string
  enabled: boolean
  definition?: string | LensSkillDefinition
}

export type LensMcpServer = {
  uuid: string
  name: string
  transport: string
  endpoint?: string
  config?: Record<string, unknown>
  enabled: boolean
}

export async function listLensSkills(): Promise<LensSkill[]> {
  const raw = await api(lensUrl('skills/'), { headers: lensHeaders() })
  return lensList<LensSkill>(raw)
}

export async function fetchLensSkill(uuid: string): Promise<LensSkill> {
  const raw = await api(lensUrl(`skills/${uuid}/`), { headers: lensHeaders() })
  return lensPayload<LensSkill>(raw)
}

export async function createLensSkill(body: Record<string, unknown>): Promise<LensSkill> {
  const raw = await api(lensUrl('skills/'), {
    method: 'POST',
    headers: lensHeaders(),
    body: JSON.stringify(body),
  })
  return lensPayload<LensSkill>(raw)
}

export async function updateLensSkill(uuid: string, body: Record<string, unknown>): Promise<LensSkill> {
  const raw = await api(lensUrl(`skills/${uuid}/`), {
    method: 'PATCH',
    headers: lensHeaders(),
    body: JSON.stringify(body),
  })
  return lensPayload<LensSkill>(raw)
}

export async function deleteLensSkill(uuid: string): Promise<void> {
  await api(lensUrl(`skills/${uuid}/`), {
    method: 'DELETE',
    headers: lensHeaders(),
  })
}

export async function beautifyLensSkill(body: {
  name?: string
  content?: string
}): Promise<{ content: string }> {
  const raw = await api(lensUrl('skills/beautify/'), {
    method: 'POST',
    headers: lensHeaders(),
    body: JSON.stringify(body),
  })
  return lensPayload<{ content: string }>(raw)
}

export async function listLensMcpServers(): Promise<LensMcpServer[]> {
  const raw = await api(lensUrl('mcp-servers/'), { headers: lensHeaders() })
  return lensList<LensMcpServer>(raw)
}

export async function fetchLensMcpServer(uuid: string): Promise<LensMcpServer> {
  const raw = await api(lensUrl(`mcp-servers/${uuid}/`), { headers: lensHeaders() })
  return lensPayload<LensMcpServer>(raw)
}

export async function createLensMcpServer(body: Record<string, unknown>): Promise<LensMcpServer> {
  const raw = await api(lensUrl('mcp-servers/'), {
    method: 'POST',
    headers: lensHeaders(),
    body: JSON.stringify(body),
  })
  return lensPayload<LensMcpServer>(raw)
}

export async function updateLensMcpServer(
  uuid: string,
  body: Record<string, unknown>,
): Promise<LensMcpServer> {
  const raw = await api(lensUrl(`mcp-servers/${uuid}/`), {
    method: 'PATCH',
    headers: lensHeaders(),
    body: JSON.stringify(body),
  })
  return lensPayload<LensMcpServer>(raw)
}

export async function deleteLensMcpServer(uuid: string): Promise<void> {
  await api(lensUrl(`mcp-servers/${uuid}/`), {
    method: 'DELETE',
    headers: lensHeaders(),
  })
}

export async function listCopilotAssistants(): Promise<LensCopilotAssistant[]> {
  const raw = await api(lensUrl('copilot/assistants/'), { headers: lensHeaders() })
  return lensList<LensCopilotAssistant>(raw)
}

export async function listCopilotKnowledgeSources(): Promise<LensKnowledgeSource[]> {
  const raw = await api(lensUrl('copilot/knowledge-sources/'), { headers: lensHeaders() })
  return lensList<LensKnowledgeSource>(raw)
}

export async function listCopilotSessions(): Promise<LensSessionLink[]> {
  const raw = await api(lensUrl('copilot/sessions/'), { headers: lensHeaders() })
  return lensList<LensSessionLink>(raw)
}

export async function fetchCopilotUsage(
  params: Record<string, string | number | undefined> = {},
): Promise<LensCopilotUsageOverview> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  const suffix = query.size ? `?${query.toString()}` : ''
  const raw = await api(lensUrl(`copilot/usage/${suffix}`), { headers: lensHeaders() })
  return lensPayload<LensCopilotUsageOverview>(raw)
}

export async function fetchCopilotUsageDetail(runUuid: string): Promise<LensCopilotUsageDetail> {
  const raw = await api(lensUrl(`copilot/usage/${runUuid}/`), { headers: lensHeaders() })
  return lensPayload<LensCopilotUsageDetail>(raw)
}

export type CreateCopilotSessionPayload = {
  idempotency_key: string
  title?: string
  backup_config_id: number
  backup_source_snapshot_id: number
  source_scopes: Array<{
    backup_snapshot_directory_id: number
    source_path: string
    path_type?: 'dir' | 'file' | 'unknown'
  }>
  gateway_mode: 'auto' | 'manual'
  gateway_link_id?: number | null
}

export async function createCopilotSession(body: CreateCopilotSessionPayload): Promise<LensSessionLink> {
  const raw = await api(lensUrl('copilot/sessions/'), {
    method: 'POST',
    headers: lensHeaders(),
    body: JSON.stringify(body),
  })
  return lensPayload<LensSessionLink>(raw)
}

export type LensSnapshotBrowseTask = {
  task_id: string
  status: 'pending' | 'running' | 'success' | 'failed' | 'timeout' | 'canceled' | string
  error?: string
  entries?: BackupSnapshotBrowserEntry[]
  has_more?: boolean
}

export async function startCopilotSnapshotBrowse(
  directoryId: number,
  params?: { path?: string; limit?: number },
  signal?: AbortSignal,
): Promise<LensSnapshotBrowseTask> {
  const raw = await api(lensUrl('copilot/snapshot-browse/'), {
    method: 'POST',
    headers: lensHeaders(),
    signal,
    body: JSON.stringify({
      directory_id: directoryId,
      path: params?.path || '',
      limit: Math.max(1, Math.min(params?.limit || 500, 500)),
    }),
  })
  return lensPayload<LensSnapshotBrowseTask>(raw)
}

export async function fetchCopilotSnapshotBrowse(
  taskId: string,
  signal?: AbortSignal,
): Promise<LensSnapshotBrowseTask> {
  const raw = await api(lensUrl(`copilot/snapshot-browse/${taskId}/`), {
    headers: lensHeaders(),
    signal,
  })
  return lensPayload<LensSnapshotBrowseTask>(raw)
}

export async function browseCopilotSnapshotDirectory(
  directoryId: number,
  params?: { path?: string; limit?: number },
  signal?: AbortSignal,
): Promise<{ entries: BackupSnapshotBrowserEntry[]; has_more: boolean }> {
  const started = await startCopilotSnapshotBrowse(directoryId, params, signal)
  let task = started
  let polls = 0
  while (task.status === 'pending' || task.status === 'running') {
    if (polls >= COPILOT_SNAPSHOT_BROWSE_MAX_POLLS) {
      throw new Error('Snapshot browsing timed out. Try again.')
    }
    polls += 1
    await new Promise<void>((resolve, reject) => {
      if (signal?.aborted) {
        reject(new DOMException('Aborted', 'AbortError'))
        return
      }
      const onAbort = () => {
        window.clearTimeout(timer)
        reject(new DOMException('Aborted', 'AbortError'))
      }
      const timer = window.setTimeout(() => {
        signal?.removeEventListener('abort', onAbort)
        resolve()
      }, COPILOT_SNAPSHOT_BROWSE_POLL_MS)
      signal?.addEventListener('abort', onAbort, { once: true })
    })
    task = await fetchCopilotSnapshotBrowse(task.task_id, signal)
  }
  if (task.status !== 'success') {
    throw new Error(task.error || 'Unable to browse the selected snapshot.')
  }
  return {
    entries: (task.entries || []).slice(0, params?.limit || 500),
    has_more: Boolean(task.has_more),
  }
}

export async function retryCopilotSession(sessionId: number): Promise<LensSessionLink> {
  const raw = await api(lensUrl(`copilot/sessions/${sessionId}/retry/`), {
    method: 'POST',
    headers: lensHeaders(),
  })
  return lensPayload<LensSessionLink>(raw)
}

export async function patchCopilotSession(
  sessionId: number,
  body: { agent_model_ref: string | null },
): Promise<LensSessionLink> {
  const raw = await api(lensUrl(`copilot/sessions/${sessionId}/model/`), {
    method: 'PATCH',
    headers: lensHeaders(),
    body: JSON.stringify(body),
  })
  return lensPayload<LensSessionLink>(raw)
}

export async function renameCopilotSession(
  sessionId: number,
  title: string,
): Promise<LensSessionLink> {
  const raw = await api(lensUrl(`copilot/sessions/${sessionId}/title/`), {
    method: 'PATCH',
    headers: lensHeaders(),
    body: JSON.stringify({ title }),
  })
  return lensPayload<LensSessionLink>(raw)
}

export async function pinCopilotSession(sessionId: number): Promise<LensSessionLink> {
  const raw = await api(lensUrl(`copilot/sessions/${sessionId}/pin/`), {
    method: 'POST',
    headers: lensHeaders(),
  })
  return lensPayload<LensSessionLink>(raw)
}

export async function unpinCopilotSession(sessionId: number): Promise<LensSessionLink> {
  const raw = await api(lensUrl(`copilot/sessions/${sessionId}/unpin/`), {
    method: 'POST',
    headers: lensHeaders(),
  })
  return lensPayload<LensSessionLink>(raw)
}

export async function markCopilotSessionViewed(sessionId: number): Promise<LensSessionLink> {
  const raw = await api(lensUrl(`copilot/sessions/${sessionId}/viewed/`), {
    method: 'POST',
    headers: lensHeaders(),
  })
  return lensPayload<LensSessionLink>(raw)
}

export async function fetchCopilotMessages(sessionId: number): Promise<LensChatMessage[]> {
  const raw = await api(lensUrl(`copilot/sessions/${sessionId}/messages/`), {
    headers: lensHeaders(),
  })
  return lensList<LensChatMessage>(raw)
}

export async function uploadCopilotAttachment(
  sessionId: number,
  file: File,
): Promise<LensChatAttachment> {
  const body = new FormData()
  body.append('file', file)
  const raw = await api(lensUrl(`copilot/sessions/${sessionId}/attachments/`), {
    method: 'POST',
    headers: lensHeaders(),
    body,
  })
  return lensPayload<LensChatAttachment>(raw)
}

export async function deleteCopilotAttachment(
  sessionId: number,
  attachmentUuid: string,
  attachmentUrl?: string,
): Promise<void> {
  await api(
    attachmentUrl
      || lensUrl(`copilot/sessions/${sessionId}/attachments/${attachmentUuid}/`),
    {
      method: 'DELETE',
      headers: lensHeaders(),
    },
  )
}

function attachmentFilename(disposition: string | null): string {
  if (!disposition) return ''
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (encoded) return decodeURIComponent(encoded)
  return disposition.match(/filename="?([^";]+)"?/i)?.[1] || ''
}

export async function fetchCopilotAttachmentBlob(
  sessionId: number,
  attachmentUuid: string,
  attachmentUrl?: string,
  signal?: AbortSignal,
): Promise<{ blob: Blob; filename: string }> {
  const path = attachmentUrl
    || lensUrl(`copilot/sessions/${sessionId}/attachments/${attachmentUuid}/`)
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    signal,
    headers: {
      Accept: '*/*',
      ...lensHeaders(),
    },
  })
  if (!response.ok) {
    let message = response.statusText || 'Unable to load attachment.'
    try {
      const body = await response.json()
      message = String(
        body?.data?.error?.message
          || body?.error?.message
          || body?.message
          || message,
      )
    } catch {
      // Keep the HTTP status text for a non-JSON response.
    }
    throw new Error(message)
  }
  return {
    blob: await response.blob(),
    filename: attachmentFilename(response.headers.get('Content-Disposition')),
  }
}

export async function createCopilotRun(
  sessionId: number,
  question: string,
  idempotencyKey?: string,
  attachmentUuids: string[] = [],
): Promise<LensRun> {
  const raw = await api(lensUrl(`copilot/sessions/${sessionId}/runs/`), {
    method: 'POST',
    headers: lensHeaders(),
    body: JSON.stringify({
      question,
      ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
      ...(attachmentUuids.length ? { attachment_uuids: attachmentUuids } : {}),
    }),
  })
  return lensPayload<LensRun>(raw)
}

export async function syncCopilotSession(sessionId: number): Promise<LensCopilotSyncResponse> {
  const raw = await api(lensUrl(`copilot/sessions/${sessionId}/sync/`), {
    headers: lensHeaders(),
  })
  return lensPayload<LensCopilotSyncResponse>(raw)
}

export async function fetchCopilotActiveRun(
  sessionId: number,
): Promise<LensCopilotActiveRun | null> {
  const raw = await api(lensUrl(`copilot/sessions/${sessionId}/active-run/`), {
    headers: lensHeaders(),
  })
  const payload = lensPayload<{ active_run: LensCopilotActiveRun | null }>(raw)
  return payload.active_run ?? null
}

export async function cancelCopilotRun(sessionId: number, runUuid: string): Promise<LensRun> {
  const raw = await api(
    lensUrl(`copilot/sessions/${sessionId}/runs/${runUuid}/cancel/`),
    {
      method: 'POST',
      headers: lensHeaders(),
    },
  )
  return lensPayload<LensRun>(raw)
}

export async function streamCopilotRun(
  sessionId: number,
  runUuid: string,
  onData: (payload: unknown) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}${lensUrl(`copilot/sessions/${sessionId}/runs/${runUuid}/stream/`)}`,
    {
      credentials: 'include',
      headers: {
        Accept: 'text/event-stream',
        ...lensHeaders(),
      },
      signal,
    },
  )
  if (!res.ok || !res.body) {
    let detail = `Stream failed (${res.status})`
    try {
      const body = await res.json()
      const data = (body as { data?: { meta?: { diagnostic?: string } } })?.data
      detail = data?.meta?.diagnostic || detail
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      if (signal?.aborted) return
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() ?? ''
      for (const part of parts) {
        const line = part
          .split('\n')
          .find((l) => l.startsWith('data:'))
        if (!line) continue
        const raw = line.slice(line.indexOf(':') + 1).trim()
        if (!raw) continue
        try {
          onData(JSON.parse(raw))
        } catch {
          onData(raw)
        }
      }
    }
  } catch (err) {
    if (signal?.aborted || isAbortError(err)) return
    throw err
  } finally {
    reader.cancel().catch(() => undefined)
  }
}

export async function deleteCopilotSession(sessionId: number): Promise<void> {
  await api(lensUrl(`copilot/sessions/${sessionId}/`), {
    method: 'DELETE',
    headers: lensHeaders(),
  })
}

export type LensChatBinding = {
  id: number
  backup_config_id: number
  backup_source_snapshot_id: number
  backup_snapshot_directory_id: number | null
  source_path: string
  gateway_link_id: number
  gateway_name: string
  gateway_scope: string
  knowledge_source_id: number | null
  knowledge_source_status: string | null
  sl_assistant_uuid: string | null
  is_active: boolean
  ready_for_chat?: boolean
}

export type LensCopilotGatewayOption = {
  gateway_link_id: number
  gateway_id: number
  name: string
  scope: string
  is_platform_default: boolean
  sidecar_status: string
  online: boolean
  hfl_usable: boolean
  copilot_eligible: boolean
}

function tenantLensUrl(relative: string): string {
  const prev = lensApiScope
  lensApiScope = 'tenant'
  const url = lensUrl(relative)
  lensApiScope = prev
  return url
}

export async function fetchActiveCopilotBinding(): Promise<LensChatBinding | null> {
  const raw = await api(tenantLensUrl('copilot/bindings/'), { headers: orgHeaders() })
  const payload = lensPayload<{ binding: LensChatBinding | null }>(raw)
  return payload.binding ?? null
}

export async function ensureCopilotBinding(body: {
  backup_config_id: number
  backup_source_snapshot_id: number
  backup_snapshot_directory_id?: number | null
  source_path?: string
  gateway_link_id?: number | null
}): Promise<LensChatBinding> {
  const raw = await api(tenantLensUrl('copilot/bindings/'), {
    method: 'POST',
    headers: orgHeaders(),
    body: JSON.stringify(body),
  })
  const payload = lensPayload<{ binding: LensChatBinding }>(raw)
  return payload.binding
}

export async function listCopilotGatewayOptions(): Promise<LensCopilotGatewayOption[]> {
  const raw = await api(tenantLensUrl('copilot/gateway-options/'), { headers: orgHeaders() })
  return lensList<LensCopilotGatewayOption>(raw)
}

export async function setPlatformLensGatewayDefault(gatewayId: number): Promise<void> {
  const prev = lensApiScope
  lensApiScope = 'platform'
  try {
    await api(lensUrl(`gateways/${gatewayId}/set-default`), {
      method: 'POST',
      headers: lensHeaders(),
    })
  } finally {
    lensApiScope = prev
  }
}
