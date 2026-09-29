// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'
import zhHans from '../../../../language-packs/packs/zh-hans/frontend/messages.json'
import { notificationTestFailureDetails } from './notificationTestFailure'
import { notifyError } from './notify'
import { toastState } from './toast/store'

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
