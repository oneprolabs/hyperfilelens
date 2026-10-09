import { describe, expect, it } from 'vitest'
import type { SourceTag } from './sourceApi'
import { applyBulkTagChanges, bulkTagOptions, canUnbindSelectedSources, countBulkTagChanges } from './sourceTagBulk'

const tags = [
  { id: 1, name: 'Keep' },
  { id: 2, name: 'Change' },
  { id: 3, name: 'Other' },
] as SourceTag[]
const sources = ['agent:1', 'nas:2']
const assignments = { 'agent:1': [tags[0], tags[1]], 'nas:2': [tags[0]] }

describe('bulk tag changes', () => {
  it('only disables unbind when every selected source is known to have no tags', () => {
    expect(canUnbindSelectedSources([], {})).toBe(false)
    expect(canUnbindSelectedSources(['agent:1'], { 'agent:1': [] })).toBe(false)
    expect(canUnbindSelectedSources(sources, { 'agent:1': [], 'nas:2': [tags[1]] })).toBe(true)
    expect(canUnbindSelectedSources(['agent:1'], {})).toBe(true)
  })
  it('counts only the associations that actually change', () => {
    expect(countBulkTagChanges('add', sources, [2], assignments)).toBe(1)
    expect(countBulkTagChanges('remove', sources, [2], assignments)).toBe(1)
    expect(bulkTagOptions('remove', tags, sources, assignments).map((tag) => tag.id)).toEqual([1, 2])
  })

  it('hides tags already bound to every selected source, but keeps partially bound tags actionable', () => {
    expect(bulkTagOptions('add', tags, ['agent:1'], assignments).map((tag) => tag.id)).toEqual([3])
    expect(bulkTagOptions('add', tags, sources, assignments).map((tag) => tag.id)).toEqual([2, 3])
    expect(bulkTagOptions('add', tags, ['agent:unknown'], {}).map((tag) => tag.id)).toEqual([1, 2, 3])
    expect(bulkTagOptions('add', tags, [], assignments).map((tag) => tag.id)).toEqual([1, 2, 3])
  })

  it('adds or removes the chosen tags without replacing unrelated labels', () => {
    const added = applyBulkTagChanges('add', sources, [2], assignments, tags)
    expect(added['agent:1'].map((tag) => tag.id)).toEqual([1, 2])
    expect(added['nas:2'].map((tag) => tag.id)).toEqual([1, 2])
    const removed = applyBulkTagChanges('remove', sources, [2], assignments, tags)
    expect(removed['agent:1'].map((tag) => tag.id)).toEqual([1])
    expect(removed['nas:2'].map((tag) => tag.id)).toEqual([1])
  })
})
