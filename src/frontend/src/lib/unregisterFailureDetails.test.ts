// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import { en } from '../locales/en'
import {
  mergeUnregisterDetails,
  notifyUnregisterCleanupWarning,
  notifyUnregisterFailure,
  notifyUnregisterFailureBatch,
  previousUnregisterFailureDetails,
  unregisterFailureBannerText,
  unregisterFailureToErrorDetails,
} from './unregisterFailureDetails'
import { closeErrorDetails } from './errors/details'
import { resetToastStoreForTests, toastState } from './toast/store'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: { en },
})
const t = i18n.global.t

describe('unregisterFailureToErrorDetails', () => {
  it('maps sync API reasons and hint into the shared details payload', () => {
    const details = unregisterFailureToErrorDetails({
      t,
      sourceId: 'agent:1',
      sourceName: 'linux-32',
      apiError: {
        message: 'Delete blocked',
        hint: 'Retry after stopping running tasks.',
        reasons: [{
          code: 'running_tasks',
          detail: 'Backup is running',
          source_id: 'agent:1',
          source_name: 'linux-32',
        }],
      },
    })

    expect(details.title).toBe('Source deregistration failed')
    expect(details.summary).toContain('linux-32')
    expect(details.reasons?.some((item) => /linux-32.*running/i.test(item))).toBe(true)
    expect(details.resolutions).toEqual(expect.arrayContaining([
      'Retry after stopping running tasks.',
    ]))
  })

  it('localizes a structured active-backup conflict', () => {
    const details = unregisterFailureToErrorDetails({
      t,
      sourceId: 'agent:1',
      sourceName: 'linux-32',
      apiError: {
        status: 409,
        message: 'Backup already running',
        errorCode: 'BACKUP.ALREADY_RUNNING',
        meta: {
          task_uuid: 'backup-task-uuid',
          source_type: 'agent',
          source_ref_id: 1,
        },
      },
    })

    expect(details.errorCode).toBe('BACKUP.ALREADY_RUNNING')
    expect(details.summary).toBe(
      'A backup is starting or running for this source. Stop it or wait for it to finish before continuing.',
    )
    expect(details.reasons).toEqual([details.summary])
    expect(details.summary).not.toContain('backup-task-uuid')
    expect(details.rawDetail).toMatchObject({
      task_uuid: 'backup-task-uuid',
      blocking_source_type: 'agent',
      blocking_source_ref_id: 1,
    })
  })

  it('maps async task cleanup residue and retained resources', () => {
    const details = unregisterFailureToErrorDetails({
      t,
      sourceId: 'nas:9',
      sourceName: 'nas-share',
      task: {
        status: 'success',
        task_uuid: 'task-uuid-9',
        error_code: '',
        error_message: '',
        result_payload: {
          result: 'partial_success',
          cleanup_complete: false,
          retained_resources: ['agent_installation'],
          cleanup_failures: [{
            code: 'repository_cleanup_required',
            detail: 'Repository leftover remains',
          }],
          hint: 'Inspect retained Agent installation.',
        },
      } as never,
    })

    expect(details.title).toBe('Removed with cleanup warnings')
    expect(details.summary).toContain('nas-share')
    expect(details.reasons).toEqual(expect.arrayContaining([
      expect.stringContaining('Repository leftover remains'),
      expect.stringContaining('agent_installation'),
    ]))
    expect((details.rawDetail as { task_uuid?: string }).task_uuid).toBe('task-uuid-9')
  })

  it('presents force residue as a completed removal instead of a failed cleanup', () => {
    const details = unregisterFailureToErrorDetails({
      t,
      sourceId: 'agent:13',
      sourceName: 'zjb',
      task: {
        status: 'success',
        current_step: 'finalize_source_unregister',
        result_payload: {
          result: 'partial_success',
          cleanup_complete: false,
          retained_resources: ['repository_cleanup_record:6'],
          snapshot_cleanup_tasks: [{
            task_uuid: 'snapshot-task-1',
            status: 'failed',
            error_message: 'Agent source is offline.',
          }],
        },
      } as never,
    })

    expect(details.title).toBe('Removed with cleanup warnings')
    expect(details.reasons?.some(item => /Failed step/i.test(item))).toBe(false)
    expect(details.reasons?.some(item => /Cleanup task .* failed/i.test(item))).toBe(false)
    expect(details.resolutions?.some(item => /Open the deregistration dialog again/i.test(item))).toBe(false)
  })

  it('falls back to a generic failure when no structured fields exist', () => {
    const details = unregisterFailureToErrorDetails({
      t,
      sourceId: 'agent:2',
      sourceName: 'host-2',
      fallbackMessage: 'Failed to deregister backup sources.',
    })

    expect(details.reasons).toEqual(['Failed to deregister backup sources.'])
    expect(details.summary).toContain('host-2')
  })

  it('keeps multi-source sync failures from forcing a single source title', () => {
    const details = unregisterFailureToErrorDetails({
      t,
      apiError: {
        message: 'Bulk delete failed',
        reasons: [
          { code: 'running_tasks', detail: 'A is busy', source_id: 'agent:1' },
          { code: 'running_tasks', detail: 'B is busy', source_id: 'agent:2' },
        ],
      },
    })

    expect(details.summary).not.toMatch(/for $/)
    expect(details.reasons?.length).toBeGreaterThanOrEqual(2)
  })

  it('scopes sync API reasons to one source', () => {
    const details = unregisterFailureToErrorDetails({
      t,
      sourceId: 'agent:1',
      sourceName: 'host-a',
      apiError: {
        message: 'Bulk delete failed',
        reasons: [
          { code: 'running_tasks', detail: 'A is busy', source_id: 'agent:1', source_name: 'host-a' },
          { code: 'running_tasks', detail: 'B is busy', source_id: 'agent:2', source_name: 'host-b' },
        ],
      },
    })

    expect(details.summary).toContain('host-a')
    expect(details.reasons?.some((item) => /host-a.*running/i.test(item))).toBe(true)
    expect(details.reasons?.some((item) => /host-b/i.test(item))).toBe(false)
  })

  it('maps sync partial_success warnings without calling them a hard failure', () => {
    const details = unregisterFailureToErrorDetails({
      t,
      sourceId: 'nas:3',
      sourceName: 'nas-a',
      partialSuccess: true,
      warnings: [{
        code: 'cleanup_warning',
        detail: 'Share unmounted with warning',
        source_id: 'nas:3',
      }],
      retainedResources: ['mount_residue'],
    })

    expect(details.title).toBe('Removed with cleanup warnings')
    expect(details.summary).toContain('nas-a')
    expect(details.reasons?.some((item) => /Share unmounted with warning/i.test(item))).toBe(true)
    expect(details.reasons?.some((item) => /mount_residue/i.test(item))).toBe(true)
    expect(details.reasons?.some((item) => /Failed to deregister/i.test(item))).toBe(false)
  })
})

