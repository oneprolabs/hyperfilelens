//go:build windows

package nas

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"hyperfilelens/agent/internal/platform/process"
)

type windowsMountMeta struct {
	Drive    string `json:"drive"`
	Remote   string `json:"remote"`
	Junction string `json:"junction"`
}

// Drive letters are host-wide resources, so allocation and mapping must be
// serialized even when different NAS mount points are otherwise independent.
var windowsDriveAllocation = newWindowsDriveAllocationLock()

const windowsMountCommandTimeout = 30 * time.Second

func newWindowsDriveAllocationLock() chan struct{} {
	lock := make(chan struct{}, 1)
	lock <- struct{}{}
	return lock
}

func lockWindowsDriveAllocation(ctx context.Context) error {
	if ctx == nil {
		ctx = context.Background()
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-windowsDriveAllocation:
		return nil
	}
}

func unlockWindowsDriveAllocation() {
	windowsDriveAllocation <- struct{}{}
}

func mountMetaPath(mountPoint string) string {
	return mountPoint + ".hfl-nas-mount.json"
}

func legacyMountMetaPath(mountPoint string) string {
	return filepath.Join(mountPoint, ".hfl-nas-mount.json")
}

func readMountMeta(mountPoint string) (windowsMountMeta, string, bool, error) {
	for _, path := range []string{mountMetaPath(mountPoint), legacyMountMetaPath(mountPoint)} {
		data, err := os.ReadFile(path)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return windowsMountMeta{}, path, false, err
		}
		var meta windowsMountMeta
		if err := json.Unmarshal(data, &meta); err != nil {
			return windowsMountMeta{}, path, false, fmt.Errorf("decode metadata: %w", err)
		}
		if err := validateWindowsMountMeta(mountPoint, &meta); err != nil {
			return windowsMountMeta{}, path, false, err
		}
		return meta, path, true, nil
	}
	return windowsMountMeta{}, "", false, nil
}

func validateWindowsMountMeta(mountPoint string, meta *windowsMountMeta) error {
	if meta == nil {
		return fmt.Errorf("metadata is empty")
	}
	drive := strings.ToUpper(strings.TrimSpace(meta.Drive))
	if len(drive) != 2 || drive[0] < 'A' || drive[0] > 'Z' || drive[1] != ':' {
		return fmt.Errorf("metadata drive is invalid")
	}
	remote := strings.TrimSpace(meta.Remote)
	if !strings.HasPrefix(remote, `\\`) {
		return fmt.Errorf("metadata remote is invalid")
	}
	wantJunction := filepath.Clean(mountPoint)
	gotJunction := filepath.Clean(strings.TrimSpace(meta.Junction))
	if !strings.EqualFold(gotJunction, wantJunction) {
		return fmt.Errorf("metadata junction does not match managed mount point")
	}
	meta.Drive = drive
	meta.Remote = remote
	meta.Junction = wantJunction
	return nil
}

func writeMountMeta(mountPoint string, meta windowsMountMeta) error {
	path := mountMetaPath(mountPoint)
	directory := filepath.Dir(path)
	if err := os.MkdirAll(directory, 0o755); err != nil {
		return err
	}
	data, err := json.Marshal(meta)
	if err != nil {
		return err
	}
	if _, err := os.Lstat(path); err == nil {
		return fmt.Errorf("SMB mount metadata already exists")
	} else if !os.IsNotExist(err) {
		return err
	}
	temporary, err := os.CreateTemp(directory, filepath.Base(path)+".tmp-*")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if _, err := temporary.Write(data); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Sync(); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryPath, path)
}

func isMounted(mountPoint string) bool {
	mountPoint = ResolvedMountPoint(mountPoint)
	if mountPoint == "" {
		return false
	}
	meta, _, ok, err := readMountMeta(mountPoint)
	if err != nil || !ok {
		return false
	}
	if _, err := os.Stat(meta.Junction); err != nil {
		return false
	}
	_, mapped, err := netUseDriveState(context.Background(), meta.Drive)
	if err != nil || !mapped {
		return false
	}
	details, err := netUseDriveDetails(context.Background(), meta.Drive)
	return err == nil && netUseOutputMatchesRemote(details, meta.Drive, meta.Remote)
}

func hasUnmountWork(mountPoint string) bool {
	mountPoint = ResolvedMountPoint(mountPoint)
	if mountPoint == "" {
		return false
	}
	_, _, ok, err := readMountMeta(mountPoint)
	// A sidecar represents Agent-owned cleanup work even when the drive or
	// junction is disconnected. Invalid metadata is fenced as well so it is
	// not mistaken for completed cleanup or overwritten by a remount.
	return ok || err != nil
}

