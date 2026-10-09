import { describe, expect, it } from 'vitest'
import { sourceTagNameIssue } from './sourceTagName'

const existing = [{ id: 1, name: 'Production' }, { id: 2, name: 'QA Tests' }]

describe('tag name rules', () => {
  it('rejects leading and trailing whitespace but allows spaces inside a name', () => {
    expect(sourceTagNameIssue('    QA Tests', existing)).toBe('boundarySpaces')
    expect(sourceTagNameIssue('QA Tests    ', existing)).toBe('boundarySpaces')
    expect(sourceTagNameIssue(' \tProduction\u3000', existing)).toBe('boundarySpaces')
    expect(sourceTagNameIssue('New Label', existing)).toBeNull()
  })

  it('rejects empty, overly long and case-insensitive duplicates', () => {
    expect(sourceTagNameIssue('    ', existing)).toBe('required')
    expect(sourceTagNameIssue('x'.repeat(65), existing)).toBe('tooLong')
    expect(sourceTagNameIssue('production', existing)).toBe('duplicate')
    expect(sourceTagNameIssue('qa tests', existing)).toBe('duplicate')
  })

  it('allows an unchanged name while editing and names with internal spaces', () => {
    expect(sourceTagNameIssue('Production', existing, 1)).toBeNull()
    expect(sourceTagNameIssue('New Label', existing)).toBeNull()
    expect(sourceTagNameIssue('x'.repeat(64), existing)).toBeNull()
  })
})
