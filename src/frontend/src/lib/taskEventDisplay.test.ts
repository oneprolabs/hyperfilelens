import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'
import { parseTaskStepStatusEvent, taskEventMessageKey, taskEventObjectText } from './taskEventDisplay'

describe('task event internationalization', () => {
  it('covers every repository cleanup step exposed by the backend', () => {
    const cleanupSteps = [
      'cleanup_direct_nas_repositories',
      'check_cleanup_dependencies',
      'verify_cleanup_owner',
      'prepare_cleanup_target',
      'delete_physical_repository',
      'cleanup_owner_local_state',
      'verify_cleanup_result',
      'finalize_cleanup_metadata',
    ]
    const translations = en.ops.task.step as Record<string, string>

    for (const step of cleanupSteps) expect(translations[step]).toBeTruthy()
  })

  it('maps Direct NAS cleanup event messages to translation keys', () => {
    expect(taskEventMessageKey('Cleaning Direct NAS physical repositories'))
      .toBe('ops.task.eventMessage.cleaningDirectNasPhysicalRepositories')
    expect(taskEventMessageKey('Direct NAS repository cleanup completed'))
      .toBe('ops.task.eventMessage.directNasRepositoryCleanupCompleted')
  })

  it('maps the repository maintenance summary event to a translation key', () => {
    expect(taskEventMessageKey('Repository maintenance summary'))
      .toBe('ops.task.eventMessage.repositoryMaintenanceSummary')
  })

  it('maps current and historical source deregistration events to the same copy', () => {
    const preparedKey = 'ops.task.eventMessage.sourceUnregisterPrepared'
    const finalizedKey = 'ops.task.eventMessage.sourceUnregisterFinalized'

    expect(taskEventMessageKey('Source deregistration prepared')).toBe(preparedKey)
    expect(taskEventMessageKey('Source unregister prepared')).toBe(preparedKey)
    expect(taskEventMessageKey('Source deregistration finalized')).toBe(finalizedKey)
    expect(taskEventMessageKey('Source unregister finalized')).toBe(finalizedKey)
  })

  it('maps Restore execution events to translation keys', () => {
    const messages = [
      ['Restore execution started', 'restoreExecutionStarted'],
      ['Restore item completed', 'restoreItemCompleted'],
      ['Restore item failed', 'restoreItemFailed'],
      ['Restore item cancelled', 'restoreItemCancelled'],
    ] as const
    const translations = en.ops.task.eventMessage as Record<string, string>

    for (const [message, key] of messages) {
      expect(taskEventMessageKey(message)).toBe(`ops.task.eventMessage.${key}`)
      expect(translations[key]).toBe(message)
    }
  })

  it('clarifies the prepared snapshot event sequence', () => {
    const messages = [
      ['Logical snapshot created', 'logicalSnapshotCreated', 'Backup snapshot initialized'],
      ['Starting directory snapshot', 'startingDirectorySnapshot', 'Preparing directory snapshot'],
      ['Preparing directory policy', 'preparingDirectoryPolicy', 'Applying backup policy to directory'],
      [
        'Dispatching prepared directory snapshot to agent',
        'dispatchingPreparedDirectorySnapshot',
        'Directory snapshot creation sent to Agent',
      ],
    ] as const
    const translations = en.ops.task.eventMessage as Record<string, string>

    for (const [message, key, label] of messages) {
      expect(taskEventMessageKey(message)).toBe(`ops.task.eventMessage.${key}`)
      expect(translations[key]).toBe(label)
    }
  })

  it('normalizes known messages and preserves unknown messages for callers', () => {
    expect(taskEventMessageKey('  TASK STARTED  ')).toBe('ops.task.eventMessage.taskStarted')
    expect(taskEventMessageKey('Task finished after worker reconciliation'))
      .toBe('ops.task.eventMessage.taskFinished')
    expect(taskEventMessageKey('Repository cleanup failed: permission denied')).toBeNull()
  })

  it('parses repository step status events', () => {
    expect(parseTaskStepStatusEvent('Step delete_physical_repository running')).toEqual({
      step: 'delete_physical_repository',
      status: 'running',
    })
    expect(parseTaskStepStatusEvent('Physical repository deleted')).toBeNull()
  })

  it('combines a meaningful object ID with its name', () => {
    expect(taskEventObjectText({
      id: 1,
      seq: 1,
      level: 'info',
      message: 'Directory snapshot created',
      metadata: {
        object_id: 'kopia-123',
        object_name: '/data/projects',
      },
    })).toBe('kopia-123 (/data/projects)')
  })

  it('shows only the name when an internal ID is not user-facing', () => {
    expect(taskEventObjectText({
      id: 2,
      seq: 2,
      level: 'info',
      message: 'Snapshot download artifact is ready',
      metadata: {
        artifact_id: 42,
        object_name: 'snapshot-download.zip',
      },
    })).toBe('snapshot-download.zip')
  })

  it('summarizes multiple object names', () => {
    expect(taskEventObjectText({
      id: 3,
      seq: 3,
      level: 'info',
      message: 'Starting snapshot download',
      metadata: {
        object_id: 'kopia-456',
        object_names: ['one.txt', 'two.txt', 'three.txt'],
      },
    })).toBe('kopia-456 (one.txt, two.txt +1)')
  })
})
