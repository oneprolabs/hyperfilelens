export const RESTORE_TASK_TYPES = ['restore', 'insight_workspace_restore'] as const

export function isRestoreTaskType(taskType?: string | null): boolean {
  return RESTORE_TASK_TYPES.includes(
    String(taskType || '') as typeof RESTORE_TASK_TYPES[number],
  )
}
