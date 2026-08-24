package nas

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"hyperfilelens/agent/internal/platform/disk"
	"hyperfilelens/agent/internal/platform/vfs"
)

// MountSourceMismatchError prevents a managed mount point from silently using
// credentials or a share left by an older repository configuration.
type MountSourceMismatchError struct {
	Expected string
	Actual   string
}

func (e *MountSourceMismatchError) Error() string {
	return fmt.Sprintf("NAS mount source mismatch: expected %s, found %s", e.Expected, e.Actual)
}

// MountReadOnlyError reports an active mount that cannot accept writes.
type MountReadOnlyError struct{ Source string }

func (e *MountReadOnlyError) Error() string {
	return fmt.Sprintf("NAS mount is read-only: %s", e.Source)
}

// WriteProbeError reports that a mounted share could not create and write a
// short-lived validation file.
type WriteProbeError struct{ Cause error }

func (e *WriteProbeError) Error() string {
	return fmt.Sprintf("NAS share write probe failed: %v", e.Cause)
}

func (e *WriteProbeError) Unwrap() error { return e.Cause }

// SpaceInfo describes filesystem usage for a mounted NAS path.
type SpaceInfo struct {
	TotalBytes uint64
	UsedBytes  uint64
	FreeBytes  uint64
}

// UnmountOptions controls how an Agent-owned NAS mount is released.
type UnmountOptions struct {
	Force bool
}

// UnmountResult reports whether cleanup detached the mount while retaining
// local references or mount-directory residue.
type UnmountResult struct {
	Attempts          int
	CleanupComplete   bool
	LazyUnmount       bool
	RetainedResources []string
	Warnings          []string
}

type unmountLocalCleanupError struct {
	err error
}

func (e *unmountLocalCleanupError) Error() string {
	return e.err.Error()
}

func (e *unmountLocalCleanupError) Unwrap() error {
	return e.err
}

func localUnmountCleanupError(err error) error {
	return &unmountLocalCleanupError{err: err}
}

func isLocalUnmountCleanupError(err error) bool {
	var cleanupErr *unmountLocalCleanupError
	return errors.As(err, &cleanupErr)
}

const unmountAttempts = 3

// Service mounts and validates NAS shares on the local host.
type Service struct {
	isMountedFn      func(string) bool
	hasUnmountWorkFn func(string) bool
	unmountFn        func(context.Context, string) error
	lazyUnmountFn    func(context.Context, string) error
	removeMountDirFn func(string) error
	retryWaitFn      func(context.Context, time.Duration) error
}

func NewService() *Service {
	return &Service{
		isMountedFn:      isMounted,
		hasUnmountWorkFn: hasUnmountWork,
		unmountFn:        unmountShare,
		lazyUnmountFn:    lazyUnmountShare,
		removeMountDirFn: removeManagedMountDirectory,
		retryWaitFn:      waitUnmountRetry,
	}
}

// IsMounted reports whether mountPoint is an active filesystem mount.
func (s *Service) IsMounted(mountPoint string) bool {
	mountPoint = ResolvedMountPoint(mountPoint)
	return mountPoint != "" && s.isMounted(mountPoint)
}

// CleanupUnmountedMountPoint removes an empty managed mount-point directory.
// It checks the live mount table before inspecting and again before removing
// the directory so cleanup never traverses a concurrently mounted NAS.
func (s *Service) CleanupUnmountedMountPoint(mountPoint string) (bool, error) {
	mountPoint = ResolvedMountPoint(mountPoint)
	if mountPoint == "" {
		return false, fmt.Errorf("invalid mount_point")
	}
	if s.hasUnmountWork(mountPoint) {
		return false, nil
	}
	entries, err := os.ReadDir(mountPoint)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if len(entries) != 0 || s.hasUnmountWork(mountPoint) {
		return false, nil
	}
	if err := removeManagedMountDirectory(mountPoint); err != nil {
		return false, err
	}
	return true, nil
}

// EnsureMounted mounts the NAS share when the mount point is not active yet.
func (s *Service) EnsureMounted(ctx context.Context, spec Spec) error {
	return s.ensureMounted(ctx, spec, false)
}

// EnsureMountedForWrite repairs one stale Agent-managed mount before an
// explicit write-intent probe. Normal health checks use EnsureMounted and are
// deliberately read-only: only initialization or explicit revalidation may
// detach a read-only or source-mismatched mount and mount the requested share
// again.
func (s *Service) EnsureMountedForWrite(ctx context.Context, spec Spec) error {
	return s.ensureMounted(ctx, spec, true)
}