func validateMountedShare(spec Spec) error {
	meta, _, ok, err := readMountMeta(spec.MountPoint)
	if err != nil || !ok {
		return &MountSourceMismatchError{Expected: expectedWindowsRemote(spec), Actual: "unmounted"}
	}
	expected := expectedWindowsRemote(spec)
	actual, mounted := netUseRemote(meta.Drive)
	if !mounted {
		return &MountSourceMismatchError{Expected: expected, Actual: "unmounted"}
	}
	if !strings.EqualFold(strings.TrimRight(actual, `\/`), strings.TrimRight(expected, `\/`)) {
		return &MountSourceMismatchError{Expected: expected, Actual: actual}
	}
	return nil
}

func expectedWindowsRemote(spec Spec) string {
	return fmt.Sprintf(`\\%s\%s`, spec.Server, strings.Trim(spec.Share, "/\\"))
}

func mountShare(ctx context.Context, spec Spec) error {
	spec.MountPoint = ResolvedMountPoint(spec.MountPoint)
	if spec.MountPoint == "" {
		return fmt.Errorf("mount_point is required")
	}
	if isMounted(spec.MountPoint) {
		return nil
	}
	switch spec.Protocol {
	case "smb":
		return mountSMB(ctx, spec)
	case "nfs":
		return fmt.Errorf("NFS mount is not supported on Windows agent hosts yet")
	default:
		return fmt.Errorf("unsupported nas protocol %q", spec.Protocol)
	}
}

