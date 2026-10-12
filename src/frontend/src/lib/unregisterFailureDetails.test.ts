// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import { en } from '../locales/en'
import zh from '../../../../language-packs/packs/zh-hans/frontend/messages.json'
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
  it('localizes offline cleanup across reasons, suggestions, entities and residue', () => {
    const zhT = createI18n({ legacy: false, locale: 'zh', messages: { zh } }).global.t
    const name = '[TEST] DR-Failed Node'
    const diagnostic = `Agent "${name}" is offline — uninstall was skipped.`
    const details = unregisterFailureToErrorDetails({
      t: zhT,
      sourceName: name,
      task: {
        status: 'success',
        task_type: 'source_unregister',
        result_payload: {
          result: 'partial_success',
          cleanup_complete: false,
          retained_resources: ['agent_installation'],
          cleanup_failures: [{ code: 'agent_offline', detail: 'Remote uninstall was skipped because the Agent was offline.' }],
          warnings: [{ code: 'agent_offline', detail: diagnostic, source_name: name }],
        },
        error_details: {
          severity: 'warning',
          cleanup_complete: false,
          retained_resources: ['agent_installation'],
          suggestions: [{ code: 'review_cleanup', detail: 'Review retained resources and complete any remaining cleanup before retrying.' }],
          entities: [{ id: 'agent:1', name, type: 'source', error: `Remote uninstall was skipped because the Agent was offline. ${diagnostic}` }],
          cleanup_failures: [{ code: 'agent_offline', detail: diagnostic }],
        },
      } as never,
    })
    expect(details.title).toBe(zh.protection.backupsPage.unregisterCleanupPendingTitle)
    expect(details.summary).toContain(name)
    expect(details.reasons).toEqual(expect.arrayContaining([
      expect.stringContaining(zh.protection.backupsPage.unregisterOfflineUninstallSkipped),
      expect.stringContaining(zh.ops.task.failureDetails.retainedResource.agent_installation),
    ]))
    expect(details.resolutions).toContain(zh.ops.task.failureDetails.suggestion.review_cleanup)
    expect(details.entities?.[0]?.error).toContain(name)
    expect(details.entities?.[0]?.error).not.toContain('uninstall was skipped')
    expect(details.cleanupResidue?.failures).toEqual([zhT('protection.backupsPage.unregisterOfflineUninstallSkippedNamed', { name })])
    expect(details.cleanupResidue?.retainedResources).toEqual(['agent_installation'])
    expect(details.rawDetail).toMatchObject({
      error_details: { cleanup_failures: [{ detail: diagnostic }] },
    })
  })

  it('localizes synchronous warnings and leaves unknown diagnostics unchanged', () => {
    const zhT = createI18n({ legacy: false, locale: 'zh', messages: { zh } }).global.t
    const details = unregisterFailureToErrorDetails({
      t: zhT,
      sourceName: 'My English Host',
      partialSuccess: true,
      warnings: [
        { code: 'agent_offline', detail: 'Agent "My English Host" is offline — uninstall was skipped.' },
        { code: 'unknown_warning', detail: 'Custom diagnostic: /my/path' },
      ],
      retainedResources: ['agent_installation', 'unknown_resource'],
    })
    expect(details.reasons).toContain(zhT('protection.backupsPage.unregisterOfflineUninstallSkippedNamed', { name: 'My English Host' }))
    expect(details.reasons).toContain('Custom diagnostic: /my/path')
    expect(details.reasons).toContain(zhT('protection.backupsPage.unregisterFailureRetainedResource', { resource: 'unknown_resource' }))
    expect(details.title).toBe(zh.protection.backupsPage.unregisterCleanupPendingTitle)
  })

  it('does not claim retained resources for a warning without resource evidence', () => {
    const details = unregisterFailureToErrorDetails({
      t, sourceName: 'host-a', partialSuccess: true,
      warnings: [{ code: 'unknown_warning', detail: 'Cleanup result needs review' }],
    })
    expect(details.title).toBe('Backup source removed; cleanup reported warnings')
    expect(details.summary).not.toContain('retained resources')
  })

  it('keeps outstanding cleanup explicit when merging source warnings', () => {
    const items = [
      unregisterFailureToErrorDetails({ t, sourceName: 'host-a', partialSuccess: true, retainedResources: ['agent_installation'] }),
      unregisterFailureToErrorDetails({ t, sourceName: 'host-b', partialSuccess: true }),
    ]
    const details = mergeUnregisterDetails(t, items, 'cleanup_warning')
    expect(details.title).toBe('Backup source removed; resource cleanup remains')
    expect(details.summary).toBe('2 sources were removed, but some retained resources still need cleanup.')
  })
  it('uses the persisted task contract across toast, retry and task detail entry points', () => {
    const details = unregisterFailureToErrorDetails({
      t, sourceId: 'agent:1', sourceName: 'host-a',
      task: {
        task_uuid: 'task-1', task_type: 'source_unregister', status: 'success',
        error_details: {
          version: 1, severity: 'warning', outcome: 'warning', summary: 'Cleanup remains',
          task_uuid: 'task-1', correlation_id: 'correlation-1', reasons: [{ code: 'cleanup', detail: 'Unmount failed' }],
          suggestions: [{ code: 'retry_cleanup', detail: 'Retry cleanup' }], cleanup_complete: false,
          retained_resources: ['mount-1'], entities: [{ id: 'agent:1', name: 'host-a', type: 'source' }],
          cleanup_failures: [{ detail: 'Unmount failed' }],
        },
      } as never,
    })
    expect(details).toMatchObject({ severity: 'warning', taskUuid: 'task-1', traceId: 'correlation-1' })
    expect(details.reasons).toContain('Unmount failed')
    expect(details.resolutions).toContain('Retry cleanup')
    expect(details.cleanupResidue).toMatchObject({ retainedResources: ['mount-1'], failures: ['Unmount failed'] })
  })
  it('reports lost visibility as a warning notice with task correlation', () => {
    resetToastStoreForTests()
    notifyUnregisterFailure({ t, details: {
      title: 'Unable to confirm task status', summary: 'Unable to confirm task status',
      severity: 'warning', errorCode: 'TASK_MONITOR_UNAVAILABLE', taskUuid: 'pending-task',
    } })
    expect(toastState.items[0]?.details?.errorCode).toBe('TASK_MONITOR_UNAVAILABLE')
    expect(toastState.items[0]?.details?.severity).toBe('warning')
    expect(toastState.items[0]?.details?.taskUuid).toBe('pending-task')
    resetToastStoreForTests()
  })
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

    expect(details.title).toBe('Backup source removed; resource cleanup remains')
    expect(details.summary).toContain('nas-share')
    expect(details.reasons).toEqual(expect.arrayContaining([
      expect.stringContaining('Repository leftover remains'),
      expect.stringContaining('Agent Installation'),
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

    expect(details.title).toBe('Backup source removed; resource cleanup remains')
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

    expect(details.title).toBe('Backup source removed; resource cleanup remains')
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
    expect(toastState.items[0]?.title).toBe('Backup source removed; resource cleanup remains')
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
