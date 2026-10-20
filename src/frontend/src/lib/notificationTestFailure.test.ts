// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'
import zhHans from '../../../../language-packs/packs/zh-hans/frontend/messages.json'
import es from '../../../../language-packs/packs/es/frontend/messages.json'
import { notificationTestFailureDetails } from './notificationTestFailure'
import { notifyError } from './notify'
import { toastState } from './toast/store'
import { ERROR_CODE_I18N_KEYS } from './errors/registry'
import { errorDetailsCopyText } from './errors/details'

function translate(locale: unknown, key: string): string {
  const value = key.split('.').reduce<unknown>(
    (current, part) => current && typeof current === 'object'
      ? (current as Record<string, unknown>)[part]
      : undefined,
    locale,
  )
  return typeof value === 'string' ? value : key
}
const t = (key: string) => translate(en, key)

describe('Feishu test failure feedback', () => {
  it.each([en, zhHans, es])('localizes the keyword rejection from the API envelope', (locale) => {
    const error = {
      errorCode: 'NOTIFICATION.FEISHU_REJECTED',
      message: 'Feishu did not accept the notification.',
      detail: {
        code: 'NOTIFICATION.FEISHU_REJECTED',
        data: {
          status: 'failed',
          code: 'NOTIFICATION.FEISHU_REJECTED',
          error: 'Feishu rejected the message (code=19024): Key Words Not Found',
        },
      },
    }
    const details = notificationTestFailureDetails(error, (key) => translate(locale, key))
    expect(details.summary).toBe(locale.errors.codes.notificationFeishuRejected)
    expect(details.issue).toBe(details.summary)
    expect(details.reasons).toEqual([locale.ops.notification.feishuKeywordReason])
    expect(details.resolutions).toEqual([locale.ops.notification.feishuKeywordResolution])
    expect(details.resolutions?.[0]).toContain('HyperFileLens')
    expect(JSON.stringify(details.rawDetail)).toContain('19024')
    expect(ERROR_CODE_I18N_KEYS[details.errorCode!]).toBe('errors.codes.notificationFeishuRejected')

    const toast = notifyError({ message: details.summary, details, showDetails: true })
    expect(toastState.items.at(-1)?.details?.reasons).toEqual(details.reasons)
    expect(toastState.items.at(-1)?.details?.resolutions).toEqual(details.resolutions)
    toast.close()
  })

  it.each([
    'Feishu rejected the message (code=19024): provider text changed',
    'Feishu rejected the message: keyword not found',
  ])('recognizes keyword rejections by code or diagnostic: %s', (error) => {
    const details = notificationTestFailureDetails({ code: 'NOTIFICATION.FEISHU_REJECTED', error }, t)
    expect(details.reasons).toEqual([en.ops.notification.feishuKeywordReason])
  })

  it('explains signature failures without claiming a keyword failure', () => {
    const details = notificationTestFailureDetails({
      code: 'NOTIFICATION.FEISHU_REJECTED',
      error: 'Feishu rejected the message (code=19021): sign match fail or timestamp is not within one hour',
    }, t)
    expect(details.reasons).toEqual([en.ops.notification.feishuSignatureReason])
    expect(details.resolutions).toEqual([en.ops.notification.feishuSignatureResolution])
  })

  it.each(['Bad Request', 'Invalid JSON response', 'Unconfirmed delivery'])(
    'gives general recovery steps for unknown or invalid responses: %s', (error) => {
      const details = notificationTestFailureDetails({ code: 'NOTIFICATION.FEISHU_REJECTED', error }, t)
      expect(details.reasons).toEqual([en.ops.notification.feishuDeliveryReason])
      expect(details.resolutions).toEqual([en.ops.notification.feishuDeliveryResolution])
    },
  )

  it('keeps trace IDs and redacts credentials from displayed and copied details', () => {
    const details = notificationTestFailureDetails({
      status: 400,
      code: 'NOTIFICATION.FEISHU_REJECTED',
      traceId: 'feishu-test-trace',
      error: 'Feishu rejected: token=private-token secret=private-secret',
    }, t)
    expect(details.traceId).toBe('feishu-test-trace')
    expect(JSON.stringify(details)).not.toMatch(/private-token|private-secret/)
    expect(errorDetailsCopyText(details)).not.toMatch(/private-token|private-secret/)
  })
})