func (s *Service) ensureMounted(ctx context.Context, spec Spec, repair bool) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	spec.MountPoint = ResolvedMountPoint(spec.MountPoint)
	if spec.MountPoint == "" {
		return fmt.Errorf("invalid mount_point")
	}
	if s.isMounted(spec.MountPoint) {
		validationErr := validateMountedShare(spec)
		if validationErr == nil || !repair || !recoverableMountValidationError(validationErr) {
			return validationErr
		}
		// ResolvedMountPoint has already proved this is below the Agent mount
		// root. Use strict unmount semantics: never kill processes or lazily
		// detach a busy mount during validation.
		if _, err := s.UnmountWithOptions(ctx, spec.MountPoint, UnmountOptions{}); err != nil {
			return fmt.Errorf("repair stale NAS mount: %w", err)
		}
	}
	if _, err := s.Mount(ctx, spec); err != nil {
		return err
	}
	return validateMountedShare(spec)
}

func recoverableMountValidationError(err error) bool {
	var readOnly *MountReadOnlyError
	var mismatch *MountSourceMismatchError
	return errors.As(err, &readOnly) || errors.As(err, &mismatch)
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
	if s.isMounted(spec.MountPoint) {
		return s.spaceInfo(spec.MountPoint)
	}
	if s.hasUnmountWork(spec.MountPoint) {
		if _, err := s.UnmountWithOptions(ctx, spec.MountPoint, UnmountOptions{}); err != nil {
			return SpaceInfo{}, fmt.Errorf("cleanup stale mount state: %w", err)
		}
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
	_, err := s.UnmountWithOptions(ctx, mountPoint, UnmountOptions{})
	return err
}

// UnmountWithOptions releases a managed mount with bounded retries. Force mode
// may lazily detach a busy Linux mount, but it never kills unknown processes.
func (s *Service) UnmountWithOptions(
	ctx context.Context,
	mountPoint string,
	options UnmountOptions,
) (UnmountResult, error) {
	result := UnmountResult{CleanupComplete: true}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	mountPoint = ResolvedMountPoint(mountPoint)
	if mountPoint == "" {
		return result, fmt.Errorf("invalid mount_point")
	}
	if s.hasUnmountWork(mountPoint) {
		var unmountErr error
		for attempt := 1; attempt <= unmountAttempts; attempt++ {
			result.Attempts = attempt
			unmountErr = s.unmount(ctx, mountPoint)
			// The live mount table is authoritative. The command can report an
			// error after the kernel has already detached the mount.
			if !s.hasUnmountWork(mountPoint) && !isLocalUnmountCleanupError(unmountErr) {
				unmountErr = nil
				break
			}
			if unmountErr == nil {
				unmountErr = fmt.Errorf("mount point remains active after unmount")
			}
			if !isBusyUnmountError(unmountErr) || attempt == unmountAttempts {
				break
			}
			if err := s.waitUnmountRetry(ctx, time.Duration(attempt)*200*time.Millisecond); err != nil {
				return result, err
			}
		}
		if unmountErr != nil {
			if !options.Force || !isBusyUnmountError(unmountErr) {
				return result, withUnmountDiagnostics(mountPoint, unmountErr)
			}
			details := strings.TrimSpace(unmountBusyDetails(mountPoint))
			if err := s.lazyUnmount(ctx, mountPoint); err != nil {
				return result, withUnmountDiagnostics(mountPoint, err)
			}
			warning := "The managed NAS mount was lazily detached because local references remained."
			if details != "" {
				warning += " Active references: " + details + "."
			}
			result.CleanupComplete = false
			result.LazyUnmount = true
			result.RetainedResources = append(result.RetainedResources, "nas_mount_reference")
			result.Warnings = append(result.Warnings, warning)
		}
		if s.hasUnmountWork(mountPoint) {
			return result, fmt.Errorf("mount point remains active after unmount")
		}
	}
	if err := s.removeMountDirectory(mountPoint); err != nil {
		if !options.Force {
			return result, fmt.Errorf("cleanup mount directory: %w", err)
		}
		result.CleanupComplete = false
		result.RetainedResources = append(result.RetainedResources, "nas_mount_directory")
		result.Warnings = append(result.Warnings, "The managed NAS mount directory requires later cleanup.")
	}
	return result, nil
}

func (s *Service) isMounted(mountPoint string) bool {
	if s != nil && s.isMountedFn != nil {
		return s.isMountedFn(mountPoint)
	}
	return isMounted(mountPoint)
}

func (s *Service) hasUnmountWork(mountPoint string) bool {
	if s != nil && s.hasUnmountWorkFn != nil {
		return s.hasUnmountWorkFn(mountPoint)
	}
	return hasUnmountWork(mountPoint)
}

func (s *Service) unmount(ctx context.Context, mountPoint string) error {
	if s != nil && s.unmountFn != nil {
		return s.unmountFn(ctx, mountPoint)
	}
	return unmountShare(ctx, mountPoint)
}

func (s *Service) lazyUnmount(ctx context.Context, mountPoint string) error {
	if s != nil && s.lazyUnmountFn != nil {
		return s.lazyUnmountFn(ctx, mountPoint)
	}
	return lazyUnmountShare(ctx, mountPoint)
}

func (s *Service) removeMountDirectory(mountPoint string) error {
	if s != nil && s.removeMountDirFn != nil {
		return s.removeMountDirFn(mountPoint)
	}
	return removeManagedMountDirectory(mountPoint)
}

func (s *Service) waitUnmountRetry(ctx context.Context, delay time.Duration) error {
	if s != nil && s.retryWaitFn != nil {
		return s.retryWaitFn(ctx, delay)
	}
	return waitUnmountRetry(ctx, delay)
}

func waitUnmountRetry(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func isBusyUnmountError(err error) bool {
	if err == nil {
		return false
	}
	message := strings.ToLower(err.Error())
	return strings.Contains(message, "device is busy") ||
		strings.Contains(message, "device or resource busy") ||
		strings.Contains(message, "target is busy") ||
		strings.Contains(message, "mount point remains active") ||
		strings.Contains(message, "resource busy")
}

func withUnmountDiagnostics(mountPoint string, err error) error {
	details := strings.TrimSpace(unmountBusyDetails(mountPoint))
	if details == "" {
		return err
	}
	return fmt.Errorf("%w; active references: %s", err, details)
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

// parseNetUseRemote extracts the UNC path from the line-oriented output of
// "net use" without splitting share names that contain spaces.
func parseNetUseRemote(text string) (string, bool) {
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		index := strings.Index(line, `\\`)
		if index < 0 {
			continue
		}
		remote := strings.TrimSpace(line[index:])
		if remote != "" {
			return remote, true
		}
	}
	return "", false
}

// Test mounts the share when needed and returns space information.
func (s *Service) Test(ctx context.Context, spec Spec) (SpaceInfo, error) {
	return s.Mount(ctx, spec)
}

// TestForWrite mounts (and, when necessary, repairs) the share and verifies
// that the Agent can create, write, and remove a temporary file.  The probe is
// intentionally scoped to explicit backup-target validation; normal health
// checks remain read-only.
func (s *Service) TestForWrite(ctx context.Context, spec Spec) (SpaceInfo, error) {
	if err := s.EnsureMountedForWrite(ctx, spec); err != nil {
		return SpaceInfo{}, err
	}
	info, err := s.spaceInfo(spec.MountPoint)
	if err != nil {
		return SpaceInfo{}, err
	}
	probe, err := os.CreateTemp(spec.MountPoint, ".hfl-write-probe-*")
	if err != nil {
		return SpaceInfo{}, &WriteProbeError{Cause: err}
	}
	probePath := probe.Name()
	removeProbe := true
	defer func() {
		if removeProbe {
			_ = os.Remove(probePath)
		}
	}()
	if _, err := probe.WriteString("hfl-write-probe\n"); err != nil {
		_ = probe.Close()
		return SpaceInfo{}, &WriteProbeError{Cause: err}
	}
	if err := probe.Sync(); err != nil {
		_ = probe.Close()
		return SpaceInfo{}, &WriteProbeError{Cause: err}
	}
	if err := probe.Close(); err != nil {
		return SpaceInfo{}, &WriteProbeError{Cause: err}
	}
	if err := os.Remove(probePath); err != nil {
		return SpaceInfo{}, &WriteProbeError{
			Cause: fmt.Errorf("remove validation file: %w", err),
		}
	}
	removeProbe = false
	return info, nil
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
