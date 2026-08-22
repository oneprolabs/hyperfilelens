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
    'The shared Data Gateway currently has insufficient capacity. Try again later or contact your platform administrator.',
  'errors.codes.subscriptionQuotaUsageUnavailable':
    'Capacity information is temporarily unavailable. Try again shortly.',
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
    expect(message).toContain('shared Data Gateway currently has insufficient capacity')
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

  it('maps temporarily unavailable quota usage without exposing diagnostics', () => {
    const message = resolveErrorMessageI18n(
      {
        status: 503,
        errorCode: 'SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE',
        message: 'internal meter diagnostic',
        meta: { quota_type: 'max_public_gateway_capacity_bytes', scope: 'instance' },
      },
      t,
    )

    expect(message).toBe('Capacity information is temporarily unavailable. Try again shortly.')
    expect(message).not.toContain('internal meter diagnostic')
  })
})