describe('DingTalk test failure feedback', () => {
  it('shows the provider rejection and keyword remedy in shared toast details', () => {
    const error = {
      status: 400,
      errorCode: 'NOTIFICATION.DINGTALK_REJECTED',
      message: 'Bad Request',
      detail: {
        code: 0,
        data: {
          status: 'failed',
          code: 'NOTIFICATION.DINGTALK_REJECTED',
          error: 'DingTalk rejected the message (errcode=310000): keyword mismatch',
        },
      },
    }
    const details = notificationTestFailureDetails(error, t)
    expect(details.errorCode).toBe('NOTIFICATION.DINGTALK_REJECTED')
    expect(details.reasons).toEqual([expect.stringContaining('configured keyword')])
    expect(details.resolutions).toEqual([expect.stringContaining('FileLens')])
    expect(JSON.stringify(details.rawDetail)).toContain('keyword mismatch')

    const toast = notifyError({ message: details.summary, details, showDetails: true })
    expect(toastState.items.at(-1)?.details?.resolutions).toEqual(details.resolutions)
    toast.close()
  })

  it('does not claim a keyword failure when DingTalk returns another reason', () => {
    const details = notificationTestFailureDetails({
      code: 'NOTIFICATION.DINGTALK_REJECTED',
      error: 'DingTalk rejected the message (errcode=310000): sign mismatch',
    }, t)
    expect(details.reasons).toEqual([expect.stringContaining('DingTalk rejected')])
    expect(details.resolutions).toEqual([expect.stringContaining('Secret')])
  })

  it('localizes a Chinese provider keyword rejection for English and Chinese UIs', () => {
    const providerReason = String.fromCodePoint(20851, 38190, 35789, 19981, 21305, 37197)
    const response = {
      status: 400,
      errorCode: 'NOTIFICATION.DINGTALK_REJECTED',
      message: 'Bad Request',
      detail: {
        code: 'NOTIFICATION.DINGTALK_REJECTED',
        error: `DingTalk rejected the message (errcode=310000): ${providerReason}`,
      },
    }
    const english = notificationTestFailureDetails(response, t)
    expect(english.reasons).toEqual([en.ops.notification.dingtalkKeywordReason])
    expect(english.resolutions).toEqual([en.ops.notification.dingtalkKeywordResolution])
    expect(english.resolutions?.join('')).not.toContain('HFL')
    expect(english.reasons?.join('')).not.toContain(providerReason)
    expect(JSON.stringify(english.rawDetail)).toContain(providerReason)

    const chinese = notificationTestFailureDetails(response, (key) => translate(zhHans, key))
    expect(chinese.reasons).toEqual([zhHans.ops.notification.dingtalkKeywordReason])
    expect(chinese.resolutions).toEqual([zhHans.ops.notification.dingtalkKeywordResolution])
    expect(chinese.resolutions?.join('')).not.toContain('HFL')
  })

  it('redacts credentials in provider diagnostics and copied details', () => {
    const details = notificationTestFailureDetails({
      code: 'NOTIFICATION.DINGTALK_REJECTED',
      error: 'DingTalk rejected: access_token=private-token secret=private-secret',
    }, t)
    expect(JSON.stringify(details)).not.toMatch(/private-token|private-secret/)
  })

  it('preserves the existing diagnostic for other webhook failures', () => {
    const details = notificationTestFailureDetails({
      status: 400,
      errorCode: 'NOTIFICATION.WEBHOOK_TIMEOUT',
      message: 'Request timed out',
    }, t)
    expect(details.summary).toContain('timed out')
    expect(details.errorCode).toBe('NOTIFICATION.WEBHOOK_TIMEOUT')
    expect(details.resolutions).toBeUndefined()
  })
})
