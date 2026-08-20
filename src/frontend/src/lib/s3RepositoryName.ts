export function buildS3RepositoryName(platformName: string | undefined, bucket: string): string {
  const prefix = (platformName || '').trim()
  const bucketName = (bucket || '').trim()
  if (!prefix) return ''
  return bucketName ? `${prefix}(${bucketName})` : prefix
}
