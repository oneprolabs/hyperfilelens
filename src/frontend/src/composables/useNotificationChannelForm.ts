import type { ChannelType } from '../pages/ops/notificationTypes'

export type NotificationChannelFormState = {
  name: string
  type: ChannelType
  enabled: boolean
  language: 'en'
  email: {
    smtp_host: string
    smtp_port: string
    smtp_username: string
    smtp_password: string
    smtp_password_configured: boolean
    from_email: string
    email_subject: string
    to_emails: string
    use_tls: boolean
    encryption: 'none' | 'starttls' | 'ssl'
  }
  webhook: { url: string; method: string; headers: string; timeout: number }
  dingtalk: { webhook_url: string; secret: string; secret_configured: boolean; secret_editing: boolean; at_mobiles: string; at_user_ids: string; is_at_all: boolean }
  wecom: { webhook_url: string; mentioned_list: string; mentioned_mobile_list: string; is_at_all: boolean }
  sms: {
    url: string
    phone_numbers: string
    api_key: string
    message_template: string
  }
}

export function defaultNotificationChannelForm(): NotificationChannelFormState {
  return {
    name: '',
    type: 'email',
    enabled: true,
    language: 'en',
    email: {
      smtp_host: '',
      smtp_port: '587',
      smtp_username: '',
      smtp_password: '',
      smtp_password_configured: false,
      from_email: '',
      email_subject: '',
      to_emails: '',
      use_tls: true,
      encryption: 'starttls',
    },
    webhook: { url: '', method: 'POST', headers: '', timeout: 30 },
    dingtalk: { webhook_url: '', secret: '', secret_configured: false, secret_editing: false, at_mobiles: '', at_user_ids: '', is_at_all: false },
    wecom: { webhook_url: '', mentioned_list: '', mentioned_mobile_list: '', is_at_all: false },
    sms: {
      url: '',
      phone_numbers: '',
      api_key: '',
      message_template: '[HyperFileLens] {event_type}',
    },
  }
}

export function buildNotificationChannelConfig(form: NotificationChannelFormState): Record<string, unknown> {
  const base = { language: form.language }
  const splitList = (value: string) => value.split(',').map((item) => item.trim()).filter(Boolean)
  if (form.type === 'email') {
    const useTls = form.email.encryption === 'starttls'
    const useSsl = form.email.encryption === 'ssl'
    return {
      ...base,
      smtp_host: form.email.smtp_host,
      smtp_port: form.email.smtp_port,
      smtp_username: form.email.smtp_username,
      smtp_password: form.email.smtp_password,
      from_email: form.email.from_email,
      email_subject: form.email.email_subject,
      to_emails: form.email.to_emails,
      use_tls: useTls,
      use_ssl: useSsl,
      encryption: form.email.encryption,
    }
  }
  if (form.type === 'webhook') {
    let headers: Record<string, string> = {}
    try {
      if (form.webhook.headers.trim()) headers = JSON.parse(form.webhook.headers)
    } catch {
      /* ignore */
    }
    return {
      ...base,
      url: form.webhook.url,
      method: form.webhook.method,
      headers,
      timeout_seconds: form.webhook.timeout,
    }
  }
  if (form.type === 'dingtalk') {
    const secret = form.dingtalk.secret.trim()
    return {
      ...base,
      webhook_url: form.dingtalk.webhook_url,
      ...(secret
        ? { secret: form.dingtalk.secret }
        : form.dingtalk.secret_editing && form.dingtalk.secret_configured
          ? { clear_secret: true }
          : {}),
      at_mobiles: splitList(form.dingtalk.at_mobiles),
      at_user_ids: splitList(form.dingtalk.at_user_ids),
      is_at_all: form.dingtalk.is_at_all,
    }
  }
  if (form.type === 'sms') {
    return {
      ...base,
      url: form.sms.url,
      phone_numbers: form.sms.phone_numbers,
      api_key: form.sms.api_key,
      message_template: form.sms.message_template,
    }
  }
  return {
    ...base,
    webhook_url: form.wecom.webhook_url,
    mentioned_list: form.wecom.is_at_all ? ['@all'] : splitList(form.wecom.mentioned_list),
    mentioned_mobile_list: splitList(form.wecom.mentioned_mobile_list),
    is_at_all: form.wecom.is_at_all,
  }
}

export function loadNotificationChannelConfig(
  form: NotificationChannelFormState,
  type: ChannelType,
  config: Record<string, unknown>,
) {
  form.language = 'en'
  const listToText = (value: unknown) => Array.isArray(value) ? value.join(', ') : String(value || '')
  if (type === 'email') {
    const encryption =
      config.encryption === 'ssl' || config.use_ssl === true
        ? 'ssl'
        : config.encryption === 'none' || config.use_tls === false
          ? 'none'
          : 'starttls'
    Object.assign(form.email, {
      smtp_host: config.smtp_host || '',
      smtp_port: String(config.smtp_port || '587'),
      smtp_username: config.smtp_username || '',
      smtp_password: '',
      smtp_password_configured: Boolean(String(config.smtp_password || '').trim()),
      from_email: config.from_email || '',
      email_subject: config.email_subject || '',
      to_emails: config.to_emails || config.to || '',
      use_tls: config.use_tls !== false,
      encryption,
    })
  } else if (type === 'webhook') {
    form.webhook.url = String(config.url || '')
    form.webhook.method = String(config.method || 'POST')
    form.webhook.headers =
      typeof config.headers === 'object' ? JSON.stringify(config.headers, null, 2) : ''
    form.webhook.timeout = Number(config.timeout_seconds || 30)
  } else if (type === 'dingtalk') {
    form.dingtalk.webhook_url = String(config.webhook_url || config.url || '')
    form.dingtalk.secret = ''
    form.dingtalk.secret_configured = Boolean(String(config.secret || '').trim())
    form.dingtalk.secret_editing = false
    form.dingtalk.at_mobiles = listToText(config.at_mobiles)
    form.dingtalk.at_user_ids = listToText(config.at_user_ids)
    form.dingtalk.is_at_all = config.is_at_all === true
  } else if (type === 'sms') {
    form.sms.url = String(config.url || config.api_url || '')
    form.sms.phone_numbers = String(config.phone_numbers || config.phones || '')
    form.sms.api_key = ''
    form.sms.message_template = String(config.message_template || '[HyperFileLens] {event_type}')
  } else {
    form.wecom.webhook_url = String(config.webhook_url || config.url || '')
    const mentionedList = Array.isArray(config.mentioned_list) ? config.mentioned_list : []
    form.wecom.is_at_all = config.is_at_all === true || mentionedList.includes('@all')
    form.wecom.mentioned_list = form.wecom.is_at_all ? '' : mentionedList.join(', ')
    form.wecom.mentioned_mobile_list = listToText(config.mentioned_mobile_list)
  }
}
