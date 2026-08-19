import { api } from './api'
import { unwrapApiPayload } from './parse'

export type BackupTargetValidationSource = {
  key: string
  source_type: 'agent' | 'nas'
  source_ref_id: number
  repository_id: number
  repository_endpoint_type: 'external' | 'internal'
}

export type BackupTargetValidationPayload = {
  sources: BackupTargetValidationSource[]
}

export type BackupTargetValidationResult = {
  key: string
  status: 'success' | 'failed'
  code: string | null
  message: string
  details?: {
    stage?: string
    source_name?: string
    source_address?: string
    proxy_name?: string
    proxy_address?: string
    endpoint?: string
    address_source?: string
    port_range?: string
    remediation?: string
    dependency?: string
    helper?: string
    execution_node_name?: string
    execution_node_address?: string
  }
}

export type BackupTargetValidationResponse = {
  status: 'success' | 'failed'
  results: BackupTargetValidationResult[]
}

export async function validateProtectionBackupTargets(
  payload: BackupTargetValidationPayload,
  options: { signal?: AbortSignal } = {},
) {
  return unwrapApiPayload<BackupTargetValidationResponse>(
    await api<unknown>('/api/v1/protection/backup-target-validations/', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal: options.signal,
    }),
  )
}
