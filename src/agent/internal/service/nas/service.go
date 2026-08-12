package nas

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"hyperfilelens/agent/internal/platform/disk"
	"hyperfilelens/agent/internal/platform/vfs"
)

// SpaceInfo describes filesystem usage for a mounted NAS path.
type SpaceInfo struct {
	TotalBytes uint64
	UsedBytes  uint64
	FreeBytes  uint64
}

// Service mounts and validates NAS shares on the local host.
type Service struct{}

func NewService() *Service {
	return &Service{}
}

// IsMounted reports whether mountPoint is an active filesystem mount.
func (s *Service) IsMounted(mountPoint string) bool {
	mountPoint = ResolvedMountPoint(mountPoint)
	return mountPoint != "" && isMounted(mountPoint)
}

// CleanupUnmountedMountPoint removes an empty managed mount-point directory.
// It checks the live mount table before inspecting and again before removing
// the directory so cleanup never traverses a concurrently mounted NAS.
func (s *Service) CleanupUnmountedMountPoint(mountPoint string) (bool, error) {
	mountPoint = ResolvedMountPoint(mountPoint)
	if mountPoint == "" {
		return false, fmt.Errorf("invalid mount_point")
	}
	if isMounted(mountPoint) {
		return false, nil
	}
	entries, err := os.ReadDir(mountPoint)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if len(entries) != 0 || isMounted(mountPoint) {
		return false, nil
	}
	if err := removeManagedMountDirectory(mountPoint); err != nil {
		return false, err
	}
	return true, nil
}

// EnsureMounted mounts the NAS share when the mount point is not active yet.
func (s *Service) EnsureMounted(ctx context.Context, spec Spec) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	spec.MountPoint = ResolvedMountPoint(spec.MountPoint)
	if spec.MountPoint == "" {
		return fmt.Errorf("invalid mount_point")
	}
	if isMounted(spec.MountPoint) {
		return nil
	}
	_, err := s.Mount(ctx, spec)
	return err
}

// Mount creates the mount point and mounts the NAS share.
func (s *Service) Mount(ctx context.Context, spec Spec) (SpaceInfo, error) {
	if err := ctx.Err(); err != nil {
		return SpaceInfo{}, err
	}
	spec.MountPoint = ResolvedMountPoint(spec.MountPoint)
	if spec.MountPoint == "" {
		return SpaceInfo{}, fmt.Errorf("invalid mount_point")
	}
	if err := mountShare(ctx, spec); err != nil {
		return SpaceInfo{}, err
	}
	info, err := s.spaceInfo(spec.MountPoint)
	if err != nil {
		return SpaceInfo{}, fmt.Errorf("mounted at %s but failed to read space info: %w", spec.MountPoint, err)
	}
	return info, nil
}

// Unmount removes the NAS mount from the local host.
func (s *Service) Unmount(ctx context.Context, mountPoint string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	mountPoint = ResolvedMountPoint(mountPoint)
	if mountPoint == "" {
		return fmt.Errorf("invalid mount_point")
	}
	if isMounted(mountPoint) {
		if err := unmountShare(ctx, mountPoint); err != nil {
			return err
		}
		if isMounted(mountPoint) {
			return fmt.Errorf("mount point remains active after unmount")
		}
	}
	if err := removeManagedMountDirectory(mountPoint); err != nil {
		return fmt.Errorf("cleanup mount directory: %w", err)
	}
	return nil
}

func removeManagedMountDirectory(mountPoint string) error {
	mountsRoot := filepath.Clean(vfs.AgentMountsDir(agentDataDirForMounts()))
	target := filepath.Clean(mountPoint)
	rel, err := filepath.Rel(mountsRoot, target)
	if err != nil || rel == "." || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
		return fmt.Errorf("refusing to remove non-resource mount directory %s", target)
	}

	current := mountsRoot
	if rootInfo, statErr := os.Lstat(current); statErr == nil {
		if rootInfo.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("refusing to remove through symlink mount root %s", current)
		}
	} else if !os.IsNotExist(statErr) {
		return statErr
	}
	for _, component := range strings.Split(rel, string(os.PathSeparator)) {
		current = filepath.Join(current, component)
		info, statErr := os.Lstat(current)
		if os.IsNotExist(statErr) {
			return nil
		}
		if statErr != nil {
			return statErr
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("refusing to remove symlink path %s", current)
		}
	}
	if err := os.Remove(target); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

// Test mounts the share when needed and returns space information.
func (s *Service) Test(ctx context.Context, spec Spec) (SpaceInfo, error) {
	return s.Mount(ctx, spec)
}

func (s *Service) spaceInfo(path string) (SpaceInfo, error) {
	total, used, free, err := disk.Usage(path)
	if err != nil {
		return SpaceInfo{}, err
	}
	return SpaceInfo{
		TotalBytes: total,
		UsedBytes:  used,
		FreeBytes:  free,
	}, nil
}
