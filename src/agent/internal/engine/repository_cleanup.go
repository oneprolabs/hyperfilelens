package engine

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	nassvc "hyperfilelens/agent/internal/service/nas"
)

func (e *Engine) runManagedRepositoryCleanup(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	spec, ok, err := parseRepositorySpec(p.Extra["repository"])
	if err != nil {
		return "failed", nil, err.Error()
	}
	if !ok {
		return "failed", nil, "repository payload is required"
	}
	if err := ctx.Err(); err != nil {
		return "failed", nil, "canceled"
	}
	operationType := strings.ToLower(strings.TrimSpace(payloadStringValue(p.Extra["operation_type"])))
	if operationType != "cleanup.target" && operationType != "cleanup.repository" {
		return "failed", nil, fmt.Sprintf("unsupported repository cleanup operation %q", operationType)
	}
	if spec.Type == "s3" {
		if spec.Ownership == nil {
			return "failed", nil, "repository ownership payload is required"
		}
		deleteBucket, _ := payloadBoolValue(p.Extra["delete_bucket_if_empty"])
		return e.runS3RepositoryCleanup(ctx, rep, taskID, spec, deleteBucket)
	}

	result := map[string]any{
		"operation_type":  operationType,
		"repository_type": spec.Type,
	}
	cleanupScope := strings.ToLower(strings.TrimSpace(payloadStringValue(p.Extra["cleanup_scope"])))
	unmountedPolicy := strings.ToLower(strings.TrimSpace(payloadStringValue(p.Extra["unmounted_policy"])))
	var repositoryPath string
	var allowedRoot string
	preservePhysicalRepository := false
	skippedUnmountedNAS := false
	if spec.Type == "nas" {
		nasService := nassvc.NewService()
		if unmountedPolicy == "retain_and_continue" && !nasService.IsMounted(spec.TargetNAS.MountPoint) {
			skippedUnmountedNAS = true
			result["mount_status"] = "not_mounted"
			result["physical_cleanup"] = "skipped_unmounted"
			result["cleanup_complete"] = false
			result["cleanup_failures"] = []map[string]any{{
				"code":   "NAS_NOT_MOUNTED",
				"detail": "Remote repository cleanup was skipped because the NAS was not mounted.",
			}}
			result["retained_resources"] = []string{fmt.Sprintf("nas_repository:%d", spec.ID)}
			nassvc.LogSpec("repository_cleanup_skip_unmounted", *spec.TargetNAS, "task_id", taskID)
		} else {
			nassvc.LogSpec("repository_cleanup_mount_begin", *spec.TargetNAS, "task_id", taskID)
			if err := nasService.EnsureMounted(ctx, *spec.TargetNAS); err != nil {
				return "failed", result, redactRepositoryCleanupPaths(
					err.Error(),
					spec.TargetNAS.MountPoint,
					nassvc.ResolvedMountPoint(spec.TargetNAS.MountPoint),
				)
			}
			allowedRoot = nassvc.ResolvedMountPoint(spec.TargetNAS.MountPoint)
			repositoryPath, err = repositoryNASPath(spec)
			if err != nil {
				return "failed", result, err.Error()
			}
		}
	} else if spec.Type == "proxy_fs" {
		if cleanupScope == "local_state_only" {
			preservePhysicalRepository = true
		} else {
			repositoryPath, allowedRoot, err = validateManagedProxyFSRepositoryPath(spec)
			if err != nil {
				return "failed", result, err.Error()
			}
		}
	} else {
		return "failed", result, fmt.Sprintf("unsupported repository cleanup type %q", spec.Type)
	}

	_ = sendProgress(ctx, rep, taskID, map[string]any{
		"phase":          "repository_cleanup",
		"operation_type": operationType,
	})
	if skippedUnmountedNAS {
		// Never inspect the mount-point tree when it is not an active NAS mount.
	} else if preservePhysicalRepository {
		result["repository_existed"] = true
		result["physical_cleanup"] = "preserved_legacy_directory"
		result["scope"] = "legacy_local_disk"
		result["retained_resources"] = []string{"legacy_local_disk_directory"}
	} else {
		if _, statErr := os.Lstat(repositoryPath); statErr == nil {
			ownershipVerified, _ := payloadBoolValue(p.Extra["ownership_verified"])
			if !ownershipVerified && spec.Ownership != nil {
				// The physical marker is the destructive-operation authority.
				// A damaged Kopia repository remains safely cleanable when its
				// signed owner matches the requested Repository identity.
				ownershipVerified = verifyFilesystemRepositoryOwnership(spec, false) == nil
			}
			if !ownershipVerified {
				// Marker-less legacy repositories still require a successful Kopia
				// connection before any adoption/cleanup path can continue.
				_, _, _, _, verifyMessage := e.prepareManagedRepository(
					ctx,
					rep,
					taskID,
					p,
					repositoryPrepareConnect,
				)
				if verifyMessage != "" {
					result["ownership_verified"] = false
					return "failed", result, "physical repository ownership could not be verified: " + redactRepositoryCleanupPaths(
						verifyMessage,
						repositoryPath,
						allowedRoot,
					)
				}
			}
			if spec.Ownership != nil {
				if err := verifyFilesystemRepositoryOwnership(spec, false); err != nil {
					result["ownership_verified"] = false
					return "failed", result, redactRepositoryCleanupPaths(
						"physical repository ownership could not be verified: "+err.Error(),
						repositoryPath,
						allowedRoot,
					)
				}
				markerPath := filepath.Join(repositoryPath, repositoryOwnershipMarkerPath)
				if err := rejectDescendantRepositoryOwners(repositoryPath, markerPath); err != nil {
					return "failed", result, redactRepositoryCleanupPaths(
						err.Error(),
						repositoryPath,
						allowedRoot,
					)
				}
			}
			result["ownership_verified"] = true
		} else if !errors.Is(statErr, os.ErrNotExist) {
			return "failed", result, redactRepositoryCleanupPaths(statErr.Error(), repositoryPath, allowedRoot)
		}
		var existed bool
		if spec.Ownership != nil {
			_, existed, err = deleteOwnedManagedRepositoryPathForOwner(
				ctx,
				repositoryPath,
				allowedRoot,
				*spec.Ownership,
			)
		} else {
			_, existed, err = deleteManagedRepositoryPath(ctx, repositoryPath, allowedRoot)
		}
		if err != nil {
			return "failed", result, redactRepositoryCleanupPaths(err.Error(), repositoryPath, allowedRoot)
		}
		result["repository_existed"] = existed
		result["physical_cleanup"] = "deleted"
		if !existed {
			result["physical_cleanup"] = "already_absent"
		}
	}

	configFiles := e.repositoryCleanupConfigPaths(spec)
	removedConfigCount := 0
	for _, configFile := range configFiles {
		removed, err := removeRepositoryLocalState(configFile)
		if err != nil {
			return "failed", result, redactRepositoryCleanupPaths(err.Error(), filepath.Dir(configFile))
		}
		removedConfigCount += removed
	}
	result["removed_config_file_count"] = removedConfigCount
	cacheExisted := false
	for _, configFile := range configFiles {
		cacheDir := managedRepositoryCacheDir(e.current(), configFile)
		_, existed, err := deleteManagedRepositoryPath(ctx, cacheDir, managedRepositoryCacheRoot(e.current()))
		if err != nil {
			return "failed", result, redactRepositoryCleanupPaths(err.Error(), cacheDir)
		}
		cacheExisted = cacheExisted || existed
	}
	result["repository_cache_existed"] = cacheExisted
	result["local_state_cleanup"] = "completed"
	if spec.Type == "nas" && skippedUnmountedNAS {
		removed, cleanupErr := nassvc.NewService().CleanupUnmountedMountPoint(spec.TargetNAS.MountPoint)
		if cleanupErr != nil {
			return "failed", result, redactRepositoryCleanupPaths(cleanupErr.Error(), spec.TargetNAS.MountPoint)
		}
		result["mount_point_directory_removed"] = removed
	} else if spec.Type == "nas" {
		mountPoint := spec.TargetNAS.MountPoint
		if err := nassvc.NewService().Unmount(ctx, mountPoint); err != nil {
			return "failed", result, redactRepositoryCleanupPaths(
				err.Error(),
				mountPoint,
				nassvc.ResolvedMountPoint(mountPoint),
			)
		}
		result["mount_status"] = "unmounted"
	}
	if err := ctx.Err(); err != nil {
		return "failed", result, "canceled"
	}
	return "success", result, ""
}

