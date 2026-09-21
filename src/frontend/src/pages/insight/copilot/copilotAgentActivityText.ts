/**
 * SourceLens runtimeEvents.formatDuration — keep activity elapsed copy aligned.
 */
export function formatAgentActivityDuration(
  seconds: number | null | undefined,
): string {
  if (seconds == null) return ''
  const roundedSeconds = Math.round(seconds)
  if (roundedSeconds < 60) return `${roundedSeconds}s`
  return `${Math.floor(roundedSeconds / 60)}m ${roundedSeconds % 60}s`
}

/** Append ` · 14s` / ` · 1m 5s` the same way SourceLens builds progress text. */
export function withAgentActivityDuration(
  text: string,
  seconds: number | null | undefined,
): string {
  const duration = formatAgentActivityDuration(seconds)
  return duration ? `${text} · ${duration}` : text
}
