import { describe, expect, it } from 'vitest'
import { s3BucketNameError } from './s3BucketName'

describe('managed S3 new-bucket names', () => {
  it.each(['backup-001', 'team.backup-001'])(
    'accepts an AWS or Huawei DNS-style name: %s',
    (name) => expect(s3BucketNameError('aws', name)).toBeNull(),
  )

  it.each([
    ['UPPERCASE', 'dns'],
    ['ab', 'dns'],
    ['team..backup', 'dns'],
    ['team.-backup', 'dns_label'],
    ['192.168.1.1', 'ip_address'],
  ])('rejects an invalid DNS-style name: %s', (name, error) => {
    expect(s3BucketNameError('huaweicloud', name)).toBe(error)
  })

  it('rejects periods for Aliyun', () => {
    expect(s3BucketNameError('aliyun', 'team.backup')).toBe('aliyun')
    expect(s3BucketNameError('aliyun', 'team-backup')).toBeNull()
  })

  it('leaves custom-provider rules to the provider', () => {
    expect(s3BucketNameError('custom', 'Legacy_Bucket')).toBeNull()
    expect(s3BucketNameError('other', 'Legacy_Bucket')).toBeNull()
  })
})
