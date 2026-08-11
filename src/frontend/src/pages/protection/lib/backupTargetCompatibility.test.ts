import { describe, expect, it } from 'vitest'
import {
  backupTargetIncompatibilityReason,
  backupTargetIncompatibilityReasonForSources,
  isBackupTargetCompatible,
  requiresCrossProxyRepositoryServerHost,
} from './backupTargetCompatibility'

describe('isBackupTargetCompatible', () => {
  it('allows a proxy-bound repository on the same proxy without server mode', () => {
    expect(isBackupTargetCompatible(
      { sourceType: 'nas', boundNodeId: 4 },
      { repoType: 'NAS', bindNodeType: 'proxy', bindNodeId: 4, crossProxyReady: false },
    )).toBe(true)
  })

  it('requires an explicit host only when a NAS source uses another proxy', () => {
    expect(requiresCrossProxyRepositoryServerHost([
      { sourceType: 'nas', boundNodeId: 4 },
      { sourceType: 'host', boundNodeId: 8 },
    ], 4)).toBe(false)
    expect(requiresCrossProxyRepositoryServerHost([
      { sourceType: 'nas', boundNodeId: 4 },
      { sourceType: 'nas', boundNodeId: 7 },
    ], 4)).toBe(true)
  })

  it('allows a different proxy only when repository server access is ready', () => {
    const source = { sourceType: 'nas' as const, boundNodeId: 4 }
    expect(isBackupTargetCompatible(source, {
      repoType: 'NAS', bindNodeType: 'proxy', bindNodeId: 1, crossProxyReady: true,
    })).toBe(true)
    expect(isBackupTargetCompatible(source, {
      repoType: 'PROXY_FS', bindNodeType: 'proxy', bindNodeId: 1, crossProxyReady: false,
    })).toBe(false)
  })

  it('does not restrict agent or unbound repositories', () => {
    expect(isBackupTargetCompatible(
      { sourceType: 'host', platform: 'linux', boundNodeId: null },
      { repoType: 'NAS', bindNodeType: 'proxy', bindNodeId: 1, crossProxyReady: false },
    )).toBe(true)
    expect(isBackupTargetCompatible(
      { sourceType: 'nas', boundNodeId: 4 },
      { repoType: 'NAS', bindNodeType: null, bindNodeId: null },
    )).toBe(true)
  })

  it.each(['windows', 'macos', undefined])(
    'rejects direct NAS for a %s host platform',
    (platform) => {
      const source = { sourceType: 'host' as const, platform }
      const directNas = { repoType: 'NAS', bindNodeType: null, bindNodeId: null }
      expect(backupTargetIncompatibilityReason(source, directNas))
        .toBe('direct_nas_linux_only')
      expect(isBackupTargetCompatible(source, directNas)).toBe(false)
    },
  )

  it('allows direct NAS only for confirmed Linux hosts', () => {
    expect(backupTargetIncompatibilityReason(
      { sourceType: 'host', platform: 'linux' },
      { repoType: 'NAS', bindNodeType: null, bindNodeId: null },
    )).toBeNull()
  })

  it('keeps proxy-bound NAS, ProxyFS, and S3 available to non-Linux hosts', () => {
    const source = { sourceType: 'host' as const, platform: 'windows' }
    expect(isBackupTargetCompatible(source, {
      repoType: 'NAS', bindNodeType: 'proxy', bindNodeId: 1,
    })).toBe(true)
    expect(isBackupTargetCompatible(source, {
      repoType: 'PROXY_FS', bindNodeType: 'proxy', bindNodeId: 1,
    })).toBe(true)
    expect(isBackupTargetCompatible(source, { repoType: 'S3' })).toBe(true)
  })

  it('rejects direct NAS for a batch containing any non-Linux host', () => {
    const directNas = { repoType: 'NAS', bindNodeType: null, bindNodeId: null }
    expect(backupTargetIncompatibilityReasonForSources([
      { sourceType: 'host', platform: 'linux' },
      { sourceType: 'host', platform: 'windows' },
    ], directNas)).toBe('direct_nas_linux_only')
    expect(backupTargetIncompatibilityReasonForSources([
      { sourceType: 'host', platform: 'linux' },
      { sourceType: 'host', platform: 'linux' },
    ], directNas)).toBeNull()
  })
})