describe('notifyUnregisterFailure presentation', () => {
  it('publishes toast details with View details payload', () => {
    resetToastStoreForTests()
    closeErrorDetails()

    notifyUnregisterFailure({
      t,
      sourceId: 'agent:1',
      sourceName: 'linux-32',
      task: {
        status: 'failed',
        task_uuid: 'task-fail-1',
        error_message: 'Agent uninstall callback failed',
        current_step: 'uninstall_agent',
        result_payload: {
          failed_step: 'uninstall_agent',
          reasons: [{ code: 'agent_offline', detail: 'Agent is offline' }],
        },
      } as never,
    })

    expect(toastState.items).toHaveLength(1)
    expect(toastState.items[0]?.details?.rawDetail).toEqual(
      expect.objectContaining({ task_uuid: 'task-fail-1' }),
    )
    expect(toastState.items[0]?.details?.reasons?.length).toBeGreaterThan(0)

    resetToastStoreForTests()
    closeErrorDetails()
  })

  it('uses warning toast for force-cleanup residue', () => {
    resetToastStoreForTests()
    vi.useFakeTimers()

    notifyUnregisterCleanupWarning({
      t,
      sourceId: 'nas:3',
      sourceName: 'nas-a',
      outcome: {
        terminal: true,
        success: true,
        partialSuccess: true,
        cleanupComplete: false,
        status: 'success',
        pendingRemovals: [],
        errorMessage: '',
        reasons: [],
        cleanupFailures: [],
        cleanupWarnings: [{
          code: 'cleanup_warning',
          detail: 'Share unmounted with warning',
          sourceId: 'nas:3',
        }],
        retainedResources: ['mount_residue'],
        failedChildren: [],
      },
    })

    expect(toastState.items[0]?.type).toBe('warning')
    expect(toastState.items[0]?.title).toBe('Removed with cleanup warnings')
    expect(toastState.items[0]?.details?.reasons?.some((item) => /mount_residue/i.test(item))).toBe(true)

    resetToastStoreForTests()
    vi.useRealTimers()
  })

  it('surfaces sync warnings in cleanup-warning toast details', () => {
    resetToastStoreForTests()

    notifyUnregisterCleanupWarning({
      t,
      sourceId: 'nas:8',
      sourceName: 'nas-sync',
      warnings: [{ code: 'cleanup_warning', detail: 'Repository leftover remains' }],
    })

    expect(toastState.items[0]?.type).toBe('warning')
    expect(toastState.items[0]?.details?.reasons).toEqual(
      expect.arrayContaining([expect.stringContaining('Repository leftover remains')]),
    )
    expect(toastState.items[0]?.details?.reasons?.some((item) => /Failed to deregister/i.test(item))).toBe(false)

    resetToastStoreForTests()
  })
})

