import { describe, expect, it } from 'vitest'
import { buildNasRepositoryName } from './nasRepositoryName'

describe('NAS repository display names', () => {
  it.each([
    ['smb', '192.168.1.10', 'backup', 'SMB(192.168.1.10/backup)'],
    ['nfs', 'nas.example.com', '/volume1/backup/', 'NFS(nas.example.com/backup)'],
    ['smb', ' nas.example.com ', '\\volume1\\backup', 'SMB(nas.example.com/backup)'],
    ['nfs', 'nas.example.com', '/', 'NFS(nas.example.com/)'],
    ['smb', '', 'backup', ''],
  ] as const)('builds %s name for %s %s', (protocol, host, path, expected) => {
    expect(buildNasRepositoryName(protocol, host, path)).toBe(expected)
  })
})
