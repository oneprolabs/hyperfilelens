const DNS_STYLE_PLATFORMS = new Set(['aws', 'huaweicloud'])
const DNS_BUCKET_PATTERN = /^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/
const ALIYUN_BUCKET_PATTERN = /^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$/

function isIpv4Address(value: string): boolean {
  const parts = value.split('.')
  return parts.length === 4 && parts.every((part) => {
    if (!/^\d{1,3}$/.test(part)) return false
    const number = Number(part)
    return number >= 0 && number <= 255
  })
}

export type S3BucketNameError = 'aliyun' | 'dns' | 'dns_label' | 'ip_address' | null

/** Mirror managed-provider naming rules for immediate form feedback.
 *
 * The backend and provider remain authoritative. Custom providers are not
 * restricted because S3-compatible implementations can use different rules.
 */
export function s3BucketNameError(platform: unknown, bucket: unknown): S3BucketNameError {
  const normalizedPlatform = String(platform || '').trim().toLowerCase()
  const name = String(bucket || '').trim()
  if (normalizedPlatform === 'aliyun') {
    return ALIYUN_BUCKET_PATTERN.test(name) ? null : 'aliyun'
  }
  if (!DNS_STYLE_PLATFORMS.has(normalizedPlatform)) return null
  if (!DNS_BUCKET_PATTERN.test(name) || name.includes('..')) return 'dns'
  if (name.split('.').some((label) => !label || label.startsWith('-') || label.endsWith('-'))) {
    return 'dns_label'
  }
  if (isIpv4Address(name)) return 'ip_address'
  return null
}
