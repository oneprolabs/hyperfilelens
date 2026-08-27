import { describe, expect, it } from 'vitest'

import { resolveRestoreResultPaths } from './restoreResultPath'

describe('resolveRestoreResultPaths', () => {
  it('appends the selected directory or file name to the restore directory', () => {
    expect(resolveRestoreResultPaths([
      {
        key: 'directory',
        sourceDirectoryId: 1,
        sourcePath: '/data',
        selectedPaths: ['reports/2026'],
        sourcePathType: 'directory',
        restoreDirectory: '/restore',
      },
      {
        key: 'file',
        sourceDirectoryId: 1,
        sourcePath: '/data',
        selectedPaths: ['reports/readme.txt'],
        sourcePathType: 'directory',
        restoreDirectory: '/restore',
      },
    ])).toEqual([
      { key: 'directory', path: '/restore/2026' },
      { key: 'file', path: '/restore/readme.txt' },
    ])
  })

  it('shows every final path when an entire snapshot contains multiple roots', () => {
    expect(resolveRestoreResultPaths([
      {
        key: 'snapshot',
        sourceDirectoryId: 1,
        sourcePath: '/data',
        sourcePathType: 'directory',
        restoreDirectory: '/restore',
      },
      {
        key: 'snapshot',
        sourceDirectoryId: 2,
        sourcePath: '/etc',
        sourcePathType: 'directory',
        restoreDirectory: '/restore',
      },
    ])).toEqual([
      { key: 'snapshot', path: '/restore/data' },
      { key: 'snapshot', path: '/restore/etc' },
    ])
  })

  it('returns the same preview for every UI row when duplicate mappings are deduplicated for submission', () => {
    expect(resolveRestoreResultPaths([
      {
        key: 'first-row',
        sourceDirectoryId: 1,
        sourcePath: '/data',
        sourcePathType: 'directory',
        restoreDirectory: '/restore',
      },
      {
        key: 'second-row',
        sourceDirectoryId: 1,
        sourcePath: '/data',
        sourcePathType: 'directory',
        restoreDirectory: '/restore',
      },
    ])).toEqual([
      { key: 'first-row', path: '/restore/data' },
      { key: 'second-row', path: '/restore/data' },
    ])
  })

  it('uses the backend-compatible disambiguation for same-name roots', () => {
    expect(resolveRestoreResultPaths([
      {
        key: 'first',
        sourceDirectoryId: 1,
        sourcePath: '/root_a/logs',
        sourcePathType: 'directory',
        restoreDirectory: '/restore',
      },
      {
        key: 'second',
        sourceDirectoryId: 2,
        sourcePath: '/root_b/logs',
        sourcePathType: 'directory',
        restoreDirectory: '/restore',
      },
    ])).toEqual([
      { key: 'first', path: '/restore/logs--from-root_a' },
      { key: 'second', path: '/restore/logs--from-root_b' },
    ])
  })

  it('preserves Windows path separators', () => {
    expect(resolveRestoreResultPaths([{
      key: 'windows',
      sourceDirectoryId: 1,
      sourcePath: String.raw`C:\Backup Test`,
      sourcePathType: 'directory',
      restoreDirectory: String.raw`C:\Restore Dir`,
    }])).toEqual([{
      key: 'windows',
      path: String.raw`C:\Restore Dir\Backup Test`,
    }])
  })
})
