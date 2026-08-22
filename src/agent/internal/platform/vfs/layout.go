package vfs

import "path/filepath"

// AgentBackupDir is AgentRoot/backup (rollback + legacy snapshots).
func AgentBackupDir(dataRoot string) string {
	return filepath.Join(dataRoot, "backup")
}

// AgentRuntimeDir is AgentRoot/runtime (download + workspace).
func AgentRuntimeDir(dataRoot string) string {
	return filepath.Join(dataRoot, "runtime")
}

// AgentLifecycleDir is AgentRoot/lifecycle (detached upgrade/uninstall).
func AgentLifecycleDir(dataRoot string) string {
	return filepath.Join(dataRoot, "lifecycle")
}
