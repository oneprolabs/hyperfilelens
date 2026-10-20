import { api } from './api'
import { unwrapApiPayload } from './parse'

export type StorageProviderRegion = {
  id: string
  display_name: string
  region_group: string
  region_group_en: string
  external_endpoint: string
  internal_endpoint: string
  driver: 's3'
  s3_url_style: 'virtual_hosted' | 'path'
  use_tls: true
}

export type StorageProviderConfig = {
  id: string
  display_name: string
  enabled: boolean
  regions: StorageProviderRegion[]
}

export type StorageProvider = StorageProviderConfig & {
  source: 'default' | 'override'
  checksum: string
  updated_at: string | null
  region_count: number
  can_restore_default: boolean
}

export type ProviderDiff = {
  provider_id: string
  change_type: 'added' | 'modified' | 'unchanged'
  provider_changes: Array<{ path: string; before: unknown; after: unknown }>
  added_regions: StorageProviderRegion[]
  removed_regions: StorageProviderRegion[]
  modified_regions: Array<{
    region_id: string
    changes: Array<{ path: string; before: unknown; after: unknown }>
  }>
  high_risk_changes: Array<{ id: string; type: string; path: string }>
  current_checksum?: string | null
  candidate_checksum?: string
  default_checksum?: string | null
  override_checksum?: string | null
  persistence_action?: 'upsert_override' | 'delete_override'
}

export type ProviderValidationStatus =
  | 'pending'
  | 'validating'
  | 'cancelling'
  | 'validation_failed'
  | 'cleanup_required'
  | 'passed'
  | 'cancelled'
  | 'expired'

export type ProviderRegionValidation = {
  id: number
  region_id: string
  region_group: string
  region_group_en: string
  external_endpoint: string
  internal_endpoint: string
  driver: 's3'
  s3_url_style: 'virtual_hosted' | 'path'
  use_tls: true
  status: 'pending' | 'running' | 'success' | 'failed' | 'cleanup_failed' | 'cancelled'
  current_step: string | null
  error_code: string | null
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  updated_at: string
}

export type ProviderValidationRun = {
  id: string
  task_id: number
  task_uuid: string | null
  provider_id: string
  schema_version: number
  status: ProviderValidationStatus
  candidate_config: StorageProviderConfig | null
  candidate_checksum: string | null
  error_code: string | null
  error_message: string | null
  expires_at: string
  finished_at: string | null
  created_at: string
  updated_at: string
  coverage: 'complete' | 'partial' | null
  diff: ProviderDiff | null
  regions: ProviderRegionValidation[]
  region_count: number
  completed_region_count: number
  failed_region_count: number
}

export type PlatformStorageProvidersResponse = {
  schema_version: number
  providers: StorageProvider[]
  validation_runs: ProviderValidationRun[]
}

export type ProviderImportPreview = {
  schema_version: number
  input_checksum: string
  target_provider_ids: string[]
  unchanged_target_provider_ids: string[]
  skipped_provider_ids: string[]
  providers: ProviderDiff[]
  high_risk_confirmation_ids: string[]
}

export type ProviderImportReview = ProviderImportPreview & {
  validation_evidence: Array<{
    provider_id: string
    status: 'passed_complete' | 'passed_partial' | 'not_run' | 'running' | 'failed' | 'expired' | 'cleanup_required' | 'stale'
    run_id: string | null
    candidate_checksum: string
    run_candidate_checksum: string | null
    selected_region_count: number
    total_region_count: number
    expires_at: string | null
  }>
  required_risk_confirmation_ids: string[]
  expires_at: string
  review_token: string
}

export type ProviderResetReview = {
  scope: 'provider' | 'selected' | 'all'
  provider_ids: string[]
  providers: ProviderDiff[]
  expires_at: string
  reset_token: string
}

export function normalizedStorageProviderSnapshot(provider: StorageProviderConfig) {
  return JSON.stringify({
    id: provider.id,
    display_name: provider.display_name.trim(),
    enabled: provider.enabled,
    regions: provider.regions.map((region) => ({
      id: region.id.trim(),
      display_name: region.display_name.trim(),
      region_group: region.region_group.trim(),
      region_group_en: region.region_group_en.trim(),
      external_endpoint: region.external_endpoint.trim().toLowerCase(),
      internal_endpoint: region.internal_endpoint.trim().toLowerCase(),
      driver: region.driver,
      s3_url_style: region.s3_url_style,
      use_tls: region.use_tls,
    })),
  })
}

async function payload<T>(request: Promise<unknown>): Promise<T> {
  return unwrapApiPayload<T>(await request)
}

