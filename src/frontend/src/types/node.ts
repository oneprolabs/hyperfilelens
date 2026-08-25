import type { NodeLifecycleInfo, NodeWorkloadInfo } from './nodeLifecycle'

export type NodeRole = 'agent' | 'proxy' | 'gateway'
export type NodeInstallationMode = 'system' | 'user' | 'user_continuous' | 'account'
export type NodeStatus =
  | 'active'
  | 'upgrading'
  | 'restarting'
  | 'verifying'
  | 'verification_pending'
  | 'removing'
  | 'cleaning_up'
  | 'failed'
  | 'upgrade_failed'
  | 'deregistration_failed'
export type Availability = 'online' | 'offline'

export type NodeStoragePool = {
  key?: string
  kind?: 'local' | 'network' | string
  device?: string
  fs_type?: string
  mount_points?: string[]
  total_bytes?: number
  used_bytes?: number
  free_bytes?: number
}

export type NodeStoragePoolRow = {
  key: string
  device: string
  fsType: string
  mountPoints: string[]
  totalBytes: number
  usedBytes: number
  availableBytes: number
}

export type ApiNode = {
  id: number
  organization: number
  name: string
  role: NodeRole
  installation_mode?: NodeInstallationMode
  version?: string
  os_name?: string
  ip_address?: string | null
  repository_server_address?: string
  effective_repository_server_address?: string | null
  repository_server_address_source?: 'proxy_override' | 'agent_reported' | 'unavailable'
  status: NodeStatus
  availability?: Availability
  availability_updated_at?: string | null
  routable?: boolean
  last_seen_at?: string | null
  metadata?: Record<string, unknown>
  created_at?: string
  updated_at?: string
  agent_control_ws_path?: string
  lifecycle?: NodeLifecycleInfo | null
  workload?: NodeWorkloadInfo | null
  associated_repository_count?: number
}

export type ApiNodeToken = {
  id: number
  organization: number
  token: string
  role: NodeRole
  installation_mode: NodeInstallationMode
  installation_mode_policy?: 'fixed' | 'auto'
  target_platform?: 'linux' | 'windows' | 'macos' | ''
  note?: string
  is_active: boolean
  created_at?: string
  expires_at?: string | null
  used_at?: string | null
  status: 'active' | 'expired' | 'revoked'
  tls_verify: boolean
}

export type CreateNodeTokenBody = {
  role: NodeRole
  installation_mode?: NodeInstallationMode
  installation_mode_policy?: 'fixed' | 'auto'
  target_platform?: 'linux' | 'windows' | 'macos'
  note?: string
  expires_at?: string | null
  is_active?: boolean
}

export type UpdateNodeBody = {
  name?: string
  ip_address?: string | null
  repository_server_address?: string
  version?: string
  os_name?: string
  metadata?: Record<string, unknown>
}
