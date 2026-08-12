import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  clearDeployProfileCache,
  fetchDeployProfile,
  platformOpsEntryUrl,
  resolvePostLoginPath,
  shouldForceDeployProfileRefresh,
} from './useDeployProfile'

const deployProfile = {
  site_role: 'ops' as const,
  email_signup_enabled: false,
  platform_ops_enabled: true,
  password_reset_available: true,
  tenant_public_url: 'https://example.test:11443',
  admin_console_url: 'https://example.test:11444',
  landing_path: '/platform-ops/overview',
  admin_console_landing_path: '/platform-ops/overview',
  admin_console_entry_visible: false,
  platform_ops_access_allowed: true,
}

describe('deploy profile caching', () => {
  beforeEach(() => {
    clearDeployProfileCache()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('reuses an in-flight request even when callers force a refresh', async () => {
    let resolveResponse: ((value: Response) => void) | undefined
    const response = new Promise<Response>((resolve) => {
      resolveResponse = resolve
    })
    const fetchMock = vi.fn(() => response)
    vi.stubGlobal('fetch', fetchMock)

    const first = fetchDeployProfile(true)
    const second = fetchDeployProfile(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    resolveResponse?.(new Response(JSON.stringify(deployProfile), { status: 200 }))
    await expect(Promise.all([first, second])).resolves.toEqual([deployProfile, deployProfile])
  })

  it('serves internal Platform Ops navigation from cache', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(deployProfile), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await fetchDeployProfile(true)
    await fetchDeployProfile(false)

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('lands operations staff on the Admin Overview after login', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify(deployProfile), { status: 200 }),
    ))

    await expect(resolvePostLoginPath()).resolves.toBe('/platform-ops/overview')
  })

  it('builds the tenant-to-admin entry URL (community-safe default)', () => {
    expect(platformOpsEntryUrl('https://example.test:11444/')).toBe(
      'https://example.test:11444/platform-ops/engine/ai-settings',
    )
    expect(platformOpsEntryUrl('https://example.test:11444/', '/platform-ops/overview')).toBe(
      'https://example.test:11444/platform-ops/overview',
    )
    expect(platformOpsEntryUrl('')).toBe('')
  })

  it('does not use tenant site landing_path "/" for the Admin Console entry', () => {
    // Tenant post-login stays "/"; Admin deep-link must use admin_console_landing_path.
    expect(platformOpsEntryUrl('https://example.test:11444/', '/')).toBe(
      'https://example.test:11444/',
    )
    expect(
      platformOpsEntryUrl(
        'https://example.test:11444/',
        '/platform-ops/engine/ai-settings',
      ),
    ).toBe('https://example.test:11444/platform-ops/engine/ai-settings')
  })
})

describe('Platform Ops deploy profile refresh policy', () => {
  it('refreshes when entering Platform Ops from another shell', () => {
    expect(shouldForceDeployProfileRefresh(
      '/platform-ops/monitoring/host',
      '/ops/host-monitor',
    )).toBe(true)
  })

  it('does not refresh during internal Platform Ops navigation', () => {
    expect(shouldForceDeployProfileRefresh(
      '/platform-ops/users',
      '/platform-ops/monitoring/host',
    )).toBe(false)
  })

  it('ignores ordinary tenant navigation', () => {
    expect(shouldForceDeployProfileRefresh('/ops/tasks', '/')).toBe(false)
  })
})