func validateManagedProxyFSRepositoryPath(spec repositorySpec) (string, string, error) {
	if spec.Layout != "managed_subdir_v1" {
		return "", "", fmt.Errorf("refusing to delete an unmanaged proxy filesystem repository")
	}
	if spec.ID <= 0 {
		return "", "", fmt.Errorf("managed proxy filesystem repository id is required")
	}
	base := filepath.Clean(strings.TrimSpace(spec.BasePath))
	if !filepath.IsAbs(base) || filepath.Dir(base) == base {
		return "", "", fmt.Errorf("managed proxy filesystem base path is invalid")
	}
	expected := filepath.Join(base, fmt.Sprintf("hfl-repo-%d", spec.ID))
	path := filepath.Clean(strings.TrimSpace(spec.Path))
	if path != expected || filepath.Dir(path) != base {
		return "", "", fmt.Errorf("managed proxy filesystem repository path does not match its owner")
	}
	validated, err := validateRepositoryCleanupPath(path, base)
	if err != nil {
		return "", "", err
	}
	return validated, base, nil
}

func (e *Engine) repositoryCleanupConfigPaths(spec repositorySpec) []string {
	primary := e.repositoryConfigPath(spec)
	paths := []string{primary}
	if spec.Type != "nas" || spec.ID <= 0 || strings.TrimSpace(spec.Subdir) == "" {
		return paths
	}
	legacy := filepath.Join(
		filepath.Dir(primary),
		fmt.Sprintf("repo-%d.config", spec.ID),
	)
	if legacy != primary {
		paths = append(paths, legacy)
	}
	return paths
}

