import { describe, expect, it } from 'vitest'
import { createI18n } from 'vue-i18n'
import { en } from '../locales/en'
import zh from '../../../../language-packs/packs/zh-hans/frontend/messages.json'
import es from '../../../../language-packs/packs/es/frontend/messages.json'
import { unregisterReasonLabel } from './backupSourceUnregisterDialog'
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
      expect(unregisterCleanupText('Backup source was not deleted.', t))
        .toBe(messages.ops.task.failureDetails.reason.SOURCE_UNREGISTER_CLEANUP_FAILED)
      expect(unregisterCleanupText('Unknown cleanup error: /srv/custom', t))
        .toBe('Unknown cleanup error: /srv/custom')
      for (const detail of ['The NAS mount could not be removed.', 'The NAS mount could not be removed']) {
        expect(unregisterCleanupText(detail, t)).toBe(messages.ops.task.failureDetails.reason.unmount_failed)
      }
      expect(unregisterCleanupText('Unmount failed', t))
        .toBe(messages.ops.task.failureDetails.reason_message['Unmount failed'])
      expect(unregisterCleanupText('Unmount failed and the source remains registered.', t))
        .toBe(messages.protection.backupsPage.unregisterUnmountFailedRegistered)
      expect(unregisterReasonLabel({ code: 'unmount_failed', detail: 'Legacy fallback' }, t))
        .toBe(messages.ops.task.failureDetails.reason.unmount_failed)
      expect(unregisterReasonLabel({ code: 'SOURCE_UNREGISTER_CLEANUP_FAILED', detail: 'Legacy fallback' }, t))
        .toBe(messages.ops.task.failureDetails.reason.SOURCE_UNREGISTER_CLEANUP_FAILED)
      expect(unregisterCleanupText('/srv/Unmount failed/log: unknown vendor error', t))
        .toBe('/srv/Unmount failed/log: unknown vendor error')
      expect(unregisterCleanupText('Unmount failed: device busy on /srv/custom', t))
        .toBe('Unmount failed: device busy on /srv/custom')
      expect(unregisterCleanupText('constructor', t)).toBe('constructor')
      expect(unregisterCleanupText('Synthetic test failure: Agent offline; remote uninstall could not complete. No real Agent was contacted.', t))
        .toBe(t('protection.backupsPage.syntheticUnregisterAgentOffline'))
      expect(unregisterCleanupText('Diagnostic fixture only. Do not retry or force cleanup; use it to inspect failure detail availability.', t))
        .toBe(t('protection.backupsPage.syntheticUnregisterDiagnostic'))
      expect(unregisterRetainedResourceLabel('source_nas_mount:32', t))
        .toBe(t('protection.backupsPage.unregisterRetainedNasMount', { id: '32' }))
    })
  }
})
