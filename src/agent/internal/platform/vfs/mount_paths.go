package vfs

import (
	"fmt"
	"path/filepath"
	"strings"
)

const (
	dirMounts            = "mounts"
	mountRepositoriesDir = "repositories"
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

// SourceMountPoint returns the canonical source mount path.
func SourceMountPoint(dataRoot string, resourceID int64) string {
	return filepath.Join(MountSourcesDir(dataRoot), fmt.Sprintf("source-%d", resourceID))
}
