import { describe, expect, it } from 'vitest'
import { createI18n } from 'vue-i18n'
import { en } from '../locales/en'
import zh from '../../../../language-packs/packs/zh-hans/frontend/messages.json'
import es from '../../../../language-packs/packs/es/frontend/messages.json'
import { unregisterBusinessReasons, unregisterEventFailure, unregisterTaskSuggestionText } from './unregisterTaskFailureDisplay'

const reasons = [{
  code: 'agent_offline',
  detail: 'Agent "Test Host" is offline — remote uninstall is required in strict mode.',
}]

describe('source deregistration task failure display', () => {
  it('filters only aggregate diagnostics and retains unique or sole unknown evidence', () => {
    const aggregate = { code: 'SOURCE_UNREGISTER_FAILED', detail: 'Synthetic diagnostic' }
    const unknown = { code: 'custom_failure', detail: 'Custom path /test' }
    expect(unregisterBusinessReasons([...reasons, aggregate, unknown], aggregate.code))
      .toEqual([...reasons, unknown])
    expect(unregisterBusinessReasons([aggregate], aggregate.code)).toEqual([aggregate])
    expect(unregisterBusinessReasons([unknown], aggregate.code)).toEqual([unknown])
  })
  for (const [locale, messages] of Object.entries({ en, zh, es })) {
    const t = createI18n({ legacy: false, locale, messages: { [locale]: messages } }).global.t

    it(`uses ${locale} offline causes and advice for current and historical contracts`, () => {
      for (const code of ['review_task', 'review_source_unregister']) {
        expect(unregisterTaskSuggestionText({ code, detail: 'Old English hint' }, reasons, t))
          .toBe(messages.ops.task.failureDetails.suggestion.reconnect_unregister_agent)
      }
      const display = unregisterEventFailure({
        error_code: 'SOURCE_UNREGISTER_PREFLIGHT_FAILED',
        error_message: 'Backup source was not deleted.',
      }, 'source_unregister', reasons, t)
      expect(display?.summary)
        .toBe(t('protection.backupsPage.unregisterStrictOfflineNamed', { name: 'Test Host' }))
      expect(display?.resolutions).toEqual([messages.ops.task.failureDetails.suggestion.reconnect_unregister_agent])
    })

    it(`prefers event evidence and preserves unknown diagnostics in ${locale}`, () => {
      const display = unregisterEventFailure({
        error_code: 'SOURCE_UNREGISTER_FAILED',
        reasons: [{ code: 'unknown', detail: 'Custom failure /test/path' }],
      }, undefined, reasons, t)
      expect(display?.summary).toBe('Custom failure /test/path')
      expect(display?.resolutions).toEqual([messages.ops.task.failureDetails.suggestion.review_source_unregister])
      expect(unregisterEventFailure({
        error_code: 'SOURCE_UNREGISTER_FAILED',
        error_message: 'Backup source was not deleted.',
      }, undefined, [], t)?.summary).toBe(messages.ops.task.eventMessage.backupSourceNotDeleted)
      expect(unregisterEventFailure({
        error_code: 'SOURCE_UNREGISTER_FAILED',
        hint: 'Custom remediation /test/path',
      }, undefined, [], t)?.resolutions).toEqual(['Custom remediation /test/path'])
      expect(unregisterEventFailure({
        error_code: 'SOURCE_UNREGISTER_FAILED',
      }, undefined, [], t, ['Specific task remediation'])?.resolutions).toEqual(['Specific task remediation'])
    })

    it(`keeps snapshot protection advice ahead of offline advice in ${locale}`, () => {
      const display = unregisterEventFailure({
        reasons: [...reasons, { code: 'snapshot_in_use', detail: 'Snapshot in use' }],
      }, 'source_unregister', [], t)
      expect(display?.resolutions).toEqual([messages.ops.task.failureDetails.suggestion.resolve_snapshot_usage])
      expect(unregisterEventFailure({}, 'source_unregister', reasons, t)).toBeNull()
      expect(unregisterEventFailure({ error_code: 'BACKUP_FAILED' }, 'backup', reasons, t)).toBeNull()
    })
  }
})