func deleteManagedRepositoryPath(ctx context.Context, path string, allowedRoot string) (string, bool, error) {
	cleaned, err := validateRepositoryCleanupPath(path, allowedRoot)
	if err != nil {
		return "", false, err
	}
	if err := ctx.Err(); err != nil {
		return cleaned, false, err
	}
	info, err := os.Lstat(cleaned)
	if errors.Is(err, os.ErrNotExist) {
		return cleaned, false, nil
	}
	if err != nil {
		return cleaned, false, err
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return cleaned, true, fmt.Errorf("repository cleanup path must not be a symbolic link")
	}
	if err := os.RemoveAll(cleaned); err != nil {
		return cleaned, true, err
	}
	if err := ctx.Err(); err != nil {
		return cleaned, true, err
	}
	if _, err := os.Lstat(cleaned); !errors.Is(err, os.ErrNotExist) {
		if err == nil {
			return cleaned, true, fmt.Errorf("repository cleanup path still exists")
		}
		return cleaned, true, err
	}
	return cleaned, true, nil
}

func deleteOwnedManagedRepositoryPath(
	ctx context.Context,
	path string,
	allowedRoot string,
) (string, bool, error) {
	return deleteOwnedManagedRepositoryPathWithRemover(
		ctx,
		path,
		allowedRoot,
		os.RemoveAll,
	)
}

func deleteOwnedManagedRepositoryPathForOwner(
	ctx context.Context,
	path string,
	allowedRoot string,
	expected repositoryOwnership,
) (string, bool, error) {
	return deleteOwnedManagedRepositoryPathWithRemoverAndOwner(
		ctx,
		path,
		allowedRoot,
		os.RemoveAll,
		&expected,
	)
}

func deleteOwnedManagedRepositoryPathWithRemover(
	ctx context.Context,
	path string,
	allowedRoot string,
	removeAll func(string) error,
) (string, bool, error) {
	return deleteOwnedManagedRepositoryPathWithRemoverAndOwner(
		ctx,
		path,
		allowedRoot,
		removeAll,
		nil,
	)
}

