export type SourceTagNameIssue = 'required' | 'boundarySpaces' | 'tooLong' | 'duplicate' | null

export function sourceTagNameIssue(
  name: string,
  existing: Array<{ id: number; name: string }>,
  editingId?: number,
): SourceTagNameIssue {
  if (!name.trim()) return 'required'
  if (name !== name.trim()) return 'boundarySpaces'
  if (name.length > 64) return 'tooLong'
  if (existing.some((tag) =>
    tag.id !== editingId && tag.name.toLocaleLowerCase() === name.toLocaleLowerCase()
  )) return 'duplicate'
  return null
}