describe('unregister multi-source notification batching', () => {
  it('merges multi-source failures into a single toast', () => {
    resetToastStoreForTests()

    const a = unregisterFailureToErrorDetails({
      t,
      sourceId: 'agent:1',
      sourceName: 'host-a',
      apiError: {
        message: 'Bulk delete failed',
        reasons: [{ code: 'running_tasks', detail: 'busy', source_id: 'agent:1', source_name: 'host-a' }],
      },
    })
    const b = unregisterFailureToErrorDetails({
      t,
      sourceId: 'agent:2',
      sourceName: 'host-b',
      apiError: {
        message: 'Bulk delete failed',
        reasons: [{ code: 'running_tasks', detail: 'busy', source_id: 'agent:2', source_name: 'host-b' }],
      },
    })

    notifyUnregisterFailureBatch({
      t,
      items: [
        { sourceId: 'agent:1', sourceName: 'host-a', details: a },
        { sourceId: 'agent:2', sourceName: 'host-b', details: b },
      ],
    })

    expect(toastState.items).toHaveLength(1)
    expect(toastState.items[0]?.message).toContain('2 sources')
    expect(mergeUnregisterDetails(t, [a, b]).reasons?.length).toBeGreaterThanOrEqual(2)

    resetToastStoreForTests()
  })

  it('combines previous failures for multi-source retry banners', () => {
    const combined = previousUnregisterFailureDetails(t, [
      unregisterFailureToErrorDetails({
        t,
        sourceId: 'agent:1',
        sourceName: 'host-a',
        fallbackMessage: 'A failed',
      }),
      unregisterFailureToErrorDetails({
        t,
        sourceId: 'agent:2',
        sourceName: 'host-b',
        fallbackMessage: 'B failed',
      }),
    ])
    expect(combined?.title).toMatch(/Previous deregistration failures/i)
    expect(combined?.summary).toContain('2 sources')
    expect(unregisterFailureBannerText(combined)).toContain('2 sources')
  })

  it('frames a single previous failure with the Previous title', () => {
    const single = previousUnregisterFailureDetails(t, [
      unregisterFailureToErrorDetails({
        t,
        sourceId: 'agent:1',
        sourceName: 'host-a',
        fallbackMessage: 'A failed',
      }),
    ])
    expect(single?.title).toBe('Previous deregistration failure')
    expect(single?.summary).toContain('host-a')
    expect(single?.title).not.toBe('Source deregistration failed')
  })

  it('guards mergeUnregisterDetails against an empty list', () => {
    const empty = mergeUnregisterDetails(t, [], 'failure')
    expect(empty.summary).toBe('Failed to deregister backup sources.')
    expect(empty.rawDetail).toEqual({ count: 0, sources: [] })
  })
})
