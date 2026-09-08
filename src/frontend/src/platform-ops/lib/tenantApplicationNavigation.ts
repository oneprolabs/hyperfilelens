import type { DeployProfile } from '../../composables/useDeployProfile'

export function tenantApplicationUrl(profile: DeployProfile | null): string {
  const value = profile?.tenant_public_url
  if (!value) return ''
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : ''
  } catch {
    return ''
  }
}

export async function openTenantApplication(
  loadProfile: () => Promise<DeployProfile | null>,
  navigate: (url: string) => void = (url) => window.location.assign(url),
): Promise<boolean> {
  const url = tenantApplicationUrl(await loadProfile())
  if (!url) return false
  navigate(url)
  return true
}
