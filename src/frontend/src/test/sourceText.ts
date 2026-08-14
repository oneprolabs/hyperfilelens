export function compactSourceText(source: string): string {
  return source
    .replace(/\s+/g, ' ')
    .replace(/\s+>(?=\s*(?:<|{{))/g, '>')
    .replace(/>\s+</g, '><')
    .replace(/>\s+(?={{)/g, '>')
    .replace(/}}\s+</g, '}}<')
    .trim()
}
