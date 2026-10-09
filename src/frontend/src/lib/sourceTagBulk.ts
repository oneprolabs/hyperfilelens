import type { SourceTag } from './sourceApi'

export type SourceTagBulkOperation = 'add' | 'remove'
type Assignments = Record<string, SourceTag[]>

/** An unknown source stays actionable until its assignments can be refreshed. */
export function canUnbindSelectedSources(sourceIds: string[], assignments: Assignments): boolean {
  return sourceIds.some((sourceId) =>
    !Object.prototype.hasOwnProperty.call(assignments, sourceId)
    || assignments[sourceId].length > 0
  )
}

export function bulkTagOptions(
  operation: SourceTagBulkOperation,
  catalog: SourceTag[],
  sourceIds: string[],
  assignments: Assignments,
): SourceTag[] {
  if (operation === 'add') {
    if (!sourceIds.length) return catalog
    return catalog.filter((tag) => !sourceIds.every((sourceId) =>
      (assignments[sourceId] || []).some((assigned) => assigned.id === tag.id)
    ))
  }
  return catalog.filter((tag) => sourceIds.some((sourceId) =>
    (assignments[sourceId] || []).some((assigned) => assigned.id === tag.id)
  ))
}

export function countBulkTagChanges(
  operation: SourceTagBulkOperation,
  sourceIds: string[],
  tagIds: number[],
  assignments: Assignments,
): number {
  return sourceIds.reduce((count, sourceId) =>
    count + tagIds.filter((tagId) => {
      const alreadyAssigned = (assignments[sourceId] || []).some((tag) => tag.id === tagId)
      return operation === 'add' ? !alreadyAssigned : alreadyAssigned
    }).length, 0)
}

export function applyBulkTagChanges(
  operation: SourceTagBulkOperation,
  sourceIds: string[],
  tagIds: number[],
  assignments: Assignments,
  catalog: SourceTag[],
): Assignments {
  return Object.fromEntries(sourceIds.map((sourceId) => {
    const current = assignments[sourceId] || []
    return [sourceId, operation === 'add'
      ? [...current, ...catalog.filter((tag) =>
        tagIds.includes(tag.id) && !current.some((assigned) => assigned.id === tag.id)
      )]
      : current.filter((tag) => !tagIds.includes(tag.id))]
  }))
}
