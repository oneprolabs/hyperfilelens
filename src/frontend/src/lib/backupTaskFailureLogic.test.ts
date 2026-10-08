import { describe, expect, it } from 'vitest'
import { createI18n } from 'vue-i18n'
import { en } from '../locales/en'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { TaskRow } from './taskApi'
import { buildTaskFailureErrorDetails, extractSkippedDetails, hasFailureDetails, buildSnapshotFailureErrorDetails } from './backupTaskFailureLogic'

const { t } = createI18n({ legacy: false, locale: 'en', messages: { en } }).global

describe('backup task error details', () => {
  it.each(['en', 'zh-hans', 'es'])('localizes repository host connectivity contracts in %s', (locale) => {
    const messages = locale === 'en' ? en : JSON.parse(
      readFileSync(resolve(process.cwd(), `../../language-packs/packs/${locale}/frontend/messages.json`), 'utf8'),
    )
    const { t: translate } = createI18n({
      legacy: false, locale, messages: { [locale]: messages },
    }).global
    for (const role of ['proxy', 'backup_host', 'host']) {
      const reasonCode = `repository_${role}_unreachable`
      const suggestionCode = `reconnect_repository_${role}`
      const details = buildTaskFailureErrorDetails({
        task: {
          task_uuid: 'repository-task', task_type: 'repository_operation', status: 'failed',
          error_details: {
            version: 1, severity: 'error', outcome: 'failed', summary: 'Task failed.',
            task_uuid: 'repository-task',
            reasons: [{ code: reasonCode, detail: 'Server-side English fallback' }],
            suggestions: [{ code: suggestionCode, detail: suggestionCode }],
          },
        } as TaskRow,
        t: translate,
      })
      expect(details.reasons).toEqual([translate(`ops.task.failureDetails.reason.${reasonCode}`)])
      expect(details.resolutions).toEqual([translate(`ops.task.failureDetails.suggestion.${suggestionCode}`)])
      expect(details.reasons?.join()).not.toContain('Agent')
      expect(details.resolutions?.join()).not.toContain('Agent')
    }
  })

  it('retains correlation, limited-detail notice and cleanup residue from the contract', () => {
    const task = {
      task_uuid: 'task-1', task_type: 'backup', status: 'success',
      error_details: {
        version: 1, severity: 'warning', outcome: 'warning', summary: 'Cleanup remains',
        task_uuid: 'task-1', correlation_id: 'correlation-1', limited: true,
        reasons: [], suggestions: [], cleanup_complete: false, retained_resources: ['resource-1'],
        cleanup_failures: [{ source_name: 'Source', detail: 'Cleanup failed' }],
        skipped_items: { count: 1, items: [{ path: '/data/file', error: 'Permission denied' }] },
      },
    } as TaskRow
    const details = buildTaskFailureErrorDetails({ task, t })
    expect(details.traceId).toBe('correlation-1')
    expect(details.issue).toContain('unavailable')
    expect(details.cleanupResidue).toEqual({
      hasResidue: true, retainedResources: ['resource-1'],
      failures: ['Source: Cleanup failed'], skippedItems: ['/data/file: Permission denied'],
    })
  })
  it('uses persisted results without events and retains every affected item', () => {
    const items = Array.from({ length: 7 }, (_, i) => ({ path: `/data/${i}`, error: 'Permission denied' }))
    const task = { task_uuid: 'task-1', task_type: 'backup', display_name: 'Nightly', status: 'failed', current_step: 'snapshot', result_payload: {
      failure_details: { category: 'source_read_failed', count: 7, items, remediation: ['check_source_access'] },
      skipped_details: { count: 2, file_count: 2 },
    } } as TaskRow
    const details = buildTaskFailureErrorDetails({ task, t })
    expect(details.reasons?.join(' ')).toContain('7 files could not be read')
    expect(details.reasons?.join(' ')).toContain('2 source items were skipped')
    expect(details.entities).toHaveLength(7)
    expect(details.resolutions?.[0]).toContain('Agent has permission')
    expect(details.failedStep).toBe('snapshot')
    expect(details.taskUuid).toBe('task-1')
  })

  it('keeps the terminal failure ahead of skipped item warnings', () => {
    const details = buildTaskFailureErrorDetails({
      task: {
        task_uuid: 'task-terminal',
        task_type: 'backup',
        status: 'failed',
        error_code: 'KOPIA_PROCESS_DIED',
        error_message: 'Backup processing failed: Device or resource busy.',
        result_payload: {
          skipped_details: {
            count: 1,
            directory_count: 1,
            items: [{ path: 'Documents and Settings', error: 'permission denied' }],
          },
        },
      } as TaskRow,
      t,
    })
    expect(details.reasons?.[0]).toContain('Device or resource busy')
    expect(details.reasons?.[1]).toContain('source items were skipped')
  })

  it('retains legacy items without counts and reports the actual sample size', () => {
    const items = Array.from({ length: 20 }, (_, i) => ({ path: `/data/${i}` }))
    const result = extractSkippedDetails({ skipped_details: { items } })
    expect(result).toMatchObject({ count: 20, reported_count: 10, truncated: true })
    expect(result?.items).toHaveLength(10)
  })

  it('does not render historical fatal samples when skipped count is explicitly zero', () => {
    const fatal = { path: '/DumpStack.log.tmp', error: 'device or resource busy' }
    expect(extractSkippedDetails({
      skipped_item_count: 0,
      skipped_file_count: 0,
      skipped_directory_count: 0,
      skipped_details: {
        count: 0, file_count: 0, directory_count: 0, items: [fatal],
      },
      failure_details: { count: 189, items: [fatal] },
    })).toBeNull()
  })

  it('does not classify a clean snapshot summary as a warning', () => {
    expect(hasFailureDetails({ result_payload: { backup_summary: { snapshot_id: 'snapshot-1' } } })).toBe(false)
  })

  it('labels legacy details as limited and partial snapshots as partial', () => {
    const details = buildTaskFailureErrorDetails({ task: { task_uuid: 'old', status: 'failed', error_message: 'Failed' } as TaskRow, t })
    expect(details.issue).toContain('unavailable')
    const snapshot = buildSnapshotFailureErrorDetails({ snapshotStatus: 'partial', sourceName: 'Source', failedDirectoryCount: 2, successfulDirectoryCount: 3, t })
    expect(snapshot?.summary).toContain('partial results')
    expect(snapshot?.reasons?.[0]).toContain('2 of 5')
  })
})
