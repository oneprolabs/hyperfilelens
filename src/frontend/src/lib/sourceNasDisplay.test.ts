import { describe, expect, it } from 'vitest'

import { enProtectionPages } from '../locales/enProtectionPages'
import { customMountPath } from './nasMountPath'
import {
  nasMountProtocol,
  nasMountSourceUri,
  nasPathKindLabelKey,
  nasProxyMountPoint,
  nasServerAddress,
  nasShareOrExport,
  SOURCE_PASSWORD_MASK,
  sourceNasStatusPresentation,
  sourcePasswordDisplay,
} from './sourceNasDisplay'

describe('nasMountSourceUri', () => {
  it('formats SMB mount source as //host/share', () => {
    expect(
      nasMountSourceUri({
        resource_type: 'nas',
        config: {
          protocol: 'smb',
          server: '192.168.14.23',
          share: 'backup',
        },
      }),
    ).toBe('//192.168.14.23/backup')
  })

  it('formats NFS mount source as host:/export', () => {
    expect(
      nasMountSourceUri({
        resource_type: 'nas',
        config: {
          protocol: 'nfs',
          server: '192.168.14.23',
          export_path: '/srv/nfs/backup',
          path: customMountPath('192.168.14.23-srv-nfs-backup'),
        },
      }),
    ).toBe('192.168.14.23:/srv/nfs/backup')
  })

  it('converts dot-separated summary', () => {
    expect(
      nasMountSourceUri({
        resource_type: 'nas',
        config: {},
        connection_summary: '192.168.14.23 · backup',
      }),
    ).toBe('//192.168.14.23/backup')
    expect(
      nasMountSourceUri({
        resource_type: 'nas',
        config: {},
        connection_summary: '192.168.14.23 · /srv/nfs/backup',
      }),
    ).toBe('192.168.14.23:/srv/nfs/backup')
  })

  it('detects protocol from config fields', () => {
    expect(nasMountProtocol({ resource_type: 'nas', config: { share: 'data' } })).toBe('smb')
    expect(nasMountProtocol({ resource_type: 'nas', config: { export_path: '/export' } })).toBe('nfs')
  })
})

describe('nas list column helpers', () => {
  it('extracts server, share/export, and proxy mount point from config', () => {
    const smbMount = customMountPath('192.168.14.23-backup')
    expect(
      nasServerAddress({
        resource_type: 'nas',
        config: { protocol: 'smb', server: '192.168.14.23', share: 'backup', path: smbMount },
      }),
    ).toBe('192.168.14.23')
    expect(
      nasShareOrExport({
        resource_type: 'nas',
        config: { protocol: 'smb', server: '192.168.14.23', share: 'backup' },
      }),
    ).toBe('backup')
    expect(
      nasShareOrExport({
        resource_type: 'nas',
        config: {
          protocol: 'nfs',
          server: '192.168.31.88',
          export_path: '/srv/nfs/share',
          path: customMountPath('192.168.31.88-srv-nfs-share'),
        },
      }),
    ).toBe('/srv/nfs/share')
    expect(
      nasProxyMountPoint({
        resource_type: 'nas',
        mount_point: smbMount,
        config: { path: customMountPath('other') },
      }),
    ).toBe(customMountPath('other'))
    expect(
      nasProxyMountPoint({
        resource_type: 'nas',
        mount_point: smbMount,
        config: {},
      }),
    ).toBe(smbMount)
  })
  it('formats SMB and NFS path values with protocol-native kind labels', () => {
    expect(
      nasShareOrExport({
        resource_type: 'nas',
        config: { protocol: 'smb', server: '192.168.10.33', share: 'source' },
      }),
    ).toBe('source')
    expect(
      nasPathKindLabelKey({
        resource_type: 'nas',
        config: { protocol: 'smb', server: '192.168.10.33', share: 'source' },
      }),
    ).toBe('protection.sourceResources.colNasShareName')
    expect(
      nasShareOrExport({
        resource_type: 'nas',
        config: {
          protocol: 'nfs',
          server: '192.168.10.33',
          export_path: '/source',
        },
      }),
    ).toBe('/source')
    expect(
      nasPathKindLabelKey({
        resource_type: 'nas',
        config: {
          protocol: 'nfs',
          server: '192.168.10.33',
          export_path: '/source',
        },
      }),
    ).toBe('protection.sourceResources.colNasExportPath')
    expect(enProtectionPages.sourceResources.colNasShareName).toBe('Share name')
    expect(enProtectionPages.sourceResources.colNasExportPath).toBe('Export path')
    expect(enProtectionPages.sourceResources.colNasShareExport).toBe('Path')
    expect(enProtectionPages.sourceResources.nasPhSmbShare).toBe('data')
  })
})

describe('sourceNasStatusPresentation', () => {
  it('maps the stable lifecycle state to backup-source terminology', () => {
    expect(sourceNasStatusPresentation({ effective_status: 'active' }, 'offline')).toEqual({
      status: 'active',
      labelKey: 'protection.sourceResources.lifecycleRegistered',
      tone: 'success',
    })
  })

  it('defines NAS error and Proxy labels in the active locale namespace', () => {
    expect(enProtectionPages.sourceResources.mountStatus.error).toBe('Mount Error')
    expect(enProtectionPages.sourceResources.proxyStatus).toBe('Proxy: {status}')
  })

  it('prioritizes a NAS error over an online Proxy', () => {
    expect(
      sourceNasStatusPresentation(
        {
          effective_status: 'error',
          mount_status: 'unmounted',
          connection_test_status: 'failed',
        },
        'online',
      ),
    ).toEqual({
      status: 'error',
      labelKey: 'protection.sourceResources.mountStatus.error',
      tone: 'danger',
    })
  })

  it('keeps unmounted and probing NAS states distinct from Proxy health', () => {
    expect(
      sourceNasStatusPresentation({ effective_status: 'unverified' }, 'online'),
    ).toMatchObject({
      status: 'unverified',
      tone: 'warning',
    })
    expect(
      sourceNasStatusPresentation({ connection_test_status: 'running' }, 'online'),
    ).toMatchObject({
      status: 'probing',
      tone: 'warning',
    })
  })

  it('falls back to the Proxy connection only when NAS health is unavailable', () => {
    expect(sourceNasStatusPresentation({}, 'reconnecting')).toEqual({
      status: 'reconnecting',
      labelKey: 'protection.sourceResources.nodeStatusReconnecting',
      tone: 'info',
    })
  })
})

describe('sourcePasswordDisplay', () => {
  it('masks when API returns has_password without the secret (#354)', () => {
    expect(
      sourcePasswordDisplay({
        username: 'backup',
        has_password: true,
        has_secret_key: false,
      }),
    ).toBe(SOURCE_PASSWORD_MASK)
  })

  it('shows empty when no password is stored', () => {
    expect(
      sourcePasswordDisplay({
        username: 'backup',
        has_password: false,
        has_secret_key: false,
      }),
    ).toBe('—')
  })

  it('still masks if a plaintext password is present (legacy)', () => {
    expect(sourcePasswordDisplay({ password: 'secret' })).toBe(SOURCE_PASSWORD_MASK)
  })
})
