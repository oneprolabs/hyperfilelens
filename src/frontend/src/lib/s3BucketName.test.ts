import { describe, expect, it } from 'vitest'
import { generateS3BucketName, s3BucketNameError } from './s3BucketName'

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


describe('generated bucket names and AWS reserved names', () => {
  it('uses a padded local timestamp accepted by all managed providers', () => {
    const name = generateS3BucketName(new Date(2026, 8, 9, 8, 3, 5))
    expect(name).toBe('hfl-20260909080305')
    for (const platform of ['aws', 'aliyun', 'huaweicloud']) {
      expect(s3BucketNameError(platform, name)).toBeNull()
    }
  })
  it.each(['xn--bucket', 'sthree-bucket', 'amzn-s3-demo-bucket', 'bucket-s3alias',
    'bucket--ol-s3', 'bucket.mrap', 'bucket--x-s3', 'bucket--table-s3'])(
    'rejects AWS reserved name %s', (name) => {
      expect(s3BucketNameError('aws', name)).toBe('aws_reserved')
      expect(s3BucketNameError('custom', name)).toBeNull()
    },
  )
})
