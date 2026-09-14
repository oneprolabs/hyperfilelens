package vfs

import "path/filepath"

// AgentBackupDir is AgentRoot/backup (rollback + legacy snapshots).
func AgentBackupDir(dataRoot string) string {
	return filepath.Join(dataRoot, "backup")
}

// AgentRuntimeDir is AgentRoot/runtime (transient lifecycle state).
func AgentRuntimeDir(dataRoot string) string {
	return filepath.Join(dataRoot, "runtime")
}

// AgentLensnodeRuntimeDir is the private Compose project directory for the
// Gateway LensNode sidecar. It is separate from AgentRuntimeDir's transient
// download/session files so upgrades can replace Compose metadata atomically.
func AgentLensnodeRuntimeDir(dataRoot string) string {
	return filepath.Join(dataRoot, "runtime", "lensnode")
}

// AgentWorkspaceDir is the root for managed Gateway workspaces.
func AgentWorkspaceDir(dataRoot string) string {
	return filepath.Join(dataRoot, "workspace")
}

// AgentLifecycleDir is AgentRoot/lifecycle (detached upgrade/uninstall).
func AgentLifecycleDir(dataRoot string) string {
	return filepath.Join(dataRoot, "lifecycle")
}
