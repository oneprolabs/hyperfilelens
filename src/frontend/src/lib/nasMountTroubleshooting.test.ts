import { describe, expect, it } from 'vitest'
import {
  NAS_REPOSITORY_WRITE_DENIED,
  nasRepositoryFailureMessage,
} from './nasMountTroubleshooting'

describe('NAS repository failure guidance', () => {
  it('replaces the structured SMB write-denied error with localized guidance', () => {
    expect(nasRepositoryFailureMessage(
      NAS_REPOSITORY_WRITE_DENIED,
      'mkdir /mounted/share/hp-repos: permission denied',
      (key) => key,
    )).toBe('repositoriesPage.nasRepositoryWriteDenied')
  })

  it('preserves unrelated failure messages', () => {
    expect(nasRepositoryFailureMessage(
      'REPOSITORY_CREATE_FAILED',
      'mount SMB share: permission denied',
      (key) => key,
    )).toBe('mount SMB share: permission denied')
  })

  it('does not create an error message for a successful repository task', () => {
    expect(nasRepositoryFailureMessage('', '', (key) => key)).toBe('')
  })

  it('localizes a missing S3 bucket in a policy failure', () => {
    expect(nasRepositoryFailureMessage(
      'POLICY_APPLY_FAILED',
      'unable to read repository ownership marker: StatusCode: 404, api error NoSuchBucket: The specified bucket does not exist',
      (key) => key,
    )).toBe('ops.task.failureDetails.reason.repositoryBucketMissing')
  })

  it('does not infer a missing bucket from a generic 404', () => {
    expect(nasRepositoryFailureMessage(
      'POLICY_APPLY_FAILED',
      'unable to read repository ownership marker: StatusCode: 404, api error NoSuchKey',
      (key) => key,
    )).toBe('ops.task.failureDetails.reason.repositoryOwnershipMarkerReadFailed')
  })

  it('localizes ownership marker failures without hiding unrelated details', () => {
    expect(nasRepositoryFailureMessage(
      'POLICY_APPLY_FAILED',
      'unable to read repository ownership marker: permission denied',
      (key) => key,
    )).toBe('ops.task.failureDetails.reason.repositoryOwnershipMarkerReadFailed')
    expect(nasRepositoryFailureMessage('POLICY_APPLY_FAILED', 'network timeout', (key) => key))
      .toBe('network timeout')
  })

  it('maps the structured error without depending on the fallback message', () => {
    expect(nasRepositoryFailureMessage(
      NAS_REPOSITORY_WRITE_DENIED,
      '',
      (key) => key,
    )).toBe('repositoriesPage.nasRepositoryWriteDenied')
  })
})
