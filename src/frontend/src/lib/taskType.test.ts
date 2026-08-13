import { describe, expect, it } from 'vitest'
import { isRestoreTaskType } from './taskType'

describe('task type families', () => {
  it.each(['restore', 'insight_workspace_restore'])(
    'treats %s as a restore task',
    (taskType) => {
      expect(isRestoreTaskType(taskType)).toBe(true)
    },
  )

  it.each(['backup', 'snapshot_download', '', null])(
    'does not treat %s as a restore task',
    (taskType) => {
      expect(isRestoreTaskType(taskType)).toBe(false)
    },
  )
})
