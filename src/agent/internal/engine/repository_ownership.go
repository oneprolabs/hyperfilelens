package engine

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	nassvc "hyperfilelens/agent/internal/service/nas"
)

const repositoryOwnershipMarkerPath = ".hyperfilelens/repository-owner-v1.json"

type repositoryOwnershipMarker struct {
	DeploymentUUID string `json:"deployment_uuid"`
	RepositoryUUID string `json:"repository_uuid"`
	LocationDigest string `json:"location_digest"`
	FormatVersion  int    `json:"format_version"`
	Signature      string `json:"signature"`
}

func validateRepositoryOwnership(ownership repositoryOwnership) error {
	if ownership.MarkerPath != repositoryOwnershipMarkerPath {
		return fmt.Errorf("unsupported repository ownership marker path")
	}
	if ownership.DeploymentUUID == "" || ownership.RepositoryUUID == "" ||
		ownership.LocationDigest == "" || ownership.Signature == "" ||
		ownership.FormatVersion != 1 {
		return fmt.Errorf("repository ownership payload is incomplete")
	}
	return nil
}

func claimFilesystemRepositoryOwnership(spec repositorySpec) error {
	if spec.Ownership == nil {
		return fmt.Errorf("repository ownership payload is required")
	}
	repositoryPath, allowedRoot, err := filesystemRepositoryOwnershipPath(spec)
	if err != nil {
		return err
	}
	if _, err := validateRepositoryCleanupPath(repositoryPath, allowedRoot); err != nil {
		return err
	}
	if err := rejectAncestorRepositoryOwners(repositoryPath, allowedRoot); err != nil {
		return err
	}
	if err := os.MkdirAll(repositoryPath, 0o755); err != nil {
		return err
	}
	markerPath := filepath.Join(repositoryPath, repositoryOwnershipMarkerPath)
	existing, err := readRepositoryOwnershipMarker(markerPath)
	if err != nil {
		return err
	}
	if existing != nil {
		if err := requireMatchingRepositoryOwner(*existing, *spec.Ownership); err != nil {
			return err
		}
		return rejectDescendantRepositoryOwners(repositoryPath, markerPath)
	}
	entries, err := os.ReadDir(repositoryPath)
	if err != nil {
		return err
	}
	if len(entries) != 0 {
		return fmt.Errorf("the selected repository directory already contains data")
	}
	if err := os.MkdirAll(filepath.Dir(markerPath), 0o700); err != nil {
		return err
	}
	marker := repositoryOwnershipMarkerFromSpec(*spec.Ownership)
	encoded, err := json.Marshal(marker)
	if err != nil {
		return err
	}
	handle, err := os.OpenFile(markerPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if errors.Is(err, fs.ErrExist) {
		existing, readErr := readRepositoryOwnershipMarker(markerPath)
		if readErr != nil {
			return readErr
		}
		if existing == nil {
			return fmt.Errorf("repository ownership changed during initialization")
		}
		return requireMatchingRepositoryOwner(*existing, *spec.Ownership)
	}
	if err != nil {
		return err
	}
	if _, err = handle.Write(append(encoded, '\n')); err == nil {
		err = handle.Sync()
	}
	closeErr := handle.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if err := rejectAncestorRepositoryOwners(repositoryPath, allowedRoot); err != nil {
		return err
	}
	if err := rejectDescendantRepositoryOwners(repositoryPath, markerPath); err != nil {
		return err
	}
	persisted, err := readRepositoryOwnershipMarker(markerPath)
	if err != nil {
		return err
	}
	if persisted == nil {
		return fmt.Errorf("repository ownership marker is not durable")
	}
	return requireMatchingRepositoryOwner(*persisted, *spec.Ownership)
}

func verifyFilesystemRepositoryOwnership(spec repositorySpec, adoptMissing bool) error {
	if spec.Ownership == nil {
		return nil
	}
	repositoryPath, allowedRoot, err := filesystemRepositoryOwnershipPath(spec)
	if err != nil {
		return err
	}
	if _, err := validateRepositoryCleanupPath(repositoryPath, allowedRoot); err != nil {
		return err
	}
	if err := rejectAncestorRepositoryOwners(repositoryPath, allowedRoot); err != nil {
		return err
	}
	markerPath := filepath.Join(repositoryPath, repositoryOwnershipMarkerPath)
	marker, err := readRepositoryOwnershipMarker(markerPath)
	if err != nil {
		return err
	}
	if marker == nil {
		if !adoptMissing {
			return fmt.Errorf("repository ownership marker is missing")
		}
		if err := rejectDescendantRepositoryOwners(repositoryPath, markerPath); err != nil {
			return err
		}
		if err := writeLegacyRepositoryOwnershipMarker(markerPath, *spec.Ownership); err != nil {
			return err
		}
		marker, err = readRepositoryOwnershipMarker(markerPath)
		if err != nil {
			return err
		}
		if marker == nil {
			return fmt.Errorf("repository ownership marker is not durable")
		}
	}
	if err := requireMatchingRepositoryOwner(*marker, *spec.Ownership); err != nil {
		return err
	}
	if adoptMissing {
		if err := rejectAncestorRepositoryOwners(repositoryPath, allowedRoot); err != nil {
			return err
		}
		if err := rejectDescendantRepositoryOwners(repositoryPath, markerPath); err != nil {
			return err
		}
	}
	return nil
}

func checkFilesystemRepositoryOwnershipIfPresent(spec repositorySpec) error {
	if spec.Ownership == nil {
		return nil
	}
	repositoryPath, allowedRoot, err := filesystemRepositoryOwnershipPath(spec)
	if err != nil {
		return err
	}
	if _, err := validateRepositoryCleanupPath(repositoryPath, allowedRoot); err != nil {
		return err
	}
	if err := rejectAncestorRepositoryOwners(repositoryPath, allowedRoot); err != nil {
		return err
	}
	marker, err := readRepositoryOwnershipMarker(
		filepath.Join(repositoryPath, repositoryOwnershipMarkerPath),
	)
	if err != nil || marker == nil {
		return err
	}
	return requireMatchingRepositoryOwner(*marker, *spec.Ownership)
}

func filesystemRepositoryOwnershipPath(spec repositorySpec) (string, string, error) {
	switch spec.Type {
	case "nas":
		path, err := repositoryNASPath(spec)
		if err != nil {
			return "", "", err
		}
		return path, nassvcResolvedMountPoint(spec), nil
	case "proxy_fs":
		if spec.Layout != "managed_subdir_v1" {
			return "", "", fmt.Errorf("unmanaged local repositories cannot be claimed")
		}
		path, root, err := validateManagedProxyFSRepositoryPath(spec)
		return path, root, err
	default:
		return "", "", fmt.Errorf("repository ownership is unsupported for %q", spec.Type)
	}
}

func nassvcResolvedMountPoint(spec repositorySpec) string {
	if spec.TargetNAS == nil {
		return ""
	}
	return filepath.Clean(nassvc.ResolvedMountPoint(spec.TargetNAS.MountPoint))
}

func rejectAncestorRepositoryOwners(repositoryPath string, allowedRoot string) error {
	root := filepath.Clean(allowedRoot)
	current := filepath.Dir(filepath.Clean(repositoryPath))
	for current != root {
		rel, err := filepath.Rel(root, current)
		if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
			return fmt.Errorf("repository ownership path escapes its managed root")
		}
		marker, err := readRepositoryOwnershipMarker(
			filepath.Join(current, repositoryOwnershipMarkerPath),
		)
		if err != nil {
			return err
		}
		if marker != nil {
			return fmt.Errorf("repository directory is nested inside another managed repository")
		}
		parent := filepath.Dir(current)
		if parent == current {
			return fmt.Errorf("repository ownership root is invalid")
		}
		current = parent
	}
	return nil
}