func deleteOwnedManagedRepositoryPathWithRemoverAndOwner(
	ctx context.Context,
	path string,
	allowedRoot string,
	removeAll func(string) error,
	expected *repositoryOwnership,
) (string, bool, error) {
	cleaned, err := validateRepositoryCleanupPath(path, allowedRoot)
	if err != nil {
		return "", false, err
	}
	if err := ctx.Err(); err != nil {
		return cleaned, false, err
	}
	info, err := os.Lstat(cleaned)
	if errors.Is(err, os.ErrNotExist) {
		return cleaned, false, nil
	}
	if err != nil {
		return cleaned, false, err
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
		return cleaned, true, fmt.Errorf("owned repository cleanup path must be a directory")
	}

	metadataPath := filepath.Join(cleaned, filepath.Dir(repositoryOwnershipMarkerPath))
	markerPath := filepath.Join(cleaned, repositoryOwnershipMarkerPath)
	if err := requireRegularRepositoryOwnershipMarker(metadataPath, markerPath); err != nil {
		return cleaned, true, err
	}
	if expected != nil {
		marker, markerErr := readRepositoryOwnershipMarker(markerPath)
		if markerErr != nil {
			return cleaned, true, markerErr
		}
		if marker == nil {
			return cleaned, true, fmt.Errorf("repository ownership marker is missing")
		}
		if ownerErr := requireMatchingRepositoryOwner(*marker, *expected); ownerErr != nil {
			return cleaned, true, ownerErr
		}
	}

	entries, err := os.ReadDir(cleaned)
	if err != nil {
		return cleaned, true, err
	}
	for _, entry := range entries {
		if entry.Name() == filepath.Base(metadataPath) {
			continue
		}
		if err := ctx.Err(); err != nil {
			return cleaned, true, err
		}
		if err := removeAll(filepath.Join(cleaned, entry.Name())); err != nil {
			return cleaned, true, err
		}
	}

	metadataEntries, err := os.ReadDir(metadataPath)
	if err != nil {
		return cleaned, true, err
	}
	for _, entry := range metadataEntries {
		if entry.Name() == filepath.Base(markerPath) {
			continue
		}
		if err := ctx.Err(); err != nil {
			return cleaned, true, err
		}
		if err := removeAll(filepath.Join(metadataPath, entry.Name())); err != nil {
			return cleaned, true, err
		}
	}

	if err := ctx.Err(); err != nil {
		return cleaned, true, err
	}
	if err := requireOnlyRepositoryOwnershipMarker(cleaned, metadataPath, markerPath); err != nil {
		return cleaned, true, err
	}
	if expected != nil {
		marker, markerErr := readRepositoryOwnershipMarker(markerPath)
		if markerErr != nil {
			return cleaned, true, markerErr
		}
		if marker == nil {
			return cleaned, true, fmt.Errorf("repository ownership marker changed during cleanup")
		}
		if ownerErr := requireMatchingRepositoryOwner(*marker, *expected); ownerErr != nil {
			return cleaned, true, ownerErr
		}
	}
	if err := removeAll(markerPath); err != nil {
		return cleaned, true, err
	}
	if err := os.Remove(metadataPath); err != nil && !errors.Is(err, os.ErrNotExist) {
		return cleaned, true, err
	}
	if err := os.Remove(cleaned); err != nil && !errors.Is(err, os.ErrNotExist) {
		return cleaned, true, err
	}
	if _, err := os.Lstat(cleaned); !errors.Is(err, os.ErrNotExist) {
		if err == nil {
			return cleaned, true, fmt.Errorf("repository cleanup path still exists")
		}
		return cleaned, true, err
	}
	return cleaned, true, nil
}

func requireRegularRepositoryOwnershipMarker(metadataPath string, markerPath string) error {
	metadataInfo, err := os.Lstat(metadataPath)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return fmt.Errorf("repository ownership marker is missing")
		}
		return err
	}
	if metadataInfo.Mode()&os.ModeSymlink != 0 || !metadataInfo.IsDir() {
		return fmt.Errorf("repository ownership metadata path must be a directory")
	}
	markerInfo, err := os.Lstat(markerPath)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return fmt.Errorf("repository ownership marker is missing")
		}
		return err
	}
	if markerInfo.Mode()&os.ModeSymlink != 0 || !markerInfo.Mode().IsRegular() {
		return fmt.Errorf("repository ownership marker must be a regular file")
	}
	return nil
}

