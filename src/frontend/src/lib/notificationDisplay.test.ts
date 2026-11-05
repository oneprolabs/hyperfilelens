import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'
import type { UserNotification } from './notificationApi'
import { notificationDisplay, notificationOutcome } from './notificationDisplay'
import { formatQuotaBytes } from './licenseQuotaDisplay'

const translatedMessages = Object.fromEntries(['zh-hans', 'es'].map(locale => [locale, JSON.parse(readFileSync(
  resolve(process.cwd(), `../../language-packs/packs/${locale}/frontend/messages.json`), 'utf8',
))]))
const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: {
    en,
    ...translatedMessages,
  },
})
const { t } = i18n.global
const notification = (title: string, summary: string): UserNotification => ({
  id: '1', kind: 'quota_increase_request', title, summary,
  severity: 'info', is_read: false, to: '/node/subscription',
})

describe('notification display', () => {
  for (const locale of ['zh-hans', 'es']) {
    it(`resolves notification copy from unambiguous nested keys in ${locale}`, () => {
      i18n.global.locale.value = locale
      const catalog = translatedMessages[locale]
      const expected = {
        'notificationsPage.status': catalog.notificationsPage.status,
        'notificationsPage.received': catalog.notificationsPage.received,
        'nav.notificationPopover.unreadCount': catalog.nav.notificationPopover.unreadCount,
        ...Object.fromEntries(Object.entries(catalog.notificationsPage.quota).map(
          ([key, value]) => [`notificationsPage.quota.${key}`, value],
        )),
      }
      const params = { n: 3, meter: 'Storage', limit: '10 GB', actor: 'Admin' }
      for (const [key, value] of Object.entries(expected)) {
        expect(catalog).not.toHaveProperty([key])
        const translated = String(value).replace(/\{(\w+)\}/g, (_, name: keyof typeof params) => String(params[name]))
        expect(t(key, params)).toBe(translated)
      }
      expect(t('notificationsPage.status')).not.toBe(en.notificationsPage.status)
      expect(t('notificationsPage.received')).not.toBe(en.notificationsPage.received)
    })
  }

  for (const locale of ['en', 'zh-hans', 'es']) {
    it(`formats quota names and storage units in ${locale} without changing evidence`, () => {
      i18n.global.locale.value = locale
      const original = notification('Quota increase approved', 'max_storage_bytes: limit is now 111050674405376. Keep this note.')
      const result = notificationDisplay(original, t)
      expect(result.title).toBe(t('notificationsPage.quota.approved'))
      expect(result.summary).toBe(t('notificationsPage.quota.approvedSummary', {
        meter: t('licenseQuota.storageGb'), limit: formatQuotaBytes(111050674405376),
      }) + ' Keep this note.')
      expect(result.summary).not.toContain('max_storage_bytes')
      expect(original.summary).toContain('111050674405376')
      const rejected = notificationDisplay(notification('Quota increase rejected', 'max_protected_sources: request for 103 was rejected.'), t)
      expect(rejected.summary).toBe(t('notificationsPage.quota.rejectedSummary', {
        meter: t('licenseQuota.protectedSources'), limit: '103',
      }))
    })
  }

  it('handles unlimited, failure, and cancellation while preserving names and notes', () => {
    i18n.global.locale.value = 'en'
    expect(notificationDisplay(notification('Quota increase approved', 'max_storage_bytes: limit is now -1.'), t).summary)
      .toContain('Unlimited')
    const failed = notification('Quota increase apply failed', 'max_source_hosts: approval was recorded, but applying the new limit failed. Please contact your platform administrator.')
    expect(notificationDisplay(failed, t).summary).toBe(t('notificationsPage.quota.failedSummary', { meter: t('licenseQuota.sourceHosts') }))
    const cancelled = notification('Quota request cancelled', 'max_source_hosts: your quota increase request was cancelled by A.B. Team. Some note.')
    expect(notificationDisplay(cancelled, t).summary).toContain('A.B. Team. Some note.')
  })

  it('does not reinterpret unknown content, malformed values, or unrelated notifications', () => {
    for (const summary of ['new_meter: limit is now 12.', 'max_storage_bytes: limit is now -2.', 'max_storage_bytes: limit is now 9007199254740993.', 'Some free-form message.']) {
      const row = notification('Quota increase approved', summary)
      expect(notificationDisplay(row, t).summary).toBe(summary)
    }
    const unrelated = { ...notification('Quota increase approved', 'max_storage_bytes: limit is now 10.'), kind: 'alert' }
    expect(notificationDisplay(unrelated, t)).toEqual({ title: unrelated.title, summary: unrelated.summary })
    const unknown = notification('New quota event', 'Unfamiliar text.')
    expect(notificationDisplay(unknown, t)).toEqual({ title: unknown.title, summary: unknown.summary })
  })

  it('keeps outcome separate from read state and recognizes error severity', () => {
    const row = notification('Quota increase rejected', '')
    expect(notificationOutcome(row)).toBe('rejected')
    expect(notificationOutcome({ ...row, is_read: true })).toBe('rejected')
    expect(notificationOutcome({ ...row, kind: 'alert', severity: 'error' })).toBe('error')
    expect(notificationOutcome({ ...row, kind: 'alert', severity: 'critical' })).toBe('error')
  })
})