func rejectDescendantRepositoryOwners(repositoryPath string, ownMarkerPath string) error {
	return filepath.WalkDir(repositoryPath, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || filepath.Clean(path) == filepath.Clean(ownMarkerPath) {
			return nil
		}
		rel, err := filepath.Rel(repositoryPath, path)
		if err != nil {
			return err
		}
		if filepath.ToSlash(rel) == repositoryOwnershipMarkerPath {
			return nil
		}
		if strings.HasSuffix(filepath.ToSlash(rel), "/"+repositoryOwnershipMarkerPath) {
			return fmt.Errorf("repository directory contains another managed repository")
		}
		return nil
	})
}

func writeLegacyRepositoryOwnershipMarker(path string, ownership repositoryOwnership) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	encoded, err := json.Marshal(repositoryOwnershipMarkerFromSpec(ownership))
	if err != nil {
		return err
	}
	handle, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if errors.Is(err, fs.ErrExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if _, err = handle.Write(append(encoded, '\n')); err == nil {
		err = handle.Sync()
	}
	closeErr := handle.Close()
	if err != nil {
		return err
	}
	return closeErr
}

func readRepositoryOwnershipMarker(path string) (*repositoryOwnershipMarker, error) {
	metadataInfo, err := os.Lstat(filepath.Dir(path))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if metadataInfo.Mode()&os.ModeSymlink != 0 || !metadataInfo.IsDir() {
		return nil, fmt.Errorf("repository ownership metadata path must be a directory")
	}
	markerInfo, err := os.Lstat(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if markerInfo.Mode()&os.ModeSymlink != 0 || !markerInfo.Mode().IsRegular() {
		return nil, fmt.Errorf("repository ownership marker must be a regular file")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	marker := &repositoryOwnershipMarker{}
	if err := json.Unmarshal(raw, marker); err != nil {
		return nil, fmt.Errorf("repository ownership marker is invalid")
	}
	return marker, nil
}

func requireMatchingRepositoryOwner(marker repositoryOwnershipMarker, expected repositoryOwnership) error {
	if marker != repositoryOwnershipMarkerFromSpec(expected) {
		return fmt.Errorf("physical repository ownership belongs to another repository")
	}
	return nil
}

func repositoryOwnershipMarkerFromSpec(ownership repositoryOwnership) repositoryOwnershipMarker {
	return repositoryOwnershipMarker{
		DeploymentUUID: ownership.DeploymentUUID,
		RepositoryUUID: ownership.RepositoryUUID,
		LocationDigest: ownership.LocationDigest,
		FormatVersion:  ownership.FormatVersion,
		Signature:      ownership.Signature,
	}
}
