import { describe, expect, it } from 'vitest'
import {
  browsableSnapshotDirectories,
  findBrowsableSnapshots,
  findFirstBrowsableSnapshotDirectory,
  isSnapshotDirectoryBrowsable,
} from './snapshotBrowseEligibility'

const availableDirectory = {
  status: 'available',
  kopia_snapshot_id: 'kopia-snapshot-1',
}

describe('isSnapshotDirectoryBrowsable', () => {
  it.each(['available', 'AVAILABLE', 'partial', 'PARTIAL'])(
    'allows browsing for a %s parent snapshot',
    (snapshotStatus) => {
      expect(isSnapshotDirectoryBrowsable(snapshotStatus, availableDirectory)).toBe(true)
    },
  )

  it.each([undefined, '', 'creating', 'failed', 'deleting', 'deleted', 'delete_failed'])(
    'blocks browsing for a %s parent snapshot',
    (snapshotStatus) => {
      expect(isSnapshotDirectoryBrowsable(snapshotStatus, availableDirectory)).toBe(false)
    },
  )

  it('blocks browsing when the directory snapshot is unavailable', () => {
    expect(isSnapshotDirectoryBrowsable('available', {
      ...availableDirectory,
      status: 'failed',
    })).toBe(false)
  })

  it.each([undefined, null, ''])(
    'blocks browsing when the directory Kopia snapshot ID is %s',
    (kopiaSnapshotId) => {
      expect(isSnapshotDirectoryBrowsable('available', {
        ...availableDirectory,
        kopia_snapshot_id: kopiaSnapshotId,
      })).toBe(false)
    },
  )
})

describe('findFirstBrowsableSnapshotDirectory', () => {
  it('selects a directory from the newest browsable snapshot in API order', () => {
    const result = findFirstBrowsableSnapshotDirectory([
      {
        id: 3,
        status: 'creating',
        directories: [{ ...availableDirectory, id: 31 }],
      },
      {
        id: 2,
        status: 'partial',
        directories: [
          { ...availableDirectory, id: 21, status: 'failed' },
          { ...availableDirectory, id: 22 },
        ],
      },
      {
        id: 1,
        status: 'available',
        directories: [{ ...availableDirectory, id: 11 }],
      },
    ])

    expect(result?.snapshot.id).toBe(2)
    expect(result?.directory.id).toBe(22)
  })

  it('returns null when no snapshot contains a browsable directory', () => {
    expect(findFirstBrowsableSnapshotDirectory([
      {
        status: 'available',
        directories: [{ status: 'available', kopia_snapshot_id: null }],
      },
      {
        status: 'failed',
        directories: [{ ...availableDirectory }],
      },
    ])).toBeNull()
  })
})

describe('snapshot browser selector options', () => {
  const snapshots = [
    {
      id: 3,
      status: 'creating',
      directories: [{ ...availableDirectory, id: 31 }],
    },
    {
      id: 2,
      status: 'partial',
      directories: [
        { ...availableDirectory, id: 21, status: 'failed' },
        { ...availableDirectory, id: 22 },
        { ...availableDirectory, id: 23 },
      ],
    },
    {
      id: 1,
      status: 'available',
      directories: [{ ...availableDirectory, id: 11 }],
    },
  ]

  it('keeps only browsable snapshots in API order', () => {
    expect(findBrowsableSnapshots(snapshots).map((snapshot) => snapshot.id)).toEqual([2, 1])
  })

  it('keeps only browsable protected paths for the selected snapshot', () => {
    expect(browsableSnapshotDirectories(snapshots[1]).map((directory) => directory.id)).toEqual([22, 23])
  })
})
