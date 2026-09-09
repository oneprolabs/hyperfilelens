/** Display name only; never changes the NAS connection or storage path. */
export function buildNasRepositoryName(protocol: 'smb' | 'nfs', server: string, path: string): string {
  const host = server.trim()
  if (!host) return ''
  const normalizedPath = path.trim().replace(/\\/g, '/')
  const leaf = normalizedPath.split('/').filter(Boolean).at(-1)
  const location = leaf ? `${host}/${leaf}` : normalizedPath ? `${host}/` : host
  return `${protocol.toUpperCase()}(${location})`
}
