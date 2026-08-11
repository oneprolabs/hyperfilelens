// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import {
  buildEnrollmentInstallCommand,
  auditPlatformGatewayEnrollmentCopy,
  fetchLifecycleWatch,
  getGatewayNode,
  fetchNodeMaintenanceRelease,
  issueGatewayEnrollmentInstall,
  issuePlatformGatewayEnrollmentInstall,
  previewNodeOperationsBatch,
  revokePlatformGatewayEnrollment,
  startNodeOperation,
  startNodeOperationsBatch,
  updateNode,
  fetchMinimalInstallerManifest,
} from './nodeApi'

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return {
    ...actual,
    api: vi.fn(),
  }
})

vi.mock('../composables/useAuth', () => ({
  getEffectiveOrgKey: vi.fn(() => 'tenant-a'),
}))

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

const installerManifest = {
  schema_version: 1,
  artifacts: {
    'linux-amd64': { filename: '0.1.0/hfl-installer-linux-amd64.tar.gz', sha256: 'a'.repeat(64), size: 100 },
    'linux-arm64': { filename: '0.1.0/hfl-installer-linux-arm64.tar.gz', sha256: 'b'.repeat(64), size: 100 },
    'darwin-amd64': { filename: '0.1.0/hfl-installer-darwin-amd64.tar.gz', sha256: 'c'.repeat(64), size: 100 },
    'darwin-arm64': { filename: '0.1.0/hfl-installer-darwin-arm64.tar.gz', sha256: 'd'.repeat(64), size: 100 },
    'windows-amd64': { filename: '0.1.0/hfl-installer-windows-amd64.zip', sha256: 'e'.repeat(64), size: 100 },
  },
}

describe('Minimal installer metadata', () => {
  it('accepts the standard API envelope used by local and production consoles', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        code: 0,
        message: 'success',
        data: installerManifest,
      }),
    }))

    await expect(fetchMinimalInstallerManifest()).resolves.toEqual(installerManifest)
  })

  it('rejects malformed artifact metadata before issuing a command', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ...installerManifest,
        artifacts: {
          ...installerManifest.artifacts,
          'linux-amd64': {
            filename: '../unexpected.tar.gz',
            sha256: 'invalid',
            size: 0,
          },
        },
      }),
    }))

    await expect(fetchMinimalInstallerManifest()).rejects.toThrow(
      'Minimal installer metadata is invalid',
    )
  })
})