func mountSMB(ctx context.Context, spec Spec) error {
	if err := lockWindowsDriveAllocation(ctx); err != nil {
		return err
	}
	defer unlockWindowsDriveAllocation()
	if isMounted(spec.MountPoint) {
		return nil
	}
	if hasUnmountWork(spec.MountPoint) {
		return fmt.Errorf("SMB mount cleanup is still required")
	}

	sharePath := strings.Trim(spec.Share, "/\\")
	remote := fmt.Sprintf(`\\%s\%s`, spec.Server, sharePath)
	junction := spec.MountPoint
	if err := os.MkdirAll(filepath.Dir(junction), 0o755); err != nil {
		return fmt.Errorf("create mount parent: %w", err)
	}
	user := spec.Username
	if spec.Domain != "" {
		user = spec.Domain + `\` + spec.Username
	}

	drive, err := pickAvailableDriveLetter(ctx)
	if err != nil {
		return err
	}
	meta := windowsMountMeta{
		Drive:    drive,
		Remote:   remote,
		Junction: junction,
	}
	// Persist intent before changing Windows network state. If the Agent exits
	// after this point, the sidecar gives the next run enough information to
	// remove an incomplete mapping safely.
	if err := writeMountMeta(junction, meta); err != nil {
		return fmt.Errorf("record SMB mount metadata: %w", err)
	}
	args := []string{
		"use",
		drive,
		remote,
		spec.Password,
		"/user:" + user,
		"/persistent:no",
	}
	res, runErr := runWindowsMountCommand(ctx, "net", args)
	if runErr != nil {
		msg := strings.TrimSpace(res.Stderr + "\n" + res.Stdout)
		if msg == "" {
			msg = runErr.Error()
		}
		return mountAttemptError(
			fmt.Errorf("mount SMB share: %s", msg),
			rollbackSMBMountAttempt(ctx, junction, drive, remote, false),
		)
	}

	if _, err := os.Stat(junction); err == nil {
		if err := os.Remove(junction); err != nil {
			return mountAttemptError(
				localUnmountCleanupError(
					fmt.Errorf("remove existing SMB junction: %w", err),
				),
				rollbackSMBMountAttempt(ctx, junction, drive, remote, false),
			)
		}
	} else if !os.IsNotExist(err) {
		return mountAttemptError(
			fmt.Errorf("inspect existing SMB junction: %w", err),
			rollbackSMBMountAttempt(ctx, junction, drive, remote, false),
		)
	}
	linkArgs := []string{"/c", "mklink", "/J", junction, drive + `\`}
	linkRes, linkErr := runWindowsMountCommand(ctx, "cmd", linkArgs)
	if linkErr != nil {
		msg := strings.TrimSpace(linkRes.Stderr + "\n" + linkRes.Stdout)
		if msg == "" {
			msg = linkErr.Error()
		}
		return mountAttemptError(
			fmt.Errorf("create SMB junction: %s", msg),
			rollbackSMBMountAttempt(ctx, junction, drive, remote, true),
		)
	}
	return nil
}

func rollbackSMBMountAttempt(
	ctx context.Context,
	mountPoint string,
	drive string,
	remote string,
	removeJunction bool,
) error {
	cleanupErrors := []error{}
	output, mapped, err := netUseDriveState(ctx, drive)
	if err != nil {
		return fmt.Errorf("inspect drive %s: %w", drive, err)
	}
	if mapped {
		output, err = netUseDriveDetails(ctx, drive)
		if err != nil {
			return fmt.Errorf("inspect mapped drive %s: %w", drive, err)
		}
		if !netUseOutputMatchesRemote(output, drive, remote) {
			return fmt.Errorf("drive %s no longer maps to the attempted NAS share", drive)
		}
		if err := netUseDelete(ctx, drive); err != nil {
			return err
		}
		_, mapped, err = netUseDriveState(ctx, drive)
		if err != nil {
			return fmt.Errorf("verify drive %s cleanup: %w", drive, err)
		}
		if mapped {
			return fmt.Errorf("drive %s remains mapped", drive)
		}
	}
	if removeJunction {
		if err := os.Remove(mountPoint); err != nil && !os.IsNotExist(err) {
			cleanupErrors = append(cleanupErrors, fmt.Errorf("remove SMB junction: %w", err))
		}
	}
	if len(cleanupErrors) == 0 {
		if err := os.Remove(mountMetaPath(mountPoint)); err != nil && !os.IsNotExist(err) {
			cleanupErrors = append(cleanupErrors, fmt.Errorf("remove SMB mount metadata: %w", err))
		}
	}
	return errors.Join(cleanupErrors...)
}

func mountAttemptError(primary error, rollback error) error {
	if rollback == nil {
		return primary
	}
	return fmt.Errorf("%w; rollback cleanup failed: %v", primary, rollback)
}

func unmountShare(ctx context.Context, mountPoint string) error {
	if err := lockWindowsDriveAllocation(ctx); err != nil {
		return err
	}
	defer unlockWindowsDriveAllocation()

	mountPoint = ResolvedMountPoint(mountPoint)
	if mountPoint == "" {
		return fmt.Errorf("mount_point is required")
	}
	meta, metaPath, ok, err := readMountMeta(mountPoint)
	if err != nil {
		return localUnmountCleanupError(fmt.Errorf("read SMB mount metadata: %w", err))
	}
	if !ok {
		return nil
	}
	if metaPath == legacyMountMetaPath(mountPoint) {
		if err := writeMountMeta(mountPoint, meta); err != nil {
			return localUnmountCleanupError(
				fmt.Errorf("migrate SMB mount metadata: %w", err),
			)
		}
		// The legacy marker lives inside the remote share. Once local cleanup
		// responsibility is durably migrated, an old read-only marker must not
		// prevent the Agent from releasing the drive and junction.
		_ = os.Remove(metaPath)
		metaPath = mountMetaPath(mountPoint)
	}
	output, mapped, stateErr := netUseDriveState(ctx, meta.Drive)
	if stateErr != nil {
		return localUnmountCleanupError(
			fmt.Errorf("inspect drive %s: %w", meta.Drive, stateErr),
		)
	}
	if mapped {
		output, stateErr = netUseDriveDetails(ctx, meta.Drive)
		if stateErr != nil {
			return localUnmountCleanupError(
				fmt.Errorf("inspect mapped drive %s: %w", meta.Drive, stateErr),
			)
		}
		if !netUseOutputMatchesRemote(output, meta.Drive, meta.Remote) {
			return localUnmountCleanupError(
				fmt.Errorf(
					"refusing to unmount drive %s because it no longer maps to the managed NAS share",
					meta.Drive,
				),
			)
		}
		if err := netUseDelete(ctx, meta.Drive); err != nil {
			return err
		}
		_, mapped, stateErr = netUseDriveState(ctx, meta.Drive)
		if stateErr != nil {
			return localUnmountCleanupError(
				fmt.Errorf("verify drive %s cleanup: %w", meta.Drive, stateErr),
			)
		}
		if mapped {
			return fmt.Errorf("unmount NAS share: drive %s remains mapped", meta.Drive)
		}
	}
	if err := os.Remove(meta.Junction); err != nil && !os.IsNotExist(err) {
		return localUnmountCleanupError(fmt.Errorf("remove SMB junction: %w", err))
	}
	if err := os.Remove(metaPath); err != nil && !os.IsNotExist(err) {
		return localUnmountCleanupError(
			fmt.Errorf("remove SMB mount metadata: %w", err),
		)
	}
	return nil
}

func lazyUnmountShare(ctx context.Context, mountPoint string) error {
	return fmt.Errorf("lazy unmount is not supported on Windows")
}

func unmountBusyDetails(_ string) string {
	return ""
}

func pickAvailableDriveLetter(ctx context.Context) (string, error) {
	output, err := netUseMappingsOutput(ctx)
	if err != nil {
		return "", fmt.Errorf("list Windows network drives: %w", err)
	}
	for letter := 'Z'; letter >= 'D'; letter-- {
		drive := fmt.Sprintf("%c:", letter)
		if netUseOutputShowsDrive(output, drive) {
			continue
		}
		if _, err := os.Stat(drive + `\`); os.IsNotExist(err) {
			return drive, nil
		}
	}
	return "", fmt.Errorf("no available drive letter for SMB mount")
}

func netUseShowsDrive(drive string) bool {
	_, ok := netUseRemote(drive)
	return ok
}

func netUseRemote(drive string) (string, bool) {
	drive = strings.TrimSpace(drive)
	if drive == "" {
		return "", false
	}
	res, err := process.Run(context.Background(), "net", []string{"use", drive}, nil, "")
	if err != nil {
		return "", false
	}
	text := res.Stdout + "\n" + res.Stderr
	if strings.Contains(strings.ToLower(text), "disconnected") {
		return "", false
	}
	return parseNetUseRemote(text)
}

func netUseMappingsOutput(ctx context.Context) (string, error) {
	res, err := runWindowsMountCommand(ctx, "net", []string{"use"})
	if err != nil {
		message := strings.TrimSpace(res.Stderr + "\n" + res.Stdout)
		if message == "" {
			message = err.Error()
		}
		return "", fmt.Errorf("query Windows network mappings: %s", message)
	}
	return res.Stdout + res.Stderr, nil
}

func netUseDriveState(ctx context.Context, drive string) (string, bool, error) {
	output, err := netUseMappingsOutput(ctx)
	if err != nil {
		return "", false, err
	}
	return output, netUseOutputShowsDrive(output, drive), nil
}

func netUseDriveDetails(ctx context.Context, drive string) (string, error) {
	res, err := runWindowsMountCommand(ctx, "net", []string{"use", drive})
	if err != nil {
		message := strings.TrimSpace(res.Stderr + "\n" + res.Stdout)
		if message == "" {
			message = err.Error()
		}
		return "", fmt.Errorf("query Windows network drive: %s", message)
	}
	return res.Stdout + res.Stderr, nil
}

func netUseOutputMatchesRemote(output string, drive string, remote string) bool {
	if !netUseOutputShowsDrive(output, drive) {
		return false
	}
	want := strings.TrimRight(strings.TrimSpace(remote), `/\`)
	for line := range strings.SplitSeq(output, "\n") {
		start := strings.Index(line, `\\`)
		if start < 0 {
			continue
		}
		candidate := strings.TrimRight(strings.TrimSpace(line[start:]), `/\`)
		if strings.EqualFold(candidate, want) {
			return true
		}
	}
	return false
}

func netUseOutputShowsDrive(output string, drive string) bool {
	drive = strings.TrimSpace(drive)
	if drive == "" {
		return false
	}
	for _, field := range strings.Fields(output) {
		if strings.EqualFold(field, drive) {
			return true
		}
	}
	return false
}

func netUseDelete(ctx context.Context, drive string) error {
	args := []string{"use", drive, "/delete", "/y"}
	res, runErr := runWindowsMountCommand(ctx, "net", args)
	if runErr != nil {
		msg := strings.TrimSpace(res.Stderr + "\n" + res.Stdout)
		if msg == "" {
			msg = runErr.Error()
		}
		return fmt.Errorf("unmount NAS share: %s", msg)
	}
	return nil
}

func runWindowsMountCommand(
	ctx context.Context,
	bin string,
	args []string,
) (process.Result, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	commandCtx, cancel := context.WithTimeout(ctx, windowsMountCommandTimeout)
	defer cancel()
	return process.Run(commandCtx, bin, args, nil, "")
}
