import { describe, expect, it } from 'vitest'

import type { BackupSourceDirectoryEntry } from './sourceApi'
import {
  isLikelySmbFilenameEncodingIssue,
  isLikelySmbFilenamePathNotFound,
  selectBackupSourceDirectoryTreeEntries,
  shouldAutoExpandRefreshedDirectory,
} from './backupSourceDirectoryTree'

describe('SMB filename encoding issue detection', () => {
  const smbWithoutCharset = { type: 'nas' as const, protocol: 'smb' as const, mount_options: 'rw' }

  it.each(['????', 'album??2026'])('flags repeated question marks in %s', (label) => {
    expect(isLikelySmbFilenameEncodingIssue({
      source: smbWithoutCharset,
      label,
      path: `/zh/${label}`,
    })).toBe(true)
  })

  it('does not flag a configured UTF-8 SMB mount', () => {
    expect(isLikelySmbFilenameEncodingIssue({
      source: { ...smbWithoutCharset, mount_options: 'rw, IOCHARSET = UTF8' },
      label: '????',
      path: '/zh/????',
    })).toBe(false)
  })

  it.each([
    { source: smbWithoutCharset, label: 'file?.txt' },
    { source: { type: 'nas' as const, protocol: 'nfs' as const, mount_options: '' }, label: '????' },
    { source: { type: 'host' as const, platform: 'linux' as const }, label: '????' },
    { source: smbWithoutCharset, label: 'test.sh' },
  ])('does not flag non-matching entry %#', ({ source, label }) => {
    expect(isLikelySmbFilenameEncodingIssue({ source, label, path: `/zh/${label}` })).toBe(false)
  })

  it('recognizes the contextual Explorer path-not-found failure', () => {
    expect(isLikelySmbFilenamePathNotFound({
      source: smbWithoutCharset,
      path: '/zh/????',
      error: {
        status: 502,
        message: 'Agent Explorer List Failed',
        errorCode: 'AGENT.EXPLORER_LIST_FAILED',
        meta: { diagnostic: 'path not found' },
      },
    })).toBe(true)
  })

  it.each([
    { path: '/zh/normal', errorCode: 'AGENT.EXPLORER_LIST_FAILED', diagnostic: 'path not found' },
    { path: '/zh/????', errorCode: 'AGENT.TIMEOUT', diagnostic: 'path not found' },
    { path: '/zh/????', errorCode: 'AGENT.EXPLORER_LIST_FAILED', diagnostic: 'permission denied' },
  ])('keeps unrelated browse failures generic %#', ({ path, errorCode, diagnostic }) => {
    expect(isLikelySmbFilenamePathNotFound({
      source: smbWithoutCharset,
      path,
      error: { status: 502, message: 'failed', errorCode, meta: { diagnostic } },
    })).toBe(false)
  })
})

const root: BackupSourceDirectoryEntry = {
  label: '/',
  path: '/',
  isLeaf: false,
  is_dir: true,
  path_type: 'directory',
}

const boot: BackupSourceDirectoryEntry = {
  label: 'boot',
  path: '/boot',
  isLeaf: false,
  is_dir: true,
  path_type: 'directory',
}

const windowsDrives: BackupSourceDirectoryEntry[] = [
  { label: 'C:\\', path: 'C:\\', isLeaf: false, is_dir: true, path_type: 'directory' },
  { label: 'D:\\', path: 'D:\\', isLeaf: false, is_dir: true, path_type: 'directory' },
]

describe('selectBackupSourceDirectoryTreeEntries', () => {
  it.each(['linux', 'macos'] as const)('uses a single root for %s hosts', (platform) => {
    expect(selectBackupSourceDirectoryTreeEntries({
      source: { type: 'host', platform },
      parentPath: '',
      result: { root, entries: [root, boot] },
    })).toEqual([root])
  })

  it('uses a single share root for NAS sources', () => {
    expect(selectBackupSourceDirectoryTreeEntries({
      source: { type: 'nas' },
      parentPath: '',
      result: { root, entries: [root, boot] },
    })).toEqual([root])
  })

  it('keeps Windows drive roots', () => {
    expect(selectBackupSourceDirectoryTreeEntries({
      source: { type: 'host', platform: 'windows' },
      parentPath: '',
      result: { root: windowsDrives[0], entries: windowsDrives },
    })).toEqual(windowsDrives)
  })

  it('uses the Home root for a Current User Windows Agent', () => {
    const home = { ...root, label: 'Home', path: 'C:\\Users\\alice' }
    expect(selectBackupSourceDirectoryTreeEntries({
      source: { type: 'host', platform: 'windows', installation_mode: 'user' },
      parentPath: '',
      result: { root: home, entries: windowsDrives },
    })).toEqual([home])
  })

  it('returns directory children after a POSIX root is expanded', () => {
    expect(selectBackupSourceDirectoryTreeEntries({
      source: { type: 'host', platform: 'linux' },
      parentPath: '/',
      result: { root, entries: [boot] },
    })).toEqual([boot])
  })
})

describe('shouldAutoExpandRefreshedDirectory', () => {
  it('expands a previously collapsed directory after a non-empty refresh', () => {
    expect(shouldAutoExpandRefreshedDirectory({
      wasExpanded: false,
      hasChildren: true,
      expansionRevisionAtStart: 2,
      expansionRevisionAfterRefresh: 2,
    })).toBe(true)
  })

  it('keeps an already expanded directory unchanged', () => {
    expect(shouldAutoExpandRefreshedDirectory({
      wasExpanded: true,
      hasChildren: true,
      expansionRevisionAtStart: 2,
      expansionRevisionAfterRefresh: 2,
    })).toBe(false)
  })

  it('does not expand an empty directory', () => {
    expect(shouldAutoExpandRefreshedDirectory({
      wasExpanded: false,
      hasChildren: false,
      expansionRevisionAtStart: 2,
      expansionRevisionAfterRefresh: 2,
    })).toBe(false)
  })

  it('respects expansion changes made while the refresh is in progress', () => {
    expect(shouldAutoExpandRefreshedDirectory({
      wasExpanded: false,
      hasChildren: true,
      expansionRevisionAtStart: 2,
      expansionRevisionAfterRefresh: 3,
    })).toBe(false)
  })
})
