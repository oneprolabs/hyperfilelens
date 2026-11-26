import { describe, expect, it } from 'vitest'
import { buildS3RepositoryName } from './s3RepositoryName'

describe('buildS3RepositoryName', () => {
  it.each([
    ['S3-Compatible Storage', 'backup-prod', 'S3-Compatible Storage (backup-prod)'],
    ['Alibaba Cloud', 'backup-prod', 'Alibaba Cloud (backup-prod)'],
    ['Huawei Cloud', 'backup-prod', 'Huawei Cloud (backup-prod)'],
    ['Tencent Cloud', 'backup-prod', 'Tencent Cloud (backup-prod)'],
    ['AWS', 'backup-prod', 'AWS (backup-prod)'],
    ['RustFS', 'zjbtest', 'RustFS (zjbtest)'],
    ['MinIO', 'zjbtest', 'MinIO (zjbtest)'],
  ])('uses the platform name prefix for %s', (platformName, bucket, expected) => {
    expect(buildS3RepositoryName(platformName, bucket)).toBe(expected)
  })

  it('returns the platform name until a bucket is available', () => {
    expect(buildS3RepositoryName('S3-Compatible Storage', '  ')).toBe('S3-Compatible Storage')
    expect(buildS3RepositoryName('Alibaba Cloud', '')).toBe('Alibaba Cloud')
  })

  it('trims inputs and inserts exactly one space before the parentheses', () => {
    expect(buildS3RepositoryName('  RustFS  ', '  zjbtest  ')).toBe('RustFS (zjbtest)')
  })

  it('returns an empty name when the platform name is unavailable', () => {
    expect(buildS3RepositoryName(undefined, 'backup-prod')).toBe('')
    expect(buildS3RepositoryName('  ', 'backup-prod')).toBe('')
  })
})