export function fetchStorageProviderCatalog() {
  return payload<{ schema_version: number; providers: StorageProviderConfig[] }>(
    api<unknown>('/api/v1/storage/provider-catalog/'),
  )
}

export function fetchPlatformStorageProviders(signal?: AbortSignal) {
  return payload<PlatformStorageProvidersResponse>(
    api<unknown>('/api/v1/platform-ops/storage-providers', { signal }),
  )
}

export function diffProviderImport(content: string) {
  return payload<ProviderImportPreview>(
    api<unknown>('/api/v1/platform-ops/storage-providers/import/diff', {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),
  )
}

export function reviewProviderImport(content: string) {
  return payload<ProviderImportReview>(
    api<unknown>('/api/v1/platform-ops/storage-providers/import/review', {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),
  )
}

export function applyProviderImport(
  content: string,
  review: ProviderImportReview,
  riskConfirmations: string[],
) {
  return payload<{ applied: boolean; idempotent: boolean; provider_ids: string[] }>(
    api<unknown>('/api/v1/platform-ops/storage-providers/import/apply', {
      method: 'POST',
      body: JSON.stringify({
        content,
        input_checksum: review.input_checksum,
        review_token: review.review_token,
        risk_confirmations: riskConfirmations,
      }),
    }),
  )
}

function requireProviderSelection(providerIds: string[]) {
  if (
    !providerIds.length
    || providerIds.some(id => typeof id !== 'string' || !id.trim())
    || new Set(providerIds).size !== providerIds.length
  ) {
    throw new Error('A non-empty, unique Provider selection is required.')
  }
}

export function exportProviders(providerIds: string[]) {
  requireProviderSelection(providerIds)
  const query = `?provider_ids=${encodeURIComponent(providerIds.join(','))}`
  return payload<{ schema_version: number; providers: StorageProviderConfig[] }>(
    api<unknown>(`/api/v1/platform-ops/storage-providers/export${query}`),
  )
}

export function reviewProviderReset(target: string | string[]) {
  const selected = Array.isArray(target)
  const providerIds = selected ? target : [target]
  requireProviderSelection(providerIds)
  const path = selected
    ? '/api/v1/platform-ops/storage-providers/reset-selected/review'
    : `/api/v1/platform-ops/storage-providers/${encodeURIComponent(target as string)}/reset/review`
  return payload<ProviderResetReview>(api<unknown>(path, {
    method: 'POST',
    body: JSON.stringify(selected ? { provider_ids: providerIds } : {}),
  }))
}

export function confirmProviderReset(review: ProviderResetReview, providerId?: string) {
  requireProviderSelection(review.provider_ids)
  const selected = review.scope === 'selected' && providerId === undefined
  const single = review.scope === 'provider'
    && providerId !== undefined
    && review.provider_ids.length === 1
    && review.provider_ids[0] === providerId
  if (!selected && !single) throw new Error('Provider restore scope does not match the review.')
  const path = selected
    ? '/api/v1/platform-ops/storage-providers/reset-selected'
    : `/api/v1/platform-ops/storage-providers/${encodeURIComponent(providerId!)}/reset`
  return payload<{ reset: boolean; provider_ids: string[] }>(
    api<unknown>(path, {
      method: 'POST',
      body: JSON.stringify({
        reset_token: review.reset_token,
        ...(selected ? { provider_ids: review.provider_ids } : {}),
      }),
    }),
  )
}

export function createProviderValidationRun(input: {
  provider_id: string
  region_ids: string[]
  access_key_id: string
  secret_access_key: string
  candidate_config: StorageProviderConfig
}) {
  return payload<ProviderValidationRun>(
    api<unknown>('/api/v1/platform-ops/storage-provider-validation-runs', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  )
}

export function fetchProviderValidationRun(runId: string, signal?: AbortSignal) {
  return payload<ProviderValidationRun>(
    api<unknown>(`/api/v1/platform-ops/storage-provider-validation-runs/${runId}`, { signal }),
  )
}

export function cancelProviderValidationRun(runId: string) {
  return payload<ProviderValidationRun>(
    api<unknown>(`/api/v1/platform-ops/storage-provider-validation-runs/${runId}/cancel`, {
      method: 'POST',
      body: '{}',
    }),
  )
}

export function retryProviderValidationRun(runId: string, accessKeyId: string, secretAccessKey: string) {
  return payload<ProviderValidationRun>(
    api<unknown>(`/api/v1/platform-ops/storage-provider-validation-runs/${runId}/retry`, {
      method: 'POST',
      body: JSON.stringify({ access_key_id: accessKeyId, secret_access_key: secretAccessKey }),
    }),
  )
}
