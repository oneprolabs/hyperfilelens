import type { NodeRole } from './node'

export type NodeLifecycleKind = 'upgrade' | 'remove'

export type NodeLifecycleState =
  | 'queued'
  | 'upgrading'
  | 'restarting'
  | 'verifying'
  | 'verification_pending'
  | 'removing'
  | 'cleaning_up'
  | 'failed'
  | 'completed'

export type NodeLifecyclePhase =
  | 'dispatching'
  | 'upgrade'
  | 'uninstall'
  | 'waiting_for_agent'
  | 'waiting_for_version'
  | 'waiting_for_disconnect'
  | 'purging_records'
  | 'offline_purged'
  | 'verification_timeout'
  | 'failed'
  | string

export type UpgradeTimelinePhase = {
  phase: string
  label: string
  at: string | null
  status: 'completed' | 'active' | 'pending' | 'failed'
  error?: string | null
}

export type NodeLifecycleInfo = {
  kind: NodeLifecycleKind
  state: NodeLifecycleState
  phase?: NodeLifecyclePhase | null
  task_id?: string | null
  target_version?: string | null
  current_version?: string | null
  started_at?: string | null
  timeline?: UpgradeTimelinePhase[] | null
  error?: string | null
}

export type NodeWorkloadReason = {
  code: string
  task_uuid: string
  task_type: string
  label: string
}

export type NodeWorkloadInfo = {
  blocked: boolean
  reasons: NodeWorkloadReason[]
}

export type NodeOperationStartResult = {
  operation_id: string
  task_id?: string | null
  node_id: number
  kind: NodeLifecycleKind
  state: NodeLifecycleState
  phase?: NodeLifecyclePhase | null
  target_version?: string | null
  offline?: boolean
  purged?: boolean
}

export type NodeOperationBatchPreview = {
  kind: NodeLifecycleKind
  requested: number
  eligible: Array<{
    node_id: number
    name: string
    target_version?: string
    offline?: boolean
    agent_version?: string
    required_capabilities?: string[]
    missing_capabilities?: string[]
    upgrade_required?: boolean
  }>
  skipped_offline: Array<{ node_id: number; name: string; reason: string }>
  skipped_workload: Array<{ node_id: number; name: string; reason: string; blockers?: NodeWorkloadReason[] }>
  skipped_in_progress: Array<{ node_id: number; name: string; reason: string }>
  skipped_not_upgradeable: Array<{ node_id: number; name: string; reason: string; message?: string }>
  skipped_proxy_bound: Array<{ node_id: number; name: string; reason: string }>
  skipped_disk_full?: Array<{
    node_id: number
    name: string
    reason: string
    failure_type?: 'minimum_free_bytes' | 'maximum_used_percent' | string
    disk_free_bytes?: number | null
    required_free_bytes?: number | null
    disk_used_percent?: number | null
    max_disk_used_percent?: number | null
    check_scope?: string
  }>
  missing_node_ids: number[]
  max_concurrent: number
}

export type NodeOperationBatchStartResult = NodeOperationBatchPreview & {
  started: NodeOperationStartResult[]
  queued: Array<{ node_id: number; name: string; target_version?: string; offline?: boolean }>
  errors: Array<Record<string, unknown>>
}

export type LifecycleQueueItem = {
  nodeId: number
  name: string
  ipAddress?: string | null
  role?: NodeRole
  kind: NodeLifecycleKind
  targetVersion?: string
  state: NodeLifecycleState
  taskId?: string | null
  error?: string
  notified?: boolean
  /** Transient failed polls from lifecycle-watch before marking batch item failed. */
  failedConfirmPolls?: number
}

export type LifecycleQueueSnapshot = {
  batchId: string
  kind: NodeLifecycleKind
  maxConcurrent: number
  running: LifecycleQueueItem[]
  queued: LifecycleQueueItem[]
  completed: LifecycleQueueItem[]
  failed: LifecycleQueueItem[]
  skipped: Array<{ nodeId: number; name: string; reason: string }>
}
