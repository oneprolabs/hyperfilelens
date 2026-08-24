import { api } from './api'
import { logger } from './logger'
import { unwrapApiPayload } from './parse'
import type { TaskRuntimePayload } from './kopiaProgress'

export type StartBackupTaskSource = {
  source_type: 'agent' | 'nas'
  source_ref_id: number
}

export type StartBackupTaskPayload = {
  sources?: StartBackupTaskSource[]
  source_ids?: string[]
  backup_config_ids?: number[]
  trigger_type?: 'manual' | 'schedule' | 'retry' | 'api'
  idempotency_key?: string
}

export type StartBackupTaskResultItem = {
  source_type: 'agent' | 'nas'
  source_ref_id: number
  backup_config_id: number | null
  task_id: number | null
  task_uuid: string | null
  source_snapshot_id: number | null
  source_snapshot_status: string | null
  status: 'created' | 'skipped' | 'conflict' | 'failed'
  error_code?: string | null
  message: string
}

export type StartBackupTaskResult = {
  created_count: number
  skipped_count: number
  results: StartBackupTaskResultItem[]
}

const backupTaskBase = '/api/v1/protection/backup-tasks'

export async function startProtectionBackupTasks(payload: StartBackupTaskPayload) {
  logger.info('protectionBackupTaskApi.ts', 43, 'protection backup task start', payload)
  return unwrapApiPayload<StartBackupTaskResult>(
    await api<unknown>(`${backupTaskBase}/`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  )
}

export type BackupTaskActionResult = {
  task_uuid: string
  status: string
  source_snapshot_id?: number
  source_snapshot_status?: string
}

export async function cancelProtectionBackupTask(taskUuid: string) {
  return unwrapApiPayload<BackupTaskActionResult>(
    await api<unknown>(`${backupTaskBase}/${taskUuid}/cancel/`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  )
}

export async function retryProtectionBackupDirectory(
  taskUuid: string,
  backupConfigDirId: number,
) {
  return unwrapApiPayload<BackupTaskActionResult>(
    await api<unknown>(`${backupTaskBase}/${taskUuid}/retry-directory/`, {
      method: 'POST',
      body: JSON.stringify({ backup_config_dir_id: backupConfigDirId }),
    }),
  )
}

export async function fetchBackupTaskRuntime(taskUuid: string) {
  return unwrapApiPayload<TaskRuntimePayload>(
    await api<unknown>(`${backupTaskBase}/${taskUuid}/runtime/`),
  )
}
