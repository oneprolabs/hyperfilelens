export interface DeployProfile {
  site_role: 'tenant' | 'ops'
  /** Customer-facing Release version, without an edition-specific image suffix. */
  product_version?: string | null
  /** Public product edition shared by the tenant and Admin Console shells. */
  edition?: 'community' | 'enterprise'
  email_signup_enabled: boolean
  email_code_login_available: boolean
  platform_ops_enabled: boolean
  password_reset_available: boolean
  tenant_public_url: string
  admin_console_url: string
  /** Site-local post-login path (tenant "/" or ops AI Models / Overview). */
  landing_path: string
  /** Tenant → Admin Console deep link (community AI Models / EE Overview). */
  admin_console_landing_path: string
  admin_console_entry_visible: boolean
  platform_ops_access_allowed: boolean
  is_staff?: boolean
  /** Platform Console role when authenticated (EE AuthZ); null when anonymous. */
  platform_role?: string | null
  /** Granted platform action keys for nav / UI gating. */
  platform_permissions?: string[]
  /** Enterprise features granted by the current instance entitlement. */
  enterprise_features?: string[]
  support_org_key?: string | null
}

/** Community-safe Admin entry when deploy-profile omits admin_console_landing_path. */
export const PLATFORM_OPS_LANDING_PATH = '/platform-ops/engine/ai-settings'

let cachedProfile: DeployProfile | null = null
let inflight: Promise<DeployProfile | null> | null = null

function parseDeployProfilePayload(raw: unknown): DeployProfile | null {
  if (!raw || typeof raw !== 'object') return null
  const outer = raw as Record<string, unknown>
  const inner = outer.data
  if (inner && typeof inner === 'object' && 'site_role' in (inner as object)) {
    return inner as DeployProfile
  }
  if ('site_role' in outer) {
    return outer as DeployProfile
  }
  return null
}

export async function fetchDeployProfile(force = false): Promise<DeployProfile | null> {
  if (!force && cachedProfile) return cachedProfile
  if (inflight) return inflight

  inflight = (async () => {
    try {
      const res = await fetch('/api/v1/meta/deploy-profile', { credentials: 'include' })
      if (!res.ok) return null
      const payload = parseDeployProfilePayload(await res.json())
      cachedProfile = payload
      return payload
    } catch {
      return null
    } finally {
      inflight = null
    }
  })()

  return inflight
}

export function getCachedDeployProfile(): DeployProfile | null {
  return cachedProfile
}

export function clearDeployProfileCache(): void {
  cachedProfile = null
}

export function shouldForceDeployProfileRefresh(
  toPath: string,
  fromPath: string,
  requiresPlatformOps = false,
): boolean {
  const targetsPlatformOps = toPath.startsWith('/platform-ops') || requiresPlatformOps
  return targetsPlatformOps && !fromPath.startsWith('/platform-ops')
}

export function platformOpsEntryUrl(adminConsoleUrl: string, landingPath?: string): string {
  const origin = adminConsoleUrl.trim().replace(/\/+$/, '')
  if (!origin) return ''
  const path = (landingPath || PLATFORM_OPS_LANDING_PATH).trim()
  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${origin}${normalized}`
}

/** Resolve the deployment-specific post-login landing page. */
export async function resolvePostLoginPath(): Promise<string> {
  const profile = await fetchDeployProfile(true)
  const path = profile?.landing_path?.trim()
  return path && path.startsWith('/') ? path : '/'
}
