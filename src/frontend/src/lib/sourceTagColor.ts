/** Stored palette keys, never arbitrary user-provided CSS colors. */
export const SOURCE_TAG_COLORS = [
  'neutral', 'blue', 'green', 'orange', 'red', 'purple', 'teal', 'pink',
] as const

export type SourceTagColor = typeof SOURCE_TAG_COLORS[number]

export function sourceTagColor(value: string | undefined): SourceTagColor {
  return SOURCE_TAG_COLORS.find((color) => color === value) ?? 'neutral'
}
