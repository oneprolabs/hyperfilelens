import type {
  BackupSourceSnapshot,
  BackupSourceSnapshotDirectory,
} from '../../../lib/protectionBackupConfigApi'

type SnapshotDirectoryBrowseCandidate = Pick<BackupSourceSnapshotDirectory, 'status' | 'kopia_snapshot_id'>

export function isSnapshotDirectoryBrowsable(
  snapshotStatus: string | null | undefined,
  directory: SnapshotDirectoryBrowseCandidate,
) {
  const normalizedSnapshotStatus = String(snapshotStatus || '').toLowerCase()
  return (normalizedSnapshotStatus === 'available' || normalizedSnapshotStatus === 'partial')
    && directory.status === 'available'
    && Boolean(directory.kopia_snapshot_id)
}

type SnapshotBrowseCandidate = Pick<BackupSourceSnapshot, 'status' | 'directories'>

export function browsableSnapshotDirectories<T extends SnapshotBrowseCandidate>(
  snapshot: T,
) {
  return snapshot.directories?.filter((directory) => (
    isSnapshotDirectoryBrowsable(snapshot.status, directory)
  )) ?? []
}

export function findBrowsableSnapshots<T extends SnapshotBrowseCandidate>(
  snapshots: readonly T[],
) {
  return snapshots.filter((snapshot) => browsableSnapshotDirectories(snapshot).length > 0)
}

export function findFirstBrowsableSnapshotDirectory<T extends SnapshotBrowseCandidate>(
  snapshots: readonly T[],
) {
  for (const snapshot of snapshots) {
    const directory = browsableSnapshotDirectories(snapshot)[0]
    if (directory) return { snapshot, directory }
  }
  return null
}