describe('Data Gateway enrollment', () => {
  it('loads Public Gateway details from the platform-scoped endpoint', async () => {
    vi.mocked(api).mockResolvedValue({ id: 42, role: 'gateway' })

    await expect(getGatewayNode(42, 'platform')).resolves.toMatchObject({ id: 42 })
    expect(vi.mocked(api)).toHaveBeenCalledWith(
      '/api/v1/platform-ops/lens/gateways/42',
      undefined,
    )
  })

  it('updates Public Gateway settings through the platform-scoped endpoint', async () => {
    vi.mocked(api).mockResolvedValue({ id: 42, role: 'gateway', name: 'gateway-b' })

    await expect(updateNode(42, { name: 'gateway-b' }, 'platform')).resolves.toMatchObject({
      name: 'gateway-b',
    })
    expect(vi.mocked(api)).toHaveBeenCalledWith(
      '/api/v1/platform-ops/lens/gateways/42',
      { method: 'PATCH', body: JSON.stringify({ name: 'gateway-b' }) },
    )
  })

  it('uses an existing-node maintenance endpoint for signed release downloads', async () => {
    vi.mocked(api).mockResolvedValue({
      version: '1.0.1',
      platform: 'linux',
      arch: 'amd64',
      download_url: 'https://console.example/media/agent-releases/1.0.1/agent.tar.gz?t=signed',
      expires_in: 600,
      tls_verify: true,
    })

    await expect(fetchNodeMaintenanceRelease({ nodeId: 42, scope: 'platform' })).resolves.toMatchObject({
      version: '1.0.1',
      tls_verify: true,
    })
    expect(vi.mocked(api)).toHaveBeenCalledWith(
      '/api/v1/platform-ops/lens/gateways/42/maintenance-release',
      { method: 'POST', body: JSON.stringify({}) },
    )
  })

  it('uses a short bootstrap one-liner with strict TLS for tenant Gateways', async () => {
    vi.stubGlobal('window', {
      location: { origin: 'https://hyperfilelens.com' },
    })
    vi.mocked(api).mockResolvedValue({
      id: 18,
      token: 'tenant-token',
      role: 'gateway',
      is_active: true,
      tls_verify: true,
    })

    const result = await issueGatewayEnrollmentInstall({ orgKey: 'tenant-a' })

    expect(result.command).toContain("curl --proto '=https' --tlsv1.2")
    expect(result.command).toContain('/api/v1/node/enrollment/bootstrap-gateway?')
    expect(result.command).toContain("| sudo bash -c 'cd / || cd /tmp; exec bash -s'")
    expect(result.command).not.toContain('curl -k')
    expect(result.command).not.toContain('--progress-bar')
    expect(result.command).not.toContain('installer.tar.gz')
    expect(result.command.split('\n')).toHaveLength(1)
    expect(result.tlsVerify).toBe(true)
  })

  it('uses the tenant API base returned by the Admin Console API', async () => {
    vi.stubGlobal('window', {
      location: { origin: 'https://console.example.com:11444' },
    })
    vi.mocked(api).mockResolvedValue({
      token: 'platform-token',
      token_id: 17,
      org_key: '__platform_lens__',
      gateway_scope: 'platform',
      api_base: 'https://console.example.com:11443',
      tls_verify: true,
      expires_at: '2026-07-28T06:00:00Z',
    })

    const result = await issuePlatformGatewayEnrollmentInstall()

    expect(result.command).toContain("curl --proto '=https' --tlsv1.2")
    expect(result.command).toContain('api_base=https%3A%2F%2Fconsole.example.com%3A11443')
    expect(result.command).toContain('/api/v1/node/enrollment/bootstrap-gateway?')
    expect(result.command).not.toContain('curl -k')
    expect(result.tlsVerify).toBe(true)
    expect(result.expiresAt).toBe('2026-07-28T06:00:00Z')
    expect(result.orgKey).toBe('__platform_lens__')
    expect(result.apiBase).toBe('https://console.example.com:11443')
    expect(result.command).not.toContain('11444')
    expect(vi.mocked(api)).toHaveBeenCalledWith(
      '/api/v1/platform-ops/lens/gateways/enrollment',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ note: 'deploy:platform-gateway' }),
      }),
    )
  })

  it('keeps the explicit insecure mode for self-hosted deployments', async () => {
    vi.mocked(api).mockResolvedValue({
      token: 'platform-token',
      token_id: 17,
      org_key: '__platform_lens__',
      gateway_scope: 'platform',
      api_base: 'https://console.example.com:11443',
      tls_verify: false,
    })

    const result = await issuePlatformGatewayEnrollmentInstall()

    expect(result.command).toMatch(/^curl -k --fail --show-error --location '/)
    expect(result.command).toContain("| sudo bash -c 'cd / || cd /tmp; exec bash -s'")
    expect(result.command).not.toContain('--progress-bar')
    expect(result.command).not.toContain('WARNING:')
    expect(result.command.split('\n')).toHaveLength(1)
    expect(result.tlsVerify).toBe(false)
  })

  it('rejects an incomplete response instead of falling back to the Admin origin', async () => {
    vi.stubGlobal('window', {
      location: { origin: 'https://console.example.com:11444' },
    })
    vi.mocked(api).mockResolvedValue({
      token: 'platform-token',
      token_id: 17,
      org_key: '__platform_lens__',
      gateway_scope: 'platform',
      tls_verify: true,
    })

    await expect(issuePlatformGatewayEnrollmentInstall()).rejects.toThrow(
      'Public Data Gateway enrollment response is incomplete',
    )
  })

  it('uses dedicated platform endpoints to revoke and audit command copies', async () => {
    vi.mocked(api).mockResolvedValue({})

    await auditPlatformGatewayEnrollmentCopy(17)
    await revokePlatformGatewayEnrollment(17)

    expect(vi.mocked(api).mock.calls).toEqual([
      ['/api/v1/platform-ops/lens/gateways/enrollment/17/copied', { method: 'POST' }],
      ['/api/v1/platform-ops/lens/gateways/enrollment/17', { method: 'DELETE' }],
    ])
  })

  it('does not disable certificate validation in strict Windows commands', () => {
    const command = buildEnrollmentInstallCommand({
      org: 'tenant-a',
      role: 'agent',
      token: 'token-a',
      apiBase: 'https://console.example.com',
      os: 'windows',
      tlsVerify: true,
    })

    expect(command).toContain('powershell -NoProfile -ExecutionPolicy Bypass -Command')
    expect(command).toContain('/api/v1/node/enrollment/bootstrap?')
    expect(command).not.toContain('ServerCertificateValidationCallback')
    expect(command).not.toContain('Write-Warning')
  })

  it('keeps the Linux copy-paste command as a single curl one-liner', () => {
    const command = buildEnrollmentInstallCommand({
      org: 'tenant-a',
      role: 'agent',
      token: 'token-a',
      apiBase: 'https://console.example.com',
      os: 'linux',
      tlsVerify: true,
    })

    expect(command).toMatch(/^curl --proto '=https' --tlsv1\.2 --fail --show-error --location '/)
    expect(command).toContain('/api/v1/node/enrollment/bootstrap?')
    expect(command).toContain("| sudo bash -c 'cd / || cd /tmp; exec bash -s'")
    expect(command).not.toContain('--progress-bar')
    expect(command).not.toContain('installer.tar.gz')
    expect(command).not.toContain('mktemp')
    expect(command).not.toContain('WARNING:')
    expect(command.split('\n')).toHaveLength(1)
  })

  it('retains the explicit Windows bypass for self-hosted deployments', () => {
    const command = buildEnrollmentInstallCommand({
      org: 'tenant-a',
      role: 'agent',
      token: 'token-a',
      apiBase: 'https://console.example.com',
      os: 'windows',
      tlsVerify: false,
    })

    expect(command).toContain('ServerCertificateValidationCallback')
    expect(command).toContain('Write-Warning')
    expect(command).toContain('/api/v1/node/enrollment/bootstrap?')
  })
})

describe('platform Data Gateway lifecycle', () => {
  it('routes every lifecycle request through Platform Operations', async () => {
    vi.mocked(api).mockResolvedValue({ nodes: [] })

    await previewNodeOperationsBatch({
      kind: 'remove',
      nodeIds: [17],
      scope: 'platform',
    })
    await startNodeOperationsBatch({
      kind: 'remove',
      nodeIds: [17],
      scope: 'platform',
    })
    await startNodeOperation(17, 'remove', { scope: 'platform' })
    await fetchLifecycleWatch([17], 'platform')

    expect(vi.mocked(api).mock.calls.map(([path]) => path)).toEqual([
      '/api/v1/platform-ops/lens/gateways/operations/preview',
      '/api/v1/platform-ops/lens/gateways/operations/batch',
      '/api/v1/platform-ops/lens/gateways/17/operations',
      '/api/v1/platform-ops/lens/gateways/lifecycle-watch',
    ])
  })
})
