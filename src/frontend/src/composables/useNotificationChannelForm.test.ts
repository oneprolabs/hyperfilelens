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
