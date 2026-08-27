package vfs

import (
	"fmt"
	"path/filepath"
	"strings"
)

const (
	dirMounts            = "mounts"
	mountRepositoriesDir = "repositories"
	mountRestoresDir     = "restores"
	mountSourcesDir      = "sources"
	mountCustomDir       = "custom"
)

// AgentMountsDir is AgentRoot/mounts (runtime NAS mount roots).
func AgentMountsDir(dataRoot string) string {
	return filepath.Join(strings.TrimSpace(dataRoot), dirMounts)
}

// MountRepositoriesDir is AgentRoot/mounts/repositories.
func MountRepositoriesDir(dataRoot string) string {
	return filepath.Join(AgentMountsDir(dataRoot), mountRepositoriesDir)
}

// MountRestoresDir is AgentRoot/mounts/restores.
func MountRestoresDir(dataRoot string) string {
	return filepath.Join(AgentMountsDir(dataRoot), mountRestoresDir)
}

// MountSourcesDir is AgentRoot/mounts/sources.
func MountSourcesDir(dataRoot string) string {
	return filepath.Join(AgentMountsDir(dataRoot), mountSourcesDir)
}

// MountCustomDir is AgentRoot/mounts/custom.
func MountCustomDir(dataRoot string) string {
	return filepath.Join(AgentMountsDir(dataRoot), mountCustomDir)
}

// RepositoryMountPoint returns the canonical repository mount path.
func RepositoryMountPoint(dataRoot string, repositoryID int64, nodeID int64) string {
	leaf := fmt.Sprintf("repo-%d", repositoryID)
	if nodeID > 0 {
		leaf = fmt.Sprintf("%s-node-%d", leaf, nodeID)
	}
	return filepath.Join(MountRepositoriesDir(dataRoot), leaf)
}

// RestoreRepositoryMountPoint returns the canonical temporary restore mount path.
func RestoreRepositoryMountPoint(dataRoot string, repositoryID int64, nodeID int64) string {
	return filepath.Join(
		MountRestoresDir(dataRoot),
		fmt.Sprintf("repo-%d-node-%d", repositoryID, nodeID),
	)
}

// SourceMountPoint returns the canonical source mount path.
func SourceMountPoint(dataRoot string, resourceID int64) string {
	return filepath.Join(MountSourcesDir(dataRoot), fmt.Sprintf("source-%d", resourceID))
}
