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

  it('maps the structured error without depending on the fallback message', () => {
    expect(nasRepositoryFailureMessage(
      NAS_REPOSITORY_WRITE_DENIED,
      '',
      (key) => key,
    )).toBe('repositoriesPage.nasRepositoryWriteDenied')
  })
})