func requireOnlyRepositoryOwnershipMarker(
	repositoryPath string,
	metadataPath string,
	markerPath string,
) error {
	if err := requireRegularRepositoryOwnershipMarker(metadataPath, markerPath); err != nil {
		return err
	}
	entries, err := os.ReadDir(repositoryPath)
	if err != nil {
		return err
	}
	if len(entries) != 1 || entries[0].Name() != filepath.Base(metadataPath) {
		return fmt.Errorf("repository data appeared during cleanup")
	}
	metadataEntries, err := os.ReadDir(metadataPath)
	if err != nil {
		return err
	}
	if len(metadataEntries) != 1 || metadataEntries[0].Name() != filepath.Base(markerPath) {
		return fmt.Errorf("repository metadata appeared during cleanup")
	}
	return nil
}

func validateRepositoryCleanupPath(path string, allowedRoot string) (string, error) {
	raw := strings.TrimSpace(path)
	if raw == "" {
		return "", fmt.Errorf("repository cleanup path is required")
	}
	if !filepath.IsAbs(raw) {
		return "", fmt.Errorf("repository cleanup path must be absolute")
	}
	cleaned := filepath.Clean(raw)
	if filepath.Dir(cleaned) == cleaned {
		return "", fmt.Errorf("refusing to delete a filesystem root")
	}

	root := ""
	if strings.TrimSpace(allowedRoot) != "" {
		root = filepath.Clean(allowedRoot)
		if !filepath.IsAbs(root) || filepath.Dir(root) == root {
			return "", fmt.Errorf("invalid repository cleanup root")
		}
		rel, err := filepath.Rel(root, cleaned)
		if err != nil || rel == "." || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) || filepath.IsAbs(rel) {
			return "", fmt.Errorf("repository cleanup path escapes the allowed root")
		}
	}
	if err := rejectCleanupSymlinkComponents(cleaned, root); err != nil {
		return "", err
	}
	return cleaned, nil
}

func rejectCleanupSymlinkComponents(path string, allowedRoot string) error {
	start := string(filepath.Separator)
	if volume := filepath.VolumeName(path); volume != "" {
		start = volume + string(filepath.Separator)
	}
	if allowedRoot != "" {
		start = filepath.Clean(allowedRoot)
		if info, err := os.Lstat(start); err == nil && info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("repository cleanup root must not be a symbolic link")
		}
	}
	rel, err := filepath.Rel(start, path)
	if err != nil {
		return err
	}
	current := start
	for _, component := range strings.Split(rel, string(os.PathSeparator)) {
		if component == "" || component == "." {
			continue
		}
		current = filepath.Join(current, component)
		info, statErr := os.Lstat(current)
		if errors.Is(statErr, os.ErrNotExist) {
			return nil
		}
		if statErr != nil {
			return statErr
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("repository cleanup path contains a symbolic link")
		}
	}
	return nil
}

func removeRepositoryLocalState(configFile string) (int, error) {
	paths := []string{
		configFile,
		strings.TrimSuffix(configFile, filepath.Ext(configFile)) + ".maintenance.config",
		repositoryLockFile(configFile),
	}
	removed := 0
	for _, path := range paths {
		if err := os.Remove(path); err != nil {
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			return removed, err
		}
		removed++
	}
	return removed, nil
}

func redactRepositoryCleanupPaths(message string, paths ...string) string {
	redacted := message
	for _, path := range paths {
		cleaned := strings.TrimSpace(path)
		if cleaned == "" {
			continue
		}
		redacted = strings.ReplaceAll(redacted, cleaned, "<repository-path>")
		if absolute, err := filepath.Abs(cleaned); err == nil && absolute != cleaned {
			redacted = strings.ReplaceAll(redacted, absolute, "<repository-path>")
		}
	}
	return redacted
}
