import { describe, expect, it } from 'vitest'
import { createI18n } from 'vue-i18n'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { en } from '../locales/en'
import { snapshotUsageFailureText, snapshotUsageReasonKey } from './snapshotUsageFailureDisplay'
import { buildTaskFailureErrorDetails } from './backupTaskFailureLogic'
import type { TaskRow } from './taskApi'
import { unregisterReasonLabel } from './backupSourceUnregisterDialog'

describe('snapshot usage failure presentation', () => {
  it.each(['en', 'zh-hans', 'es'])('keeps snapshot and failed-Chat context in %s', (locale) => {
    const messages = locale === 'en' ? en : JSON.parse(
      readFileSync(resolve(process.cwd(), `../../language-packs/packs/${locale}/frontend/messages.json`), 'utf8'),
    )
    const { t } = createI18n({ legacy: false, locale, messages: { [locale]: messages } }).global
    const reason = {
      code: 'snapshot_in_use',
      detail: 'Snapshot #790 is protected by a failed Chat.',
      snapshot_id: 790,
      consumers: [{ type: 'chat', status: 'failed' }],
    }
    expect(snapshotUsageFailureText(reason, t)).toBe(
      `${t('ops.task.failureDetails.reason.snapshot_in_use_detail', { snapshotId: 790 })} ${t('ops.task.failureDetails.reason.snapshot_failed_chat')}`,
    )
    expect(unregisterReasonLabel(reason, t)).toBe(snapshotUsageFailureText(reason, t))
    const details = buildTaskFailureErrorDetails({
      task: {
        task_uuid: 'unregister-task', task_type: 'source_unregister', status: 'failed',
        error_details: {
          version: 1, severity: 'error', outcome: 'failed', summary: 'Task failed.',
          task_uuid: 'unregister-task', reasons: [reason],
          suggestions: [{ code: 'resolve_snapshot_usage', detail: 'English fallback' }],
          entities: [{ id: 'agent:406', name: 'jlb-154', type: 'source' }],
        },
      } as TaskRow,
      t,
    })
    expect(details.reasons).toEqual([snapshotUsageFailureText(reason, t)])
    expect(details.resolutions).toEqual([t('ops.task.failureDetails.suggestion.resolve_snapshot_usage')])
    expect(details.entities).toHaveLength(1)
    expect(t('ops.task.step.cleanup_direct_nas_repositories')).not.toContain('Direct NAS')
  })

  it('leaves historical reasons without metadata on their existing path', () => {
    const { t } = createI18n({ legacy: false, locale: 'en', messages: { en } }).global
    expect(snapshotUsageFailureText({ code: 'snapshot_in_use', detail: 'Snapshot in use.' }, t)).toBeUndefined()
  })

  it('does not call a restoring snapshot a failed Chat', () => {
    const { t } = createI18n({ legacy: false, locale: 'en', messages: { en } }).global
    const text = snapshotUsageFailureText({
      code: 'snapshot_in_use', detail: '', snapshot_id: 790,
      consumers: [{ type: 'restore', status: 'unknown' }],
    }, t)
    expect(text).toContain('#790')
    expect(text).not.toContain('failed Chat')
  })

  it('wires structured snapshot reasons into both task drawers', () => {
    for (const path of ['../pages/ops/Tasks.vue', '../pages/protection/components/TaskDetailDrawer.vue']) {
      const source = readFileSync(resolve(process.cwd(), 'src/lib', path), 'utf8')
      expect(source).toContain('snapshotUsageFailureText(item, t)')
    }
  })

  it('does not collapse different snapshot blockers under the same error code', () => {
    const reasons = [
      { code: 'snapshot_in_use', detail: 'Protected.', snapshot_id: 790 },
      { code: 'snapshot_in_use', detail: 'Protected.', snapshot_id: 791 },
      { code: 'snapshot_in_use', detail: 'Protected.', snapshot_id: 790 },
    ]
    expect(new Set(reasons.map(snapshotUsageReasonKey)).size).toBe(2)
    expect(snapshotUsageReasonKey({ code: 'agent_offline', detail: 'Offline.' })).toBe('agent_offline')
    const source = readFileSync(resolve(process.cwd(), 'src/pages/protection/components/TaskDetailDrawer.vue'), 'utf8')
    expect(source).toContain('const dedupe = snapshotUsageReasonKey(r)')
  })
})
