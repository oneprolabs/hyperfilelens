export type S3StoragePlatform =
  | 'aliyun'
  | 'huaweicloud'
  | 'tencent'
  | 'aws'
  | 'gcp'
  | 'azure'
  | 'other'
  | 'custom'

export function generateS3ObjectPrefix(now = new Date()): string {
  const year = now.getFullYear() % 10
  const month = 'ABCDEFGHIJKL'[now.getMonth()]
  const day = String(now.getDate()).padStart(2, '0')
  const time = [now.getHours(), now.getMinutes(), now.getSeconds()]
    .map((part) => String(part).padStart(2, '0'))
    .join('')
  return `hfl${year}${month}${day}${time}/`
}

const PLATFORM_LABEL_KEYS: Record<S3StoragePlatform, string> = {
  aliyun: 'addS3Repo.platformAliyun',
  huaweicloud: 'addS3Repo.platformHuawei',
  tencent: 'addS3Repo.platformTencent',
  aws: 'addS3Repo.platformAws',
  gcp: 'addS3Repo.platformGcp',
  azure: 'addS3Repo.platformAzure',
  other: 'addS3Repo.platformOtherS3',
  custom: 'addS3Repo.platformOtherS3',
}

/** Cloud brand icons from newmuse; `other` / `custom` use the shared Database lucide icon in UI. */
export const S3_PLATFORM_ICON_URLS: Record<S3StoragePlatform, string> = {
  aliyun: '/cloud-platforms/aliyun.svg',
  huaweicloud: '/cloud-platforms/huawei.svg',
  tencent: '/cloud-platforms/tencent.svg',
  aws: '/cloud-platforms/aws.svg',
  gcp: '/cloud-platforms/gcp.svg',
  azure: '/cloud-platforms/azure.svg',
  other: '/cloud-platforms/other.svg',
  custom: '/cloud-platforms/other.svg',
}

export function normalizeS3StoragePlatform(raw: string | undefined | null): S3StoragePlatform {
  const value = (raw || '').trim().toLowerCase()
  if (value === 'aliyun' || value === 'huaweicloud' || value === 'tencent' || value === 'aws') return value
  if (value === 'gcp') return 'gcp'
  if (value === 'azure') return 'azure'
  if (value === 'other') return 'other'
  return 'custom'
}

export function s3PlatformIconUrl(platform: string | undefined | null): string {
  return S3_PLATFORM_ICON_URLS[normalizeS3StoragePlatform(platform)]
}

export function s3PlatformLabelKey(platform: string | undefined | null): string {
  return PLATFORM_LABEL_KEYS[normalizeS3StoragePlatform(platform)]
}

export function normalizeS3EndpointInput(endpoint?: string | null): string {
  return (endpoint || '').trim().replace(/^https?:\/\//i, '')
}

export function s3EndpointDisplay(endpoint?: string | null) {
  const raw = normalizeS3EndpointInput(endpoint)
  if (!raw) return '—'
  return raw
}

export function distinctS3EndpointPair(
  externalEndpoint?: string | null,
  internalEndpoint?: string | null,
): { external: string; internal: string } | null {
  const external = normalizeS3EndpointInput(externalEndpoint).replace(/\.$/, '')
  const internal = normalizeS3EndpointInput(internalEndpoint).replace(/\.$/, '')
  if (!external || !internal || external.toLowerCase() === internal.toLowerCase()) return null
  return { external, internal }
}

/**
 * Normalize an S3 object prefix: trim whitespace, strip any leading slashes,
 * and append a trailing `/` when the value is non-empty. Returns an empty
 * string when the value is empty/whitespace-only.
 */
export function normalizeS3ObjectPrefix(value?: string | null): string {
  const raw = (value || '').trim()
  if (!raw) return ''
  let normalized = raw.replace(/^\/+/, '').replace(/\/+$/, '')
  if (!normalized) return ''
  normalized = normalized.replace(/\/+/g, '/')
  return `${normalized}/`
}
