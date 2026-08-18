import { describe, expect, it } from 'vitest'
import { resolveErrorMessage, resolveErrorMessageI18n } from './resolver'

const messages: Record<string, string> = {
  'errors.codes.subscriptionQuotaExceeded':
    'Organization quota is full. Contact your platform administrator to raise limits.',
  'errors.codes.subscriptionQuotaExceededMeter':
    '{meter} quota is full for this organization. Contact your platform administrator to raise limits.',
  'errors.codes.subscriptionQuotaExceededInstance':
    'Shared instance capacity is full. Contact your platform administrator to raise the deployment grant or free capacity from other organizations.',
  'errors.codes.subscriptionQuotaExceededInstanceMeter':
    'Shared instance {meter} capacity is full. Contact your platform administrator to raise the deployment grant or free capacity from other organizations.',
  'errors.codes.subscriptionQuotaExceededGateway':
    'This Public Data Gateway is at capacity. Contact your platform administrator to raise the workspace limit on this gateway.',
  'licenseQuota.users': 'Users',
  'licenseQuota.publicGatewayCapacity': 'Public Gateway Capacity',
}

function t(key: string, params?: Record<string, unknown>) {
  const template = messages[key] || key
  if (!params) return template
  return template.replace(/\{(\w+)\}/g, (_, name: string) => String(params[name] ?? ''))
}

describe('subscription quota error messages', () => {
  it('uses organization meter copy when scope is organization', () => {
    const message = resolveErrorMessageI18n(
      {
        status: 403,
        errorCode: 'SUBSCRIPTION.QUOTA_EXCEEDED',
        message: 'blocked',
        meta: { quota_type: 'max_users', scope: 'organization', limit: 10, used: 10 },
      },
      t,
    )
    expect(message).toContain('Users quota is full for this organization')
  })

  it('uses instance meter copy when scope is instance', () => {
    const message = resolveErrorMessageI18n(
      {
        status: 403,
        errorCode: 'SUBSCRIPTION.QUOTA_EXCEEDED',
        message: 'blocked',
        meta: { quota_type: 'max_users', scope: 'instance', limit: 500, used: 500 },
      },
      t,
    )
    expect(message).toContain('Shared instance Users capacity is full')
  })

  it('uses gateway capacity copy for per-gateway infra limits', () => {
    const message = resolveErrorMessageI18n(
      {
        status: 403,
        errorCode: 'SUBSCRIPTION.QUOTA_EXCEEDED',
        message: 'blocked',
        meta: { quota_type: 'gateway.public_capacity_bytes', scope: 'gateway' },
      },
      t,
    )
    expect(message).toContain('This Public Data Gateway is at capacity')
  })

  it('falls back to English without a translate function', () => {
    const message = resolveErrorMessage({
      status: 403,
      errorCode: 'SUBSCRIPTION.QUOTA_EXCEEDED',
      message: 'blocked',
      meta: { quota_type: 'max_object_storage', scope: 'organization' },
    })
    expect(message).toContain('Object Storage quota is full for this organization')
  })
})
