import { describe, expect, it } from 'vitest'

import {
  sourceUnregisterPendingKind,
  sourceUnregisterTaskBindings,
  sourceUnregisterTaskOutcome,
} from './sourceUnregisterMonitor'

describe('sourceUnregisterTaskBindings', () => {
  it('uses the explicit source-to-task mapping instead of array position', () => {
    expect(sourceUnregisterTaskBindings(['agent:1', 'nas:2'], {
      tasks: [
        { source_id: 'nas:2', task_id: 22, task_uuid: 'task-nas' },
        { source_id: 'agent:1', task_id: 11, task_uuid: 'task-agent' },
      ],
    })).toEqual([
      { sourceId: 'nas:2', taskId: 22, taskUuid: 'task-nas' },
      { sourceId: 'agent:1', taskId: 11, taskUuid: 'task-agent' },
    ])
  })

  it('keeps compatibility with the legacy parallel arrays', () => {
    expect(sourceUnregisterTaskBindings(['agent:1'], {
      task_ids: [11],
      task_uuids: ['task-agent'],
    })).toEqual([{ sourceId: 'agent:1', taskId: 11, taskUuid: 'task-agent' }])
  })
})

describe('sourceUnregisterPendingKind', () => {
  it('keeps waiting and blocked distinct from active cleanup', () => {
    expect(sourceUnregisterPendingKind('waiting')).toBe('delete_waiting')
    expect(sourceUnregisterPendingKind('blocked')).toBe('delete_blocked')
    expect(sourceUnregisterPendingKind('running')).toBe('deleting')
  })
})

describe('sourceUnregisterTaskOutcome', () => {
  it('keeps blocked deregistration non-terminal while it awaits attention', () => {
    const outcome = sourceUnregisterTaskOutcome({
      status: 'blocked',
      error_code: 'SOURCE_UNREGISTER_BLOCKED',
      result_payload: {},
    } as never)

    expect(outcome.terminal).toBe(false)
    expect(outcome.success).toBe(false)
    expect(outcome.status).toBe('blocked')
  })

  it('exposes terminal failure details immediately', () => {
    expect(sourceUnregisterTaskOutcome({
      status: 'failed',
      error_message: 'Agent uninstall callback failed',
      result_payload: {},
    } as never)).toMatchObject({
      terminal: true,
      success: false,
      errorMessage: 'Agent uninstall callback failed',
    })
  })

  it('distinguishes force cleanup residue from a clean success', () => {
    expect(sourceUnregisterTaskOutcome({
      status: 'success',
      result_payload: {
        result: 'partial_success',
        cleanup_complete: false,
        retained_resources: ['agent_installation'],
      },
    } as never)).toMatchObject({
      terminal: true,
      success: true,
      partialSuccess: true,
      cleanupComplete: false,
      retainedResources: ['agent_installation'],
    })

    expect(sourceUnregisterTaskOutcome({
      status: 'success',
      result_payload: {
        result: 'success',
        cleanup_complete: true,
      },
    } as never)).toMatchObject({
      success: true,
      partialSuccess: false,
      cleanupComplete: true,
    })
  })

  it('exposes structured failure fields for the details adapter', () => {
    expect(sourceUnregisterTaskOutcome({
      status: 'failed',
      task_uuid: 'task-abc',
      error_code: 'AGENT.UNINSTALL_FAILED',
      error_message: 'Agent uninstall callback failed',
      current_step: 'uninstall_agent',
      result_payload: {
        failed_step: 'uninstall_agent',
        hint: 'Bring the Agent online and retry.',
        reasons: [{ code: 'agent_offline', detail: 'Agent is offline' }],
        cleanup_failures: [{ code: 'cleanup_failed', detail: 'Repo purge failed' }],
      },
      recent_events: [{
        level: 'error',
        message: 'callback timeout',
        metadata: { hint: 'Check Agent connectivity' },
      }],
    } as never)).toMatchObject({
      terminal: true,
      success: false,
      errorCode: 'AGENT.UNINSTALL_FAILED',
      taskUuid: 'task-abc',
      failedStep: 'uninstall_agent',
      hint: 'Bring the Agent online and retry.',
      reasons: expect.arrayContaining(['Agent is offline', 'Check Agent connectivity']),
      cleanupFailures: [expect.objectContaining({ detail: 'Repo purge failed' })],
    })
  })
})
