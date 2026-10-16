import { describe, expect, it } from 'vitest'
import { isRepositoryDeletionAvailable } from './repositoryResidualState'

describe('repository deletion availability', () => {
  it('allows an old removed repository with residual locations to use Delete', () => {
    expect(isRepositoryDeletionAvailable({
      status: 'removed',
      initialization_state: 'attention_required',
    })).toBe(true)
  })

  it('does not allow already released tombstones or a running deletion', () => {
    expect(isRepositoryDeletionAvailable({
      status: 'removed',
      initialization_state: 'released',
    })).toBe(false)
    expect(isRepositoryDeletionAvailable({ status: 'removed' })).toBe(false)
    expect(isRepositoryDeletionAvailable({
      status: 'removing',
      initialization_state: 'attention_required',
    })).toBe(false)
  })

  it.each(['created', 'create_failed', 'remove_failed', 'creating'])(
    'preserves existing deletion availability for %s',
    (status) => {
      expect(isRepositoryDeletionAvailable({ status })).toBe(true)
    },
  )
})
