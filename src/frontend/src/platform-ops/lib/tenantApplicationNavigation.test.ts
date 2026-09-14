import { describe, expect, it, vi } from 'vitest'
import type { DeployProfile } from '../../composables/useDeployProfile'
import shellSource from '../layout/PlatformOpsShell.vue?raw'
import { openTenantApplication, tenantApplicationUrl } from './tenantApplicationNavigation'

function profile(tenantPublicUrl: string): DeployProfile {
  return {
    site_role: 'ops',
    email_signup_enabled: false,
    email_code_login_available: false,
    platform_ops_enabled: true,
    password_reset_available: true,
    tenant_public_url: tenantPublicUrl,
    admin_console_url: 'https://admin.example.test:11444',
    landing_path: '/platform-ops/overview',
    admin_console_landing_path: '/platform-ops/overview',
    admin_console_entry_visible: false,
    platform_ops_access_allowed: true,
  }
}

describe('Admin Console tenant application navigation', () => {
  it('forces a live profile refresh when Back to Application is clicked', () => {
    expect(shellSource).toContain('openTenantApplication(() => fetchDeployProfile(true))')
    expect(shellSource).not.toContain("onMounted(async () => {\n  const profile = await fetchDeployProfile()")
  })

  it('navigates with the URL returned by the latest profile request', async () => {
    const loadProfile = vi.fn().mockResolvedValue(profile('https://public.example.test:11443'))
    const navigate = vi.fn()

    await expect(openTenantApplication(loadProfile, navigate)).resolves.toBe(true)

    expect(loadProfile).toHaveBeenCalledTimes(1)
    expect(navigate).toHaveBeenCalledWith('https://public.example.test:11443/')
  })

  it('does not navigate when the refreshed profile is unavailable', async () => {
    const navigate = vi.fn()

    await expect(openTenantApplication(async () => null, navigate)).resolves.toBe(false)

    expect(navigate).not.toHaveBeenCalled()
  })

  it('rejects invalid and non-HTTP application URLs', () => {
    expect(tenantApplicationUrl(profile('not-a-url'))).toBe('')
    expect(tenantApplicationUrl(profile('javascript:alert(1)'))).toBe('')
  })
})
