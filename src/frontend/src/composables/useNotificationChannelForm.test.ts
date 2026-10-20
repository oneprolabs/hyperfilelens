import { describe, expect, it } from 'vitest'
import {
  buildNotificationChannelConfig,
  defaultNotificationChannelForm,
  loadNotificationChannelConfig,
} from './useNotificationChannelForm'

describe('DingTalk Secret draft', () => {
  it('keeps an existing secret when the editor is unchanged or cancelled', () => {
    const form = defaultNotificationChannelForm()
    form.type = 'dingtalk'
    loadNotificationChannelConfig(form, 'dingtalk', {
      webhook_url: 'https://example.com/robot',
      secret: '********',
    })
    expect(form.dingtalk.secret_configured).toBe(true)
    expect(buildNotificationChannelConfig(form)).not.toHaveProperty('secret')
    expect(buildNotificationChannelConfig(form)).not.toHaveProperty('clear_secret')

    form.dingtalk.secret_editing = true
    form.dingtalk.secret = 'new-secret'
    form.dingtalk.secret_editing = false
    form.dingtalk.secret = ''
    expect(buildNotificationChannelConfig(form)).not.toHaveProperty('clear_secret')
  })

  it('sends a replacement or explicit clear only when reset is active', () => {
    const form = defaultNotificationChannelForm()
    form.type = 'dingtalk'
    loadNotificationChannelConfig(form, 'dingtalk', { secret: '********' })
    form.dingtalk.secret_editing = true
    expect(buildNotificationChannelConfig(form)).toHaveProperty('clear_secret', true)
    form.dingtalk.secret = 'replacement'
    expect(buildNotificationChannelConfig(form)).toHaveProperty('secret', 'replacement')
    expect(buildNotificationChannelConfig(form)).not.toHaveProperty('clear_secret')
  })
})

describe('Webhook Secret draft', () => {
  it('starts without signing and submits a new signing key', () => {
    const form = defaultNotificationChannelForm()
    form.type = 'webhook'
    expect(buildNotificationChannelConfig(form)).not.toHaveProperty('secret')
    expect(buildNotificationChannelConfig(form)).not.toHaveProperty('clear_secret')
    form.webhook.secret = '  new-secret  '
    expect(buildNotificationChannelConfig(form)).toHaveProperty('secret', 'new-secret')
  })

  it('loads only configured state and preserves existing keys unless explicitly cleared', () => {
    const form = defaultNotificationChannelForm()
    form.type = 'webhook'
    loadNotificationChannelConfig(form, 'webhook', {
      url: 'https://open.feishu.cn/open-apis/bot/v2/hook/test',
      secret: '********',
    })
    expect(form.webhook.secret).toBe('')
    expect(form.webhook.secret_configured).toBe(true)
    expect(buildNotificationChannelConfig(form)).not.toHaveProperty('secret')
    expect(buildNotificationChannelConfig(form)).not.toHaveProperty('clear_secret')
    form.webhook.secret_editing = true
    expect(buildNotificationChannelConfig(form)).toHaveProperty('clear_secret', true)
    form.webhook.secret = 'replacement'
    expect(buildNotificationChannelConfig(form)).toHaveProperty('secret', 'replacement')
    expect(buildNotificationChannelConfig(form)).not.toHaveProperty('clear_secret')
    form.webhook.secret = ''
    form.webhook.secret_editing = false
    expect(buildNotificationChannelConfig(form)).not.toHaveProperty('clear_secret')
  })

  it('resets stale credential editing state when loading another channel', () => {
    const form = defaultNotificationChannelForm()
    form.webhook.secret = 'unsaved'
    form.webhook.secret_editing = true
    loadNotificationChannelConfig(form, 'webhook', { endpoint: 'https://example.com/hook' })
    expect(form.webhook.secret).toBe('')
    expect(form.webhook.secret_configured).toBe(false)
    expect(form.webhook.secret_editing).toBe(false)
    expect(form.webhook.url).toBe('https://example.com/hook')
  })
})
