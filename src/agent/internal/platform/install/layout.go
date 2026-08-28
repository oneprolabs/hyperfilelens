package install

import (
	"path/filepath"
	"strings"
)

const (
	dirBackup          = "backup"
	dirRuntime         = "runtime"
	dirLifecycle       = "lifecycle"
	stateLatestArchive = "latest.tar.gz"
	backupMetaFile     = "meta.json"
	runtimeDownload    = "download"
	runtimeWorkspace   = "workspace"
	lifecycleUpgrade   = "upgrade"
	lifecycleUninstall = "uninstall"
	upgradeStateFile   = "upgrade-state.json"
)

// BackupRollbackBinDir is the fixed rollback snapshot for install-dir binaries.
func BackupRollbackBinDir(dataDir string) string {
	return filepath.Join(strings.TrimSpace(dataDir), dirBackup, "rollback", "bin")
}

// BackupStateLatestPath is the fixed agent.env/agent.db snapshot path.
func BackupStateLatestPath(dataDir string) string {
	return filepath.Join(strings.TrimSpace(dataDir), dirBackup, "rollback", stateLatestArchive)
}

// BackupMetaPath is backup/rollback/meta.json describing the latest pre-upgrade snapshot.
func BackupMetaPath(dataDir string) string {
	return filepath.Join(strings.TrimSpace(dataDir), dirBackup, "rollback", backupMetaFile)
}

// RuntimeDownloadDir holds WS download artifacts before staging.
func RuntimeDownloadDir(dataDir string) string {
	return filepath.Join(strings.TrimSpace(dataDir), dirRuntime, runtimeDownload)
}

// RuntimeWorkspaceDir is the extract target for install.sh upgrade --from archive.
func RuntimeWorkspaceDir(dataDir string) string {
	return filepath.Join(strings.TrimSpace(dataDir), dirRuntime, runtimeWorkspace)
}

// LifecycleUpgradeDir holds staged package + detached upgrade runner.
func LifecycleUpgradeDir(dataDir string) string {
	return filepath.Join(strings.TrimSpace(dataDir), dirLifecycle, lifecycleUpgrade)
}

// LifecycleUninstallDir holds detached uninstall runner.
func LifecycleUninstallDir(dataDir string) string {
	return filepath.Join(strings.TrimSpace(dataDir), dirLifecycle, lifecycleUninstall)
}

// LifecycleUpgradeStatePath records the durable local upgrade transaction.
func LifecycleUpgradeStatePath(dataDir string) string {
	return filepath.Join(strings.TrimSpace(dataDir), dirLifecycle, upgradeStateFile)
}

// LifecycleUpgradeFailedPath is written when detached upgrade fails.
func LifecycleUpgradeFailedPath(dataDir string) string {
	return filepath.Join(LifecycleUpgradeDir(dataDir), PendingUpgradeFailedMarker)
}

// StagedUpgradePackagePath returns lifecycle/upgrade/package.<ext>.
func StagedUpgradePackagePath(dataDir, archivePath string) string {
	return filepath.Join(LifecycleUpgradeDir(dataDir), stagedPackageFilename(archivePath))
}

// PathAllowedForRemoval reports whether an install or data directory may be rm -rf'd by uninstall.
func PathAllowedForRemoval(path string) bool {
	path = filepath.Clean(strings.TrimSpace(path))
	if !filepath.IsAbs(path) {
		return false
	}
	switch path {
	case "/opt/hyperfilelens-agent", "/var/lib/hyperfilelens-agent", "/Library/Application Support/HyperFileLens/Agent":
		return true
	default:
		return strings.HasPrefix(path, "/opt/hyperfilelens-agent/") ||
			strings.HasPrefix(path, "/var/lib/hyperfilelens-agent/") ||
			strings.HasPrefix(path, "/Library/Application Support/HyperFileLens/Agent/")
	}
}
