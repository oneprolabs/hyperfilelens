import { describe, expect, it } from 'vitest'
import { createI18n } from 'vue-i18n'
import { en } from '../locales/en'
import zh from '../../../../language-packs/packs/zh-hans/frontend/messages.json'
import es from '../../../../language-packs/packs/es/frontend/messages.json'
import {
  unregisterCleanupSuggestion,
  unregisterCleanupText,
  unregisterRetainedResourceLabel,
} from './unregisterCleanupDisplay'

describe('unregister cleanup display', () => {
  for (const [locale, messages] of Object.entries({ en, zh, es })) {
    it(`uses ${locale} labels while preserving names and unknown evidence`, () => {
      const t = createI18n({ legacy: false, locale, messages: { [locale]: messages } }).global.t
      expect(unregisterRetainedResourceLabel('agent_installation', t))
        .toBe(messages.ops.task.failureDetails.retainedResource.agent_installation)
      expect(unregisterRetainedResourceLabel('unknown_resource:42', t)).toBe('unknown_resource:42')
      expect(unregisterCleanupText('Agent "Custom Host" is offline — uninstall was skipped.', t))
        .toBe(t('protection.backupsPage.unregisterOfflineUninstallSkippedNamed', { name: 'Custom Host' }))
      expect(unregisterCleanupSuggestion({ code: 'review_cleanup', detail: 'legacy text' }, t))
        .toBe(messages.ops.task.failureDetails.suggestion.review_cleanup)
      expect(unregisterCleanupSuggestion({ code: 'unknown', detail: 'Custom diagnostic' }, t))
        .toBe('Custom diagnostic')
      expect(unregisterCleanupText('Review retained resources and complete any remaining cleanup before retrying.', t))
        .toBe(messages.ops.task.failureDetails.suggestion.review_cleanup)
    })
  }
})
